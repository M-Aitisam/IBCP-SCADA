# packages/backend/app/ingestion/extractor.py
"""GEE query, server-side reduction, and normalisation into ObservationRecords.

Design constraint: no raster ever leaves Earth Engine. For each date chunk the
pipeline builds one server-side computation

    filterDate -> filterBounds -> quality filter -> select
               -> map(reduceRegions over the whole ROI) -> flatten

and pulls the result back with a single getInfo(). That is one round-trip per
(dataset, chunk), not per region and certainly not per pixel.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.databases.timestampdb.repository import ObservationRecord
from app.ingestion.config import IngestionSettings, ingestion_settings
from app.ingestion.gee_client import EarthEngineClient
from app.ingestion.registry import BandSpec, DatasetConfig, DerivedSpec, Reducer
from app.ingestion.roi import ROI

logger = logging.getLogger(__name__)

# Reducer output suffixes produced by the combined reducer below.
_STAT_KEYS = ("mean", "median", "min", "max", "count", "sum")


@dataclass
class ChunkResult:
    records: list[ObservationRecord]
    images_found: int
    latest_observation: Optional[date] = None


class Extractor:
    def __init__(
        self,
        client: EarthEngineClient,
        roi: ROI,
        settings: Optional[IngestionSettings] = None,
    ):
        self.client = client
        self.roi = roi
        self.settings = settings or ingestion_settings

    # ------------------------------------------------------------------
    # Collection assembly
    # ------------------------------------------------------------------

    def build_collection(
        self, config: DatasetConfig, start: date, end: date
    ) -> Any:
        """Filtered ImageCollection for one dataset and date window.

        `end` is exclusive on the GEE side, matching filterDate semantics.
        """
        ee = self.client.ee
        collection = ee.ImageCollection(config.asset_id)
        collection = collection.filterDate(start.isoformat(), end.isoformat())
        collection = collection.filterBounds(self.roi.feature_collection.geometry())

        # Scene-level cloud screening, only where the collection publishes the
        # property. Never applied to SAR (config leaves cloud_property None).
        if config.cloud_property and config.cloud_threshold is not None:
            threshold = (
                self.settings.GEE_CLOUD_THRESHOLD
                if config.name == "sentinel2"
                else config.cloud_threshold
            )
            collection = collection.filter(
                ee.Filter.lte(config.cloud_property, threshold)
            )

        for prop, operator, value in config.property_filters:
            filter_fn = getattr(ee.Filter, operator, None)
            if filter_fn is None:
                raise ValueError(
                    f"{config.name}: unsupported property filter operator {operator!r}"
                )
            collection = collection.filter(filter_fn(prop, value))

        # Sentinel-1 carries VV/VH in separate scenes depending on
        # polarisation; require both bands so reduceRegions never sees a
        # missing band.
        if config.name == "sentinel1":
            for band in ("VV", "VH"):
                collection = collection.filter(
                    ee.Filter.listContains("transmitterReceiverPolarisation", band)
                )

        return collection

    def _apply_mask(self, image: Any, config: DatasetConfig) -> Any:
        """Pixel-level quality masking, per the dataset's declared strategy."""
        if config.mask_strategy == "s2_scl":
            # Scene Classification Layer: drop saturated(1), cloud shadow(3),
            # cloud medium/high probability(8,9), cirrus(10) and snow(11).
            scl = image.select("SCL")
            bad = (
                scl.eq(1)
                .Or(scl.eq(3))
                .Or(scl.eq(8))
                .Or(scl.eq(9))
                .Or(scl.eq(10))
                .Or(scl.eq(11))
            )
            return image.updateMask(bad.Not())
        return image

    def _prepare_image(self, image: Any, config: DatasetConfig) -> Any:
        """Mask, then select the bands to reduce plus any derived band."""
        ee = self.client.ee
        masked = self._apply_mask(image, config)

        band_names = [spec.band for spec in config.bands]
        prepared = masked.select(band_names)

        for spec in config.derived:
            if spec.expression == "normalizedDifference":
                # Computed server-side from the *masked* image so cloud pixels
                # never contribute to the index.
                nd = masked.normalizedDifference(list(spec.bands)).rename(spec.metric)
                prepared = prepared.addBands(nd)
            else:
                raise ValueError(
                    f"{config.name}: unsupported derived expression {spec.expression!r}"
                )
        # copyProperties returns an ee.Element, which has no .select(). Cast
        # back to Image so the caller can keep treating it as one.
        return ee.Image(prepared.copyProperties(image, image.propertyNames()))

    def _reducer_for(self, group: Reducer) -> Any:
        """Primary reducer combined with the descriptive stats we always store."""
        ee = self.client.ee
        primary = {
            Reducer.MEAN: ee.Reducer.mean(),
            Reducer.MEDIAN: ee.Reducer.median(),
            Reducer.SUM: ee.Reducer.sum(),
        }[group]
        return (
            primary.combine(ee.Reducer.minMax(), sharedInputs=True)
            .combine(ee.Reducer.count(), sharedInputs=True)
        )

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract_chunk(
        self, config: DatasetConfig, start: date, end: date
    ) -> ChunkResult:
        """Reduce every image in [start, end) over every ROI region."""
        ee = self.client.ee
        collection = self.build_collection(config, start, end)

        images_found = self.client.with_retry(
            lambda: collection.size().getInfo(),
            description=f"{config.name} count {start}..{end}",
        )
        if not images_found:
            logger.info(
                "dataset=%s window=%s..%s images=0 (no source observations)",
                config.name,
                start,
                end,
            )
            return ChunkResult(records=[], images_found=0)

        # Group bands by reducer so each group needs only one reduceRegions.
        groups: dict[Reducer, list[str]] = {}
        for spec in config.bands:
            groups.setdefault(spec.reducer, []).append(spec.band)
        for spec in config.derived:
            groups.setdefault(spec.reducer, []).append(spec.metric)

        metadata_props = list(config.metadata_properties)
        if config.cloud_property:
            metadata_props.append(config.cloud_property)

        roi_fc = self.roi.feature_collection
        scale = config.spatial_resolution
        id_property = self.roi.id_property

        all_features: list[dict] = []
        for reducer_group, band_names in groups.items():
            reducer = self._reducer_for(reducer_group)

            def _reduce_image(image, band_names=band_names, reducer=reducer):
                prepared = self._prepare_image(image, config).select(band_names)
                reduced = prepared.reduceRegions(
                    collection=roi_fc,
                    reducer=reducer,
                    scale=scale,
                )
                # Stamp image-level provenance onto every region feature so the
                # flattened result is self-describing.
                # reduceRegions names its outputs "<band>_<stat>" for a
                # multi-band image but bare "<stat>" for a single-band one.
                # Record which case this group is so the parser is never
                # guessing, and so two single-band groups cannot collide.
                sole_band = band_names[0] if len(band_names) == 1 else ""
                stamped = reduced.map(
                    lambda feature: feature.set(
                        {
                            "__image_id": image.get("system:index"),
                            "__time_start": image.get("system:time_start"),
                            "__sole_band": sole_band,
                        }
                    ).copyProperties(image, metadata_props)
                )
                return stamped

            reduced_fc = ee.FeatureCollection(
                collection.map(_reduce_image)
            ).flatten()

            info = self.client.with_retry(
                lambda fc=reduced_fc: fc.getInfo(),
                description=f"{config.name} reduce {reducer_group.value} {start}..{end}",
            )
            all_features.extend(info.get("features", []))

        records, latest = self._normalise(config, all_features, id_property)
        logger.info(
            "dataset=%s window=%s..%s images=%d regions=%d records=%d latest=%s",
            config.name,
            start,
            end,
            images_found,
            len(self.roi.regions),
            len(records),
            latest,
        )
        return ChunkResult(
            records=records, images_found=images_found, latest_observation=latest
        )

    # ------------------------------------------------------------------

    def _normalise(
        self, config: DatasetConfig, features: list[dict], id_property: str
    ) -> tuple[list[ObservationRecord], Optional[date]]:
        """Turn raw reduceRegions output into scaled, unit-correct records."""
        records: list[ObservationRecord] = []
        latest: Optional[date] = None

        for feature in features:
            props = feature.get("properties", {}) or {}
            region_id = str(props.get(id_property, "")).strip()
            region = self.roi.regions.get(region_id)
            if region is None:
                continue

            timestamp = _epoch_ms_to_datetime(props.get("__time_start"))
            if timestamp is None:
                continue
            observation_date = timestamp.date()
            latest = observation_date if latest is None else max(latest, observation_date)

            image_id = str(props.get("__image_id") or "")
            cloud_pct = _as_float(
                props.get(config.cloud_property) if config.cloud_property else None
            )
            metadata = {
                key: props[key]
                for key in config.metadata_properties
                if key in props and props[key] is not None
            }

            for spec in config.bands:
                record = self._record_from_band(
                    config, spec, props, region, timestamp, observation_date,
                    image_id, cloud_pct, metadata,
                )
                if record is not None:
                    records.append(record)

            for spec in config.derived:
                record = self._record_from_derived(
                    config, spec, props, region, timestamp, observation_date,
                    image_id, cloud_pct, metadata,
                )
                if record is not None:
                    records.append(record)

        return records, latest

    def _record_from_band(
        self, config, spec: BandSpec, props, region, timestamp, observation_date,
        image_id, cloud_pct, metadata,
    ) -> Optional[ObservationRecord]:
        stats = _collect_stats(props, spec.band, spec.reducer)
        if stats is None:
            return None

        primary_raw = stats.get("primary")
        # Scale factor and unit transform are applied exactly once, here.
        value = spec.to_physical(primary_raw) if primary_raw is not None else None

        return ObservationRecord(
            dataset=config.name,
            dataset_asset_id=config.asset_id,
            dataset_version=config.version,
            region_id=region.region_id,
            region_type=region.region_type,
            province=region.province,
            district=region.district,
            tehsil=region.tehsil,
            band=spec.band,
            metric=spec.metric,
            derived_metric=None,  # raw measurement
            observation_timestamp=timestamp,
            observation_date=observation_date,
            source_image_id=image_id,
            unit=spec.unit,
            scale_factor=spec.scale_factor,
            value=value,
            min_value=_scaled(spec, stats.get("min")),
            max_value=_scaled(spec, stats.get("max")),
            mean_value=_scaled(spec, stats.get("mean")),
            median_value=_scaled(spec, stats.get("median")),
            pixel_count=_as_int(stats.get("count")),
            cloud_percentage=cloud_pct,
            spatial_resolution=config.spatial_resolution,
            acquisition_metadata=metadata or None,
            quality_flag="quality_band" if spec.is_quality_band else None,
        )

    def _record_from_derived(
        self, config, spec: DerivedSpec, props, region, timestamp, observation_date,
        image_id, cloud_pct, metadata,
    ) -> Optional[ObservationRecord]:
        stats = _collect_stats(props, spec.metric, spec.reducer)
        if stats is None:
            return None

        return ObservationRecord(
            dataset=config.name,
            dataset_asset_id=config.asset_id,
            dataset_version=config.version,
            region_id=region.region_id,
            region_type=region.region_type,
            province=region.province,
            district=region.district,
            tehsil=region.tehsil,
            band=None,
            metric=spec.metric,
            # The one thing that marks this as computed, not measured.
            derived_metric=spec.metric.upper(),
            observation_timestamp=timestamp,
            observation_date=observation_date,
            source_image_id=image_id,
            unit=spec.unit,
            scale_factor=1.0,
            value=_as_float(stats.get("primary")),
            min_value=_as_float(stats.get("min")),
            max_value=_as_float(stats.get("max")),
            mean_value=_as_float(stats.get("mean")),
            median_value=_as_float(stats.get("median")),
            pixel_count=_as_int(stats.get("count")),
            cloud_percentage=cloud_pct,
            spatial_resolution=config.spatial_resolution,
            acquisition_metadata=metadata or None,
        )

    # ------------------------------------------------------------------

    # Look-back windows tried in order when probing for the newest observation.
    # Starting narrow matters: sorting or scanning the full archive of a dense
    # collection (Sentinel-2 is millions of scenes) exceeds GEE's per-request
    # memory limit. Nearly every probe is answered by the first window.
    AVAILABILITY_PROBE_DAYS = (60, 180, 730, 3650, None)

    def latest_available(self, config: DatasetConfig, ceiling: date) -> Optional[date]:
        """Newest observation GEE actually holds for this dataset, at or below
        `ceiling`.

        Uses aggregate_max rather than sort().first(): the sort materialises the
        whole filtered collection server-side, which is what blew the memory
        limit on Sentinel-2. aggregate_max is a single reduction over the
        property and stays cheap regardless of collection size.
        """
        ee = self.client.ee
        geometry = self.roi.feature_collection.geometry()
        end = (ceiling + timedelta(days=1)).isoformat()

        for days in self.AVAILABILITY_PROBE_DAYS:
            start = (
                "1980-01-01"
                if days is None
                else (ceiling - timedelta(days=days)).isoformat()
            )
            collection = (
                ee.ImageCollection(config.asset_id)
                .filterBounds(geometry)
                .filterDate(start, end)
            )

            millis = self.client.with_retry(
                lambda c=collection: c.aggregate_max("system:time_start").getInfo(),
                description=f"{config.name} latest availability ({days or 'all'}d)",
            )
            stamp = _epoch_ms_to_datetime(millis)
            if stamp is not None:
                return stamp.date()

        # Nothing anywhere in the archive intersects this ROI at or before the
        # ceiling. Reported as no_source_data, never guessed at.
        return None


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _collect_stats(props: dict, name: str, reducer: Reducer) -> Optional[dict]:
    """Pull one band's reducer outputs out of a flattened feature.

    Earth Engine names reduceRegions outputs `<band>_<stat>` when the reduced
    image has several bands, but bare `<stat>` when it has exactly one. The
    `__sole_band` marker set during reduction says which convention applies,
    so a single-band group is read correctly instead of silently yielding no
    records.
    """
    stats: dict[str, Any] = {}
    found = False

    for stat in _STAT_KEYS:
        key = f"{name}_{stat}"
        if key in props:
            stats[stat] = props[key]
            found = True

    if not found and props.get("__sole_band") == name:
        for stat in _STAT_KEYS:
            if stat in props:
                stats[stat] = props[stat]
                found = True

    # Some reducers emit the band name alone rather than a suffixed key.
    if not found and name in props:
        stats.setdefault(reducer.value, props[name])
        found = True

    if not found:
        return None

    stats["primary"] = _as_float(stats.get(reducer.value))
    for stat in ("mean", "median", "min", "max"):
        stats[stat] = _as_float(stats.get(stat))
    return stats


def _scaled(spec: BandSpec, raw: Optional[float]) -> Optional[float]:
    return None if raw is None else spec.to_physical(raw)


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    number = _as_float(value)
    return None if number is None else int(number)


def _epoch_ms_to_datetime(millis: Any) -> Optional[datetime]:
    """GEE system:time_start is epoch milliseconds UTC."""
    number = _as_float(millis)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
