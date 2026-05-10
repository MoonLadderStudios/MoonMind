"""Unit tests for the inline MM-667 → "In Progress" run-time orchestration helpers.

Covers:
- T008: case-insensitive trimmed matching of `to.name` against `In Progress`
        (zero / exactly-one / >1 / whitespace+case variants)
- T009: required-field detection on the matched transition's `fields` map
- T010: pre-fetch no-op detection when issue `status.name` already equals "In Progress"
- T011: run-report builder shape + redacted ≤500-char `errorReason`
- T012: single-issue invariant (every emitted Jira tool call carries `issue_key="MM-667"`)
- T013: redaction of secret-pattern strings through `redact_sensitive_text`
- T027: verification of `jira.get_transitions(expand_fields=true)` exposing
        `transitions[*].fields`
- T028: verification of `jira.get_issue` exposing `status.name` for `MM-667`
- T029: verification of the run-report builder canonical shape

Per spec.md, plan.md, data-model.md, and contracts/transition-mm667.md
in `specs/334-transition-mm667-in-pr/`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from moonmind.integrations.jira.errors import JiraToolError
from moonmind.integrations.jira.models import (
    GetIssueRequest,
    GetTransitionsRequest,
    TransitionIssueRequest,
)
from moonmind.integrations.jira.tool import JiraToolService
from moonmind.workflows.temporal.transition_mm667 import (
    MM667_ISSUE_KEY,
    build_outcome,
    is_already_in_progress,
    missing_required_fields,
    redact_error_reason,
    select_in_progress_transition,
    transition_mm667_to_in_progress,
)


# ---------------------------------------------------------------------------
# Helpers
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


def _issue(status_name: str = "Backlog") -> dict[str, Any]:
    return {
        "key": MM667_ISSUE_KEY,
        "fields": {"status": {"name": status_name}},
    }


# ---------------------------------------------------------------------------
# T008 — match selection
# ---------------------------------------------------------------------------


def test_select_in_progress_zero_matches() -> None:
    transitions = [
        _transition(transition_id="11", name="Start", to_name="To Do"),
        _transition(transition_id="21", name="Done", to_name="Done"),
    ]

    result = select_in_progress_transition(transitions)

    assert result.kind == "no_match"
    assert result.candidates == []


def test_select_in_progress_exactly_one_match() -> None:
    transitions = [
        _transition(transition_id="11", name="Start", to_name="To Do"),
        _transition(transition_id="31", name="Begin Work", to_name="In Progress"),
    ]

    result = select_in_progress_transition(transitions)

    assert result.kind == "ok"
    assert result.transition is not None
    assert result.transition["id"] == "31"
    assert result.transition["to"]["name"] == "In Progress"


def test_select_in_progress_more_than_one_match_is_ambiguous() -> None:
    transitions = [
        _transition(transition_id="31", name="Start Work", to_name="In Progress"),
        _transition(transition_id="32", name="Resume", to_name="In Progress"),
    ]

    result = select_in_progress_transition(transitions)

    assert result.kind == "ambiguous"
    assert {item["id"] for item in result.candidates} == {"31", "32"}


@pytest.mark.parametrize(
    "to_name",
    [
        "in progress",
        "IN PROGRESS",
        "  In Progress  ",
        "\tIn Progress\n",
        "iN pRoGrEsS",
    ],
)
def test_select_in_progress_whitespace_and_case_variants(to_name: str) -> None:
    transitions = [
        _transition(transition_id="11", name="Start", to_name="To Do"),
        _transition(transition_id="55", name="Begin", to_name=to_name),
    ]

    result = select_in_progress_transition(transitions)

    assert result.kind == "ok"
    assert result.transition is not None
    assert result.transition["id"] == "55"


# ---------------------------------------------------------------------------
# T009 — required-field detection
# ---------------------------------------------------------------------------


def test_missing_required_fields_returns_required_field_ids() -> None:
    transition = _transition(
        transition_id="55",
        name="Begin",
        to_name="In Progress",
        fields={
            "resolution": {"required": True},
            "comment": {"required": False},
            "priority": {"required": True},
        },
    )

    assert missing_required_fields(transition) == ["priority", "resolution"]


def test_missing_required_fields_empty_when_no_required() -> None:
    transition = _transition(
        transition_id="55",
        name="Begin",
        to_name="In Progress",
        fields={
            "comment": {"required": False},
            "labels": {},
        },
    )

    assert missing_required_fields(transition) == []


def test_missing_required_fields_handles_missing_or_unknown_shape() -> None:
    assert missing_required_fields({"id": "55", "name": "Begin"}) == []
    assert missing_required_fields({"id": "55", "fields": "garbage"}) == []


# ---------------------------------------------------------------------------
# T010 — pre-fetch no-op detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status_name",
    ["In Progress", "in progress", "IN PROGRESS", "  In Progress  "],
)
def test_is_already_in_progress_true_for_variants(status_name: str) -> None:
    assert is_already_in_progress({"fields": {"status": {"name": status_name}}}) is True


@pytest.mark.parametrize(
    "status_name",
    ["To Do", "Done", "Backlog", "In Review", "", "  "],
)
def test_is_already_in_progress_false_for_other_statuses(status_name: str) -> None:
    assert is_already_in_progress({"fields": {"status": {"name": status_name}}}) is False


def test_is_already_in_progress_handles_missing_status() -> None:
    assert is_already_in_progress({}) is False
    assert is_already_in_progress({"fields": {}}) is False
    assert is_already_in_progress({"fields": {"status": None}}) is False


# ---------------------------------------------------------------------------
# T011 + T029 — run-report builder shape
# ---------------------------------------------------------------------------


def test_build_outcome_transitioned_path() -> None:
    outcome = build_outcome(
        prior_status="To Do",
        action="transitioned",
        outcome="transitioned",
        transition={"id": "31", "name": "Start Work", "toStatusName": "In Progress"},
        verified_final_status="In Progress",
    )

    payload = outcome.to_dict()

    assert payload["issueKey"] == MM667_ISSUE_KEY
    assert payload["priorStatus"] == "To Do"
    assert payload["action"] == "transitioned"
    assert payload["outcome"] == "transitioned"
    assert payload["transition"] == {
        "id": "31",
        "name": "Start Work",
        "toStatusName": "In Progress",
    }
    assert payload["verifiedFinalStatus"] == "In Progress"
    assert payload["availableTransitions"] == []
    assert payload["missingFields"] == []
    assert payload["errorClass"] is None
    assert payload["errorReason"] is None


def test_build_outcome_noop_path() -> None:
    outcome = build_outcome(
        prior_status="In Progress",
        action="noop_already_in_progress",
        outcome="noop_already_in_progress",
    )

    payload = outcome.to_dict()
    assert payload["action"] == "noop_already_in_progress"
    assert payload["outcome"] == "noop_already_in_progress"
    assert payload["priorStatus"] == "In Progress"
    assert payload["verifiedFinalStatus"] == "In Progress"
    assert payload["transition"] == {"id": None, "name": None, "toStatusName": None}
    assert payload["errorReason"] is None


def test_build_outcome_stopped_with_redacted_error_reason() -> None:
    long_reason = (
        "Authorization: Bearer ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "
        "token=secretvalue " + "x" * 800
    )

    outcome = build_outcome(
        prior_status=None,
        action="stopped",
        outcome="stopped:auth_or_permission",
        error_class="JiraToolError",
        error_reason=long_reason,
    )

    payload = outcome.to_dict()
    assert payload["action"] == "stopped"
    assert payload["outcome"] == "stopped:auth_or_permission"
    assert payload["errorClass"] == "JiraToolError"
    assert payload["errorReason"] is not None
    # Redaction must remove secret-pattern strings.
    assert "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in payload["errorReason"]
    assert "Bearer ghp_" not in payload["errorReason"]
    assert "token=secretvalue" not in payload["errorReason"]
    # Cap at 500 chars.
    assert len(payload["errorReason"]) <= 500


def test_build_outcome_redacts_all_jira_derived_report_strings() -> None:
    secret = "token" + "=secretvalue"
    outcome = build_outcome(
        prior_status=f"To Do {secret}",
        action="transitioned",
        outcome="transitioned",
        transition={
            "id": f"31-{secret}",
            "name": f"Start {secret}",
            "toStatusName": f"In Progress {secret}",
        },
        verified_final_status=f"In Progress {secret}",
    )

    payload = outcome.to_dict()
    assert secret not in repr(payload)
    assert "[REDACTED]" in repr(payload)


def test_build_outcome_redacts_available_transitions_and_missing_fields() -> None:
    secret = "password" + "=hunter2"
    outcome = build_outcome(
        prior_status="To Do",
        action="stopped",
        outcome="stopped:no_matching_transition",
        available_transitions=[
            {"id": f"11-{secret}", "name": f"Start {secret}", "toStatusName": "To Do"},
        ],
    )
    assert secret not in repr(outcome.to_dict())

    missing = build_outcome(
        prior_status="To Do",
        action="stopped",
        outcome="stopped:missing_required_fields",
        missing_fields=[f"resolution-{secret}"],
    )
    assert secret not in repr(missing.to_dict())


def test_build_outcome_action_outcome_consistency_enforced() -> None:
    # action="transitioned" requires outcome="transitioned".
    with pytest.raises(ValueError):
        build_outcome(
            prior_status="To Do",
            action="transitioned",
            outcome="stopped:final_status_mismatch",
        )

    # action="noop_already_in_progress" requires matching outcome.
    with pytest.raises(ValueError):
        build_outcome(
            prior_status="In Progress",
            action="noop_already_in_progress",
            outcome="transitioned",
        )

    # action="stopped" requires outcome to start with "stopped:".
    with pytest.raises(ValueError):
        build_outcome(
            prior_status="To Do",
            action="stopped",
            outcome="transitioned",
        )


def test_build_outcome_available_transitions_for_no_match_and_ambiguous() -> None:
    available = [
        {"id": "11", "name": "Start", "toStatusName": "To Do"},
        {"id": "21", "name": "Done", "toStatusName": "Done"},
    ]
    outcome = build_outcome(
        prior_status="Backlog",
        action="stopped",
        outcome="stopped:no_matching_transition",
        available_transitions=available,
    )
    assert outcome.to_dict()["availableTransitions"] == available


def test_build_outcome_missing_fields_lists_field_ids_only() -> None:
    outcome = build_outcome(
        prior_status="To Do",
        action="stopped",
        outcome="stopped:missing_required_fields",
        missing_fields=["resolution", "priority"],
    )
    payload = outcome.to_dict()
    assert payload["missingFields"] == ["resolution", "priority"]
    # Never serialize values for missing-required-fields.
    assert "values" not in payload


# ---------------------------------------------------------------------------
# T013 — redaction of known secret-pattern strings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret_token",
    [
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "github_pat_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "Bearer abcdefghijklmnopqrstuv12345",
        "token=mypassword123",
        "password=hunter2",
    ],
)
def test_redact_error_reason_removes_known_secret_patterns(secret_token: str) -> None:
    redacted = redact_error_reason(f"Failure context: {secret_token} — please retry.")
    assert secret_token not in redacted


def test_redact_error_reason_caps_length_to_500() -> None:
    redacted = redact_error_reason("x" * 1200)
    assert len(redacted) <= 500


def test_redact_error_reason_handles_none_and_empty() -> None:
    assert redact_error_reason(None) is None
    assert redact_error_reason("") == ""


def test_redact_error_reason_strips_private_key_block() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "AAAAA\nBBBBB\nCCCCC\n"
        "-----END RSA PRIVATE KEY-----"
    )
    redacted = redact_error_reason(f"context {pem} suffix")
    assert "BEGIN RSA PRIVATE KEY" not in redacted
    assert "AAAAA" not in redacted


# ---------------------------------------------------------------------------
# T012 — single-issue invariant + forbidden-tool guard
# ---------------------------------------------------------------------------


class _RecordingTrustedJira:
    """Stub of the trusted Jira tool surface that records every tool call."""

    def __init__(
        self,
        *,
        issue_payload: dict[str, Any] | Exception | None = None,
        transitions_payload: dict[str, Any] | Exception | None = None,
        transition_response: dict[str, Any] | Exception | None = None,
        verification_payload: dict[str, Any] | Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._issue_payload = issue_payload
        self._transitions_payload = transitions_payload
        self._transition_response = transition_response
        self._verification_payload = verification_payload
        self._get_issue_count = 0

    async def get_issue(self, **kwargs: Any) -> dict[str, Any]:
        self._get_issue_count += 1
        self.calls.append(("jira.get_issue", dict(kwargs)))
        # First call is pre-fetch; second call (if any) is verification.
        payload: dict[str, Any] | Exception | None
        if self._get_issue_count == 1:
            payload = self._issue_payload
        else:
            payload = self._verification_payload
        if isinstance(payload, Exception):
            raise payload
        if payload is None:
            return {}
        return payload

    async def get_transitions(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("jira.get_transitions", dict(kwargs)))
        if isinstance(self._transitions_payload, Exception):
            raise self._transitions_payload
        return self._transitions_payload or {}

    async def transition_issue(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("jira.transition_issue", dict(kwargs)))
        if isinstance(self._transition_response, Exception):
            raise self._transition_response
        return self._transition_response or {"transitioned": True}


def test_single_issue_invariant_every_tool_call_uses_mm667() -> None:
    jira = _RecordingTrustedJira(
        issue_payload=_issue("To Do"),
        transitions_payload={
            "transitions": [
                _transition(transition_id="31", name="Start", to_name="In Progress"),
            ],
        },
        verification_payload=_issue("In Progress"),
    )

    outcome = asyncio.run(transition_mm667_to_in_progress(trusted_jira=jira))

    assert outcome.to_dict()["outcome"] == "transitioned"
    assert outcome.to_dict()["issueKey"] == MM667_ISSUE_KEY

    # Every recorded tool call must use issue_key="MM-667".
    for tool_name, kwargs in jira.calls:
        assert kwargs.get("issue_key") == MM667_ISSUE_KEY, (
            f"{tool_name} called with non-MM-667 issue_key: {kwargs!r}"
        )

    # Forbidden tools must never appear among calls.
    called_tools = {name for name, _ in jira.calls}
    assert "jira.edit_issue" not in called_tools
    assert "jira.add_comment" not in called_tools
    assert "jira.create_subtask" not in called_tools


def test_transition_issue_called_with_empty_fields_and_update() -> None:
    jira = _RecordingTrustedJira(
        issue_payload=_issue("To Do"),
        transitions_payload={
            "transitions": [
                _transition(transition_id="31", name="Start", to_name="In Progress"),
            ],
        },
        verification_payload=_issue("In Progress"),
    )

    outcome = asyncio.run(transition_mm667_to_in_progress(trusted_jira=jira))

    transitions_calls = [
        kwargs for name, kwargs in jira.calls if name == "jira.transition_issue"
    ]
    assert len(transitions_calls) == 1
    assert transitions_calls[0]["fields"] == {}
    assert transitions_calls[0]["update"] == {}
    assert transitions_calls[0]["issue_key"] == MM667_ISSUE_KEY
    assert transitions_calls[0]["transition_id"] == "31"

    # `get_transitions` must be called with `expand_fields=True`.
    discovery_calls = [
        kwargs for name, kwargs in jira.calls if name == "jira.get_transitions"
    ]
    assert len(discovery_calls) == 1
    assert discovery_calls[0].get("expand_fields") is True
    assert outcome.to_dict()["outcome"] == "transitioned"


def test_jira_validation_failure_is_not_classified_transient() -> None:
    validation_error = JiraToolError(
        "transitionId is not available for the target issue.",
        code="jira_validation_failed",
        status_code=422,
        action="transition_issue",
    )
    jira = _RecordingTrustedJira(
        issue_payload=_issue("To Do"),
        transitions_payload={
            "transitions": [
                _transition(transition_id="31", name="Start", to_name="In Progress"),
            ],
        },
        transition_response=validation_error,
    )

    outcome = asyncio.run(transition_mm667_to_in_progress(trusted_jira=jira))

    assert outcome.to_dict()["outcome"] == "stopped:validation_failure"


class _ModelRequestJiraToolService(JiraToolService):
    """JiraToolService test double whose methods require request models."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._get_issue_count = 0

    async def get_issue(self, request: GetIssueRequest) -> dict[str, Any]:
        assert isinstance(request, GetIssueRequest)
        self._get_issue_count += 1
        self.calls.append(("jira.get_issue", request))
        if self._get_issue_count == 1:
            return _issue("To Do")
        return _issue("In Progress")

    async def get_transitions(
        self, request: GetTransitionsRequest
    ) -> dict[str, Any]:
        assert isinstance(request, GetTransitionsRequest)
        self.calls.append(("jira.get_transitions", request))
        return {
            "transitions": [
                _transition(transition_id="31", name="Start", to_name="In Progress"),
            ],
        }

    async def transition_issue(
        self, request: TransitionIssueRequest
    ) -> dict[str, Any]:
        assert isinstance(request, TransitionIssueRequest)
        self.calls.append(("jira.transition_issue", request))
        return {"transitioned": True}


