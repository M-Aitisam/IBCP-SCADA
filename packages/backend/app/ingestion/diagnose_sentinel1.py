"""Read-only scene counts: python -m app.ingestion.diagnose_sentinel1."""
from datetime import date
import json

from app.ingestion.extractor import Extractor
from app.ingestion.gee_client import EarthEngineClient
from app.ingestion.registry import DATASETS
from app.ingestion.roi import ROIResolver


def main():
    client = EarthEngineClient()
    client.initialise()
    ee = client.ee
    roi = ROIResolver(client).resolve()
    print(f"ROI: {roi.source}; regions: {len(roi)}", flush=True)
    config = DATASETS["sentinel1"]
    for end in ("2016-01-08", "2016-04-01"):
        source = ee.ImageCollection(config.asset_id).filterDate("2016-01-01", end)
        spatial = source.filterBounds(roi.feature_collection.geometry())
        iw = spatial.filter(ee.Filter.eq("instrumentMode", "IW"))
        vv = iw.filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        dual = vv.filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        result = ee.Dictionary({
            "global_IW": source.filter(ee.Filter.eq("instrumentMode", "IW")).size(),
            "ROI_before_IW": spatial.size(),
            "ROI_IW": iw.size(),
            "point_IW": source.filterBounds(ee.Geometry.Point([68, 25.5]))
                .filter(ee.Filter.eq("instrumentMode", "IW")).size(),
            "ROI_IW_VV": vv.size(),
            "ROI_IW_VV_VH": dual.size(),
            "pipeline": Extractor(client, roi).build_collection(
                config, date(2016, 1, 1), date.fromisoformat(end)).size(),
        }).getInfo()
        print(f"2016-01-01..{end} (exclusive): {json.dumps(result)}", flush=True)

    # Tiny synthetic server-side reductions verify optional VH handling without
    # running a district backfill or writing anything to the database.
    ex = Extractor(client, roi)
    for dual_pol in (False, True):
        image = ee.Image.constant(-18).rename("VV")
        if dual_pol:
            image = image.addBands(ee.Image.constant(-24).rename("VH"))
        values = ex._prepare_image(image, config).reduceRegion(
            reducer=ee.Reducer.mean(), geometry=ee.Geometry.Point([68, 25.5]).buffer(20),
            scale=10,
        ).getInfo()
        assert values["VV"] == -18 and values["water_fraction"] == 1
        assert values.get("VH") == (-24 if dual_pol else None)
        print(f"Prepared synthetic dual_pol={dual_pol}: {values}", flush=True)


if __name__ == "__main__":
    main()
