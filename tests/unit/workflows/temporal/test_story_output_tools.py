from __future__ import annotations

from typing import Any

import pytest

from moonmind.workflows.temporal.story_output_tools import (
    create_jira_issues_from_stories,
)


class _FakeJiraService:
    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.subtask_requests: list[Any] = []
        self.search_requests: list[Any] = []
        self.search_response: Any = {"issues": []}

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
async def test_create_jira_issues_falls_back_to_docs_tmp_when_jira_target_missing():
    result = await create_jira_issues_from_stories(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "targetBranch": "breakdown-branch",
            "startingBranch": "main",
            "storyBreakdownPath": "docs/tmp/story-breakdowns/example/stories.json",
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
        "path": "docs/tmp/story-breakdowns/example/stories.json",
    }
    assert result.outputs["push_status"] == ""
    assert result.outputs["push_branch"] == "breakdown-branch"


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
            "storyBreakdownPath": "docs/tmp/story-breakdowns/example/stories.json",
            "storyOutput": {
                "mode": "jira",
                "jira": {"projectKey": "MM", "issueTypeId": "10001"},
            },
            "stories": [{"summary": "First"}, {"summary": "Second"}],
        },
        jira_service_factory=lambda: service,
    )

    assert result.outputs["storyOutput"]["status"] == "fallback"
    assert result.outputs["storyOutput"]["createdCount"] == 1
    assert result.outputs["jira"]["partial"] is True
    assert result.outputs["jira"]["createdIssues"][0]["issueKey"] == "MM-1"
