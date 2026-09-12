"""Deterministic failure projection for the Run workflow.

This boundary owns exception classification, redaction and compact diagnostic
receipts. The workflow owns execution, step transitions and command ordering;
projection never schedules an Activity or selects a recovery action.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from temporalio import exceptions
from temporalio.exceptions import CancelledError

from moonmind.utils.logging import redact_sensitive_text


class RunFailureDiagnostics:
    """Failure evidence attached to the workflow's existing step ledger."""

    @staticmethod
    def _operator_failure_summary(exc: BaseException) -> str:
        """Return the most actionable bounded message from a nested failure chain."""

        generic_messages = {
            "Activity task failed",
            "Activity error",
            "Child Workflow execution failed",
            "Child workflow execution failed",
            "Workflow execution failed",
            "activity failed",
        }
        generic_types = (
            exceptions.ActivityError,
            exceptions.ChildWorkflowError,
        )
        chain: list[tuple[BaseException, str]] = []
        current: BaseException | None = exc
        for _ in range(20):
            if current is None:
                break
            message = str(current).strip()
            if message:
                chain.append((current, message))
            next_exc = getattr(current, "cause", None)
            if not isinstance(next_exc, BaseException):
                next_exc = current.__cause__
            current = next_exc

        for exc_obj, message in reversed(chain):
            if message not in generic_messages and not isinstance(
                exc_obj, generic_types
            ):
                return message[:1000]
        if chain:
            return chain[-1][1][:1000]
        return exc.__class__.__name__

    def _bounded_operator_failure(
        self, exc: BaseException, *, max_chars: int = 500
    ) -> str:
        """Return redacted nested failure evidence suitable for durable summaries."""

        raw_message = self._operator_failure_summary(exc)
        sanitized = self._sanitize_operator_summary(redact_sensitive_text(raw_message))
        return self._coerce_text(sanitized, max_chars=max_chars) or (
            exc.__class__.__name__
        )

    @staticmethod
    def _failure_root_cause(exc: BaseException) -> BaseException:
        """Walk the exception chain to the deepest non-generic cause."""

        generic_types = (
            exceptions.ActivityError,
            exceptions.ChildWorkflowError,
        )
        chain: list[BaseException] = []
        current: BaseException | None = exc
        for _ in range(20):
            if current is None:
                break
            chain.append(current)
            next_exc = getattr(current, "cause", None)
            if not isinstance(next_exc, BaseException):
                next_exc = current.__cause__
            current = next_exc
        for candidate in reversed(chain):
            if not isinstance(candidate, generic_types):
                return candidate
        return chain[-1] if chain else exc

    @classmethod
    def _classify_failure_category(cls, exc: BaseException) -> str:
        """Map an exception chain to one of the canonical errorCategory values.

        Categories align with `ExecutionTerminalStateInput.error_category`:
        ``user_error`` | ``integration_error`` | ``execution_error`` | ``system_error``.
        """

        # CancelledError is not a normal failure; callers must handle it before
        # invoking this helper, but classify defensively.
        if isinstance(exc, (CancelledError, asyncio.CancelledError)):
            return "execution_error"

        root = cls._failure_root_cause(exc)

        # Inspect ApplicationError.type when present (set by activities raising
        # typed errors per docs/Temporal/ErrorTaxonomy.md).
        application_types: list[str] = []
        current: BaseException | None = exc
        for _ in range(20):
            if current is None:
                break
            if isinstance(current, exceptions.ApplicationError):
                raw_type = getattr(current, "type", None)
                if isinstance(raw_type, str) and raw_type.strip():
                    application_types.append(raw_type.strip())
            next_exc = getattr(current, "cause", None)
            if not isinstance(next_exc, BaseException):
                next_exc = current.__cause__
            current = next_exc

        user_error_types = {"INVALID_INPUT"}
        integration_error_types = {
            "UnsupportedStatus",
            "ProfileResolutionError",
            "SlotAcquisitionTimeout",
            "RATE_LIMITED",
        }
        system_error_types = {"WORKER_CAPABILITY_UNAVAILABLE"}
        for app_type in reversed(application_types):
            if app_type in user_error_types:
                return "user_error"
            if app_type in integration_error_types:
                return "integration_error"
            if app_type in system_error_types:
                return "system_error"

        # Heuristic fallback based on the deepest root-cause type.
        root_type_name = root.__class__.__name__
        if root_type_name in {"ValueError", "TypeError", "KeyError"}:
            # These typically indicate malformed/invalid input.
            return "user_error"
        if "Timeout" in root_type_name or "Connection" in root_type_name:
            return "integration_error"
        return "execution_error"

    # Canonical errorCategory tokens. These are machine classifications, not
    # operator-readable messages, so they must never be surfaced verbatim as a
    # step/plan summary.
    _ERROR_CATEGORY_TOKENS = frozenset(
        {"user_error", "integration_error", "execution_error", "system_error"}
    )

    @classmethod
    def _humanize_step_failure_summary(
        cls,
        *,
        summary: str | None,
        tool_name: str,
        failure_message: str | None,
    ) -> str:
        """Return an operator-actionable step-failure summary.

        When the only text a failed step result carries is a bare error-category
        token (e.g. a runtime that timed out and emitted ``execution_error`` with
        no provider detail), surface a descriptive line instead of propagating
        the token. Otherwise the token would become the terminal summary, the
        finish-outcome reason, and the workflow's ApplicationError message —
        leaving operators with nothing actionable. The raw category is preserved
        separately via ``errorCategory``.
        """
        text = (summary or "").strip()
        if text and text not in cls._ERROR_CATEGORY_TOKENS:
            return text
        category = (failure_message or "").strip()
        if category in cls._ERROR_CATEGORY_TOKENS:
            return (
                f"{tool_name} failed ({category}); the runtime reported no "
                "diagnostic detail — inspect step diagnostics/artifacts."
            )
        return f"{tool_name} failed"

    def _format_step_failure_exception_message(
        self,
        *,
        node_id: str,
        tool_name: str,
        result_status: str,
        step_failure_summary: str,
        failure_message: str | None,
        child_workflow_id: str | None,
        diagnostics_ref: str | None,
    ) -> str:
        """Build the fail-fast ApplicationError text for a failed plan step."""

        summary = (
            self._sanitize_operator_summary(step_failure_summary)
            or step_failure_summary
            or f"{tool_name} failed"
        )
        bounded_summary = self._coerce_text(summary, max_chars=900) or (
            f"{tool_name} failed"
        )
        message = (
            f"Plan step '{node_id}' ({tool_name}) returned status "
            f"{result_status}: {bounded_summary}"
        )
        details: list[str] = []
        raw_last_error = self._coerce_text(failure_message, max_chars=240)
        last_error = self._sanitize_operator_summary(raw_last_error) or raw_last_error
        if last_error and last_error not in bounded_summary:
            details.append(f"lastError={last_error}")
        child_id = self._coerce_text(child_workflow_id, max_chars=400)
        if child_id:
            details.append(f"childWorkflowId={child_id}")
        diag_ref = self._coerce_text(diagnostics_ref, max_chars=400)
        if diag_ref:
            details.append(f"diagnosticsRef={diag_ref}")
        if details:
            message = f"{message} ({'; '.join(details)})"
        return self._coerce_text(message, max_chars=1200) or message

    def _failure_diagnostic_from_exception(
        self,
        exc: BaseException,
        *,
        stage: str | None = None,
        step_id: str | None = None,
        step_title: str | None = None,
        source: str | None = None,
        child_workflow_id: str | None = None,
        diagnostics_ref: str | None = None,
    ) -> dict[str, Any]:
        """Build a bounded, redacted failure diagnostic from a failure chain.

        The returned dict is intentionally small and free of secrets so it
        can flow through the workflow's finish-summary contract and the
        terminal-state activity without leaking credential-bearing payloads.
        """

        raw_message = self._operator_failure_summary(exc)
        sanitized = self._sanitize_operator_summary(raw_message) or raw_message
        bounded_message = self._coerce_text(sanitized, max_chars=1000) or (
            exc.__class__.__name__
        )
        category = self._classify_failure_category(exc)
        root = self._failure_root_cause(exc)

        diagnostic: dict[str, Any] = {
            "stage": self._coerce_text(stage or self._state, max_chars=80),
            "category": category,
            "source": self._coerce_text(source, max_chars=40) or "workflow",
            "stepId": self._coerce_text(step_id, max_chars=120),
            "stepTitle": self._coerce_text(step_title, max_chars=200),
            "childWorkflowId": self._coerce_text(child_workflow_id, max_chars=400),
            "message": bounded_message,
            "rootCauseType": self._coerce_text(root.__class__.__name__, max_chars=80),
            "diagnosticsRef": self._coerce_text(diagnostics_ref, max_chars=400),
        }
        current: BaseException | None = exc
        for _ in range(20):
            if current is None:
                break
            if (
                isinstance(current, exceptions.ApplicationError)
                and getattr(current, "type", None) == "WORKER_CAPABILITY_UNAVAILABLE"
            ):
                diagnostic.update(
                    {
                        "reasonCode": "worker_capability_unavailable",
                        "agentExecutionLaunched": False,
                    }
                )
                details = getattr(current, "details", ()) or ()
                detail = (
                    details[0] if details and isinstance(details[0], Mapping) else {}
                )
                for source_key, target_key in (
                    ("workflowType", "workflowType"),
                    ("taskQueue", "taskQueue"),
                    ("registryFingerprint", "registryFingerprint"),
                    ("observedWorkerBuilds", "observedWorkerBuilds"),
                ):
                    if detail.get(source_key) is not None:
                        diagnostic[target_key] = detail[source_key]
                break
            next_exc = getattr(current, "cause", None)
            if not isinstance(next_exc, BaseException):
                next_exc = current.__cause__
            current = next_exc
        # Drop empty optional keys to keep the structure compact.
        return {key: value for key, value in diagnostic.items() if value is not None}

    def _record_failure_diagnostic(
        self,
        exc: BaseException,
        *,
        stage: str | None = None,
        step_id: str | None = None,
        step_title: str | None = None,
        source: str | None = None,
        child_workflow_id: str | None = None,
        diagnostics_ref: str | None = None,
    ) -> dict[str, Any]:
        """Capture a failure diagnostic on the workflow if none is set yet."""

        diagnostic = self._failure_diagnostic_from_exception(
            exc,
            stage=stage,
            step_id=step_id,
            step_title=step_title,
            source=source,
            child_workflow_id=child_workflow_id,
            diagnostics_ref=diagnostics_ref,
        )
        # First failure wins: keep the deepest available root cause and avoid
        # later generic wrapping handlers from overwriting it.
        if self._failure_diagnostic is None:
            self._failure_diagnostic = diagnostic
        return diagnostic

    def _record_step_execution_exception(
        self,
        exc: BaseException,
        *,
        logical_step_id: str,
        tool_name: str,
        source: str,
        updated_at: datetime,
        child_workflow_id: str | None = None,
        diagnostics_ref: str | None = None,
    ) -> dict[str, Any]:
        """Record step-scoped terminal failure evidence for raised executions."""

        diagnostic = self._record_failure_diagnostic(
            exc,
            stage=self._state,
            step_id=logical_step_id,
            step_title=tool_name,
            source=source,
            child_workflow_id=child_workflow_id,
            diagnostics_ref=diagnostics_ref,
        )
        self._mark_step_terminal(
            logical_step_id,
            status="failed",
            updated_at=updated_at,
            summary=diagnostic["message"],
            last_error=diagnostic["category"],
        )
        return diagnostic

    def _record_result_failure_diagnostic(
        self,
        *,
        stage: str | None,
        category: str | None,
        source: str,
        step_id: str,
        step_title: str,
        message: str,
        child_workflow_id: str | None = None,
        diagnostics_ref: str | None = None,
        terminal_evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Capture a failure diagnostic from a completed-but-failed step result."""

        normalized_category = self._coerce_text(category, max_chars=80)
        if normalized_category not in {
            "user_error",
            "integration_error",
            "execution_error",
            "system_error",
        }:
            normalized_category = "execution_error"
        sanitized = self._sanitize_operator_summary(message) or message
        diagnostic: dict[str, Any] = {
            "stage": self._coerce_text(stage or self._state, max_chars=80),
            "category": normalized_category,
            "source": self._coerce_text(source, max_chars=40) or "workflow",
            "stepId": self._coerce_text(step_id, max_chars=120),
            "stepTitle": self._coerce_text(step_title, max_chars=200),
            "childWorkflowId": self._coerce_text(child_workflow_id, max_chars=400),
            "message": self._coerce_text(sanitized, max_chars=1000)
            or "plan step failed",
            "rootCauseType": (
                "AgentRunResult" if source == "child_workflow" else "ActivityResult"
            ),
            "diagnosticsRef": self._coerce_text(diagnostics_ref, max_chars=400),
        }
        compact = {key: value for key, value in diagnostic.items() if value is not None}
        if isinstance(terminal_evidence, Mapping):
            for key in (
                "failureCode",
                "terminalContractId",
                "terminalContractMissingEvidence",
                "queuedChildCount",
                "queuedChildren",
            ):
                value = terminal_evidence.get(key)
                if value is not None:
                    compact[key] = value
        if self._failure_diagnostic is None:
            self._failure_diagnostic = compact
        return compact
