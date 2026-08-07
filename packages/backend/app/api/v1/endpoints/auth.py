# packages/backend/app/api/v1/endpoints/auth.py
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Depends, status, Request
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    verify_password, get_password_hash, create_access_token,
    ACCESS_TOKEN_EXPIRE_MINUTES
)
from app.db.database import get_db
from app.db.models import ExchangeCode, OAuthState, User

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

# ============================================
# Email/Password Authentication
# ============================================

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    full_name: Optional[str] = None
    role: str = "user"
    team: Optional[str] = None


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
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return create_access_token(
        data={"sub": user.username, "role": user.role, "team": user.team or ""},
        expires_delta=access_token_expires,
    )


@router.post("/register")
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user"""
    if await db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=400, detail="Username already exists")
    if await db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        username=payload.username,
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=get_password_hash(payload.password),
        role=payload.role,
        team=payload.team,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Username or email already exists")
    await db.refresh(user)
    return {"message": "User created successfully", "user_id": user.id}


@router.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    """Login and get access token"""
    user = await db.scalar(select(User).where(User.username == form_data.username))
    if not user or not user.hashed_password or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "access_token": _issue_token(user),
        "token_type": "bearer",
        "user": _public_user(user),
    }


@router.get("/me")
async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    """Get current user info"""
    from jose import JWTError, jwt
    from app.core.security import SECRET_KEY, ALGORITHM

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await db.scalar(select(User).where(User.username == username))
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    return _public_user(user)


@router.post("/logout")
async def logout():
    """Logout (client-side token removal)"""
    return {"message": "Logged out successfully"}

# ============================================
# Google OAuth Authentication
# ============================================

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

_STATE_TTL_SECONDS = 300
_CODE_TTL_SECONDS = 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/google")
async def google_login(db: AsyncSession = Depends(get_db)):
    """Redirect to Google's OAuth consent screen"""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured")

    await db.execute(delete(OAuthState).where(OAuthState.expires_at < _utcnow()))

    state = secrets.token_urlsafe(32)
    db.add(OAuthState(state=state, expires_at=_utcnow() + timedelta(seconds=_STATE_TTL_SECONDS)))
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
    """Handle Google's OAuth redirect: verify state, exchange the code, upsert the user."""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured")

    oauth_error = request.query_params.get("error")
    if oauth_error:
        return RedirectResponse(url=f"{settings.FRONTEND_URL}/auth/callback?error={oauth_error}")

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    state_row = await db.get(OAuthState, state)
    if state_row is None or state_row.expires_at < _utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    await db.delete(state_row)
    await db.commit()

    async with httpx.AsyncClient() as client:
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
            raise HTTPException(status_code=400, detail="Failed to exchange authorization code")
        google_token = token_resp.json()

        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {google_token['access_token']}"},
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch Google profile")
        userinfo = userinfo_resp.json()

    email = userinfo.get("email")
    if not email or not userinfo.get("verified_email", userinfo.get("email_verified")):
        raise HTTPException(status_code=400, detail="Google account has no verified email")

    google_id = userinfo.get("id") or userinfo.get("sub")
    name = userinfo.get("name") or email.split("@")[0]

    existing = await db.scalar(select(User).where(User.email == email))

    if existing and not existing.google_id:
        # An account with this email already exists via password signup —
        # don't silently take it over; the owner must link it explicitly.
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/auth/callback?error=account_exists_use_password"
        )

    if existing:
        user = existing
    else:
        user = User(
            username=f"google_{google_id[:12]}",
            email=email,
            full_name=name,
            hashed_password=None,
            role="user",
            team=None,
            google_id=google_id,
        )
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=409, detail="Account already exists, please retry")
        await db.refresh(user)

    await db.execute(delete(ExchangeCode).where(ExchangeCode.expires_at < _utcnow()))
    exchange_code = secrets.token_urlsafe(32)
    db.add(ExchangeCode(code=exchange_code, user_id=user.id, expires_at=_utcnow() + timedelta(seconds=_CODE_TTL_SECONDS)))
    await db.commit()

    return RedirectResponse(url=f"{settings.FRONTEND_URL}/auth/callback?code={exchange_code}")


class ExchangeRequest(BaseModel):
    code: str


@router.post("/google/exchange")
async def google_exchange(payload: ExchangeRequest, db: AsyncSession = Depends(get_db)):
    """One-time exchange of the short-lived callback code for a real access token."""
    code_row = await db.get(ExchangeCode, payload.code)
    if code_row is None or code_row.expires_at < _utcnow():
        if code_row is not None:
            await db.delete(code_row)
            await db.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    user = await db.get(User, code_row.user_id)
    await db.delete(code_row)
    await db.commit()

    if user is None:
        raise HTTPException(status_code=400, detail="User not found")

    return {
        "access_token": _issue_token(user),
        "token_type": "bearer",
        "user": _public_user(user),
    }
