from __future__ import annotations

import json
from typing import Any

import pytest

from moonmind.workflows.temporal.story_output_tools import (
    check_jira_blockers,
    create_jira_issues_from_stories,
    create_jira_orchestrate_tasks_from_issue_mappings,
)

class _FakeJiraService:
    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.subtask_requests: list[Any] = []
        self.link_requests: list[Any] = []
        self.search_requests: list[Any] = []
        self.search_response: Any = {"issues": []}
        self.issue_responses: dict[str, Any] = {}
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

    async def list_create_issue_types(self, request):
        return {
            "issueTypes": [
                {"id": "10005", "name": "Story"},
                {"id": "10006", "name": "Task"},
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
async def test_create_jira_issues_blocks_story_breakdown_without_source_reference():
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

    assert service.requests == []
    assert result.outputs["storyOutput"]["status"] == "fallback"
    assert "requires sourceReference.path" in result.outputs["storyOutput"]["reason"]
    assert "STORY-001" in result.outputs["storyOutput"]["reason"]

@pytest.mark.asyncio
async def test_create_jira_issues_accepts_string_source_reference_from_breakdown():
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
                    "sourceReference": "docs/Designs/RuntimeTypes.md",
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
                    "sourceReference": {"path": "docs/Designs/RuntimeTypes.md"},
                },
                {
                    "summary": "Second",
                    "sourceReference": {"path": "docs/Designs/RuntimeTypes.md"},
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
async def test_check_jira_blockers_ignores_outward_links_from_target_issue():
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
                    "outwardIssue": {
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
async def test_check_jira_blockers_blocks_only_on_inward_unresolved_blocks_link():
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

@pytest.mark.asyncio
async def test_check_jira_blockers_fetches_missing_blocker_status_and_allows_done():
    service = _FakeJiraService()
    service.issue_responses["MM-2"] = {
        "key": "MM-2",
        "fields": {
            "issuelinks": [
                {
                    "type": {"name": "Blocks"},
                    "inwardIssue": {"key": "MM-1"},
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
    assert orchestration["dependencyCount"] == 2
    assert [task["jiraIssueKey"] for task in orchestration["tasks"]] == [
        "MM-501",
        "MM-502",
        "MM-503",
    ]
    assert orchestration["tasks"][0]["dependsOn"] == []
    assert orchestration["tasks"][1]["dependsOn"] == ["mm:story-1"]
    assert orchestration["tasks"][2]["dependsOn"] == ["mm:story-2"]
    assert orchestration["traceability"]["sourceIssueKey"] == "MM-404"

    assert creator.requests[0]["initial_parameters"]["task"].get("dependsOn") is None
    assert creator.requests[1]["initial_parameters"]["task"]["dependsOn"] == ["mm:story-1"]
    assert creator.requests[2]["initial_parameters"]["task"]["dependsOn"] == ["mm:story-2"]
    assert creator.requests[0]["idempotency_key"] == (
        "jira-orchestrate:MM-404:STORY-001:MM-501"
    )
    assert "Run Jira Orchestrate for MM-501" in creator.requests[0]["title"]
    first_parameters = creator.requests[0]["initial_parameters"]
    assert first_parameters["publishMode"] == "pr"
    assert first_parameters["task"]["publish"] == {
        "mode": "pr",
        "mergeAutomation": {"enabled": True},
    }
    assert "merge_automation" not in first_parameters["task"]["publish"]
    assert "MM-404" in first_parameters["task"]["instructions"]

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
    assert first_parameters["task"]["publish"] == {"mode": "pr"}

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
    assert first_parameters["task"]["publish"] == {"mode": "pr"}

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
    task = creator.requests[0]["initial_parameters"]["task"]
    assert "orchestration_mode" not in task["inputs"]
    assert task["inputs"]["constraints"] == "Preserve source issue MM-404 traceability."
    assert "Source Jira issue: MM-404." in task["instructions"]

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
    assert one.outputs["jiraOrchestration"]["dependencyCount"] == 0
    assert one.outputs["jiraOrchestration"]["tasks"][0]["dependsOn"] == []

    zero = await create_jira_orchestrate_tasks_from_issue_mappings(
        {"jira": {"issueMappings": []}, "traceability": {"sourceIssueKey": "MM-404"}},
        execution_creator=_FakeExecutionCreator(),
    )

    assert zero.outputs["jiraOrchestration"]["status"] == "no_downstream_tasks"
    assert zero.outputs["jiraOrchestration"]["createdTaskCount"] == 0
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
