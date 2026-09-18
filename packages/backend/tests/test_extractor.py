# packages/backend/tests/test_extractor.py
"""Normalisation of reduceRegions output into ObservationRecords.

Exercises the part that turns raw GEE feature properties into scaled,
unit-correct, provenance-carrying records — without touching Earth Engine.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from app.ingestion.extractor import (
    Extractor,
    _epoch_ms_to_datetime,
    _collect_stats,
)
from app.ingestion.registry import DATASETS, Reducer
from conftest import FakeClient, feature, make_roi, ms


def extractor(regions: int = 2) -> Extractor:
    roi = make_roi(regions)
    return Extractor(FakeClient(), roi)


@pytest.mark.parametrize("image_count,delay", [(0, 5.0), (1, 5.0), (5, 5.0), (5, 0.0)])
def test_reduction_pacing_only_between_batches(monkeypatch, image_count, delay):
    client = MagicMock()
    events = []
    index = {"features": [
        {"properties": {"id": str(i), "t": ms(2026, 8, 1)}}
        for i in range(image_count)
    ]}
    responses = iter([index] + [{"features": []}] * ((image_count + 1) // 2))

    def run(operation, description=""):
        result = next(responses)
        events.append("index" if "image list" in description else "reduce")
        return result

    client.with_retry.side_effect = run
    ex = Extractor(client, make_roi(2))
    monkeypatch.setattr(ex, "build_collection", MagicMock())
    monkeypatch.setattr("app.ingestion.extractor.time.sleep", lambda seconds: events.append(seconds))
    config = replace(DATASETS["sentinel1"], inter_batch_delay_seconds=delay)
    ex.extract_chunk(config, date(2026, 8, 1), date(2026, 8, 8))
    expected = ["index"]
    for batch in range((image_count + 1) // 2):
        if batch and delay:
            expected.append(delay)
        expected.append("reduce")
    assert events == expected


# --- timestamps -------------------------------------------------------------


def test_epoch_millis_parsed_as_utc():
    stamp = _epoch_ms_to_datetime(ms(2026, 8, 1))
    assert stamp == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert stamp.tzinfo is timezone.utc


def test_epoch_seconds_mistake_does_not_crash():
    # A seconds value is silently a 1970 date; validation catches it later.
    assert _epoch_ms_to_datetime(1786000000).year == 1970


def test_invalid_timestamp_returns_none():
    assert _epoch_ms_to_datetime(None) is None
    assert _epoch_ms_to_datetime("not-a-number") is None


# --- stat collection --------------------------------------------------------

def test_collect_stats_reads_suffixed_reducer_outputs():
    props = {
        "NDVI_mean": 6500,
        "NDVI_min": 1000,
        "NDVI_max": 9000,
        "NDVI_count": 42,
    }
    stats = _collect_stats(props, "NDVI", Reducer.MEAN)
    assert stats["primary"] == 6500
    assert stats["min"] == 1000 and stats["max"] == 9000
    assert stats["count"] == 42


def test_collect_stats_handles_bare_band_name():
    stats = _collect_stats({"precipitation": 3.2}, "precipitation", Reducer.MEAN)
    assert stats["primary"] == 3.2


def test_collect_stats_returns_none_for_absent_band():
    assert _collect_stats({"other": 1}, "NDVI", Reducer.MEAN) is None


# --- normalisation ----------------------------------------------------------


def test_modis_ndvi_scaled_before_storage():
    ex = extractor()
    features = [
        feature("R1", ms(2026, 7, 12), "2026_07_12",
                {"NDVI_mean": 6500, "NDVI_count": 100,
                 "EVI_mean": 4200, "DetailedQA_median": 2112}),
    ]
    records, latest = ex._normalise(DATASETS["mod13q1"], features, "__region_id")
    by_metric = {r.metric: r for r in records}

    assert by_metric["ndvi"].value == pytest.approx(0.65)
    assert by_metric["ndvi"].scale_factor == pytest.approx(1e-4)
    assert by_metric["evi"].value == pytest.approx(0.42)
    assert latest == date(2026, 7, 12)


def test_lst_converted_to_celsius_with_unit_recorded():
    ex = extractor()
    features = [
        feature("R1", ms(2026, 8, 1), "2026_08_01",
                {"LST_Day_1km_mean": 15000, "QC_Day_median": 0}),
    ]
    records, _ = ex._normalise(DATASETS["mod11a2"], features, "__region_id")
    lst = next(r for r in records if r.metric == "lst_day_c")
    assert lst.value == pytest.approx(26.85)
    assert lst.unit == "celsius"
    assert lst.scale_factor == pytest.approx(0.02)


def test_min_max_are_scaled_too_not_left_raw():
    ex = extractor()
    features = [
        feature("R1", ms(2026, 7, 12), "img",
                {"NDVI_mean": 6500, "NDVI_min": 1000, "NDVI_max": 9000}),
    ]
    records, _ = ex._normalise(DATASETS["mod13q1"], features, "__region_id")
    ndvi = next(r for r in records if r.metric == "ndvi")
    assert ndvi.min_value == pytest.approx(0.10)
    assert ndvi.max_value == pytest.approx(0.90)


def test_derived_ndvi_is_labelled_and_raw_bands_are_not():
    ex = extractor()
    features = [
        feature("R1", ms(2026, 8, 1), "S2_img",
                {"B4_mean": 1200, "B8_mean": 3400, "ndvi_mean": 0.478,
                 "CLOUDY_PIXEL_PERCENTAGE": 8.5, "MGRS_TILE": "42RUR"}),
    ]
    records, _ = ex._normalise(DATASETS["sentinel2"], features, "__region_id")
    by_metric = {r.metric: r for r in records}

    # Derived product carries derived_metric; raw measurements do not.
    assert by_metric["ndvi"].derived_metric == "NDVI"
    assert by_metric["ndvi"].band is None
    assert by_metric["reflectance_red"].derived_metric is None
    assert by_metric["reflectance_red"].band == "B4"
    # Reflectance scale factor applied.
    assert by_metric["reflectance_red"].value == pytest.approx(0.12)
    assert by_metric["reflectance_nir"].value == pytest.approx(0.34)


def test_provenance_is_preserved():
    ex = extractor()
    features = [
        feature("R1", ms(2026, 8, 1), "S2A_MSIL2A_20260801",
                {"B4_mean": 1200, "B8_mean": 3400,
                 "CLOUDY_PIXEL_PERCENTAGE": 8.5, "MGRS_TILE": "42RUR"}),
    ]
    records, _ = ex._normalise(DATASETS["sentinel2"], features, "__region_id")
    record = records[0]
    assert record.source_image_id == "S2A_MSIL2A_20260801"
    assert record.cloud_percentage == pytest.approx(8.5)
    assert record.acquisition_metadata["MGRS_TILE"] == "42RUR"
    assert record.spatial_resolution == 10.0
    assert record.dataset_asset_id == "COPERNICUS/S2_SR_HARMONIZED"
    assert record.dataset_version == "harmonized"


def test_sar_metadata_captured():
    ex = extractor()
    features = [
        feature("R1", ms(2026, 8, 1), "S1A_IW_GRDH",
                {"VV_mean": -12.5, "VH_mean": -18.2,
                 "orbitProperties_pass": "DESCENDING",
                 "instrumentMode": "IW", "resolution": "H"}),
    ]
    records, _ = ex._normalise(DATASETS["sentinel1"], features, "__region_id")
    vv = next(r for r in records if r.metric == "backscatter_vv")
    assert vv.value == pytest.approx(-12.5)
    assert vv.unit == "dB"
    assert vv.acquisition_metadata["orbitProperties_pass"] == "DESCENDING"
    assert vv.acquisition_metadata["instrumentMode"] == "IW"
    # SAR carries no cloud percentage.
    assert vv.cloud_percentage is None


def test_region_metadata_attached_to_every_record():
    ex = extractor()
    features = [
        feature("R1", ms(2026, 8, 1), "img", {"precipitation": 3.2}),
        feature("R2", ms(2026, 8, 1), "img", {"precipitation": 0.0}),
    ]
    records, _ = ex._normalise(DATASETS["chirps"], features, "__region_id")
    assert {r.region_id for r in records} == {"R1", "R2"}
    assert all(r.region_type == "district" for r in records)
    assert {r.province for r in records} == {"Sindh", "Balochistan"}


def test_features_from_unknown_regions_are_dropped():
    ex = extractor()
    features = [feature("GHOST", ms(2026, 8, 1), "img", {"precipitation": 3.2})]
    records, _ = ex._normalise(DATASETS["chirps"], features, "__region_id")
    assert records == []


def test_masked_region_yields_null_not_zero():
    """An all-cloud region must not silently become a rainfall of 0."""
    ex = extractor()
    features = [feature("R1", ms(2026, 8, 1), "img", {"precipitation_mean": None})]
    records, _ = ex._normalise(DATASETS["chirps"], features, "__region_id")
    assert len(records) == 1
    assert records[0].value is None


def test_latest_observation_is_the_max_across_features():
    ex = extractor()
    features = [
        feature("R1", ms(2026, 8, 1), "a", {"precipitation": 1.0}),
        feature("R1", ms(2026, 8, 15), "b", {"precipitation": 2.0}),
        feature("R2", ms(2026, 8, 9), "c", {"precipitation": 3.0}),
    ]
    _, latest = ex._normalise(DATASETS["chirps"], features, "__region_id")
    assert latest == date(2026, 8, 15)


def test_one_record_per_region_per_metric_per_image():
    ex = extractor()
    features = [
        feature(r, ms(2026, 7, 12), "img", {"NDVI_mean": 5000, "EVI_mean": 3000})
        for r in ("R1", "R2")
    ]
    records, _ = ex._normalise(DATASETS["mod13q1"], features, "__region_id")
    # 2 regions x 2 numeric metrics (DetailedQA absent from this payload)
    assert len(records) == 4
    keys = {r.observation_key for r in records}
    assert len(keys) == 4
