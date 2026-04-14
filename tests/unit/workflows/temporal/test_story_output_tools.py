from __future__ import annotations

from typing import Any

import pytest

from moonmind.workflows.temporal.story_output_tools import (
    create_jira_issues_from_stories,
)


class _FakeJiraService:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def create_issue(self, request):
        self.requests.append(request)
        return {
            "created": True,
            "issueKey": f"MM-{len(self.requests)}",
            "issueId": str(len(self.requests)),
            "self": f"https://jira.example/rest/api/3/issue/{len(self.requests)}",
        }


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
    assert result.outputs["push_status"] == "pushed"
    assert result.outputs["push_branch"] == "breakdown-branch"
