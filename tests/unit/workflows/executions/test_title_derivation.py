from __future__ import annotations

import sys
from pathlib import Path

import pytest

from moonmind.workflows.executions.title_derivation import (
    is_generic_title,
    synthesize_workflow_title,
)


@pytest.mark.parametrize(
    "title",
    [
        None,
        "",
        "Run",
        "run",
        " Run ",
        "Run!",
        "New-run",
        "New_run",
        "New run",
        "Untitled",
        "Untitled run",
        "Workflow",
        "New workflow",
        " new workflow. ",
    ],
)
def test_classifies_low_information_titles(title: str | None) -> None:
    assert is_generic_title(title)


@pytest.mark.parametrize(
    "title",
    ["Verify KANDY-123", "Fix failing CI on auth redirect", "Run MM-123 checks"],
)
def test_classifies_meaningful_titles(title: str) -> None:
    assert not is_generic_title(title)


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ({"label": "Custom Label"}, "Custom Label: KANDY-123"),
        ({"displayName": "Jira reviewer"}, "Jira reviewer: KANDY-123"),
        ({"name": "jira-pr-verify"}, "Jira PR Verify: KANDY-123"),
        ({"id": "fix-ci"}, "Fix CI: KANDY-123"),
        ({}, "Review Step: KANDY-123"),
    ],
)
def test_derives_capability_labels(tool: dict[str, object], expected: str) -> None:
    steps = [{"title": "Review Step"}] if not tool else []

    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={"inputs": {"issueKey": "KANDY-123"}},
            normalized_tool=tool,
            normalized_steps=steps,
        )
        == expected
    )


def test_uses_workflow_tool_skill_git_steps_and_top_level_targets() -> None:
    payload = {
        "workflow": {
            "inputs": {"ignored": "plain text"},
            "tool": {"inputs": {"issueUrl": "https://example.atlassian.net/browse/KANDY-123"}},
            "skill": {"inputs": {"pullRequestUrl": "https://github.com/org/repo/pull/456"}},
            "git": {"startingBranch": "feature/auth-redirect"},
            "steps": [
                {
                    "inputs": {"checkName": "unit-tests"},
                    "tool": {"inputs": {"jobName": "lint"}},
                    "skill": {"inputs": {"branch": "feature/from-step"}},
                }
            ],
        },
        "issueKey": "MM-777",
    }

    assert (
        synthesize_workflow_title(
            current_title="Untitled",
            task_payload=payload,
            normalized_tool={"name": "jira-pr-verify"},
            normalized_steps=[],
        )
        == "Jira PR Verify: KANDY-123 — PR #456"
    )


@pytest.mark.parametrize(
    ("tool_name", "payload", "expected"),
    [
        ("pr-resolver", {"inputs": {"pr": 456}}, "PR Resolver: PR #456"),
        (
            "pr-resolver",
            {"inputs": {"pullRequestUrl": "https://github.com/org/repo/pull/456"}},
            "PR Resolver: PR #456",
        ),
        ("fix-comments", {"inputs": {"pull_request": 456}}, "Fix Comments: PR #456"),
        ("fix-ci", {"inputs": {"prUrl": "https://github.com/org/repo/pull/456"}}, "Fix CI: PR #456"),
    ],
)
def test_extracts_pull_request_targets(
    tool_name: str, payload: dict[str, object], expected: str
) -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload=payload,
            normalized_tool={"name": tool_name},
            normalized_steps=[],
        )
        == expected
    )


def test_restricts_bare_numeric_pr_targets_to_explicit_pr_fields() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={"inputs": {"port": 8080, "issue_number": 123}},
            normalized_tool={"name": "future-skill"},
            normalized_steps=[],
        )
        is None
    )

    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={"inputs": {"pr": 456}},
            normalized_tool={"name": "future-skill"},
            normalized_steps=[],
        )
        == "Future Skill: PR #456"
    )


def test_rejects_extremely_long_pr_numbers_without_integer_conversion() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={"inputs": {"pr": "1" * 5000}},
            normalized_tool={"name": "pr-resolver"},
            normalized_steps=[],
        )
        is None
    )


def test_pr_url_extraction_requires_number_boundary() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={
                "inputs": {"pullRequestUrl": "https://github.com/org/repo/pull/456abc"}
            },
            normalized_tool={"name": "pr-resolver"},
            normalized_steps=[],
        )
        is None
    )

    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={
                "inputs": {
                    "pullRequestUrl": "https://github.com/org/repo/pull/456?diff=split"
                }
            },
            normalized_tool={"name": "pr-resolver"},
            normalized_steps=[],
        )
        == "PR Resolver: PR #456"
    )


def test_combines_issue_and_pull_request_in_priority_order() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={
                "inputs": {
                    "pullRequestUrl": "https://github.com/org/repo/pull/456",
                    "issueKey": "KANDY-123",
                }
            },
            normalized_tool={"name": "jira-pr-verify"},
            normalized_steps=[],
        )
        == "Jira PR Verify: KANDY-123 — PR #456"
    )


