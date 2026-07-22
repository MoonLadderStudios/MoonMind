from __future__ import annotations

import json
from typing import Any

import pytest

from moonmind.workflows.temporal import story_output_tools as story_tools
from moonmind.workflows.temporal.story_output_tools import (
    check_github_issue_blockers,
    check_jira_blockers,
    create_document_update_tasks_from_paths,
    create_github_issue_implement_workflows_from_issue_mappings,
    create_github_issue_orchestrate_workflows_from_issue_mappings,
    create_github_issues_from_stories,
    create_jira_implement_tasks_from_issue_mappings,
    create_jira_issues_from_stories,
    create_jira_orchestrate_tasks_from_issue_mappings,
    discover_documents,
    load_github_issue_preset_brief,
    load_jira_preset_brief,
    update_github_issue_status,
    update_jira_issue_status,
)

class _FakeJiraService:
    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.subtask_requests: list[Any] = []
        self.link_requests: list[Any] = []
        self.search_requests: list[Any] = []
        self.search_response: Any = {"issues": []}
        self.issue_responses: dict[str, Any] = {}
        self.transition_responses: dict[str, Any] = {}
        self.transition_requests: list[Any] = []
        self.transitions_requests: list[Any] = []
        self.transitions_response: Any = {"transitions": []}
        self.get_issue_requests: list[Any] = []
        self.existing_links: set[tuple[str, str]] = set()
        self.fail_link_after: int | None = None

    async def create_issue(self, request):
        self.requests.append(request)
        return {
            "created": True,
            "issueKey": f"MM-{len(self.requests)}",
            "issueId": str(len(self.requests)),
            "self": f"https://jira.example/rest/api/3/issue/{len(self.requests)}",
        }

    async def create_subtask(self, request):
        self.subtask_requests.append(request)
        return {
            "created": True,
            "issueKey": f"MM-SUB-{len(self.subtask_requests)}",
            "issueId": f"sub-{len(self.subtask_requests)}",
            "self": f"https://jira.example/rest/api/3/issue/sub-{len(self.subtask_requests)}",
        }

    async def search_issues(self, request):
        self.search_requests.append(request)
        return self.search_response

    async def get_issue(self, request):
        self.get_issue_requests.append(request)
        return self.issue_responses.get(request.issue_key, {"key": request.issue_key})

    async def get_transitions(self, request):
        self.transitions_requests.append(request)
        return self.transitions_response

    async def transition_issue(self, request):
        self.transition_requests.append(request)
        if request.issue_key in self.transition_responses:
            self.issue_responses[request.issue_key] = self.transition_responses[
                request.issue_key
            ]
        return {
            "transitioned": True,
            "issueKey": request.issue_key,
            "transitionId": request.transition_id,
        }

    async def list_create_issue_types(self, request):
        return {
            "issueTypes": [
                {"id": "10005", "name": "Story"},
                {"id": "10006", "name": "Task"},
                {"id": "10007", "name": "Sub-task", "subtask": True},
            ]
        }

    async def create_issue_link(self, request):
        self.link_requests.append(request)
        if self.fail_link_after is not None and len(self.link_requests) > self.fail_link_after:
            raise RuntimeError("jira link unavailable")
        key = (request.blocks_issue_key, request.blocked_issue_key)
        if key in self.existing_links:
            return {
                "linked": False,
                "existing": True,
                "blocksIssueKey": request.blocks_issue_key,
                "blockedIssueKey": request.blocked_issue_key,
                "linkType": request.link_type,
            }
        return {
            "linked": True,
            "blocksIssueKey": request.blocks_issue_key,
            "blockedIssueKey": request.blocked_issue_key,
            "linkType": request.link_type,
        }

class _FakeExecutionCreator:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.fail_at = fail_at

    async def __call__(self, **kwargs):
        self.requests.append(kwargs)
        if self.fail_at is not None and len(self.requests) == self.fail_at:
            raise RuntimeError("execution service unavailable")
        index = len(self.requests)
        return {
            "workflowId": f"mm:story-{index}",
            "runId": f"run-{index}",
            "title": kwargs.get("title"),
        }


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.skills: dict[str, Any] = {}

    def register_skill(self, *, skill_name: str, handler: Any) -> None:
        self.skills[skill_name] = handler


