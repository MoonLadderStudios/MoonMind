"""Catalog-boundary tests for the seed presets' workflow-level publish policy.

`workflowPublish` decides which seed presets carry MoonMind-managed repository
publication and which publish nothing at all. Getting it wrong is silent in
both directions: a parent that only queues children or writes local artifacts
would be pushed through the managed publisher, and an implementation preset
that lost its managed policy would finish without publishing its work. These
tests pin the classification at the real seed + expansion boundary.
"""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, Preset, PresetScopeType
from api_service.services.presets.catalog import (
    PresetCatalogService,
    PresetValidationError,
)

pytestmark = [pytest.mark.asyncio]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRESET_DIR = _REPO_ROOT / "api_service" / "data" / "presets"

# Presets that publish nothing to the repository. Parent orchestrators own only
# child workflows or external tracker side effects; assessment presets are
# repository reads that write local handoff artifacts.
_PUBLISH_NOTHING_PRESETS = frozenset(
    {
        "batch-github-workflows",
        "batch-workflows",
        "document-update-orchestrate",
        "github-issue-breakdown-implement",
        "github-issue-breakdown-orchestrate",
        "issue-implement-assessment",
        "jira-breakdown",
        "jira-breakdown-implement",
        "jira-breakdown-orchestrate",
        "pr-review-resolve",
    }
)

# Presets that edit the repository and hand publication to the MoonMind-managed
# publisher. None of them declares agent-owned publication, so none may default
# to `auto`: that would require execution-bound publish evidence on every
# terminal path, including no-change and blocked runs.
_MANAGED_PUBLISH_PRESETS = frozenset(
    {
        "document-author",
        "document-health-update",
        "github-issue-implement",
        "github-issue-orchestrate",
        "issue-implement-work-pr",
        "jira-implement",
        "jira-orchestrate",
        "moonspec-orchestrate",
    }
)


@asynccontextmanager
async def _catalog_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/preset_publish_policy.db",
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
    shutil.copytree(_PRESET_DIR, seed_dir)
    return seed_dir


async def _seeded_annotations(session, tmp_path) -> dict[str, dict]:
    service = PresetCatalogService(session)
    await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
    result = await session.execute(
        select(Preset).where(
            Preset.scope_type == PresetScopeType.GLOBAL,
            Preset.scope_ref.is_(None),
        )
    )
    return {
        template.slug: template.annotations or {}
        for template in result.scalars().all()
    }


async def test_seed_presets_declare_the_expected_publish_policy(tmp_path):
    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            annotations_by_slug = await _seeded_annotations(session, tmp_path)

    # Every seed preset is classified: a new one must make a deliberate choice.
    assert set(annotations_by_slug) == (
        _PUBLISH_NOTHING_PRESETS | _MANAGED_PUBLISH_PRESETS
    )

    for slug in sorted(_PUBLISH_NOTHING_PRESETS):
        publish = annotations_by_slug[slug].get("workflowPublish")
        assert publish is not None, f"{slug} must declare workflowPublish"
        assert publish["mode"] == "none", slug

    for slug in sorted(_MANAGED_PUBLISH_PRESETS):
        assert "workflowPublish" not in annotations_by_slug[slug], (
            f"{slug} publishes through the MoonMind-managed publisher and must "
            "not pin a workflow-level publish mode"
        )


async def test_no_seed_preset_declares_auto_publish(tmp_path):
    """`auto` requires an agent-owned publication skill and publish evidence."""

    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            annotations_by_slug = await _seeded_annotations(session, tmp_path)

    for slug, preset_annotations in sorted(annotations_by_slug.items()):
        publish = preset_annotations.get("workflowPublish") or {}
        assert str(publish.get("mode") or "").strip().lower() != "auto", slug


