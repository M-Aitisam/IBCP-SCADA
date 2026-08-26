# Event engine

## The idea

**An observation is not an event.**

A flood lasting nine days produces dozens of observations across several
datasets. Treating each as its own event hands an operator a list of dozens of
floods for one flood — which is how alert fatigue starts, and how a real
emergency gets lost among its own duplicates.

An event is created once, updated as observations arrive, and resolved when
conditions normalise.

Implemented in `app/intelligence/events.py` (pure rules) and
`app/intelligence/event_service.py` (persistence). No ML anywhere.

## Lifecycle

```
        score >= onset                persisted N cycles
NONE ─────────────────► DETECTED ──────────────────► CONFIRMED
                            │                            │
                            │      score rises sharply   │
                            └──────────► ESCALATING ◄────┘
                                             │
                                     at/near highest score
                                             ▼
                                           PEAK
                                             │
                                    score falls sharply
                                             ▼
                                        DECLINING
                                             │
                             below recovery for N cycles
                                             ▼
                                         RESOLVED
```

Verified end to end: a 7-cycle flood trajectory produced exactly **one** event
traversing `DETECTED → ESCALATING → PEAK → DECLINING → RESOLVED` with 7
timeline frames.

## Deduplication

Identity is **(hazard_type, region_id) among rows that are not RESOLVED**. One
open event per pair. A second detection while an event is open updates that
event rather than creating a sibling.

## Thresholds

`EventRules` per hazard, in `RULES`. Stated as data so they can be tuned,
documented and asserted in tests rather than buried in branches.

| Hazard | onset | recovery | confirm after | resolve after |
|---|---|---|---|---|
| flood | 35 | 25 | 1 cycle | 2 cycles |
| drought | 40 | 28 | 2 | 3 |
| heat_stress | 45 | 32 | 2 | 2 |
| crop_stress | 45 | 32 | 2 | 3 |
| multi_hazard | 50 | 35 | 2 | 2 |

**Flood confirms in one cycle** because flood onset is genuinely sudden and
waiting could mean a day too late. **Drought resolves over three** because a
single wet week does not end a drought.

`onset > recovery` in every row. That gap is hysteresis: without it a score
hovering at the boundary opens and closes an event every cycle, and the event
history becomes unreadable.

## Two rules that must not break

**1. A missing observation never resolves an open event.** A gap in satellite
coverage is not evidence a flood ended; resolving on absent data would quietly
close real emergencies. Pinned by
`test_missing_observation_never_resolves_an_open_event`.

**2. Poor data quality holds an event at DETECTED.** Acting on a badly observed
signal is how false emergencies happen. Below `min_quality_to_confirm` an event
is tracked but never confirmed.

## Timeline and replay

Every cycle appends a row to `gv_event_observations`: status, severity, score,
extent, transition. That is what `/events/{id}/timeline` and `/events/replay`
read.

Stored rather than recomputed on demand — replaying a recomputation against
today's data would rewrite history and make the feature worthless as a record.

## Before / during / after

`impact_window()` returns three **non-overlapping** windows. A "before" window
containing the event would compare the event against itself and understate the
impact.

## Recovery

`app/intelligence/patterns.py`. Measured from the event's low point, not from
zero:

```
recovery% = (current − minimum) / (pre_event − minimum) × 100
```

NDVI falling 0.48 → 0.29 and climbing to 0.36 has recovered **37% of what it
lost**, not 75% of some absolute scale. The denominator is the loss, which is
the only quantity the question is about.

States: `RECOVERING`, `SLOW_RECOVERY`, `STALLED`, `RECOVERED`, `UNKNOWN`.

**This is a satellite-derived recovery indicator, not ecological or economic
recovery.** NDVI returning says the vegetation signal returned. It says nothing
about whether a crop was replanted or a farmer compensated.

## Extension point for ML

`evaluate()` takes a score and a history and returns a decision. Where the
score comes from — a deterministic composite today, a model later — is not its
concern. Swapping in a predicted score requires no change to this module.
