import pytest
from moonmind.schemas.agent_skill_models import (
    SkillSelector,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    AgentSkillProvenance,
)
from moonmind.services.skill_resolution import (
    AgentSkillResolver,
    SkillResolutionContext,
    BuiltInSkillLoader,
    LocalSkillLoader,
    RepoSkillLoader,
    DeploymentSkillLoader,
)


pytestmark = [pytest.mark.asyncio]


async def test_resolver_can_resolve_empty_selector():
    resolver = AgentSkillResolver()
    context = SkillResolutionContext(snapshot_id="snap-123", allow_local_skills=True)
    selector = SkillSelector(include=[])
    
    result = await resolver.resolve(selector, context)
    
    assert result.snapshot_id == "snap-123"
    assert result.skills == []


async def test_resolver_resolves_built_in_skills():
    loader = BuiltInSkillLoader()
    async def mock_load(sel, ctx):
        if any(s.name == "read_file" for s in sel.include):
            return [
                ResolvedSkillEntry(
                    skill_name="read_file",
                    version="1.0",
                    provenance=AgentSkillProvenance(source_kind=AgentSkillSourceKind.BUILT_IN)
                )
            ]
        return []
    loader.load_skills = mock_load

    resolver = AgentSkillResolver(loaders=[loader])
    context = SkillResolutionContext(snapshot_id="snap-123", allow_local_skills=True)
    selector = SkillSelector(
        include=[
            {
                "name": "read_file",
                "version": "1.0",
            }
        ]
    )
    
    result = await resolver.resolve(selector, context)
    
    assert len(result.skills) == 1
    skill = result.skills[0]
    assert skill.skill_name == "read_file"
    assert skill.provenance.source_kind == AgentSkillSourceKind.BUILT_IN
    
async def test_resolver_resolves_local_skills_when_allowed():
    loader = LocalSkillLoader()
    async def mock_load(sel, ctx):
        if not ctx.allow_local_skills:
            return []
        if any(s.name == "my_local_skill" for s in sel.include):
            return [
                ResolvedSkillEntry(
                    skill_name="my_local_skill",
                    provenance=AgentSkillProvenance(source_kind=AgentSkillSourceKind.LOCAL)
                )
            ]
        return []
    loader.load_skills = mock_load

    resolver = AgentSkillResolver(loaders=[loader])
    context = SkillResolutionContext(snapshot_id="snap-123", allow_local_skills=True)
    selector = SkillSelector(
        include=[
            {
                "name": "my_local_skill",
            }
        ]
    )
    
    result = await resolver.resolve(selector, context)
    assert len(result.skills) == 1
    skill = result.skills[0]
    assert skill.skill_name == "my_local_skill"
    assert skill.provenance.source_kind == AgentSkillSourceKind.LOCAL
    
async def test_resolver_filters_local_skills_when_not_allowed():
    resolver = AgentSkillResolver()
    context = SkillResolutionContext(snapshot_id="snap-123", allow_local_skills=False)
    selector = SkillSelector(
        include=[
            {
                "name": "my_local_skill",
            }
        ]
    )
    
    result = await resolver.resolve(selector, context)
    assert len(result.skills) == 0

async def test_resolver_resolves_repo_skills_when_workspace_provided():
    loader = RepoSkillLoader()
    async def mock_load(sel, ctx):
        if not ctx.workspace_root:
            return []
        if any(s.name == "my_repo_skill" for s in sel.include):
            return [
                ResolvedSkillEntry(
                    skill_name="my_repo_skill",
                    provenance=AgentSkillProvenance(source_kind=AgentSkillSourceKind.REPO)
                )
            ]
        return []
    loader.load_skills = mock_load

    resolver = AgentSkillResolver(loaders=[loader])
    context = SkillResolutionContext(
        snapshot_id="snap-123",
        workspace_root="/tmp/workspace",
        allow_local_skills=False
    )
    selector = SkillSelector(
        include=[
            {
                "name": "my_repo_skill",
            }
        ]
    )
    
    result = await resolver.resolve(selector, context)
    assert len(result.skills) == 1
    skill = result.skills[0]
    assert skill.skill_name == "my_repo_skill"
    assert skill.provenance.source_kind == AgentSkillSourceKind.REPO

