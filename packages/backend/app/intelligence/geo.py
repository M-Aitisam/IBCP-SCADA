# packages/backend/app/intelligence/geo.py
"""Deterministic geometry maths over stored region boundaries.

Pure functions, no database and no Earth Engine. Two things downstream need
them and neither should be asking GEE per request:

  area_km2   flood extent in square kilometres = water fraction × region area
  adjacency  hotspot clustering needs to know which districts touch

Postgres here has no PostGIS, so this is computed in Python from the GeoJSON
already stored in `gv_region_geometry` and cached in `gv_region_geometry_stats`.
Region boundaries change on the order of years; recomputing them per request
would be wasteful for numbers that essentially never move.

Every approximation below is documented where it is made. An approximate area
that is honest about being approximate is useful; a precise-looking number
whose derivation nobody can check is not.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# Mean Earth radius (IUGG). Using a sphere rather than the WGS84 ellipsoid
# costs at most ~0.3% in area at these latitudes — far below the error already
# introduced by simplifying the boundaries to ~1 km vertex tolerance, so a
# fully ellipsoidal computation would be false precision.
EARTH_RADIUS_KM = 6371.0088

# Degrees of tolerance when testing whether two districts touch. GAUL
# boundaries are simplified before storage, which pulls shared borders slightly
# apart; without a small buffer genuinely adjacent districts would test as
# separate. ~0.05° is roughly 5 km at Pakistan's latitudes.
ADJACENCY_BUFFER_DEG = 0.05


@dataclass
class RegionGeometryFacts:
    region_id: str
    area_km2: Optional[float] = None
    centroid_lat: Optional[float] = None
    centroid_lon: Optional[float] = None
    bbox: Optional[tuple[float, float, float, float]] = None  # min_lat, min_lon, max_lat, max_lon
    province: Optional[str] = None
    district: Optional[str] = None
    region_type: Optional[str] = None
    neighbours: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        bbox = self.bbox or (None, None, None, None)
        return {
            "region_id": self.region_id,
            "region_type": self.region_type,
            "province": self.province,
            "district": self.district,
            "area_km2": self.area_km2,
            "centroid_lat": self.centroid_lat,
            "centroid_lon": self.centroid_lon,
            "bbox_min_lat": bbox[0],
            "bbox_min_lon": bbox[1],
            "bbox_max_lat": bbox[2],
            "bbox_max_lon": bbox[3],
            "neighbours": {"items": self.neighbours},
        }


def _rings(geometry: Optional[dict]) -> list[list[list[float]]]:
    """Every outer ring in a Polygon or MultiPolygon.

    Interior rings (holes) are deliberately ignored: at district scale on
    simplified boundaries they are rare, and treating a hole as solid
    overstates area by far less than the simplification already does.
    """
    if not isinstance(geometry, dict):
        return []
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or []

    if kind == "Polygon":
        return [coordinates[0]] if coordinates else []
    if kind == "MultiPolygon":
        return [poly[0] for poly in coordinates if poly]
    return []


def ring_area_km2(ring: list[list[float]]) -> float:
    """Spherical area of one closed ring of [lon, lat] positions.

    Uses the standard spherical-excess summation. Sign depends on winding
    order, so the absolute value is taken — a boundary's area does not depend
    on which direction its vertices were listed in.
    """
    if len(ring) < 4:
        return 0.0

    total = 0.0
    for i in range(len(ring)):
        lon1, lat1 = ring[i][0], ring[i][1]
        lon2, lat2 = ring[(i + 1) % len(ring)][0], ring[(i + 1) % len(ring)][1]
        total += math.radians(lon2 - lon1) * (
            2.0 + math.sin(math.radians(lat1)) + math.sin(math.radians(lat2))
        )
    return abs(total * EARTH_RADIUS_KM * EARTH_RADIUS_KM / 2.0)


def polygon_area_km2(geometry: Optional[dict]) -> Optional[float]:
    """Total area of a Polygon or MultiPolygon, or None if not areal."""
    rings = _rings(geometry)
    if not rings:
        return None
    area = sum(ring_area_km2(ring) for ring in rings)
    return round(area, 3) if area > 0 else None


def bounding_box(
    geometry: Optional[dict],
) -> Optional[tuple[float, float, float, float]]:
    """(min_lat, min_lon, max_lat, max_lon) across every ring."""
    rings = _rings(geometry)
    if not rings:
        return None

    lats: list[float] = []
    lons: list[float] = []
    for ring in rings:
        for position in ring:
            if len(position) >= 2:
                lons.append(position[0])
                lats.append(position[1])
    if not lats or not lons:
        return None
    return (min(lats), min(lons), max(lats), max(lons))


def centroid(geometry: Optional[dict]) -> Optional[tuple[float, float]]:
    """Area-weighted centroid as (lat, lon).

    Planar shoelace on the largest ring. At district scale the distortion from
    treating degrees as planar is small, and this centroid is used for map
    labelling and distance heuristics — not for anything that needs geodetic
    rigour.
    """
    rings = _rings(geometry)
    if not rings:
        return None

    ring = max(rings, key=ring_area_km2)
    if len(ring) < 4:
        return None

    twice_area = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(len(ring) - 1):
        x0, y0 = ring[i][0], ring[i][1]
        x1, y1 = ring[i + 1][0], ring[i + 1][1]
        cross = x0 * y1 - x1 * y0
        twice_area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross

    if abs(twice_area) < 1e-12:
        # Degenerate ring: fall back to the mean vertex, which is always
        # defined and is good enough for a label position.
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        return (round(sum(lats) / len(lats), 6), round(sum(lons) / len(lons), 6))

    factor = 1.0 / (3.0 * twice_area)
    return (round(cy * factor, 6), round(cx * factor, 6))


def boxes_touch(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    buffer_deg: float = ADJACENCY_BUFFER_DEG,
) -> bool:
    """Whether two bounding boxes overlap within a tolerance.

    A deliberate approximation of adjacency. Two districts whose bounding boxes
    overlap are usually neighbours; occasionally two L-shaped districts will
    test as adjacent without sharing a border.

    That error direction is the safe one for hotspot detection: it can merge
    two nearby clusters, which understates the number of hotspots. The opposite
    error — missing a genuine neighbour — would fragment one real regional
    emergency into several small ones and hide it.
    """
    a_min_lat, a_min_lon, a_max_lat, a_max_lon = a
    b_min_lat, b_min_lon, b_max_lat, b_max_lon = b

    lat_overlap = (a_min_lat - buffer_deg) <= b_max_lat and (b_min_lat - buffer_deg) <= a_max_lat
    lon_overlap = (a_min_lon - buffer_deg) <= b_max_lon and (b_min_lon - buffer_deg) <= a_max_lon
    return lat_overlap and lon_overlap


def build_adjacency(
    facts: dict[str, RegionGeometryFacts], buffer_deg: float = ADJACENCY_BUFFER_DEG
) -> dict[str, list[str]]:
    """Neighbour lists for every region with a bounding box.

    O(n²) over ~119 districts is ~7,000 comparisons — microseconds, and run
    once when boundaries change. A spatial index would be premature here.
    """
    ids = [rid for rid, f in facts.items() if f.bbox is not None]
    adjacency: dict[str, list[str]] = {rid: [] for rid in facts}

    for i, left in enumerate(ids):
        for right in ids[i + 1 :]:
            if boxes_touch(facts[left].bbox, facts[right].bbox, buffer_deg):
                adjacency[left].append(right)
                adjacency[right].append(left)

    return {rid: sorted(neighbours) for rid, neighbours in adjacency.items()}


def facts_from_geojson(geojson: dict) -> dict[str, RegionGeometryFacts]:
    """Derive area, centroid, bbox and adjacency for a FeatureCollection."""
    facts: dict[str, RegionGeometryFacts] = {}

    for feature in (geojson or {}).get("features", []):
        properties = feature.get("properties") or {}
        region_id = str(properties.get("region_id", "")).strip()
        if not region_id:
            continue

        geometry = feature.get("geometry")
        centre = centroid(geometry)
        facts[region_id] = RegionGeometryFacts(
            region_id=region_id,
            area_km2=polygon_area_km2(geometry),
            centroid_lat=centre[0] if centre else None,
            centroid_lon=centre[1] if centre else None,
            bbox=bounding_box(geometry),
            province=properties.get("province"),
            district=properties.get("district"),
            region_type=properties.get("region_type"),
        )

    adjacency = build_adjacency(facts)
    for region_id, neighbours in adjacency.items():
        if region_id in facts:
            facts[region_id].neighbours = neighbours

    return facts


def connected_components(
    members: Iterable[str], adjacency: dict[str, list[str]]
) -> list[list[str]]:
    """Group members into spatially contiguous clusters.

    Plain breadth-first search over the adjacency graph, restricted to the
    supplied members. This is what turns "these 12 districts are stressed" into
    "there are 3 stress hotspots, the largest covering 7 districts" — which is
    the operationally meaningful statement.
    """
    remaining = set(members)
    clusters: list[list[str]] = []

    while remaining:
        seed = remaining.pop()
        component = [seed]
        queue = [seed]

        while queue:
            current = queue.pop()
            for neighbour in adjacency.get(current, []):
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    component.append(neighbour)
                    queue.append(neighbour)

        clusters.append(sorted(component))

    # Largest first: the biggest contiguous cluster is the headline.
    clusters.sort(key=len, reverse=True)
    return clusters
