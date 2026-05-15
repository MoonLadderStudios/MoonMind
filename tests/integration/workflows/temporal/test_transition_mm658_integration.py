"""Integration tests for the MM-658 → "In Progress" run-time orchestration.

The "integration" tier here exercises the orchestration driver
(`transition_mm658_to_in_progress`) end-to-end against a stubbed trusted Jira
tool surface that conforms to `contracts/transition-mm658.md`. Every scenario
asserts the run-report contract from `data-model.md` and the single-issue
invariant.

Covers tasks T014–T024 plus T030 verification.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from moonmind.integrations.jira.errors import JiraToolError
from moonmind.workflows.temporal.transition_mm658 import (
    MM658_ISSUE_KEY,
    TrustedJiraToolUnavailable,
    transition_mm658_to_in_progress,
)


# Hermetic — no compose service is required for these stub-driven tests.
pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _transition(
    *,
    transition_id: str,
    name: str,
    to_name: str,
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": transition_id,
        "name": name,
        "to": {"name": to_name},
        "fields": fields or {},
    }


def _issue(status_name: str) -> dict[str, Any]:
    return {
        "key": MM658_ISSUE_KEY,
        "fields": {"status": {"name": status_name}},
    }


class _StubTrustedJira:
    """Stub of MoonMind's trusted Jira tool surface for SCN-001..004 + edges."""

    def __init__(
        self,
        *,
        get_issue_responses: list[Any] | None = None,
        get_transitions_response: Any = None,
        transition_issue_response: Any = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._get_issue_queue = list(get_issue_responses or [])
        self._get_transitions = get_transitions_response
        self._transition_issue = transition_issue_response

    async def get_issue(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("jira.get_issue", dict(kwargs)))
        if not self._get_issue_queue:
            raise AssertionError("Unexpected jira.get_issue call (queue empty)")
        item = self._get_issue_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def get_transitions(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("jira.get_transitions", dict(kwargs)))
        if isinstance(self._get_transitions, Exception):
            raise self._get_transitions
        if self._get_transitions is None:
            raise AssertionError("Unexpected jira.get_transitions call (no stub)")
        return self._get_transitions

    async def transition_issue(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("jira.transition_issue", dict(kwargs)))
        if isinstance(self._transition_issue, Exception):
            raise self._transition_issue
        return self._transition_issue or {"transitioned": True}


def _assert_single_issue_invariant(jira: _StubTrustedJira) -> None:
    for tool_name, kwargs in jira.calls:
        assert kwargs.get("issue_key") == MM658_ISSUE_KEY, (
            f"{tool_name} called with non-MM-658 issue_key: {kwargs!r}"
        )
    called_tools = {name for name, _ in jira.calls}
    assert "jira.edit_issue" not in called_tools
    assert "jira.add_comment" not in called_tools
    assert "jira.create_subtask" not in called_tools


def _assert_no_secret_strings(report: dict[str, Any]) -> None:
    serialized = repr(report)
    forbidden = [
        "ghp_AAAA",
        "github_pat_AAAA",
        "AIzaSecret",
        "ATATT",
        "AKIA",
        "Bearer ghp_",
        "token=secretvalue",
        "password=hunter2",
        "BEGIN PRIVATE KEY",
    ]
    for needle in forbidden:
        assert needle not in serialized, (
            f"Forbidden secret pattern leaked into report: {needle}"
        )


# ---------------------------------------------------------------------------
# T014 / T030 — SCN-001 transition path
# ---------------------------------------------------------------------------


def test_scn_001_transition_path_emits_one_transition_call_and_verifies_final_status() -> None:
    jira = _StubTrustedJira(
        get_issue_responses=[_issue("To Do"), _issue("In Progress")],
        get_transitions_response={
            "transitions": [
                _transition(transition_id="31", name="Start Work", to_name="In Progress"),
            ]
        },
    )

    outcome = asyncio.run(transition_mm658_to_in_progress(trusted_jira=jira))
    report = outcome.to_dict()

    # Calls 1→4 from contracts/transition-mm658.md.
    assert [name for name, _ in jira.calls] == [
        "jira.get_issue",
        "jira.get_transitions",
        "jira.transition_issue",
        "jira.get_issue",
    ]

    # Exactly one `jira.transition_issue` with empty fields + update + correct ID.
    transition_calls = [k for n, k in jira.calls if n == "jira.transition_issue"]
    assert len(transition_calls) == 1
    assert transition_calls[0]["transition_id"] == "31"
    assert transition_calls[0]["fields"] == {}
    assert transition_calls[0]["update"] == {}

    # discovery used expand_fields=True
    discovery_calls = [k for n, k in jira.calls if n == "jira.get_transitions"]
    assert discovery_calls[0]["expand_fields"] is True

    # Run-report contract.
    assert report["issueKey"] == MM658_ISSUE_KEY
    assert report["priorStatus"] == "To Do"
    assert report["action"] == "transitioned"
    assert report["outcome"] == "transitioned"
    assert report["transition"] == {
        "id": "31",
        "name": "Start Work",
        "toStatusName": "In Progress",
    }
    assert report["verifiedFinalStatus"] == "In Progress"
    assert report["availableTransitions"] == []
    assert report["missingFields"] == []
    assert report["errorReason"] is None

    _assert_single_issue_invariant(jira)
    _assert_no_secret_strings(report)


# ---------------------------------------------------------------------------
# T015 — SCN-002 already-in-progress no-op
# ---------------------------------------------------------------------------


def test_scn_002_already_in_progress_emits_no_op() -> None:
    jira = _StubTrustedJira(
        get_issue_responses=[_issue("In Progress")],
        get_transitions_response=None,  # must never be called.
    )

    outcome = asyncio.run(transition_mm658_to_in_progress(trusted_jira=jira))
    report = outcome.to_dict()

    assert [name for name, _ in jira.calls] == ["jira.get_issue"]
    assert report["action"] == "noop_already_in_progress"
    assert report["outcome"] == "noop_already_in_progress"
    assert report["priorStatus"] == "In Progress"
    assert report["verifiedFinalStatus"] == "In Progress"
    assert report["transition"] == {"id": None, "name": None, "toStatusName": None}

    _assert_single_issue_invariant(jira)


# ---------------------------------------------------------------------------
# T016 — SCN-003 no matching transition
# ---------------------------------------------------------------------------


def test_scn_003_no_matching_transition() -> None:
    jira = _StubTrustedJira(
        get_issue_responses=[_issue("Done")],
        get_transitions_response={
            "transitions": [
                _transition(transition_id="11", name="Reopen", to_name="To Do"),
                _transition(transition_id="21", name="Close", to_name="Closed"),
            ]
        },
    )

    outcome = asyncio.run(transition_mm658_to_in_progress(trusted_jira=jira))
    report = outcome.to_dict()

    assert report["outcome"] == "stopped:no_matching_transition"
    assert report["action"] == "stopped"
    available = report["availableTransitions"]
    assert {item["toStatusName"] for item in available} == {"To Do", "Closed"}
    # No mutation:
    assert all(name != "jira.transition_issue" for name, _ in jira.calls)
    _assert_single_issue_invariant(jira)


# ---------------------------------------------------------------------------
# T017 — SCN-004 ambiguous transition
# ---------------------------------------------------------------------------


def test_scn_004_ambiguous_transition() -> None:
    jira = _StubTrustedJira(
        get_issue_responses=[_issue("To Do")],
        get_transitions_response={
            "transitions": [
                _transition(transition_id="31", name="Start", to_name="In Progress"),
                _transition(transition_id="32", name="Resume", to_name="In Progress"),
            ]
        },
    )

    outcome = asyncio.run(transition_mm658_to_in_progress(trusted_jira=jira))
    report = outcome.to_dict()

    assert report["outcome"] == "stopped:ambiguous_transition"
    assert report["action"] == "stopped"
    candidate_ids = {item["id"] for item in report["availableTransitions"]}
    assert candidate_ids == {"31", "32"}
    # No mutation.
    assert all(name != "jira.transition_issue" for name, _ in jira.calls)
    _assert_single_issue_invariant(jira)


# ---------------------------------------------------------------------------
# T018 — stopped:tool_unavailable
# ---------------------------------------------------------------------------


def test_edge_tool_unavailable_emits_no_calls() -> None:
    outcome = asyncio.run(transition_mm658_to_in_progress(trusted_jira=None))
    report = outcome.to_dict()

    assert report["outcome"] == "stopped:tool_unavailable"
    assert report["action"] == "stopped"
    assert report["errorClass"] is not None
    # Sanitized error reason: redacted, no secret pattern.
    assert report["errorReason"] is not None
    _assert_no_secret_strings(report)


def test_edge_tool_unavailable_via_explicit_signal() -> None:
    class _DisabledStub:
        async def get_issue(self, **kwargs: Any) -> dict[str, Any]:
            raise TrustedJiraToolUnavailable("Trusted Jira binding disabled.")

        async def get_transitions(self, **kwargs: Any) -> dict[str, Any]:
            raise TrustedJiraToolUnavailable("Trusted Jira binding disabled.")

        async def transition_issue(self, **kwargs: Any) -> dict[str, Any]:
            raise TrustedJiraToolUnavailable("Trusted Jira binding disabled.")

    outcome = asyncio.run(transition_mm658_to_in_progress(trusted_jira=_DisabledStub()))
    report = outcome.to_dict()
    assert report["outcome"] == "stopped:tool_unavailable"


# ---------------------------------------------------------------------------
# T019 — stopped:issue_not_found
# ---------------------------------------------------------------------------


def test_edge_issue_not_found() -> None:
    not_found = JiraToolError(
        "Issue MM-658 was not found.",
        code="jira_not_found",
        status_code=404,
        action="get_issue",
    )

    jira = _StubTrustedJira(get_issue_responses=[not_found])

    outcome = asyncio.run(transition_mm658_to_in_progress(trusted_jira=jira))
    report = outcome.to_dict()

    assert report["outcome"] == "stopped:issue_not_found"
    assert report["action"] == "stopped"
    # No mutation tool calls were issued.
    assert all(
        name not in {"jira.transition_issue", "jira.get_transitions"}
        for name, _ in jira.calls
    )
    _assert_no_secret_strings(report)


# ---------------------------------------------------------------------------
# T020 — stopped:auth_or_permission
# ---------------------------------------------------------------------------


def test_edge_auth_or_permission_redacts_secret_strings() -> None:
    auth_error = JiraToolError(
        "Authorization: Bearer ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA failed",
        code="jira_auth_failed",
        status_code=401,
        action="get_issue",
    )

    jira = _StubTrustedJira(get_issue_responses=[auth_error])

    outcome = asyncio.run(transition_mm658_to_in_progress(trusted_jira=jira))
    report = outcome.to_dict()

    assert report["outcome"] == "stopped:auth_or_permission"
    assert "ghp_AAAA" not in repr(report)
    assert "Bearer ghp_" not in repr(report)


# ---------------------------------------------------------------------------
# Validation failures are permanent operator-actionable failures.
# ---------------------------------------------------------------------------


def test_edge_validation_failure_during_transition_is_not_transient() -> None:
    validation_error = JiraToolError(
        "transitionId is not available for the target issue.",
        code="jira_validation_failed",
        status_code=422,
        action="transition_issue",
    )
    jira = _StubTrustedJira(
        get_issue_responses=[_issue("To Do")],
        get_transitions_response={
            "transitions": [
                _transition(transition_id="31", name="Start", to_name="In Progress"),
            ],
        },
        transition_issue_response=validation_error,
    )

    outcome = asyncio.run(transition_mm658_to_in_progress(trusted_jira=jira))
    report = outcome.to_dict()

    assert report["outcome"] == "stopped:validation_failure"
    assert report["action"] == "stopped"
    assert report["priorStatus"] == "To Do"


# ---------------------------------------------------------------------------
# T021 — stopped:transient_failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_edge_transient_failure_during_discovery(status_code: int) -> None:
    transient = JiraToolError(
        "Transient upstream failure",
        code="jira_request_failed",
        status_code=status_code,
        action="get_transitions",
    )

    jira = _StubTrustedJira(
        get_issue_responses=[_issue("To Do")],
        get_transitions_response=transient,
    )

    outcome = asyncio.run(transition_mm658_to_in_progress(trusted_jira=jira))
    report = outcome.to_dict()

    assert report["outcome"] == "stopped:transient_failure"
    assert report["action"] == "stopped"
    assert report["priorStatus"] == "To Do"
    # `transition_issue` MUST NOT be called.
    assert all(name != "jira.transition_issue" for name, _ in jira.calls)


# ---------------------------------------------------------------------------
# T022 — stopped:missing_required_fields
# ---------------------------------------------------------------------------


def test_edge_missing_required_fields() -> None:
    jira = _StubTrustedJira(
        get_issue_responses=[_issue("To Do")],
        get_transitions_response={
            "transitions": [
                _transition(
                    transition_id="55",
                    name="Begin",
                    to_name="In Progress",
                    fields={
                        "resolution": {"required": True},
                        "priority": {"required": True},
                        "comment": {"required": False},
                    },
                ),
            ]
        },
    )

    outcome = asyncio.run(transition_mm658_to_in_progress(trusted_jira=jira))
    report = outcome.to_dict()

    assert report["outcome"] == "stopped:missing_required_fields"
    assert report["missingFields"] == ["priority", "resolution"]
    # `transition_issue` MUST NOT be called.
    assert all(name != "jira.transition_issue" for name, _ in jira.calls)
    # Field VALUES must never appear in the report.
    assert "values" not in report
    _assert_no_secret_strings(report)


# ---------------------------------------------------------------------------
# T023 — stopped:final_status_mismatch
# ---------------------------------------------------------------------------


def test_edge_final_status_mismatch_after_transition() -> None:
    jira = _StubTrustedJira(
        # Pre-fetch then verification both go through get_issue.
        get_issue_responses=[_issue("To Do"), _issue("Code Review")],
        get_transitions_response={
            "transitions": [
                _transition(transition_id="31", name="Start", to_name="In Progress"),
            ]
        },
    )

    outcome = asyncio.run(transition_mm658_to_in_progress(trusted_jira=jira))
    report = outcome.to_dict()

    assert report["outcome"] == "stopped:final_status_mismatch"
    assert report["action"] == "stopped"
    assert report["verifiedFinalStatus"] == "Code Review"
    # transition_issue WAS called once.
    transition_calls = [k for n, k in jira.calls if n == "jira.transition_issue"]
    assert len(transition_calls) == 1


# ---------------------------------------------------------------------------
# T024 — single-issue invariant across every scenario
# ---------------------------------------------------------------------------


def test_single_issue_invariant_holds_across_all_scenarios() -> None:
    """Every Jira tool call across SCN-001..004 + edges must use issue_key="MM-658"
    and never invoke jira.edit_issue / jira.add_comment / jira.create_subtask.
    """

    scenarios: list[tuple[str, _StubTrustedJira]] = [
        (
            "scn_001",
            _StubTrustedJira(
                get_issue_responses=[_issue("To Do"), _issue("In Progress")],
                get_transitions_response={
                    "transitions": [
                        _transition(transition_id="31", name="Start", to_name="In Progress"),
                    ]
                },
            ),
        ),
        (
            "scn_002",
            _StubTrustedJira(get_issue_responses=[_issue("In Progress")]),
        ),
        (
            "scn_003",
            _StubTrustedJira(
                get_issue_responses=[_issue("Done")],
                get_transitions_response={
                    "transitions": [
                        _transition(transition_id="11", name="Reopen", to_name="To Do"),
                    ]
                },
            ),
        ),
        (
            "scn_004",
            _StubTrustedJira(
                get_issue_responses=[_issue("To Do")],
                get_transitions_response={
                    "transitions": [
                        _transition(transition_id="31", name="Start", to_name="In Progress"),
                        _transition(transition_id="32", name="Resume", to_name="In Progress"),
                    ]
                },
            ),
        ),
    ]

    for tag, stub in scenarios:
        asyncio.run(transition_mm658_to_in_progress(trusted_jira=stub))
        for tool_name, kwargs in stub.calls:
            assert kwargs.get("issue_key") == MM658_ISSUE_KEY, (
                f"{tag}/{tool_name} called with non-MM-658 issue_key: {kwargs!r}"
            )
        called = {name for name, _ in stub.calls}
        assert "jira.edit_issue" not in called
        assert "jira.add_comment" not in called
        assert "jira.create_subtask" not in called
