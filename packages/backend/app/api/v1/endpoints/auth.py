# packages/backend/app/api/v1/endpoints/auth.py
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user, require_admin
from app.core.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    BCRYPT_MAX_BYTES,
    create_access_token,
    get_password_hash,
    verify_password,
)
from app.db.database import get_db
from app.db.models import ExchangeCode, OAuthState, User

logger = logging.getLogger(__name__)

router = APIRouter()

# Roles a user may hold. Assigned by an administrator, never self-selected.
VALID_ROLES = {"admin", "operator", "user", "viewer"}
VALID_TEAMS = {"geovision", "flood", "soil"}
DEFAULT_ROLE = "user"

# ============================================
# Email/Password Authentication
# ============================================


class RegisterRequest(BaseModel):
    """Self-service signup.

    Deliberately has no `role` or `team` field: both are privileges, and
    accepting them from the request body let anyone POST role="admin" and mint
    themselves an administrator. They are set server-side and changed only via
    the admin-only endpoint below.
    """

    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: Optional[str] = Field(default=None, max_length=255)

    @field_validator("password")
    @classmethod
    def _within_bcrypt_limit(cls, v: str) -> str:
        # Enforced in bytes because that is what bcrypt actually limits.
        if len(v.encode("utf-8")) > BCRYPT_MAX_BYTES:
            raise ValueError(
                f"password must be at most {BCRYPT_MAX_BYTES} bytes when "
                "UTF-8 encoded (non-ASCII characters use more than one byte)"
            )
        return v


class RoleAssignmentRequest(BaseModel):
    role: str
    team: Optional[str] = None

    @field_validator("role")
    @classmethod
    def _known_role(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")
        return v

    @field_validator("team")
    @classmethod
    def _known_team(cls, v: Optional[str]) -> Optional[str]:
        if v not in (None, "") and v not in VALID_TEAMS:
            raise ValueError(f"team must be one of: {', '.join(sorted(VALID_TEAMS))}")
        return v or None


def _public_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "team": user.team,
    }


