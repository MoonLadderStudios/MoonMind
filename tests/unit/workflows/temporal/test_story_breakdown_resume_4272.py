"""Bounded backlog for MoonLadderStudios/MoonMind#4272.

Executable behavior for simplifying story breakdown through existing
composition and resumable evidence handling. Tests executable behavior only
(no documentation wording, headings, structure, counts, or required phrases):

- unsupported ``issueCreation.action`` values suspend as ``manual_review``
  without a new failure (existing wire value, non-mutating);
- recoverable reconciliation blocks carry an automation-owned continuation
  naming the specific missing input (genuine explicit holds stay human-owned);
- source-kind classification is authority/meaning-based across explicit-file,
  trusted-issue, inline, imperative, and ambiguous sources without fabricated
  claim IDs;
- receipt-driven resume continues only unfinished effects while preserving
  original issue/PR/child identity (no new ledger, no re-search);
- downstream child bindings preserve declared dependency order with actual
  Tool/Skill/preset identities.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.presets.catalog import PresetCatalogService
from moonmind.workflows.temporal import story_output_tools as story_tools

REPO_ROOT = Path(__file__).resolve().parents[4]
PRESET_DIR = REPO_ROOT / "api_service" / "data" / "presets"


class _FakeJiraService:
    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.search_response: Any = {"issues": []}

    async def create_issue(self, request):
        self.requests.append(request)
        return {
            "created": True,
            "issueKey": f"MM-{len(self.requests)}",
            "issueId": str(len(self.requests)),
            "self": f"https://jira.example/rest/api/3/issue/{len(self.requests)}",
        }

    async def search_issues(self, request):
        return self.search_response

    async def list_create_issue_types(self, request):
        return {"issueTypes": [{"id": "10005", "name": "Story"}]}

    async def create_issue_link(self, request):
        return {
            "linked": True,
            "blocksIssueKey": request.blocks_issue_key,
            "blockedIssueKey": request.blocked_issue_key,
            "linkType": request.link_type,
        }


def test_unknown_issue_creation_action_suspends_without_mutation() -> None:
    eligible, skipped, blocked, _ = story_tools._reconcile_stories_for_issue_creation(
        [
            {
                "id": "STORY-001",
                "summary": "Unknown action story",
                "implementationStatus": "not_implemented",
                "issueCreation": {
                    "action": "needs_human_signoff_v2",
                    "reason": "Custom review state.",
                },
            }
        ]
    )
    assert eligible == []
    assert skipped == []
    assert len(blocked) == 1
    record = blocked[0]
    # Existing wire value is preserved for non-mutating suspension.
    assert record["issueCreationAction"] == "manual_review"
    assert record["jiraCreationAction"] == "manual_review"
    continuation = record["continuation"]
    assert continuation["owner"] == "automation"
    assert "needs_human_signoff_v2" in str(
        continuation.get("missingInput") or continuation.get("reason") or ""
    )


def test_unsupported_implementation_status_suspends_without_failure() -> None:
    eligible, skipped, blocked, _ = story_tools._reconcile_stories_for_issue_creation(
        [
            {
                "id": "STORY-002",
                "summary": "Future status story",
                "implementationStatus": "awaiting_triage_v2",
            }
        ]
    )
    assert eligible == []
    assert skipped == []
    assert len(blocked) == 1
    assert blocked[0]["issueCreationAction"] == "manual_review"
    assert blocked[0]["continuation"]["owner"] == "automation"


@pytest.mark.asyncio
async def test_unknown_action_creates_no_provider_issue() -> None:
    service = _FakeJiraService()
    result = await story_tools.create_jira_issues_from_stories(
        {
            "storyOutput": {"mode": "jira", "dependencyMode": "none"},
            "stories": [
                {
                    "id": "STORY-001",
                    "summary": "Unknown action story",
                    "implementationStatus": "not_implemented",
                    "issueCreation": {"action": "needs_human_signoff_v2"},
                }
            ],
        },
        jira_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert result.outputs["jira"]["createdIssues"] == []
    assert len(result.outputs["storyOutput"]["blockedStories"]) == 1
    assert service.requests == []


def test_partial_without_remaining_work_reports_automation_continuation() -> None:
    _eligible, _skipped, blocked, _ = (
        story_tools._reconcile_stories_for_issue_creation(
            [
                {
                    "id": "STORY-003",
                    "summary": "Partial story",
                    "implementationStatus": "partially_implemented",
                }
            ]
        )
    )
    assert len(blocked) == 1
    continuation = blocked[0]["continuation"]
    assert continuation["owner"] == "automation"
    assert "remainingWork" in str(
        continuation.get("missingInput") or continuation.get("reason") or ""
    )


def test_explicit_hold_stays_human_owned() -> None:
    _eligible, _skipped, blocked, _ = (
        story_tools._reconcile_stories_for_issue_creation(
            [
                {
                    "id": "STORY-004",
                    "summary": "Held story",
                    "implementationStatus": "unverifiable",
                    "issueCreation": {
                        "action": "manual_review",
                        "reason": "Operator hold: user decision required before scoping.",
                    },
                }
            ]
        )
    )
    assert len(blocked) == 1
    assert blocked[0]["continuation"]["owner"] == "human"


@pytest.mark.parametrize(
    ("selection", "expected_kind"),
    [
        (
            {
                "source": {
                    "path": "docs/Steps/SkillSystem.md",
                    "sourceDocumentClass": "canonical-declarative",
                },
                "sourceReference": {
                    "path": "docs/Steps/SkillSystem.md",
                    "claimIds": ["SKILL-001"],
                },
            },
            "explicit-file",
        ),
        (
            {
                "source": {
                    "sourceDocumentClass": "imperative-input",
                    "title": "Roadmap phases",
                },
                "sourceReference": {"path": None, "claimIds": []},
            },
            "imperative",
        ),
        (
            {
                "source": {"title": "MM-123 trusted brief"},
                "sourceIssueKey": "MM-123",
                "sourceReference": {"path": None, "claimIds": []},
            },
            "trusted-issue",
        ),
        (
            {
                "source": {"title": "Inline instructions"},
                "sourceReference": {"path": None, "claimIds": []},
            },
            "inline",
        ),
        (
            {
                "source": {},
                "sourceCandidates": ["docs/A.md", "docs/B.md"],
                "sourceReference": {"path": None, "claimIds": []},
            },
            "ambiguous-source",
        ),
    ],
)
def test_source_kind_classification_is_authority_based(
    selection: dict[str, Any], expected_kind: str
) -> None:
    assert story_tools._resolve_breakdown_source_kind(selection) == expected_kind


def test_imperative_source_never_requires_canonical_claim_ids() -> None:
    assert (
        story_tools._source_reference_requires_claim_ids(
            source_document_class="imperative-input",
            source_path="scripts/tmp_notes.md",
        )
        is False
    )


def test_receipt_filter_continues_only_unfinished_effects() -> None:
    stories = [
        {"id": "STORY-001", "summary": "First"},
        {"id": "STORY-002", "summary": "Second"},
        {"id": "STORY-003", "summary": "Third"},
    ]
    receipts = [
        {"storyId": "STORY-001", "storyIndex": 1, "issueKey": "MM-1"},
        {"storyId": "STORY-003", "storyIndex": 3, "issueKey": "MM-3"},
    ]
    pending, already_created = story_tools._filter_pending_stories_by_receipts(
        stories, receipts
    )
    assert [story_tools._story_id(item, index=i) for i, item in enumerate(pending, 1)] == [
        "STORY-002"
    ]
    assert [item["storyId"] for item in already_created] == ["STORY-001", "STORY-003"]
    # Original identity is preserved from the receipt, not re-searched.
    assert already_created[0]["issueKey"] == "MM-1"
    assert already_created[1]["issueKey"] == "MM-3"


def test_receipt_positional_fallback_requires_matching_story_id() -> None:
    """Positional receipts must not claim a story with a different stable ID."""
    stories = [
        {"id": "STORY-A", "summary": "First"},
        {"id": "STORY-B", "summary": "Second"},
    ]
    receipts = [
        {"storyId": "STORY-X", "storyIndex": 1, "issueKey": "MM-99"},
    ]
    pending, already_created = story_tools._filter_pending_stories_by_receipts(
        stories, receipts
    )
    assert [item["id"] for item in pending] == ["STORY-A", "STORY-B"]
    assert already_created == []


def test_receipt_positional_fallback_supports_legacy_receipts_without_story_id() -> (
    None
):
    """Legacy receipts without a stable story ID still resume by position."""
    stories = [
        {"id": "STORY-A", "summary": "First"},
        {"id": "STORY-B", "summary": "Second"},
    ]
    receipts = [
        {"storyIndex": 1, "issueKey": "MM-99"},
    ]
    pending, already_created = story_tools._filter_pending_stories_by_receipts(
        stories, receipts
    )
    assert [item["id"] for item in pending] == ["STORY-B"]
    assert [item["issueKey"] for item in already_created] == ["MM-99"]


@asynccontextmanager
async def _catalog_service(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/breakdown_4272.db")
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=PRESET_DIR)
            yield service
    finally:
        await engine.dispose()


_BREAKDOWN_CASES = {
    "github-issue-breakdown-implement": {
        "inputs": {
            "feature_request": "Split the work.",
            "source_design_path": "",
            "github_repository": "MoonLadderStudios/MoonMind",
            "publish_mode": "pr_with_merge_automation",
            "source_issue_key": "MM-1063",
        },
        "context": {"targetRuntime": "codex"},
        "skill_chain": [
            "moonspec-breakdown",
            "story-reconcile-implementation",
            "story.create_github_issues",
            "story.create_github_issue_implement_workflows",
        ],
        "story_output_mode": "github",
        "orchestration_key": "githubOrchestration",
        "downstream_preset": "github-issue-implement",
    },
    "github-issue-breakdown-orchestrate": {
        "inputs": {
            "feature_request": "Split the work.",
            "source_design_path": "",
            "github_repository": "MoonLadderStudios/MoonMind",
            "publish_mode": "pr",
            "source_issue_key": "MM-1063",
        },
        "context": {"targetRuntime": "codex"},
        "skill_chain": [
            "moonspec-breakdown",
            "story-reconcile-implementation",
            "story.create_github_issues",
            "story.create_github_issue_orchestrate_workflows",
        ],
        "story_output_mode": "github",
        "orchestration_key": "githubOrchestration",
        "downstream_preset": "github-issue-orchestrate",
    },
    "jira-breakdown-implement": {
        "inputs": {
            "feature_request": "Split the work.",
            "source_design_path": "",
            "jira_project_key": "MM",
            "jira_issue_type": "Story",
            "jira_dependency_mode": "linear_blocker_chain",
            "publish_mode": "pr_with_merge_automation",
            "source_issue_key": "MM-1063",
        },
        "context": {"repository": "MoonLadderStudios/MoonMind", "targetRuntime": "codex"},
        "skill_chain": [
            "moonspec-breakdown",
            "story-reconcile-implementation",
            "story.create_jira_issues",
            "story.create_jira_implement_tasks",
        ],
        "story_output_mode": "jira",
        "orchestration_key": "jiraOrchestration",
        "downstream_preset": "jira-implement",
    },
    "jira-breakdown-orchestrate": {
        "inputs": {
            "feature_request": "Split the work.",
            "source_design_path": "",
            "jira_project_key": "MM",
            "jira_issue_type": "Story",
            "jira_dependency_mode": "none",
            "publish_mode": "pr",
            "source_issue_key": "MM-1063",
        },
        "context": {"repository": "MoonLadderStudios/MoonMind", "targetRuntime": "codex"},
        "skill_chain": [
            "moonspec-breakdown",
            "story-reconcile-implementation",
            "story.create_jira_issues",
            "story.create_jira_orchestrate_tasks",
        ],
        "story_output_mode": "jira",
        "orchestration_key": "jiraOrchestration",
        "downstream_preset": "jira-orchestrate",
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", sorted(_BREAKDOWN_CASES))
async def test_breakdown_presets_expand_through_existing_components(
    tmp_path, slug: str
) -> None:
    """Changed presets expand with provider/creation/dependency differences kept.

    Already-correct ``issueCreation`` paths are reused; GitHub presets stay
    traceability-only on the source issue key while Jira presets keep the
    trusted brief tool step and the selected dependency mode.
    """
    case = _BREAKDOWN_CASES[slug]
    async with _catalog_service(tmp_path) as service:
        expanded = await service.expand_template(
            slug=slug,
            scope="global",
            scope_ref=None,
            inputs=dict(case["inputs"]),
            context=dict(case["context"]),
        )
    steps = expanded["steps"]

    def _by_id(step_id: str) -> dict[str, Any]:
        for step in steps:
            payload = step.get("skill") or step.get("tool") or {}
            if payload.get("id") == step_id:
                return step
        raise AssertionError(f"missing step {step_id} in {slug}")

    chain = [
        (step.get("skill") or step.get("tool"))["id"] for step in steps
    ]
    if chain[0] == "jira.load_preset_brief":
        assert chain == ["jira.load_preset_brief"] + case["skill_chain"]
    else:
        assert chain == case["skill_chain"]
    create_step = _by_id(case["skill_chain"][2])
    assert create_step["storyOutput"]["mode"] == case["story_output_mode"]
    assert create_step["storyOutput"]["fallback"] == "fail"
    downstream_step = _by_id(case["skill_chain"][3])
    orchestration = downstream_step[case["orchestration_key"]]
    assert orchestration["traceability"] == {"sourceIssueKey": "MM-1063"}
    downstream_slugs = (
        story_tools._GITHUB_DOWNSTREAM_PRESETS
        if case["story_output_mode"] == "github"
        else story_tools._DOWNSTREAM_PRESETS
    )
    if case["story_output_mode"] == "github":
        expected_slug = (
            "github-issue-implement"
            if "implement" in case["downstream_preset"]
            else "github-issue-orchestrate"
        )
    else:
        expected_slug = case["downstream_preset"]
    assert expected_slug in {
        preset["slug"] for preset in downstream_slugs.values()
    }


@pytest.mark.asyncio
async def test_jira_breakdown_keeps_trusted_brief_and_dependency_mode(tmp_path) -> None:
    async with _catalog_service(tmp_path) as service:
        expanded = await service.expand_template(
            slug="jira-breakdown-implement",
            scope="global",
            scope_ref=None,
            inputs=dict(_BREAKDOWN_CASES["jira-breakdown-implement"]["inputs"]),
            context={"repository": "MoonLadderStudios/MoonMind", "targetRuntime": "codex"},
        )
    tool_steps = [step for step in expanded["steps"] if step.get("tool")]
    assert [step["tool"]["id"] for step in tool_steps] == ["jira.load_preset_brief"]
    create_step = next(
        step
        for step in expanded["steps"]
        if (step.get("skill") or {}).get("id") == "story.create_jira_issues"
    )
    assert (
        create_step["storyOutput"]["jira"]["dependencyMode"]
        == "linear_blocker_chain"
    )


@pytest.mark.asyncio
async def test_github_breakdown_source_issue_key_stays_traceability_only(
    tmp_path,
) -> None:
    async with _catalog_service(tmp_path) as service:
        expanded = await service.expand_template(
            slug="github-issue-breakdown-implement",
            scope="global",
            scope_ref=None,
            inputs=dict(
                _BREAKDOWN_CASES["github-issue-breakdown-implement"]["inputs"]
            ),
            context={"targetRuntime": "codex"},
        )
    create_step = next(
        step
        for step in expanded["steps"]
        if (step.get("skill") or {}).get("id") == "story.create_github_issues"
    )
    assert create_step["storyOutput"]["github"] == {
        "repository": "MoonLadderStudios/MoonMind",
        "sourceIssueKey": "MM-1063",
    }
    assert not [step for step in expanded["steps"] if step.get("tool")]
    assert "issueCreation" in "\n".join(
        step.get("instructions", "") for step in expanded["steps"]
    )


class _FakeGitHubService:
    def __init__(self) -> None:
        self.create_issue_requests: list[dict[str, Any]] = []

    async def create_issue(
        self,
        *,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
        github_token: str | None = None,
    ):
        self.create_issue_requests.append({"repo": repo, "title": title})
        issue_number = len(self.create_issue_requests)
        return {
            "externalKey": str(issue_number),
            "externalUrl": f"https://github.com/{repo}/issues/{issue_number}",
            "created": True,
            "summary": f"GitHub issue created: https://github.com/{repo}/issues/{issue_number}",
        }


def _jira_inputs(
    stories: Any,
    prior_mappings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "stories": stories,
        "storyOutput": {
            "mode": "jira",
            "jira": {"projectKey": "MM", "issueTypeName": "Story"},
        },
    }
    if prior_mappings is not None:
        inputs["previous_outputs"] = {"jira": {"issueMappings": prior_mappings}}
    return inputs


@pytest.mark.asyncio
async def test_jira_creation_resumes_from_prior_issue_mappings() -> None:
    """Partial Jira runs resume from receipts without re-creating finished work."""
    service = _FakeJiraService()
    stories = [
        {"id": "STORY-001", "summary": "First"},
        {"id": "STORY-002", "summary": "Second"},
        {"id": "STORY-003", "summary": "Third"},
    ]
    prior = [
        {
            "storyId": "STORY-001",
            "storyIndex": 1,
            "issueKey": "MM-99",
            "summary": "First",
        }
    ]
    result = await story_tools.create_jira_issues_from_stories(
        _jira_inputs(stories, prior),
        jira_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    # Only unfinished effects retry through the provider adapter.
    assert len(service.requests) == 2
    mappings = result.outputs["jira"]["issueMappings"]
    assert len(mappings) == 3
    by_story = {item["storyId"]: item for item in mappings}
    # Original identity is preserved, not re-searched or re-issued.
    assert by_story["STORY-001"]["issueKey"] == "MM-99"
    assert by_story["STORY-002"]["issueKey"] != "MM-99"
    assert by_story["STORY-003"]["issueKey"] != "MM-99"
    assert result.outputs["jira"]["createdIssues"] is not None
    assert len(result.outputs["jira"]["createdIssues"]) == 3


@pytest.mark.asyncio
async def test_github_creation_resumes_from_prior_issue_mappings() -> None:
    """Partial GitHub runs resume from receipts without re-creating finished work."""
    service = _FakeGitHubService()
    stories = [
        {"id": "STORY-001", "summary": "First"},
        {"id": "STORY-002", "summary": "Second"},
    ]
    prior = [
        {
            "storyId": "STORY-001",
            "storyIndex": 1,
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": "7",
            "issueUrl": "https://github.com/MoonLadderStudios/MoonMind/issues/7",
            "summary": "First",
        }
    ]
    result = await story_tools.create_github_issues_from_stories(
        {
            "stories": stories,
            "storyOutput": {
                "mode": "github",
                "github": {"repository": "MoonLadderStudios/MoonMind"},
            },
            "previous_outputs": {"github": {"issueMappings": prior}},
        },
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    assert len(service.create_issue_requests) == 1
    assert service.create_issue_requests[0]["title"] == "Second"
    mappings = result.outputs["github"]["issueMappings"]
    assert len(mappings) == 2
    by_story = {item["storyId"]: item for item in mappings}
    assert by_story["STORY-001"]["issueNumber"] == "7"


class _FailingSecondJiraService(_FakeJiraService):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def create_issue(self, request):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("boom after first issue")
        return await super().create_issue(request)


@pytest.mark.asyncio
async def test_jira_partial_failure_preserves_issue_mappings() -> None:
    """Jira fallback after a mid-run failure keeps story-linked receipts."""
    service = _FailingSecondJiraService()
    stories = [
        {"id": "STORY-001", "summary": "First"},
        {"id": "STORY-002", "summary": "Second"},
    ]
    result = await story_tools.create_jira_issues_from_stories(
        _jira_inputs(stories),
        jira_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    jira_outputs = result.outputs["jira"]
    assert jira_outputs.get("partial") is True
    mappings = jira_outputs.get("issueMappings") or []
    assert len(mappings) == 1
    assert mappings[0]["storyId"] == "STORY-001"
    assert mappings[0].get("issueKey")


class _FailingSecondGitHubService(_FakeGitHubService):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def create_issue(self, *, repo, title, body, labels=None, github_token=None):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("boom after first issue")
        return await super().create_issue(
            repo=repo, title=title, body=body, labels=labels, github_token=github_token
        )


@pytest.mark.asyncio
async def test_github_partial_failure_preserves_issue_mappings() -> None:
    """GitHub mid-run failure keeps completed mappings for the retry."""
    service = _FailingSecondGitHubService()
    stories = [
        {"id": "STORY-001", "summary": "First"},
        {"id": "STORY-002", "summary": "Second"},
    ]
    with pytest.raises(RuntimeError, match="boom") as exc_info:
        await story_tools.create_github_issues_from_stories(
            {
                "stories": stories,
                "storyOutput": {
                    "mode": "github",
                    "github": {"repository": "MoonLadderStudios/MoonMind"},
                },
            },
            github_service_factory=lambda: service,
        )
    assert service.calls == 2
    partial = getattr(exc_info.value, "partial_issue_mappings", None)
    assert isinstance(partial, list)
    assert len(partial) == 1
    assert partial[0]["storyId"] == "STORY-001"
    assert partial[0].get("issueNumber")


@pytest.mark.asyncio
async def test_jira_creation_fails_fast_on_ambiguous_source() -> None:
    """Multiple unresolved source candidates fail fast instead of arbitrary split."""
    service = _FakeJiraService()
    payload: dict[str, Any] = {
        "source": {},
        "sourceCandidates": ["docs/A.md", "docs/B.md"],
        "stories": [{"id": "STORY-001", "summary": "First"}],
    }
    with pytest.raises(ValueError, match="[Aa]mbiguous"):
        await story_tools.create_jira_issues_from_stories(
            _jira_inputs(payload),
            jira_service_factory=lambda: service,
        )
    assert service.requests == []


@pytest.mark.asyncio
async def test_github_creation_fails_fast_on_ambiguous_source() -> None:
    """GitHub creation also fails fast on unresolved source candidates."""
    service = _FakeGitHubService()
    payload: dict[str, Any] = {
        "source": {},
        "sourceCandidates": ["docs/A.md", "docs/B.md"],
        "stories": [{"id": "STORY-001", "summary": "First"}],
    }
    with pytest.raises(ValueError, match="[Aa]mbiguous"):
        await story_tools.create_github_issues_from_stories(
            {
                "stories": payload,
                "storyOutput": {
                    "mode": "github",
                    "github": {"repository": "MoonLadderStudios/MoonMind"},
                },
            },
            github_service_factory=lambda: service,
        )
    assert service.create_issue_requests == []
