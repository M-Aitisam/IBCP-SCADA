# GeoVision AI — API and dashboard

Companion to `GEE_INGESTION.md`, which covers acquisition. This document covers
what happens *after* observations are stored: the aggregation API and the
command-centre dashboard that reads it.

---

## 1. The rule everything else follows

The dashboard displays values that came out of `gee_satellite_observations`,
or it displays an explicit absence. There is no third option — no placeholder
series, no zero substituted for a missing reading, no "typical" value.

Concretely:

| Situation | What the API returns | What the UI shows |
|---|---|---|
| Data exists | the value, with its observation date and source dataset | the number |
| Nothing ingested for that metric/period | `data_source: "no_data"`, value `null` | `—` and "No observation available" |
| Comparison period has no data | `status: "insufficient_data"` + `reason` | "Insufficient data" |
| Baseline too short for an anomaly | `status: "insufficient_data"` + `reason` | "Insufficient baseline" |
| Dataset published nothing recently | `state: "NO NEW DATA"` | a neutral badge, **not** an error |

That last row is deliberate. MOD13Q1 is a 16-day composite: on 15 days out of
16 it has nothing new, and rendering that as a fault would make the status
panel meaningless.

---

## 2. Endpoints

All under `/api/v1/geovision`, all requiring a bearer token.

Every filtered endpoint accepts the same query parameters, so the map, KPIs,
charts and tables cannot end up describing different selections:

```
range=7d|30d|3m|6m|1y|5y|10y     # or start=YYYY-MM-DD&end=YYYY-MM-DD
province= district= tehsil=       # cascading geography
region_id=                        # most specific; overrides the rest
```

| Endpoint | Purpose |
|---|---|
| `GET /overview` | KPI command centre: each headline metric with unit, trend, observation date and source |
| `GET /regions` | every region's latest value per metric, plus the classification bands applied |
| `GET /regions/hierarchy` | province → district → tehsil tree for the cascading filters |
| `GET /regions/geometry` | simplified boundaries as GeoJSON for the map |
| `GET /region/{region_id}` | detail panel: current health, per-dataset latest, quality, trends |
| `GET /trends?metric=` | bucketed series + period-over-period comparison |
| `GET /anomaly?metric=` | departure from the same season in prior years (derived) |
| `GET /datasets` | data-source catalogue: configuration joined to measured coverage |
| `GET /ingestion-status` | per-dataset pipeline health |
| `GET /watch` | Satellite Watch indicators |
| `GET /freshness` | newest real observation timestamp and its age |

The original per-metric feeds (`/vegetation`, `/rainfall`, `/temperature`,
`/timeseries/{region_id}`, `/coverage`) are unchanged and still served.

