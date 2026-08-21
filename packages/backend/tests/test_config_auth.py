# packages/backend/tests/test_config_auth.py
"""Configuration loading and credential handling.

Includes the security-relevant behaviour: keys are accepted in either encoding,
and nothing credential-shaped survives into a log line.
"""
from __future__ import annotations

import base64
import json
from datetime import date

import pytest

from app.ingestion.config import IngestionSettings
from app.ingestion.gee_client import (
    GEEPermanentError,
    GEETransientError,
    classify_error,
    scrub,
)

FAKE_KEY = {
    "type": "service_account",
    "project_id": "demo",
    "private_key": "-----BEGIN PRIVATE KEY-----\nAAAAFAKEKEY\n-----END PRIVATE KEY-----\n",
    "client_email": "gee@demo.iam.gserviceaccount.com",
}


def settings(**overrides) -> IngestionSettings:
    base = {
        "GEE_SERVICE_ACCOUNT": None,
        "GEE_PRIVATE_KEY": None,
        "GOOGLE_APPLICATION_CREDENTIALS": None,
        "GEE_PROJECT_ID": None,
    }
    base.update(overrides)
    return IngestionSettings(**base)


# --- defaults ---------------------------------------------------------------


def test_default_window_matches_the_specified_range():
    s = settings()
    assert s.GEE_HISTORICAL_START == date(2016, 1, 1)
    assert s.GEE_TARGET_END == date(2026, 8, 19)
    assert s.GEE_LOOKBACK_DAYS == 7
    assert s.GEE_CLOUD_THRESHOLD == 20.0


def test_default_roi_targets_the_two_provinces():
    s = settings()
    assert s.GEE_ROI_ASSET_ID == "FAO/GAUL/2015/level2"
    assert s.roi_provinces == ["Balochistan", "Sindh"]


def test_project_id_falls_back_to_existing_core_setting(monkeypatch):
    import app.ingestion.config as config_module

    monkeypatch.setattr(config_module.core_settings, "GEE_PROJECT", "legacy-project")
    assert settings().project_id == "legacy-project"
    # An explicit ingestion-specific value wins.
    assert settings(GEE_PROJECT_ID="new-project").project_id == "new-project"


# --- validation -------------------------------------------------------------


def test_negative_lookback_rejected():
    with pytest.raises(ValueError):
        settings(GEE_LOOKBACK_DAYS=-1)


def test_cloud_threshold_must_be_a_percentage():
    with pytest.raises(ValueError):
        settings(GEE_CLOUD_THRESHOLD=150)


def test_target_end_before_historical_start_rejected():
    with pytest.raises(ValueError, match="must not precede"):
        settings(GEE_HISTORICAL_START=date(2026, 1, 1), GEE_TARGET_END=date(2020, 1, 1))


# --- credentials ------------------------------------------------------------


def test_raw_json_key_accepted():
    s = settings(GEE_PRIVATE_KEY=json.dumps(FAKE_KEY))
    assert json.loads(s.service_account_key())["client_email"].endswith(
        "gserviceaccount.com"
    )


def test_base64_key_accepted():
    """CI secret stores mangle embedded newlines, so base64 must work."""
    encoded = base64.b64encode(json.dumps(FAKE_KEY).encode()).decode()
    s = settings(GEE_PRIVATE_KEY=encoded)
    assert json.loads(s.service_account_key())["project_id"] == "demo"


def test_garbage_key_raises_without_echoing_contents():
    s = settings(GEE_PRIVATE_KEY="!!!not-base64-or-json!!!")
    with pytest.raises(ValueError) as exc:
        s.service_account_key()
    assert "!!!not-base64" not in str(exc.value)


def test_no_key_returns_none():
    assert settings().service_account_key() is None


def test_missing_configuration_is_reported():
    missing = settings().missing_configuration()
    assert len(missing) == 2
    assert any("GEE_PROJECT_ID" in m for m in missing)
    assert any("GEE_SERVICE_ACCOUNT" in m for m in missing)


def test_complete_configuration_reports_nothing_missing():
    s = settings(
        GEE_PROJECT_ID="p",
        GEE_SERVICE_ACCOUNT="a@b.iam.gserviceaccount.com",
        GEE_PRIVATE_KEY=json.dumps(FAKE_KEY),
    )
    assert s.missing_configuration() == []