class _FakeGitHubService:
    def __init__(self) -> None:
        self.token_requests: list[str] = []
        self.create_issue_requests: list[dict[str, Any]] = []

    async def resolve_github_token(self, *, repo: str):
        self.token_requests.append(repo)
        return "ghs-test", None

    async def create_issue(
        self,
        *,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
        github_token: str | None = None,
    ):
        self.create_issue_requests.append(
            {
                "repo": repo,
                "title": title,
                "body": body,
                "labels": labels or [],
                "github_token": github_token,
            }
        )
        issue_number = len(self.create_issue_requests)
        return {
            "externalKey": str(issue_number),
            "externalUrl": f"https://github.com/{repo}/issues/{issue_number}",
            "created": True,
            "summary": f"GitHub issue created: https://github.com/{repo}/issues/{issue_number}",
        }

    def _github_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _github_permission_summary(self, response) -> str:
        return f"github status {response.status_code}"


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeHttpClient:
    def __init__(self, *args, **kwargs) -> None:
        self.requests: list[tuple[str, str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, **kwargs):
        self.requests.append(("GET", url, kwargs))
        return _FakeHttpResponse(
            {
                "number": 1067,
                "title": "Add GitHub Issue Orchestrate preset",
                "body": "Build the preset.",
                "html_url": "https://github.com/MoonLadderStudios/MoonMind/issues/1067",
                "state": "open",
                "labels": [{"name": "moonspec"}],
            }
        )

    async def patch(self, url: str, **kwargs):
        self.requests.append(("PATCH", url, kwargs))
        return _FakeHttpResponse(
            {
                "number": 1067,
                "title": "Add GitHub Issue Orchestrate preset",
                "body": "Build the preset.",
                "html_url": "https://github.com/MoonLadderStudios/MoonMind/issues/1067",
                "state": "open",
                "labels": [{"name": "status: in-progress"}],
            }
        )

    async def post(self, url: str, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return _FakeHttpResponse({"id": 1})


@pytest.mark.asyncio
async def test_load_github_issue_preset_brief_uses_requested_artifact_path(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(story_tools.httpx, "AsyncClient", _FakeHttpClient)
    service = _FakeGitHubService()

    result = await load_github_issue_preset_brief(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "artifactPath": "artifacts/github-issue-orchestrate-brief.json",
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["artifactPath"] == (
        "artifacts/github-issue-orchestrate-brief.json"
    )
    assert result.outputs["issue"]["number"] == 1067
    assert service.token_requests == ["MoonLadderStudios/MoonMind"]


@pytest.mark.asyncio
async def test_update_github_issue_status_skips_start_for_fully_implemented_assessment(
    tmp_path,
):
    assessment = tmp_path / "assessment.json"
    assessment.write_text('{"verdict": "FULLY_IMPLEMENTED"}', encoding="utf-8")
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "mode": "start",
            "assessmentArtifactPath": str(assessment),
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "skipped"
    assert result.outputs["assessmentVerdict"] == "FULLY_IMPLEMENTED"
    assert service.token_requests == []


@pytest.mark.asyncio
async def test_update_github_issue_status_blocks_start_for_blocked_assessment(
    tmp_path,
):
    assessment = tmp_path / "assessment.json"
    assessment.write_text('{"verdict": "BLOCKED"}', encoding="utf-8")
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(assessment),
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["assessmentVerdict"] == "BLOCKED"
    assert service.token_requests == []


@pytest.mark.asyncio
async def test_update_github_issue_status_blocks_code_review_without_verification_artifact(
    tmp_path,
):
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/2913"}',
        encoding="utf-8",
    )
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(pr_artifact),
            "verificationArtifactPath": str(tmp_path / "missing-verify.json"),
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert "verification artifact" in result.outputs["summary"]
    assert service.token_requests == []


@pytest.mark.asyncio
async def test_update_github_issue_status_blocks_code_review_without_verification_artifact_path(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(story_tools.httpx, "AsyncClient", _FakeHttpClient)
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/2913"}',
        encoding="utf-8",
    )
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(pr_artifact),
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert "verification artifact path" in result.outputs["summary"]
    assert service.token_requests == []


@pytest.mark.asyncio
async def test_update_github_issue_status_allows_code_review_without_verification_when_disabled(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(story_tools.httpx, "AsyncClient", _FakeHttpClient)
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/2913"}',
        encoding="utf-8",
    )
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(pr_artifact),
            "requireVerification": False,
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["appliedActions"] == ["patch_issue", "comment"]
    assert "mode code_review" in result.outputs["summary"]
    assert service.token_requests == [
        "MoonLadderStudios/MoonMind",
        "MoonLadderStudios/MoonMind",
    ]


@pytest.mark.asyncio
async def test_update_github_issue_status_declares_completed_close_side_effect(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(story_tools.httpx, "AsyncClient", _FakeHttpClient)
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "mode": "finalize_after_pr_or_done",
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["sideEffect"] == {
        "effectClass": "external_non_idempotent",
        "kind": "github",
        "operation": "github.issue.close",
        "target": "https://github.com/MoonLadderStudios/MoonMind/issues/1067",
        "summary": (
            "Updated GitHub issue MoonLadderStudios/MoonMind#1067 with mode done."
        ),
    }


@pytest.mark.asyncio
async def test_update_github_issue_status_blocks_done_when_pushed_changes_have_no_pr() -> None:
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/Tactics",
            "issueNumber": 2231,
            "mode": "finalize_after_pr_or_done",
            "previousOutputs": {
                "push_status": "pushed",
                "push_branch": "feature/issue-2231",
                "push_commit_count": 6,
            },
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs == {
        "issueRef": "MoonLadderStudios/Tactics#2231",
        "decision": "blocked",
        "pushStatus": "pushed",
        "commitCount": 6,
        "summary": (
            "Skipped GitHub issue finalization because repository changes were "
            "published without an authoritative pull request URL."
        ),
    }
    assert service.token_requests == []


@pytest.mark.asyncio
async def test_update_github_issue_status_uses_pr_url_from_publish_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(story_tools.httpx, "AsyncClient", _FakeHttpClient)
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/Tactics",
            "issueNumber": 2231,
            "mode": "finalize_after_pr_or_done",
            "requireVerification": False,
            "previousOutputs": {
                "publishContext": {
                    "pushStatus": "pushed",
                    "commitCount": 6,
                    "pullRequestUrl": (
                        "https://github.com/MoonLadderStudios/Tactics/pull/2240"
                    ),
                },
            },
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["appliedActions"] == ["patch_issue", "comment"]
    assert result.outputs["sideEffect"]["operation"] == "github.issue.update"
    assert service.token_requests == [
        "MoonLadderStudios/Tactics",
        "MoonLadderStudios/Tactics",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("require_verification", [None, "", "   "])
async def test_update_github_issue_status_requires_verification_for_blank_values(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    require_verification,
):
    monkeypatch.setattr(story_tools.httpx, "AsyncClient", _FakeHttpClient)
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/2913"}',
        encoding="utf-8",
    )
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(pr_artifact),
            "requireVerification": require_verification,
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert "verification artifact path" in result.outputs["summary"]
    assert service.token_requests == []


@pytest.mark.asyncio
async def test_update_github_issue_status_uses_previous_verification_payload(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(story_tools.httpx, "AsyncClient", _FakeHttpClient)
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/2913"}',
        encoding="utf-8",
    )
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(pr_artifact),
            "verificationArtifactPath": str(tmp_path / "workspace-only-verify.json"),
        },
        {
            "previousOutputs": {
                "moonSpecVerify": {
                    "verdict": "FULLY_IMPLEMENTED",
                    "gateResultRef": "art_verify",
                },
                "moonSpecVerifyArtifactRef": "art_verify",
            }
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["appliedActions"] == ["patch_issue", "comment"]
    assert "mode code_review" in result.outputs["summary"]
    assert service.token_requests == [
        "MoonLadderStudios/MoonMind",
        "MoonLadderStudios/MoonMind",
    ]


@pytest.mark.asyncio
async def test_update_github_issue_status_uses_previous_pull_request_output(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(story_tools.httpx, "AsyncClient", _FakeHttpClient)
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 3324,
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(tmp_path / "unavailable-pr.json"),
            "verificationArtifactPath": str(tmp_path / "unavailable-verify.json"),
            "previousOutputs": {
                "pull_request_url": (
                    "https://github.com/MoonLadderStudios/MoonMind/pull/3343"
                ),
                "moonSpecVerify": {"verdict": "FULLY_IMPLEMENTED"},
            },
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["appliedActions"] == ["patch_issue", "comment"]
    assert "mode code_review" in result.outputs["summary"]
    assert result.outputs["sideEffect"]["operation"] == "github.issue.update"


@pytest.mark.asyncio
async def test_update_github_issue_status_blocks_code_review_for_malformed_verification_artifact(
    tmp_path,
):
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/2913"}',
        encoding="utf-8",
    )
    verify_artifact = tmp_path / "verify.json"
    verify_artifact.write_text('{"verdict": ', encoding="utf-8")
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(pr_artifact),
            "verificationArtifactPath": str(verify_artifact),
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert "verification artifact" in result.outputs["summary"]
    assert service.token_requests == []


@pytest.mark.asyncio
async def test_update_github_issue_status_blocks_code_review_until_fully_implemented(
    tmp_path,
):
    pr_artifact = tmp_path / "pr.json"
    pr_artifact.write_text(
        '{"pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/2913"}',
        encoding="utf-8",
    )
    verify_artifact = tmp_path / "verify.json"
    verify_artifact.write_text('{"verdict": "ADDITIONAL_WORK_NEEDED"}', encoding="utf-8")
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(pr_artifact),
            "verificationArtifactPath": str(verify_artifact),
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["verificationVerdict"] == "ADDITIONAL_WORK_NEEDED"
    assert service.token_requests == []


@pytest.mark.asyncio
async def test_update_jira_issue_status_transitions_by_target_status() -> None:
    service = _FakeJiraService()
    service.issue_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "1", "name": "Backlog"}},
    }
    service.transition_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {
            "status": {
                "id": "3",
                "name": "In Progress",
                "statusCategory": {"key": "indeterminate"},
            }
        },
    }
    service.transitions_response = {
        "transitions": [
            {
                "id": "31",
                "name": "Start Progress",
                "to": {
                    "id": "3",
                    "name": "In Progress",
                    "statusCategory": {"key": "indeterminate"},
                },
            }
        ]
    }

    result = await update_jira_issue_status(
        {"issueKey": "MM-1125", "targetStatus": "In Progress"},
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "transitioned"
    assert result.outputs["transitionId"] == "31"
    assert result.outputs["confirmedStatus"]["name"] == "In Progress"
    assert [request.issue_key for request in service.get_issue_requests] == [
        "MM-1125",
        "MM-1125",
    ]
    assert service.transitions_requests[0].expand_fields is True
    assert service.transition_requests[0].transition_id == "31"


@pytest.mark.asyncio
async def test_update_jira_issue_status_skips_start_for_fully_implemented_assessment(
    tmp_path,
) -> None:
    assessment = tmp_path / "assessment.json"
    assessment.write_text('{"verdict": "FULLY_IMPLEMENTED"}', encoding="utf-8")
    service = _FakeJiraService()

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1125",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(assessment),
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "skipped"
    assert result.outputs["assessmentVerdict"] == "FULLY_IMPLEMENTED"
    assert service.get_issue_requests == []
    assert service.transition_requests == []


@pytest.mark.asyncio
async def test_update_jira_issue_status_reads_relative_assessment_from_previous_workspace(
    tmp_path,
) -> None:
    workspace = tmp_path / "repo"
    assessment = workspace / "artifacts" / "jira-implement-assessment.json"
    assessment.parent.mkdir(parents=True)
    assessment.write_text('{"verdict": "FULLY_IMPLEMENTED"}', encoding="utf-8")
    service = _FakeJiraService()

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1125",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": "artifacts/jira-implement-assessment.json",
            "previousOutputs": {"workspacePath": str(workspace)},
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "skipped"
    assert result.outputs["assessmentVerdict"] == "FULLY_IMPLEMENTED"
    assert service.get_issue_requests == []
    assert service.transition_requests == []


@pytest.mark.asyncio
async def test_update_jira_issue_status_uses_previous_assessment_text_when_artifact_missing(
    tmp_path,
) -> None:
    service = _FakeJiraService()
    service.issue_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "1", "name": "Backlog"}},
    }
    service.transition_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "3", "name": "In Progress"}},
    }
    service.transitions_response = {
        "transitions": [
            {"id": "31", "name": "In Progress", "to": {"name": "In Progress"}}
        ]
    }

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1125",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(tmp_path / "missing-assessment.json"),
            "previousOutputs": {
                "lastAssistantText": (
                    "Assessment complete for MM-1125: `PARTIALLY_IMPLEMENTED`."
                )
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "transitioned"
    assert service.transition_requests[0].transition_id == "31"


@pytest.mark.asyncio
async def test_update_jira_issue_status_accepts_bold_previous_assessment_verdict(
    tmp_path,
) -> None:
    service = _FakeJiraService()
    service.issue_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "1", "name": "Backlog"}},
    }
    service.transition_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "3", "name": "In Progress"}},
    }
    service.transitions_response = {
        "transitions": [
            {"id": "31", "name": "In Progress", "to": {"name": "In Progress"}}
        ]
    }

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1125",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(tmp_path / "missing-assessment.json"),
            "previousOutputs": {
                "lastAssistantText": "Assessment complete: **PARTIALLY_IMPLEMENTED**.",
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "transitioned"
    assert service.transition_requests[0].transition_id == "31"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "assistant_text",
    [
        "Assessment complete. Verdict: `PARTIALLY_IMPLEMENTED`.",
        "Assessment complete! Verdict: `PARTIALLY_IMPLEMENTED`.",
        "Assessment complete? Verdict: `PARTIALLY_IMPLEMENTED`.",
        "Assessment complete \u2014 verdict: **PARTIALLY_IMPLEMENTED**.",
    ],
)
async def test_update_jira_issue_status_accepts_sentence_previous_assessment_verdict(
    tmp_path,
    assistant_text,
) -> None:
    service = _FakeJiraService()
    service.issue_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "1", "name": "Backlog"}},
    }
    service.transition_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "3", "name": "In Progress"}},
    }
    service.transitions_response = {
        "transitions": [
            {"id": "31", "name": "In Progress", "to": {"name": "In Progress"}}
        ]
    }

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1125",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(tmp_path / "missing-assessment.json"),
            "previousOutputs": {
                "lastAssistantText": assistant_text,
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "transitioned"
    assert service.transition_requests[0].transition_id == "31"


@pytest.mark.asyncio
async def test_check_jira_blockers_preserves_em_dash_previous_assessment_verdict() -> None:
    service = _FakeJiraService()
    service.issue_responses["MM-1137"] = {
        "key": "MM-1137",
        "fields": {"status": {"id": "1", "name": "Backlog"}, "issuelinks": []},
    }

    result = await check_jira_blockers(
        {
            "targetIssueKey": "MM-1137",
            "assessmentArtifactPath": "artifacts/missing-assessment.json",
            "previousOutputs": {
                "operator_summary": (
                    "## Assessment complete \u2014 verdict: "
                    "**PARTIALLY_IMPLEMENTED**"
                ),
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "continue"
    assert result.outputs["assessmentVerdict"] == "PARTIALLY_IMPLEMENTED"


@pytest.mark.asyncio
async def test_update_jira_issue_status_accepts_issue_key_before_bold_verdict(
    tmp_path,
) -> None:
    service = _FakeJiraService()
    service.issue_responses["MM-1133"] = {
        "key": "MM-1133",
        "fields": {"status": {"id": "1", "name": "Backlog"}},
    }
    service.transition_responses["MM-1133"] = {
        "key": "MM-1133",
        "fields": {"status": {"id": "3", "name": "In Progress"}},
    }
    service.transitions_response = {
        "transitions": [
            {"id": "31", "name": "In Progress", "to": {"name": "In Progress"}}
        ]
    }

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1133",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(tmp_path / "missing-assessment.json"),
            "previousOutputs": {
                "lastAssistantText": (
                    "Assessment complete: `MM-1133` is "
                    "**PARTIALLY_IMPLEMENTED**."
                ),
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "transitioned"
    assert service.transition_requests[0].transition_id == "31"


@pytest.mark.asyncio
async def test_update_jira_issue_status_accepts_newline_assessment_verdict(
    tmp_path,
) -> None:
    service = _FakeJiraService()
    service.issue_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "1", "name": "Backlog"}},
    }
    service.transition_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "3", "name": "In Progress"}},
    }
    service.transitions_response = {
        "transitions": [
            {"id": "31", "name": "In Progress", "to": {"name": "In Progress"}}
        ]
    }

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1125",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(tmp_path / "missing-assessment.json"),
            "previousOutputs": {
                "lastAssistantText": (
                    "Assessment complete:\n**PARTIALLY_IMPLEMENTED**."
                ),
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "transitioned"
    assert service.transition_requests[0].transition_id == "31"


@pytest.mark.asyncio
async def test_update_jira_issue_status_does_not_parse_negated_verdict(
    tmp_path,
) -> None:
    service = _FakeJiraService()

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1125",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(tmp_path / "missing-assessment.json"),
            "previousOutputs": {
                "lastAssistantText": "Assessment complete: NOT FULLY_IMPLEMENTED.",
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert "assessment verdict" in result.outputs["summary"]
    assert service.get_issue_requests == []
    assert service.transition_requests == []


@pytest.mark.asyncio
async def test_update_jira_issue_status_accepts_underscore_wrapped_assessment_verdict(
    tmp_path,
) -> None:
    service = _FakeJiraService()
    service.issue_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "1", "name": "Backlog"}},
    }
    service.transition_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "3", "name": "In Progress"}},
    }
    service.transitions_response = {
        "transitions": [
            {"id": "31", "name": "In Progress", "to": {"name": "In Progress"}}
        ]
    }

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1125",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(tmp_path / "missing-assessment.json"),
            "previousOutputs": {
                "lastAssistantText": "Assessment complete: __PARTIALLY_IMPLEMENTED__.",
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "transitioned"
    assert service.transition_requests[0].transition_id == "31"


@pytest.mark.asyncio
async def test_update_jira_issue_status_accepts_single_quoted_previous_verdict(
    tmp_path,
) -> None:
    service = _FakeJiraService()
    service.issue_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "1", "name": "Backlog"}},
    }
    service.transition_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "3", "name": "In Progress"}},
    }
    service.transitions_response = {
        "transitions": [
            {"id": "31", "name": "In Progress", "to": {"name": "In Progress"}}
        ]
    }

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1125",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(tmp_path / "missing-assessment.json"),
            "previousOutputs": {
                "summary": "{'verdict': 'PARTIALLY_IMPLEMENTED'}",
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "transitioned"
    assert service.transition_requests[0].transition_id == "31"


@pytest.mark.asyncio
async def test_update_jira_issue_status_accepts_concise_previous_verdict(
    tmp_path,
) -> None:
    service = _FakeJiraService()
    service.issue_responses["MM-1130"] = {
        "key": "MM-1130",
        "fields": {"status": {"id": "1", "name": "Backlog"}},
    }
    service.transition_responses["MM-1130"] = {
        "key": "MM-1130",
        "fields": {"status": {"id": "3", "name": "In Progress"}},
    }
    service.transitions_response = {
        "transitions": [
            {"id": "31", "name": "In Progress", "to": {"name": "In Progress"}}
        ]
    }

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1130",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(tmp_path / "missing-assessment.json"),
            "previousOutputs": {
                "lastAssistantText": "Verdict: `PARTIALLY_IMPLEMENTED`.",
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "transitioned"
    assert service.transition_requests[0].transition_id == "31"


@pytest.mark.asyncio
async def test_update_jira_issue_status_accepts_markdown_heading_previous_verdict(
    tmp_path,
) -> None:
    service = _FakeJiraService()
    service.issue_responses["MM-1139"] = {
        "key": "MM-1139",
        "fields": {"status": {"id": "1", "name": "Backlog"}},
    }
    service.transition_responses["MM-1139"] = {
        "key": "MM-1139",
        "fields": {"status": {"id": "3", "name": "In Progress"}},
    }
    service.transitions_response = {
        "transitions": [
            {"id": "31", "name": "In Progress", "to": {"name": "In Progress"}}
        ]
    }

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1139",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(tmp_path / "missing-assessment.json"),
            "previousOutputs": {
                "summary": (
                    "Both handoff artifacts are written and valid. "
                    "Here is the assessment result.\n\n"
                    "  ## Verdict: NOT_IMPLEMENTED\n\n"
                    "Evidence follows."
                ),
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "transitioned"
    assert service.transition_requests[0].transition_id == "31"


@pytest.mark.asyncio
async def test_update_jira_issue_status_blocks_for_malformed_assessment_artifact(
    tmp_path,
) -> None:
    assessment = tmp_path / "assessment.json"
    assessment.write_text('{"summary": "assessment incomplete"}', encoding="utf-8")
    service = _FakeJiraService()

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1125",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(assessment),
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert "assessment verdict" in result.outputs["summary"]
    assert service.get_issue_requests == []
    assert service.transition_requests == []


@pytest.mark.asyncio
async def test_update_jira_issue_status_blocks_when_transition_is_unavailable() -> None:
    service = _FakeJiraService()
    service.issue_responses["MM-1125"] = {
        "key": "MM-1125",
        "fields": {"status": {"id": "1", "name": "Backlog"}},
    }
    service.transitions_response = {
        "transitions": [{"id": "41", "name": "Done", "to": {"name": "Done"}}]
    }

    result = await update_jira_issue_status(
        {"issueKey": "MM-1125", "targetStatus": "In Progress"},
        jira_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["availableTransitions"][0]["transitionId"] == "41"
    assert service.transition_requests == []


@pytest.mark.asyncio
async def test_create_jira_issues_from_inline_story_breakdown():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "labels": ["moonmind"],
                },
            },
            "stories": [
                {
                    "id": "STORY-001",
                    "summary": "Create proposal intent records",
                    "description": "As an operator, I can track proposal intent.",
                    "acceptanceCriteria": ["Intent is visible", "Intent is auditable"],
                }
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["storyOutput"]["status"] == "jira_created"
    assert result.outputs["jira"]["createdIssues"][0]["issueKey"] == "MM-1"
    assert len(service.requests) == 1
    request = service.requests[0]
    assert request.project_key == "MM"
    assert request.issue_type_id == "10001"
    assert request.summary == "Create proposal intent records"
    assert request.fields["labels"] == ["moonmind"]
    assert "Intent is visible" in request.description

@pytest.mark.asyncio
async def test_create_jira_issues_skips_completed_and_blocks_unverifiable_stories():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {"mode": "jira"},
            "stories": [
                {
                    "id": "STORY-001",
                    "summary": "Already complete",
                    "implementationStatus": "fully_implemented",
                    "implementedEvidence": [
                        {"requirement": "DESIGN-REQ-001", "evidence": "tests pass"}
                    ],
                    "jiraCreation": {
                        "action": "skip",
                        "reason": "All criteria already have evidence.",
                    },
                },
                {
                    "id": "STORY-002",
                    "summary": "Needs manual review",
                    "implementationStatus": "unverifiable",
                    "jiraCreation": {
                        "action": "manual_review",
                        "reason": "External behavior cannot be verified locally.",
                    },
                },
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["storyOutput"]["status"] == "jira_noop"
    assert result.outputs["storyOutput"]["storyCount"] == 2
    assert result.outputs["storyOutput"]["eligibleStoryCount"] == 0
    assert result.outputs["storyOutput"]["createdCount"] == 0
    assert result.outputs["storyOutput"]["skippedStories"][0]["storyId"] == "STORY-001"
    assert result.outputs["storyOutput"]["blockedStories"][0]["storyId"] == "STORY-002"
    assert result.outputs["jira"]["issueMappings"] == []
    assert service.requests == []


@pytest.mark.asyncio
async def test_create_jira_issues_accepts_provider_neutral_issue_creation():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {"projectKey": "MM", "issueTypeId": "10001"},
            },
            "stories": [
                {
                    "id": "STORY-001",
                    "summary": "Already complete through neutral contract",
                    "implementationStatus": "fully_implemented",
                    "issueCreation": {
                        "action": "skip",
                        "reason": "Already implemented.",
                    },
                },
                {
                    "id": "STORY-002",
                    "summary": "Create through neutral contract",
                    "description": "Create a Jira issue from issueCreation.",
                    "issueCreation": {"action": "create_issue"},
                },
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    assert result.outputs["storyOutput"]["skippedStories"][0]["issueCreationAction"] == (
        "skip"
    )
    assert result.outputs["storyOutput"]["skippedStories"][0]["jiraCreationAction"] == (
        "skip"
    )
    assert [request.summary for request in service.requests] == [
        "Create through neutral contract"
    ]


@pytest.mark.asyncio
async def test_create_jira_issues_narrows_partially_implemented_story_to_remaining_work():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {"projectKey": "MM", "issueTypeId": "10001"},
            },
            "stories": [
                {
                    "id": "STORY-001",
                    "summary": "Publish report bundles",
                    "description": "Original broad story.",
                    "implementationStatus": "partially_implemented",
                    "implementedEvidence": [
                        {
                            "requirement": "DESIGN-REQ-001",
                            "status": "met",
                            "evidence": "Report artifacts already persist.",
                        }
                    ],
                    "remainingWork": {
                        "summary": "Complete report bundle download UX",
                        "description": "Add the missing dashboard link.",
                        "acceptanceCriteria": ["Bundle download link is visible."],
                        "requirements": ["Expose bundle URL in the workflow details UI."],
                    },
                    "jiraCreation": {
                        "action": "create_remaining_work_issue",
                        "reason": "Only the UI work remains.",
                    },
                }
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["storyOutput"]["status"] == "jira_created"
    assert result.outputs["storyOutput"]["storyCount"] == 1
    assert result.outputs["storyOutput"]["eligibleStoryCount"] == 1
    assert result.outputs["storyOutput"]["partialStoriesAdjusted"][0]["storyId"] == (
        "STORY-001"
    )
    assert len(service.requests) == 1
    request = service.requests[0]
    assert request.summary == "Complete report bundle download UX"
    assert "Add the missing dashboard link." in request.description
    assert "Already Implemented Evidence" in request.description
    assert "Report artifacts already persist." in request.description
    assert "Original Story Scope" in request.description
    assert "Original broad story." in request.description
    assert "Bundle download link is visible." in request.description

@pytest.mark.asyncio
async def test_create_jira_issues_blocks_partial_story_without_remaining_work():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {"projectKey": "MM", "issueTypeId": "10001"},
            },
            "stories": [
                {
                    "id": "STORY-001",
                    "summary": "Partially done without a remainder",
                    "implementationStatus": "partially_implemented",
                    "acceptanceCriteria": ["Original completed criterion."],
                    "requirements": ["Original completed requirement."],
                }
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["storyOutput"]["status"] == "jira_noop"
    assert result.outputs["storyOutput"]["blockedStories"][0]["storyId"] == "STORY-001"
    assert "remainingWork" in result.outputs["storyOutput"]["blockedStories"][0]["reason"]
    assert result.outputs["storyOutput"]["partialStoriesAdjusted"] == []
    assert service.requests == []

@pytest.mark.asyncio
async def test_create_jira_issues_drops_original_criteria_when_partial_remaining_lists_missing():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {"projectKey": "MM", "issueTypeId": "10001"},
            },
            "stories": [
                {
                    "id": "STORY-001",
                    "summary": "Partially done",
                    "description": "Original story description.",
                    "implementationStatus": "partially_implemented",
                    "acceptanceCriteria": ["Original completed criterion."],
                    "requirements": ["Original completed requirement."],
                    "remainingWork": {
                        "summary": "Complete the missing behavior",
                        "description": "Implement only the missing behavior.",
                    },
                }
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    request = service.requests[0]
    assert request.summary == "Complete the missing behavior"
    assert "Implement only the missing behavior." in request.description
    assert "Acceptance Criteria\nOriginal completed criterion." not in request.description
    assert "Requirements\nOriginal completed requirement." not in request.description


@pytest.mark.asyncio
async def test_create_github_issues_from_reconciled_story_breakdown():
    service = _FakeGitHubService()

    result = await create_github_issues_from_stories(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "workflowId": "mm-1068-run",
            "github": {
                "labels": ["moonmind", "MM-1068"],
                "token": "workflow-payload-token",
                "githubToken": "workflow-payload-token-alias",
            },
            "stories": {
                "source": {
                    "referencePath": "docs/Workflows/SkillAndPlanContracts.md",
                    "sourceDocumentClass": "canonical-declarative",
                },
                "stories": [
                    {
                        "id": "STORY-001",
                        "summary": "Create GitHub issue story output",
                        "description": "As a breakdown workflow, create GitHub issues.",
                        "acceptanceCriteria": ["One issue is created."],
                        "sourceReference": {
                            "path": "docs/Workflows/SkillAndPlanContracts.md",
                            "title": "MM-1063: Update Presets",
                            "sections": [
                                "Add GitHub Issue creation from MoonSpec breakdowns"
                            ],
                            "claimIds": ["DESIGN-REQ-007"],
                            "coverageIds": ["DESIGN-REQ-014"],
                            "sourceIssueKey": "MM-1063",
                        },
                    },
                    {
                        "id": "STORY-002",
                        "summary": "Already implemented",
                        "implementationStatus": "fully_implemented",
                        "jiraCreation": {"action": "skip"},
                    },
                    {
                        "id": "STORY-003",
                        "summary": "Unverifiable story",
                        "implementationStatus": "unverifiable",
                        "jiraCreation": {"action": "manual_review"},
                    },
                ],
            },
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["storyOutput"]["status"] == "github_partial"
    assert result.outputs["storyOutput"]["storyCount"] == 3
    assert result.outputs["storyOutput"]["eligibleStoryCount"] == 1
    assert result.outputs["storyOutput"]["createdCount"] == 1
    assert result.outputs["storyOutput"]["dependencyMode"] == "none"
    assert result.outputs["storyOutput"]["dependencyCount"] == 0
    assert result.outputs["storyOutput"]["skippedStories"][0]["storyId"] == "STORY-002"
    assert result.outputs["storyOutput"]["blockedStories"][0]["storyId"] == "STORY-003"
    assert result.outputs["github"]["issueMappings"] == [
        {
            "storyId": "STORY-001",
            "storyIndex": 1,
            "summary": "Create GitHub issue story output",
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": "1",
            "issueUrl": "https://github.com/MoonLadderStudios/MoonMind/issues/1",
            "sourceDesignPath": "docs/Workflows/SkillAndPlanContracts.md",
            "sourceTitle": "MM-1063: Update Presets",
            "sourceClaimIds": ["DESIGN-REQ-007"],
            "sourceSections": [
                "Add GitHub Issue creation from MoonSpec breakdowns"
            ],
            "sourceIssueKey": "MM-1063",
            "coverageIds": ["DESIGN-REQ-014"],
        }
    ]

    request = service.create_issue_requests[0]
    assert request["repo"] == "MoonLadderStudios/MoonMind"
    assert request["title"] == "Create GitHub issue story output"
    assert request["labels"] == [
        "moonmind",
        "MM-1068",
        "moonmind-workflow-mm-1068-run",
    ]
    assert "Source Document: docs/Workflows/SkillAndPlanContracts.md" in request["body"]
    assert "Source Title: MM-1063: Update Presets" in request["body"]
    assert "Source Sections:" in request["body"]
    assert "DESIGN-REQ-007" in request["body"]
    assert "DESIGN-REQ-014" in request["body"]
    assert "One issue is created." in request["body"]
    assert request["github_token"] is None


@pytest.mark.asyncio
async def test_create_github_issues_prefers_provider_neutral_issue_creation():
    service = _FakeGitHubService()

    result = await create_github_issues_from_stories(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "stories": [
                {
                    "id": "STORY-001",
                    "summary": "Already complete through issueCreation",
                    "implementationStatus": "fully_implemented",
                    "issueCreation": {
                        "action": "skip",
                        "reason": "Already implemented.",
                    },
                    "jiraCreation": {
                        "action": "create_issue",
                        "reason": "Legacy alias should not override issueCreation.",
                    },
                },
                {
                    "id": "STORY-002",
                    "summary": "Create this story",
                    "description": "Needs a GitHub issue.",
                    "issueCreation": {"action": "create_issue"},
                },
            ],
        },
        github_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "github_created"
    assert result.outputs["storyOutput"]["skippedStories"] == [
        {
            "storyId": "STORY-001",
            "storyIndex": 1,
            "summary": "Already complete through issueCreation",
            "implementationStatus": "fully_implemented",
            "issueCreationAction": "skip",
            "jiraCreationAction": "skip",
            "reason": "Already implemented.",
        }
    ]
    assert [request["title"] for request in service.create_issue_requests] == [
        "Create this story"
    ]


@pytest.mark.asyncio
async def test_create_github_issues_narrows_partial_story_to_remaining_work():
    service = _FakeGitHubService()

    result = await create_github_issues_from_stories(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "stories": [
                {
                    "id": "STORY-001",
                    "summary": "Original broad GitHub story",
                    "description": "Original work.",
                    "implementationStatus": "partially_implemented",
                    "implementedEvidence": [
                        {"requirement": "DESIGN-REQ-007", "evidence": "Tool exists."}
                    ],
                    "remainingWork": {
                        "summary": "Complete GitHub workflow mapping",
                        "description": "Add workflow creation outputs.",
                        "acceptanceCriteria": ["Workflow mappings are returned."],
                    },
                }
            ],
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["storyOutput"]["status"] == "github_created"
    assert result.outputs["storyOutput"]["partialStoriesAdjusted"][0]["storyId"] == (
        "STORY-001"
    )
    request = service.create_issue_requests[0]
    assert request["title"] == "Complete GitHub workflow mapping"
    assert "Add workflow creation outputs." in request["body"]
    assert "Already Implemented Evidence" in request["body"]
    assert "Tool exists." in request["body"]
    assert "Original Story Scope" in request["body"]
    assert "Workflow mappings are returned." in request["body"]


@pytest.mark.asyncio
async def test_create_github_issues_uses_story_output_github_and_previous_artifact():
    service = _FakeGitHubService()
    artifact_reads: list[str] = []

    async def read_artifact(ref: str) -> dict[str, Any]:
        artifact_reads.append(ref)
        return {
            "source": {"referencePath": "docs/Designs/GitHubBreakdown.md"},
            "stories": [
                {
                    "id": "STORY-001",
                    "summary": "Artifact GitHub story",
                    "description": "Create this from a previous breakdown artifact.",
                    "sourceReference": {
                        "claimIds": ["claim:github-breakdown"],
                        "sourceIssueKey": "TargetOrg/TargetRepo#77",
                    },
                }
            ],
        }

    result = await create_github_issues_from_stories(
        {
            "storyOutput": {
                "github": {
                    "repository": "TargetOrg/TargetRepo",
                    "traceability": {"sourceIssueKey": "TargetOrg/TargetRepo#77"},
                },
            },
            "previousOutputs": {
                "storyOutput": {
                    "storyBreakdownArtifactRef": "art_previous_github_breakdown"
                }
            },
        },
        github_service_factory=lambda: service,
        artifact_reader=read_artifact,
    )

    github = result.outputs["github"]
    assert result.outputs["storyOutput"]["status"] == "github_created"
    assert github["issueMappings"][0]["repository"] == "TargetOrg/TargetRepo"
    assert github["issueMappings"][0]["sourceDesignPath"] == (
        "docs/Designs/GitHubBreakdown.md"
    )
    assert artifact_reads == ["art_previous_github_breakdown"]
    assert service.create_issue_requests[0]["repo"] == "TargetOrg/TargetRepo"
    assert "Source Issue: TargetOrg/TargetRepo#77" in service.create_issue_requests[0][
        "body"
    ]


@pytest.mark.asyncio
async def test_create_github_issue_workflows_from_issue_mappings():
    creator = _FakeExecutionCreator()

    result = await create_github_issue_implement_workflows_from_issue_mappings(
        {
            "github": {
                "issueMappings": [
                    {
                        "storyId": "STORY-001",
                        "storyIndex": 1,
                        "summary": "First story",
                        "repository": "MoonLadderStudios/MoonMind",
                        "issueNumber": "11",
                        "sourceDesignPath": "docs/Workflows/SkillAndPlanContracts.md",
                        "sourceClaimIds": ["DESIGN-REQ-007"],
                    },
                    {
                        "storyId": "STORY-002",
                        "storyIndex": 2,
                        "summary": "Second story",
                        "repository": "MoonLadderStudios/MoonMind",
                        "issueNumber": "12",
                    },
                ]
            },
            "githubOrchestration": {
                "traceability": {
                    "sourceIssueKey": "MM-1063",
                    "sourceBriefRef": "MM-1068",
                },
                "task": {
                    "runtime": {"mode": "codex"},
                    "inputs": {"run_verify": False},
                    "publish": {"mode": "pr"},
                },
            },
        },
        execution_creator=creator,
    )

    orchestration = result.outputs["githubWorkflowOrchestration"]
    assert orchestration["status"] == "completed"
    assert orchestration["storyCount"] == 2
    assert orchestration["createdWorkflowCount"] == 2
    assert orchestration["dependencyMode"] == "workflow_linear_chain"
    assert orchestration["dependencyCount"] == 1
    assert orchestration["workflows"][0]["githubIssueNumber"] == "11"
    assert orchestration["workflows"][1]["dependsOn"] == ["mm:story-1"]

    first_request = creator.requests[0]
    assert first_request["integration"] == "github"
    assert first_request["idempotency_key"].startswith(
        "github-issue-implement:MM-1063:STORY-001:"
    )
    workflow = first_request["initial_parameters"]["workflow"]
    assert workflow["taskTemplate"]["slug"] == "github-issue-implement"
    assert workflow["inputs"]["github_issue"] == {
        "repository": "MoonLadderStudios/MoonMind",
        "number": 11,
        "title": "First story",
    }
    assert workflow["inputs"]["run_verify"] is False
    assert workflow["inputs"]["github_issue_ref"] == "MoonLadderStudios/MoonMind#11"
    assert "Source issue: MM-1063." in workflow["instructions"]
    assert "Source canonical claim IDs: DESIGN-REQ-007." in workflow["instructions"]
    assert "breakdown task" not in workflow["instructions"]
    assert "breakdown workflow" in workflow["instructions"]


@pytest.mark.asyncio
async def test_create_github_issue_workflows_mark_remaining_after_failure():
    creator = _FakeExecutionCreator(fail_at=2)

    result = await create_github_issue_implement_workflows_from_issue_mappings(
        {
            "github": {
                "issueMappings": [
                    {
                        "storyId": "STORY-001",
                        "storyIndex": 1,
                        "summary": "First",
                        "issueNumber": "101",
                    },
                    {
                        "storyId": "STORY-002",
                        "storyIndex": 2,
                        "summary": "Second",
                        "issueNumber": "102",
                    },
                    {
                        "storyId": "STORY-003",
                        "storyIndex": 3,
                        "summary": "Third",
                        "issueNumber": "103",
                    },
                ],
            },
            "githubOrchestration": {
                "task": {"repository": "MoonLadderStudios/MoonMind"},
                "traceability": {"sourceIssueKey": "MM-1063"},
            },
        },
        execution_creator=creator,
    )

    orchestration = result.outputs["githubWorkflowOrchestration"]
    assert orchestration["status"] == "partial"
    assert orchestration["createdWorkflowCount"] == 1
    assert orchestration["failures"][0]["storyId"] == "STORY-002"
    assert orchestration["failures"][0]["errorCode"] == "workflow_creation_failed"
    assert orchestration["skippedStories"] == [
        {
            "storyId": "STORY-003",
            "storyIndex": 3,
            "repository": "MoonLadderStudios/MoonMind",
            "githubIssueNumber": "103",
            "errorCode": "dependency_not_created",
            "message": "Earlier downstream workflow creation failed.",
        }
    ]


def test_github_downstream_workflow_payload_propagates_fallback_repository():
    _title, task = story_tools._github_downstream_workflow_payload(
        mapping={
            "storyId": "STORY-001",
            "summary": "Fallback repo issue",
            "issueNumber": "101",
        },
        task_payload={"repository": "MoonLadderStudios/MoonMind"},
        traceability={},
        depends_on=[],
        source_issue_key="",
        target_preset="implement",
    )

    assert task["inputs"]["github_issue"] == {
        "repository": "MoonLadderStudios/MoonMind",
        "number": 101,
        "title": "Fallback repo issue",
    }
    assert task["inputs"]["github_issue_ref"] == "MoonLadderStudios/MoonMind#101"


@pytest.mark.asyncio
async def test_create_github_issue_orchestrate_workflows_uses_orchestrate_preset():
    creator = _FakeExecutionCreator()

    result = await create_github_issue_orchestrate_workflows_from_issue_mappings(
        {
            "issueMappings": [
                {
                    "storyId": "STORY-001",
                    "storyIndex": 1,
                    "summary": "Orchestrate story",
                    "repository": "MoonLadderStudios/MoonMind",
                    "issueNumber": "21",
                }
            ],
            "traceability": {"sourceIssueKey": "MM-1063"},
        },
        execution_creator=creator,
    )

    assert result.outputs["githubWorkflowOrchestration"]["createdWorkflowCount"] == 1
    workflow = creator.requests[0]["initial_parameters"]["workflow"]
    assert workflow["taskTemplate"]["slug"] == "github-issue-orchestrate"
    assert workflow["inputs"]["github_issue_ref"] == "MoonLadderStudios/MoonMind#21"


def test_register_story_output_tool_handlers_includes_github_story_tools():
    dispatcher = _RecordingDispatcher()

    story_tools.register_story_output_tool_handlers(dispatcher)

    assert "jira.update_issue_status" in dispatcher.skills
    assert "story.create_github_issues" in dispatcher.skills
    assert "story.create_github_issue_implement_workflows" in dispatcher.skills
    assert "story.create_github_issue_orchestrate_workflows" in dispatcher.skills

@pytest.mark.asyncio
async def test_load_jira_preset_brief_uses_trusted_jira_issue_payload():
    service = _FakeJiraService()
    service.issue_responses["MM-657"] = {
        "key": "MM-657",
        "self": "https://jira.example/rest/api/3/issue/657",
        "names": {"customfield_10042": "Acceptance Criteria"},
        "fields": {
            "summary": "Settings HTTP API surface",
            "description": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": "Expose catalog and audit endpoints.",
                            }
                        ],
                    }
                ],
            },
            "customfield_10042": "Given an operator\nThen settings are auditable",
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Story"},
            "assignee": {"displayName": "Nate"},
        },
    }

    result = await load_jira_preset_brief(
        {
            "issueKey": "MM-657",
            "artifactPath": "artifacts/jira-implement-brief.json",
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert service.get_issue_requests[0].issue_key == "MM-657"
    assert service.get_issue_requests[0].expand == ["names"]
    assert result.outputs["trustedSource"] == "moonmind.jira.get_issue"
    assert result.outputs["jiraIssueKey"] == "MM-657"
    assert result.outputs["artifactPath"] == "artifacts/jira-implement-brief.json"
    assert result.outputs["presetBrief"] == result.outputs["jiraPresetBrief"]
    assert "MM-657: Settings HTTP API surface" in result.outputs["jiraPresetBrief"]
    assert "Expose catalog and audit endpoints." in result.outputs["jiraPresetBrief"]
    assert "Given an operator" in result.outputs["jiraPresetBrief"]
    assert result.outputs["jiraIssue"]["status"] == "In Progress"


@pytest.mark.asyncio
async def test_load_jira_preset_brief_ignores_criteria_only_custom_fields():
    service = _FakeJiraService()
    service.issue_responses["MM-657"] = {
        "key": "MM-657",
        "names": {
            "customfield_10001": "Exit Criteria",
            "customfield_10042": "Acceptance Criteria",
        },
        "fields": {
            "summary": "Settings HTTP API surface",
            "description": "Expose catalog and audit endpoints.",
            "customfield_10001": "Wrong criteria text",
            "customfield_10042": "Given an operator\nThen settings are auditable",
        },
    }

    result = await load_jira_preset_brief(
        {"issueKey": "MM-657"},
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert "Given an operator" in result.outputs["jiraPresetBrief"]
    assert "Wrong criteria text" not in result.outputs["jiraPresetBrief"]


@pytest.mark.asyncio
async def test_load_jira_preset_brief_resolves_existing_source_design_path(tmp_path):
    source_path = "docs/ManagedAgents/ManagedRuntimeCleanup.md"
    source_file = tmp_path / source_path
    source_file.parent.mkdir(parents=True)
    source_file.write_text("# Managed Runtime Cleanup\n", encoding="utf-8")
    service = _FakeJiraService()
    service.issue_responses["MM-940"] = {
        "key": "MM-940",
        "fields": {
            "summary": f"Implement {source_path}",
            "description": "Break the canonical cleanup design into work.",
        },
    }

    result = await load_jira_preset_brief(
        {"issueKey": "MM-940", "repositoryRoot": str(tmp_path)},
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["resolvedSourceDesignPath"] == source_path
    resolution = result.outputs["sourceResolution"]
    assert resolution["status"] == "resolved"
    assert resolution["selectedPath"] == source_path
    assert resolution["candidatePaths"] == [
        {
            "path": source_path,
            "sourceField": "jira.summary",
            "sourceFields": ["jira.summary", "jira.presetBrief"],
            "exists": True,
        }
    ]


@pytest.mark.asyncio
async def test_load_jira_preset_brief_resolves_single_path_without_worker_cwd_validation():
    service = _FakeJiraService()
    source_path = "docs/ExternalProject/DesiredState.md"
    service.issue_responses["MM-941"] = {
        "key": "MM-941",
        "fields": {
            "summary": f"Implement {source_path}",
            "description": "Break the external repository design into work.",
        },
    }

    result = await load_jira_preset_brief(
        {"issueKey": "MM-941"},
        jira_service_factory=lambda: service,
    )

    assert result.outputs["resolvedSourceDesignPath"] == source_path
    resolution = result.outputs["sourceResolution"]
    assert resolution["status"] == "resolved"
    assert resolution["selectedPath"] == source_path
    assert resolution["candidatePaths"] == [
        {
            "path": source_path,
            "sourceField": "jira.summary",
            "sourceFields": ["jira.summary", "jira.presetBrief"],
            "exists": None,
        }
    ]
    assert "repository-root validation not run" in resolution["reason"]


@pytest.mark.asyncio
async def test_load_jira_preset_brief_reports_ambiguous_source_paths(tmp_path):
    first_path = "docs/ManagedAgents/ManagedRuntimeCleanup.md"
    second_path = "docs/ManagedAgents/ManagedAgentArchitecture.md"
    for source_path in (first_path, second_path):
        source_file = tmp_path / source_path
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("# Source\n", encoding="utf-8")
    service = _FakeJiraService()
    service.issue_responses["MM-940"] = {
        "key": "MM-940",
        "fields": {
            "summary": "Managed runtime cleanup",
            "description": f"Use {first_path} and compare {second_path}.",
        },
    }

    result = await load_jira_preset_brief(
        {"issueKey": "MM-940", "repositoryRoot": str(tmp_path)},
        jira_service_factory=lambda: service,
    )

    assert "resolvedSourceDesignPath" not in result.outputs
    resolution = result.outputs["sourceResolution"]
    assert resolution["status"] == "ambiguous"
    assert resolution["selectedPath"] == ""
    assert [item["path"] for item in resolution["candidatePaths"]] == [
        first_path,
        second_path,
    ]


@pytest.mark.asyncio
async def test_create_jira_issues_resolves_issue_type_name_from_story_breakdown_source():
    service = _FakeJiraService()
    breakdown = {
        "source": {
            "referencePath": "docs/Designs/RuntimeTypes.md",
            "title": "Runtime Types",
        },
        "stories": [
            {
                "id": "STORY-001",
                "summary": "Create proposal intent records",
                "description": "As an operator, I can track proposal intent.",
                "sourceReference": {
                    "sections": ["Section 1"],
                    "claimIds": ["docs/Designs/RuntimeTypes.md#section-1-001"],
                    "coverageIds": ["DESIGN-REQ-001"],
                },
            }
        ],
    }

    async def fetcher(_repo: str, _ref: str, _path: str) -> str:
        import json

        return json.dumps(breakdown)

    result = await create_jira_issues_from_stories(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "targetBranch": "breakdown-branch",
            "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeName": "Story",
                    "dependencyMode": "none",
                },
            },
        },
        jira_service_factory=lambda: service,
        story_fetcher=fetcher,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    request = service.requests[0]
    assert request.issue_type_id == "10005"
    assert request.description.startswith(
        "Source Reference\nSource Document: docs/Designs/RuntimeTypes.md"
    )
    assert "Source Document: docs/Designs/RuntimeTypes.md" in request.description
    assert "Section 1" in request.description
    assert "Canonical Claim IDs:" in request.description
    assert "docs/Designs/RuntimeTypes.md#section-1-001" in request.description
    assert "DESIGN-REQ-001" in request.description

@pytest.mark.asyncio
async def test_create_jira_issues_reads_story_breakdown_artifact_before_repo_path():
    service = _FakeJiraService()
    breakdown = {
        "source": {
            "referencePath": "docs/Designs/RuntimeTypes.md",
            "title": "Runtime Types",
        },
        "stories": [
            {
                "id": "STORY-001",
                "summary": "Create artifact-backed Jira story",
                "description": "As an operator, I can export from a durable artifact.",
                "sourceReference": {
                    "sections": ["Section 1"],
                    "claimIds": ["docs/Designs/RuntimeTypes.md#section-1-001"],
                    "coverageIds": ["DESIGN-REQ-001"],
                },
            }
        ],
    }
    fetch_calls: list[tuple[str, str, str]] = []
    artifact_reads: list[str] = []

    async def artifact_reader(ref: str) -> bytes:
        artifact_reads.append(ref)
        return json.dumps(breakdown).encode("utf-8")

    async def fetcher(repo: str, ref: str, path: str) -> str:
        fetch_calls.append((repo, ref, path))
        raise AssertionError("repo fetch should not run when artifact ref is present")

    result = await create_jira_issues_from_stories(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "targetBranch": "breakdown-branch",
            "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
            "storyOutput": {
                "mode": "jira",
                "storyBreakdownArtifactRef": "art_story_breakdown",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeName": "Story",
                    "dependencyMode": "none",
                },
            },
        },
        jira_service_factory=lambda: service,
        story_fetcher=fetcher,
        artifact_reader=artifact_reader,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    assert artifact_reads == ["art_story_breakdown"]
    assert fetch_calls == []
    assert service.requests[0].summary == "Create artifact-backed Jira story"

@pytest.mark.asyncio
async def test_create_jira_issues_requires_artifact_handoff_when_marked():
    fetch_calls: list[tuple[str, str, str]] = []

    async def fetcher(repo: str, ref: str, path: str) -> str:
        fetch_calls.append((repo, ref, path))
        raise AssertionError("repo fetch should not run for artifact handoff")

    with pytest.raises(ValueError, match="durable storyBreakdownArtifactRef"):
        await create_jira_issues_from_stories(
            {
                "repository": "MoonLadderStudios/MoonMind",
                "targetBranch": "breakdown-branch",
                "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
                "storyOutput": {
                    "mode": "jira",
                    "handoff": "artifact",
                    "requiresStoryBreakdownArtifactRef": True,
                    "fallback": "fail",
                    "jira": {
                        "projectKey": "MM",
                        "issueTypeName": "Story",
                        "dependencyMode": "none",
                    },
                },
            },
            story_fetcher=fetcher,
        )

    assert fetch_calls == []

@pytest.mark.asyncio
async def test_create_jira_issues_reads_story_payload_from_previous_outputs():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "none",
                },
            },
        },
        {
            "previousOutputs": {
                "storyOutput": {
                    "stories": [
                        {
                            "id": "STORY-001",
                            "summary": "Create previous-output Jira story",
                            "description": "As an operator, I can reuse prior output.",
                            "sourceReference": {
                                "path": "docs/Designs/RuntimeTypes.md",
                                "claimIds": [
                                    "docs/Designs/RuntimeTypes.md#section-1-001"
                                ],
                            },
                        }
                    ]
                }
            }
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    assert service.requests[0].summary == "Create previous-output Jira story"

@pytest.mark.asyncio
async def test_create_jira_issues_reads_story_artifact_ref_from_previous_outputs():
    service = _FakeJiraService()
    breakdown = {
        "source": {"referencePath": "docs/Designs/RuntimeTypes.md"},
        "stories": [
            {
                "id": "STORY-001",
                "summary": "Create previous-output artifact Jira story",
                "description": "As an operator, I can reuse a durable handoff.",
                "sourceReference": {
                    "path": "docs/Designs/RuntimeTypes.md",
                    "claimIds": ["docs/Designs/RuntimeTypes.md#section-1-001"],
                },
            }
        ],
    }
    fetch_calls: list[tuple[str, str, str]] = []
    artifact_reads: list[str] = []

    async def artifact_reader(ref: str) -> bytes:
        artifact_reads.append(ref)
        return json.dumps(breakdown).encode("utf-8")

    async def fetcher(repo: str, ref: str, path: str) -> str:
        fetch_calls.append((repo, ref, path))
        raise AssertionError("repo fetch should not run when previous artifact exists")

    result = await create_jira_issues_from_stories(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "targetBranch": "breakdown-branch",
            "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeName": "Story",
                    "dependencyMode": "none",
                },
            },
        },
        {
            "previousOutputs": {
                "storyBreakdownArtifactRef": "art_previous_story_breakdown",
            }
        },
        jira_service_factory=lambda: service,
        story_fetcher=fetcher,
        artifact_reader=artifact_reader,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    assert artifact_reads == ["art_previous_story_breakdown"]
    assert fetch_calls == []
    assert service.requests[0].summary == "Create previous-output artifact Jira story"