def test_concrete_jira_tool_service_is_wrapped_with_request_models() -> None:
    service = _ModelRequestJiraToolService()

    outcome = asyncio.run(transition_mm667_to_in_progress(trusted_jira=service))

    assert outcome.to_dict()["outcome"] == "transitioned"
    assert [name for name, _ in service.calls] == [
        "jira.get_issue",
        "jira.get_transitions",
        "jira.transition_issue",
        "jira.get_issue",
    ]
    transition_request = service.calls[2][1]
    assert transition_request.issue_key == MM667_ISSUE_KEY
    assert transition_request.transition_id == "31"


# ---------------------------------------------------------------------------
# T027 — verification: jira.get_transitions(expand_fields=true) exposes fields
# ---------------------------------------------------------------------------


def test_verification_get_transitions_expand_fields_returns_fields_metadata() -> None:
    """T027: the trusted `jira.get_transitions` tool, when called with
    `expand_fields=True`, surfaces `transitions[*].fields` for the missing-required-fields
    check (FR-002, FR-007).
    """

    jira = _RecordingTrustedJira(
        issue_payload=_issue("To Do"),
        transitions_payload={
            "transitions": [
                _transition(
                    transition_id="31",
                    name="Begin",
                    to_name="In Progress",
                    fields={"resolution": {"required": True}},
                ),
            ],
        },
    )

    async def _run() -> None:
        await jira.get_transitions(issue_key=MM667_ISSUE_KEY, expand_fields=True)

    asyncio.run(_run())

    # Argument was forwarded.
    assert jira.calls[0][1]["expand_fields"] is True
    # The trusted-tool stub returned `fields` metadata.
    transitions = jira._transitions_payload["transitions"]
    assert transitions[0]["fields"]["resolution"]["required"] is True


# ---------------------------------------------------------------------------
# T028 — verification: jira.get_issue surfaces status.name for MM-667
# ---------------------------------------------------------------------------


def test_verification_get_issue_surfaces_status_name() -> None:
    """T028: the trusted `jira.get_issue` tool, called with `issue_key="MM-667"`,
    returns a payload whose `status.name` is consumable by the run-report
    builder (FR-005).
    """

    jira = _RecordingTrustedJira(issue_payload=_issue("In Review"))

    async def _run() -> dict[str, Any]:
        return await jira.get_issue(issue_key=MM667_ISSUE_KEY)

    payload = asyncio.run(_run())

    assert payload["fields"]["status"]["name"] == "In Review"
    assert jira.calls[0][1]["issue_key"] == MM667_ISSUE_KEY
