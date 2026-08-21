# packages/backend/app/db/database.py
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool

from app.core.config import settings

# Connection pooling and serverless do not mix.
#
# An asyncpg connection is bound to the event loop that opened it. The engine
# is module-level, so it survives across invocations of a warm serverless
# function — but the event loop does not. A pooled connection handed to a new
# loop fails with "attached to a different loop" or "'NoneType' has no
# attribute 'send'".
#
# NullPool opens a connection per session and closes it afterwards, which is
# the correct trade-off for serverless and for short-lived CLI runs. Set
# DB_POOL_ENABLED=true when running under a long-lived server (uvicorn on a
# VM/container) where pooling is a genuine win.
_POOLING_ENABLED = os.getenv("DB_POOL_ENABLED", "").lower() in {"1", "true", "yes"}

_engine_kwargs: dict = {"echo": False}
if _POOLING_ENABLED:
    # pool_pre_ping avoids handing out a connection the server has already
    # dropped after an idle period.
    _engine_kwargs.update(pool_pre_ping=True, pool_size=5, max_overflow=10)
else:
    _engine_kwargs.update(poolclass=NullPool)

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, expire_on_commit=False, class_=AsyncSession
)

Base = declarative_base()


async def get_db():
    """FastAPI dependency yielding a session, rolled back on error.

    Without the explicit rollback a failed request could return its connection
    to the caller mid-transaction; with NullPool the connection is closed on
    exit either way, but the rollback keeps behaviour identical under pooling.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
