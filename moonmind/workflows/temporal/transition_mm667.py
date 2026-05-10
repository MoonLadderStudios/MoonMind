"""Inline run-time orchestration for the one-shot MM-667 → "In Progress" story.

This module implements the spec at `specs/334-transition-mm667-in-pr/`:

- Pre-fetches `MM-667` to record `priorStatus` (and short-circuits when already
  in `In Progress`).
- Discovers transitions with `expand_fields=true` so `transitions[*].fields`
  metadata is available for the missing-required-fields guard.
- Selects exactly one transition whose `to.name` (case-insensitive, trimmed)
  equals `In Progress`; emits the named ambiguity / no-match error otherwise.
- Executes the transition with `fields={}` and `update={}` — never any other
  field mutation, never any other issue.
- Verifies the post-transition status by re-reading `MM-667`.
- Emits a redacted, ≤500-char run report conforming to
  `specs/334-transition-mm667-in-pr/data-model.md` and
  `specs/334-transition-mm667-in-pr/contracts/transition-mm667.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Mapping, Protocol

from moonmind.integrations.jira.errors import JiraToolError
from moonmind.integrations.jira.models import (
    GetIssueRequest,
    GetTransitionsRequest,
    TransitionIssueRequest,
)
from moonmind.integrations.jira.tool import JiraToolService
from moonmind.utils.logging import SecretRedactor, redact_sensitive_text

MM667_ISSUE_KEY = "MM-667"
IN_PROGRESS_TARGET_NAME = "In Progress"
_IN_PROGRESS_NORMALIZED = IN_PROGRESS_TARGET_NAME.strip().lower()
_ERROR_REASON_MAX = 500


Action = Literal["transitioned", "noop_already_in_progress", "stopped"]
OutcomeName = Literal[
    "transitioned",
    "noop_already_in_progress",
    "stopped:no_matching_transition",
    "stopped:ambiguous_transition",
    "stopped:issue_not_found",
    "stopped:missing_required_fields",
    "stopped:auth_or_permission",
    "stopped:validation_failure",
    "stopped:tool_unavailable",
    "stopped:transient_failure",
    "stopped:final_status_mismatch",
]


class TrustedJiraToolUnavailable(RuntimeError):
    """Raised when the trusted Jira tool surface is not registered."""


class TrustedJiraToolSurface(Protocol):
    """Subset of MoonMind's trusted Jira tool surface used by this story."""

    async def get_issue(self, **kwargs: Any) -> Mapping[str, Any]:
        pass

    async def get_transitions(self, **kwargs: Any) -> Mapping[str, Any]:
        pass

    async def transition_issue(self, **kwargs: Any) -> Mapping[str, Any]:
        pass


# ---------------------------------------------------------------------------
# Match selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Result of selecting an `In Progress` transition from a discovery payload."""

    kind: Literal["ok", "no_match", "ambiguous"]
    transition: Mapping[str, Any] | None = None
    candidates: list[Mapping[str, Any]] = field(default_factory=list)


def _to_name(transition: Mapping[str, Any]) -> str:
    target = transition.get("to")
    if not isinstance(target, Mapping):
        return ""
    return str(target.get("name") or "")


