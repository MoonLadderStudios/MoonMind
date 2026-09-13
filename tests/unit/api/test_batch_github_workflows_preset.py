"""Catalog-boundary tests for the Batch GitHub Workflows seed preset."""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.presets.catalog import PresetCatalogService

pytestmark = [pytest.mark.asyncio]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRESET_PATH = (
    _REPO_ROOT / "api_service/data/presets/batch-github-workflows.yaml"
)


@asynccontextmanager
async def _catalog_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/batch_github_workflows.db",
        future=True,
    )
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


def _seed_dir(tmp_path) -> Path:
    seed_dir = tmp_path / "presets"
    seed_dir.mkdir()
    shutil.copy(_PRESET_PATH, seed_dir / _PRESET_PATH.name)
    return seed_dir


async def test_batch_github_workflows_seed_and_expansion_contract(tmp_path):
    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            expanded = await service.expand_template(
                slug="batch-github-workflows",
                scope="global",
                scope_ref=None,
                inputs={
                    "issue_range": "3142-3150",
                    "run_ref": "preset:github-issue-orchestrate",
                    "repository": "MoonLadderStudios/MoonMind",
                    "publish_mode": "pr_with_merge_automation",
                    "max_workflows": "10",
                    "run_verify": True,
                    "constraints": "Be safe",
                },
            )

    assert expanded["appliedTemplate"]["slug"] == "batch-github-workflows"
    assert expanded["publish"] == {"mode": "none"}
    assert sorted(expanded["capabilities"]) == ["gh", "git"]
    assert len(expanded["steps"]) == 1
    step = expanded["steps"][0]
    assert step["skill"]["requiredCapabilities"] == ["git", "gh"]
    orchestration = step["batchOrchestration"]
    assert orchestration["source"] == {
        "kind": "github_issue_range",
        "githubIssueRange": {
            "range": "3142-3150",
            "repository": "MoonLadderStudios/MoonMind",
        },
    }
    assert orchestration["target"]["runRef"] == (
        "preset:github-issue-orchestrate"
    )
    assert orchestration["publish"]["mode"] == "pr_with_merge_automation"
    assert orchestration["runtime"]["inherit"] == "caller"
    # Executable fan-out: preset prose invokes the resolved Skill entrypoint
    # with data-safe arguments (fixed --constraints-file path, never
    # interpolated constraints value).
    assert 'run_verify="True"' in step["instructions"]
    assert '"3142-3150"' in step["instructions"]
    assert '"MoonLadderStudios/MoonMind"' in step["instructions"]
    assert "only for open Issue objects returned by GitHub" in step["instructions"]
    assert "$MOONMIND_ACTIVE_SKILLS_DIR" in step["instructions"]
    assert "python3" in step["instructions"]
    assert "--github-issue-range" in step["instructions"]
    assert "--github-repository" in step["instructions"]
    assert "--repository-connection-ref" in step["instructions"]
    assert "--run-ref" in step["instructions"]
    assert "--publish-mode" in step["instructions"]
    assert "--max-workflows" in step["instructions"]
    assert "batch-github-workflows/bin/batch_workflows.py" in step["instructions"]
    assert '--constraints "' not in step["instructions"]
    assert "--constraints-file" in step["instructions"]
    assert "artifacts/batch-workflows-constraints.txt" in step["instructions"]
    assert orchestration["sharedInputs"]["constraints"] == "Be safe"
    assert expanded["appliedTemplate"]["inputs"]["constraints"] == "Be safe"


