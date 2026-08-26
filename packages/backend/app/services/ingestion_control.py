# packages/backend/app/services/ingestion_control.py
"""Triggering and observing ingestion runs from the API.

The API does not ingest. It dispatches the GitHub Actions workflow that already
owns the nightly schedule, and reads run state back out of the database.

Why not just run it here? The backend deploys as a Vercel serverless function
with an execution budget measured in seconds, while a historical backfill runs
for hours and a daily run for minutes. Importing the pipeline into the web tier
would give the HTTP surface the ability to start a job it cannot finish, leaving
a half-written checkpoint and a held lock behind. Dispatching keeps one
execution path — the same one the cron uses — instead of two that can drift.

When dispatch is not configured, every trigger returns a clear "not configured"
result carrying the exact CLI command to run. That is deliberately more useful
than an error, because running the CLI locally is a legitimate and common way to
operate this pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.ingestion.registry import DATASETS

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
DISPATCH_TIMEOUT_SECONDS = 15.0

# Modes the API is willing to dispatch. `test` is intentionally absent: it is a
# developer verification aid, not something to trigger from a dashboard.
DISPATCHABLE_MODES = ("daily", "backfill")


class IngestionControlError(RuntimeError):
    """Dispatch failed for a reason worth showing the caller.

    Means "we could not get the job started" — an unreachable or unhappy
    GitHub. The caller maps this to 502.
    """


class IngestionRequestError(IngestionControlError):
    """The request itself was invalid — a bad dataset name or mode.

    Kept distinct because the correct HTTP status differs: this is the client's
    mistake (400), whereas a plain IngestionControlError is an upstream failure
    (502). Collapsing them told callers to retry a request that will never work.
    """


@dataclass
class DispatchResult:
    accepted: bool
    mode: str
    detail: str
    workflow: Optional[str] = None
    repository: Optional[str] = None
    cli_command: Optional[str] = None

    def to_json(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "mode": self.mode,
            "detail": self.detail,
            "workflow": self.workflow,
            "repository": self.repository,
            # Always present, so an operator without dispatch configured is
            # never left guessing how to actually run the job.
            "cli_command": self.cli_command,
        }


def dispatch_configured() -> bool:
    return bool(settings.GITHUB_REPOSITORY and settings.GITHUB_DISPATCH_TOKEN)


def build_cli_command(
    mode: str,
    datasets: Optional[list[str]] = None,
    dry_run: bool = False,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> str:
    """The equivalent local command, for operators and for the not-configured path."""
    parts = ["python -m app.ingestion.cli", mode]
    for name in datasets or []:
        parts.append(f"--dataset {name}")
    if dry_run:
        parts.append("--dry-run")
    # Dates are environment overrides rather than CLI flags, so they are shown
    # as such instead of inventing options the CLI does not have.
    prefix = ""
    if start is not None:
        prefix += f"GEE_HISTORICAL_START={start.isoformat()} "
    if end is not None:
        prefix += f"GEE_TARGET_END={end.isoformat()} "
    return prefix + " ".join(parts)


def validate_datasets(datasets: Optional[list[str]]) -> list[str]:
    """Reject unknown dataset names before dispatching anything."""
    if not datasets:
        return []
    unknown = [d for d in datasets if d not in DATASETS]
    if unknown:
        raise IngestionRequestError(
            f"unknown dataset(s): {', '.join(sorted(unknown))}. "
            f"Configured: {', '.join(sorted(DATASETS))}"
        )
    return list(datasets)


async def dispatch_workflow(
    mode: str,
    *,
    datasets: Optional[list[str]] = None,
    dry_run: bool = False,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> DispatchResult:
    """Fire the GitHub Actions workflow, or explain why we cannot.

    GitHub answers a successful `workflow_dispatch` with 204 and no body — it
    queues the run rather than returning an id — so this reports acceptance,
    not completion. Progress is observed through the run log in the database,
    which the pipeline writes as it goes.
    """
    if mode not in DISPATCHABLE_MODES:
        raise IngestionRequestError(
            f"mode must be one of {', '.join(DISPATCHABLE_MODES)}, got {mode!r}"
        )
    resolved = validate_datasets(datasets)
    cli = build_cli_command(mode, resolved, dry_run, start, end)

    if not dispatch_configured():
        return DispatchResult(
            accepted=False,
            mode=mode,
            detail=(
                "Remote ingestion dispatch is not configured on this deployment "
                "(GITHUB_REPOSITORY and GITHUB_DISPATCH_TOKEN are unset). The "
                "nightly scheduled run is unaffected. Run the command below to "
                "ingest manually."
            ),
            cli_command=cli,
        )

    url = (
        f"{GITHUB_API}/repos/{settings.GITHUB_REPOSITORY}"
        f"/actions/workflows/{settings.GITHUB_WORKFLOW_FILE}/dispatches"
    )
    # workflow_dispatch inputs are strings on the wire, whatever their declared
    # type, and omitted keys fall back to the workflow's own defaults.
    inputs: dict[str, str] = {"mode": mode, "dry_run": "true" if dry_run else "false"}
    if resolved:
        inputs["dataset"] = resolved[0] if len(resolved) == 1 else ",".join(resolved)
    if start is not None:
        inputs["start_date"] = start.isoformat()
    if end is not None:
        inputs["end_date"] = end.isoformat()

    payload = {"ref": settings.GITHUB_WORKFLOW_REF, "inputs": inputs}

    try:
        async with httpx.AsyncClient(timeout=DISPATCH_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {settings.GITHUB_DISPATCH_TOKEN}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except httpx.HTTPError as exc:
        # The token must never reach a log line, and the exception can carry the
        # request headers, so only the exception type is recorded.
        logger.error("workflow dispatch transport failure: %s", type(exc).__name__)
        raise IngestionControlError(
            f"could not reach GitHub to dispatch the workflow ({type(exc).__name__})"
        ) from None

    if response.status_code == 204:
        logger.info(
            "dispatched ingestion workflow mode=%s datasets=%s dry_run=%s",
            mode,
            resolved or "all",
            dry_run,
        )
        return DispatchResult(
            accepted=True,
            mode=mode,
            detail=(
                "Ingestion workflow dispatched. GitHub queues the run rather "
                "than returning an id; track progress via /ingestion/status."
            ),
            workflow=settings.GITHUB_WORKFLOW_FILE,
            repository=settings.GITHUB_REPOSITORY,
            cli_command=cli,
        )

    # Map the failures an operator can actually act on.
    if response.status_code in (401, 403):
        detail = "GitHub rejected the dispatch token (needs `actions: write` on this repo)"
    elif response.status_code == 404:
        detail = (
            f"workflow {settings.GITHUB_WORKFLOW_FILE!r} or repository "
            f"{settings.GITHUB_REPOSITORY!r} not found, or the ref "
            f"{settings.GITHUB_WORKFLOW_REF!r} does not exist"
        )
    elif response.status_code == 422:
        detail = (
            "GitHub rejected the workflow inputs — the workflow file may not "
            "declare the inputs this API sends"
        )
    else:
        detail = f"GitHub returned {response.status_code}"

    logger.error("workflow dispatch failed: %s", detail)
    raise IngestionControlError(detail)
