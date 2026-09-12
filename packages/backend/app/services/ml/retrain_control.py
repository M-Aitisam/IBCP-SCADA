# packages/backend/app/services/ml/retrain_control.py
"""Triggering the ML retraining workflow from the API.

Same shape and reasoning as app.services.ingestion_control: the API never
trains in-process (Vercel's execution budget is seconds; a hyperparameter
search over a real dataset is minutes), so this only dispatches the
`ml-training.yml` GitHub Actions workflow and reports whether that dispatch
was accepted.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
DISPATCH_TIMEOUT_SECONDS = 15.0


class RetrainControlError(RuntimeError):
    """Dispatch failed for a reason worth showing the caller (maps to 502)."""


@dataclass
class RetrainDispatchResult:
    accepted: bool
    detail: str
    workflow: Optional[str] = None
    repository: Optional[str] = None
    cli_command: Optional[str] = None

    def to_json(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "detail": self.detail,
            "workflow": self.workflow,
            "repository": self.repository,
            "cli_command": self.cli_command,
        }


def dispatch_configured() -> bool:
    return bool(settings.GITHUB_REPOSITORY and settings.GITHUB_DISPATCH_TOKEN)


CLI_COMMAND = (
    "cd packages/ml-pipeline && "
    "python scripts/extract_training_data.py && "
    "python scripts/feature_engineering.py && "
    "python scripts/train_xgboost.py && "
    "python scripts/evaluate_model.py"
)


async def dispatch_retrain() -> RetrainDispatchResult:
    if not dispatch_configured():
        return RetrainDispatchResult(
            accepted=False,
            detail=(
                "Remote retraining dispatch is not configured on this deployment "
                "(GITHUB_REPOSITORY and GITHUB_DISPATCH_TOKEN are unset). Run the "
                "pipeline manually with the command below."
            ),
            cli_command=CLI_COMMAND,
        )

    url = (
        f"{GITHUB_API}/repos/{settings.GITHUB_REPOSITORY}"
        f"/actions/workflows/{settings.ML_TRAINING_WORKFLOW_FILE}/dispatches"
    )
    payload = {"ref": settings.GITHUB_WORKFLOW_REF, "inputs": {}}

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
        logger.error("ml retrain dispatch transport failure: %s", type(exc).__name__)
        raise RetrainControlError(
            f"could not reach GitHub to dispatch the workflow ({type(exc).__name__})"
        ) from None

    if response.status_code == 204:
        logger.info("dispatched ml-training workflow")
        return RetrainDispatchResult(
            accepted=True,
            detail="Retraining workflow dispatched. Check GitHub Actions for progress.",
            workflow=settings.ML_TRAINING_WORKFLOW_FILE,
            repository=settings.GITHUB_REPOSITORY,
            cli_command=CLI_COMMAND,
        )

    if response.status_code in (401, 403):
        detail = "GitHub rejected the dispatch token (needs `actions: write` on this repo)"
    elif response.status_code == 404:
        detail = (
            f"workflow {settings.ML_TRAINING_WORKFLOW_FILE!r} or repository "
            f"{settings.GITHUB_REPOSITORY!r} not found"
        )
    else:
        detail = f"GitHub returned {response.status_code}"

    logger.error("ml retrain dispatch failed: %s", detail)
    raise RetrainControlError(detail)
