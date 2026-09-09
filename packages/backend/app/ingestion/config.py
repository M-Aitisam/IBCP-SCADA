# packages/backend/app/ingestion/config.py
"""Ingestion-specific settings.

Kept separate from app.core.config.Settings so that importing the FastAPI app
never pulls in GEE configuration, and so the ingestion CLI can run with a
different env surface than the web tier. GEE_PROJECT is read from the existing
core settings rather than redeclared, to avoid a second source of truth for a
variable the project already has.
"""
from __future__ import annotations

import base64
import binascii
import re
from datetime import date
from pathlib import Path
from typing import Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import settings as core_settings


class IngestionSettings(BaseSettings):
    # --- Google Earth Engine auth ---------------------------------------
    # Service account email, e.g. gee-ingest@<project>.iam.gserviceaccount.com
    GEE_SERVICE_ACCOUNT: Optional[str] = None
    # The service account JSON key. Accepts either raw JSON or base64-encoded
    # JSON, because most CI secret stores mangle embedded newlines.
    GEE_PRIVATE_KEY: Optional[str] = None
    # Alternative: path to a key file (never commit one).
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None
    # Falls back to the project id the rest of the app already configures.
    GEE_PROJECT_ID: Optional[str] = None

    # --- Temporal window --------------------------------------------------
    GEE_HISTORICAL_START: date = date(2016, 1, 1)
    # A *requested* cutoff, not an assertion that any dataset reaches it.
    GEE_TARGET_END: date = date(2026, 8, 19)
    # Overlap re-queried on each daily run so late-published scenes are picked
    # up. Safe because writes are idempotent upserts.
    GEE_LOOKBACK_DAYS: int = 7

    # --- Quality ----------------------------------------------------------
    GEE_CLOUD_THRESHOLD: float = 20.0

    # --- Region of interest ----------------------------------------------
    # Default: GEE's own FAO GAUL level-2 admin boundaries, filtered to the two
    # provinces the proposal targets. No boundary data is invented locally.
    GEE_ROI_ASSET_ID: str = "FAO/GAUL/2015/level2"
    GEE_ROI_PROVINCE_PROPERTY: str = "ADM1_NAME"
    GEE_ROI_DISTRICT_PROPERTY: str = "ADM2_NAME"
    GEE_ROI_TEHSIL_PROPERTY: Optional[str] = None
    GEE_ROI_ID_PROPERTY: str = "ADM2_CODE"
    GEE_ROI_PROVINCES: str = "Balochistan,Sindh"
    GEE_ROI_COUNTRY_PROPERTY: str = "ADM0_NAME"
    GEE_ROI_COUNTRY: str = "Pakistan"
    # Local GeoJSON overrides the asset entirely, for supplied tehsil files.
    GEE_ROI_GEOJSON_PATH: Optional[str] = None
    GEE_ROI_REGION_TYPE: str = "district"
    # Cap regions per run; useful for the small verification run.
    GEE_ROI_LIMIT: Optional[int] = None

    # --- Execution --------------------------------------------------------
    GEE_DAILY_ENABLED: bool = True
    GEE_MAX_RETRIES: int = 4
    GEE_RETRY_BASE_SECONDS: float = 2.0
    GEE_REQUEST_TIMEOUT_SECONDS: int = 300

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------

    @field_validator("GEE_LOOKBACK_DAYS")
    @classmethod
    def _non_negative_lookback(cls, v: int) -> int:
        if v < 0:
            raise ValueError("GEE_LOOKBACK_DAYS must be >= 0")
        return v

    @field_validator("GEE_CLOUD_THRESHOLD")
    @classmethod
    def _percentage(cls, v: float) -> float:
        if not 0 <= v <= 100:
            raise ValueError("GEE_CLOUD_THRESHOLD must be between 0 and 100")
        return v

    @model_validator(mode="after")
    def _check_window(self) -> "IngestionSettings":
        if self.GEE_TARGET_END < self.GEE_HISTORICAL_START:
            raise ValueError("GEE_TARGET_END must not precede GEE_HISTORICAL_START")
        return self

    # ------------------------------------------------------------------

    @property
    def project_id(self) -> Optional[str]:
        """The Earth Engine cloud project.

        Falls back to the service-account email when unset or obviously wrong:
        a service account is always `name@<project-id>.iam.gserviceaccount.com`,
        so the project is recoverable from it.
        """
        configured = (self.GEE_PROJECT_ID or core_settings.GEE_PROJECT or "").strip()
        if configured and not self._looks_like_client_id(configured):
            return configured
        derived = self.project_from_service_account()
        return derived or (configured or None)

    @staticmethod
    def _looks_like_client_id(value: str) -> bool:
        """A GCP project id is never all digits; the numeric client_id is."""
        return value.isdigit()

    def project_from_service_account(self) -> Optional[str]:
        email = (self.GEE_SERVICE_ACCOUNT or "").strip()
        match = re.fullmatch(
            r"[^@]+@([a-z][a-z0-9-]{4,28}[a-z0-9])\.iam\.gserviceaccount\.com", email
        )
        return match.group(1) if match else None

    def project_id_warning(self) -> Optional[str]:
        """Explain a rejected GEE_PROJECT_ID, for the config report."""
        configured = (self.GEE_PROJECT_ID or core_settings.GEE_PROJECT or "").strip()
        if configured and self._looks_like_client_id(configured):
            derived = self.project_from_service_account()
            return (
                f"GEE_PROJECT_ID is {configured!r}, which is the service account's "
                "numeric client_id, not a project id."
                + (f" Using {derived!r}, derived from GEE_SERVICE_ACCOUNT." if derived
                   else " Set it to the `project_id` field of your key file.")
            )
        return None

    @property
    def roi_provinces(self) -> list[str]:
        return [p.strip() for p in self.GEE_ROI_PROVINCES.split(",") if p.strip()]

    def service_account_key(self) -> Optional[str]:
        """Return the service account key as a JSON string, or None.

        Accepts, in order: the raw JSON, a path to the downloaded .json file,
        or base64-encoded JSON (CI secret stores mangle embedded newlines).

        The value is never echoed  -  not in errors, not in logs. Diagnostics
        describe the *shape* of what was supplied so a mis-paste is
        identifiable without exposing key material.
        """
        raw = self.GEE_PRIVATE_KEY
        if not raw:
            return None
        raw = raw.strip().strip('"').strip("'")

        if raw.startswith("{"):
            return raw

        # A path to the key file is the least error-prone option locally.
        try:
            candidate = Path(raw)
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8").strip()
                if not text.startswith("{"):
                    raise ValueError(
                        f"GEE_PRIVATE_KEY points at {candidate}, but that file is "
                        "not a JSON service-account key."
                    )
                return text
        except OSError:
            # Not a usable path (too long, illegal characters); fall through.
            pass

        self._reject_common_mistakes(raw)

        try:
            decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError(self._key_shape_hint(raw)) from exc
        if not decoded.lstrip().startswith("{"):
            raise ValueError(self._key_shape_hint(raw))
        return decoded

    @staticmethod
    def _reject_common_mistakes(raw: str) -> None:
        """Name the specific wrong field when the shape is recognisable."""
        if raw.startswith("-----BEGIN"):
            raise ValueError(
                "GEE_PRIVATE_KEY looks like the `private_key` PEM block from the "
                "service-account file. Supply the WHOLE .json file instead  -  its "
                "path, its full contents, or those contents base64-encoded."
            )
        if re.fullmatch(r"[0-9a-fA-F]{40}", raw):
            raise ValueError(
                "GEE_PRIVATE_KEY looks like the `private_key_id` field (a 40-character "
                "fingerprint), not the key itself. Supply the WHOLE .json file: its "
                "path, its full contents, or those contents base64-encoded."
            )
        if raw.endswith(".json"):
            raise ValueError(
                f"GEE_PRIVATE_KEY looks like a file path ({raw}) but no file exists "
                "there. Use an absolute path, and on Windows prefer forward slashes."
            )

    @staticmethod
    def _key_shape_hint(raw: str) -> str:
        return (
            "GEE_PRIVATE_KEY is not a service-account key. It must be one of:\n"
            "    - an absolute path to the downloaded .json key file\n"
            "    - the full contents of that file (starting with '{')\n"
            "    - those contents base64-encoded\n"
            f"  (received {len(raw)} characters, not starting with '{{')"
        )

    def missing_configuration(self) -> list[str]:
        """Human-readable list of what still has to be supplied before a live run."""
        missing: list[str] = []
        if not self.project_id:
            missing.append(
                "GEE_PROJECT_ID (or the existing GEE_PROJECT) - the Earth Engine "
                "cloud project to bill and authorise requests against"
            )
        has_inline_key = bool(self.GEE_SERVICE_ACCOUNT and self.GEE_PRIVATE_KEY)
        has_key_file = bool(self.GOOGLE_APPLICATION_CREDENTIALS)
        if not (has_inline_key or has_key_file):
            missing.append(
                "GEE_SERVICE_ACCOUNT + GEE_PRIVATE_KEY (or GOOGLE_APPLICATION_"
                "CREDENTIALS) - service-account credentials for unattended runs"
            )
        return missing


ingestion_settings = IngestionSettings()
