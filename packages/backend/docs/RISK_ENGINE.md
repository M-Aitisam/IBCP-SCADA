# Risk and hazard engines

All deterministic. No model training, no inference, no ML anywhere in this
layer. `app/intelligence/hazards.py` and `app/intelligence/flood.py` are pure
functions — testable without fixtures, arguable directly.

## Three commitments

**Anomalies, not absolutes.** Rainfall and temperature are scored by departure
from the region's own seasonal normal, never a universal threshold. There is no
rainfall figure that means "drought" everywhere: 30 mm in a week is a failed
monsoon in Punjab and unremarkable in Chagai.

Vegetation indices are the exception — NDVI has conventional vigour bands that
hold across regions — and even those are combined with anomalies, not used
alone.

**Confidence travels with the score.** `completeness × baseline_depth ×
data_quality`, multiplied rather than averaged so one weak leg pulls the whole
thing down. A two-component score on a three-year baseline must not look as
solid as a five-component score on a decade.

**Contributors are never hidden.** Every score returns its components, weights
and signed contributions. A score that cannot be interrogated will not be
trusted, and should not be.

## Hazards and weights

| Hazard | Inputs | Weights |
|---|---|---|
| drought | rainfall / NDVI / LST anomaly | 0.40 / 0.35 / 0.25 |
| crop_stress | NDVI level + NDVI / moisture / rainfall / LST anomaly | 0.30 / 0.25 / 0.15 / 0.15 / 0.15 |
| heat_stress | day + night LST anomaly | 0.60 / 0.40 |
| flood | Sentinel-1 water-fraction anomaly | see below |
| multi_hazard | fusion of the above | 0.35 / 0.30 / 0.20 / 0.15 |

Night LST is weighted alongside day deliberately: sustained warm nights prevent
overnight recovery, so a hot day that cools off is not the same hazard as one
that does not.

### Not SPI

The drought score is a weighted standardised-anomaly composite. It is **not**
SPI — SPI requires fitting a gamma distribution to a long precipitation record.
Calling it SPI would misrepresent the method. It becomes available once the
historical backfill provides the record.

## Flood detection

**Absolute water fraction is never used to detect flooding — only its departure
from the region's own baseline.**

Open water is specular and returns low C-band backscatter, so thresholding VV
at −15 dB finds water. But smooth *dry* surfaces are dark too: sand sheets, dry
lake beds, bare rock. Measured on this database, an arid Balochistan district
reads **0.61 water fraction in a dry August**. Treating that as 61% flooded
would be nonsense.

Differencing cancels the persistent part — a sand sheet is dark in the baseline
too — leaving *newly* dark area, which is what a flood is.

```
new_water_fraction = current_fraction − baseline_fraction
flooded_area_km2   = new_water_fraction × region_area_km2
```

The per-pixel classification happens **inside Earth Engine**: `VV.lt(−15)`
produces a 0/1 mask, and the MEAN reducer over a district turns it into a
fraction. No raster crosses the wire.

Region areas come from `gv_region_geometry_stats`, computed from stored
boundaries by spherical-excess summation. Validated against reality: Chagai
49,724 km² (genuinely Pakistan's largest district), Karachi Central 63 km².

Severity bands on the **new** fraction: 0.05 minor, 0.10 moderate, 0.20 major,
0.35 severe. A standardised-departure escape catches a small-but-unusual rise
in a region whose baseline is tight.

### Documented limitation

Radar shadow and smooth dry surfaces are counted as water by the threshold.
Differencing removes the persistent component; it does not remove a
*newly* smooth dry surface. This is stated in the code, not hidden.

## Refusing to score

| Situation | Result |
|---|---|
| Fewer than 2 components | `INSUFFICIENT_DATA`, no score |
| No baseline | `UNKNOWN` — never `NONE` |
| Baseline under 3 years | `UNKNOWN`, with the year count |
| Data quality below floor | withheld, with the reason |

`UNKNOWN` means "cannot tell". `NONE` means "checked and clear". A district that
has not been assessed must never be presented as safe.

Absent hazards are **excluded from fusion and the weights renormalised**, never
scored as zero. Absent evidence is not evidence of absence.

## Hotspots

Contiguous clusters of affected regions (`app/intelligence/patterns.py`).
Breadth-first search over a precomputed adjacency graph — deterministic and
explainable, not a clustering model.

Adjacency is bounding-box overlap with a ~5 km buffer, an approximation
documented at the call site. It can merge two nearby clusters (understating the
count); it will not fragment one real regional emergency into several, which
would hide it. Minimum cluster size 3: one stressed district is a stressed
district; three contiguous ones suggest a shared regional driver.

## Where ML plugs in later

Every engine takes numbers and returns a score plus contributors. A model could
replace any single scoring function without touching the event engine, the
alert engine, the fusion, or the API. Nothing downstream knows how a score was
produced — only that it carries a `calculation_version`.