@pytest.mark.asyncio
async def test_create_jira_issues_noops_for_empty_previous_story_artifact():
    service = _FakeJiraService()
    breakdown = {
        "source": {"referencePath": "docs/Designs/RuntimeTypes.md"},
        "stories": [],
        "reconciliation": {"status": "no_stories_to_reconcile"},
    }
    fetch_calls: list[tuple[str, str, str]] = []
    artifact_reads: list[str] = []

    async def artifact_reader(ref: str) -> bytes:
        artifact_reads.append(ref)
        return json.dumps(breakdown).encode("utf-8")

    async def fetcher(repo: str, ref: str, path: str) -> str:
        fetch_calls.append((repo, ref, path))
        raise AssertionError("repo fetch should not run when artifact ref is present")

    result = await create_jira_issues_from_stories(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "targetBranch": "breakdown-branch",
            "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
            "storyOutput": {
                "mode": "jira",
                "handoff": "artifact",
                "requiresStoryBreakdownArtifactRef": True,
                "fallback": "fail",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeName": "Story",
                    "dependencyMode": "linear_blocker_chain",
                },
            },
        },
        {
            "previousOutputs": {
                "storyOutput": {
                    "storyBreakdownArtifactRef": "art_empty_story_breakdown",
                }
            }
        },
        jira_service_factory=lambda: service,
        story_fetcher=fetcher,
        artifact_reader=artifact_reader,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_noop"
    assert result.outputs["storyOutput"]["storyCount"] == 0
    assert result.outputs["storyOutput"]["createdCount"] == 0
    assert (
        result.outputs["storyOutput"]["storyBreakdownArtifactRef"]
        == "art_empty_story_breakdown"
    )
    assert result.outputs["jira"]["issueMappings"] == []
    assert artifact_reads == ["art_empty_story_breakdown"]
    assert fetch_calls == []
    assert service.requests == []


