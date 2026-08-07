# packages/backend/app/db/database.py
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.core.config import settings

# pool_pre_ping avoids handing out dead connections after a serverless
# function goes idle; NullPool-style small pools suit serverless better than
# a large persistent pool, but leaving SQLAlchemy defaults is fine for a
# single-instance deployment. Revisit sizing if this runs under high concurrency.
engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

Base = declarative_base()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
