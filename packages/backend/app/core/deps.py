# packages/backend/app/core/deps.py
"""Shared FastAPI dependencies.

The reusable current-user dependency lives here rather than inside the auth
router, so every other router can depend on it without importing endpoint
modules (which would be circular). Its absence was why the flood, soil and
geovision routers had no authentication at all.
"""
from typing import Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.database import get_db
from app.db.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the bearer token to a live, active user row.

    The database lookup is deliberate: a token alone is not proof of a usable
    account, since the user may have been deactivated or deleted after the
    token was issued.
    """
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise CREDENTIALS_EXCEPTION from None

    username = payload.get("sub")
    if not username:
        raise CREDENTIALS_EXCEPTION

    user = await db.scalar(select(User).where(User.username == username))
    if user is None:
        raise CREDENTIALS_EXCEPTION
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )
    return user


def require_roles(*roles: str) -> Callable:
    """Dependency factory gating an endpoint on the caller's role.

    Used for actuation endpoints (barrage gates, drainage pumps), where being
    signed in is not by itself sufficient authority.
    """
    allowed = set(roles)

    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of these roles: {', '.join(sorted(allowed))}",
            )
        return user

    return _check


require_operator = require_roles("admin", "operator")
require_admin = require_roles("admin")
