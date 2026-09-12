# packages/ml-pipeline/scripts/extract_training_data.py
"""Pull historical GEE observations and drought hazard scores into a CSV.

Reads directly from the same Postgres database the backend uses
(`DATABASE_URL` in `.env`), via a *synchronous* SQLAlchemy engine — the
backend's own engine is async (asyncpg, bound to a serverless event loop),
which is the wrong tool for a one-shot batch export script.

Ground truth, read from the actual schema before writing this script (do not
assume "tehsil_id" — it does not exist):

  - `gee_satellite_observations` (app/databases/timestampdb/models.py) is the
    single source of satellite measurements. The aggregation unit is
    `region_id` (FAO GAUL level-2 district code, as a string) — the pipeline
    is explicitly DISTRICT-level ("region_type" == "district"); the `tehsil`
    column exists on the row but is null everywhere because no tehsil
    boundary source is configured (see app/ingestion/roi.py's module
    docstring). Do not invent tehsil granularity that isn't there.
  - `gv_hazard_scores` (app/databases/timestampdb/intelligence.py) holds the
    already-computed CURRENT drought score per region/date
    (`hazard='drought'`, `score` 0-100, `reference_date`). This is the label
    source — the analytics cascade already did the hard work of turning raw
    bands into a drought severity number.

This script does not compute anything — it only joins and exports. All
feature engineering (lags, SPI approximation, anomalies) happens in
feature_engineering.py, which consumes this CSV's output.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db_utils import load_settings, sync_engine_url  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Metrics pulled from gee_satellite_observations. These are the raw/derived
# bands that exist today per app/ingestion/registry.py — do not reference a
# metric name that isn't actually produced by the pipeline.
METRICS = ("ndvi", "rainfall_mm", "lst_day_c", "water_fraction")

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "Data" / "training_data.csv"

OBSERVATIONS_SQL = text(
    """
    SELECT region_id, observation_date, metric, AVG(value) AS value
    FROM gee_satellite_observations
    WHERE metric = ANY(:metrics)
      AND observation_date >= :start_date
      AND observation_date <= :end_date
      AND value IS NOT NULL
    GROUP BY region_id, observation_date, metric
    """
)

HAZARD_SQL = text(
    """
    SELECT region_id, reference_date, score AS drought_score
    FROM gv_hazard_scores
    WHERE hazard = 'drought'
      AND reference_date >= :start_date
      AND reference_date <= :end_date
      AND score IS NOT NULL
    """
)


def score_to_severity(score: float) -> int:
    """Map the existing 0-100 drought score onto the 5 requested classes.

    0=Normal(80-100) 1=Watch(60-79) 2=Moderate(40-59) 3=Severe(20-39) 4=Extreme(0-19)
    Higher score = healthier, so severity is inverted relative to score.
    """
    if score >= 80:
        return 0
    if score >= 60:
        return 1
    if score >= 40:
        return 2
    if score >= 20:
        return 3
    return 4


def extract(engine, start_date: date, end_date: date, region_ids: list[str] | None) -> pd.DataFrame:
    logger.info("Querying gee_satellite_observations for %s..%s", start_date, end_date)
    obs = pd.read_sql(
        OBSERVATIONS_SQL,
        engine,
        params={"metrics": list(METRICS), "start_date": start_date, "end_date": end_date},
    )
    logger.info("Querying gv_hazard_scores (hazard=drought) for %s..%s", start_date, end_date)
    hazard = pd.read_sql(
        HAZARD_SQL, engine, params={"start_date": start_date, "end_date": end_date}
    )

    if obs.empty:
        raise SystemExit(
            "No rows found in gee_satellite_observations for the requested window. "
            "Nothing has been ingested yet, or DATABASE_URL points at the wrong "
            "database. Run `python -m app.ingestion.cli daily` (or check the "
            "ingestion GitHub Actions history) before training."
        )
    if hazard.empty:
        raise SystemExit(
            "No rows found in gv_hazard_scores for hazard='drought'. Observations "
            "exist but the analytics cascade has not run. Run "
            "`python -m app.ingestion.cli analytics` first — hazard scores are the "
            "label source for this model."
        )

    if region_ids:
        obs = obs[obs["region_id"].isin(region_ids)]
        hazard = hazard[hazard["region_id"].isin(region_ids)]

    wide = obs.pivot_table(
        index=["region_id", "observation_date"], columns="metric", values="value"
    ).reset_index()
    wide = wide.rename(columns={"observation_date": "date"})
    for metric in METRICS:
        if metric not in wide.columns:
            wide[metric] = pd.NA

    hazard = hazard.rename(columns={"reference_date": "date"})

    # asof-merge per region: hazard scores are computed on a different
    # cadence than any single satellite product, so an exact date join would
    # drop most rows. Nearest-prior hazard score within 3 days is a small,
    # explicit tolerance rather than silently matching an arbitrarily old one.
    merged_parts = []
    for region_id, obs_group in wide.groupby("region_id"):
        hz_group = hazard[hazard["region_id"] == region_id].sort_values("date")
        if hz_group.empty:
            continue
        obs_group = obs_group.sort_values("date")
        merged = pd.merge_asof(
            obs_group,
            hz_group[["date", "drought_score"]],
            on="date",
            direction="nearest",
            tolerance=pd.Timedelta(days=3),
        )
        merged["region_id"] = region_id
        merged_parts.append(merged)

    if not merged_parts:
        raise SystemExit(
            "No region had both satellite observations and drought hazard scores "
            "within a 3-day tolerance. Cannot build labelled training rows."
        )

    result = pd.concat(merged_parts, ignore_index=True)
    result = result.dropna(subset=["drought_score"])
    result["severity_label"] = result["drought_score"].apply(score_to_severity)
    result = result.rename(
        columns={
            "ndvi": "ndvi",
            "rainfall_mm": "rainfall_mm",
            "lst_day_c": "lst",
            "water_fraction": "flood_water",
            "drought_score": "drought_score",
        }
    )
    columns = [
        "region_id",
        "date",
        "ndvi",
        "rainfall_mm",
        "lst",
        "flood_water",
        "drought_score",
        "severity_label",
    ]
    result = result[columns].sort_values(["region_id", "date"]).reset_index(drop=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--years-back", type=int, default=2, help="How many years of history to pull (default 2)"
    )
    parser.add_argument(
        "--region", action="append", default=None, help="Limit to this region_id (repeatable)"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    settings = load_settings()
    engine = create_engine(sync_engine_url(settings["DATABASE_URL"]))

    end_date = date.today()
    start_date = end_date - timedelta(days=365 * args.years_back)

    df = extract(engine, start_date, end_date, args.region)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)
    logger.info(
        "Wrote %d rows across %d regions to %s",
        len(df),
        df["region_id"].nunique(),
        args.output,
    )
    logger.info("Severity label distribution:\n%s", df["severity_label"].value_counts().sort_index())


if __name__ == "__main__":
    main()