@pytest.mark.parametrize(
    ("slug", "inputs"),
    [
        (
            "document-update-orchestrate",
            {"document_directory": "docs", "publish_mode": "pr"},
        ),
        (
            "issue-implement-assessment",
            {
                "issue_provider": "github",
                "issue_ref": "MoonLadderStudios/MoonMind#1",
                "brief_artifact_path": "artifacts/brief.md",
                "assessment_artifact_path": "artifacts/assessment.json",
            },
        ),
        (
            "jira-breakdown",
            {
                "feature_request": "Break this down into stories.",
                "jira_project_key": "MM",
                "jira_issue_type": "Story",
                "jira_dependency_mode": "linear_blocker_chain",
            },
        ),
        (
            "jira-breakdown-implement",
            {
                "feature_request": "Break this down into stories.",
                "jira_project_key": "MM",
                "jira_issue_type": "Story",
                "jira_dependency_mode": "linear_blocker_chain",
                "publish_mode": "pr",
            },
        ),
        (
            "jira-breakdown-orchestrate",
            {
                "feature_request": "Break this down into stories.",
                "jira_project_key": "MM",
                "jira_issue_type": "Story",
                "jira_dependency_mode": "linear_blocker_chain",
                "publish_mode": "pr",
            },
        ),
        (
            "github-issue-breakdown-implement",
            {
                "feature_request": "Break this down into issues.",
                "github_repository": "MoonLadderStudios/MoonMind",
                "publish_mode": "pr",
            },
        ),
        (
            "github-issue-breakdown-orchestrate",
            {
                "feature_request": "Break this down into issues.",
                "github_repository": "MoonLadderStudios/MoonMind",
                "publish_mode": "pr",
            },
        ),
    ],
)
async def test_publish_nothing_presets_expand_with_publish_none(
    tmp_path,
    slug,
    inputs,
):
    """The annotation reaches the expanded workflow payload.

    The breakdown presets take a `publish_mode` input, but it configures the
    dependent child workflows they create. The parent itself only creates
    tracker issues and child workflows, so its own publish mode stays `none`.
    """

    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            expanded = await service.expand_template(
                slug=slug,
                scope="global",
                scope_ref=None,
                inputs=inputs,
                context={
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                },
            )

    assert expanded["publish"] == {"mode": "none"}


async def test_included_assessment_preset_does_not_disable_parent_publishing(
    tmp_path,
):
    """A child preset's publish policy never overwrites the root workflow's.

    `issue-implement-assessment` is reused by the implementation presets, which
    do publish. Expansion reads `workflowPublish` from the root preset only.
    """

    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            expanded = await service.expand_template(
                slug="github-issue-implement",
                scope="global",
                scope_ref=None,
                inputs={
                    "github_issue": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "number": 3142,
                    },
                    "github_issue_ref": "MoonLadderStudios/MoonMind#3142",
                },
                context={"repository": "MoonLadderStudios/MoonMind"},
            )

    assert "publish" not in expanded
    assert any(
        step.get("presetProvenance", {}).get("source", {}).get("slug")
        == "issue-implement-assessment"
        for step in expanded["steps"]
    )


@pytest.mark.parametrize(
    ("default_from", "expected_message"),
    [
        ("run_ref", "must be an object"),
        ({"map": {"a": "b"}}, "requires a source 'field'"),
        ({"field": "run_ref"}, "requires a source 'field'"),
        ({"field": "run_ref", "map": {}}, "requires a source 'field'"),
        (
            {"field": "run_ref", "map": {"a": "token=raw-secret"}},
            "secret-like value",
        ),
    ],
)
async def test_malformed_dependent_default_rule_is_rejected_at_seed_time(
    tmp_path,
    default_from,
    expected_message,
):
    """A broken rule fails fast instead of silently using the static default."""

    seed_dir = tmp_path / "presets"
    seed_dir.mkdir()
    (seed_dir / "broken.yaml").write_text(
        yaml.safe_dump(
            {
                "slug": "broken-dependent-default",
                "title": "Broken Dependent Default",
                "description": "Preset with an invalid defaultFrom rule.",
                "scope": "global",
                "annotations": {
                    "uiSchema": {"publish_mode": {"defaultFrom": default_from}}
                },
                "inputs": [
                    {"name": "run_ref", "label": "Run", "type": "text"},
                    {"name": "publish_mode", "label": "Publish", "type": "text"},
                ],
                "steps": [
                    {
                        "title": "Do nothing",
                        "instructions": "Do nothing.",
                        "skill": {"id": "noop"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            service = PresetCatalogService(session)
            with pytest.raises(PresetValidationError, match=expected_message):
                await service.sync_seed_templates(seed_dir=seed_dir)
