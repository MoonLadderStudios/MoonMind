from __future__ import annotations

import pytest

from moonmind.workflows.executions.title_derivation import (
    MAX_WORKFLOW_TITLE_LENGTH,
    is_generic_title,
    synthesize_workflow_title,
)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("", True),
        ("Run", True),
        (" run ", True),
        ("New-run", True),
        ("Untitled", True),
        ("Untitled run", True),
        ("Workflow", True),
        ("New workflow", True),
        ("Verify KANDY-123", False),
        ("Fix failing CI on auth redirect", False),
    ],
)
def test_is_generic_title_ignores_case_punctuation_and_whitespace(
    title: str, expected: bool
) -> None:
    assert is_generic_title(title) is expected


@pytest.mark.parametrize(
    ("skill_name", "inputs", "expected"),
    [
        ("jira-verify", {"issueKey": "KANDY-123"}, "Jira Verify: KANDY-123"),
        (
            "jira-pr-verify",
            {
                "issueKey": "KANDY-123",
                "pullRequestUrl": "https://github.com/org/repo/pull/456",
            },
            "Jira PR Verify: KANDY-123 \u2014 PR #456",
        ),
        ("pr-resolver", {"pr": "456"}, "PR Resolver: PR #456"),
        ("fix-comments", {"pr": "456"}, "Fix Comments: PR #456"),
        ("fix-ci", {"pr": "#456"}, "Fix CI: PR #456"),
        (
            "fix-merge-conflicts",
            {"branch": "feature/auth-redirect"},
            "Fix Merge Conflicts: feature/auth-redirect",
        ),
        (
            "fix-ci",
            {"pr": "456", "checkName": "unit-tests"},
            "Fix CI: PR #456 \u2014 failing check: unit-tests",
        ),
    ],
)
def test_synthesize_workflow_title_for_skill_targets(
    skill_name: str, inputs: dict[str, str], expected: str
) -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={
                "title": "Run",
                "tool": {"type": "skill", "name": skill_name, "inputs": inputs},
            },
            normalized_tool={"type": "skill", "name": skill_name, "inputs": inputs},
        )
        == expected
    )


def test_synthesize_workflow_title_normalizes_jira_issue_url() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={
                "skill": {"id": "jira-verify"},
                "inputs": {
                    "issueUrl": "https://example.atlassian.net/browse/KANDY-123"
                },
            },
            normalized_tool={"type": "skill", "name": "jira-verify"},
        )
        == "Jira Verify: KANDY-123"
    )


def test_synthesize_workflow_title_preserves_meaningful_explicit_title() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Verify auth redirect fix",
            task_payload={
                "title": "Verify auth redirect fix",
                "tool": {"type": "skill", "name": "jira-verify"},
                "inputs": {"issueKey": "KANDY-123"},
            },
            normalized_tool={"type": "skill", "name": "jira-verify"},
        )
        == "Verify auth redirect fix"
    )


def test_synthesize_workflow_title_ignores_repository_fields() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={
                "tool": {
                    "type": "skill",
                    "name": "pr-resolver",
                    "inputs": {
                        "repo": "MoonLadderStudios/MoonMind",
                        "repository": "MoonLadderStudios/MoonMind",
                        "pr": "456",
                    },
                },
            },
            normalized_tool={"type": "skill", "name": "pr-resolver"},
        )
        == "PR Resolver: PR #456"
    )


def test_synthesize_workflow_title_handles_future_skill_target() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={
                "tool": {
                    "type": "skill",
                    "name": "future-skill",
                    "inputs": {"issueKey": "KANDY-123"},
                },
            },
            normalized_tool={"type": "skill", "name": "future-skill"},
        )
        == "Future Skill: KANDY-123"
    )


def test_synthesize_workflow_title_returns_none_without_useful_target() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={
                "instructions": "Run the task.",
                "tool": {"type": "skill", "name": "future-skill"},
            },
            normalized_tool={"type": "skill", "name": "future-skill"},
        )
        is None
    )


def test_synthesize_workflow_title_caps_generated_title() -> None:
    long_branch = "feature/" + ("very-long-branch-name-" * 10)
    title = synthesize_workflow_title(
        current_title="Run",
        task_payload={
            "tool": {
                "type": "skill",
                "name": "fix-merge-conflicts",
                "inputs": {"branch": long_branch},
            },
        },
        normalized_tool={"type": "skill", "name": "fix-merge-conflicts"},
    )

    assert title is not None
    assert len(title) == MAX_WORKFLOW_TITLE_LENGTH
