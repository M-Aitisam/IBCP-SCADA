# packages/backend/app/intelligence/__init__.py
"""Derived intelligence over stored satellite observations.

Everything in this package computes; nothing here acquires. Acquisition lives
in `app.ingestion` and remains the only writer of
`gee_satellite_observations`.

The split matters operationally: ingestion is slow, network-bound and runs in
GitHub Actions, while these computations are fast, database-bound and can be
re-run at will. Keeping them apart means a change to the drought formula does
not require re-downloading a decade of imagery.

Module order mirrors the dependency chain:

    quality    -> per-observation trust score
    baselines  -> reproducible climatology per region/metric/season
    features   -> temporal intelligence (change, anomaly, percentile)
    hazards    -> drought, heat, crop health, flood proxy, fused risk
    alerts     -> rule evaluation with hysteresis and deduplication
    brief      -> daily situation summary
    lineage    -> provenance assembly for any displayed figure
    orchestrator -> runs the chain as recorded, resumable stages
"""