@pytest.mark.asyncio
async def test_create_jira_issues_fails_for_failed_empty_story_artifact():
    service = _FakeJiraService()
    breakdown = {
        "error": {
            "code": "moonspec-breakdown.no-technical-design",
            "message": "No technical design provided",
        },
        "stories": [],
    }

    async def artifact_reader(ref: str) -> bytes:
        assert ref == "art_failed_story_breakdown"
        return json.dumps(breakdown).encode("utf-8")

    with pytest.raises(
        ValueError,
        match="moonspec-breakdown\\.no-technical-design",
    ):
        await create_jira_issues_from_stories(
            {
                "storyOutput": {
                    "mode": "jira",
                    "handoff": "artifact",
                    "requiresStoryBreakdownArtifactRef": True,
                    "fallback": "fail",
                    "jira": {
                        "projectKey": "MM",
                        "issueTypeName": "Story",
                    },
                },
            },
            {
                "previousOutputs": {
                    "storyOutput": {
                        "storyBreakdownArtifactRef": "art_failed_story_breakdown",
                    }
                }
            },
            jira_service_factory=lambda: service,
            artifact_reader=artifact_reader,
        )
    assert service.requests == []


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"stories": []}, True),
        ({"userStories": []}, True),
        ([], True),
        ({"stories": None}, False),
        ({"stories": "some description string"}, False),
        ({"stories": {"summary": "Not a list"}}, False),
        ({"stories": 1}, False),
        ('{"stories": []}', True),
        ('{"stories": "some description string"}', False),
        ("not json", False),
    ],
)
def test_has_explicit_empty_story_list_requires_empty_sequence(
    payload: Any,
    expected: bool,
):
    assert story_tools._has_explicit_empty_story_list(payload) is expected

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "breakdown",
    [
        {"stories": None},
        {"stories": "some description string"},
        {"stories": {"summary": "Not a list"}},
        {"stories": 1},
    ],
)
async def test_create_jira_issues_rejects_malformed_empty_story_artifact_payload(
    breakdown: dict[str, Any],
):
    service = _FakeJiraService()
    artifact_reads: list[str] = []

    async def artifact_reader(ref: str) -> bytes:
        artifact_reads.append(ref)
        return json.dumps(breakdown).encode("utf-8")

    with pytest.raises(ValueError, match="No stories were available"):
        await create_jira_issues_from_stories(
            {
                "storyOutput": {
                    "mode": "jira",
                    "fallback": "fail",
                    "jira": {
                        "projectKey": "MM",
                        "issueTypeName": "Story",
                    },
                },
            },
            {
                "previousOutputs": {
                    "storyOutput": {
                        "storyBreakdownArtifactRef": "art_malformed_story_breakdown",
                    }
                }
            },
            jira_service_factory=lambda: service,
            artifact_reader=artifact_reader,
        )

    assert artifact_reads == ["art_malformed_story_breakdown"]
    assert service.requests == []

@pytest.mark.asyncio
async def test_create_jira_issues_reads_previous_outputs_from_tool_inputs():
    service = _FakeJiraService()
    breakdown = {
        "source": {"referencePath": "docs/Designs/RuntimeTypes.md"},
        "stories": [
            {
                "id": "STORY-001",
                "summary": "Create input previous-output Jira story",
                "description": "As an operator, I can reuse an input handoff.",
                "sourceReference": {
                    "path": "docs/Designs/RuntimeTypes.md",
                    "claimIds": ["docs/Designs/RuntimeTypes.md#section-1-001"],
                },
            }
        ],
    }
    fetch_calls: list[tuple[str, str, str]] = []
    artifact_reads: list[str] = []

    async def artifact_reader(ref: str) -> bytes:
        artifact_reads.append(ref)
        return json.dumps(breakdown).encode("utf-8")

    async def fetcher(repo: str, ref: str, path: str) -> str:
        fetch_calls.append((repo, ref, path))
        raise AssertionError("repo fetch should not run when input artifact exists")

    result = await create_jira_issues_from_stories(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "targetBranch": "breakdown-branch",
            "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
            "storyOutput": {
                "mode": "jira",
                "handoff": "artifact",
                "requiresStoryBreakdownArtifactRef": True,
                "fallback": "fail",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeName": "Story",
                    "dependencyMode": "none",
                },
            },
            "previousOutputs": {
                "storyOutput": {
                    "storyBreakdownArtifactRef": "art_input_story_breakdown",
                }
            },
        },
        jira_service_factory=lambda: service,
        story_fetcher=fetcher,
        artifact_reader=artifact_reader,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    assert artifact_reads == ["art_input_story_breakdown"]
    assert fetch_calls == []
    assert service.requests[0].summary == "Create input previous-output Jira story"

@pytest.mark.asyncio
async def test_create_jira_issues_explains_protected_branch_handoff_failure():
    async def fetcher(_repo: str, _ref: str, _path: str) -> str:
        raise RuntimeError("404 Not Found")

    with pytest.raises(ValueError, match="protected branch 'main'"):
        await create_jira_issues_from_stories(
            {
                "repository": "MoonLadderStudios/Tactics",
                "branch": "main",
                "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
                "storyOutput": {
                    "mode": "jira",
                    "fallback": "fail",
                    "jira": {
                        "projectKey": "MM",
                        "issueTypeId": "10001",
                        "dependencyMode": "none",
                    },
                },
            },
            {
                "previousOutputs": {
                    "push_status": "protected_branch",
                    "push_branch": "main",
                }
            },
            story_fetcher=fetcher,
        )

@pytest.mark.asyncio
async def test_create_jira_issues_explains_no_commit_handoff_failure():
    async def fetcher(_repo: str, _ref: str, _path: str) -> str:
        raise AssertionError("fetcher should not run for unpublished handoff")

    with pytest.raises(ValueError, match="made no commits"):
        await create_jira_issues_from_stories(
            {
                "repository": "MoonLadderStudios/Tactics",
                "targetBranch": "breakdown-branch",
                "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
                "storyOutput": {
                    "mode": "jira",
                    "fallback": "fail",
                    "jira": {
                        "projectKey": "MM",
                        "issueTypeId": "10001",
                        "dependencyMode": "none",
                    },
                },
            },
            {
                "previousOutputs": {
                    "push_status": "no_commits",
                    "push_branch": "breakdown-branch",
                },
            },
            story_fetcher=fetcher,
        )

@pytest.mark.asyncio
async def test_create_jira_issues_explains_repo_handoff_read_failure():
    async def fetcher(_repo: str, _ref: str, _path: str) -> str:
        raise RuntimeError("404 Not Found")

    with pytest.raises(ValueError) as exc_info:
        await create_jira_issues_from_stories(
            {
                "repository": "MoonLadderStudios/MoonMind",
                "targetBranch": "breakdown-branch",
                "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
                "storyOutput": {
                    "mode": "jira",
                    "fallback": "fail",
                    "jira": {
                        "projectKey": "MM",
                        "issueTypeName": "Story",
                        "dependencyMode": "none",
                    },
                },
            },
            story_fetcher=fetcher,
        )

    message = str(exc_info.value)
    assert "MoonLadderStudios/MoonMind" in message
    assert "breakdown-branch" in message
    assert "storyBreakdownArtifactRef" in message
    assert "404 Not Found" in message

@pytest.mark.asyncio
async def test_create_jira_issues_preserves_source_reference_when_description_truncates():
    service = _FakeJiraService()
    long_description = "x" * 40000

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "none",
                },
            },
            "stories": [
                {
                    "summary": "Create a traced story",
                    "description": long_description,
                    "sourceReference": {
                        "path": "docs/Designs/RuntimeTypes.md",
                        "sections": ["Section 1"],
                        "claimIds": [
                            "docs/Designs/RuntimeTypes.md#section-1-001"
                        ],
                        "coverageIds": ["DESIGN-REQ-001"],
                    },
                }
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    request = service.requests[0]
    assert len(request.description) == 32767
    assert request.description.startswith(
        "Source Reference\nSource Document: docs/Designs/RuntimeTypes.md"
    )
    assert "Section 1" in request.description
    assert "DESIGN-REQ-001" in request.description
    assert request.description.endswith("[Truncated by MoonMind before Jira export]")

@pytest.mark.asyncio
async def test_create_jira_issues_accepts_pasted_story_breakdown_without_source_reference():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "linear_blocker_chain",
                },
            },
            "stories": [{"id": "STORY-001", "summary": "No source"}],
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    assert result.outputs["jira"]["issueMappings"][0]["sourceDesignPath"] == ""
    assert len(service.requests) == 1
    assert not service.requests[0].description.startswith("Source Reference")

@pytest.mark.asyncio
async def test_create_jira_issues_blocks_story_breakdown_when_source_reference_required():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "sourceReferencePolicy": "required",
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "linear_blocker_chain",
                },
            },
            "stories": [{"id": "STORY-001", "summary": "No source"}],
        },
        jira_service_factory=lambda: service,
    )

    assert service.requests == []
    assert result.outputs["storyOutput"]["status"] == "fallback"
    assert "requires sourceReference.path" in result.outputs["storyOutput"]["reason"]
    assert "STORY-001" in result.outputs["storyOutput"]["reason"]

@pytest.mark.asyncio
async def test_create_jira_issues_blocks_file_backed_story_without_claim_ids():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "none",
                },
            },
            "storyBreakdown": {
                "source": {"referencePath": "docs/Designs/RuntimeTypes.md"},
                "stories": [
                    {
                        "id": "STORY-001",
                        "summary": "Missing canonical claim",
                        "sourceReference": {
                            "path": "docs/Designs/RuntimeTypes.md",
                        },
                    },
                ],
            },
        },
        jira_service_factory=lambda: service,
    )

    assert service.requests == []
    assert result.outputs["storyOutput"]["status"] == "fallback"
    assert "requires sourceReference.claimIds" in result.outputs["storyOutput"]["reason"]
    assert "STORY-001" in result.outputs["storyOutput"]["reason"]

@pytest.mark.asyncio
async def test_create_jira_issues_accepts_imperative_file_backed_story_without_claim_ids():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "none",
                },
            },
            "storyBreakdown": {
                "source": {
                    "referencePath": "docs/tmp/Roadmap.md",
                    "sourceDocumentClass": "imperative-input",
                },
                "stories": [
                    {
                        "id": "STORY-001",
                        "summary": "Imperative fallback story",
                        "sourceReference": {
                            "path": "docs/tmp/Roadmap.md",
                            "claimIds": [],
                            "coverageIds": ["DESIGN-REQ-001"],
                        },
                    },
                ],
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    assert len(service.requests) == 1
    assert "Coverage IDs:" in service.requests[0].description
    assert "Canonical Claim IDs:" not in service.requests[0].description

@pytest.mark.asyncio
async def test_create_jira_issues_blocks_misclassified_canonical_story_without_claim_ids():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "none",
                },
            },
            "storyBreakdown": {
                "source": {
                    "referencePath": "docs/Designs/RuntimeTypes.md",
                    "sourceDocumentClass": "imperative-input",
                },
                "stories": [
                    {
                        "id": "STORY-001",
                        "summary": "Misclassified canonical source",
                    },
                ],
            },
        },
        jira_service_factory=lambda: service,
    )

    assert service.requests == []
    assert result.outputs["storyOutput"]["status"] == "fallback"
    assert "requires sourceReference.claimIds" in result.outputs["storyOutput"]["reason"]
    assert "STORY-001" in result.outputs["storyOutput"]["reason"]

