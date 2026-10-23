"""Isolated API process for account-retirement startup/restart evidence.

Use the production startup and mounted application. External Temporal schedule
maintenance is outside this database/access journey and has its own tests.
"""

import os
from contextlib import asynccontextmanager

import uvicorn
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service import main
from api_service.db import base


async def external_schedule_maintenance():
    pass


for name in (
    "ensure_provider_profile_managers_started",
    "ensure_managed_session_reconcile_schedule_started",
    "ensure_managed_runtime_workspace_cleanup_schedule_started",
    "ensure_omnigent_oauth_host_janitor_schedule_started",
    "ensure_recurring_workflow_schedules_reconciled",
):
    setattr(main, name, external_schedule_maintenance)

engine = create_async_engine(
    os.environ["GH4354_DATABASE_URL"],
    connect_args={"server_settings": {"search_path": os.environ["GH4354_SCHEMA"]}},
)
base.async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def lifespan(_app):
    await main.startup_event()
    yield
    await engine.dispose()


main.app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(
        main.app,
        host="127.0.0.1",
        port=int(os.environ["GH4354_PORT"]),
        proxy_headers=False,
        log_level="warning",
    )
