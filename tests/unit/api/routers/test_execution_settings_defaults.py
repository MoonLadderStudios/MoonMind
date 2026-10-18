"""Saved execution defaults through the real settings persistence boundary."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers.executions import _saved_default_provider_profile_ref
from api_service.db.models import Base, ManagedAgentProviderProfile, SettingsOverride


@pytest.mark.asyncio
@pytest.mark.parametrize("principal_kind", ["system", "uuid_string", "uuid"])
async def test_saved_profile_default_normalizes_settings_subjects(
    tmp_path, principal_kind
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/settings.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    workspace_id, user_id = uuid4(), uuid4()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            for profile_id in ("workspace-default", "user-default"):
                session.add(
                    ManagedAgentProviderProfile(
                        profile_id=profile_id,
                        runtime_id="codex_cli",
                        provider_id="openai",
                        enabled=True,
                    )
                )
            session.add_all(
                [
                    SettingsOverride(
                        scope="workspace",
                        workspace_id=workspace_id,
                        key="workflow.default_provider_profile_ref",
                        value_json="workspace-default",
                    ),
                    SettingsOverride(
                        scope="user",
                        workspace_id=workspace_id,
                        user_id=user_id,
                        key="workflow.default_provider_profile_ref",
                        value_json="user-default",
                    ),
                ]
            )
            await session.commit()
            user = SimpleNamespace(
                id=(
                    "system"
                    if principal_kind == "system"
                    else str(user_id) if principal_kind == "uuid_string" else user_id
                ),
                workspace_id=(
                    str(workspace_id)
                    if principal_kind == "uuid_string"
                    else workspace_id
                ),
            )
            selected = await _saved_default_provider_profile_ref(
                session=session, user=user, payloads=({},)
            )
            assert selected == (
                "workspace-default" if principal_kind == "system" else "user-default"
            )
    finally:
        await engine.dispose()