@pytest.mark.asyncio
async def test_create_jira_issues_preserves_source_metadata_from_story_breakdown_json():
    service = _FakeJiraService()
    breakdown = {
        "source": {
            "referencePath": "docs/tmp/Roadmap.md",
            "sourceDocumentClass": "imperative-input",
        },
        "stories": [
            {
                "id": "STORY-001",
                "summary": "JSON payload story",
                "sourceReference": {
                    "coverageIds": ["DESIGN-REQ-001"],
                },
            },
        ],
    }

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "none",
                },
            },
            "storyBreakdownJson": json.dumps(breakdown),
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    assert result.outputs["jira"]["issueMappings"][0]["sourceDesignPath"] == (
        "docs/tmp/Roadmap.md"
    )
    assert len(service.requests) == 1
    assert service.requests[0].description.startswith(
        "Source Reference\nSource Document: docs/tmp/Roadmap.md"
    )
    assert "Coverage IDs:" in service.requests[0].description

@pytest.mark.asyncio
async def test_create_jira_issues_accepts_optional_file_backed_story_without_claim_ids():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "sourceReferencePolicy": "optional",
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "none",
                },
            },
            "storyBreakdown": {
                "stories": [
                    {
                        "id": "STORY-001",
                        "summary": "Optional canonical claim",
                        "sourceReference": {
                            "path": "docs/Designs/RuntimeTypes.md",
                        },
                    },
                ],
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    assert len(service.requests) == 1

@pytest.mark.parametrize("policy", [True, "true", "yes", "1", "on"])
def test_requires_story_source_reference_accepts_truthy_policy_values(policy):
    assert (
        story_tools._requires_story_source_reference(
            inputs={"sourceReferencePolicy": policy},
            story_output={},
            fallback_path="",
        )
        is True
    )

@pytest.mark.parametrize("policy", [False, "false", "no", "0", "off"])
def test_requires_story_source_reference_accepts_falsy_policy_values(policy):
    assert (
        story_tools._requires_story_source_reference(
            inputs={},
            story_output={"sourceReferencePolicy": policy},
            fallback_path="docs/Designs/RuntimeTypes.md",
        )
        is False
    )

@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("AGENTS.md", True),
        ("./AGENTS.md", True),
        ("/AGENTS.md", True),
        # The standalone constitution file was removed; principles now live in
        # AGENTS.md, so the old path is no longer a canonical source.
        (".specify/memory/constitution.md", False),
        ("docs/Designs/RuntimeTypes.md", True),
        ("./docs/Designs/RuntimeTypes.md", True),
        ("docs/tmp/Roadmap.md", False),
        ("artifacts/story-breakdowns/demo/stories.json", False),
    ],
)
def test_is_canonical_source_path_handles_prefixes(path, expected):
    assert story_tools._is_canonical_source_path(path) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("AGENTS.md", "AGENTS.md"),
        ("./AGENTS.md", "AGENTS.md"),
        ("`AGENTS.md`", "AGENTS.md"),
        ("docs/Designs/RuntimeTypes.md", "docs/Designs/RuntimeTypes.md"),
        # Removed standalone constitution path is no longer a recognized source.
        (".specify/memory/constitution.md", ""),
    ],
)
def test_normalize_source_document_path_recognizes_agents_md(value, expected):
    assert story_tools._normalize_source_document_path(value) == expected


def test_source_document_path_regex_extracts_agents_md():
    text = "Derived from AGENTS.md and docs/Designs/RuntimeTypes.md."
    matches = [
        match.group("path")
        for match in story_tools._SOURCE_DOCUMENT_PATH_RE.finditer(text)
    ]
    assert "AGENTS.md" in matches
    assert "docs/Designs/RuntimeTypes.md" in matches
    # A longer token must not yield a spurious AGENTS.md match.
    assert not list(
        story_tools._SOURCE_DOCUMENT_PATH_RE.finditer("SUBAGENTS.md")
    )

@pytest.mark.asyncio
async def test_create_jira_issues_accepts_claim_backed_source_reference_from_breakdown():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "none",
                },
            },
            "stories": [
                {
                    "id": "STORY-001",
                    "summary": "String source",
                    "sourceReference": {
                        "path": "docs/Designs/RuntimeTypes.md",
                        "claimIds": ["docs/Designs/RuntimeTypes.md#section-1-001"],
                    },
                }
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    request = service.requests[0]
    assert request.description.startswith(
        "Source Reference\nSource Document: docs/Designs/RuntimeTypes.md"
    )

@pytest.mark.asyncio
async def test_create_jira_issues_uses_source_document_as_breakdown_fallback_path():
    service = _FakeJiraService()
    breakdown = {
        "sourceDocument": " ",
        "source_document": "docs/Designs/RuntimeTypes.md",
        "stories": [
            {
                "id": "STORY-001",
                "summary": "Top-level source document",
                "description": "Create a traced story.",
                "sourceReference": {
                    "claimIds": ["docs/Designs/RuntimeTypes.md#section-1-001"],
                },
            }
        ],
    }

    async def fetcher(_repo: str, _ref: str, _path: str) -> str:
        import json

        return json.dumps(breakdown)

    result = await create_jira_issues_from_stories(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "targetBranch": "breakdown-branch",
            "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "none",
                },
            },
        },
        jira_service_factory=lambda: service,
        story_fetcher=fetcher,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    request = service.requests[0]
    assert request.description.startswith(
        "Source Reference\nSource Document: docs/Designs/RuntimeTypes.md"
    )

@pytest.mark.asyncio
async def test_create_jira_issues_falls_back_to_docs_tmp_when_jira_target_missing():
    result = await create_jira_issues_from_stories(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "targetBranch": "breakdown-branch",
            "startingBranch": "main",
            "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
            "storyOutput": {"mode": "jira"},
            "stories": [{"summary": "Story without Jira config"}],
        }
    )

    assert result.status == "COMPLETED"
    assert result.outputs["storyOutput"] == {
        "mode": "docs_tmp",
        "status": "fallback",
        "reason": "Jira projectKey and issueTypeId are required.",
        "storyCount": 1,
        "path": "artifacts/story-breakdowns/example/stories.json",
    }
    assert result.outputs["push_status"] == ""
    assert result.outputs["push_branch"] == "breakdown-branch"

@pytest.mark.asyncio
async def test_create_jira_issues_fails_when_jira_mode_has_no_story_payload():
    with pytest.raises(ValueError, match="No stories were available"):
        await create_jira_issues_from_stories(
            {
                "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
                "storyOutput": {
                    "mode": "jira",
                    "jira": {
                        "projectKey": "MM",
                        "issueTypeId": "10001",
                        "dependencyMode": "linear_blocker_chain",
                    },
                },
            }
        )

@pytest.mark.asyncio
async def test_create_jira_issues_allows_explicit_fallback_when_jira_mode_has_no_story_payload():
    result = await create_jira_issues_from_stories(
        {
            "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
            "storyOutput": {
                "mode": "jira",
                "fallback": "docs_tmp",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "linear_blocker_chain",
                },
            },
        }
    )

    assert result.outputs["storyOutput"]["status"] == "fallback"
    assert (
        result.outputs["storyOutput"]["reason"]
        == "No stories were available for Jira issue creation."
    )

@pytest.mark.asyncio
async def test_create_jira_issues_truncates_description_and_creates_subtasks():
    service = _FakeJiraService()
    long_description = "x" * 40000

    result = await create_jira_issues_from_stories(
        {
            "workflowId": "workflow/123",
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10002",
                    "parentIssueKey": "MM-100",
                },
            },
            "stories": [
                {
                    "summary": "Create a child story",
                    "description": long_description,
                }
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["jira"]["createdIssues"][0]["issueKey"] == "MM-SUB-1"
    assert not service.requests
    request = service.subtask_requests[0]
    assert request.parent_issue_key == "MM-100"
    assert len(request.description) == 32767
    assert request.description.endswith("[Truncated by MoonMind before Jira export]")
    assert request.fields["labels"] == ["moonmind-workflow-workflow-123"]

@pytest.mark.asyncio
async def test_create_jira_issues_uses_source_issue_as_subtask_parent():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeName": "Sub-task",
                    "sourceIssueKey": "MM-100",
                },
            },
            "stories": [
                {
                    "summary": "Create a generated child issue",
                    "description": "As an operator, I can request sub-task output.",
                }
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    assert result.outputs["jira"]["createdIssues"][0]["issueKey"] == "MM-SUB-1"
    assert service.requests == []
    request = service.subtask_requests[0]
    assert request.issue_type_id == "10007"
    assert request.parent_issue_key == "MM-100"

@pytest.mark.asyncio
async def test_create_jira_issues_does_not_use_source_issue_as_story_parent():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeName": "Story",
                    "sourceIssueKey": "MM-100",
                },
            },
            "stories": [
                {
                    "summary": "Create a generated story",
                    "description": "As an operator, I can request story output.",
                }
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_created"
    assert result.outputs["jira"]["createdIssues"][0]["issueKey"] == "MM-1"
    assert service.subtask_requests == []
    request = service.requests[0]
    assert request.issue_type_id == "10005"
    assert request.summary == "Create a generated story"

@pytest.mark.asyncio
async def test_create_jira_issues_requires_each_subtask_story_to_have_parent():
    service = _FakeJiraService()

    with pytest.raises(ValueError, match="parentIssueKey for every story"):
        await create_jira_issues_from_stories(
            {
                "storyOutput": {
                    "mode": "jira",
                    "onFailure": "fail",
                    "jira": {
                        "projectKey": "MM",
                        "issueTypeName": "Sub-task",
                    },
                },
                "stories": [
                    {
                        "summary": "Create first child issue",
                        "description": "First generated sub-task.",
                        "parentIssueKey": "MM-100",
                    },
                    {
                        "summary": "Create second child issue",
                        "description": "Second generated sub-task.",
                    },
                ],
            },
            jira_service_factory=lambda: service,
        )

    assert service.requests == []
    assert service.subtask_requests == []

@pytest.mark.asyncio
async def test_create_jira_issues_reuses_existing_issue_with_workflow_marker():
    service = _FakeJiraService()
    service.search_response = {
        "issues": [
            {
                "key": "MM-123",
                "id": "123",
                "self": "https://jira.example/rest/api/3/issue/123",
                "fields": {"summary": "Existing story"},
            }
        ]
    }

    result = await create_jira_issues_from_stories(
        {
            "workflowId": "wf-123",
            "storyOutput": {
                "mode": "jira",
                "jira": {"projectKey": "MM", "issueTypeId": "10001"},
            },
            "stories": [{"summary": "Existing story"}],
        },
        jira_service_factory=lambda: service,
    )

    assert service.search_requests
    assert not service.requests
    created_issue = result.outputs["jira"]["createdIssues"][0]
    assert created_issue["existing"] is True
    assert created_issue["issueKey"] == "MM-123"

@pytest.mark.asyncio
async def test_create_jira_issues_fallback_reports_partial_success():
    class _FailingAfterFirstService(_FakeJiraService):
        async def create_issue(self, request):
            if self.requests:
                raise RuntimeError("jira unavailable")
            return await super().create_issue(request)

    service = _FailingAfterFirstService()

    result = await create_jira_issues_from_stories(
        {
            "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
            "storyOutput": {
                "mode": "jira",
                "jira": {"projectKey": "MM", "issueTypeId": "10001"},
            },
            "stories": [
                {
                    "summary": "First",
                    "sourceReference": {
                        "path": "docs/Designs/RuntimeTypes.md",
                        "claimIds": ["docs/Designs/RuntimeTypes.md#section-1-001"],
                    },
                },
                {
                    "summary": "Second",
                    "sourceReference": {
                        "path": "docs/Designs/RuntimeTypes.md",
                        "claimIds": ["docs/Designs/RuntimeTypes.md#section-2-001"],
                    },
                },
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "fallback"
    assert result.outputs["storyOutput"]["createdCount"] == 1
    assert result.outputs["jira"]["partial"] is True
    assert result.outputs["jira"]["createdIssues"][0]["issueKey"] == "MM-1"

@pytest.mark.asyncio
async def test_create_jira_issues_linear_blocker_chain_creates_adjacent_links():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "linear_blocker_chain",
                },
            },
            "stories": [
                {"id": "STORY-001", "summary": "First"},
                {"id": "STORY-002", "summary": "Second"},
                {"id": "STORY-003", "summary": "Third"},
            ],
        },
        jira_service_factory=lambda: service,
    )

    jira = result.outputs["jira"]
    assert jira["dependencyMode"] == "linear_blocker_chain"
    assert jira["dependencyChainComplete"] is True
    assert jira["linkCount"] == 2
    assert [item["issueKey"] for item in jira["issueMappings"]] == [
        "MM-1",
        "MM-2",
        "MM-3",
    ]
    assert [(req.blocks_issue_key, req.blocked_issue_key) for req in service.link_requests] == [
        ("MM-1", "MM-2"),
        ("MM-2", "MM-3"),
    ]
    assert [item["status"] for item in jira["linkResults"]] == ["created", "created"]

@pytest.mark.asyncio
async def test_create_jira_issues_linear_blocker_chain_orders_by_declared_dependencies():
    """A reversed breakdown order must still block prerequisites-first.

    The stories arrive dependent-first (STORY-003, STORY-002, STORY-001), but
    each declares the story it truly depends on. The blocker chain must follow
    the declared dependencies (STORY-001 blocks STORY-002 blocks STORY-003), not
    the raw list order, otherwise the Jira dependencies are reversed.
    """
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "linear_blocker_chain",
                },
            },
            "stories": [
                {"id": "STORY-003", "summary": "Third", "dependencies": ["STORY-002"]},
                {"id": "STORY-002", "summary": "Second", "dependencies": ["STORY-001"]},
                {"id": "STORY-001", "summary": "First"},
            ],
        },
        jira_service_factory=lambda: service,
    )

    jira = result.outputs["jira"]
    # Issues are still created in the breakdown's list order.
    assert [item["issueKey"] for item in jira["issueMappings"]] == [
        "MM-1",
        "MM-2",
        "MM-3",
    ]
    # STORY-001 == MM-3 (created last) is the true root and must block first.
    assert [
        (req.blocks_issue_key, req.blocked_issue_key) for req in service.link_requests
    ] == [
        ("MM-3", "MM-2"),
        ("MM-2", "MM-1"),
    ]
    assert jira["dependencyChainComplete"] is True
    assert jira["linkCount"] == 2

@pytest.mark.asyncio
async def test_create_jira_issues_linear_blocker_chain_fans_out_declared_dependencies():
    """Fan-out dependencies link each prerequisite directly, not an adjacent chain.

    STORY-002 and STORY-003 both depend only on STORY-001. A dependency-ordered
    adjacent chain (001 -> 002 -> 003) would fabricate a false ``002 blocks 003``
    link and never link STORY-001 to STORY-003, so each story must instead be
    blocked by the stories it actually declares as prerequisites.
    """
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "linear_blocker_chain",
                },
            },
            "stories": [
                {"id": "STORY-001", "summary": "First"},
                {"id": "STORY-002", "summary": "Second", "dependencies": ["STORY-001"]},
                {"id": "STORY-003", "summary": "Third", "dependencies": ["STORY-001"]},
            ],
        },
        jira_service_factory=lambda: service,
    )

    jira = result.outputs["jira"]
    # STORY-001 (MM-1) directly blocks both dependents; no fabricated MM-2 -> MM-3.
    assert [
        (req.blocks_issue_key, req.blocked_issue_key) for req in service.link_requests
    ] == [
        ("MM-1", "MM-2"),
        ("MM-1", "MM-3"),
    ]
    assert jira["dependencyChainComplete"] is True
    assert jira["linkCount"] == 2

@pytest.mark.asyncio
async def test_create_jira_issues_linear_blocker_chain_survives_dependency_cycle():
    """A dependency cycle emits one deterministic link instead of failing.

    STORY-001 and STORY-002 declare each other as prerequisites. Only a single
    link is created (no reciprocal ``Blocks`` pair): the first story processed
    (STORY-001) is blocked by the prerequisite it declares (STORY-002 / MM-2).
    """
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "linear_blocker_chain",
                },
            },
            "stories": [
                {"id": "STORY-001", "summary": "First", "dependencies": ["STORY-002"]},
                {"id": "STORY-002", "summary": "Second", "dependencies": ["STORY-001"]},
            ],
        },
        jira_service_factory=lambda: service,
    )

    jira = result.outputs["jira"]
    assert [
        (req.blocks_issue_key, req.blocked_issue_key) for req in service.link_requests
    ] == [("MM-2", "MM-1")]
    assert jira["linkCount"] == 1

@pytest.mark.asyncio
async def test_create_jira_issues_issue_mappings_carry_source_design_path():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {"projectKey": "MM", "issueTypeId": "10001"},
            },
            "stories": [
                {
                    "id": "STORY-001",
                    "summary": "First",
                    "sourceReference": {
                        "path": "docs/Designs/RuntimeTypes.md",
                        "claimIds": ["docs/Designs/RuntimeTypes.md#section-1-001"],
                    },
                },
                {
                    "id": "STORY-002",
                    "summary": "No reference",
                },
            ],
        },
        jira_service_factory=lambda: service,
    )

    mappings = result.outputs["jira"]["issueMappings"]
    assert mappings[0]["sourceDesignPath"] == "docs/Designs/RuntimeTypes.md"
    assert mappings[0]["sourceClaimIds"] == [
        "docs/Designs/RuntimeTypes.md#section-1-001"
    ]
    assert mappings[1]["sourceDesignPath"] == ""

