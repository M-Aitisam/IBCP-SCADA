# packages/backend/app/ingestion/validation.py
"""Pre-storage validation.

Rejected observations are never silently clamped or dropped — each one is
returned with a reason so the run log can report why. A record that fails
validation does not reach timestampdb.
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional, Sequence

from app.databases.timestampdb.repository import ObservationRecord
from app.ingestion.registry import DatasetConfig

logger = logging.getLogger(__name__)


class RejectionReason(str):
    pass


NOT_FINITE = "value_not_finite"
OUT_OF_RANGE = "value_out_of_physical_range"
BAD_TIMESTAMP = "invalid_timestamp"
FUTURE_TIMESTAMP = "timestamp_in_future"
UNKNOWN_REGION = "region_not_in_configured_roi"
UNKNOWN_METRIC = "metric_not_in_dataset_config"
MISSING_SOURCE_IMAGE = "missing_source_image_id"
NULL_VALUE = "null_value_from_source"


@dataclass
class ValidationOutcome:
    accepted: list[ObservationRecord] = field(default_factory=list)
    rejected: list[tuple[ObservationRecord, str]] = field(default_factory=list)

    @property
    def rejection_summary(self) -> dict[str, int]:
        return dict(Counter(reason for _, reason in self.rejected))

    def log_rejections(self, dataset: str, sample: int = 5) -> None:
        if not self.rejected:
            return
        summary = self.rejection_summary
        logger.warning(
            "dataset=%s validation rejected %d record(s): %s",
            dataset,
            len(self.rejected),
            summary,
        )
        for record, reason in self.rejected[:sample]:
            logger.warning(
                "  rejected dataset=%s metric=%s region=%s date=%s value=%r reason=%s",
                record.dataset,
                record.metric,
                record.region_id,
                record.observation_date,
                record.value,
                reason,
            )


class Validator:
    """Applies dataset-driven rules. No dataset-specific branches live here —
    ranges and units come from the registry."""

    def __init__(
        self,
        config: DatasetConfig,
        valid_region_ids: set[str],
        *,
        allow_null_values: bool = True,
        max_future_skew_days: int = 2,
    ):
        self.config = config
        self.valid_region_ids = valid_region_ids
        # A null reduction is legitimate: it means every pixel in the region was
        # masked out (all cloud, or outside the swath). Stored as a null-valued
        # record with processing_status "no_valid_pixels" so the gap is visible
        # rather than being confused with "not yet ingested".
        self.allow_null_values = allow_null_values
        self.max_future_skew_days = max_future_skew_days

    def validate(self, records: Sequence[ObservationRecord]) -> ValidationOutcome:
        outcome = ValidationOutcome()
        now = datetime.now(timezone.utc)

        for record in records:
            reason = self._check(record, now)
            if reason is None:
                outcome.accepted.append(record)
            else:
                record.processing_status = "rejected"
                outcome.rejected.append((record, reason))
        return outcome

    # ------------------------------------------------------------------

    def _check(self, record: ObservationRecord, now: datetime) -> Optional[str]:
        if record.metric not in self.config.all_metrics:
            return UNKNOWN_METRIC

        if self.valid_region_ids and record.region_id not in self.valid_region_ids:
            return UNKNOWN_REGION

        if not record.source_image_id:
            return MISSING_SOURCE_IMAGE

        timestamp = record.observation_timestamp
        if not isinstance(timestamp, datetime):
            return BAD_TIMESTAMP
        if timestamp.tzinfo is None:
            return BAD_TIMESTAMP
        if not isinstance(record.observation_date, date):
            return BAD_TIMESTAMP
        # A satellite cannot observe the future; a timestamp past today means a
        # unit mix-up (seconds vs milliseconds) rather than real data.
        if (timestamp - now).days > self.max_future_skew_days:
            return FUTURE_TIMESTAMP

        if record.value is None:
            if self.allow_null_values:
                record.processing_status = "no_valid_pixels"
                return None
            return NULL_VALUE

        if not isinstance(record.value, (int, float)) or isinstance(record.value, bool):
            return NOT_FINITE
        if math.isnan(record.value) or math.isinf(record.value):
            return NOT_FINITE

        band_spec = self.config.band_for_metric(record.metric)
        if band_spec is not None:
            if band_spec.is_quality_band:
                # QA bitfields have no physical range to check.
                return None
            if band_spec.valid_range and not band_spec.valid_range.contains(record.value):
                return OUT_OF_RANGE
            return None

        derived_spec = self.config.derived_for_metric(record.metric)
        if derived_spec is not None:
            if derived_spec.valid_range and not derived_spec.valid_range.contains(
                record.value
            ):
                return OUT_OF_RANGE
            return None

        return UNKNOWN_METRIC
