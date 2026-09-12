# packages/ml-pipeline/scripts/feature_engineering.py
"""Turn training_data.csv (one row per region/day) into a weekly ML feature matrix.

Design notes, since these deviate from a naive reading of the spec:

* **Weekly, not daily.** Rainfall is daily but NDVI (MOD13Q1) is a 16-day
  composite and LST (MOD11A2) is 8-day — a daily row would just be the same
  composite value repeated with forward-fill noise. Resampling to weekly
  (W-MON, last value carried forward up to 2 weeks) matches the coarsest
  real cadence in the data and is what "2 weeks ahead" naturally means: 2
  weekly steps.

* **spi_30d is an approximation, not a calibrated SPI.** True SPI needs a
  multi-decade gamma-fitted climatology per calendar period; this pipeline
  has ~2 years of data. `spi_30d` here is a z-score of trailing 30-day
  rainfall against that region's own expanding historical mean/std — same
  spirit (standardised rainfall anomaly), openly weaker statistically. This
  is stated in the saved feature metadata so nobody mistakes it for the real
  index later.

* **Time-based 80/20 split, not random.** This is a time series per region:
  a random row-level split would let the model train on a week that comes
  chronologically *after* a week in the test set for the same region, which
  leaks the future into training. Splitting on the last 20% of each region's
  own timeline avoids that; it is a deliberate, better-than-asked-for
  interpretation of "80/20 split" and is noted here so it isn't mistaken for
  a bug when the row counts don't look like a plain random 80/20.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[1] / "Data"
PROCESSED_DIR = DATA_DIR / "processed"

LEAD_WEEKS = 2  # "2 weeks ahead"
NDVI_LAGS = 8  # ndvi_t1..ndvi_t8, last 8 weeks


def weekly_resample(df: pd.DataFrame) -> pd.DataFrame:
    """One row per region per ISO week, carrying composite values forward."""
    parts = []
    for region_id, g in df.groupby("region_id"):
        g = g.set_index(pd.to_datetime(g["date"])).sort_index()
        weekly = g[["ndvi", "rainfall_mm", "lst", "flood_water", "drought_score", "severity_label"]].resample(
            "W-MON"
        ).mean()
        # Composite products (NDVI/LST) do not refresh every week; carrying
        # the last known value forward for up to 2 weeks reflects that these
        # ARE still the current reading, not a gap. Rainfall/severity are not
        # forward-filled — those are meant to be missing if truly absent.
        weekly[["ndvi", "lst"]] = weekly[["ndvi", "lst"]].ffill(limit=2)
        weekly["region_id"] = region_id
        weekly = weekly.reset_index().rename(columns={"date": "date"})
        parts.append(weekly)
    return pd.concat(parts, ignore_index=True)


def build_features(weekly: pd.DataFrame) -> pd.DataFrame:
    weekly = weekly.sort_values(["region_id", "date"]).reset_index(drop=True)
    out_parts = []

    for region_id, g in weekly.groupby("region_id"):
        g = g.sort_values("date").reset_index(drop=True)

        for lag in range(1, NDVI_LAGS + 1):
            g[f"ndvi_t{lag}"] = g["ndvi"].shift(lag - 1)

        # spi_30d: rolling ~30-day (≈4-week) rainfall sum, standardised
        # against this region's own expanding mean/std up to that point (no
        # lookahead: expanding() at row i only sees rows <= i).
        rolling_rain = g["rainfall_mm"].rolling(window=4, min_periods=2).sum()
        expanding_mean = rolling_rain.expanding(min_periods=4).mean()
        expanding_std = rolling_rain.expanding(min_periods=4).std().replace(0, np.nan)
        g["spi_30d"] = (rolling_rain - expanding_mean) / expanding_std

        # lst_anomaly: current LST minus this region's expanding mean LST for
        # the same ISO week-of-year (a crude seasonal baseline — the
        # dedicated gv_metric_baselines climatology is a much better source
        # once several years of data exist, see app/intelligence/baselines.py).
        g["week_of_year"] = pd.to_datetime(g["date"]).dt.isocalendar().week.astype(int)
        seasonal_mean = g.groupby("week_of_year")["lst"].transform(
            lambda s: s.expanding(min_periods=1).mean().shift(1)
        )
        g["lst_anomaly"] = g["lst"] - seasonal_mean

        # drought_duration: consecutive prior weeks (including current) with
        # severity_label >= 1 (Watch or worse).
        in_drought = (g["severity_label"] >= 1).astype(int)
        duration = in_drought.groupby((in_drought != in_drought.shift()).cumsum()).cumsum()
        g["drought_duration"] = np.where(in_drought == 1, duration, 0)

        g["prev_severity"] = g["severity_label"].shift(1)
        g["month"] = pd.to_datetime(g["date"]).dt.month

        # Label: severity LEAD_WEEKS weeks ahead of THIS row's date.
        g["target_severity"] = g["severity_label"].shift(-LEAD_WEEKS)

        out_parts.append(g)

    result = pd.concat(out_parts, ignore_index=True)
    result = result.drop(columns=["week_of_year"])
    return result


FEATURE_COLUMNS = (
    [f"ndvi_t{i}" for i in range(1, NDVI_LAGS + 1)]
    + ["spi_30d", "lst_anomaly", "drought_duration", "prev_severity", "month", "region_id_enc"]
)


def split_and_encode(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series, LabelEncoder]:
    df = df.dropna(subset=FEATURE_COLUMNS[:-1] + ["target_severity"]).reset_index(drop=True)
    if df.empty:
        raise SystemExit(
            "No complete feature rows after building lags/targets. This usually "
            "means there isn't enough historical depth yet (need >= "
            f"{NDVI_LAGS + LEAD_WEEKS} consecutive weeks of data per region). "
            "Ingest more history with `python -m app.ingestion.cli backfill` "
            "and re-run analytics before training."
        )

    encoder = LabelEncoder()
    df["region_id_enc"] = encoder.fit_transform(df["region_id"])

    train_parts, test_parts = [], []
    for _region_id, g in df.groupby("region_id"):
        g = g.sort_values("date")
        cut = int(len(g) * 0.8)
        train_parts.append(g.iloc[:cut])
        test_parts.append(g.iloc[cut:])

    train_df = pd.concat(train_parts, ignore_index=True)
    test_df = pd.concat(test_parts, ignore_index=True)
    test_df = test_df[test_df["target_severity"].notna()]

    X_train = train_df[FEATURE_COLUMNS]
    X_test = test_df[FEATURE_COLUMNS]
    y_train = train_df["target_severity"].astype(int)
    y_test = test_df["target_severity"].astype(int)
    return X_train, X_test, y_train, y_test, encoder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DATA_DIR / "training_data.csv")
    parser.add_argument("--output-dir", type=Path, default=PROCESSED_DIR)
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"{args.input} does not exist — run extract_training_data.py first")

    raw = pd.read_csv(args.input, parse_dates=["date"])
    weekly = weekly_resample(raw)
    featured = build_features(weekly)
    X_train, X_test, y_train, y_test, encoder = split_and_encode(featured)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    X_train.to_csv(args.output_dir / "X_train.csv", index=False)
    X_test.to_csv(args.output_dir / "X_test.csv", index=False)
    y_train.to_csv(args.output_dir / "y_train.csv", index=False)
    y_test.to_csv(args.output_dir / "y_test.csv", index=False)

    (args.output_dir / "feature_metadata.json").write_text(
        json.dumps(
            {
                "features": FEATURE_COLUMNS,
                "lead_weeks": LEAD_WEEKS,
                "ndvi_lags": NDVI_LAGS,
                "region_id_classes": list(encoder.classes_),
                "severity_classes": ["Normal", "Watch", "Moderate", "Severe", "Extreme"],
                "notes": {
                    "spi_30d": "Approximation: z-score of trailing 30-day rainfall vs "
                    "the region's own expanding mean/std. Not a calibrated "
                    "gamma-distribution SPI.",
                    "split": "Time-based 80/20 per region (last 20% of each region's "
                    "own timeline), not a random row split, to avoid leaking "
                    "future weeks into training.",
                },
            },
            indent=2,
        )
    )

    logger.info(
        "X_train=%d rows, X_test=%d rows, %d features", len(X_train), len(X_test), len(FEATURE_COLUMNS)
    )
    logger.info("y_train distribution:\n%s", y_train.value_counts().sort_index())
    logger.info("Wrote outputs to %s", args.output_dir)


if __name__ == "__main__":
    main()
