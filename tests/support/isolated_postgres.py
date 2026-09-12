"""Per-test PostgreSQL schemas on an explicitly selected disposable server."""

import os
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@asynccontextmanager
async def isolated_postgres(tables):
    url = os.environ.get("MOONMIND_TEST_POSTGRES_URL")
    if not url:
        raise RuntimeError(
            "MOONMIND_TEST_POSTGRES_URL must identify the disposable qualification PostgreSQL server"
        )
    url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    schema = "reliability_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            for table in tables:
                await conn.run_sync(
                    lambda connection, table=table: table.create(connection)
                )
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
