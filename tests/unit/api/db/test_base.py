import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from api_service.db.base import get_async_session, engine, async_session_maker


@pytest.mark.asyncio
async def test_get_async_session():
    """Test the get_async_session dependency."""
    async for session in get_async_session():
        assert isinstance(session, AsyncSession)
        await session.close()  # Ensure session is closed


def test_engine_not_none():
    """Test that the engine is created."""
    assert engine is not None


def test_async_session_maker_not_none():
    """Test that the async_session_maker is created."""
    assert async_session_maker is not None