def select_in_progress_transition(
    transitions: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> MatchResult:
    """Return the unique transition whose `to.name` equals `In Progress`.

    Matching is case-insensitive and ignores surrounding whitespace, per FR-003
    and DESIGN-REQ-002.
    """

    matches = [
        transition
        for transition in (transitions or [])
        if isinstance(transition, Mapping)
        and _to_name(transition).strip().lower() == _IN_PROGRESS_NORMALIZED
    ]
    if not matches:
        return MatchResult(kind="no_match", candidates=[])
    if len(matches) > 1:
        return MatchResult(kind="ambiguous", candidates=matches)
    return MatchResult(kind="ok", transition=matches[0], candidates=matches)


def missing_required_fields(transition: Mapping[str, Any]) -> list[str]:
    """Return the IDs of fields the matched transition declares as required.

    Field VALUES are never returned — only the IDs (FR-007 / FR-009).
    """

    raw = transition.get("fields") if isinstance(transition, Mapping) else None
    if not isinstance(raw, Mapping):
        return []
    required = [
        str(field_id)
        for field_id, meta in raw.items()
        if isinstance(meta, Mapping) and meta.get("required")
    ]
    return sorted(required)


def is_already_in_progress(issue: Mapping[str, Any] | None) -> bool:
    """Return True when the issue's `status.name` already equals `In Progress`."""

    if not isinstance(issue, Mapping):
        return False
    fields = issue.get("fields")
    if not isinstance(fields, Mapping):
        return False
    status = fields.get("status")
    if not isinstance(status, Mapping):
        return False
    name = status.get("name")
    if not isinstance(name, str):
        return False
    return name.strip().lower() == _IN_PROGRESS_NORMALIZED


def _status_name(issue: Mapping[str, Any] | None) -> str | None:
    if not isinstance(issue, Mapping):
        return None
    fields = issue.get("fields")
    if not isinstance(fields, Mapping):
        return None
    status = fields.get("status")
    if not isinstance(status, Mapping):
        return None
    name = status.get("name")
    if not isinstance(name, str):
        return None
    return name


def _serialize_transition(transition: Mapping[str, Any]) -> dict[str, str]:
    return {
        "id": _redact_report_string(str(transition.get("id") or "")) or "",
        "name": _redact_report_string(str(transition.get("name") or "")) or "",
        "toStatusName": _redact_report_string(_to_name(transition)) or "",
    }


# ---------------------------------------------------------------------------
# Redaction + run-report builder
# ---------------------------------------------------------------------------


def _redact_report_string(text: str | None) -> str | None:
    """Redact and trim one user-visible run-report string."""
    if text is None:
        return None
    if text == "":
        return ""
    redactor = SecretRedactor()
    redacted = redact_sensitive_text(redactor.scrub(str(text)))
    if len(redacted) > _ERROR_REASON_MAX:
        redacted = redacted[: _ERROR_REASON_MAX - 1] + "…"
    return redacted


def redact_error_reason(text: str | None) -> str | None:
    """Redact and trim a user-visible error reason to ≤500 chars (FR-009, SC-003)."""

    return _redact_report_string(text)


_VALID_OUTCOMES: frozenset[str] = frozenset(
    [
        "transitioned",
        "noop_already_in_progress",
        "stopped:no_matching_transition",
        "stopped:ambiguous_transition",
        "stopped:issue_not_found",
        "stopped:missing_required_fields",
        "stopped:auth_or_permission",
        "stopped:validation_failure",
        "stopped:tool_unavailable",
        "stopped:transient_failure",
        "stopped:final_status_mismatch",
    ]
)


@dataclass(frozen=True, slots=True)
class TransitionMM667Outcome:
    issueKey: str
    priorStatus: str | None
    action: Action
    outcome: OutcomeName
    transition: dict[str, str | None]
    verifiedFinalStatus: str | None
    availableTransitions: list[dict[str, str]]
    missingFields: list[str]
    errorClass: str | None
    errorReason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "issueKey": self.issueKey,
            "priorStatus": self.priorStatus,
            "action": self.action,
            "outcome": self.outcome,
            "transition": dict(self.transition),
            "verifiedFinalStatus": self.verifiedFinalStatus,
            "availableTransitions": [dict(item) for item in self.availableTransitions],
            "missingFields": list(self.missingFields),
            "errorClass": self.errorClass,
            "errorReason": self.errorReason,
        }


