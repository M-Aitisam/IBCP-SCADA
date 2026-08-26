# packages/backend/tests/test_ingestion_control.py
"""Ingestion trigger/dispatch behaviour.

No network and no GitHub: the HTTP client is stubbed, so these assert the
contract this module promises rather than GitHub's availability.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services import ingestion_control as ic


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class FakeAsyncClient:
    """Stands in for httpx.AsyncClient, recording what would have been sent."""

    last_request: dict = {}

    def __init__(self, status_code: int = 204, raises: Exception | None = None):
        self._status = status_code
        self._raises = raises

    def __call__(self, *args, **kwargs):  # used as the AsyncClient factory
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        if self._raises is not None:
            raise self._raises
        type(self).last_request = {"url": url, "json": json, "headers": headers}
        return FakeResponse(self._status)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(ic.settings, "GITHUB_REPOSITORY", "acme/ibcp-scada")
    monkeypatch.setattr(ic.settings, "GITHUB_DISPATCH_TOKEN", "ghp_secret_value")
    monkeypatch.setattr(ic.settings, "GITHUB_WORKFLOW_FILE", "gee-daily-ingestion.yml")
    monkeypatch.setattr(ic.settings, "GITHUB_WORKFLOW_REF", "main")


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.setattr(ic.settings, "GITHUB_REPOSITORY", None)
    monkeypatch.setattr(ic.settings, "GITHUB_DISPATCH_TOKEN", None)


def _install_client(monkeypatch, client: FakeAsyncClient) -> None:
    monkeypatch.setattr(ic.httpx, "AsyncClient", client)


# --- dataset validation -----------------------------------------------------


def test_unknown_dataset_is_rejected_before_dispatch():
    """A typo must fail loudly rather than silently ingesting everything."""
    with pytest.raises(ic.IngestionRequestError) as exc:
        ic.validate_datasets(["chirps", "sentinal2"])
    assert "sentinal2" in str(exc.value)
    # Subclass of the general error, so existing handlers still catch it, but
    # distinguishable so the API can answer 400 rather than 502.
    assert isinstance(exc.value, ic.IngestionControlError)


def test_known_datasets_pass_through():
    assert ic.validate_datasets(["chirps", "sentinel2"]) == ["chirps", "sentinel2"]
    assert ic.validate_datasets(None) == []


# --- the not-configured path ------------------------------------------------


@pytest.mark.asyncio
async def test_missing_dispatch_config_returns_the_cli_command(unconfigured):
    """Not configured is a reportable state, not an error.

    Running the CLI directly is a legitimate way to operate this pipeline, so
    the response hands back the exact command instead of a bare failure.
    """
    result = await ic.dispatch_workflow("daily", datasets=["chirps"])

    assert result.accepted is False
    assert "not configured" in result.detail.lower()
    assert result.cli_command == "python -m app.ingestion.cli daily --dataset chirps"


@pytest.mark.asyncio
async def test_backfill_cli_command_carries_dates_as_env_overrides(unconfigured):
    """The CLI has no --start flag; dates are environment overrides."""
    result = await ic.dispatch_workflow(
        "backfill", start=date(2016, 1, 1), end=date(2026, 8, 19), dry_run=True
    )
    assert result.cli_command == (
        "GEE_HISTORICAL_START=2016-01-01 GEE_TARGET_END=2026-08-19 "
        "python -m app.ingestion.cli backfill --dry-run"
    )


# --- successful dispatch ----------------------------------------------------


@pytest.mark.asyncio
async def test_successful_dispatch_sends_the_expected_workflow_inputs(
    configured, monkeypatch
):
    _install_client(monkeypatch, FakeAsyncClient(204))

    result = await ic.dispatch_workflow(
        "backfill",
        datasets=["chirps", "mod11a2"],
        start=date(2016, 1, 1),
        end=date(2026, 8, 19),
    )

    assert result.accepted is True
    sent = FakeAsyncClient.last_request
    assert sent["url"].endswith(
        "/repos/acme/ibcp-scada/actions/workflows/gee-daily-ingestion.yml/dispatches"
    )
    assert sent["json"]["ref"] == "main"
    inputs = sent["json"]["inputs"]
    assert inputs["mode"] == "backfill"
    # Several datasets travel as one comma list; the workflow splits them.
    assert inputs["dataset"] == "chirps,mod11a2"
    assert inputs["start_date"] == "2016-01-01"
    assert inputs["end_date"] == "2026-08-19"
    # workflow_dispatch inputs are strings on the wire whatever their type.
    assert inputs["dry_run"] == "false"


@pytest.mark.asyncio
async def test_single_dataset_is_sent_unwrapped(configured, monkeypatch):
    """The workflow's `dataset` is a choice field, so one name must stay bare."""
    _install_client(monkeypatch, FakeAsyncClient(204))
    await ic.dispatch_workflow("daily", datasets=["sentinel2"])
    assert FakeAsyncClient.last_request["json"]["inputs"]["dataset"] == "sentinel2"


# --- failures ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_mode_is_not_dispatchable(configured):
    """`test-run` is a developer aid and must not be triggerable from the UI."""
    with pytest.raises(ic.IngestionRequestError):
        await ic.dispatch_workflow("test")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [
        (401, "token"),
        (403, "token"),
        (404, "not found"),
        (422, "inputs"),
        (500, "500"),
    ],
)
async def test_github_errors_are_translated_to_actionable_messages(
    configured, monkeypatch, status, expected
):
    _install_client(monkeypatch, FakeAsyncClient(status))
    with pytest.raises(ic.IngestionControlError) as exc:
        await ic.dispatch_workflow("daily")
    assert expected in str(exc.value).lower()


@pytest.mark.asyncio
async def test_transport_failure_does_not_leak_the_token(configured, monkeypatch, caplog):
    """A network error can carry request headers; the token must never surface."""
    _install_client(
        monkeypatch, FakeAsyncClient(raises=ic.httpx.ConnectError("ghp_secret_value leaked"))
    )
    with caplog.at_level("ERROR"):
        with pytest.raises(ic.IngestionControlError) as exc:
            await ic.dispatch_workflow("daily")

    assert "ghp_secret_value" not in str(exc.value)
    assert "ghp_secret_value" not in caplog.text
    assert "ConnectError" in str(exc.value)
