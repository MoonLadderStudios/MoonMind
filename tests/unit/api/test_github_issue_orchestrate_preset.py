"""Catalog-boundary tests for the MM-1067 ``github-issue-orchestrate`` seed preset."""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, Preset, PresetScopeType
from api_service.services.presets.catalog import PresetCatalogService

pytestmark = [pytest.mark.asyncio]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRESET_DIR = _REPO_ROOT / "api_service" / "data" / "presets"


@asynccontextmanager
async def _catalog_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/github_issue_orchestrate.db"
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


def _seed_dir(tmp_path) -> Path:
    seed_dir = tmp_path / "presets"
    seed_dir.mkdir()
    for filename in (
        "github-issue-orchestrate.yaml",
        "issue-implement-assessment.yaml",
    ):
        shutil.copy(_PRESET_DIR / filename, seed_dir / filename)
    return seed_dir


async def _load_preset(session) -> Preset:
    result = await session.execute(
        select(Preset).where(
            Preset.slug == "github-issue-orchestrate",
            Preset.scope_type == PresetScopeType.GLOBAL,
            Preset.scope_ref.is_(None),
        )
    )
    return result.scalar_one()


async def test_github_issue_orchestrate_seed_exposes_issue_picker_and_tools(tmp_path):
    async with _catalog_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            template = await _load_preset(session)
            annotations = template.annotations or {}

    assert template.title == "GitHub Issue Orchestrate"
    assert template.scope_type is PresetScopeType.GLOBAL
    assert sorted(template.required_capabilities) == ["gh", "git"]

    assert annotations["jiraIssue"] == "MM-1067"
    assert annotations["sourceReference"] == "MM-1063"
    assert annotations["issueInput"] == {
        "provider": "github",
        "objectInput": "github_issue",
        "refInput": "github_issue_ref",
        "refTemplate": "{{ repository }}#{{ number }}",
    }
    assert annotations["inputSchema"]["required"] == ["github_issue"]
    github_issue = annotations["inputSchema"]["properties"]["github_issue"]
    assert github_issue["required"] == ["repository", "number"]
    assert annotations["uiSchema"]["github_issue"] == {
        "widget": "github.issue-picker",
        "dataSource": "github.issues",
        "searchPlaceholder": "Search GitHub issues",
        "allowManualIssueEntry": True,
    }
    assert annotations["githubIssueUpdate"]["inProgress"]["labelsToAdd"] == [
        "status: in-progress"
    ]
    assert annotations["githubIssueUpdate"]["codeReview"][
        "commentPullRequestUrl"
    ] is True

    assert [step.get("kind", "step") for step in template.steps[:4]] == [
        "step",
        "include",
        "step",
        "step",
    ]
    assert [
        (step.get("tool") or step.get("skill") or {}).get("id")
        for step in template.steps[:4]
    ] == [
        "github.load_issue_preset_brief",
        None,
        "github.check_issue_blockers",
        "github.update_issue_status",
    ]
    assert template.steps[1]["slug"] == "issue-implement-assessment"
    assert template.steps[0]["tool"]["inputs"]["artifactPath"] == (
        "artifacts/github-issue-orchestrate-brief.json"
    )
    assert template.steps[1]["inputMapping"] == {
        "issue_provider": "github",
        "issue_ref": "{{ inputs.github_issue.repository }}#{{ inputs.github_issue.number }}",
        "issue_url": "{{ inputs.github_issue.url | default('') }}",
        "brief_artifact_path": "artifacts/github-issue-orchestrate-brief.json",
        "assessment_artifact_path": "artifacts/github-issue-orchestrate-assessment.json",
        "constraints": "{{ inputs.constraints }}",
    }
    assert template.steps[3]["tool"]["inputs"]["assessmentArtifactPath"] == (
        "artifacts/github-issue-orchestrate-assessment.json"
    )