@pytest.mark.asyncio
async def test_create_jira_issues_issue_mappings_use_breakdown_source_fallback():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {"projectKey": "MM", "issueTypeId": "10001"},
            },
            "storyBreakdown": {
                "source": {"referencePath": "docs/Designs/RuntimeTypes.md"},
                "stories": [
                    {
                        "id": "STORY-001",
                        "summary": "First",
                        "sourceReference": {
                            "claimIds": [
                                "docs/Designs/RuntimeTypes.md#section-1-001"
                            ],
                        },
                    },
                ],
            },
        },
        jira_service_factory=lambda: service,
    )

    mappings = result.outputs["jira"]["issueMappings"]
    assert mappings[0]["sourceDesignPath"] == "docs/Designs/RuntimeTypes.md"
    assert mappings[0]["sourceClaimIds"] == [
        "docs/Designs/RuntimeTypes.md#section-1-001"
    ]

@pytest.mark.asyncio
async def test_check_jira_blockers_blocks_on_single_outward_unresolved_blocks_link():
    service = _FakeJiraService()
    service.issue_responses["MM-2"] = {
        "key": "MM-2",
        "fields": {
            "issuelinks": [
                {
                    "type": {
                        "name": "Blocks",
                        "outward": "blocks",
                        "inward": "is blocked by",
                    },
                    "outwardIssue": {
                        "key": "MM-1",
                        "fields": {"status": {"name": "Backlog"}},
                    },
                }
            ]
        },
    }

    result = await check_jira_blockers(
        {"targetIssueKey": "MM-2"},
        jira_service_factory=lambda: service,
    )

    assert result.outputs["decision"] == "blocked"
    assert result.outputs["blockingIssues"] == [
        {
            "issueKey": "MM-1",
            "status": "Backlog",
            "statusKnown": True,
            "linkType": "Blocks",
            "relationship": "blocks",
            "done": False,
        }
    ]
    assert result.outputs["summary"] == (
        "MM-2 is blocked by unresolved Jira issue(s): MM-1 (Backlog)."
    )
    assert [request.issue_key for request in service.get_issue_requests] == ["MM-2"]

@pytest.mark.asyncio
async def test_check_jira_blockers_ignores_single_inward_links_from_target_issue():
    service = _FakeJiraService()
    service.issue_responses["MM-1"] = {
        "key": "MM-1",
        "fields": {
            "issuelinks": [
                {
                    "type": {
                        "name": "Blocks",
                        "outward": "blocks",
                        "inward": "is blocked by",
                    },
                    "inwardIssue": {
                        "key": "MM-2",
                        "fields": {"status": {"name": "Backlog"}},
                    },
                }
            ]
        },
    }

    result = await check_jira_blockers(
        {"targetIssueKey": "MM-1"},
        jira_service_factory=lambda: service,
    )

    assert result.outputs["decision"] == "continue"
    assert result.outputs["blockingIssues"] == []
    assert "no Jira blocker links" in result.outputs["summary"]
    assert [request.issue_key for request in service.get_issue_requests] == ["MM-1"]

@pytest.mark.asyncio
async def test_check_jira_blockers_fetches_missing_blocker_status_and_allows_done():
    service = _FakeJiraService()
    service.issue_responses["MM-2"] = {
        "key": "MM-2",
        "fields": {
            "issuelinks": [
                {
                    "type": {"name": "Blocks"},
                    "outwardIssue": {"key": "MM-1"},
                }
            ]
        },
    }
    service.issue_responses["MM-1"] = {
        "key": "MM-1",
        "fields": {
            "status": {
                "name": "Closed",
                "statusCategory": {"key": "done"},
            }
        },
    }

    result = await check_jira_blockers(
        {"targetIssueKey": "MM-2"},
        jira_service_factory=lambda: service,
    )

    assert result.outputs["decision"] == "continue"
    assert result.outputs["blockingIssues"] == []
    assert result.outputs["resolvedBlockingIssues"][0]["issueKey"] == "MM-1"
    assert result.outputs["resolvedBlockingIssues"][0]["done"] is True
    assert [request.issue_key for request in service.get_issue_requests] == [
        "MM-2",
        "MM-1",
    ]


