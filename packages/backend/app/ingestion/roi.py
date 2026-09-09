# packages/backend/app/ingestion/roi.py
"""Region-of-interest resolution.

The repository contained no tehsil geometry when this module was written, so
nothing here invents boundaries. Two supported sources, in priority order:

  1. GEE_ROI_GEOJSON_PATH - a supplied FeatureCollection/GeoJSON file. Use this
     once real tehsil boundaries are available; set the property names to match
     the file and the pipeline stores true tehsil-level records.
  2. GEE_ROI_ASSET_ID     - a server-side FeatureCollection. The default is
     FAO/GAUL/2015/level2 filtered to Balochistan and Sindh.

Note on granularity: FAO GAUL level 2 is *district* (ADM2) for Pakistan, not
tehsil. The default therefore yields district-level records with region_type
"district" and tehsil left null, which is accurate rather than mislabelled.
Supply a tehsil GeoJSON to get tehsil granularity.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.ingestion.config import IngestionSettings, ingestion_settings
from app.ingestion.gee_client import EarthEngineClient

logger = logging.getLogger(__name__)


class ROIConfigurationError(RuntimeError):
    """The configured ROI cannot be resolved."""


@dataclass(frozen=True)
class Region:
    """One aggregation unit, as stored on every observation."""

    region_id: str
    region_type: str
    province: Optional[str] = None
    district: Optional[str] = None
    tehsil: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@dataclass
class ROI:
    """The resolved region set plus its server-side FeatureCollection."""

    feature_collection: Any
    regions: dict[str, Region]
    source: str
    region_type: str
    # Property on each feature that carries region_id, used to join the
    # reduceRegions output back to Region metadata.
    id_property: str

    def __len__(self) -> int:
        return len(self.regions)


class ROIResolver:
    def __init__(
        self,
        client: EarthEngineClient,
        settings: Optional[IngestionSettings] = None,
    ):
        self.client = client
        self.settings = settings or ingestion_settings

    def resolve(self) -> ROI:
        if self.settings.GEE_ROI_GEOJSON_PATH:
            roi = self._from_geojson(Path(self.settings.GEE_ROI_GEOJSON_PATH))
        else:
            roi = self._from_asset()

        if not roi.regions:
            raise ROIConfigurationError(
                f"ROI source {roi.source!r} resolved to zero regions. Check "
                "GEE_ROI_PROVINCES / GEE_ROI_COUNTRY spelling against the asset's "
                "property values."
            )
        logger.info(
            "ROI resolved: %d %s regions from %s",
            len(roi.regions),
            roi.region_type,
            roi.source,
        )
        return roi

    # ------------------------------------------------------------------

    def _from_asset(self) -> ROI:
        ee = self.client.ee
        s = self.settings

        collection = ee.FeatureCollection(s.GEE_ROI_ASSET_ID)

        # Country filter first: GAUL province names are not globally unique.
        if s.GEE_ROI_COUNTRY and s.GEE_ROI_COUNTRY_PROPERTY:
            collection = collection.filter(
                ee.Filter.eq(s.GEE_ROI_COUNTRY_PROPERTY, s.GEE_ROI_COUNTRY)
            )
        provinces = s.roi_provinces
        if provinces and s.GEE_ROI_PROVINCE_PROPERTY:
            collection = collection.filter(
                ee.Filter.inList(s.GEE_ROI_PROVINCE_PROPERTY, provinces)
            )

        if s.GEE_ROI_LIMIT:
            collection = collection.limit(s.GEE_ROI_LIMIT)

        # Stamp a normalised region_id onto every feature so downstream joins
        # do not depend on which property the source happens to use.
        id_prop = s.GEE_ROI_ID_PROPERTY

        def _stamp(feature):
            # feature.get() returns an untyped ComputedObject, so it has no
            # .format()/.cat() until it is cast. The id property may legitimately
            # be a number (GAUL's ADM2_CODE is an int) or a string in a
            # user-supplied asset, so coerce generically rather than assuming.
            return feature.set(
                "__region_id", ee.Algorithms.String(feature.get(id_prop))
            )

        collection = collection.map(_stamp)

        properties = [
            p
            for p in (
                "__region_id",
                s.GEE_ROI_PROVINCE_PROPERTY,
                s.GEE_ROI_DISTRICT_PROPERTY,
                s.GEE_ROI_TEHSIL_PROPERTY,
            )
            if p
        ]

        # One getInfo for all region metadata; geometry stays server-side.
        info = self.client.with_retry(
            lambda: collection.select(properties, retainGeometry=False)
            .getInfo(),
            description="resolve ROI metadata",
        )

        regions: dict[str, Region] = {}
        for feature in info.get("features", []):
            props = feature.get("properties", {})
            region_id = str(props.get("__region_id", "")).strip()
            if not region_id:
                continue
            regions[region_id] = Region(
                region_id=region_id,
                region_type=s.GEE_ROI_REGION_TYPE,
                province=_opt(props.get(s.GEE_ROI_PROVINCE_PROPERTY)),
                district=_opt(props.get(s.GEE_ROI_DISTRICT_PROPERTY)),
                tehsil=_opt(props.get(s.GEE_ROI_TEHSIL_PROPERTY))
                if s.GEE_ROI_TEHSIL_PROPERTY
                else None,
            )

        return ROI(
            feature_collection=collection,
            regions=regions,
            source=f"{s.GEE_ROI_ASSET_ID} [{','.join(provinces) or 'all'}]",
            region_type=s.GEE_ROI_REGION_TYPE,
            id_property="__region_id",
        )

    def _from_geojson(self, path: Path) -> ROI:
        if not path.exists():
            raise ROIConfigurationError(
                f"GEE_ROI_GEOJSON_PATH points at {path}, which does not exist"
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ROIConfigurationError(f"{path} is not valid GeoJSON: {exc}") from exc

        features = raw.get("features") if isinstance(raw, dict) else None
        if not features:
            raise ROIConfigurationError(
                f"{path} has no 'features'; expected a GeoJSON FeatureCollection"
            )

        ee = self.client.ee
        s = self.settings
        regions: dict[str, Region] = {}
        ee_features = []

        for index, feature in enumerate(features):
            props = dict(feature.get("properties") or {})
            region_id = str(
                props.get(s.GEE_ROI_ID_PROPERTY)
                or props.get("region_id")
                or index
            ).strip()
            if region_id in regions:
                raise ROIConfigurationError(
                    f"{path}: duplicate region id {region_id!r}; region_id must be "
                    f"unique because it is part of the observation key"
                )
            regions[region_id] = Region(
                region_id=region_id,
                region_type=s.GEE_ROI_REGION_TYPE,
                province=_opt(props.get(s.GEE_ROI_PROVINCE_PROPERTY)),
                district=_opt(props.get(s.GEE_ROI_DISTRICT_PROPERTY)),
                tehsil=_opt(props.get(s.GEE_ROI_TEHSIL_PROPERTY))
                if s.GEE_ROI_TEHSIL_PROPERTY
                else None,
            )
            props["__region_id"] = region_id
            ee_features.append(ee.Feature(feature["geometry"], props))

            if s.GEE_ROI_LIMIT and len(regions) >= s.GEE_ROI_LIMIT:
                break

        return ROI(
            feature_collection=ee.FeatureCollection(ee_features),
            regions=regions,
            source=str(path),
            region_type=s.GEE_ROI_REGION_TYPE,
            id_property="__region_id",
        )


def _opt(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