async def test_batch_github_workflows_exposes_constraints_input(tmp_path):
    """Operator guidance travels as a distinct `constraints` input (REQ-03).

    The preset must not ask the agent to separate operator-added text from
    template prose inside the instructions box; constraints arrive as data
    through the input contract, sharedInputs, and child bindings.
    """

    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            template = await service.get_template(
                slug="batch-github-workflows",
                scope="global",
                scope_ref=None,
            )

            expanded = await service.expand_template(
                slug="batch-github-workflows",
                scope="global",
                scope_ref=None,
                inputs={
                    "issue_range": "3142-3150",
                    "run_ref": "preset:github-issue-implement",
                    "repository": "MoonLadderStudios/MoonMind",
                    "constraints": "Stale client value",
                },
            )

    assert "constraints" in template["inputSchema"]["properties"]
    assert template["uiSchema"]["constraints"] == {
        "widget": "textarea",
        "advanced": True,
    }
    assert template["defaults"]["constraints"] == ""
    assert any(
        definition["name"] == "constraints" for definition in template["inputs"]
    )
    for binding in template["annotations"]["bindings"].values():
        assert binding["constraints"] == "{{ shared.constraints }}"

    assert (
        expanded["appliedTemplate"]["inputs"]["constraints"]
        == "Stale client value"
    )
    step = expanded["steps"][0]
    assert '--constraints "' not in step["instructions"]
    assert "--constraints-file" in step["instructions"]
    assert "python3" in step["instructions"]
    assert "artifacts/batch-workflows-constraints.txt" in step["instructions"]
    # The arbitrary value travels as orchestration data, never on a shell
    # command line: only the fixed file path is named in prose.
    assert (
        step["batchOrchestration"]["sharedInputs"]["constraints"]
        == "Stale client value"
    )
    assert "Copy only that added guidance" not in step["instructions"]
    assert "never this template text" not in step["instructions"]


async def test_batch_github_workflows_materializes_constraints_via_file(tmp_path):
    """Constraints travel as a distinct input written via file, not shell."""

    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            expanded = await service.expand_template(
                slug="batch-github-workflows",
                scope="global",
                scope_ref=None,
                inputs={
                    "issue_range": "3142-3150",
                    "run_ref": "preset:github-issue-implement",
                    "repository": "MoonLadderStudios/MoonMind",
                },
            )

    instructions = expanded["steps"][0]["instructions"]
    assert "--constraints-file" in instructions
    assert "python3" in instructions
    assert "batch-github-workflows/bin/batch_workflows.py" in instructions
    assert "artifacts/batch-workflows-constraints.txt" in instructions
    assert "never via" in instructions
    assert "shell" in instructions
    assert "never forward this template text" in instructions
    assert "This preset has no separate constraints input" not in instructions
    assert "Copy only that added guidance" not in instructions


async def test_batch_github_workflows_uses_repository_context(tmp_path):
    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            expanded = await service.expand_template(
                slug="batch-github-workflows",
                scope="global",
                scope_ref=None,
                inputs={
                    "issue_range": "3142-3150",
                    "run_ref": "preset:github-issue-implement",
                },
                context={"repository": "MoonLadderStudios/MoonMind"},
            )

    source = expanded["steps"][0]["batchOrchestration"]["source"]
    assert source["githubIssueRange"]["repository"] == (
        "MoonLadderStudios/MoonMind"
    )
    assert expanded["steps"][0]["skill"]["id"] == "batch-github-workflows"


async def test_batch_github_workflows_defaults_child_publish_to_pr(tmp_path):
    """Both run options implement code, so children publish a PR by default.

    A default batch run previously queued an implementation child per issue
    with ``publish_mode = none``, which implemented every issue and then left
    the work unpublished.
    """

    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            template = await service.get_template(
                slug="batch-github-workflows",
                scope="global",
                scope_ref=None,
            )

            expanded = await service.expand_template(
                slug="batch-github-workflows",
                scope="global",
                scope_ref=None,
                inputs={
                    "issue_range": "3142-3150",
                    "repository": "MoonLadderStudios/MoonMind",
                },
            )

    assert template["defaults"]["publish_mode"] == "pr"
    assert template["defaults"]["run_ref"] == "preset:github-issue-implement"
    step = expanded["steps"][0]
    assert step["batchOrchestration"]["target"]["runRef"] == (
        "preset:github-issue-implement"
    )
    assert step["batchOrchestration"]["publish"]["mode"] == "pr"
    assert '"pr"' in step["instructions"]
    assert "--publish-mode" in step["instructions"]
    assert "python3" in step["instructions"]
    # The parent queues children and writes summary artifacts; it never
    # publishes repository changes itself.
    assert expanded["publish"] == {"mode": "none"}