@pytest.mark.asyncio
async def test_check_jira_blockers_preserves_previous_assessment_verdict():
    service = _FakeJiraService()
    service.issue_responses["MM-2"] = {
        "key": "MM-2",
        "fields": {"issuelinks": []},
    }

    result = await check_jira_blockers(
        {
            "targetIssueKey": "MM-2",
            "assessmentArtifactPath": "artifacts/jira-implement-assessment.json",
            "previousOutputs": {
                "assessmentVerdict": "PARTIALLY_IMPLEMENTED",
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "continue"
    assert result.outputs["assessmentVerdict"] == "PARTIALLY_IMPLEMENTED"


@pytest.mark.asyncio
async def test_check_jira_blockers_preserves_bold_previous_assessment_verdict():
    service = _FakeJiraService()
    service.issue_responses["MM-2"] = {
        "key": "MM-2",
        "fields": {"issuelinks": []},
    }

    result = await check_jira_blockers(
        {
            "targetIssueKey": "MM-2",
            "assessmentArtifactPath": "artifacts/jira-implement-assessment.json",
            "previousOutputs": {
                "lastAssistantText": "Assessment complete: **PARTIALLY_IMPLEMENTED**.",
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "continue"
    assert result.outputs["assessmentVerdict"] == "PARTIALLY_IMPLEMENTED"


@pytest.mark.asyncio
async def test_check_jira_blockers_preserves_sentence_previous_assessment_verdict():
    service = _FakeJiraService()
    service.issue_responses["MM-2"] = {
        "key": "MM-2",
        "fields": {"issuelinks": []},
    }

    result = await check_jira_blockers(
        {
            "targetIssueKey": "MM-2",
            "assessmentArtifactPath": "artifacts/jira-implement-assessment.json",
            "previousOutputs": {
                "lastAssistantText": (
                    "Assessment complete! Verdict: `PARTIALLY_IMPLEMENTED`."
                ),
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "continue"
    assert result.outputs["assessmentVerdict"] == "PARTIALLY_IMPLEMENTED"


@pytest.mark.asyncio
async def test_check_jira_blockers_preserves_markdown_heading_previous_assessment_verdict():
    service = _FakeJiraService()
    service.issue_responses["MM-1139"] = {
        "key": "MM-1139",
        "fields": {"issuelinks": []},
    }

    result = await check_jira_blockers(
        {
            "targetIssueKey": "MM-1139",
            "assessmentArtifactPath": "artifacts/jira-implement-assessment.json",
            "previousOutputs": {
                "summary": (
                    "Both handoff artifacts are written and valid. "
                    "Here is the assessment result.\n\n"
                    "  ## Verdict: NOT_IMPLEMENTED\n\n"
                    "Evidence follows."
                ),
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "continue"
    assert result.outputs["assessmentVerdict"] == "NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_check_jira_blockers_preserves_issue_key_before_bold_verdict():
    service = _FakeJiraService()
    service.issue_responses["MM-1133"] = {
        "key": "MM-1133",
        "fields": {"issuelinks": []},
    }

    result = await check_jira_blockers(
        {
            "targetIssueKey": "MM-1133",
            "assessmentArtifactPath": "artifacts/jira-implement-assessment.json",
            "previousOutputs": {
                "lastAssistantText": (
                    "Assessment complete: `MM-1133` is "
                    "**PARTIALLY_IMPLEMENTED**."
                ),
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "continue"
    assert result.outputs["assessmentVerdict"] == "PARTIALLY_IMPLEMENTED"


@pytest.mark.asyncio
async def test_check_jira_blockers_preserves_concise_previous_verdict():
    service = _FakeJiraService()
    service.issue_responses["MM-1130"] = {
        "key": "MM-1130",
        "fields": {"issuelinks": []},
    }

    result = await check_jira_blockers(
        {
            "targetIssueKey": "MM-1130",
            "assessmentArtifactPath": "artifacts/jira-implement-assessment.json",
            "previousOutputs": {
                "lastAssistantText": "Verdict: `PARTIALLY_IMPLEMENTED`.",
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "continue"
    assert result.outputs["assessmentVerdict"] == "PARTIALLY_IMPLEMENTED"


@pytest.mark.asyncio
async def test_check_jira_blockers_does_not_parse_verdict_shaped_issue_key():
    service = _FakeJiraService()
    service.issue_responses["BLOCKED-123"] = {
        "key": "BLOCKED-123",
        "fields": {"issuelinks": []},
    }

    result = await check_jira_blockers(
        {
            "targetIssueKey": "BLOCKED-123",
            "assessmentArtifactPath": "artifacts/jira-implement-assessment.json",
            "previousOutputs": {
                "lastAssistantText": (
                    "Assessment complete: `BLOCKED-123` is "
                    "**PARTIALLY_IMPLEMENTED**."
                ),
            },
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "continue"
    assert result.outputs["assessmentVerdict"] == "PARTIALLY_IMPLEMENTED"


@pytest.mark.asyncio
async def test_jira_implement_status_update_uses_blocker_preserved_assessment_verdict(
    tmp_path,
) -> None:
    service = _FakeJiraService()
    service.issue_responses["MM-2"] = {
        "key": "MM-2",
        "fields": {
            "issuelinks": [],
            "status": {"id": "1", "name": "Backlog"},
        },
    }
    service.transition_responses["MM-2"] = {
        "key": "MM-2",
        "fields": {"status": {"id": "3", "name": "In Progress"}},
    }
    service.transitions_response = {
        "transitions": [
            {"id": "31", "name": "In Progress", "to": {"name": "In Progress"}}
        ]
    }

    blocker_result = await check_jira_blockers(
        {
            "targetIssueKey": "MM-2",
            "assessmentArtifactPath": "artifacts/jira-implement-assessment.json",
            "previousOutputs": {
                "lastAssistantText": (
                    "Assessment complete? Verdict: `PARTIALLY_IMPLEMENTED`."
                ),
            },
        },
        jira_service_factory=lambda: service,
    )

    status_result = await update_jira_issue_status(
        {
            "issueKey": "MM-2",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": str(tmp_path / "missing-assessment.json"),
            "previousOutputs": blocker_result.outputs,
        },
        jira_service_factory=lambda: service,
    )

    assert blocker_result.outputs["assessmentVerdict"] == "PARTIALLY_IMPLEMENTED"
    assert status_result.status == "COMPLETED"
    assert status_result.outputs["decision"] == "transitioned"
    assert service.transition_requests[0].transition_id == "31"


@pytest.mark.asyncio
async def test_check_jira_blockers_respects_configured_link_type_name():
    service = _FakeJiraService()
    service.issue_responses["MM-2"] = {
        "key": "MM-2",
        "fields": {
            "issuelinks": [
                {
                    "type": {
                        "name": "Blocks",
                        "outward": "blocks",
                        "inward": "is blocked by",
                    },
                    "inwardIssue": {
                        "key": "MM-1",
                        "fields": {"status": {"name": "Backlog"}},
                    },
                }
            ]
        },
    }

    result = await check_jira_blockers(
        {"targetIssueKey": "MM-2", "linkType": "Depends"},
        jira_service_factory=lambda: service,
    )

    assert result.outputs["decision"] == "continue"
    assert result.outputs["blockingIssues"] == []
    assert [request.issue_key for request in service.get_issue_requests] == ["MM-2"]

@pytest.mark.asyncio
async def test_create_jira_issues_dependency_mode_none_skips_links():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "none",
                },
            },
            "stories": [
                {"id": "STORY-001", "summary": "First"},
                {"id": "STORY-002", "summary": "Second"},
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert service.link_requests == []
    assert result.outputs["jira"]["dependencyMode"] == "none"
    assert result.outputs["jira"]["linkCount"] == 0
    assert result.outputs["jira"]["dependencyChainComplete"] is None

@pytest.mark.asyncio
async def test_create_jira_issues_partial_link_failure_preserves_created_issues():
    service = _FakeJiraService()
    service.fail_link_after = 1

    result = await create_jira_issues_from_stories(
        {
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "linear_blocker_chain",
                },
            },
            "stories": [
                {"id": "STORY-001", "summary": "First"},
                {"id": "STORY-002", "summary": "Second"},
                {"id": "STORY-003", "summary": "Third"},
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "jira_partial"
    jira = result.outputs["jira"]
    assert jira["partial"] is True
    assert jira["createdCount"] == 3
    assert [item["issueKey"] for item in jira["createdIssues"]] == [
        "MM-1",
        "MM-2",
        "MM-3",
    ]
    assert jira["dependencyChainComplete"] is False
    assert [item["status"] for item in jira["linkResults"]] == ["created", "failed"]
    assert jira["linkResults"][1]["blocksIssueKey"] == "MM-2"
    assert jira["linkResults"][1]["blockedIssueKey"] == "MM-3"

@pytest.mark.asyncio
async def test_create_jira_issues_reuses_existing_issues_and_links():
    service = _FakeJiraService()
    service.search_response = {
        "issues": [
            {
                "key": "MM-10",
                "id": "10",
                "self": "https://jira.example/rest/api/3/issue/10",
                "fields": {"summary": "First"},
            },
            {
                "key": "MM-11",
                "id": "11",
                "self": "https://jira.example/rest/api/3/issue/11",
                "fields": {"summary": "Second"},
            },
        ]
    }
    service.existing_links = {("MM-10", "MM-11")}

    result = await create_jira_issues_from_stories(
        {
            "workflowId": "wf-123",
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "linear_blocker_chain",
                },
            },
            "stories": [
                {"id": "STORY-001", "summary": "First"},
                {"id": "STORY-002", "summary": "Second"},
            ],
        },
        jira_service_factory=lambda: service,
    )

    assert not service.requests
    assert [item["existing"] for item in result.outputs["jira"]["issueMappings"]] == [
        True,
        True,
    ]
    assert result.outputs["jira"]["linkResults"][0]["status"] == "existing"
    assert result.outputs["jira"]["dependencyChainComplete"] is True

@pytest.mark.asyncio
async def test_create_jira_issues_rejects_unsupported_dependency_mode_before_mutation():
    service = _FakeJiraService()

    result = await create_jira_issues_from_stories(
        {
            "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
            "storyOutput": {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeId": "10001",
                    "dependencyMode": "graph",
                },
            },
            "stories": [{"summary": "First"}],
        },
        jira_service_factory=lambda: service,
    )

    assert service.requests == []
    assert result.outputs["storyOutput"]["status"] == "fallback"
    assert "Unsupported Jira dependencyMode" in result.outputs["storyOutput"]["reason"]

@pytest.mark.asyncio
async def test_create_jira_issues_fallback_preserves_dependency_mode_metadata():
    result = await create_jira_issues_from_stories(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "targetBranch": "breakdown-branch",
            "startingBranch": "main",
            "storyBreakdownPath": "artifacts/story-breakdowns/example/stories.json",
            "storyOutput": {
                "mode": "jira",
                "jira": {"dependencyMode": "linear_blocker_chain"},
            },
            "stories": [{"summary": "Story without Jira config"}],
        }
    )

    assert result.outputs["storyOutput"]["status"] == "fallback"
    assert result.outputs["storyOutput"]["dependencyMode"] == "linear_blocker_chain"
    assert result.outputs["storyOutput"]["reason"] == "Jira projectKey and issueTypeId are required."

@pytest.mark.asyncio
async def test_create_jira_orchestrate_tasks_wires_ordered_dependencies_and_traceability():
    creator = _FakeExecutionCreator()

    result = await create_jira_orchestrate_tasks_from_issue_mappings(
        {
            "jira": {
                "issueMappings": [
                    {"storyId": "STORY-003", "storyIndex": 3, "summary": "Third", "issueKey": "MM-503"},
                    {"storyId": "STORY-001", "storyIndex": 1, "summary": "First", "issueKey": "MM-501"},
                    {"storyId": "STORY-002", "storyIndex": 2, "summary": "Second", "issueKey": "MM-502"},
                ]
            },
            "task": {
                "repository": "MoonLadderStudios/MoonMind",
                "runtime": {"mode": "codex_cli"},
                "inputs": {"run_verify": False},
                "publish": {
                    "mode": "pr",
                    "mergeAutomation": {"enabled": True},
                    "merge_automation": {"enabled": False},
                },
            },
            "traceability": {
                "sourceIssueKey": "MM-404",
                "sourceBriefRef": "spec.md (Input)",
            },
        },
        execution_creator=creator,
    )

    assert result.status == "COMPLETED"
    orchestration = result.outputs["jiraOrchestration"]
    assert orchestration["status"] == "completed"
    assert orchestration["storyCount"] == 3
    assert orchestration["createdTaskCount"] == 3
    assert orchestration["createdWorkflowCount"] == 3
    assert orchestration["dependencyCount"] == 2
    assert orchestration["workflows"] == orchestration["tasks"]
    assert orchestration["workflowMappings"] == orchestration["tasks"]
    assert [task["jiraIssueKey"] for task in orchestration["tasks"]] == [
        "MM-501",
        "MM-502",
        "MM-503",
    ]
    assert orchestration["tasks"][0]["dependsOn"] == []
    assert orchestration["tasks"][1]["dependsOn"] == ["mm:story-1"]
    assert orchestration["tasks"][2]["dependsOn"] == ["mm:story-2"]
    assert orchestration["traceability"]["sourceIssueKey"] == "MM-404"

    assert creator.requests[0]["initial_parameters"]["workflow"].get("dependsOn") is None
    assert creator.requests[1]["initial_parameters"]["workflow"]["dependsOn"] == ["mm:story-1"]
    assert creator.requests[2]["initial_parameters"]["workflow"]["dependsOn"] == ["mm:story-2"]
    assert creator.requests[0]["idempotency_key"] == (
        "jira-orchestrate:MM-404:STORY-001:MM-501"
    )
    assert "Run Jira Orchestrate for MM-501" in creator.requests[0]["title"]
    first_parameters = creator.requests[0]["initial_parameters"]
    assert first_parameters["publishMode"] == "pr"
    assert first_parameters["workflow"]["publish"] == {
        "mode": "pr",
        "mergeAutomation": {"enabled": True},
    }
    assert first_parameters["workflow"]["inputs"]["jira_issue"] == {
        "key": "MM-501",
        "summary": "First",
    }
    assert first_parameters["workflow"]["inputs"]["run_verify"] is False
    assert "jira_issue_key" not in first_parameters["workflow"]["inputs"]
    assert "merge_automation" not in first_parameters["workflow"]["publish"]
    assert "MM-404" in first_parameters["workflow"]["instructions"]

@pytest.mark.asyncio
async def test_create_jira_orchestrate_tasks_omits_disabled_merge_automation():
    creator = _FakeExecutionCreator()

    result = await create_jira_orchestrate_tasks_from_issue_mappings(
        {
            "jira": {
                "issueMappings": [
                    {
                        "storyId": "STORY-001",
                        "storyIndex": 1,
                        "summary": "First",
                        "issueKey": "MM-501",
                    },
                ]
            },
            "task": {
                "repository": "MoonLadderStudios/MoonMind",
                "runtime": {"mode": "codex_cli"},
                "publish": {
                    "mode": "pr",
                    "mergeAutomation": {"enabled": "False"},
                },
            },
        },
        execution_creator=creator,
    )

    assert result.status == "COMPLETED"
    first_parameters = creator.requests[0]["initial_parameters"]
    assert first_parameters["publishMode"] == "pr"
    assert first_parameters["workflow"]["publish"] == {"mode": "pr"}

@pytest.mark.asyncio
async def test_create_jira_orchestrate_tasks_ignores_boolean_merge_automation():
    creator = _FakeExecutionCreator()

    result = await create_jira_orchestrate_tasks_from_issue_mappings(
        {
            "jira": {
                "issueMappings": [
                    {
                        "storyId": "STORY-001",
                        "storyIndex": 1,
                        "summary": "First",
                        "issueKey": "MM-501",
                    },
                ]
            },
            "task": {
                "repository": "MoonLadderStudios/MoonMind",
                "runtime": {"mode": "codex_cli"},
                "publish": {
                    "mode": "pr",
                    "mergeAutomation": True,
                },
            },
        },
        execution_creator=creator,
    )

    assert result.status == "COMPLETED"
    first_parameters = creator.requests[0]["initial_parameters"]
    assert first_parameters["publishMode"] == "pr"
    assert first_parameters["workflow"]["publish"] == {"mode": "pr"}

@pytest.mark.asyncio
async def test_create_jira_orchestrate_tasks_uses_previous_step_mappings_and_owner_context():
    creator = _FakeExecutionCreator()

    result = await create_jira_orchestrate_tasks_from_issue_mappings(
        {
            "jiraOrchestration": {
                "task": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "runtime": {"mode": "codex_cli"},
                    "publish": {"mode": "none"},
                },
                "traceability": {"sourceIssueKey": "MM-404"},
            }
        },
        {
            "ownerId": "user-123",
            "ownerType": "user",
            "previousOutputs": {
                "jira": {
                    "issueMappings": [
                        {
                            "storyId": "STORY-001",
                            "storyIndex": 1,
                            "summary": "First",
                            "issueKey": "MM-501",
                        }
                    ]
                }
            },
        },
        execution_creator=creator,
    )

    assert result.outputs["jiraOrchestration"]["status"] == "completed"
    assert result.outputs["jiraOrchestration"]["createdTaskCount"] == 1
    assert creator.requests[0]["owner_id"] == "user-123"
    assert creator.requests[0]["owner_type"] == "user"
    task = creator.requests[0]["initial_parameters"]["workflow"]
    assert "orchestration_mode" not in task["inputs"]
    assert task["inputs"]["constraints"] == "Preserve source issue MM-404 traceability."
    assert "Source Jira issue: MM-404." in task["instructions"]

@pytest.mark.asyncio
async def test_create_jira_orchestrate_tasks_uses_input_previous_outputs_mappings():
    creator = _FakeExecutionCreator()

    result = await create_jira_orchestrate_tasks_from_issue_mappings(
        {
            "jiraOrchestration": {
                "task": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "runtime": {"mode": "codex_cli"},
                    "publish": {"mode": "pr"},
                },
                "traceability": {"sourceIssueKey": "MM-705"},
            },
            "previousOutputs": {
                "jira": {
                    "issueMappings": [
                        {
                            "storyId": "STORY-003",
                            "storyIndex": 1,
                            "summary": "Remaining work",
                            "issueKey": "MM-810",
                        }
                    ]
                }
            },
        },
        execution_creator=creator,
    )

    orchestration = result.outputs["jiraOrchestration"]
    assert orchestration["status"] == "completed"
    assert orchestration["storyCount"] == 1
    assert orchestration["createdTaskCount"] == 1
    assert orchestration["tasks"][0]["jiraIssueKey"] == "MM-810"
    inputs = creator.requests[0]["initial_parameters"]["workflow"]["inputs"]
    assert inputs["jira_issue"] == {"key": "MM-810", "summary": "Remaining work"}
    assert "jira_issue_key" not in inputs

@pytest.mark.asyncio
async def test_create_jira_orchestrate_tasks_propagates_source_design_path():
    creator = _FakeExecutionCreator()

    result = await create_jira_orchestrate_tasks_from_issue_mappings(
        {
            "jira": {
                "issueMappings": [
                    {
                        "storyId": "STORY-001",
                        "storyIndex": 1,
                        "summary": "First",
                        "issueKey": "MM-501",
                        "sourceDesignPath": "docs/Designs/RuntimeTypes.md",
                        "sourceClaimIds": [
                            "docs/Designs/RuntimeTypes.md#section-1-001"
                        ],
                    },
                    {
                        "storyId": "STORY-002",
                        "storyIndex": 2,
                        "summary": "Legacy mapping without source path",
                        "issueKey": "MM-502",
                    },
                ]
            },
            "traceability": {"sourceIssueKey": "MM-404"},
        },
        execution_creator=creator,
    )

    assert result.outputs["jiraOrchestration"]["status"] == "completed"
    first_task = creator.requests[0]["initial_parameters"]["workflow"]
    assert first_task["inputs"]["source_design_path"] == (
        "docs/Designs/RuntimeTypes.md"
    )
    assert first_task["inputs"]["source_claim_ids"] == [
        "docs/Designs/RuntimeTypes.md#section-1-001"
    ]
    assert (
        "Source design document: docs/Designs/RuntimeTypes.md"
        in first_task["instructions"]
    )
    assert (
        "Source canonical claim IDs: docs/Designs/RuntimeTypes.md#section-1-001"
        in first_task["instructions"]
    )
    second_task = creator.requests[1]["initial_parameters"]["workflow"]
    assert second_task["inputs"]["source_design_path"] == ""
    assert second_task["inputs"]["source_claim_ids"] == []
    assert "Source design document: not provided" in second_task["instructions"]

@pytest.mark.asyncio
async def test_create_jira_orchestrate_tasks_handles_one_and_zero_story_results():
    one_creator = _FakeExecutionCreator()

    one = await create_jira_orchestrate_tasks_from_issue_mappings(
        {
            "jira": {
                "issueMappings": [
                    {"storyId": "STORY-001", "storyIndex": 1, "summary": "Only", "issueKey": "MM-501"}
                ]
            },
            "traceability": {"sourceIssueKey": "MM-404"},
        },
        execution_creator=one_creator,
    )

    assert one.outputs["jiraOrchestration"]["status"] == "completed"
    assert one.outputs["jiraOrchestration"]["createdTaskCount"] == 1
    assert one.outputs["jiraOrchestration"]["createdWorkflowCount"] == 1
    assert one.outputs["jiraOrchestration"]["dependencyCount"] == 0
    assert one.outputs["jiraOrchestration"]["tasks"][0]["dependsOn"] == []
    assert one.outputs["jiraOrchestration"]["workflows"][0]["dependsOn"] == []

    zero = await create_jira_orchestrate_tasks_from_issue_mappings(
        {"jira": {"issueMappings": []}, "traceability": {"sourceIssueKey": "MM-404"}},
        execution_creator=_FakeExecutionCreator(),
    )

    assert zero.outputs["jiraOrchestration"]["status"] == "no_downstream_tasks"
    assert zero.outputs["jiraOrchestration"]["workflowStatus"] == (
        "no_downstream_workflows"
    )
    assert zero.outputs["jiraOrchestration"]["createdTaskCount"] == 0
    assert zero.outputs["jiraOrchestration"]["createdWorkflowCount"] == 0
    assert zero.outputs["jiraOrchestration"]["dependencyCount"] == 0

@pytest.mark.asyncio
async def test_create_jira_orchestrate_tasks_reports_missing_issue_key_and_partial_failures():
    missing_key = await create_jira_orchestrate_tasks_from_issue_mappings(
        {
            "jira": {
                "issueMappings": [
                    {"storyId": "STORY-001", "storyIndex": 1, "summary": "Missing key"},
                    {"storyId": "STORY-002", "storyIndex": 2, "summary": "Second", "issueKey": "MM-502"},
                ]
            },
            "traceability": {"sourceIssueKey": "MM-404"},
        },
        execution_creator=_FakeExecutionCreator(),
    )

    assert missing_key.outputs["jiraOrchestration"]["status"] == "partial"
    assert missing_key.outputs["jiraOrchestration"]["createdTaskCount"] == 1
    assert missing_key.outputs["jiraOrchestration"]["failures"][0]["errorCode"] == (
        "missing_issue_key"
    )

    creator = _FakeExecutionCreator(fail_at=2)
    partial = await create_jira_orchestrate_tasks_from_issue_mappings(
        {
            "jira": {
                "issueMappings": [
                    {"storyId": "STORY-001", "storyIndex": 1, "summary": "First", "issueKey": "MM-501"},
                    {"storyId": "STORY-002", "storyIndex": 2, "summary": "Second", "issueKey": "MM-502"},
                    {"storyId": "STORY-003", "storyIndex": 3, "summary": "Third", "issueKey": "MM-503"},
                ]
            },
            "traceability": {"sourceIssueKey": "MM-404"},
        },
        execution_creator=creator,
    )

    orchestration = partial.outputs["jiraOrchestration"]
    assert orchestration["status"] == "partial"
    assert orchestration["createdTaskCount"] == 1
    assert orchestration["dependencyCount"] == 0
    assert orchestration["failures"][0]["storyId"] == "STORY-002"
    assert orchestration["failures"][0]["errorCode"] == "task_creation_failed"


@pytest.mark.asyncio
async def test_create_jira_implement_tasks_targets_jira_implement_preset():
    creator = _FakeExecutionCreator()

    result = await create_jira_implement_tasks_from_issue_mappings(
        {
            "jira": {
                "issueMappings": [
                    {
                        "storyId": "STORY-001",
                        "storyIndex": 1,
                        "summary": "First",
                        "issueKey": "MM-501",
                        "issueUrl": "https://jira.example/browse/MM-501",
                    },
                    {
                        "storyId": "STORY-002",
                        "storyIndex": 2,
                        "summary": "Second",
                        "issueKey": "MM-502",
                    },
                ]
            },
            "task": {
                "repository": "MoonLadderStudios/MoonMind",
                "runtime": {"mode": "codex_cli"},
                "publish": {
                    "mode": "pr",
                    "mergeAutomation": {"enabled": True},
                },
            },
            "traceability": {
                "sourceIssueKey": "MM-404",
                "sourceBriefRef": "spec.md (Input)",
            },
        },
        execution_creator=creator,
    )

    assert result.status == "COMPLETED"
    orchestration = result.outputs["jiraOrchestration"]
    assert orchestration["status"] == "completed"
    assert orchestration["createdTaskCount"] == 2
    assert orchestration["createdWorkflowCount"] == 2
    assert orchestration["dependencyCount"] == 1
    assert orchestration["workflows"] == orchestration["tasks"]
    assert orchestration["workflowMappings"] == orchestration["tasks"]
    assert orchestration["tasks"][0]["dependsOn"] == []
    assert orchestration["tasks"][1]["dependsOn"] == ["mm:story-1"]

    first_request = creator.requests[0]
    assert first_request["idempotency_key"] == (
        "jira-implement:MM-404:STORY-001:MM-501"
    )
    assert "Jira Implement workflow for MM-501" in first_request["summary"]
    first_task = first_request["initial_parameters"]["workflow"]
    assert first_task["taskTemplate"] == {
        "slug": "jira-implement",
    }
    assert first_task["title"].startswith("Run Jira Implement for MM-501")
    assert "Run Jira Implement for MM-501" in first_task["instructions"]
    assert (
        "Use the existing Jira Implement workflow"
        in first_task["instructions"]
    )
    assert first_task["inputs"]["jira_issue"] == {
        "key": "MM-501",
        "summary": "First",
        "url": "https://jira.example/browse/MM-501",
    }
    assert "jira_issue_key" not in first_task["inputs"]
    assert first_task["inputs"]["constraints"] == (
        "Preserve source issue MM-404 traceability."
    )
    assert first_task["publish"] == {
        "mode": "pr",
        "mergeAutomation": {"enabled": True},
    }

@pytest.mark.asyncio
async def test_create_jira_implement_tasks_orders_dependsOn_by_declared_dependencies():
    """The downstream dependsOn chain must follow declared dependencies.

    The mappings arrive dependent-first (matching a breakdown that listed the
    most-derived story first), but each declares the story it depends on. The
    generated workflow chain must run the prerequisite (STORY-001 / MM-3) before
    the stories that depend on it, so it stays consistent with the Jira blocker
    chain instead of reversing the dependencies.
    """
    creator = _FakeExecutionCreator()

    result = await create_jira_implement_tasks_from_issue_mappings(
        {
            "jira": {
                "issueMappings": [
                    {
                        "storyId": "STORY-003",
                        "storyIndex": 1,
                        "summary": "Third",
                        "issueKey": "MM-1",
                        "dependencies": ["STORY-002"],
                    },
                    {
                        "storyId": "STORY-002",
                        "storyIndex": 2,
                        "summary": "Second",
                        "issueKey": "MM-2",
                        "dependencies": ["STORY-001"],
                    },
                    {
                        "storyId": "STORY-001",
                        "storyIndex": 3,
                        "summary": "First",
                        "issueKey": "MM-3",
                    },
                ]
            },
            "task": {
                "repository": "MoonLadderStudios/MoonMind",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
            },
            "traceability": {"sourceIssueKey": "MM-404"},
        },
        execution_creator=creator,
    )

    orchestration = result.outputs["jiraOrchestration"]
    assert orchestration["dependencyCount"] == 2
    # Workflows are created prerequisite-first regardless of the mapping order.
    assert [task["jiraIssueKey"] for task in orchestration["tasks"]] == [
        "MM-3",
        "MM-2",
        "MM-1",
    ]
    assert orchestration["tasks"][0]["dependsOn"] == []
    assert orchestration["tasks"][1]["dependsOn"] == ["mm:story-1"]
    assert orchestration["tasks"][2]["dependsOn"] == ["mm:story-2"]


@pytest.mark.asyncio
async def test_create_jira_implement_tasks_fans_out_dependsOn_by_declared_dependencies():
    """Fan-out dependencies make each task depend on its declared prerequisites.

    STORY-002 and STORY-003 both depend only on STORY-001. The downstream chain
    must make both depend on STORY-001's workflow rather than chaining STORY-003
    onto STORY-002 (its predecessor in the list), which would fabricate a
    dependency STORY-003 never declared.
    """
    creator = _FakeExecutionCreator()

    result = await create_jira_implement_tasks_from_issue_mappings(
        {
            "jira": {
                "issueMappings": [
                    {
                        "storyId": "STORY-001",
                        "storyIndex": 1,
                        "summary": "First",
                        "issueKey": "MM-1",
                    },
                    {
                        "storyId": "STORY-002",
                        "storyIndex": 2,
                        "summary": "Second",
                        "issueKey": "MM-2",
                        "dependencies": ["STORY-001"],
                    },
                    {
                        "storyId": "STORY-003",
                        "storyIndex": 3,
                        "summary": "Third",
                        "issueKey": "MM-3",
                        "dependencies": ["STORY-001"],
                    },
                ]
            },
            "task": {
                "repository": "MoonLadderStudios/MoonMind",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
            },
            "traceability": {"sourceIssueKey": "MM-404"},
        },
        execution_creator=creator,
    )

    orchestration = result.outputs["jiraOrchestration"]
    assert orchestration["dependencyCount"] == 2
    assert [task["jiraIssueKey"] for task in orchestration["tasks"]] == [
        "MM-1",
        "MM-2",
        "MM-3",
    ]
    assert orchestration["tasks"][0]["dependsOn"] == []
    # Both dependents point at STORY-001 (mm:story-1), not the adjacent task.
    assert orchestration["tasks"][1]["dependsOn"] == ["mm:story-1"]
    assert orchestration["tasks"][2]["dependsOn"] == ["mm:story-1"]
    assert {
        (dependency["fromStoryId"], dependency["toStoryId"])
        for dependency in orchestration["dependencies"]
    } == {("STORY-001", "STORY-002"), ("STORY-001", "STORY-003")}


@pytest.mark.asyncio
async def test_discover_documents_finds_matching_files(tmp_path):
    (tmp_path / "readme.md").write_text("# readme")
    (tmp_path / "notes.txt").write_text("notes")
    (tmp_path / "paper.tex").write_text("\\documentclass")
    (tmp_path / "script.py").write_text("print()")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.md").write_text("deep")

    result = await discover_documents({"directory": str(tmp_path)})

    assert result.status == "COMPLETED"
    assert result.outputs["documentCount"] == 4
    paths = result.outputs["documentPaths"]
    assert "readme.md" in paths
    assert "notes.txt" in paths
    assert "paper.tex" in paths
    assert "sub/deep.md" in paths
    assert "script.py" not in paths


@pytest.mark.asyncio
async def test_discover_documents_respects_custom_extensions(tmp_path):
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.rst").write_text("b")
    (tmp_path / "c.txt").write_text("c")

    result = await discover_documents(
        {"directory": str(tmp_path), "extensions": [".rst"]}
    )

    assert result.status == "COMPLETED"
    assert result.outputs["documentCount"] == 1
    assert result.outputs["documentPaths"] == ["b.rst"]


@pytest.mark.asyncio
async def test_discover_documents_returns_workspace_relative_paths(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# guide")
    nested = docs / "nested"
    nested.mkdir()
    (nested / "notes.txt").write_text("notes")

    monkeypatch.chdir(tmp_path)

    result = await discover_documents({"directory": str(docs)})

    assert result.status == "COMPLETED"
    assert result.outputs["documentPaths"] == ["docs/guide.md", "docs/nested/notes.txt"]


@pytest.mark.asyncio
async def test_discover_documents_accepts_windows_style_relative_directory(
    tmp_path, monkeypatch
):
    docs = tmp_path / "docs" / "Artifacts"
    docs.mkdir(parents=True)
    (docs / "ReportArtifacts.md").write_text("# report")

    monkeypatch.chdir(tmp_path)

    result = await discover_documents({"directory": "docs\\Artifacts"})

    assert result.status == "COMPLETED"
    assert result.outputs["documentPaths"] == ["docs/Artifacts/ReportArtifacts.md"]


@pytest.mark.asyncio
async def test_discover_documents_resolves_relative_directory_from_repo_root(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "repo"
    docs = repo_root / "docs" / "Artifacts"
    docs.mkdir(parents=True)
    (docs / "ReportArtifacts.md").write_text("# report")

    outside = tmp_path / "worker-cwd"
    outside.mkdir()
    monkeypatch.chdir(outside)

    result = await discover_documents(
        {
            "directory": "docs/Artifacts",
            "repoRoot": str(repo_root),
        }
    )

    assert result.status == "COMPLETED"
    assert result.outputs["source"] == "filesystem"
    assert result.outputs["documentPaths"] == ["docs/Artifacts/ReportArtifacts.md"]


@pytest.mark.asyncio
async def test_discover_documents_uses_repository_tree_for_repo_relative_directory(
    tmp_path, monkeypatch
):
    worker_cwd = tmp_path / "worker-cwd"
    worker_cwd.mkdir()
    monkeypatch.chdir(worker_cwd)

    async def fake_discover_github_document_paths(**kwargs):
        assert kwargs["repository"] == "MoonLadderStudios/MoonMind"
        assert kwargs["directory"] == "docs/Artifacts"
        assert kwargs["ref"] == "main"
        return ["docs/Artifacts/ReportArtifacts.md"], "main", False, True

    monkeypatch.setattr(
        story_tools,
        "_discover_github_document_paths",
        fake_discover_github_document_paths,
    )

    result = await discover_documents(
        {
            "directory": "docs\\Artifacts",
            "repository": "MoonLadderStudios/MoonMind",
            "ref": "main",
        }
    )

    assert result.status == "COMPLETED"
    assert result.outputs["source"] == "github"
    assert result.outputs["documentPaths"] == ["docs/Artifacts/ReportArtifacts.md"]


@pytest.mark.asyncio
async def test_discover_documents_fails_when_github_tree_truncated(
    tmp_path, monkeypatch
):
    worker_cwd = tmp_path / "worker-cwd"
    worker_cwd.mkdir()
    monkeypatch.chdir(worker_cwd)

    async def fake_discover_github_document_paths(**kwargs):
        return ["docs/Artifacts/ReportArtifacts.md"], "main", True, True

    monkeypatch.setattr(
        story_tools,
        "_discover_github_document_paths",
        fake_discover_github_document_paths,
    )

    result = await discover_documents(
        {
            "directory": "docs/Artifacts",
            "repository": "MoonLadderStudios/MoonMind",
            "ref": "main",
        }
    )

    assert result.status == "FAILED"
    assert result.outputs["documentPaths"] == []
    assert "truncated" in result.outputs["error"].lower()


@pytest.mark.asyncio
async def test_discover_documents_missing_directory():
    result = await discover_documents({"directory": "/nonexistent/path"})

    assert result.status == "FAILED"
    assert result.outputs["documentPaths"] == []
    assert "does not exist" in result.outputs["error"]


@pytest.mark.asyncio
async def test_discover_documents_missing_input():
    result = await discover_documents({})

    assert result.status == "FAILED"
    assert result.outputs["documentPaths"] == []
    assert "Missing required input" in result.outputs["error"]


@pytest.mark.asyncio
async def test_create_document_update_tasks_from_inline_paths():
    creator = _FakeExecutionCreator()

    result = await create_document_update_tasks_from_paths(
        {
            "documentPaths": [
                "/docs/readme.md",
                "/docs/architecture.tex",
            ],
            "documentUpdateOrchestration": {
                "task": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "runtime": {"mode": "codex_cli"},
                    "publish": {
                        "mode": "pr",
                        "mergeAutomation": {"enabled": True},
                    },
                },
                "traceability": {"sourceDirectory": "/docs"},
            },
        },
        execution_creator=creator,
    )

    assert result.status == "COMPLETED"
    orchestration = result.outputs["documentUpdateOrchestration"]
    assert orchestration["status"] == "completed"
    assert orchestration["documentCount"] == 2
    assert orchestration["createdTaskCount"] == 2
    assert orchestration["createdWorkflowCount"] == 2
    assert orchestration["dependencyCount"] == 1
    assert orchestration["workflows"] == orchestration["tasks"]
    assert orchestration["workflowMappings"] == orchestration["tasks"]

    first = orchestration["tasks"][0]
    assert first["documentPath"] == "/docs/readme.md"
    assert first["dependsOn"] == []

    second = orchestration["tasks"][1]
    assert second["documentPath"] == "/docs/architecture.tex"
    assert second["dependsOn"] == [first["workflowId"]]

    first_task = creator.requests[0]["initial_parameters"]["workflow"]
    assert first_task["publish"]["mode"] == "pr"
    assert first_task["publish"]["mergeAutomation"]["enabled"] is True
    assert "taskTemplate" not in first_task
    assert first_task["skill"] == {
        "name": "document-update",
        "args": {
            "document_path": "/docs/readme.md",
            "source_directory": "/docs",
        },
    }


@pytest.mark.asyncio
async def test_create_document_update_tasks_from_previous_outputs():
    creator = _FakeExecutionCreator()

    result = await create_document_update_tasks_from_paths(
        {
            "documentUpdateOrchestration": {
                "task": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "runtime": {"mode": "codex_cli"},
                    "publish": {"mode": "pr", "mergeAutomation": {"enabled": False}},
                },
                "traceability": {"sourceDirectory": "/docs"},
            }
        },
        {
            "previousOutputs": {
                "documentPaths": ["/docs/guide.md"],
            }
        },
        execution_creator=creator,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["documentUpdateOrchestration"]["createdTaskCount"] == 1
    assert result.outputs["documentUpdateOrchestration"]["createdWorkflowCount"] == 1
    assert creator.requests[0]["initial_parameters"]["workflow"]["inputs"]["document_path"] == "/docs/guide.md"


@pytest.mark.asyncio
async def test_create_document_update_tasks_handles_empty_paths():
    result = await create_document_update_tasks_from_paths(
        {"documentPaths": []},
        execution_creator=_FakeExecutionCreator(),
    )

    assert result.status == "COMPLETED"
    assert result.outputs["documentUpdateOrchestration"]["status"] == "no_downstream_tasks"
    assert result.outputs["documentUpdateOrchestration"]["workflowStatus"] == (
        "no_downstream_workflows"
    )
    assert result.outputs["documentUpdateOrchestration"]["createdTaskCount"] == 0
    assert result.outputs["documentUpdateOrchestration"]["createdWorkflowCount"] == 0


@pytest.mark.asyncio
async def test_create_document_update_tasks_reports_partial_failures():
    creator = _FakeExecutionCreator(fail_at=2)

    result = await create_document_update_tasks_from_paths(
        {
            "documentPaths": [
                "/docs/a.md",
                "/docs/b.md",
                "/docs/c.md",
            ],
            "documentUpdateOrchestration": {
                "task": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "runtime": {"mode": "codex_cli"},
                    "publish": {"mode": "pr"},
                },
                "traceability": {"sourceDirectory": "/docs"},
            },
        },
        execution_creator=creator,
    )

    orchestration = result.outputs["documentUpdateOrchestration"]
    assert orchestration["status"] == "partial"
    assert orchestration["createdTaskCount"] == 1
    assert orchestration["createdWorkflowCount"] == 1
    assert orchestration["dependencyCount"] == 0
    assert orchestration["failures"][0]["documentPath"] == "/docs/b.md"
    assert orchestration["failures"][0]["errorCode"] == "task_creation_failed"


@pytest.mark.asyncio
async def test_create_document_update_tasks_requires_execution_creator():
    with pytest.raises(ValueError, match="execution_creator is required"):
        await create_document_update_tasks_from_paths({"documentPaths": ["/docs/a.md"]})


class _FakeAssessmentArtifactService:
    """Minimal artifact service exposing read-by-ref for verdict resolution."""

    def __init__(self, payloads: dict[str, Any]) -> None:
        self._payloads = payloads
        self.read_calls: list[str] = []

    async def read(
        self,
        *,
        artifact_id: str,
        principal: str,
        allow_restricted_raw: bool = False,
    ) -> tuple[object, bytes]:
        self.read_calls.append(artifact_id)
        payload = self._payloads[artifact_id]
        return object(), json.dumps(payload).encode("utf-8")


class _FakeMappingAssessmentArtifactService(_FakeAssessmentArtifactService):
    async def read(
        self,
        *,
        artifact_id: str,
        principal: str,
        allow_restricted_raw: bool = False,
    ) -> tuple[object, dict[str, Any]]:
        self.read_calls.append(artifact_id)
        return object(), self._payloads[artifact_id]


@pytest.mark.asyncio
async def test_update_jira_issue_status_resolves_verdict_by_artifact_ref() -> None:
    # No local file and no in-payload verdict: the durable artifact ref is the
    # only source. This is the bridge-compatible path (agent workspace not shared).
    artifact_service = _FakeAssessmentArtifactService(
        {"art_assessment_1": {"verdict": "FULLY_IMPLEMENTED"}}
    )
    service = _FakeJiraService()

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1139",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": "artifacts/missing-assessment.json",
            "assessmentArtifactRef": "art_assessment_1",
        },
        {"temporal_artifact_service": artifact_service},
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "skipped"
    assert result.outputs["assessmentVerdict"] == "FULLY_IMPLEMENTED"
    assert artifact_service.read_calls == ["art_assessment_1"]
    assert service.get_issue_requests == []


@pytest.mark.asyncio
async def test_update_jira_issue_status_blocks_by_artifact_ref_blocked_verdict() -> None:
    artifact_service = _FakeAssessmentArtifactService(
        {"art_assessment_2": {"verdict": "BLOCKED"}}
    )
    service = _FakeJiraService()

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1139",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": "artifacts/missing-assessment.json",
            "previousOutputs": {"assessmentArtifactRef": "art_assessment_2"},
        },
        {"temporal_artifact_service": artifact_service},
        jira_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["assessmentVerdict"] == "BLOCKED"
    assert service.transition_requests == []


@pytest.mark.asyncio
async def test_update_jira_issue_status_ref_missing_verdict_preserves_unavailable() -> None:
    # An artifact that resolves but has no verdict must NOT flip the
    # "unavailable" result to a proceed decision — behavior stays identical to
    # today for payloads that carry no usable verdict.
    artifact_service = _FakeAssessmentArtifactService(
        {"art_assessment_3": {"summary": "no verdict here"}}
    )
    service = _FakeJiraService()

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1139",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": "artifacts/missing-assessment.json",
            "assessmentArtifactRef": "art_assessment_3",
        },
        {"temporal_artifact_service": artifact_service},
        jira_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert "assessment verdict" in result.outputs["summary"].lower()


@pytest.mark.asyncio
async def test_update_jira_issue_status_without_ref_or_service_stays_unavailable() -> None:
    # No local file, no ref, no artifact service: the pre-existing fail-safe
    # behavior is preserved (no regression for in-flight runs carrying no ref).
    service = _FakeJiraService()

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1139",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": "artifacts/missing-assessment.json",
        },
        jira_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"


@pytest.mark.asyncio
async def test_check_jira_blockers_resolves_and_forwards_artifact_ref() -> None:
    artifact_service = _FakeAssessmentArtifactService(
        {"art_assessment_4": {"verdict": "NOT_IMPLEMENTED"}}
    )
    service = _FakeJiraService()
    service.issue_responses["MM-1139"] = {
        "key": "MM-1139",
        "fields": {"status": {"id": "1", "name": "Backlog"}, "issuelinks": []},
    }

    result = await check_jira_blockers(
        {
            "targetIssueKey": "MM-1139",
            "assessmentArtifactPath": "artifacts/missing-assessment.json",
            "assessmentArtifactRef": "art_assessment_4",
        },
        {"temporal_artifact_service": artifact_service},
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "continue"
    assert result.outputs["assessmentVerdict"] == "NOT_IMPLEMENTED"
    # The ref is forwarded so the later In Progress step can resolve by ref too.
    assert result.outputs["assessmentArtifactRef"] == "art_assessment_4"


@pytest.mark.asyncio
async def test_update_jira_issue_status_prefers_in_payload_verdict_over_ref() -> None:
    # The compact in-payload verdict is the fast path; the artifact must not be
    # read when a verdict is already present in previousOutputs.
    artifact_service = _FakeAssessmentArtifactService(
        {"art_assessment_5": {"verdict": "NOT_IMPLEMENTED"}}
    )
    service = _FakeJiraService()

    result = await update_jira_issue_status(
        {
            "issueKey": "MM-1139",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": "artifacts/missing-assessment.json",
            "previousOutputs": {
                "assessmentVerdict": "FULLY_IMPLEMENTED",
                "assessmentArtifactRef": "art_assessment_5",
            },
        },
        {"temporal_artifact_service": artifact_service},
        jira_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "skipped"
    assert result.outputs["assessmentVerdict"] == "FULLY_IMPLEMENTED"
    assert artifact_service.read_calls == []


@pytest.mark.asyncio
async def test_update_github_issue_status_resolves_verdict_by_artifact_ref() -> None:
    # GitHub In Progress gate resolves the verdict via the durable artifact ref
    # when the local handoff file is unavailable across the step boundary.
    artifact_service = _FakeAssessmentArtifactService(
        {"art_gh_assessment": {"verdict": "FULLY_IMPLEMENTED"}}
    )
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "mode": "start",
            "assessmentArtifactPath": "artifacts/missing-assessment.json",
            "assessmentArtifactRef": "art_gh_assessment",
        },
        {"temporal_artifact_service": artifact_service},
        github_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "skipped"
    assert result.outputs["assessmentVerdict"] == "FULLY_IMPLEMENTED"
    assert artifact_service.read_calls == ["art_gh_assessment"]


@pytest.mark.asyncio
async def test_update_github_issue_status_resolves_mapping_artifact_ref() -> None:
    artifact_service = _FakeMappingAssessmentArtifactService(
        {"art_gh_assessment_mapping": {"verdict": "FULLY_IMPLEMENTED"}}
    )
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "mode": "start",
            "assessmentArtifactRef": "art_gh_assessment_mapping",
        },
        {"temporal_artifact_service": artifact_service},
        github_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    assert result.outputs["decision"] == "skipped"
    assert result.outputs["assessmentVerdict"] == "FULLY_IMPLEMENTED"
    assert artifact_service.read_calls == ["art_gh_assessment_mapping"]


@pytest.mark.asyncio
async def test_update_github_issue_status_blocks_unavailable_artifact_ref() -> None:
    service = _FakeGitHubService()

    result = await update_github_issue_status(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "mode": "start",
            "assessmentArtifactRef": "art_gh_assessment_missing_service",
        },
        github_service_factory=lambda: service,
    )

    assert result.status == "FAILED"
    assert result.outputs["decision"] == "blocked"
    assert service.create_issue_requests == []


@pytest.mark.asyncio
async def test_check_github_issue_blockers_forwards_artifact_ref() -> None:
    # The verdict + durable ref are forwarded unconditionally so the later
    # In Progress step can resolve by ref regardless of the blocker outcome.
    artifact_service = _FakeAssessmentArtifactService(
        {"art_gh_assessment_2": {"verdict": "NOT_IMPLEMENTED"}}
    )
    service = _FakeGitHubService()

    result = await check_github_issue_blockers(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "issueNumber": 1067,
            "assessmentArtifactPath": "artifacts/missing-assessment.json",
            "assessmentArtifactRef": "art_gh_assessment_2",
        },
        {"temporal_artifact_service": artifact_service},
        github_service_factory=lambda: service,
    )

    assert result.outputs["assessmentVerdict"] == "NOT_IMPLEMENTED"
    assert result.outputs["assessmentArtifactRef"] == "art_gh_assessment_2"
