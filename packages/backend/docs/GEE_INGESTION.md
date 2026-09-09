# GEE Satellite Data Acquisition

Automated Google Earth Engine ingestion for GeoVision AI.

**Scope:** `Earth Engine → filter → server-side reduce → validate → timestampdb`.
Nothing beyond that. No drought indices, no flood classification, no model
training, no dashboard code.

---

## 1. What it does

Runs daily, asks Earth Engine what new observations exist since the last
successful ingestion, reduces them over the configured regions entirely
server-side, validates the results, and upserts them into the timestampdb
observation store.

```
ImageCollection
   → filterDate        (only the window since the last checkpoint)
   → filterBounds      (the configured ROI)
   → quality filter    (scene cloud % for Sentinel-2; none for SAR)
   → pixel mask        (Sentinel-2 SCL: cloud, shadow, cirrus, snow)
   → select bands      (+ derived NDVI computed server-side)
   → reduceRegions     (one call per date-chunk, all regions at once)
   → scale + unit conversion
   → validation
   → idempotent upsert into timestampdb
```

**A daily scheduler is not a daily observation.** The pipeline runs every day,
but each product publishes on its own cadence — MOD13Q1 every 16 days, MOD11A2
every 8, Sentinel-1/2 on orbit. On a day with no new source imagery the run
stores nothing and reports zero. It never forward-fills, interpolates,
re-timestamps an old observation, or invents a record to make a day look
covered.

---

## 2. Dataset registry

All GEE asset IDs live in one place: `app/ingestion/registry.py`. No collection
ID appears anywhere else in the codebase. Adding a dataset is a config entry,
not an engine change.

| Dataset | GEE asset | Cadence | Metrics stored | Unit | Scale |
|---|---|---|---|---|---|
| `sentinel2` | `COPERNICUS/S2_SR_HARMONIZED` | scene | `reflectance_red`, `reflectance_nir`, `ndvi` | reflectance / index | 1e-4 |
| `mod13q1` | `MODIS/061/MOD13Q1` | 16-day | `ndvi`, `evi`, `detailed_qa` | index / bitfield | 1e-4 |
| `chirps` | `UCSB-CHG/CHIRPS/DAILY` | daily | `rainfall_mm` | mm | 1.0 |
| `mod11a2` | `MODIS/061/MOD11A2` | 8-day | `lst_day_c`, `qc_day` | °C / bitfield | 0.02 → K → °C |
| `sentinel1` | `COPERNICUS/S1_GRD` | scene | `backscatter_vv`, `backscatter_vh` | dB | 1.0 |

Registered but **disabled**, ready to enable by flipping `enabled=True`:
`GOOGLE/DYNAMICWORLD/V1`, `COPERNICUS/DEM/GLO30`,
`JRC/GSW1_4/GlobalSurfaceWater`, `MODIS/061/MOD16A2`.

### Scale factors and units

The registry applies each product's documented scale factor exactly once,
before storage, and records the resulting unit on every row.

- MODIS NDVI arrives as int16: raw `6500` is stored as `0.65`, never `6500`.
- MOD11A2 LST: raw × `0.02` gives Kelvin, converted to Celsius, `unit="celsius"`.
- Sentinel-2 L2A reflectance: raw × `1e-4`.
- QA bitfields (`DetailedQA`, `QC_Day`) are preserved verbatim and exempted
  from numeric range checks, since a bitfield has no physical range.

### Raw vs derived

NDVI computed from Sentinel-2 is stored with `derived_metric = "NDVI"`. Raw
measurements always have `derived_metric = NULL`. Raw B4/B8 reflectance is
stored alongside the index so a later module can recompute any index without
re-querying GEE. **The drought index itself is not implemented here.**

### Reducers

Configured per band, not globally: `mean`, `median` or `sum`, combined with
min/max/count for every metric.

