# packages/ml-pipeline/scripts/train_xgboost.py
"""Train the drought-severity XGBoost classifier and save the artifact.

Run after feature_engineering.py has produced Data/processed/{X,y}_{train,test}.csv.

Class imbalance: Severe/Extreme weeks are rare by definition. XGBClassifier
has no `class_weight=` (that's the sklearn estimators' API); the equivalent
is passing per-row `sample_weight` computed with
`sklearn.utils.class_weight.compute_sample_weight("balanced", y)`, applied
during both cross-validation and the final fit.

R² is an odd metric for a 5-class label, but it's requested by the project
spec and the classes ARE ordinal (Normal < Watch < ... < Extreme), so it is
computed by treating the class index as a continuous ordinal target — this is
a secondary diagnostic ("how far off, on average, in severity steps") on top
of the primary metric, F1 (macro), which is what the ≥0.70 target is actually
about for a classification task.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import randint, uniform
from sklearn.metrics import (
    confusion_matrix,
    f1_score,
    mean_squared_error,
    r2_score,
)
from sklearn.model_selection import RandomizedSearchCV, StratifiedKFold
from sklearn.utils.class_weight import compute_sample_weight
from xgboost import XGBClassifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(__file__).resolve().parents[1] / "Data" / "processed"
MODELS_DIR = Path(__file__).resolve().parents[1] / "models" / "trained"

CLASS_NAMES = ["Normal", "Watch", "Moderate", "Severe", "Extreme"]

BASE_PARAMS = dict(
    n_estimators=200,
    max_depth=6,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
    reg_alpha=0.1,
    reg_lambda=1.0,
    objective="multi:softprob",
    num_class=5,
    eval_metric="mlogloss",
)

SEARCH_SPACE = {
    "n_estimators": randint(100, 400),
    "max_depth": randint(3, 9),
    "learning_rate": uniform(0.01, 0.19),
    "subsample": uniform(0.6, 0.4),
    "colsample_bytree": uniform(0.6, 0.4),
    "reg_alpha": uniform(0.0, 0.5),
    "reg_lambda": uniform(0.5, 2.0),
}


def load_split(output_dir: Path):
    X_train = pd.read_csv(output_dir / "X_train.csv")
    X_test = pd.read_csv(output_dir / "X_test.csv")
    y_train = pd.read_csv(output_dir / "y_train.csv").iloc[:, 0]
    y_test = pd.read_csv(output_dir / "y_test.csv").iloc[:, 0]
    return X_train, X_test, y_train, y_test


def train(X_train, y_train, n_iter: int, random_state: int) -> XGBClassifier:
    base_model = XGBClassifier(**{**BASE_PARAMS, "random_state": random_state})
    sample_weight = compute_sample_weight("balanced", y_train)

    n_splits = min(5, y_train.value_counts().min())
    if n_splits < 2:
        logger.warning(
            "Smallest class has <2 samples; skipping CV/search and fitting the "
            "base configuration directly. Train on more history for a real search."
        )
        base_model.fit(X_train, y_train, sample_weight=sample_weight)
        return base_model

    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=random_state)
    search = RandomizedSearchCV(
        estimator=XGBClassifier(**{k: v for k, v in BASE_PARAMS.items()
                                    if k not in SEARCH_SPACE}, random_state=random_state),
        param_distributions=SEARCH_SPACE,
        n_iter=n_iter,
        scoring="f1_macro",
        cv=cv,
        n_jobs=-1,
        random_state=random_state,
        verbose=1,
    )
    search.fit(X_train, y_train, sample_weight=sample_weight)
    logger.info("Best CV f1_macro=%.4f, params=%s", search.best_score_, search.best_params_)
    return search.best_estimator_


def evaluate(model: XGBClassifier, X_test, y_test) -> dict:
    y_pred = model.predict(X_test)
    f1_macro = f1_score(y_test, y_pred, average="macro", zero_division=0)
    r2 = r2_score(y_test, y_pred)  # ordinal-index diagnostic, see module docstring
    rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
    cm = confusion_matrix(y_test, y_pred, labels=list(range(5)))
    return {
        "f1_macro": float(f1_macro),
        "r2_score": float(r2),
        "rmse": rmse,
        "confusion_matrix": cm.tolist(),
        "n_test": int(len(y_test)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--model-dir", type=Path, default=MODELS_DIR)
    parser.add_argument("--version", default="v1")
    parser.add_argument("--n-iter", type=int, default=25, help="RandomizedSearchCV iterations")
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()

    X_train, X_test, y_train, y_test = load_split(args.data_dir)
    logger.info("Training on %d rows, testing on %d rows", len(X_train), len(X_test))

    model = train(X_train, y_train, args.n_iter, args.random_state)
    metrics = evaluate(model, X_test, y_test)

    logger.info("Test F1 (macro): %.4f", metrics["f1_macro"])
    logger.info("Test R2 (ordinal diagnostic): %.4f", metrics["r2_score"])
    logger.info("Test RMSE (ordinal diagnostic): %.4f", metrics["rmse"])

    if metrics["f1_macro"] < 0.70:
        logger.warning(
            "F1 (macro) = %.4f is below the target of 0.70. The model is still "
            "saved — do not deploy it to /ml/predict/drought until it clears the "
            "bar; more history and real tehsil-level boundaries are the likeliest "
            "fix, not further hyperparameter tuning alone.",
            metrics["f1_macro"],
        )

    args.model_dir.mkdir(parents=True, exist_ok=True)
    model_path = args.model_dir / f"drought_xgboost_{args.version}.pkl"
    metadata_path = args.model_dir / f"drought_xgboost_{args.version}.json"

    joblib.dump(model, model_path)

    feature_metadata_path = args.data_dir / "feature_metadata.json"
    features = (
        json.loads(feature_metadata_path.read_text())["features"]
        if feature_metadata_path.exists()
        else list(X_train.columns)
    )

    metadata = {
        "model_version": args.version,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "f1_score": metrics["f1_macro"],
        "r2_score": metrics["r2_score"],
        "rmse": metrics["rmse"],
        "confusion_matrix": metrics["confusion_matrix"],
        "n_train": int(len(X_train)),
        "n_test": metrics["n_test"],
        "features": features,
        "classes": CLASS_NAMES,
        "lead_time_weeks": 2,
        "hyperparameters": model.get_params(),
        "meets_target": bool(metrics["f1_macro"] >= 0.70 and metrics["r2_score"] >= 0.70),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, default=str))

    logger.info("Saved model to %s", model_path)
    logger.info("Saved metadata to %s", metadata_path)


if __name__ == "__main__":
    main()