def build_outcome(
    *,
    prior_status: str | None,
    action: Action,
    outcome: OutcomeName,
    transition: Mapping[str, Any] | None = None,
    verified_final_status: str | None = None,
    available_transitions: list[Mapping[str, Any]] | None = None,
    missing_fields: list[str] | None = None,
    error_class: str | None = None,
    error_reason: str | None = None,
) -> TransitionMM667Outcome:
    """Compose the canonical run-report outcome (data-model.md / contracts)."""

    if outcome not in _VALID_OUTCOMES:
        raise ValueError(f"Unknown outcome name: {outcome!r}")

    if action == "transitioned" and outcome != "transitioned":
        raise ValueError(
            "action='transitioned' requires outcome='transitioned'"
        )
    if action == "noop_already_in_progress" and outcome != "noop_already_in_progress":
        raise ValueError(
            "action='noop_already_in_progress' requires matching outcome"
        )
    if action == "stopped" and not outcome.startswith("stopped:"):
        raise ValueError("action='stopped' requires outcome to start with 'stopped:'")

    if action == "transitioned":
        if not transition:
            raise ValueError("transitioned outcome requires a transition payload")
        transition_payload: dict[str, str | None] = {
            "id": _redact_report_string(str(transition.get("id") or "")),
            "name": _redact_report_string(str(transition.get("name") or "")),
            "toStatusName": _redact_report_string(
                str(transition.get("toStatusName") or "")
            ),
        }
    else:
        transition_payload = {"id": None, "name": None, "toStatusName": None}

    if action == "noop_already_in_progress":
        verified_final = verified_final_status or prior_status
    elif action == "transitioned":
        verified_final = verified_final_status
    else:
        verified_final = verified_final_status

    available = []
    if outcome in {
        "stopped:no_matching_transition",
        "stopped:ambiguous_transition",
    }:
        for item in available_transitions or []:
            if isinstance(item, Mapping):
                available.append(
                    {
                        "id": _redact_report_string(str(item.get("id") or "")) or "",
                        "name": _redact_report_string(str(item.get("name") or ""))
                        or "",
                        "toStatusName": _redact_report_string(
                            str(item.get("toStatusName") or "")
                        )
                        or "",
                    }
                )

    missing = (
        [
            _redact_report_string(str(field_id)) or ""
            for field_id in list(missing_fields or [])
        ]
        if outcome == "stopped:missing_required_fields"
        else []
    )

    err_class = _redact_report_string(error_class) if action == "stopped" else None
    err_reason = redact_error_reason(error_reason) if action == "stopped" else None

    return TransitionMM667Outcome(
        issueKey=_redact_report_string(MM667_ISSUE_KEY) or MM667_ISSUE_KEY,
        priorStatus=_redact_report_string(prior_status),
        action=action,
        outcome=outcome,
        transition=transition_payload,
        verifiedFinalStatus=_redact_report_string(verified_final),
        availableTransitions=available,
        missingFields=missing,
        errorClass=err_class,
        errorReason=err_reason,
    )


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


def _classify_jira_tool_error(exc: JiraToolError) -> OutcomeName:
    code = (exc.code or "").lower()
    status = int(exc.status_code or 0)
    if status == 404 or "not_found" in code:
        return "stopped:issue_not_found"
    if status in (401, 403) or "auth" in code or "permission" in code:
        return "stopped:auth_or_permission"
    if status == 429 or status >= 500:
        return "stopped:transient_failure"
    if (
        400 <= status < 500
        or "validation" in code
        or "policy" in code
    ):
        return "stopped:validation_failure"
    return "stopped:tool_unavailable"


def _build_stopped_from_exception(
    *,
    prior_status: str | None,
    exc: BaseException,
    fallback_outcome: OutcomeName,
) -> TransitionMM667Outcome:
    if isinstance(exc, TrustedJiraToolUnavailable):
        outcome: OutcomeName = "stopped:tool_unavailable"
    elif isinstance(exc, JiraToolError):
        outcome = _classify_jira_tool_error(exc)
    else:
        outcome = fallback_outcome
    return build_outcome(
        prior_status=prior_status,
        action="stopped",
        outcome=outcome,
        error_class=type(exc).__name__,
        error_reason=str(exc),
    )


# ---------------------------------------------------------------------------
# Inline driver
# ---------------------------------------------------------------------------


