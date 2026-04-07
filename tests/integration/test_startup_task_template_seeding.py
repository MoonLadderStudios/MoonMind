from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload, sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base, TaskStepTemplate, TaskTemplateScopeType
from api_service.main import startup_event

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.asyncio
async def test_startup_seeds_default_task_templates(disabled_env_keys, tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    with (
        patch("api_service.main._initialize_embedding_model"),
        patch("api_service.main._initialize_vector_store"),
        patch("api_service.main._initialize_contexts"),
        patch("api_service.main._load_or_create_vector_index"),
        patch("api_service.main._initialize_oidc_provider"),
    ):
        await startup_event()

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(TaskStepTemplate)
            .where(
                TaskStepTemplate.slug == "speckit-orchestrate",
                TaskStepTemplate.scope_type == TaskTemplateScopeType.GLOBAL,
                TaskStepTemplate.scope_ref.is_(None),
            )
            .options(selectinload(TaskStepTemplate.latest_version))
        )
        template = result.scalar_one_or_none()
        assert template is not None
        assert template.latest_version is not None
        assert template.latest_version.release_status.value == "active"
        assert template.latest_version.steps[1]["skill"]["id"] == "speckit-specify"
