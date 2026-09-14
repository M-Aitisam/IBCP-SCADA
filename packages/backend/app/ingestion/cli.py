# packages/backend/app/ingestion/cli.py
"""Command-line entry point for the GEE acquisition pipeline.

    python -m app.ingestion.cli daily [--dry-run] [--dataset chirps ...]
    python -m app.ingestion.cli backfill [--dry-run] [--dataset ...] [--restart]
    python -m app.ingestion.cli test-run [--days N] [--regions N] [--dry-run]
    python -m app.ingestion.cli availability
    python -m app.ingestion.cli check-config
    python -m app.ingestion.cli analytics [--date YYYY-MM-DD] [--stage NAME] [--resume RUN_ID]

Deliberately never imported by app.main: the Vercel HTTP function must not be
able to trigger a ten-year backfill, and nothing here should run at app start.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date, timedelta
from typing import Optional

from app.databases.timestampdb.repository import DryRunRepository, TimestampRepository
from app.db.database import AsyncSessionLocal, engine
from app.ingestion.backfill import planned_rows, validate_datasets
from app.ingestion.config import ingestion_settings
from app.ingestion.gee_client import EarthEngineClient, GEEAuthError
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.registry import DATASETS, FUTURE_DATASETS, validate_registry
from app.ingestion.roi import ROIConfigurationError
from app.ingestion.windows import DateWindow

logger = logging.getLogger("app.ingestion")


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        # stderr, so stdout carries only the report. `availability --json` and
        # `daily --json` are meant to be piped into jq or a log collector.
        stream=sys.stderr,
    )
    # asyncpg/sqlalchemy chatter drowns the run summary otherwise.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def _backfill_args(args: argparse.Namespace) -> tuple[date, date, list[str]]:
    start = date.fromisoformat(args.start_date or os.getenv("BACKFILL_START_DATE", "") or ingestion_settings.GEE_HISTORICAL_START.isoformat())
    end = date.fromisoformat(args.end_date or os.getenv("BACKFILL_END_DATE", "") or ingestion_settings.GEE_TARGET_END.isoformat())
    env_datasets = [item.strip() for item in os.getenv("BACKFILL_DATASETS", "").split(",") if item.strip()]
    return start, end, validate_datasets(args.dataset or env_datasets or None)


async def _backfill_status(repo: TimestampRepository) -> int:
    rows = await repo.backfill_progress()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    print("GEE BACKFILL STATUS")
    print("-" * 20)
    print(f"chunks: {len(rows)}  " + "  ".join(f"{key}: {value}" for key, value in sorted(counts.items())))
    for row in rows:
        print(f"{row.dataset:<10} {row.chunk_start}..{row.chunk_end} {row.status:<9} attempts={row.attempts} records=+{row.records_inserted}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    validate_registry()

    if args.command == "check-config":
        return _check_config()

    if args.command == "analytics":
        # Deliberately handled before any Earth Engine work: the analytics
        # cascade reads and writes the database only. Requiring GEE
        # credentials to recompute a drought score would be a false
        # dependency, and would stop the cascade being re-runnable anywhere.
        from app.intelligence import orchestrator

        async with AsyncSessionLocal() as session:
            cycle = await orchestrator.run_cycle(
                session,
                reference_date=args.date,
                run_id=args.resume,
                only=args.stage,
                resume=bool(args.resume),
            )
        print()
        print(cycle.render())
        if args.json:
            print()
            print(json.dumps(cycle.to_json(), indent=2, default=str))
        return 1 if cycle.status == "failed" else 0

    settings = ingestion_settings
    if getattr(args, "regions", None):
        settings = settings.model_copy(update={"GEE_ROI_LIMIT": args.regions})

    async with AsyncSessionLocal() as session:
        real_repo = TimestampRepository(session)
        if args.command == "backfill-plan":
            start, end, datasets = _backfill_args(args)
            rows = planned_rows(start, end, datasets)
            inserted = await real_repo.plan_backfill(rows)
            print(f"Planned {len(rows)} chunks; inserted {inserted} new chunks.")
            return 0
        if args.command == "backfill-status":
            return await _backfill_status(real_repo)
        if args.command == "backfill-reset":
            removed = await real_repo.reset_backfill(args.dataset[0] if args.dataset else None)
            print(f"Removed {removed} backfill progress row(s).")
            return 0
        if args.command == "backfill-next":
            row = await real_repo.claim_next_backfill()
            if row is None:
                print("No pending or retryable backfill chunks remain.")
                return 0
            window = DateWindow(row.chunk_start, row.chunk_end + timedelta(days=1))
            scoped = settings.model_copy(update={"GEE_HISTORICAL_START": row.chunk_start, "GEE_TARGET_END": row.chunk_end})
            pipeline = IngestionPipeline(store=real_repo, settings=scoped)
            try:
                result = await pipeline.run(mode="backfill", only=[row.dataset], window_override=window)
                outcome = result.outcomes[0] if result.outcomes else None
                success = outcome is not None and outcome.status == "success"
                await real_repo.finish_backfill(row.id, status="completed" if success else "failed", records_inserted=outcome.records_inserted if outcome else 0, error=outcome.error if outcome else "no dataset outcome")
                print(result.render())
                return 0 if success else 1
            except Exception as exc:  # noqa: BLE001 - queue state must record all failures
                await real_repo.finish_backfill(row.id, status="failed", error=f"{type(exc).__name__}: {exc}")
                logger.exception("backfill chunk failed")
                return 1
        # Dry run still reads the real database (for resume points and
        # existing-key checks) but suppresses every write.
        store = DryRunRepository(real_repo) if args.dry_run else real_repo

        pipeline = IngestionPipeline(store=store, settings=settings)

        try:
            if args.command == "availability":
                report = await pipeline.availability_report()
                print(
                    json.dumps(
                        [o.to_json() for o in report], indent=2, default=str
                    )
                )
                return 0

            if args.command == "test-run":
                # Small, bounded verification before trusting the full backfill.
                # The window is derived per dataset from its real availability
                # (see IngestionPipeline._window_for), so no date override here.
                scoped = settings.model_copy(
                    update={"GEE_ROI_LIMIT": args.regions or 3}
                )
                pipeline = IngestionPipeline(
                    store=store, settings=scoped, test_lookback_days=args.days
                )
                result = await pipeline.run(
                    mode="test", dry_run=args.dry_run, only=args.dataset
                )
            elif args.command == "backfill":
                if args.restart:
                    for name in args.dataset or list(DATASETS):
                        await store.save_checkpoint(
                            name, backfill_cursor=None, backfill_complete=False
                        )
                    logger.info("backfill cursors reset")
                result = await pipeline.run(
                    mode="backfill", dry_run=args.dry_run, only=args.dataset
                )
            else:
                if not settings.GEE_DAILY_ENABLED and not args.force:
                    logger.warning(
                        "GEE_DAILY_ENABLED is false; skipping. Use --force to override."
                    )
                    return 0
                result = await pipeline.run(
                    mode="daily", dry_run=args.dry_run, only=args.dataset
                )
        except (GEEAuthError, ValueError) as exc:
            # ValueError here means the credential/config values are malformed.
            # Report it as a message, not a traceback: the value itself must
            # never reach the log, and a stack trace helps nobody diagnose a
            # mis-pasted .env entry.
            logger.error("%s", exc)
            return 2
        except ROIConfigurationError as exc:
            logger.error("ROI configuration problem: %s", exc)
            return 3

    print()
    print(result.render())
    if args.json:
        print()
        print(json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status,
                "dry_run": result.dry_run,
                "roi_regions": result.roi_regions,
                "datasets": [o.to_json() for o in result.outcomes],
            },
            indent=2,
            default=str,
        ))

    # Non-zero on total failure so a cron job surfaces it; partial success is
    # an expected, reportable state and exits 0.
    return 1 if result.status == "failed" else 0


def _check_config() -> int:
    """Report what is configured and what is missing, without contacting GEE."""
    s = ingestion_settings
    print("GEE INGESTION CONFIGURATION")
    print("-" * 32)
    warning = s.project_id_warning()
    print(f"Earth Engine project : {s.project_id or '(not set)'}")
    if warning:
        print(f"  WARNING            : {warning}")
    print(f"Service account      : {s.GEE_SERVICE_ACCOUNT or '(not set)'}")
    print(f"Private key          : {'set' if s.GEE_PRIVATE_KEY else '(not set)'}")
    print(f"Credentials file     : {s.GOOGLE_APPLICATION_CREDENTIALS or '(not set)'}")
    print()
    print(f"Historical start     : {s.GEE_HISTORICAL_START}")
    print(f"Requested cutoff     : {s.GEE_TARGET_END}")
    print(f"Lookback days        : {s.GEE_LOOKBACK_DAYS}")
    print(f"Cloud threshold      : {s.GEE_CLOUD_THRESHOLD}%")
    print()
    if s.GEE_ROI_GEOJSON_PATH:
        print(f"ROI source           : GeoJSON {s.GEE_ROI_GEOJSON_PATH}")
    else:
        print(f"ROI source           : {s.GEE_ROI_ASSET_ID}")
        print(f"ROI provinces        : {', '.join(s.roi_provinces)}")
    print(f"ROI region type      : {s.GEE_ROI_REGION_TYPE}")
    print(f"ROI limit            : {s.GEE_ROI_LIMIT or '(none)'}")
    print()
    print("Enabled datasets:")
    for name, config in DATASETS.items():
        flag = "on " if config.enabled else "off"
        print(
            f"  [{flag}] {name:<14} {config.asset_id:<34} "
            f"{config.cadence.value:<8} metrics={','.join(config.all_metrics)}"
        )
    print("Registered but disabled (future phases):")
    for name, config in FUTURE_DATASETS.items():
        print(f"  [off] {name:<14} {config.asset_id}")
    print()

    missing = s.missing_configuration()
    if missing:
        print("MISSING CONFIGURATION - a live run cannot start until these are set:")
        for item in missing:
            print(f"  - {item}")
        return 1

    print("Configuration present. Verifying Earth Engine credentials...")
    try:
        client = EarthEngineClient(s)
        client.initialise()
        ok = client.verify()
    except (GEEAuthError, ValueError) as exc:
        print()
        print(f"  FAILED: {exc}")
        return 1
    print("  Earth Engine authentication OK" if ok else "  Unexpected verify result")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion.cli",
        description="Google Earth Engine satellite data acquisition for GeoVision AI",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--json", action="store_true", help="also emit a JSON summary")

    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="do everything except write to timestampdb",
        )
        p.add_argument(
            "--dataset",
            action="append",
            choices=sorted(set(DATASETS) | set(FUTURE_DATASETS)),
            help="limit to one dataset (repeatable)",
        )

    daily = sub.add_parser("daily", help="incremental run since the last checkpoint")
    add_common(daily)
    daily.add_argument(
        "--force", action="store_true", help="run even if GEE_DAILY_ENABLED is false"
    )

    backfill = sub.add_parser("backfill", help="historical run from GEE_HISTORICAL_START")
    add_common(backfill)
    backfill.add_argument(
        "--restart",
        action="store_true",
        help="clear stored backfill cursors and start from the beginning",
    )

    test = sub.add_parser(
        "test-run", help="small bounded run to verify the pipeline end to end"
    )
    add_common(test)
    test.add_argument("--days", type=int, default=18, help="days back from the cutoff")
    test.add_argument("--regions", type=int, default=3, help="max regions to process")

    sub.add_parser(
        "availability", help="report the real latest observation per dataset"
    )
    sub.add_parser("check-config", help="show configuration and verify GEE auth")

    plan = sub.add_parser("backfill-plan", help="create the automated backfill queue")
    plan.add_argument("--dataset", action="append", choices=sorted(DATASETS))
    plan.add_argument("--start-date")
    plan.add_argument("--end-date")
    sub.add_parser("backfill-status", help="show automated backfill progress")
    reset = sub.add_parser("backfill-reset", help="delete automated backfill progress")
    reset.add_argument("--dataset", action="append", choices=sorted(DATASETS))
    sub.add_parser("backfill-next", help="run one oldest eligible backfill chunk")

    analytics = sub.add_parser(
        "analytics",
        help="run the analytics cascade (quality, baselines, features, hazards, alerts, brief)",
    )
    analytics.add_argument(
        "--date",
        type=lambda v: __import__("datetime").date.fromisoformat(v),
        help="reference date (default: today, UTC)",
    )
    analytics.add_argument(
        "--stage",
        action="append",
        help="limit to one stage (repeatable)",
    )
    analytics.add_argument(
        "--resume",
        help="resume an existing run id, skipping stages that already succeeded",
    )

    return parser


async def _run_and_dispose(args: argparse.Namespace) -> int:
    """Run the command, then release the pool inside the same loop."""
    try:
        return await _run(args)
    finally:
        await engine.dispose()


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    for attr, default in (("dry_run", False), ("dataset", None), ("regions", None),
                          ("start_date", None), ("end_date", None),
                          ("days", None), ("restart", False), ("force", False),
                          ("date", None), ("stage", None), ("resume", None)):
        if not hasattr(args, attr):
            setattr(args, attr, default)
    # One event loop for the work AND the disposal.
    #
    # The previous shape — asyncio.run(_run(...)) followed by
    # asyncio.run(engine.dispose()) in a finally — creates two loops. A pooled
    # asyncpg connection is bound to the loop that opened it, so disposing in
    # the second loop tries to close sockets belonging to a loop that no longer
    # exists and fails with "'NoneType' object has no attribute 'send'".
    #
    # It was invisible while the engine used NullPool (nothing survived to be
    # disposed) and appeared as soon as pooling became the default outside
    # serverless. The work had already committed, so this only ever produced a
    # noisy traceback — but in CI a traceback reads as a failed run.
    return asyncio.run(_run_and_dispose(args))


if __name__ == "__main__":
    raise SystemExit(main())