def _issue_token(user: User) -> str:
    return create_access_token(
        data={"sub": user.username, "role": user.role, "team": user.team or ""},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user. Always created with the default (unprivileged) role."""
    if await db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=400, detail="Username already exists")
    if await db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        username=payload.username,
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=get_password_hash(payload.password),
        role=DEFAULT_ROLE,
        team=None,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Lost a race with a concurrent signup on the same username/email.
        await db.rollback()
        raise HTTPException(status_code=400, detail="Username or email already exists")
    await db.refresh(user)
    return {"message": "User created successfully", "user_id": user.id}


@router.post("/token")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Exchange username + password for an access token."""
    user = await db.scalar(select(User).where(User.username == form_data.username))

    if user is None or not user.hashed_password:
        # Hash anyway so a missing user and a wrong password take the same
        # time, denying an attacker a username-enumeration oracle.
        verify_password(form_data.password, _DUMMY_HASH)
        raise _invalid_credentials()

    if not verify_password(form_data.password, user.hashed_password):
        raise _invalid_credentials()

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    return {
        "access_token": _issue_token(user),
        "token_type": "bearer",
        "user": _public_user(user),
    }


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


# Precomputed so the timing-equalising path in /token costs the same as a real
# verification without hashing a fresh value on every failed attempt.
_DUMMY_HASH = get_password_hash("dummy-password-for-constant-time-comparison")


@router.get("/me")
async def read_current_user(user: User = Depends(get_current_user)):
    """Return the signed-in user."""
    return _public_user(user)


@router.post("/logout")
async def logout():
    """Logout is client-side: the client discards the token.

    Kept as an endpoint so the frontend has a single place to hook future
    server-side revocation (a token denylist) without changing its call sites.
    """
    return {"message": "Logged out successfully"}


@router.put("/users/{user_id}/role")
async def assign_role(
    user_id: str,
    payload: RoleAssignmentRequest,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Set a user's role and team. Administrators only."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.role = payload.role
    user.team = payload.team
    await db.commit()
    await db.refresh(user)
    return _public_user(user)


# ============================================
# Google OAuth Authentication
# ============================================

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

_STATE_TTL_SECONDS = 300
_CODE_TTL_SECONDS = 60
_HTTP_TIMEOUT = httpx.Timeout(10.0)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _frontend_redirect(path: str, **params: str) -> RedirectResponse:
    base = settings.FRONTEND_URL.rstrip("/")
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"{base}{path}{query}")


@router.get("/google")
async def google_login(db: AsyncSession = Depends(get_db)):
    """Redirect to Google's OAuth consent screen."""
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured")

    await db.execute(delete(OAuthState).where(OAuthState.expires_at < _utcnow()))

    state = secrets.token_urlsafe(32)
    db.add(
        OAuthState(state=state, expires_at=_utcnow() + timedelta(seconds=_STATE_TTL_SECONDS))
    )
    await db.commit()

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        "state": state,
    }
    return RedirectResponse(url=f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/google/callback")
async def google_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle Google's redirect: verify state, exchange the code, upsert the user."""
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured")

    if oauth_error := request.query_params.get("error"):
        # Google's own error string is echoed back to the frontend, not raised,
        # so the user lands on a page rather than a JSON 400.
        return _frontend_redirect("/auth/callback/", error=oauth_error)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return _frontend_redirect("/auth/callback/", error="missing_code_or_state")

    state_row = await db.get(OAuthState, state)
    if state_row is None or state_row.expires_at < _utcnow():
        return _frontend_redirect("/auth/callback/", error="invalid_state")
    # Consumed immediately: a state value must never be replayable.
    await db.delete(state_row)
    await db.commit()

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            token_resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )
            if token_resp.status_code != 200:
                logger.warning("Google token exchange failed: %s", token_resp.status_code)
                return _frontend_redirect("/auth/callback/", error="token_exchange_failed")

            access_token = token_resp.json().get("access_token")
            if not access_token:
                return _frontend_redirect("/auth/callback/", error="token_exchange_failed")

            userinfo_resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if userinfo_resp.status_code != 200:
                return _frontend_redirect("/auth/callback/", error="profile_fetch_failed")
            userinfo = userinfo_resp.json()
    except httpx.HTTPError:
        logger.exception("Network failure talking to Google")
        return _frontend_redirect("/auth/callback/", error="google_unreachable")

    email = userinfo.get("email")
    verified = userinfo.get("verified_email", userinfo.get("email_verified"))
    if not email or not verified:
        return _frontend_redirect("/auth/callback/", error="email_not_verified")

    google_id = userinfo.get("id") or userinfo.get("sub")
    if not google_id:
        return _frontend_redirect("/auth/callback/", error="missing_google_id")

    existing = await db.scalar(select(User).where(User.email == email))

    if existing and not existing.google_id:
        # A password account already owns this address. Linking it silently
        # would let anyone controlling the Google address take it over.
        return _frontend_redirect(
            "/auth/callback/", error="account_exists_use_password"
        )

    if existing:
        user = existing
    else:
        user = await _create_google_user(db, google_id, email, userinfo.get("name"))
        if user is None:
            return _frontend_redirect("/auth/callback/", error="account_creation_failed")

    await db.execute(delete(ExchangeCode).where(ExchangeCode.expires_at < _utcnow()))
    exchange_code = secrets.token_urlsafe(32)
    db.add(
        ExchangeCode(
            code=exchange_code,
            user_id=user.id,
            expires_at=_utcnow() + timedelta(seconds=_CODE_TTL_SECONDS),
        )
    )
    await db.commit()

    return _frontend_redirect("/auth/callback/", code=exchange_code)


async def _create_google_user(
    db: AsyncSession, google_id: str, email: str, name: Optional[str]
) -> Optional[User]:
    """Create a Google-backed account, retrying on username collision.

    The username is derived from the Google id, so two ids sharing a prefix
    would collide forever if the first attempt were the only one — the previous
    implementation told the user to "retry", which regenerated the identical
    name and failed identically every time.
    """
    base = f"google_{google_id[:12]}"
    for attempt in range(5):
        username = base if attempt == 0 else f"{base}_{secrets.token_hex(3)}"
        user = User(
            username=username,
            email=email,
            full_name=name or email.split("@")[0],
            hashed_password=None,
            role=DEFAULT_ROLE,
            team=None,
            google_id=google_id,
        )
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            # Someone else may have created the account for this google_id or
            # email in the meantime; prefer that row over a new one.
            claimed = await db.scalar(select(User).where(User.google_id == google_id))
            if claimed is not None:
                return claimed
            continue
        await db.refresh(user)
        return user

    logger.error("Could not allocate a username for google_id prefix %s", base)
    return None


class ExchangeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


@router.post("/google/exchange")
async def google_exchange(payload: ExchangeRequest, db: AsyncSession = Depends(get_db)):
    """One-time exchange of the short-lived callback code for an access token."""
    code_row = await db.get(ExchangeCode, payload.code)
    if code_row is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    expired = code_row.expires_at < _utcnow()
    user_id = code_row.user_id
    # Consumed whether or not it was still valid, so a code is never reusable.
    await db.delete(code_row)
    await db.commit()

    if expired:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="Account is unavailable")

    return {
        "access_token": _issue_token(user),
        "token_type": "bearer",
        "user": _public_user(user),
    }
