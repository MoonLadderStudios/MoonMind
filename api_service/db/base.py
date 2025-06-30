from __future__ import annotations # Ensure this is at the very top

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from collections.abc import AsyncGenerator # Use collections.abc for Python 3.9+

from moonmind.config.settings import settings

DATABASE_URL = settings.database.POSTGRES_URL

engine = create_async_engine(DATABASE_URL)
async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


from contextlib import asynccontextmanager

async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session

@asynccontextmanager
async def get_async_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for an async database session.
    The caller is responsible for commit/rollback based on operations.
    """
    async with async_session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
