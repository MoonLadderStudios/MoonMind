from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from moonmind.config.settings import settings

DATABASE_URL = settings.database.POSTGRES_URL

engine = create_async_engine(DATABASE_URL)
async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


from contextlib import asynccontextmanager

async def get_async_session() -> AsyncSession:
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
            # Caller should explicitly commit if operations within the 'with' block succeeded
            # For example, by calling session.commit() before the block ends.
            # However, for services that manage their own commits, this outer commit might be unnecessary
            # or could interfere if not handled carefully.
            # For the startup logic, since get_or_create_default_user and get_or_create_profile
            # already commit, an outer commit here is likely not needed.
        except Exception:
            # Rollback might be too broad here if services already rolled back.
            # But if an error happens before service commit, this is useful.
            await session.rollback() # Ensure rollback on error within the context
            raise
        finally:
            await session.close()
