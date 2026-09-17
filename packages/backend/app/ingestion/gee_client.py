# packages/backend/app/ingestion/gee_client.py
"""Earth Engine authentication and transient-failure retry.

Credentials come from environment/secrets only. Nothing here logs a key, a
token or a private key body, and error messages from Google are scrubbed of
anything that looks like a credential before being re-raised.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any, Callable, Optional, TypeVar

from app.ingestion.config import IngestionSettings, ingestion_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Substrings that mark a GEE/network failure as worth retrying. Anything else
# (bad asset id, malformed geometry, permission denied) is permanent and
# retrying it just burns quota.
_TRANSIENT_MARKERS = (
    "timed out",
    "timeout",
    "deadline exceeded",
    "temporarily unavailable",
    "backend error",
    "internal error",
    "service unavailable",
    "too many requests",
    "rate limit",
    "quota exceeded",
    "connection reset",
    "connection aborted",
    "bad gateway",
    "503",
    "502",
    "429",
    # GEE's hard per-request element cap. Deterministic given the same
    # request, so a bare retry never succeeds - extractor.py batches images
    # to stay under this cap. Listed as transient (not permanent) because a
    # smaller/rebatched retry of the *same operation* can still succeed.
    "accumulating over",
    "5000 element",
)

_PERMANENT_MARKERS = (
    "not found",
    "does not exist",
    "permission denied",
    "not authorized",
    "invalid argument",
    "no such band",
    "image.select",
)

# Redact anything resembling a key or token from text that might be logged.
_SECRET_PATTERN = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----)"
    r"|(ya29\.[\w\-\.]+)"
    r"|(\"private_key\"\s*:\s*\"[^\"]*\")",
    re.DOTALL,
)


def scrub(message: str) -> str:
    """Remove credential-looking material from a string before logging it."""
    return _SECRET_PATTERN.sub("[REDACTED]", message)


class GEEAuthError(RuntimeError):
    """Raised when Earth Engine cannot be initialised."""


class GEETransientError(RuntimeError):
    """A failure worth retrying."""


class GEEPermanentError(RuntimeError):
    """A failure that will not fix itself; do not retry."""


def classify_error(exc: BaseException) -> Exception:
    """Sort a raw exception into transient vs permanent."""
    text = scrub(str(exc)).lower()
    for marker in _PERMANENT_MARKERS:
        if marker in text:
            return GEEPermanentError(scrub(str(exc)))
    for marker in _TRANSIENT_MARKERS:
        if marker in text:
            return GEETransientError(scrub(str(exc)))
    # Unknown failures are treated as transient once or twice rather than
    # aborting a 10-year backfill on a blip, but the retry cap bounds the cost.
    return GEETransientError(scrub(str(exc)))


class EarthEngineClient:
    """Thin wrapper owning initialisation and the retry policy."""

    def __init__(self, settings: Optional[IngestionSettings] = None):
        self.settings = settings or ingestion_settings
        self._initialised = False
        self._ee: Any = None

    # ------------------------------------------------------------------

    @property
    def ee(self) -> Any:
        """The `ee` module, guaranteed initialised."""
        if not self._initialised:
            self.initialise()
        return self._ee

    def initialise(self) -> None:
        if self._initialised:
            return

        missing = self.settings.missing_configuration()
        try:
            import ee  # imported lazily so unit tests need no GEE install
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise GEEAuthError(
                "earthengine-api is not installed; run "
                "`pip install -r packages/backend/requirements.txt`"
            ) from exc

        project = self.settings.project_id
        credentials = None

        key_json = self.settings.service_account_key()
        if self.settings.GEE_SERVICE_ACCOUNT and key_json:
            try:
                parsed = json.loads(key_json)
            except json.JSONDecodeError as exc:
                raise GEEAuthError(
                    "GEE_PRIVATE_KEY is not valid JSON (contents not shown)"
                ) from exc
            if "private_key" not in parsed:
                raise GEEAuthError(
                    "GEE_PRIVATE_KEY JSON has no 'private_key' field; expected a "
                    "service-account key file"
                )
            credentials = ee.ServiceAccountCredentials(
                self.settings.GEE_SERVICE_ACCOUNT, key_data=key_json
            )
            logger.info(
                "Earth Engine: service-account auth for %s (project=%s)",
                self.settings.GEE_SERVICE_ACCOUNT,
                project,
            )
        elif self.settings.GOOGLE_APPLICATION_CREDENTIALS:
            logger.info(
                "Earth Engine: application-default credentials from file (project=%s)",
                project,
            )
        elif missing:
            raise GEEAuthError(
                "Earth Engine is not configured. Missing:\n  - "
                + "\n  - ".join(missing)
                + "\nSee packages/backend/docs/GEE_INGESTION.md for setup."
            )

        try:
            if credentials is not None:
                ee.Initialize(credentials, project=project)
            else:
                # Falls back to whatever ADC is present; this is the local
                # developer path after `earthengine authenticate`.
                ee.Initialize(project=project)
        except Exception as exc:  # noqa: BLE001 - surface a scrubbed message
            raise GEEAuthError(
                f"Earth Engine initialisation failed: {scrub(str(exc))}"
            ) from None

        self._ee = ee
        self._initialised = True
        logger.info("Earth Engine initialised (project=%s)", project)

    def verify(self) -> bool:
        """Round-trip a trivial computation to prove auth actually works."""
        value = self.ee.Number(1).add(1).getInfo()
        ok = value == 2
        logger.info("Earth Engine connectivity check: %s", "ok" if ok else "unexpected")
        return ok

    # ------------------------------------------------------------------

    def with_retry(self, operation: Callable[[], T], description: str = "") -> T:
        """Run a GEE call with exponential backoff on transient failures."""
        attempts = max(1, self.settings.GEE_MAX_RETRIES)
        base = self.settings.GEE_RETRY_BASE_SECONDS
        last: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                return operation()
            except Exception as exc:  # noqa: BLE001
                classified = classify_error(exc)
                last = classified
                if isinstance(classified, GEEPermanentError):
                    logger.error(
                        "GEE permanent failure (%s): %s", description, classified
                    )
                    raise classified from None
                if attempt == attempts:
                    break
                # Full jitter, so parallel dataset runs do not resynchronise.
                delay = random.uniform(0, base * (2 ** (attempt - 1)))
                logger.warning(
                    "GEE transient failure (%s) attempt %d/%d, retrying in %.1fs: %s",
                    description,
                    attempt,
                    attempts,
                    delay,
                    classified,
                )
                time.sleep(delay)

        logger.error("GEE failed after %d attempts (%s): %s", attempts, description, last)
        raise last if last else RuntimeError("GEE call failed")
