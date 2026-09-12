# packages/backend/app/services/ml/predictor.py
"""Loads the trained drought XGBoost model and serves predictions.

The model artifact is produced by packages/ml-pipeline/scripts/train_xgboost.py
and is NOT part of this package's source — it is a build output. On Vercel,
the artifact must be committed (or fetched at build time) under
`packages/ml-pipeline/models/trained/`, since there is no persistent
filesystem to train into at request time; this module only ever loads,
never trains.

Loading is lazy and cached at module level: importing this module before a
model exists must not crash the API (the endpoint reports "not trained yet"
instead), and re-loading a multi-MB joblib file per request would be wasteful
on a warm serverless instance.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MODEL_DIR = (
    # parents[4] is packages/ (predictor.py -> ml -> services -> app -> backend -> packages)
    Path(__file__).resolve().parents[4] / "ml-pipeline" / "models" / "trained"
)
DEFAULT_VERSION = "v1"


class ModelNotAvailable(RuntimeError):
    """No trained model artifact exists yet."""


class DroughtPredictor:
    def __init__(self, model_path: Path, metadata_path: Path):
        import joblib  # imported lazily: not a dependency of the web tier's

        # normal request path, only of the (optional) prediction endpoint.
        self.model = joblib.load(model_path)
        self.metadata: dict[str, Any] = json.loads(metadata_path.read_text())
        self.features: list[str] = self.metadata["features"]
        self.classes: list[str] = self.metadata["classes"]
        self.region_id_classes: list[str] = self.metadata.get("region_id_classes", [])

    def _region_id_encoding(self, region_id: str) -> Optional[int]:
        """Same encoding LabelEncoder produced at training time: sorted-order index.

        `feature_engineering.py` saves the fitted encoder's classes_ (already
        sorted, as sklearn's LabelEncoder always stores them) into
        feature_metadata.json, so this reproduces the exact mapping without
        needing to ship the encoder object itself.
        """
        try:
            return sorted(self.region_id_classes).index(region_id)
        except ValueError:
            return None

    def build_feature_vector(self, region_id: str, features: dict[str, float]) -> list[float]:
        row = dict(features)
        if "region_id_enc" not in row:
            encoded = self._region_id_encoding(region_id)
            if encoded is None:
                raise ValueError(
                    f"region_id {region_id!r} was not seen during training "
                    f"({len(self.region_id_classes)} known regions); the model "
                    "cannot encode an unseen region."
                )
            row["region_id_enc"] = encoded

        missing = [f for f in self.features if f not in row]
        if missing:
            raise ValueError(f"missing required features: {missing}")
        return [row[f] for f in self.features]

    def predict(self, region_id: str, features: dict[str, float]) -> dict[str, Any]:
        vector = self.build_feature_vector(region_id, features)
        prediction = int(self.model.predict([vector])[0])
        probability = self.model.predict_proba([vector])[0]

        importances = getattr(self.model, "feature_importances_", None)
        contributing_factors = []
        if importances is not None:
            ranked = sorted(
                zip(self.features, importances, vector), key=lambda t: t[1], reverse=True
            )[:5]
            contributing_factors = [
                {"feature": name, "value": value, "importance": float(imp)}
                for name, imp, value in ranked
            ]

        return {
            "region_id": region_id,
            "severity_label": prediction,
            "severity_name": self.classes[prediction],
            "confidence": float(max(probability)),
            "probabilities": {
                self.classes[i]: float(p) for i, p in enumerate(probability)
            },
            "lead_time_weeks": self.metadata.get("lead_time_weeks", 2),
            "model_version": self.metadata.get("model_version"),
            "contributing_factors": contributing_factors,
        }


@lru_cache(maxsize=1)
def _cached_predictor(model_path_str: str, metadata_path_str: str) -> DroughtPredictor:
    return DroughtPredictor(Path(model_path_str), Path(metadata_path_str))


def get_predictor(version: str = DEFAULT_VERSION) -> DroughtPredictor:
    model_path = MODEL_DIR / f"drought_xgboost_{version}.pkl"
    metadata_path = MODEL_DIR / f"drought_xgboost_{version}.json"
    if not model_path.exists() or not metadata_path.exists():
        raise ModelNotAvailable(
            f"no trained model at {model_path} — run the ml-pipeline training "
            "scripts (extract_training_data.py -> feature_engineering.py -> "
            "train_xgboost.py) and commit the resulting artifact"
        )
    try:
        return _cached_predictor(str(model_path), str(metadata_path))
    except Exception as exc:  # noqa: BLE001 - surfaced as ModelNotAvailable to the caller
        logger.exception("failed to load drought model from %s", model_path)
        raise ModelNotAvailable(f"model artifact at {model_path} failed to load: {exc}") from exc