> **One deliberate deviation from the original spec.** Rainfall uses `mean`,
> not `sum`. A *spatial* sum of a daily CHIRPS raster over a polygon scales
> with region area and is not a physical quantity; the areal-average depth in
> mm is. Temporal summation (weekly/seasonal totals) belongs to a downstream
> module reading this series. To change it, edit the `chirps` `BandSpec` in
> `registry.py` — one line.

---

## 3. Temporal range

| | Value |
|---|---|
| Historical start | `2016-01-01` (`GEE_HISTORICAL_START`) |
| Requested cutoff | `2026-08-19` (`GEE_TARGET_END`) |
| Daily lookback | 7 days (`GEE_LOOKBACK_DAYS`) |

`GEE_TARGET_END` is a **requested** cutoff, not a claim that any dataset has
data through that date. Before each run the pipeline queries GEE for each
dataset's genuine newest observation and records both numbers separately:

```json
{
  "dataset": "mod13q1",
  "requested_until": "2026-08-19",
  "latest_available": "2026-07-12",
  "missing_days": 38,
  "availability_status": "partial_source_availability"
}
```

Statuses: `complete`, `current_within_cadence` (lag smaller than one
publication cycle — normal), `partial_source_availability`, `no_source_data`.
The missing period is **reported, never fabricated**.

---

## 4. Geographic scope

The repository contains no tehsil boundary geometry, so none is invented.

**Default:** `FAO/GAUL/2015/level2`, filtered server-side to `ADM1_NAME in
('Balochistan', 'Sindh')` within Pakistan.

> ⚠️ **GAUL level 2 is district (ADM2) for Pakistan, not tehsil.** The default
> therefore produces district-level rows with `region_type = "district"` and
> `tehsil = NULL` — accurate rather than mislabelled. Every row still carries
> `province`, `district` and `tehsil` columns.

**For true tehsil granularity**, supply a boundary file:

```bash
GEE_ROI_GEOJSON_PATH=/path/to/tehsils.geojson
GEE_ROI_REGION_TYPE=tehsil
GEE_ROI_ID_PROPERTY=TEHSIL_CODE
GEE_ROI_TEHSIL_PROPERTY=TEHSIL_NAME
GEE_ROI_DISTRICT_PROPERTY=DISTRICT
GEE_ROI_PROVINCE_PROPERTY=PROVINCE
```

A published Earth Engine asset works too, via `GEE_ROI_ASSET_ID`. `region_id`
must be unique — it is part of the observation key.

---

## 5. Database schema

Three tables, created by migration `0002_timestampdb`.

### `gee_satellite_observations`

A TimescaleDB hypertable partitioned on `observation_timestamp` where the
extension is available. **The migration probes for `timescaledb` and falls back
to a plain indexed Postgres table if it is absent** — Neon, Supabase and Vercel
Postgres do not ship it by default. Application behaviour is identical either
way; only performance at scale differs. The fallback is logged, not silent.

Primary key: `(observation_timestamp, observation_key)`, where

```
observation_key = sha256(dataset | source_image_id | region_id | metric)
```

Timescale requires every unique index on a hypertable to include the
partitioning column, hence the composite key. Hashing keeps it narrow.

Columns include: `observation_date`, `ingested_at`, `dataset`,
`dataset_asset_id`, `dataset_version`, `region_type`, `region_id`, `province`,
`district`, `tehsil`, `band`, `metric`, `derived_metric`, `value`, `unit`,
`scale_factor`, `min_value`, `max_value`, `mean_value`, `median_value`,
`pixel_count`, `quality_flag`, `cloud_percentage`, `source_image_id`,
`spatial_resolution`, `acquisition_metadata` (JSONB), `processing_status`.

No raster arrays are ever stored — only reduced statistics.

### `gee_ingestion_runs`

One row per execution: timings, status, per-dataset stats, records
inserted/updated/skipped/rejected, errors, latest observation per dataset.

### `gee_ingestion_checkpoints`

Per-dataset resume state: `last_successful_observation_date`,
`backfill_cursor`, `backfill_complete`, `requested_until`,
`latest_available_at_source`, `availability_status`, `last_error`.

---

## 6. Authenticating with GEE

