# packages/backend/tests/test_registry.py
"""Dataset registry loads, and structurally invalid configuration is rejected."""
from __future__ import annotations

import pytest

from app.ingestion.registry import (
    DATASETS,
    FUTURE_DATASETS,
    BandSpec,
    Cadence,
    DatasetConfig,
    DerivedSpec,
    Reducer,
    RegistryError,
    ValueRange,
    enabled_datasets,
    get_dataset,
    kelvin_to_celsius,
    validate_dataset,
    validate_registry,
)

REQUIRED = {
    "sentinel2": "COPERNICUS/S2_SR_HARMONIZED",
    "mod13q1": "MODIS/061/MOD13Q1",
    "chirps": "UCSB-CHG/CHIRPS/DAILY",
    "mod11a2": "MODIS/061/MOD11A2",
    "sentinel1": "COPERNICUS/S1_GRD",
}


def test_registry_is_valid():
    validate_registry()


@pytest.mark.parametrize("name,asset_id", REQUIRED.items())
def test_required_datasets_present_and_enabled(name, asset_id):
    config = DATASETS[name]
    assert config.asset_id == asset_id
    assert config.enabled


def test_all_five_datasets_enabled_by_default():
    assert {d.name for d in enabled_datasets()} == set(REQUIRED)


def test_future_datasets_registered_but_disabled():
    assert {"dynamicworld", "copernicus_dem", "global_surface_water", "mod16a2"} <= set(
        FUTURE_DATASETS
    )
    assert all(not c.enabled for c in FUTURE_DATASETS.values())
    # Reachable by name, so enabling one is a config change not a code change.
    assert get_dataset("mod16a2").asset_id == "MODIS/061/MOD16A2"


def test_unknown_dataset_raises():
    with pytest.raises(RegistryError, match="unknown dataset"):
        get_dataset("landsat99")


# --- scale factors and units ------------------------------------------------


def test_modis_ndvi_scale_factor_applied():
    """The classic bug: storing the raw int16 as if it were NDVI."""
    spec = DATASETS["mod13q1"].band_for_metric("ndvi")
    assert spec.scale_factor == pytest.approx(1e-4)
    # Raw 6500 is NDVI 0.65, not 6500.
    assert spec.to_physical(6500) == pytest.approx(0.65)
    assert spec.valid_range.contains(spec.to_physical(6500))
    assert not spec.valid_range.contains(6500)


def test_mod11a2_lst_converts_kelvin_to_celsius():
    spec = DATASETS["mod11a2"].band_for_metric("lst_day_c")
    assert spec.unit == "celsius"
    # 15000 * 0.02 = 300 K = 26.85 C
    assert spec.to_physical(15000) == pytest.approx(26.85, abs=1e-9)
    assert kelvin_to_celsius(273.15) == pytest.approx(0.0)


def test_chirps_stores_millimetres_unscaled():
    spec = DATASETS["chirps"].band_for_metric("rainfall_mm")
    assert spec.unit == "mm"
    assert spec.scale_factor == 1.0
    assert spec.to_physical(3.2) == pytest.approx(3.2)


def test_sentinel1_bands_are_configurable_vv_vh_in_db():
    config = DATASETS["sentinel1"]
    assert [b.band for b in config.bands] == ["VV", "VH"]
    assert all(b.unit == "dB" for b in config.bands)
    # SAR must never be cloud-filtered.
    assert config.cloud_property is None
    assert config.cloud_threshold is None
    assert "orbitProperties_pass" in config.metadata_properties
    assert "instrumentMode" in config.metadata_properties


def test_sentinel2_has_cloud_filter_and_derived_ndvi():
    config = DATASETS["sentinel2"]
    assert config.cloud_property == "CLOUDY_PIXEL_PERCENTAGE"
    assert config.cloud_threshold == 20.0
    assert config.mask_strategy == "s2_scl"
    ndvi = config.derived_for_metric("ndvi")
    assert ndvi is not None and ndvi.bands == ("B8", "B4")
    # Raw reflectance is kept alongside the derived index.
    assert {b.metric for b in config.bands} == {"reflectance_red", "reflectance_nir"}


def test_cadences_match_products():
    assert DATASETS["chirps"].cadence is Cadence.DAILY
    assert DATASETS["mod13q1"].cadence is Cadence.COMPOSITE_16_DAY
    assert DATASETS["mod11a2"].cadence is Cadence.COMPOSITE_8_DAY
    assert DATASETS["sentinel1"].cadence is Cadence.SCENE
    assert DATASETS["mod13q1"].cadence.nominal_days == 16


# --- rejection of invalid configuration -------------------------------------


def _config(**overrides) -> DatasetConfig:
    defaults = dict(
        name="x",
        asset_id="A/B",
        metric_group="test",
        cadence=Cadence.DAILY,
        bands=(BandSpec("B1", "m1", "unit"),),
        spatial_resolution=10.0,
    )
    defaults.update(overrides)
    return DatasetConfig(**defaults)


def test_rejects_missing_asset_id():
    with pytest.raises(RegistryError, match="asset_id"):
        validate_dataset(_config(asset_id="not-a-path"))


def test_rejects_no_bands():
    with pytest.raises(RegistryError, match="at least one band"):
        validate_dataset(_config(bands=()))


def test_rejects_duplicate_metric_names():
    """metric is part of the idempotency key, so duplicates would collide."""
    with pytest.raises(RegistryError, match="duplicate metric"):
        validate_dataset(
            _config(bands=(BandSpec("B1", "same", "u"), BandSpec("B2", "same", "u")))
        )


def test_rejects_derived_from_unconfigured_band():
    with pytest.raises(RegistryError, match="unconfigured bands"):
        validate_dataset(
            _config(
                derived=(
                    DerivedSpec("ndvi", "index", "normalizedDifference", ("B8", "B4")),
                )
            )
        )


def test_rejects_zero_scale_factor():
    with pytest.raises(RegistryError, match="scale_factor"):
        validate_dataset(_config(bands=(BandSpec("B1", "m", "u", scale_factor=0),)))


def test_rejects_cloud_threshold_without_property():
    with pytest.raises(RegistryError, match="cloud_threshold set without"):
        validate_dataset(_config(cloud_threshold=20.0))


def test_rejects_out_of_range_cloud_threshold():
    with pytest.raises(RegistryError, match="percentage"):
        validate_dataset(_config(cloud_property="C", cloud_threshold=180.0))


def test_rejects_nonpositive_chunk_days():
    with pytest.raises(RegistryError, match="chunk_days"):
        validate_dataset(_config(chunk_days=0))


def test_rejects_key_name_mismatch():
    with pytest.raises(RegistryError, match="does not match name"):
        validate_registry({"wrong": _config(name="x")})


def test_value_range_bounds():
    r = ValueRange(-1.0, 1.0)
    assert r.contains(-1.0) and r.contains(1.0) and r.contains(0.0)
    assert not r.contains(1.0001)


def test_reducer_configurable_per_band_not_global():
    """Reducers come from config, not a hardcoded global choice."""
    reducers = {
        b.metric: b.reducer for d in DATASETS.values() for b in d.bands
    }
    assert reducers["detailed_qa"] is Reducer.MEDIAN
    assert reducers["rainfall_mm"] is Reducer.MEAN
    assert reducers["backscatter_vv"] is Reducer.MEAN