async def transition_mm667_to_in_progress(
    *,
    trusted_jira: TrustedJiraToolSurface | None,
) -> TransitionMM667Outcome:
    """Drive `MM-667` to `In Progress` per `contracts/transition-mm667.md`.

    The driver enforces the single-issue invariant (every Jira tool call uses
    `issue_key="MM-667"`), never invokes `jira.edit_issue` /
    `jira.add_comment` / `jira.create_subtask`, and emits exactly one of the
    enumerated outcomes (FR-001, FR-008, DESIGN-REQ-001, SC-002, SC-004).
    """

    if trusted_jira is None:
        return build_outcome(
            prior_status=None,
            action="stopped",
            outcome="stopped:tool_unavailable",
            error_class="TrustedJiraToolUnavailable",
            error_reason="Trusted Jira tool surface is not registered in this runtime.",
        )
    if isinstance(trusted_jira, JiraToolService):
        trusted_jira = adapter_from_jira_tool_service(trusted_jira)

    # Call 1 — get_issue (pre-fetch).
    try:
        issue_payload = await trusted_jira.get_issue(issue_key=MM667_ISSUE_KEY)
    except (JiraToolError, TrustedJiraToolUnavailable) as exc:
        return _build_stopped_from_exception(
            prior_status=None,
            exc=exc,
            fallback_outcome="stopped:transient_failure",
        )

    prior_status = _status_name(issue_payload)
    if is_already_in_progress(issue_payload):
        return build_outcome(
            prior_status=prior_status,
            action="noop_already_in_progress",
            outcome="noop_already_in_progress",
            verified_final_status=prior_status,
        )

    # Call 2 — get_transitions(expand_fields=true).
    try:
        transitions_payload = await trusted_jira.get_transitions(
            issue_key=MM667_ISSUE_KEY,
            expand_fields=True,
        )
    except (JiraToolError, TrustedJiraToolUnavailable) as exc:
        return _build_stopped_from_exception(
            prior_status=prior_status,
            exc=exc,
            fallback_outcome="stopped:transient_failure",
        )

    raw_transitions = []
    if isinstance(transitions_payload, Mapping):
        raw = transitions_payload.get("transitions") or []
        if isinstance(raw, list):
            raw_transitions = [item for item in raw if isinstance(item, Mapping)]

    available_for_report = [_serialize_transition(item) for item in raw_transitions]
    selection = select_in_progress_transition(raw_transitions)

    if selection.kind == "no_match":
        return build_outcome(
            prior_status=prior_status,
            action="stopped",
            outcome="stopped:no_matching_transition",
            available_transitions=available_for_report,
            error_class="NoMatchingTransition",
            error_reason=(
                f"No available transition for {MM667_ISSUE_KEY} targets {IN_PROGRESS_TARGET_NAME!r}."
            ),
        )
    if selection.kind == "ambiguous":
        candidates = [_serialize_transition(item) for item in selection.candidates]
        return build_outcome(
            prior_status=prior_status,
            action="stopped",
            outcome="stopped:ambiguous_transition",
            available_transitions=candidates,
            error_class="AmbiguousTransition",
            error_reason=(
                f"More than one transition for {MM667_ISSUE_KEY} targets "
                f"{IN_PROGRESS_TARGET_NAME!r}."
            ),
        )

    assert selection.transition is not None  # narrow for type-checkers
    matched = selection.transition
    missing = missing_required_fields(matched)
    if missing:
        return build_outcome(
            prior_status=prior_status,
            action="stopped",
            outcome="stopped:missing_required_fields",
            missing_fields=missing,
            error_class="MissingRequiredFields",
            error_reason=(
                f"Transition {matched.get('id')!r} for {MM667_ISSUE_KEY} requires "
                f"fields without configured defaults."
            ),
        )

    transition_id = str(matched.get("id") or "")
    transition_name = str(matched.get("name") or "")
    target_status_name = _to_name(matched)

    # Call 3 — transition_issue.
    try:
        await trusted_jira.transition_issue(
            issue_key=MM667_ISSUE_KEY,
            transition_id=transition_id,
            fields={},
            update={},
        )
    except (JiraToolError, TrustedJiraToolUnavailable) as exc:
        return _build_stopped_from_exception(
            prior_status=prior_status,
            exc=exc,
            fallback_outcome="stopped:transient_failure",
        )

    # Call 4 — verification get_issue.
    try:
        verification_payload = await trusted_jira.get_issue(issue_key=MM667_ISSUE_KEY)
    except (JiraToolError, TrustedJiraToolUnavailable) as exc:
        return _build_stopped_from_exception(
            prior_status=prior_status,
            exc=exc,
            fallback_outcome="stopped:transient_failure",
        )

    verified_status = _status_name(verification_payload)
    transition_payload = {
        "id": transition_id,
        "name": transition_name,
        "toStatusName": target_status_name,
    }

    if (
        verified_status is not None
        and verified_status.strip().lower() == _IN_PROGRESS_NORMALIZED
    ):
        return build_outcome(
            prior_status=prior_status,
            action="transitioned",
            outcome="transitioned",
            transition=transition_payload,
            verified_final_status=verified_status,
        )

    return build_outcome(
        prior_status=prior_status,
        action="stopped",
        outcome="stopped:final_status_mismatch",
        transition=None,
        verified_final_status=verified_status,
        error_class="FinalStatusMismatch",
        error_reason=(
            f"Post-transition status {verified_status!r} does not equal "
            f"{IN_PROGRESS_TARGET_NAME!r}."
        ),
    )


