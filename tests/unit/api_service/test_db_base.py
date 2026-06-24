import pytest

from api_service.db.base import create_application_engine


@pytest.mark.asyncio
async def test_create_application_engine_enables_pool_pre_ping() -> None:
    engine = create_application_engine("sqlite+aiosqlite:///:memory:")
    try:
        assert engine.sync_engine.pool._pre_ping is True
    finally:
        await engine.dispose()
