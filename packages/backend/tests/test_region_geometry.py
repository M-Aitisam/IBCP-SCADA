# packages/backend/tests/test_region_geometry.py
"""Earth Engine geometry normalisation.

These exist because of a real failure: the map crashed with Leaflet's
"Invalid GeoJSON object" on a single `LinearRing` buried inside a
GeometryCollection, among 678 sub-geometries across 119 districts. One
unrecognised type takes down the whole dashboard, so the shapes below are the
ones actually observed in the export.
"""
from __future__ import annotations

import pytest

from app.services.region_geometry import (
    sanitise_features,
    to_renderable_geometry,
)

# What Leaflet's geometryToLayer will accept. Anything else makes it throw.
LEAFLET_RENDERABLE = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
}

SQUARE = [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]]
OTHER_SQUARE = [[[2.0, 2.0], [2.0, 3.0], [3.0, 3.0], [3.0, 2.0], [2.0, 2.0]]]
RING = [[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]


def feature(geometry, region_id="R1"):
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {"region_id": region_id},
    }


# --- the types that pass straight through --------------------------------


def test_polygon_is_unchanged():
    result = to_renderable_geometry({"type": "Polygon", "coordinates": SQUARE})
    assert result == {"type": "Polygon", "coordinates": SQUARE}


def test_multipolygon_is_unchanged():
    geometry = {"type": "MultiPolygon", "coordinates": [SQUARE, OTHER_SQUARE]}
    assert to_renderable_geometry(geometry) == geometry


# --- the Earth Engine quirks that broke the map --------------------------


def test_linear_ring_becomes_a_polygon():
    """`LinearRing` is an Earth Engine type; GeoJSON has no such thing.

    A closed ring is exactly a polygon's outer ring, so the conversion loses
    nothing — and without it Leaflet throws on the whole FeatureCollection.
    """
    result = to_renderable_geometry({"type": "LinearRing", "coordinates": RING})
    assert result == {"type": "Polygon", "coordinates": [RING]}
    assert result["type"] in LEAFLET_RENDERABLE


def test_a_ring_too_short_to_close_is_not_an_area():
    # Three positions cannot enclose anything once closed.
    assert to_renderable_geometry({"type": "LinearRing", "coordinates": RING[:3]}) is None


def test_geometry_collection_is_flattened_to_its_polygons():
    geometry = {
        "type": "GeometryCollection",
        "geometries": [
            {"type": "Polygon", "coordinates": SQUARE},
            {"type": "Polygon", "coordinates": OTHER_SQUARE},
        ],
    }
    assert to_renderable_geometry(geometry) == {
        "type": "MultiPolygon",
        "coordinates": [SQUARE, OTHER_SQUARE],
    }


def test_degenerate_slivers_are_dropped_not_drawn():
    """simplify() leaves Points and LineStrings behind.

    The real export carried 382 LineStrings and 121 Points alongside genuine
    polygons. They are not areal boundaries; drawing them in a choropleth would
    be meaningless.
    """
    geometry = {
        "type": "GeometryCollection",
        "geometries": [
            {"type": "Point", "coordinates": [0.0, 0.0]},
            {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]},
            {"type": "Polygon", "coordinates": SQUARE},
        ],
    }
    # Only the polygon survives, and one polygon stays a Polygon.
    assert to_renderable_geometry(geometry) == {"type": "Polygon", "coordinates": SQUARE}


def test_the_exact_failing_shape_from_production():
    """A GeometryCollection mixing slivers with a LinearRing — the crash case."""
    geometry = {
        "type": "GeometryCollection",
        "geometries": [
            {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]},
            {"type": "LinearRing", "coordinates": RING},
            {"type": "Point", "coordinates": [5.0, 5.0]},
            {"type": "Polygon", "coordinates": OTHER_SQUARE},
        ],
    }
    result = to_renderable_geometry(geometry)
    assert result is not None
    assert result["type"] == "MultiPolygon"
    # The ring was rescued as a polygon rather than discarded.
    assert [RING] in result["coordinates"]
    assert OTHER_SQUARE in result["coordinates"]


def test_nested_geometry_collections_are_flattened():
    geometry = {
        "type": "GeometryCollection",
        "geometries": [
            {
                "type": "GeometryCollection",
                "geometries": [{"type": "Polygon", "coordinates": SQUARE}],
            }
        ],
    }
    assert to_renderable_geometry(geometry) == {"type": "Polygon", "coordinates": SQUARE}


# --- nothing areal at all -------------------------------------------------


@pytest.mark.parametrize(
    "geometry",
    [
        None,
        {},
        {"type": "Point", "coordinates": [0.0, 0.0]},
        {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]},
        {"type": "GeometryCollection", "geometries": []},
        {"type": "Polygon", "coordinates": []},
        "not a dict",
    ],
)
def test_non_areal_input_yields_nothing(geometry):
    assert to_renderable_geometry(geometry) is None


# --- feature-level behaviour ---------------------------------------------


def test_sanitise_keeps_every_district_and_reports_what_changed():
    features = [
        feature({"type": "Polygon", "coordinates": SQUARE}, "keep-1"),
        feature(
            {
                "type": "GeometryCollection",
                "geometries": [
                    {"type": "LinearRing", "coordinates": RING},
                    {"type": "Point", "coordinates": [1.0, 1.0]},
                ],
            },
            "fix-1",
        ),
    ]
    clean, stats = sanitise_features(features)

    assert stats == {"kept": 2, "normalised": 1, "dropped": 0}
    assert [f["properties"]["region_id"] for f in clean] == ["keep-1", "fix-1"]
    assert all(f["geometry"]["type"] in LEAFLET_RENDERABLE for f in clean)


def test_features_with_no_areal_geometry_are_dropped_and_counted():
    features = [
        feature({"type": "Polygon", "coordinates": SQUARE}, "keep"),
        feature({"type": "Point", "coordinates": [0.0, 0.0]}, "drop"),
        feature(None, "drop-null"),
    ]
    clean, stats = sanitise_features(features)

    assert stats["kept"] == 1
    assert stats["dropped"] == 2
    assert [f["properties"]["region_id"] for f in clean] == ["keep"]


def test_every_feature_is_stamped_as_a_Feature():
    # Leaflet requires this on each member of a FeatureCollection.
    features = [{"geometry": {"type": "Polygon", "coordinates": SQUARE}, "properties": {}}]
    clean, _ = sanitise_features(features)
    assert clean[0]["type"] == "Feature"


def test_output_can_never_contain_a_type_leaflet_rejects():
    """The guarantee this module exists to provide."""
    features = [
        feature({"type": t, "coordinates": c}, f"r{i}")
        for i, (t, c) in enumerate(
            [
                ("Polygon", SQUARE),
                ("LinearRing", RING),
                ("MultiPolygon", [SQUARE, OTHER_SQUARE]),
                ("LineString", [[0.0, 0.0], [1.0, 1.0]]),
                ("Point", [0.0, 0.0]),
            ]
        )
    ]
    clean, _ = sanitise_features(features)
    assert clean, "some features must survive"
    for f in clean:
        assert f["geometry"]["type"] in LEAFLET_RENDERABLE
