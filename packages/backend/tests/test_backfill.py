from datetime import date

from app.ingestion.backfill import BACKFILL_CHUNK_DAYS, planned_rows


def test_plan_is_bounded_and_inclusive():
    rows = planned_rows(date(2024, 1, 1), date(2024, 2, 15), ["mod11a2"])
    assert rows[0]["chunk_start"] == date(2024, 1, 1)
    assert rows[-1]["chunk_end"] == date(2024, 2, 15)
    assert all(
        (row["chunk_end"] - row["chunk_start"]).days + 1
        <= BACKFILL_CHUNK_DAYS["mod11a2"]
        for row in rows
    )


def test_plan_covers_all_five_datasets():
    rows = planned_rows(date(2024, 1, 1), date(2024, 1, 1))
    assert {row["dataset"] for row in rows} == {
        "chirps", "mod13q1", "mod11a2", "sentinel1", "sentinel2"
    }