# GEE pipeline

Acquisition detail lives in `GEE_INGESTION.md`. This covers the automated cycle
and what was added for hazard intelligence.

## The daily cycle

```
GitHub Actions  02:30 UTC
   ↓
alembic upgrade head
   ↓
check-config            (verify GEE auth)
   ↓
INGESTION               discover → fetch → process → validate → dedupe → store
   ↓
ANALYTICS CASCADE       12 recorded, resumable stages
   ↓
gv_pipeline_stage_runs  (what the dashboard renders)
```

The analytics step runs with `if: always()`. Ingestion partly failing is a
normal state (one product lagging), and the datasets that *did* land should
still be analysed.

## The 12 stages

| # | Stage | Depends on |
|---|---|---|
| 1 | registry_sync | — |
| 2 | data_quality | — |
| 3 | geometry_stats | — |
| 4 | baselines | — |
| 5 | features | baselines |
| 6 | flood_detection | features, geometry_stats |
| 7 | hazards | features |
| 8 | events | hazards |
| 9 | hotspots | hazards, geometry_stats |
| 10 | alerts | hazards |
| 11 | recovery | events |
| 12 | daily_brief | — |

Only real data dependencies are listed. A stage that fails is recorded and the
cascade continues; stages that genuinely depend on it are **skipped rather than
run on absent inputs** — computing hazards from features that were never
written would produce confident nonsense.

Verified live: when `geometry_stats` failed, `flood_detection` and `hotspots`
were skipped and the other nine stages completed.

## Datasets

| Dataset | Collection | Metrics |
|---|---|---|
| sentinel2 | COPERNICUS/S2_SR_HARMONIZED | blue/red/nir reflectance, NDVI, EVI |
| sentinel1 | COPERNICUS/S1_GRD | VV, VH, **water_fraction** |
| chirps | UCSB-CHG/CHIRPS/DAILY | rainfall_mm |
| mod13q1 | MODIS/061/MOD13Q1 | NDVI, EVI, QA |
| mod11a2 | MODIS/061/MOD11A2 | LST day, LST night, QC |

### water_fraction

Added for flood detection. `VV.lt(-15 dB)` produces a 0/1 mask; the MEAN
reducer over a district turns it into the fraction of that district classified
as water. The per-pixel classification happens inside Earth Engine — no raster
crosses the wire.

Verified live: 22 usable values in [0.047, 0.614].

## Idempotency

Observations key on `sha256(dataset|source_image_id|region_id|metric)` with a
composite primary key and `ON CONFLICT DO UPDATE`.

Every analytics stage upserts on a natural key. Verified: re-running a full
cycle left all row counts unchanged.

## Resilience

- per-dataset lease locks, reclaimable after expiry
- resumable checkpoints (`--resume RUN_ID` skips completed stages)
- exponential backoff with full jitter on transient GEE errors
- permanent errors (bad asset, permission denied) are not retried
- one dataset failing never stops the others — the run reports
  `partial_success`, not `failed`

## Commands

```bash
cd packages/backend

venv/Scripts/python -m alembic upgrade head
venv/Scripts/python -m app.ingestion.cli check-config
venv/Scripts/python -m app.ingestion.cli daily
venv/Scripts/python -m app.ingestion.cli backfill          # hours; resumable
venv/Scripts/python -m app.ingestion.cli analytics
venv/Scripts/python -m app.ingestion.cli analytics --resume RUN_ID
venv/Scripts/python -m app.ingestion.cli analytics --stage hazards
```

## Environment

Required: `DATABASE_URL`, `GEE_SERVICE_ACCOUNT`, `GEE_PRIVATE_KEY`,
`GEE_PROJECT_ID`, `SECRET_KEY`.

Optional: `GEE_ROI_PROVINCES` (empty = all Pakistan), `GEE_LOOKBACK_DAYS`,
`GEE_CLOUD_THRESHOLD`, `DB_POOL_ENABLED`, `GITHUB_REPOSITORY` +
`GITHUB_DISPATCH_TOKEN` for API-triggered runs.

`DB_POOL_ENABLED=false` is required for any harness running each request in a
fresh event loop (notably `fastapi.testclient.TestClient`).