async def test_github_issue_orchestrate_expands_required_order_and_gates(tmp_path):
    async with _catalog_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))

            expanded = await service.expand_template(
                slug="github-issue-orchestrate",
                scope="global",
                scope_ref=None,
                inputs={
                    "github_issue": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "number": 1063,
                        "url": "https://github.com/MoonLadderStudios/MoonMind/issues/1063",
                    },
                    "github_issue_ref": "MoonLadderStudios/MoonMind#1063",
                    "source_design_path": "",
                    "constraints": "Preserve MM-1063 traceability.",
                },
                context={},
            )

    steps = expanded["steps"]
    assert len(steps) == 27
    assert [step["title"] for step in steps[:4]] == [
        "Load GitHub issue brief",
        "Assess existing implementation state",
        "Check GitHub issue blockers before orchestration",
        "Mark GitHub issue In Progress",
    ]
    assert steps[0]["tool"]["id"] == "github.load_issue_preset_brief"
    assert steps[0]["tool"]["inputs"] == {
        "repository": "MoonLadderStudios/MoonMind",
        "issueNumber": "1063",
        "artifactPath": "artifacts/github-issue-orchestrate-brief.json",
    }
    assert steps[1]["skill"]["id"] == "auto"
    assert "artifacts/github-issue-orchestrate-assessment.json" in steps[1][
        "instructions"
    ]
    assert "artifacts/github-issue-orchestrate-brief.json" in steps[1][
        "instructions"
    ]
    assert "Preserve MM-1063 traceability." in steps[1]["instructions"]
    assert "FULLY_IMPLEMENTED" in steps[1]["instructions"]
    assert steps[2]["tool"]["id"] == "github.check_issue_blockers"
    assert "deterministic trusted GitHub blocker preflight" in steps[2][
        "instructions"
    ]
    assert steps[3]["tool"] == {
        "id": "github.update_issue_status",
        "requiredCapabilities": ["gh"],
        "inputs": {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": "1063",
            "targetStatus": "In Progress",
            "mode": "start",
            "assessmentArtifactPath": "artifacts/github-issue-orchestrate-assessment.json",
        },
    }

    assert [steps[index]["skill"]["id"] for index in range(4, 11)] == [
        "auto",
        "moonspec-specify",
        "auto",
        "moonspec-plan",
        "moonspec-tasks",
        "moonspec-align",
        "moonspec-implement",
    ]
    assert "Preserve MM-1063 traceability." in steps[4]["instructions"]
    assert "FULLY_IMPLEMENTED" in steps[4]["instructions"]
    assert "make no code changes" in steps[10]["instructions"]
    assert steps[11]["skill"]["id"] == "moonspec-verify"

    assert steps[12]["title"] == "Remediate verification gaps 1 of 6"
    assert steps[12]["annotations"]["jiraOrchestrateRole"] == "moonspec-remediation"
    assert "ADDITIONAL_WORK_NEEDED" in steps[12]["instructions"]
    assert steps[23]["title"] == "Verify remediation 6 of 6"
    assert steps[23]["annotations"]["moonSpecFinalRemediationGate"] is True
    assert "controlling verification gate" in steps[23]["instructions"]

    assert steps[24]["title"] == "Reconcile declarative docs"
    assert steps[24]["annotations"] == {"jiraOrchestrateRole": "doc-reconciliation"}
    assert steps[24]["skill"]["id"] == "moonspec-doc-reconcile"
    assert "FULLY_IMPLEMENTED" in steps[24]["instructions"]
    assert "skip doc reconciliation" in steps[24]["instructions"]
    assert "artifacts/github-issue-orchestrate-doc-reconcile.json" in steps[24][
        "instructions"
    ]

    assert steps[25]["title"] == "Create pull request"
    assert steps[25]["annotations"] == {"jiraOrchestrateRole": "pull-request-handoff"}
    assert "skip pull request creation entirely" in steps[25]["instructions"]
    assert "post-remediation moonspec-verify" in steps[25]["instructions"]
    assert "terminal verifier outcomes" not in steps[25]["instructions"].lower()
    assert "ADDITIONAL_WORK_NEEDED" in steps[25]["instructions"]
    assert "artifacts/github-issue-orchestrate-pr.json" in steps[25]["instructions"]

    assert steps[26]["title"] == "Finalize GitHub issue status"
    assert steps[26]["annotations"] == {"jiraOrchestrateRole": "code-review-handoff"}
    assert steps[26]["tool"] == {
        "id": "github.update_issue_status",
        "requiredCapabilities": ["gh"],
        "inputs": {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": "1063",
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": "artifacts/github-issue-orchestrate-pr.json",
        },
    }
    assert "apply the configured Done strategy" in steps[26]["instructions"]
    assert "terminal verifier outcomes" in steps[26]["instructions"]
    assert "Code Review strategy" in steps[26]["instructions"]
