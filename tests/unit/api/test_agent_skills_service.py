"""Unit tests for the AgentSkillsService."""

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import AgentSkillFormat, Base
from api_service.services.agent_skills_service import (
    AgentSkillsService,
    AgentSkillDuplicateError,
    AgentSkillNotFoundError,
)
from moonmind.workflows.temporal import TemporalArtifactService

@pytest.fixture
def mock_artifact_service() -> AsyncMock:
    svc = AsyncMock(spec=TemporalArtifactService)
    
    mock_artifact = MagicMock()
    mock_artifact.artifact_id = "test-artifact-id"
    mock_artifact.sha256 = "sha256:dummy"
    mock_artifact.size_bytes = 100
    mock_artifact.content_type = "text/markdown"
    mock_artifact.encryption.value = "none"
    
    # Mock create to return a dummy artifact and None upload
    svc.create.return_value = (mock_artifact, None)
    
    # Mock write_complete to return the same artifact
    svc.write_complete.return_value = mock_artifact
    
    return svc

@asynccontextmanager
async def template_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/agent_skills.db"
    engine = create_async_engine(db_url, future=True)
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield async_session_maker
    finally:
        await engine.dispose()

@pytest.mark.asyncio
async def test_create_skill_success(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as db_session:
            svc = AgentSkillsService(session=db_session)
            skill = await svc.create_skill(slug="test-skill", title="Test Skill", description="A test skill.")
            assert skill.slug == "test-skill"
            assert skill.title == "Test Skill"
            assert skill.id is not None

            # Retrieve it
            fetched = await svc.get_skill("test-skill")
            assert fetched is not None
            assert fetched.slug == "test-skill"

@pytest.mark.asyncio
async def test_create_skill_duplicate_slug(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as db_session:
            svc = AgentSkillsService(session=db_session)
            await svc.create_skill(slug="duplicate-skill", title="First Option")
            
            with pytest.raises(AgentSkillDuplicateError):
                await svc.create_skill(slug="duplicate-skill", title="Second Option")

@pytest.mark.asyncio
async def test_require_skill_not_found(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as db_session:
            svc = AgentSkillsService(session=db_session)
            with pytest.raises(AgentSkillNotFoundError):
                await svc.require_skill("nonexistent")

@pytest.mark.asyncio
async def test_update_skill_content_success(tmp_path, mock_artifact_service: AsyncMock):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as db_session:
            svc = AgentSkillsService(session=db_session, artifact_service=mock_artifact_service)
            skill = await svc.create_skill(slug="docs-skill", title="Docs")

            updated = await svc.update_skill_content(
                skill_slug="docs-skill",
                content="some markdown content",
            )

            assert updated.id == skill.id
            assert updated.format == AgentSkillFormat.MARKDOWN
            assert updated.artifact_ref == "test-artifact-id"
            assert updated.content_digest.startswith("sha256:")

            # Verify that the artifact service was awaited
            mock_artifact_service.create.assert_awaited_once()
            mock_artifact_service.write_complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_skill_content_persists_input_contract_metadata(
    tmp_path, mock_artifact_service: AsyncMock
):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as db_session:
            svc = AgentSkillsService(
                session=db_session,
                artifact_service=mock_artifact_service,
            )
            await svc.create_skill(slug="issue-skill", title="Issue Skill")

            await svc.update_skill_content(
                skill_slug="issue-skill",
                content=(
                    "---\n"
                    "name: Issue Skill\n"
                    "inputSchema:\n"
                    "  type: object\n"
                    "  required: [issue_key]\n"
                    "  properties:\n"
                    "    issue_key:\n"
                    "      type: string\n"
                    "      title: Issue key\n"
                    "      allOf:\n"
                    "        - minLength: 3\n"
                    "uiSchema:\n"
                    "  issue_key:\n"
                    "    widget: jira.issue-picker\n"
                    "defaults:\n"
                    "  issue_key: MM-1047\n"
                    "---\n"
                    "# Issue Skill\n"
                ),
            )

            metadata = mock_artifact_service.create.await_args.kwargs["metadata_json"]
            assert metadata["skill_slug"] == "issue-skill"
            assert metadata["input_schema"]["required"] == ["issue_key"]
            assert metadata["ui_schema"]["issue_key"]["widget"] == "jira.issue-picker"
            assert metadata["defaults"] == {"issue_key": "MM-1047"}
            assert metadata["input_contract_digest"].startswith("sha256:")
            assert metadata["content_digest"].startswith("sha256:")
            assert metadata["input_schema_diagnostics"][0]["code"] == (
                "unsupported_keyword"
            )

@pytest.mark.asyncio
async def test_update_skill_content_replaces_current_content(
    tmp_path, mock_artifact_service: AsyncMock
):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as db_session:
            svc = AgentSkillsService(
                session=db_session, artifact_service=mock_artifact_service
            )
            await svc.create_skill(slug="docs-skill", title="Docs")

            first = await svc.update_skill_content(
                skill_slug="docs-skill",
                content="first markdown content",
            )
            first_digest = first.content_digest

            second = await svc.update_skill_content(
                skill_slug="docs-skill",
                content="second markdown content",
            )

            fetched = await svc.require_skill("docs-skill")

            assert second.id == first.id
            assert fetched.artifact_ref == second.artifact_ref
            assert fetched.content_digest == second.content_digest
            assert fetched.content_digest != first_digest

@pytest.mark.asyncio
async def test_update_skill_content_rejects_invalid_format(tmp_path, mock_artifact_service: AsyncMock):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as db_session:
            svc = AgentSkillsService(session=db_session, artifact_service=mock_artifact_service)
            await svc.create_skill(slug="docs-skill", title="Docs")

            with pytest.raises(ValueError, match="Invalid format"):
                await svc.update_skill_content(
                    skill_slug="docs-skill",
                    content="second",
                    format_str="invalid",
                )

@pytest.mark.asyncio
async def test_create_skill_set(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as db_session:
            svc = AgentSkillsService(session=db_session)
            skill_set = await svc.create_skill_set(slug="test-set", title="Test Set")
            
            assert skill_set.slug == "test-set"
            assert skill_set.title == "Test Set"

            with pytest.raises(AgentSkillDuplicateError):
                await svc.create_skill_set(slug="test-set", title="Duplicate")