def test_orders_targets_deterministically_and_renders_at_most_two() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Workflow",
            task_payload={
                "inputs": {
                    "checkName": "unit-tests",
                    "pr": 456,
                    "issueKey": "KANDY-123",
                }
            },
            normalized_tool={"name": "fix-ci"},
            normalized_steps=[],
        )
        == "Fix CI: KANDY-123 — PR #456"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"git": {"startingBranch": "feature/auth-redirect"}}, "Fix Merge Conflicts: feature/auth-redirect"),
        ({"inputs": {"headBranch": "feature/head"}}, "Fix Merge Conflicts: feature/head"),
        ({"inputs": {"checkName": "unit-tests"}}, "Fix Merge Conflicts: failing check: unit-tests"),
        ({"inputs": {"jobName": "lint"}}, "Fix Merge Conflicts: failing check: lint"),
    ],
)
def test_extracts_branch_ref_check_and_job_targets(
    payload: dict[str, object], expected: str
) -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload=payload,
            normalized_tool={"name": "fix-merge-conflicts"},
            normalized_steps=[],
        )
        == expected
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"instructions": "Verify KANDY-123 and report back."}, "Future Skill: KANDY-123"),
        (
            {"instructions": "Review https://github.com/org/repo/pull/456"},
            "Future Skill: PR #456",
        ),
        ({"instructions": "Review pull request #456"}, "Future Skill: PR #456"),
        ({"instructions": "Review #456"}, "Future Skill: PR #456"),
    ],
)
def test_regex_fallback_runs_after_structured_targets(
    payload: dict[str, object], expected: str
) -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload=payload,
            normalized_tool={"name": "future-skill"},
            normalized_steps=[],
        )
        == expected
    )

    with_structured = dict(payload)
    with_structured["inputs"] = {"issueKey": "KANDY-123"}
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload=with_structured,
            normalized_tool={"name": "future-skill"},
            normalized_steps=[],
        )
        == "Future Skill: KANDY-123"
    )


def test_regex_fallback_can_extract_issue_and_pr_from_same_text() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={"instructions": "Fix MM-123 and review PR #456"},
            normalized_tool={"name": "jira-pr-verify"},
            normalized_steps=[],
        )
        == "Jira PR Verify: MM-123 — PR #456"
    )


def test_structured_github_shorthand_is_only_pr_target_for_pr_fields() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={"inputs": {"comment": "#456"}},
            normalized_tool={"name": "future-skill"},
            normalized_steps=[],
        )
        is None
    )

    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={"inputs": {"pr": "#456"}},
            normalized_tool={"name": "future-skill"},
            normalized_steps=[],
        )
        == "Future Skill: PR #456"
    )


def test_single_step_title_fallback_requires_exactly_one_step() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={"inputs": {"issueKey": "KANDY-123"}},
            normalized_tool={},
            normalized_steps=[{"title": "First Step"}, {"title": "Second Step"}],
        )
        == "Workflow: KANDY-123"
    )


def test_generic_tool_title_falls_back_to_skill_name() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={"inputs": {"issueKey": "KANDY-123"}},
            normalized_tool={
                "type": "skill",
                "name": "jira-pr-verify",
                "title": "Run",
            },
            normalized_steps=[],
        )
        == "Jira PR Verify: KANDY-123"
    )


def test_preserves_meaningful_explicit_title() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Verify auth redirect fix",
            task_payload={"inputs": {"issueKey": "KANDY-123"}},
            normalized_tool={"name": "jira-verify"},
            normalized_steps=[],
        )
        == "Verify auth redirect fix"
    )


def test_excludes_repository_fields_and_caps_synthesized_title() -> None:
    title = synthesize_workflow_title(
        current_title="Run",
        task_payload={
            "repository": "MoonLadderStudios/MoonMind",
            "repo": "other/repo",
            "repoRef": "repo-ref",
            "inputs": {"branch": "feature/" + "x" * 180},
        },
        normalized_tool={"name": "fix-merge-conflicts"},
        normalized_steps=[],
    )

    assert title is not None
    assert len(title) == 150
    assert "MoonMind" not in title
    assert "other/repo" not in title
    assert "repo-ref" not in title
    assert title.startswith("Fix Merge Conflicts: feature/")


def test_unknown_future_skill_with_no_target_returns_none() -> None:
    assert (
        synthesize_workflow_title(
            current_title="Run",
            task_payload={"inputs": {"repository": "MoonLadderStudios/MoonMind"}},
            normalized_tool={"name": "future-skill"},
            normalized_steps=[],
        )
        is None
    )


def test_title_synthesis_has_no_external_or_skill_runtime_imports() -> None:
    before = set(sys.modules)
    synthesize_workflow_title(
        current_title="Run",
        task_payload={"inputs": {"issueKey": "KANDY-123"}},
        normalized_tool={"name": "jira-verify"},
        normalized_steps=[],
    )
    loaded = set(sys.modules) - before

    assert not any(name.startswith("jira") for name in loaded)
    assert not any("github" in name.lower() for name in loaded)
    assert ".agents/skills" not in str(Path(__file__).resolve())