### Ingestion control — `/api/v1/ingestion`

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /status` | any user | per-dataset state, in-flight locks, recent runs, configuration |
| `GET /jobs`, `GET /jobs/{run_id}` | any user | run history |
| `POST /run` | **operator** | trigger an incremental run |
| `POST /backfill` | **operator** | trigger a historical run |

The POST endpoints **do not ingest in the request**. The backend deploys as a
serverless function with a seconds-long budget; a backfill runs for hours.
Letting HTTP start a job it cannot finish would leave a half-written checkpoint
and a held lock. They dispatch the GitHub Actions workflow that already owns
the nightly schedule — one execution path, not two that can drift.

With `GITHUB_REPOSITORY`/`GITHUB_DISPATCH_TOKEN` unset they return
`status: "not_configured"` along with the exact CLI command, which is more
useful than an error because running the CLI directly is a normal way to
operate this pipeline.

---

## 3. Why a ten-year request is cheap

The bucket is chosen from the requested span, so payload size stays roughly
constant regardless of range:

| Span | Bucket | Approx. points |
|---|---|---|
| ≤ 92 days | daily | ≤ 92 |
| ≤ 400 days | weekly | ~57 |
| longer | monthly | 120 for 10 years |

Buckets with no source observation are **absent from the response**. They are
not zero-filled and not interpolated: a gap in a satellite record is
information, and the charts render it as a gap (`connectNulls={false}`) rather
than bridging it with a line implying a measurement that was never made.

### Two-stage aggregation

Every regional figure collapses over **time within each region first**, then
across regions.

For rainfall this is dimensional necessity: sum over time, average over space.
Summing every district's rainfall into one number would produce a figure that
grows with how many districts you selected.

For state variables like NDVI and LST the reason is subtler but real: a flat
average over every row weights each district by how many observations it
happens to have, and that count reflects orbit geometry and cloud cover, not
geography. A district with 40 clear scenes would quietly outvote one with 4.

---

## 4. Classification and thresholds

**NDVI / EVI / crop condition** use conventional vigour bands, applied by the
backend and sent to the client with the data, so the legend cannot drift from
the classification actually used. See `NDVI_CLASSES` and
`CROP_CONDITION_CLASSES` in `app/services/geovision_service.py`.

Crop condition is labelled everywhere as a *satellite-derived indicator*, never
an agronomic diagnosis — NDVI cannot distinguish a fallow field from a failed
one.

**Rainfall and LST** deliberately have **no named bands**. There is no
climate-independent threshold for "high rainfall": 30 mm in a week is a drought
in monsoon Punjab and a deluge in Chagai. Inventing categories there would be
an unsupported claim, so the map uses a continuous ramp stretched over the
values actually present and the legend shows the real numeric endpoints.

## 5. Satellite Watch

Indicators fire on standardised departure from the seasonal baseline:

```
|z| >= 1.5  ADVISORY/WATCH
|z| >= 2.0  HIGH
|z| >= 3.0  CRITICAL
```

plus data-freshness indicators when a dataset exceeds two publication cycles
without new data.

Anomalies require at least **3 prior years** of the same season
(`MIN_BASELINE_YEARS`). Below that the API returns `insufficient_data` with the
reason rather than a z-score computed against one year of weather.

Each indicator carries the rule that produced it and the evidence behind it, so
a reader can check the reasoning instead of trusting a severity badge.

**These are not official warnings.** Every indicator carries
`is_official_warning: false` and the envelope carries a disclaimer stating they
have no authority from NDMA, PDMA or any government body.

---

## 6. Caching

Three tiers, cheapest first, and the reasoning differs per tier:

| What | Where | TTL | Why |
|---|---|---|---|
| overview, hierarchy, catalog | process memory | 120–300s | recomputed cheaply; bounds staleness after a manual run |
| region boundaries | **Postgres** (`gee_region_geometry`) | until config changes | see below |
| region boundaries | browser | 24h (`Cache-Control: private`) | ~620 KB, the largest response the dashboard fetches |

Boundary geometry is persisted to the database rather than only cached in
memory because exporting 119 simplified districts from Earth Engine takes
**~15 seconds**. In-process caching handles that on a long-lived server, but
not on serverless: every cold instance would pay it again, and the function
timeout is shorter than the export, so the map would fail rather than be slow.

The stored copy is keyed by the ROI configuration that produced it, so widening
the ROI or changing the simplification tolerance produces a new row instead of
serving boundaries that no longer match the observations. It is a cache of a
derived artifact, not a second region system.

To refresh boundaries after changing the ROI, delete the row:

```sql
DELETE FROM gee_region_geometry;   -- next request re-exports and re-persists
```

---

## 7. Database connection pooling

`app/db/database.py` picks the pool from the runtime rather than assuming one:

- **serverless** (`VERCEL=1`) → `NullPool`. A pooled asyncpg connection is
  bound to the event loop that opened it; a warm invocation gets a new loop and
  the connection fails with "attached to a different loop".
- **anything else** → pooled.

This is a latency decision, not a tidiness one. Measured against the Supabase
instance this project uses (ap-northeast-1):

```
open + close a connection      ~3.2 s
one query on an open one       ~0.2 s
```

With `NullPool` on a long-lived server every request paid that ~3.2s again.
Enabling pooling took endpoint latency from ~3.5s to ~1.2s. `DB_POOL_ENABLED`
overrides the automatic choice in either direction.

> If API latency matters more than it currently does, the largest remaining
> factor is geography: the database is in Tokyo. Moving the Supabase project
> closer to its users would cut the per-query round trip far more than any
> further query tuning. That is a data-migration decision, not a code change.

---

## 8. Frontend

```
src/store/geovisionFilters.ts        one source of truth for every filter
src/hooks/useGeovision.ts            React Query hooks, keyed off that store
src/components/geovision/            command centre components
```

Centralised filter state is what makes §50 hold: every panel reads the same
store, so they cannot disagree. The React Query key deliberately **excludes**
`selectedRegionId`, so opening the region detail panel does not re-fetch the
map, the KPI row and every chart.

Polling is configurable and defaults to 5 minutes (`DEFAULT_POLL_INTERVAL_MS`).
The pipeline writes at most once a night, so anything faster is load without
information.

### Running the tests

```bash
cd packages/dashboard
npm test          # vitest run
npm run test:watch
```
