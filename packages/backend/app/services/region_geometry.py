# packages/backend/app/services/region_geometry.py
"""Region boundaries for the GIS map.

The polygons come from exactly the same source the ingestion ROI is resolved
from — `ROIResolver`, reading FAO/GAUL (or a supplied GeoJSON when one is
configured). That is deliberate: a second boundary source would eventually
disagree with the first, and a map whose shapes do not match the region_ids on
the observations is worse than no map.

Nothing here invents or edits a boundary. Simplification reduces vertex count;
it does not move borders to taste.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import GEOMETRY_TTL, cache
from app.databases.timestampdb.models import RegionGeometryCache, utcnow
from app.ingestion.config import IngestionSettings, ingestion_settings
from app.ingestion.gee_client import EarthEngineClient, GEEAuthError
from app.ingestion.roi import ROIConfigurationError

logger = logging.getLogger(__name__)

GEOMETRY_CACHE_KEY = "geometry:regions"

# Simplification tolerance in metres, applied server-side by Earth Engine.
#
# District boundaries rendered across a country-scale map are a few pixels wide
# at best, so ~1 km of vertex error is invisible while cutting the payload by
# well over an order of magnitude. Full-resolution GAUL for 119 districts is
# several megabytes and would make the map unusable on a normal connection.
DEFAULT_SIMPLIFY_METRES = 1000.0
# Guard rails: below this, the payload explodes; above it, districts visibly
# lose their shape and the map starts lying about where borders are.
MIN_SIMPLIFY_METRES = 100.0
MAX_SIMPLIFY_METRES = 20_000.0


class GeometryUnavailable(RuntimeError):
    """Boundaries could not be produced. Carries a reason fit to show a user."""


def _extract_polygons(geometry: Optional[dict]) -> list:
    """Collect every polygonal part of a geometry as a list of ring-arrays.

    Earth Engine does not emit strict GeoJSON, and two of its quirks break a
    map renderer outright:

    1. `LinearRing` — a type Earth Engine uses but the GeoJSON spec does not
       define. Leaflet's geometryToLayer switches on geometry.type and throws
       "Invalid GeoJSON object" on anything unrecognised, so a SINGLE
       LinearRing anywhere in the export kills the whole map. Observed here:
       exactly one, buried inside a GeometryCollection among 678 sub-geometries.

    2. `simplify()` leaves degenerate slivers behind. A district's collection
       came back holding 382 LineStrings and 121 Points alongside its real
       Polygons. Those are not areal boundaries and must not be drawn as if
       they were — a choropleth of a line is meaningless.

    So this keeps only what is genuinely areal, converts the LinearRing case
    (a closed ring IS a polygon's outer ring), and drops the rest.
    """
    if not isinstance(geometry, dict):
        return []

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if geometry_type == "Polygon":
        return [coordinates] if coordinates else []

    if geometry_type == "MultiPolygon":
        return [poly for poly in (coordinates or []) if poly]

    if geometry_type == "LinearRing":
        # A ring is a flat list of positions; as a polygon it is the outer ring.
        # Fewer than 4 positions cannot close, so it is not an area.
        ring = coordinates or []
        return [[ring]] if len(ring) >= 4 else []

    if geometry_type == "GeometryCollection":
        collected: list = []
        for sub in geometry.get("geometries", []) or []:
            collected.extend(_extract_polygons(sub))
        return collected

    # Point, LineString, MultiPoint, MultiLineString: not areal. Dropped.
    return []


def to_renderable_geometry(geometry: Optional[dict]) -> Optional[dict]:
    """Normalise any Earth Engine geometry to a Polygon or MultiPolygon.

    Returns None when nothing areal survives, so the caller can drop the
    feature rather than hand the map something it cannot draw.
    """
    polygons = _extract_polygons(geometry)
    if not polygons:
        return None
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def sanitise_features(features: list) -> tuple[list, dict]:
    """Make every feature renderable, reporting what had to be changed.

    The counts are logged rather than discarded: silently dropping map features
    is exactly the kind of thing that should be visible in a run log.
    """
    clean: list = []
    stats = {"kept": 0, "normalised": 0, "dropped": 0}

    for feature in features:
        original = feature.get("geometry")
        normalised = to_renderable_geometry(original)
        if normalised is None:
            stats["dropped"] += 1
            continue
        if not isinstance(original, dict) or original.get("type") != normalised["type"]:
            stats["normalised"] += 1
        feature["geometry"] = normalised
        # Leaflet requires this on every member of a FeatureCollection.
        feature["type"] = "Feature"
        clean.append(feature)
        stats["kept"] += 1

    return clean, stats


def _export_from_geojson_file(
    settings: IngestionSettings, path: Path
) -> dict[str, Any]:
    """Serve boundaries straight from the configured GeoJSON file.

    When a local boundary file is configured, Earth Engine is not involved at
    all: the file already holds the geometry, so contacting GEE to read back
    what we supplied would be pure latency. This is also the path that keeps
    the map working on a deployment with no Earth Engine credentials.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    features = raw.get("features", []) if isinstance(raw, dict) else []

    out: list[dict[str, Any]] = []
    for index, feature in enumerate(features):
        props = dict(feature.get("properties") or {})
        region_id = str(
            props.get(settings.GEE_ROI_ID_PROPERTY)
            or props.get("region_id")
            or index
        ).strip()
        province = props.get(settings.GEE_ROI_PROVINCE_PROPERTY)
        district = props.get(settings.GEE_ROI_DISTRICT_PROPERTY)
        tehsil = (
            props.get(settings.GEE_ROI_TEHSIL_PROPERTY)
            if settings.GEE_ROI_TEHSIL_PROPERTY
            else None
        )
        out.append(
            {
                "type": "Feature",
                "geometry": feature.get("geometry"),
                "properties": {
                    "region_id": region_id,
                    "region_type": settings.GEE_ROI_REGION_TYPE,
                    "province": province,
                    "district": district,
                    "tehsil": tehsil,
                    "name": tehsil or district or region_id,
                },
            }
        )

    # A supplied file should already be valid, but the same guarantee should
    # hold whichever source the boundaries came from.
    out, stats = sanitise_features(out)
    if stats["dropped"] or stats["normalised"]:
        logger.info(
            "region geometry from %s: %d normalised, %d dropped",
            path.name,
            stats["normalised"],
            stats["dropped"],
        )

    return {
        "type": "FeatureCollection",
        "features": out,
        "count": len(out),
        "source": str(path),
        "region_type": settings.GEE_ROI_REGION_TYPE,
        "simplify_metres": 0.0,
        "attribution": f"Boundaries: {path.name}",
    }


def _export_geometry(
    settings: IngestionSettings, simplify_metres: float
) -> dict[str, Any]:
    """Blocking Earth Engine export. Called via a worker thread.

    The whole simplification happens server-side; only the reduced GeoJSON
    crosses the wire, consistent with the rest of the pipeline never pulling a
    raster or a full-resolution vector out of Earth Engine.

    One getInfo, not two. The earlier version called ROIResolver.resolve()
    first — which itself performs a getInfo purely to read region metadata —
    and then exported the geometry. But the exported features already carry
    that metadata, so the first round trip was redundant and roughly doubled
    the cold-start time of the slowest endpoint on the dashboard.
    """
    client = EarthEngineClient(settings)
    client.initialise()
    ee = client.ee
    s = settings

    collection = ee.FeatureCollection(s.GEE_ROI_ASSET_ID)
    # Same filter chain the ingestion ROI uses, so the features the map draws
    # are exactly the features observations were reduced over.
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

    id_prop = s.GEE_ROI_ID_PROPERTY
    province_prop = s.GEE_ROI_PROVINCE_PROPERTY
    district_prop = s.GEE_ROI_DISTRICT_PROPERTY
    tehsil_prop = s.GEE_ROI_TEHSIL_PROPERTY

    def _simplify(feature):
        # Keep only what the map needs. Dropping the source properties here is
        # what keeps the payload proportional to geometry rather than to GAUL's
        # full attribute table.
        simplified = feature.geometry().simplify(maxError=simplify_metres)
        props = {
            # ee.Algorithms.String coerces generically: GAUL's ADM2_CODE is an
            # integer, a supplied asset's id may be a string.
            "region_id": ee.Algorithms.String(feature.get(id_prop)),
            "province": feature.get(province_prop),
            "district": feature.get(district_prop),
        }
        if tehsil_prop:
            props["tehsil"] = feature.get(tehsil_prop)
        return ee.Feature(simplified).set(props)

    simplified_fc = collection.map(_simplify)

    geojson = client.with_retry(
        lambda: simplified_fc.getInfo(),
        description="export simplified region geometry",
    )

    raw_features = geojson.get("features", []) if isinstance(geojson, dict) else []
    for feature in raw_features:
        props = feature.setdefault("properties", {})
        region_id = str(props.get("region_id", "")).strip()
        props["region_id"] = region_id
        props["region_type"] = s.GEE_ROI_REGION_TYPE
        props.setdefault("tehsil", None)
        props["name"] = props.get("tehsil") or props.get("district") or region_id

    # Earth Engine geometry is not strict GeoJSON; normalise before anything
    # stores or renders it, so the bad shapes never reach the browser.
    features, stats = sanitise_features(raw_features)
    logger.info(
        "region geometry: %d kept, %d normalised, %d dropped (no areal geometry)",
        stats["kept"],
        stats["normalised"],
        stats["dropped"],
    )

    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "source": f"{s.GEE_ROI_ASSET_ID} [{','.join(provinces) or 'all'}]",
        "region_type": s.GEE_ROI_REGION_TYPE,
        "simplify_metres": simplify_metres,
        # Recorded so the map's attribution and the catalogue can state which
        # boundary vintage is on screen — GAUL 2015 predates the 2018 FATA/KP
        # merger and uses the older province names.
        "attribution": (
            f"Boundaries: {settings.GEE_ROI_ASSET_ID} via Google Earth Engine"
        ),
    }



