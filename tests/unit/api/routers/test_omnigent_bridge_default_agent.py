"""Launch-boundary durable default agent resolution (MoonLadderStudios/MoonMind#3517 §8)."""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import api_service.api.routers.omnigent_bridge as bridge
from api_service.api.routers.omnigent_agent_profiles import _digest
from api_service.db.models import (
    Base,
    OmnigentAgentProfile,
    OmnigentAgentProfileVersion,
)
from api_service.services.omnigent_agent_bootstrap_service import (
    build_bootstrap_document,
)

pytestmark = [pytest.mark.asyncio]


@pytest_asyncio.fixture()
async def session_maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _add_default(maker, *, state: str, active_version: int | None):
    document = build_bootstrap_document("codex-prod")
    async with maker() as session:
        session.add_all(
            [
                OmnigentAgentProfile(
                    profile_id="codex-team",
                    display_name="Codex Team",
                    visibility="workspace",
                    state=state,
                    active_version=active_version,
                    default_for_runtime=True,
                ),
                OmnigentAgentProfileVersion(
                    profile_id="codex-team",
                    version=1,
                    digest=_digest(document),
                    document=document,
                    upstream_snapshot={"id": "codex-prod", "name": "Codex Prod"},
                ),
            ]
        )
        await session.commit()


async def test_launch_prefers_durable_active_default(session_maker, monkeypatch):
    monkeypatch.delenv("OMNIGENT_DEFAULT_AGENT_NAME", raising=False)
    await _add_default(session_maker, state="active", active_version=1)
    async with session_maker() as session:
        resolved = await bridge._get_launch_default_agent_selection(session)
    assert resolved.agent_id == "codex-prod"
    assert resolved.agent_name is None


async def test_launch_uses_env_fallback_when_no_durable_default(
    session_maker, monkeypatch
):
    monkeypatch.setenv("OMNIGENT_DEFAULT_AGENT_NAME", "codex-env")
    async with session_maker() as session:
        resolved = await bridge._get_launch_default_agent_selection(session)
    assert resolved.agent_id is None
    assert resolved.agent_name == "codex-env"


async def test_launch_fails_closed_on_conflicting_default(session_maker, monkeypatch):
    monkeypatch.setenv("OMNIGENT_DEFAULT_AGENT_NAME", "codex-env")
    await _add_default(session_maker, state="draft", active_version=None)
    async with session_maker() as session:
        with pytest.raises(HTTPException) as exc_info:
            await bridge._get_launch_default_agent_selection(session)
    assert exc_info.value.status_code == 409
