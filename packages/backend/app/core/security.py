# packages/backend/app/core/security.py
"""Password hashing and JWT issue/verify.

Uses bcrypt and PyJWT directly. passlib was dropped because its final release
(1.7.4, 2020) reads `bcrypt.__about__.__version__`, which bcrypt removed in
4.1 — the two cannot both be current. python-jose was dropped for PyJWT
because it is effectively unmaintained and 3.3.0 carries
CVE-2024-33663/33664.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
import jwt
from pydantic import BaseModel

from app.core.config import settings

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

# bcrypt hashes at most 72 *bytes* and raises on anything longer. The limit is
# in bytes, not characters, so a 40-character password containing Urdu or
# emoji can exceed it.
BCRYPT_MAX_BYTES = 72
BCRYPT_ROUNDS = 12


class TokenData(BaseModel):
    username: Optional[str] = None
    role: Optional[str] = None


def _prepare(password: str) -> bytes:
    """Encode to UTF-8 and clamp to bcrypt's 72-byte ceiling.

    Truncation happens on a character boundary so the stored input is always
    valid UTF-8 — dropping a partial multi-byte sequence would still hash
    deterministically, but makes the value impossible to reason about.
    """
    encoded = password.encode("utf-8")
    if len(encoded) <= BCRYPT_MAX_BYTES:
        return encoded
    truncated = encoded[:BCRYPT_MAX_BYTES]
    while truncated:
        try:
            truncated.decode("utf-8")
            break
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return truncated


def get_password_hash(password: str) -> str:
    hashed = bcrypt.hashpw(_prepare(password), bcrypt.gensalt(rounds=BCRYPT_ROUNDS))
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False
    try:
        return bcrypt.checkpw(_prepare(plain_password), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed or non-bcrypt hash in the column: treat as a failed login
        # rather than a 500.
        return False


def create_access_token(
    data: dict, expires_delta: Optional[timedelta] = None
) -> str:
    to_encode = data.copy()
    # timezone-aware: datetime.utcnow() is deprecated in 3.12+ and returns a
    # naive value that silently misbehaves near DST/UTC boundaries.
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify a token. Raises jwt.PyJWTError on any problem.

    Signature, expiry and algorithm are all verified; `algorithms` is pinned to
    a single value so a token cannot request `none` or a weaker algorithm.
    """
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