def build_cache_key(settings: IngestionSettings, simplify_metres: float) -> str:
    """Identity of a stored export.

    Includes every input that changes the output, so widening the ROI or
    changing the tolerance produces a new row instead of serving boundaries
    that no longer match the observations.
    """
    parts = [
        settings.GEE_ROI_GEOJSON_PATH or settings.GEE_ROI_ASSET_ID,
        settings.GEE_ROI_COUNTRY or "",
        ",".join(settings.roi_provinces),
        settings.GEE_ROI_REGION_TYPE,
        str(settings.GEE_ROI_LIMIT or ""),
        f"{simplify_metres:.0f}",
    ]
    return "|".join(parts)[:128]


async def load_from_database(
    db: AsyncSession, cache_key: str
) -> Optional[dict[str, Any]]:
    """Return a previously exported boundary set, if one is stored."""
    row = await db.scalar(
        select(RegionGeometryCache).where(RegionGeometryCache.cache_key == cache_key)
    )
    if row is None:
        return None
    payload = dict(row.geojson)
    payload["count"] = row.feature_count
    payload["source"] = row.source
    payload["region_type"] = row.region_type
    payload["simplify_metres"] = row.simplify_metres
    payload["attribution"] = row.attribution
    payload["generated_at"] = row.generated_at.isoformat()
    payload["served_from"] = "database"
    return payload