def test_credentials_file_alone_is_sufficient():
    s = settings(GEE_PROJECT_ID="p", GOOGLE_APPLICATION_CREDENTIALS="/tmp/key.json")
    assert s.missing_configuration() == []


# --- secret scrubbing -------------------------------------------------------


def test_private_key_body_is_scrubbed():
    message = f"auth failed with {FAKE_KEY['private_key']} while connecting"
    cleaned = scrub(message)
    assert "AAAAFAKEKEY" not in cleaned
    assert "[REDACTED]" in cleaned


def test_oauth_token_is_scrubbed():
    cleaned = scrub("Bearer ya29.a0AfH6SMBxxxxxxxxtoken expired")
    assert "ya29.a0" not in cleaned


def test_private_key_json_field_is_scrubbed():
    cleaned = scrub(json.dumps(FAKE_KEY))
    assert "AAAAFAKEKEY" not in cleaned


def test_scrubbing_applies_to_classified_errors():
    exc = classify_error(Exception(f"timeout using {FAKE_KEY['private_key']}"))
    assert "AAAAFAKEKEY" not in str(exc)
    assert isinstance(exc, GEETransientError)


def test_unknown_errors_default_to_transient():
    """A blip should not abandon a ten-year backfill; the retry cap bounds cost."""
    assert isinstance(classify_error(Exception("something odd")), GEETransientError)


def test_permanent_errors_short_circuit():
    assert isinstance(
        classify_error(Exception("Image.select: no such band 'B9'")),
        GEEPermanentError,
    )


# --- credential mis-paste diagnostics ---------------------------------------


def test_private_key_id_is_recognised_and_named():
    """A 40-char hex fingerprint is `private_key_id`, not the key."""
    s = settings(GEE_PRIVATE_KEY="bd453c60" + "a" * 32)
    with pytest.raises(ValueError, match="private_key_id"):
        s.service_account_key()


def test_pem_block_is_recognised_and_named():
    s = settings(GEE_PRIVATE_KEY=FAKE_KEY["private_key"])
    with pytest.raises(ValueError, match="private_key.*PEM"):
        s.service_account_key()


def test_key_file_path_is_accepted(tmp_path):
    key_file = tmp_path / "sa.json"
    key_file.write_text(json.dumps(FAKE_KEY), encoding="utf-8")
    s = settings(GEE_PRIVATE_KEY=str(key_file))
    assert json.loads(s.service_account_key())["project_id"] == "demo"


def test_missing_json_path_reports_the_path():
    s = settings(GEE_PRIVATE_KEY="C:/nowhere/missing-key.json")
    with pytest.raises(ValueError, match="no file exists"):
        s.service_account_key()


def test_shape_hint_never_echoes_the_value():
    secret = "S3CR3T" + "x" * 60
    s = settings(GEE_PRIVATE_KEY=secret)
    with pytest.raises(ValueError) as exc:
        s.service_account_key()
    assert secret not in str(exc.value)
    assert "S3CR3T" not in str(exc.value)


def test_error_messages_are_ascii_safe():
    """Windows consoles are cp1252; a stray em dash renders as a replacement char."""
    s = settings(GEE_PRIVATE_KEY="ab" * 20)
    with pytest.raises(ValueError) as exc:
        s.service_account_key()
    str(exc.value).encode("ascii")  # raises if any non-ASCII slipped in


# --- project id ---------------------------------------------------------------


def test_numeric_client_id_is_rejected_and_project_derived():
    s = settings(
        GEE_PROJECT_ID="115110891277837331619",
        GEE_SERVICE_ACCOUNT="ibcp-scada@ibcp-scada-506122.iam.gserviceaccount.com",
    )
    assert s.project_id == "ibcp-scada-506122"
    assert "client_id" in s.project_id_warning()


def test_valid_project_id_is_left_alone():
    s = settings(
        GEE_PROJECT_ID="my-real-project",
        GEE_SERVICE_ACCOUNT="a@other-project.iam.gserviceaccount.com",
    )
    assert s.project_id == "my-real-project"
    assert s.project_id_warning() is None


def test_project_derived_when_unset():
    s = settings(GEE_SERVICE_ACCOUNT="a@derived-project.iam.gserviceaccount.com")
    assert s.project_id == "derived-project"


def test_non_service_account_email_yields_no_derivation():
    s = settings(GEE_SERVICE_ACCOUNT="someone@gmail.com")
    assert s.project_from_service_account() is None