# ---------------------------------------------------------------------------
# Convenience wrappers for adapters that hold loose async callables.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _CallableTrustedJiraAdapter:
    """Adapter that wraps a set of async callables in a Protocol-conforming object."""

    _get_issue: Callable[..., Awaitable[Mapping[str, Any]]]
    _get_transitions: Callable[..., Awaitable[Mapping[str, Any]]]
    _transition_issue: Callable[..., Awaitable[Mapping[str, Any]]]

    async def get_issue(self, **kwargs: Any) -> Mapping[str, Any]:
        return await self._get_issue(**kwargs)

    async def get_transitions(self, **kwargs: Any) -> Mapping[str, Any]:
        return await self._get_transitions(**kwargs)

    async def transition_issue(self, **kwargs: Any) -> Mapping[str, Any]:
        return await self._transition_issue(**kwargs)


@dataclass(frozen=True, slots=True)
class _JiraToolServiceAdapter:
    """Adapter from JiraToolService request models to this story's keyword surface."""

    _service: JiraToolService

    async def get_issue(self, **kwargs: Any) -> Mapping[str, Any]:
        return await self._service.get_issue(GetIssueRequest(**kwargs))

    async def get_transitions(self, **kwargs: Any) -> Mapping[str, Any]:
        return await self._service.get_transitions(GetTransitionsRequest(**kwargs))

    async def transition_issue(self, **kwargs: Any) -> Mapping[str, Any]:
        return await self._service.transition_issue(TransitionIssueRequest(**kwargs))


def adapter_from_callables(
    *,
    get_issue: Callable[..., Awaitable[Mapping[str, Any]]],
    get_transitions: Callable[..., Awaitable[Mapping[str, Any]]],
    transition_issue: Callable[..., Awaitable[Mapping[str, Any]]],
) -> TrustedJiraToolSurface:
    """Wrap a triple of async callables in a Protocol-conforming adapter."""

    return _CallableTrustedJiraAdapter(
        _get_issue=get_issue,
        _get_transitions=get_transitions,
        _transition_issue=transition_issue,
    )


def adapter_from_jira_tool_service(service: JiraToolService) -> TrustedJiraToolSurface:
    """Wrap the concrete JiraToolService in the keyword API used by the driver."""

    return _JiraToolServiceAdapter(_service=service)


__all__ = [
    "IN_PROGRESS_TARGET_NAME",
    "MM667_ISSUE_KEY",
    "MatchResult",
    "TransitionMM667Outcome",
    "TrustedJiraToolSurface",
    "TrustedJiraToolUnavailable",
    "adapter_from_callables",
    "adapter_from_jira_tool_service",
    "build_outcome",
    "is_already_in_progress",
    "missing_required_fields",
    "redact_error_reason",
    "select_in_progress_transition",
    "transition_mm667_to_in_progress",
]
