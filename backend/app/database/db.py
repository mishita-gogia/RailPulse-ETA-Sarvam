"""
Async SQLAlchemy database setup.

RETAINED EXCLUSIVELY FOR HISTORICAL MIGRATION TOOLING AND TEST COMPATIBILITY.
No production runtime code imports or uses this module.
"""

import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./railpulse.db"
)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 30} if "sqlite" in DATABASE_URL else {},
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db():
    """Initialize database tables for migration scripts and test runners."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