The project had no existing GEE credentials, so this module introduces them.
**Never commit a key.**

1. Create a service account in the Google Cloud project registered with Earth
   Engine, and grant it the *Earth Engine Resource Viewer* role.
2. Register the service account at <https://signup.earthengine.google.com/#!/service_accounts>.
3. Download its JSON key.

```bash
GEE_PROJECT_ID=your-ee-project
GEE_SERVICE_ACCOUNT=gee-ingest@your-project.iam.gserviceaccount.com
GEE_PRIVATE_KEY=<the raw JSON, or the same JSON base64-encoded>
```

Use **base64** in CI — GitHub Actions secrets mangle the newlines inside a raw
key. Both encodings are accepted automatically.

For local development, `earthengine authenticate` plus
`GOOGLE_APPLICATION_CREDENTIALS` also works.

Credential material is scrubbed from every log line and exception message
(`app/ingestion/gee_client.scrub`).

---

## 7. Commands

Run from `packages/backend/` with the virtualenv active.

```bash
# Show configuration, list datasets, verify credentials. Contacts no data.
python -m app.ingestion.cli check-config

# What does GEE actually hold right now, per dataset? Ingests nothing.
python -m app.ingestion.cli availability

# Full pipeline, writes nothing. Run this before anything else.
python -m app.ingestion.cli daily --dry-run

# Small bounded verification: last 18 days, 3 regions.
python -m app.ingestion.cli test-run --days 18 --regions 3

# Incremental run (what the scheduler executes).
python -m app.ingestion.cli daily

# Historical backfill. Explicit only - never runs automatically.
python -m app.ingestion.cli backfill

# Narrow to one dataset; repeatable.
python -m app.ingestion.cli daily --dataset chirps --dataset mod11a2

# Restart a backfill from the beginning.
python -m app.ingestion.cli backfill --restart
```

Exit codes: `0` success or partial success, `1` every dataset failed, `2` GEE
auth problem, `3` ROI configuration problem.

### Recommended first-run order

```bash
alembic upgrade head
python -m app.ingestion.cli check-config          # credentials OK?
python -m app.ingestion.cli availability          # what does GEE have?
python -m app.ingestion.cli daily --dry-run       # nothing written
python -m app.ingestion.cli test-run --days 18 --regions 3
python -m app.ingestion.cli test-run --days 18 --regions 3   # re-run: row count must not change
python -m app.ingestion.cli backfill              # only after the above pass
```

Verify between steps:

```sql
SELECT dataset, count(*), min(observation_date), max(observation_date)
FROM gee_satellite_observations GROUP BY dataset;
```

---

## 8. Dry-run mode

`--dry-run` authenticates, queries GEE, reduces, validates, and reports exactly
what *would* be written — then writes nothing. Reads still hit the real
database so resume points and duplicate detection reflect reality. Checkpoints
are not advanced.

---

## 9. Scheduling

`.github/workflows/gee-daily-ingestion.yml`, 02:30 UTC daily, plus manual
`workflow_dispatch` with dry-run and dataset inputs.

Vercel is not used for this: its serverless function has no persistent
scheduler and too short an execution budget. The workflow uses a `concurrency`
group so two runs never overlap.

Required repository secrets: `DATABASE_URL`, `GEE_PROJECT_ID`,
`GEE_SERVICE_ACCOUNT`, `GEE_PRIVATE_KEY_BASE64`.

Set `GEE_DAILY_ENABLED=false` to make the schedule a no-op without deleting the
cron; `--force` overrides.

---

## 10. Idempotency

Uniqueness key: `dataset + source_image_id + region_id + metric`, hashed into
`observation_key` and enforced by the primary key. Writes are
`INSERT ... ON CONFLICT DO UPDATE`, so:

- Running the scheduler twice in one day changes no row count.
- The 7-day lookback re-reads already-stored days harmlessly.
- A corrected re-run overwrites values in place.
- Duplicates *within* a batch (one region covered by two tiles of the same
  image) are collapsed before the statement is issued, avoiding Postgres's
  "cannot affect row a second time" error.

