"""Expand real child presets without treating machine authority as a user UUID."""

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.execution_fanout import resolve_execution_request_authority
from api_service.api.routers.executions import (
    _expand_goal_preset_for_workflow_submission,
)
from api_service.db.models import Base, PresetRecent, User
from api_service.services.presets.catalog import PresetCatalogService
from moonmind.config.settings import settings
from moonmind.security.execution_fanout_capabilities import (
    mint_execution_fanout_capability,
    verify_execution_fanout_capability,
)
from moonmind.workflows.executions.runtime_inheritance import (
    SCOPE_CREATE_CHILD,
    SCOPE_INHERIT_RUNTIME,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner_id",
    [
        "system",
        None,
        UUID("d7135e61-a73c-4bfa-b4de-92f6c075d821"),
        "1e946d09-a478-4c5c-8dfe-b67837ab69f2",
    ],
    ids=["system", "no_owner", "uuid", "uuid_string"],
)
async def test_child_preset_expansion_preserves_authority_and_valid_recent_attribution(
    tmp_path: Path, owner_id: str | UUID | None
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/catalog.db")
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        parent = SimpleNamespace(
            owner_type="system" if owner_id in ("system", None) else "user",
            owner_id=owner_id,
        )

        class ParentService:
            async def describe_execution(self, workflow_id: str):
                assert workflow_id == "mm:batch-parent"
                return parent

        secret = str(settings.security.JWT_SECRET_KEY)
        capability = verify_execution_fanout_capability(
            mint_execution_fanout_capability(
                secret=secret,
                parent_workflow_id="mm:batch-parent",
                agent_run_id="batch-agent",
                step_id="batch-step",
                session_id="batch-session",
                runtime_id="codex_cli",
                source_kind="omnigent",
                lifetime_seconds=60,
            ),
            secret=secret,
        )
        authority = await resolve_execution_request_authority(
            user=None, service=ParentService(), capability=capability
        )
        async with maker() as session:
            await PresetCatalogService(session).sync_seed_templates(
                seed_dir=Path(__file__).resolve().parents[3]
                / "api_service/data/presets"
            )
            task = {
                "taskTemplate": {"slug": "pr-review-resolve", "scope": "global"},
                "inputs": {
                    "pull_request": "2754",
                    "review_provider": "none",
                    "finish_with_pr_resolver": True,
                    "max_iterations": 5,
                },
                "git": {"branch": "existing-pr-branch"},
            }
            await _expand_goal_preset_for_workflow_submission(
                task_payload=task,
                request_payload={"repository": "MoonLadderStudios/Tactics"},
                session=session,
                user=authority.user,
            )
            assert task["steps"]
            assert task["taskTemplate"]["slug"] == "pr-review-resolve"
            assert authority.user.id == (owner_id or "system")
            assert authority.principal.workflow_id == "mm:batch-parent"
            assert authority.principal.agent_run_id == "batch-agent"
            assert authority.principal.scopes == frozenset(
                {SCOPE_CREATE_CHILD, SCOPE_INHERIT_RUNTIME}
            )
            assert task["git"]["branch"] == "existing-pr-branch"
            recents = (await session.execute(select(PresetRecent))).scalars().all()
            if owner_id in ("system", None):
                assert recents == []
            else:
                assert [recent.user_id for recent in recents] == [UUID(str(owner_id))]
            assert (await session.execute(select(User))).scalars().all() == []
    finally:
        await engine.dispose()
