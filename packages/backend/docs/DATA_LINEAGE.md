# Data lineage

Every displayed figure is traceable back to the scenes behind it.

## The chain

```
dashboard metric
   ↓ gv_derived_features   (windowed aggregate, calculation_version)
   ↓ gv_metric_baselines   (climatology, sample_years, version)
   ↓ gee_satellite_observations  (real acquisitions + quality)
   ↓ gv_dataset_registry   (collection, provider, resolution, cadence)
   ↓ processing method     (filter → mask → derive → scale → reduce → validate)
```

`GET /api/v1/intelligence/lineage?region_id=...&metric=...`

## Assembled, not stored

Lineage is built on demand from the registry, feature, baseline and observation
tables rather than written to a lineage table of its own.

A lineage row per displayed metric would duplicate what those tables already
record — and duplicated provenance is provenance that can disagree with itself.
The assembled chain cannot drift from the data because it *is* the data.

## What each step carries

| Step | Carries |
|---|---|
| derived_feature | value, window, observation count, `calculation_version`, computed_at |
| baseline | sample years, sufficiency flag, version |
| observations | 5 most recent source scenes: date, `source_image_id`, cloud %, pixel count, quality score and reason |
| dataset | collection id, provider, resolution, cadence |
| processing | the full transformation chain applied at ingestion |

## Observation time vs system time

Three distinct timestamps, never conflated:

- **`observation_timestamp`** — when the satellite acquired it
- **`ingested_at`** — when we wrote it
- **`computed_at`** — when the derived value was calculated

The dashboard shows the observation date next to every value. A number without
its observation date invites the reader to assume it is current, which for an
8- or 16-day composite is usually wrong.

The system never claims "real-time satellite data".

## Versions

Every derived row carries a `calculation_version`:

| Prefix | Layer |
|---|---|
| `q1` | data quality |
| `b1` | baselines |
| `f1` | derived features |
| `h1` | hazard scores |
| `fl1` | flood detection |
| `e1` | event lifecycle |
| `hs1` | hotspots |
| `rc1` | recovery |
| `a1` | alerts |
| `br1` | daily brief |

Change the maths, change the version. Old rows stay interpretable instead of
silently meaning something different from new ones.

## Audit

`gv_audit_logs` records operator actions: who, what, which entity, old state,
new state, when, metadata. Written for alert transitions, event verification,
field-report review and manual cycle triggers.
