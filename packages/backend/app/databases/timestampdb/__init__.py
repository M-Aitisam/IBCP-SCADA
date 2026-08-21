# packages/backend/app/databases/timestampdb/__init__.py
"""timestampdb — the project's time-series observation store.

Backed by the same Postgres instance the rest of the app uses (see
app.db.database), with the observations table created as a TimescaleDB
hypertable where the extension is available. Import from here rather than
reaching into the submodules.
"""
from app.databases.timestampdb.models import (
    IngestionCheckpoint,
    IngestionRun,
    SatelliteObservation,
    build_observation_key,
)
from app.databases.timestampdb.repository import (
    DryRunRepository,
    ObservationRecord,
    ObservationStore,
    TimestampRepository,
    WriteResult,
)

__all__ = [
    "IngestionCheckpoint",
    "IngestionRun",
    "SatelliteObservation",
    "build_observation_key",
    "DryRunRepository",
    "ObservationRecord",
    "ObservationStore",
    "TimestampRepository",
    "WriteResult",
]