---

## 11. Incremental ingestion and backfill

Each daily run starts at `last_successful_observation_date − GEE_LOOKBACK_DAYS`
and never reprocesses the full decade. The lookback catches late-published
scenes; it is safe purely because writes are idempotent.

Backfill walks `2016-01-01 → min(target_end, latest_available)` in per-dataset
chunks (30 days for Sentinel-1/2, 90 for CHIRPS, 365 for MODIS composites) and
commits after each chunk, so an interrupted run resumes from `backfill_cursor`
rather than starting over. A single ten-year request is never issued.

---

## 12. Validation

Rejected records are logged with a reason and **not stored**. Nothing is
silently clamped.

| Check | Rejects |
|---|---|
| Finiteness | `NaN`, `±Infinity`, booleans |
| NDVI / EVI | outside −1 … 1 *after* scaling |
| Rainfall | negative |
| LST | outside −50 … 70 °C after conversion (catches Kelvin stored as Celsius) |
| SAR | outside −50 … 20 dB |
| Reflectance | outside 0 … 1.6 |
| Timestamp | naive, non-datetime, or more than 2 days in the future |
| Region | not in the configured ROI |
| Metric | not in the dataset config |
| Provenance | missing `source_image_id` |

A `NULL` value is **not** an error: it means every pixel in that region was
masked (all cloud, or outside the swath). It is stored with
`processing_status = "no_valid_pixels"` so the gap stays visible rather than
being confused with "not yet ingested" — and is never filled with zero.

---

## 13. Error handling

A failure in one dataset never stops the others; the run finishes as
`partial_success`.

```
Sentinel-2  SUCCESS
MOD13Q1     SUCCESS
CHIRPS      SUCCESS
MOD11A2     FAILED    ← recorded, retried next run
Sentinel-1  SUCCESS
→ overall_status = partial_success   (exit code 0)
```

Transient failures (timeout, 429, 502/503, connection reset) retry with
exponential backoff and full jitter, up to `GEE_MAX_RETRIES`. Permanent
failures (asset not found, permission denied, no such band) are **not**
retried — they will not fix themselves and retrying only burns quota. A single
failed chunk is skipped without abandoning the rest of the window.

---

## 14. Troubleshooting

**`earthengine-api is not installed`** — `pip install -r requirements.txt`.

**`Earth Engine is not configured`** — run `check-config`; it names exactly
which variables are missing.

**`ROI source resolved to zero regions`** — the province or country spelling
does not match the asset's property values. GAUL uses `Balochistan` (not
`Baluchistan`) and `Pakistan`.

**`timescaledb extension is not available`** (migration warning) — expected on
Neon/Supabase/Vercel Postgres. The table is created as a plain indexed table
and everything works; only large-scale query performance differs.

**Everything rejected as `value_out_of_physical_range`** — a scale factor is
wrong for that dataset. Check the `BandSpec` against the product's GEE catalog
page.

**Zero records but images were found** — the ROI probably does not intersect
the imagery, or Sentinel-2 pixel masking removed everything. Try
`--dataset chirps` (global daily coverage) to isolate ROI problems.

**`Invalid or expired` / permission errors on the asset** — the service account
was not registered with Earth Engine (step 2 in §6).

**Backfill seems stuck** — check `backfill_cursor` in
`gee_ingestion_checkpoints`; it advances one chunk at a time by design.

Inspect any run:

```sql
SELECT run_id, status, records_inserted, records_rejected,
       latest_observation_per_dataset, errors
FROM gee_ingestion_runs ORDER BY started_at DESC LIMIT 5;
```

---

## 15. Tests

```bash
cd packages/backend && pytest -q
```

164 tests, no live GEE and no database required — the Earth Engine surface is
mocked. Coverage: registry loading and rejection of invalid config, scale
factors and unit conversions, date-window arithmetic (historical, incremental,
cutoff, resume), validation rules, idempotency, dry-run isolation, dataset
failure isolation, retry classification, and secret scrubbing.