async def save_to_database(
    db: AsyncSession, cache_key: str, payload: dict[str, Any]
) -> None:
    """Persist an export so no other instance has to repeat it."""
    values = {
        "cache_key": cache_key,
        "source": str(payload.get("source", ""))[:255],
        "region_type": str(payload.get("region_type", ""))[:32],
        "simplify_metres": float(payload.get("simplify_metres") or 0.0),
        "feature_count": int(payload.get("count") or 0),
        "attribution": (payload.get("attribution") or None),
        "geojson": {
            "type": "FeatureCollection",
            "features": payload.get("features", []),
        },
        "generated_at": utcnow(),
    }
    stmt = pg_insert(RegionGeometryCache).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cache_key"],
        set_={k: v for k, v in values.items() if k != "cache_key"},
    )
    await db.execute(stmt)
    await db.commit()


async def region_geometry(
    simplify_metres: float = DEFAULT_SIMPLIFY_METRES,
    settings: Optional[IngestionSettings] = None,
    db: Optional[AsyncSession] = None,
) -> dict[str, Any]:
    """Simplified boundaries as GeoJSON, cached for a day.

    Boundaries change on the order of years, and the export is the one
    genuinely slow operation behind the dashboard, so it is cached hard and
    deliberately *not* invalidated by an ingestion run.
    """
    settings = settings or ingestion_settings
    simplify_metres = max(
        MIN_SIMPLIFY_METRES, min(MAX_SIMPLIFY_METRES, float(simplify_metres))
    )
    key = f"{GEOMETRY_CACHE_KEY}:{simplify_metres:.0f}"
    db_key = build_cache_key(settings, simplify_metres)

    async def _load() -> dict[str, Any]:
        # Three tiers, cheapest first: process memory (handled by the caller's
        # get_or_set), then the database, then Earth Engine. The database tier
        # is what makes this survivable on serverless — a cold instance reads
        # one row instead of spending ~15s re-exporting boundaries that have
        # not changed.
        if db is not None:
            try:
                stored = await load_from_database(db, db_key)
                if stored is not None:
                    logger.info("region geometry served from database (%s)", db_key)
                    return stored
            except Exception:  # noqa: BLE001 - a cache miss must never be fatal
                logger.exception("could not read stored region geometry; re-exporting")

        try:
            # A configured local boundary file needs no Earth Engine at all.
            if settings.GEE_ROI_GEOJSON_PATH:
                path = Path(settings.GEE_ROI_GEOJSON_PATH)
                if not path.exists():
                    raise GeometryUnavailable(
                        f"GEE_ROI_GEOJSON_PATH points at {path}, which does not exist"
                    )
                from_file = await asyncio.to_thread(
                    _export_from_geojson_file, settings, path
                )
                if db is not None:
                    try:
                        await save_to_database(db, db_key, from_file)
                    except Exception:  # noqa: BLE001
                        logger.exception("could not persist region geometry")
                return from_file
            # Earth Engine's client is synchronous and this call takes seconds;
            # a worker thread keeps it off the event loop so concurrent
            # requests are not blocked behind it.
            exported = await asyncio.to_thread(
                _export_geometry, settings, simplify_metres
            )
            if db is not None:
                try:
                    await save_to_database(db, db_key, exported)
                    logger.info("region geometry persisted (%s)", db_key)
                except Exception:  # noqa: BLE001 - failing to cache is not fatal
                    logger.exception("could not persist region geometry")
            return exported
        except GeometryUnavailable:
            raise
        except GEEAuthError as exc:
            raise GeometryUnavailable(
                "Earth Engine credentials are not configured on the API, so "
                f"region boundaries cannot be exported: {exc}"
            ) from None
        except ROIConfigurationError as exc:
            raise GeometryUnavailable(f"Region of interest is misconfigured: {exc}") from None
        except Exception as exc:  # noqa: BLE001 - surface a usable message
            logger.exception("region geometry export failed")
            raise GeometryUnavailable(
                f"Region boundaries could not be exported: {type(exc).__name__}"
            ) from None

    return await cache.get_or_set(key, _load, ttl=GEOMETRY_TTL)
