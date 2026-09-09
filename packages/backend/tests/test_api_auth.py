# packages/backend/tests/test_api_auth.py
"""API-level tests for authentication and authorization.

The database is replaced with an in-memory fake via dependency_overrides, so
these run without Postgres. They cover the security fixes specifically:
privilege escalation on signup, unauthenticated access to control endpoints,
and role gating.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.db.database import get_db
from app.db.models import User
from app.main import app


# --------------------------------------------------------------------------
# Minimal async-session fake: just enough of the SQLAlchemy surface the auth
# router touches. Keeps these tests free of a live database.
# --------------------------------------------------------------------------


class FakeResult:
    def __init__(self, value: Any):
        self._value = value


class FakeSession:
    def __init__(self) -> None:
        self.users: list[User] = []
        self.oauth_states: dict[str, Any] = {}
        self.exchange_codes: dict[str, Any] = {}
        self._pending: list[Any] = []

    # -- query paths used by the auth router --

    async def scalar(self, stmt) -> Optional[User]:
        # Inspect the compiled WHERE clause to decide which column is filtered.
        text = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        for user in self.users:
            if f"= '{user.username}'" in text and "username" in text:
                return user
            if f"= '{user.email}'" in text and "email" in text:
                return user
            if user.google_id and f"= '{user.google_id}'" in text:
                return user
        return None

    async def get(self, model, key):
        if model is User:
            return next((u for u in self.users if u.id == key), None)
        return None

    async def execute(self, stmt):
        return FakeResult(None)

    def add(self, obj) -> None:
        self._pending.append(obj)

    async def commit(self) -> None:
        for obj in self._pending:
            if isinstance(obj, User):
                if obj.id is None:
                    obj.id = str(uuid.uuid4())
                if obj.role is None:
                    obj.role = "user"
                if obj.is_active is None:
                    obj.is_active = True
                if obj.created_at is None:
                    obj.created_at = datetime.now(timezone.utc)
                self.users.append(obj)
        self._pending.clear()

    async def rollback(self) -> None:
        self._pending.clear()

    async def refresh(self, obj) -> None:
        return None

    async def delete(self, obj) -> None:
        if isinstance(obj, User) and obj in self.users:
            self.users.remove(obj)


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def client(session: FakeSession):
    async def _get_db():
        yield session

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def make_user(username="alice", role="user", active=True) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        email=f"{username}@example.com",
        full_name=username.title(),
        hashed_password=None,
        role=role,
        team=None,
        is_active=active,
        created_at=datetime.now(timezone.utc),
    )
    return user


def authenticate_as(user: User) -> None:
    """Bypass token parsing and pin the current user for a test."""
    app.dependency_overrides[get_current_user] = lambda: user


# --------------------------------------------------------------------------
# Privilege escalation
# --------------------------------------------------------------------------


def test_registration_ignores_a_supplied_admin_role(client, session):
    """The original bug: POST role="admin" minted an administrator."""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "attacker",
            "email": "attacker@example.com",
            "password": "correct horse battery",
            "role": "admin",
            "team": "flood",
        },
    )
    assert response.status_code == 201
    created = session.users[-1]
    assert created.role == "user"
    assert created.team is None


def test_registration_rejects_short_password(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "bob", "email": "bob@example.com", "password": "short"},
    )
    assert response.status_code == 422


def test_registration_rejects_password_over_bcrypt_byte_limit(client):
    # 30 four-byte emoji = 120 bytes, but only 30 characters.
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "emoji",
            "email": "emoji@example.com",
            "password": "🔥" * 30,
        },
    )
    assert response.status_code == 422


def test_registration_rejects_invalid_username_characters(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "bad name!",
            "email": "x@example.com",
            "password": "a-good-password",
        },
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Endpoints must require authentication
# --------------------------------------------------------------------------

PROTECTED_GETS = [
    "/api/v1/flood/status",
    "/api/v1/flood/gates",
    "/api/v1/soil/salinity",
    "/api/v1/soil/degradation",
    "/api/v1/geovision/vegetation",
    "/api/v1/geovision/rainfall",
    "/api/v1/geovision/temperature",
    "/api/v1/geovision/coverage",
    "/api/v1/geovision/drought",
    "/api/v1/geovision/predict",
    "/api/v1/auth/me",
]


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_read_endpoints_reject_anonymous_access(client, path):
    assert client.get(path).status_code == 401


CONTROL_POSTS = [
    "/api/v1/flood/gates/1/control?action=open",
    "/api/v1/soil/pumps/1/control?action=start",
]


@pytest.mark.parametrize("path", CONTROL_POSTS)
def test_control_endpoints_reject_anonymous_access(client, path):
    """Barrage gates and drainage pumps were previously wide open."""
    assert client.post(path).status_code == 401


@pytest.mark.parametrize("path", CONTROL_POSTS)
def test_control_endpoints_reject_a_plain_user(client, path):
    authenticate_as(make_user(role="user"))
    assert client.post(path).status_code == 403


@pytest.mark.parametrize("path", CONTROL_POSTS)
def test_control_endpoints_accept_an_operator(client, path):
    authenticate_as(make_user(role="operator"))
    response = client.post(path)
    assert response.status_code == 200
    body = response.json()
    # The stub must not claim it reached hardware.
    assert body["dispatched"] is False


def test_gate_control_rejects_an_unknown_action(client):
    authenticate_as(make_user(role="operator"))
    response = client.post("/api/v1/flood/gates/1/control?action=detonate")
    assert response.status_code == 422


def test_gate_control_rejects_a_non_positive_gate_id(client):
    authenticate_as(make_user(role="operator"))
    assert client.post("/api/v1/flood/gates/0/control?action=open").status_code == 422


def test_role_assignment_requires_admin(client):
    authenticate_as(make_user(role="operator"))
    response = client.put(
        "/api/v1/auth/users/some-id/role", json={"role": "admin"}
    )
    assert response.status_code == 403


def test_role_assignment_rejects_an_unknown_role(client, session):
    admin = make_user(username="root", role="admin")
    target = make_user(username="target")
    session.users.extend([admin, target])
    authenticate_as(admin)
    response = client.put(
        f"/api/v1/auth/users/{target.id}/role", json={"role": "superuser"}
    )
    assert response.status_code == 422


def test_admin_can_assign_a_role(client, session):
    admin = make_user(username="root", role="admin")
    target = make_user(username="target")
    session.users.extend([admin, target])
    authenticate_as(admin)
    response = client.put(
        f"/api/v1/auth/users/{target.id}/role",
        json={"role": "operator", "team": "flood"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "operator"
    assert target.role == "operator"


# --------------------------------------------------------------------------
# Unauthenticated surface that should stay open
# --------------------------------------------------------------------------


def test_health_and_root_stay_public(client):
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200


def test_debug_config_endpoint_is_gone(client):
    """It leaked the deployed OAuth topology to anyone who asked."""
    assert client.get("/api/v1/auth/google/debug-config").status_code == 404


def test_google_login_reports_unconfigured_rather_than_500(client, monkeypatch):
    """Missing OAuth config must be a clean 503, not a 500.

    The settings are patched explicitly rather than relying on the ambient
    environment: a developer with a real .env would otherwise see this test
    behave differently from CI.
    """
    from app.core.config import settings as core_settings

    monkeypatch.setattr(core_settings, "GOOGLE_CLIENT_ID", None)
    monkeypatch.setattr(core_settings, "GOOGLE_CLIENT_SECRET", None)
    response = client.get("/api/v1/auth/google", follow_redirects=False)
    assert response.status_code == 503


def test_google_login_redirects_when_configured(client, monkeypatch):
    """With credentials present the endpoint redirects to Google's consent screen."""
    from app.core.config import settings as core_settings

    monkeypatch.setattr(core_settings, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(core_settings, "GOOGLE_CLIENT_SECRET", "test-client-secret")
    response = client.get("/api/v1/auth/google", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].startswith(
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    # A CSRF state must always be present on the outbound URL.
    assert "state=" in response.headers["location"]