async def test_resolver_ignores_repo_skills_when_no_workspace():
    loader = RepoSkillLoader()
    async def mock_load(sel, ctx):
        if not ctx.workspace_root:
            return []
        if any(s.name == "my_repo_skill" for s in sel.include):
            return [
                ResolvedSkillEntry(
                    skill_name="my_repo_skill",
                    provenance=AgentSkillProvenance(source_kind=AgentSkillSourceKind.REPO)
                )
            ]
        return []
    loader.load_skills = mock_load

    resolver = AgentSkillResolver(loaders=[loader])
    context = SkillResolutionContext(
        snapshot_id="snap-123", 
        allow_local_skills=False
    )
    selector = SkillSelector(
        include=[
            {
                "name": "my_repo_skill",
            }
        ]
    )
    
    result = await resolver.resolve(selector, context)
    assert len(result.skills) == 0


async def test_repo_skill_loader_scans_fs(tmp_path):
    loader = RepoSkillLoader()
    skills_dir = tmp_path / ".agents" / "skills"
    
    # Create two skills
    skill1 = skills_dir / "skill1"
    skill1.mkdir(parents=True)
    (skill1 / "SKILL.md").touch()
    
    skill2 = skills_dir / "skill2"
    skill2.mkdir(parents=True)
    (skill2 / "SKILL.md").touch()
    
    # Non-skill directory
    skill3 = skills_dir / "not_a_skill"
    skill3.mkdir(parents=True)
    
    context = SkillResolutionContext(snapshot_id="snap", workspace_root=str(tmp_path))
    selector = SkillSelector(include=[{"name": "skill1"}])
    
    results = await loader.load_skills(selector, context)
    assert len(results) == 2
    names = {r.skill_name for r in results}
    assert "skill1" in names
    assert "skill2" in names
    assert "not_a_skill" not in names

async def test_local_skill_loader_scans_fs(tmp_path):
    loader = LocalSkillLoader()
    skills_dir = tmp_path / ".agents" / "skills" / "local"
    
    skill1 = skills_dir / "local1"
    skill1.mkdir(parents=True)
    (skill1 / "SKILL.md").touch()
    
    context = SkillResolutionContext(snapshot_id="snap", workspace_root=str(tmp_path), allow_local_skills=True)
    selector = SkillSelector(include=[])
    
    results = await loader.load_skills(selector, context)
    assert len(results) == 1
    assert results[0].skill_name == "local1"

async def test_deployment_skill_loader_fetches_from_db():
    from unittest.mock import MagicMock, AsyncMock
    loader = DeploymentSkillLoader()
    
    mock_session = AsyncMock()
    
    class MockVersion:
        def __init__(self):
            self.version_string = "1.0.0"
            self.format = MagicMock(value="markdown")
            self.artifact_ref = "art_123"
            self.content_digest = "digest123"
            
    class MockDef:
        def __init__(self):
            self.slug = "db_skill"
            self.versions = [MockVersion()]
    
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [MockDef()]
    mock_session.execute.return_value = mock_result
    
    async def get_session():
        yield mock_session
        
    from contextlib import asynccontextmanager
    
    @asynccontextmanager
    async def mock_maker():
        yield mock_session
        
    context = SkillResolutionContext(snapshot_id="snap", async_session_maker=mock_maker)
    selector = SkillSelector(include=[])
    
    results = await loader.load_skills(selector, context)
    assert len(results) == 1
    assert results[0].skill_name == "db_skill"
    assert results[0].version == "1.0.0"
    assert results[0].provenance.source_kind == AgentSkillSourceKind.DEPLOYMENT

