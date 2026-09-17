# packages/backend/app/ingestion/registry.py
"""Central dataset registry.

Every GEE asset id, band, scale factor, reducer and quality rule lives here  - 
nowhere else in the codebase should contain a hardcoded collection id. Adding a
dataset means adding a DatasetConfig entry, not touching the ingestion engine.

Two concepts are deliberately kept apart:

  BandSpec    - a raw measurement read straight off a source band, converted
                only by its documented scale factor and unit transform.
  DerivedSpec - a product computed from bands (NDVI from B8/B4). Always stored
                with `derived_metric` set, so downstream modules can tell a
                measurement from a computation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class Cadence(str, Enum):
    """How often the source product actually publishes an observation.

    The scheduler runs daily regardless; this tells the pipeline how wide a
    date chunk should be and what a plausible gap looks like. It is never used
    to invent an observation for a day the sensor did not produce one.
    """

    DAILY = "daily"
    SCENE = "scene"  # irregular, orbit-driven (Sentinel-1/2)
    COMPOSITE_8_DAY = "8-day"
    COMPOSITE_16_DAY = "16-day"

    @property
    def nominal_days(self) -> int:
        return {
            Cadence.DAILY: 1,
            Cadence.SCENE: 3,
            Cadence.COMPOSITE_8_DAY: 8,
            Cadence.COMPOSITE_16_DAY: 16,
        }[self]


class Reducer(str, Enum):
    """Spatial reducer applied inside GEE via reduceRegions."""

    MEAN = "mean"
    MEDIAN = "median"
    SUM = "sum"


def kelvin_to_celsius(value: float) -> float:
    return value - 273.15


@dataclass(frozen=True)
class ValueRange:
    """Physically plausible bounds, checked after scaling and unit transform."""

    minimum: float
    maximum: float

    def contains(self, value: float) -> bool:
        return self.minimum <= value <= self.maximum


@dataclass(frozen=True)
class BandSpec:
    """One raw band to read, reduce and store."""

    band: str
    metric: str
    unit: str
    reducer: Reducer = Reducer.MEAN
    # Multiply the reduced integer/float by this to reach the physical value.
    # MODIS NDVI ships as int16 with a 0.0001 factor; storing the raw integer
    # as if it were NDVI is the classic bug this exists to prevent.
    scale_factor: float = 1.0
    # Applied after scale_factor (e.g. Kelvin -> Celsius).
    transform: Optional[Callable[[float], float]] = None
    valid_range: Optional[ValueRange] = None
    # Quality/QA bands are stored for provenance but skip numeric validation.
    is_quality_band: bool = False

    def to_physical(self, raw: float) -> float:
        scaled = raw * self.scale_factor
        return self.transform(scaled) if self.transform else scaled


@dataclass(frozen=True)
class DerivedSpec:
    """A product computed from bands during acquisition.

    Kept minimal on purpose: the acquisition module computes NDVI because it is
    a plain band ratio that is far cheaper server-side than shipping both
    reflectance bands, but anything index-like (drought indices, flood
    classification) belongs to a later module, not here.
    """

    metric: str
    unit: str
    expression: str  # GEE normalizedDifference band pair or expression name
    bands: tuple[str, ...]
    reducer: Reducer = Reducer.MEAN
    valid_range: Optional[ValueRange] = None
    # Applied to every input band *before* the expression is evaluated.
    #
    # This matters only for non-ratio indices. NDVI is a normalised difference,
    # so any common scale factor cancels and 1.0 is correct. EVI is not: its
    # coefficients and the "+1" in the denominator are defined against surface
    # reflectance in [0,1], so feeding it raw S2 integers (0-10000) yields a
    # number that is not EVI at all. Set this to the collection's reflectance
    # scale factor for any such index.
    band_scale: float = 1.0
    # Cut point for mask-style indices (expression="water_mask"). The reduced
    # MEAN of a 0/1 mask is the FRACTION of pixels satisfying it, which is how
    # a per-pixel classification becomes a regional areal statistic without
    # ever downloading a raster.
    threshold: Optional[float] = None


@dataclass(frozen=True)
class DatasetConfig:
    name: str
    asset_id: str
    metric_group: str
    cadence: Cadence
    bands: tuple[BandSpec, ...]
    # Native resolution in metres; also the `scale` passed to reduceRegions.
    spatial_resolution: float
    enabled: bool = True
    version: Optional[str] = None
    derived: tuple[DerivedSpec, ...] = ()
    # Scene-level cloud metadata property, when the collection exposes one.
    cloud_property: Optional[str] = None
    cloud_threshold: Optional[float] = None
    # Extra image properties to carry into acquisition_metadata.
    metadata_properties: tuple[str, ...] = ()
    # Server-side filters expressed as (property, operator, value).
    property_filters: tuple[tuple[str, str, object], ...] = ()
    # Days per GEE request during backfill. Wider for sparse products.
    chunk_days: int = 30
    # Pixel-level mask strategy, resolved in extractor.py.
    mask_strategy: Optional[str] = None
    # Passed to reduceRegions. GEE splits work into tileScale^2 server-side
    # sub-tiles, which helps a dense, high-resolution collection (Sentinel)
    # avoid "Computation timed out" over a large ROI. Left at 1 (GEE's
    # default) for sparse/coarse products that don't need it.
    tile_scale: int = 1
    # Caps images-per-reduceRegions-request below what the 5000-element math
    # alone would allow (extractor.py still takes the smaller of the two).
    # For a computationally heavy per-pixel product (e.g. SAR despeckling)
    # the element count is not the binding constraint - request *time* is -
    # so this bounds batch size directly instead of only bounding elements.
    # None means the element-based cap is the only limit.
    max_images_per_batch: Optional[int] = None

    @property
    def all_metrics(self) -> tuple[str, ...]:
        return tuple(b.metric for b in self.bands) + tuple(d.metric for d in self.derived)

    def band_for_metric(self, metric: str) -> Optional[BandSpec]:
        for spec in self.bands:
            if spec.metric == metric:
                return spec
        return None

    def derived_for_metric(self, metric: str) -> Optional[DerivedSpec]:
        for spec in self.derived:
            if spec.metric == metric:
                return spec
        return None


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

NDVI_RANGE = ValueRange(-1.0, 1.0)
EVI_RANGE = ValueRange(-1.0, 1.0)

DATASETS: dict[str, DatasetConfig] = {
    # --- Crop health -------------------------------------------------------
    "sentinel2": DatasetConfig(
        name="sentinel2",
        asset_id="COPERNICUS/S2_SR_HARMONIZED",
        version="harmonized",
        metric_group="vegetation",
        cadence=Cadence.SCENE,
        spatial_resolution=10.0,
        # Raw surface reflectance is stored alongside the derived index so a
        # later module can recompute any index without re-querying GEE.
        # S2 L2A reflectance is scaled by 1e-4.
        bands=(
            # Blue is carried for EVI, which needs it; red/nir serve NDVI and
            # let a later module recompute any index without re-querying GEE.
            BandSpec("B2", "reflectance_blue", "reflectance", Reducer.MEAN, 1e-4,
                     valid_range=ValueRange(0.0, 1.6)),
            BandSpec("B4", "reflectance_red", "reflectance", Reducer.MEAN, 1e-4,
                     valid_range=ValueRange(0.0, 1.6)),
            BandSpec("B8", "reflectance_nir", "reflectance", Reducer.MEAN, 1e-4,
                     valid_range=ValueRange(0.0, 1.6)),
        ),
        derived=(
            DerivedSpec("ndvi", "index", "normalizedDifference", ("B8", "B4"),
                        Reducer.MEAN, NDVI_RANGE),
            # EVI as defined by Huete et al. (2002), the same formulation MODIS
            # MOD13Q1 ships, so the S2 and MODIS EVI series are comparable:
            #   2.5 * (NIR - RED) / (NIR + 6*RED - 7.5*BLUE + 1)
            # band_scale converts S2 L2A integers to reflectance first; without
            # it the "+1" term would be meaningless against values near 10000.
            DerivedSpec("evi", "index", "evi", ("B8", "B4", "B2"),
                        Reducer.MEAN, EVI_RANGE, band_scale=1e-4),
        ),
        cloud_property="CLOUDY_PIXEL_PERCENTAGE",
        cloud_threshold=20.0,
        metadata_properties=("MGRS_TILE", "SENSING_ORBIT_NUMBER", "SPACECRAFT_NAME"),
        mask_strategy="s2_scl",
        chunk_days=30,
        tile_scale=4,
    ),
    "mod13q1": DatasetConfig(
        name="mod13q1",
        asset_id="MODIS/061/MOD13Q1",
        version="061",
        metric_group="vegetation",
        cadence=Cadence.COMPOSITE_16_DAY,
        spatial_resolution=250.0,
        bands=(
            BandSpec("NDVI", "ndvi", "index", Reducer.MEAN, 1e-4,
                     valid_range=NDVI_RANGE),
            BandSpec("EVI", "evi", "index", Reducer.MEAN, 1e-4,
                     valid_range=EVI_RANGE),
            # QA word is preserved verbatim; a mean over a bitfield is not
            # meaningful physically, but it is retained for provenance and is
            # excluded from numeric validation.
            BandSpec("DetailedQA", "detailed_qa", "bitfield", Reducer.MEDIAN,
                     is_quality_band=True),
        ),
        metadata_properties=(),
        chunk_days=365,
    ),
    # --- Geohazard / climate ----------------------------------------------
    "chirps": DatasetConfig(
        name="chirps",
        asset_id="UCSB-CHG/CHIRPS/DAILY",
        version="v2.0",
        metric_group="rainfall",
        cadence=Cadence.DAILY,
        spatial_resolution=5566.0,
        bands=(
            # Rainfall accumulates over area, so SUM is the wrong reducer for a
            # regional depth: CHIRPS pixels are already mm/day, and the mean
            # over a polygon is the areal-average depth in mm. Total volume is
            # a later module's concern.
            BandSpec("precipitation", "rainfall_mm", "mm", Reducer.MEAN, 1.0,
                     valid_range=ValueRange(0.0, 2000.0)),
        ),
        chunk_days=90,
    ),
    "mod11a2": DatasetConfig(
        name="mod11a2",
        asset_id="MODIS/061/MOD11A2",
        version="061",
        metric_group="temperature",
        cadence=Cadence.COMPOSITE_8_DAY,
        spatial_resolution=1000.0,
        bands=(
            # 0.02 scale gives Kelvin; converted to Celsius, with the unit
            # recorded so nothing downstream has to guess.
            BandSpec("LST_Day_1km", "lst_day_c", "celsius", Reducer.MEAN, 0.02,
                     transform=kelvin_to_celsius,
                     valid_range=ValueRange(-50.0, 70.0)),
            # Night LST shares the day band's 0.02 Kelvin scale factor. Stored
            # as its own metric rather than averaged with day: the diurnal
            # difference is the physically meaningful signal, and collapsing
            # them would destroy it.
            BandSpec("LST_Night_1km", "lst_night_c", "celsius", Reducer.MEAN, 0.02,
                     transform=kelvin_to_celsius,
                     valid_range=ValueRange(-60.0, 60.0)),
            BandSpec("QC_Day", "qc_day", "bitfield", Reducer.MEDIAN,
                     is_quality_band=True),
        ),
        chunk_days=365,
    ),
    "sentinel1": DatasetConfig(
        name="sentinel1",
        asset_id="COPERNICUS/S1_GRD",
        version="GRD",
        metric_group="sar",
        cadence=Cadence.SCENE,
        spatial_resolution=10.0,
        bands=(
            # GRD values in GEE are already calibrated to dB (sigma0).
            BandSpec("VV", "backscatter_vv", "dB", Reducer.MEAN,
                     valid_range=ValueRange(-50.0, 20.0)),
            BandSpec("VH", "backscatter_vh", "dB", Reducer.MEAN,
                     valid_range=ValueRange(-50.0, 20.0)),
        ),
        # Optical cloud filtering is meaningless for SAR and is not applied.
        cloud_property=None,
        cloud_threshold=None,
        metadata_properties=(
            "orbitProperties_pass",
            "relativeOrbitNumber_start",
            "instrumentMode",
            "resolution",
            "resolution_meters",
            "transmitterReceiverPolarisation",
            "platform_number",
        ),
        property_filters=(
            ("instrumentMode", "equals", "IW"),
        ),
        derived=(
            # Open water is a specular reflector: it bounces C-band radar away
            # from the sensor, so water pixels return very low backscatter.
            # Thresholding VV and taking the MEAN of the resulting 0/1 mask
            # gives the FRACTION of the district that looks like water — which
            # multiplied by the district's area is an extent in km².
            #
            # -15 dB is a widely used open-water cut for Sentinel-1 IW GRD in
            # VV. It is a documented, configurable parameter rather than a
            # tuned constant: radiometric terrain effects mean no single value
            # is correct everywhere, and the number that matters operationally
            # is the CHANGE against each region's own dry-season baseline, not
            # the absolute fraction.
            #
            # Known limitation, stated rather than hidden: radar shadow and
            # smooth dry surfaces (bare tarmac, some sand sheets) are also
            # dark in VV and will be counted. Differencing against the region's
            # own baseline removes the persistent part of that error, since
            # those surfaces are dark in the baseline too.
            DerivedSpec(
                "water_fraction",
                "fraction",
                "water_mask",
                ("VV",),
                Reducer.MEAN,
                ValueRange(0.0, 1.0),
                threshold=-15.0,
            ),
        ),
        chunk_days=30,
        # SAR reduce is heavier per pixel than optical, and S1 windows carry
        # more overlapping orbit passes than S2 - both bands (VV+VH) plus the
        # derived water_fraction come out of the same reduceRegions call.
        # tileScale=4 alone still timed out at "batch 1/4" in production;
        # 8 gives GEE twice the server-side sub-tiling. max_images_per_batch
        # bounds request *time*, since the element-count cap (4500/regions)
        # allows ~37 images/batch here, which is too much wall-clock work per
        # request for this dataset even though it's well under 5000 elements.
        tile_scale=8,
        max_images_per_batch=10,
    ),
}


# Datasets researched for later phases. Present so the shape of the registry is
# obvious to whoever enables them; disabled so nothing queries them today.
FUTURE_DATASETS: dict[str, DatasetConfig] = {
    "dynamicworld": DatasetConfig(
        name="dynamicworld",
        asset_id="GOOGLE/DYNAMICWORLD/V1",
        version="v1",
        metric_group="landcover",
        cadence=Cadence.SCENE,
        spatial_resolution=10.0,
        bands=(BandSpec("label", "landcover_label", "class", Reducer.MEDIAN,
                        is_quality_band=True),),
        enabled=False,
    ),
    "copernicus_dem": DatasetConfig(
        name="copernicus_dem",
        asset_id="COPERNICUS/DEM/GLO30",
        version="GLO30",
        metric_group="terrain",
        cadence=Cadence.SCENE,
        spatial_resolution=30.0,
        bands=(BandSpec("DEM", "elevation_m", "m", Reducer.MEAN,
                        valid_range=ValueRange(-500.0, 9000.0)),),
        enabled=False,
    ),
    "global_surface_water": DatasetConfig(
        name="global_surface_water",
        asset_id="JRC/GSW1_4/GlobalSurfaceWater",
        version="1.4",
        metric_group="water",
        cadence=Cadence.SCENE,
        spatial_resolution=30.0,
        bands=(BandSpec("occurrence", "water_occurrence_pct", "percent",
                        Reducer.MEAN, valid_range=ValueRange(0.0, 100.0)),),
        enabled=False,
    ),
    "mod16a2": DatasetConfig(
        name="mod16a2",
        asset_id="MODIS/061/MOD16A2",
        version="061",
        metric_group="evapotranspiration",
        cadence=Cadence.COMPOSITE_8_DAY,
        spatial_resolution=500.0,
        bands=(BandSpec("ET", "evapotranspiration", "kg/m^2/8day", Reducer.MEAN,
                        0.1, valid_range=ValueRange(-100.0, 1000.0)),),
        enabled=False,
    ),
}


class RegistryError(ValueError):
    """Raised when a dataset configuration is structurally invalid."""


def validate_dataset(config: DatasetConfig) -> None:
    """Reject configurations that would silently produce wrong data."""
    if not config.name:
        raise RegistryError("dataset name must not be empty")
    if not config.asset_id or "/" not in config.asset_id:
        raise RegistryError(
            f"{config.name}: asset_id must be a GEE collection path, got {config.asset_id!r}"
        )
    if not config.bands:
        raise RegistryError(f"{config.name}: at least one band must be configured")
    if config.spatial_resolution <= 0:
        raise RegistryError(f"{config.name}: spatial_resolution must be positive")
    if config.chunk_days <= 0:
        raise RegistryError(f"{config.name}: chunk_days must be positive")

    metrics = [b.metric for b in config.bands] + [d.metric for d in config.derived]
    duplicates = {m for m in metrics if metrics.count(m) > 1}
    if duplicates:
        raise RegistryError(
            f"{config.name}: duplicate metric names {sorted(duplicates)}  -  "
            "metric is part of the idempotency key and must be unique per dataset"
        )

    for spec in config.bands:
        if spec.scale_factor == 0:
            raise RegistryError(f"{config.name}.{spec.band}: scale_factor must not be 0")
        if not spec.unit:
            raise RegistryError(f"{config.name}.{spec.band}: unit is required")

    for spec in config.derived:
        missing = [b for b in spec.bands if b not in {x.band for x in config.bands}]
        if missing:
            raise RegistryError(
                f"{config.name}.{spec.metric}: derived from unconfigured bands {missing}"
            )

    if config.cloud_threshold is not None and config.cloud_property is None:
        raise RegistryError(
            f"{config.name}: cloud_threshold set without a cloud_property to filter on"
        )
    if config.cloud_threshold is not None and not 0 <= config.cloud_threshold <= 100:
        raise RegistryError(f"{config.name}: cloud_threshold must be a percentage")


def validate_registry(datasets: Optional[dict[str, DatasetConfig]] = None) -> None:
    registry = DATASETS if datasets is None else datasets
    for key, config in registry.items():
        if key != config.name:
            raise RegistryError(f"registry key {key!r} does not match name {config.name!r}")
        validate_dataset(config)


def get_dataset(name: str) -> DatasetConfig:
    if name in DATASETS:
        return DATASETS[name]
    if name in FUTURE_DATASETS:
        return FUTURE_DATASETS[name]
    raise RegistryError(
        f"unknown dataset {name!r}; configured: {sorted(DATASETS)}"
    )


def enabled_datasets(only: Optional[list[str]] = None) -> list[DatasetConfig]:
    """Datasets the pipeline should run, optionally narrowed by name."""
    if only:
        selected = [get_dataset(n) for n in only]
    else:
        selected = [d for d in DATASETS.values() if d.enabled]
    return selected


# Fail fast at import: a malformed registry must never reach a live GEE query.
validate_registry()
