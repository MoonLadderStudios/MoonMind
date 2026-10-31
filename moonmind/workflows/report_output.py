"""Helpers for the compact report-output contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from moonmind.utils.logging import redact_sensitive_text
from moonmind.workflows.temporal.completion_summary import is_generic_completion_summary


def normalize_report_output_primary_path(raw_path: object) -> str:
    """Return a report path with a Markdown suffix when no suffix was provided."""

    path = str(raw_path or "").strip()
    if not path:
        return ""
    candidate = path.rstrip("/\\")
    if not candidate:
        return ""
    basename = candidate.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in basename.strip("."):
        return candidate
    return f"{candidate}md" if candidate.endswith(".") else f"{candidate}.md"


def report_output_display_name(
    raw_path: object, *, default: str = "final-report.md"
) -> str:
    path = normalize_report_output_primary_path(raw_path)
    if not path:
        return default
    name = path.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name or default


def build_agent_report_bundle(
    result: Mapping[str, Any],
    *,
    fallback_execution: Mapping[str, str],
    diagnostics_ref: str | None = None,
    operator_summary: str | None = None,
) -> dict[str, Any]:
    """Render retained results identically for initial and report-only saves."""
    metadata = result.get("metadata") or {}
    moonmind = metadata.get("moonmind") or {}
    report = moonmind.get("reportOutput") or {}
    execution = report.get("executionRef") or {}
    identity = {
        key: str(
            execution.get(key)
            or execution.get(alias)
            or fallback_execution.get(key)
            or ""
        )
        for key, alias in (
            ("namespace", "namespace"),
            ("workflow_id", "workflowId"),
            ("run_id", "runId"),
        )
    }
    if not identity["workflow_id"] or not identity["run_id"]:
        raise ValueError("reportOutput enabled but executionRef is incomplete")
    report_type = (
        str(
            report.get("reportType") or report.get("report_type") or "agent_run_report"
        ).strip()
        or "agent_run_report"
    )
    advisory = report_type == "optional_quality_review"
    assistant = str(
        metadata.get("assistantText") or metadata.get("lastAssistantText") or ""
    ).strip()
    if advisory:
        operator_summary = redact_sensitive_text(
            str(
                metadata.get("operator_summary")
                or metadata.get("operatorSummary")
                or ""
            )
        ).strip()
    summary = str(result.get("summary") or result.get("raw") or "").strip()
    if (
        operator_summary
        and not is_generic_completion_summary(operator_summary)
        and is_generic_completion_summary(summary)
    ):
        summary = operator_summary
    body = assistant or operator_summary or summary
    failure = result.get("failureClass") or result.get("failure_class")
    if advisory:
        body = redact_sensitive_text(body)
        if not (assistant or operator_summary) and (
            not body or is_generic_completion_summary(body)
        ):
            body = "Review unavailable: no textual assessment was produced."
        if metadata.get("lastAssistantTextTruncated") is True:
            body += "\n\nPartial report: text was truncated. Consult the retained output artifacts for the full response."
        if failure:
            body += f"\n\nPartial review: provider execution failed ({failure}); observations may be incomplete."
        # Bind recovery to the original provider diagnostics, which are already
        # retained even when the initial artifact Activity loses its reply.
        diagnostics_ref = result.get("diagnosticsRef") or result.get("diagnostics_ref")
    body = body or "Agent run completed without a textual report body."
    title = str(report.get("title") or "Final report").strip()
    rendered = (
        body.rstrip() + "\n"
        if body.lstrip().startswith("#")
        else f"# {title}\n\n{body.rstrip()}\n"
    )
    ledger = moonmind.get("stepLedger") or {}
    step_metadata = {}
    if ledger.get("logicalStepId"):
        step_metadata["step_id"] = str(ledger["logicalStepId"]).strip()
    attempt = ledger.get("attempt", ledger.get("executionOrdinal"))
    if isinstance(attempt, (int, float)) and not isinstance(attempt, bool):
        step_metadata["attempt"] = int(attempt)
    if ledger.get("scope"):
        step_metadata["scope"] = str(ledger["scope"]).strip()
    producer = "activity:agent_runtime.publish_artifacts"
    return {
        "principal": "system:agent_runtime",
        **identity,
        "report_type": report_type,
        "report_scope": "final",
        "primary": {
            "payload": rendered,
            "content_type": "text/markdown",
            "label": "Final report",
            "metadata": {
                "artifact_type": report_type,
                "title": title or "Final report",
                "description": str(report.get("description") or "").strip()
                or "Agent-authored final report",
                "producer": producer,
                "render_hint": "text",
                "name": report_output_display_name(
                    report.get("primaryPath") or report.get("primary_path")
                ),
                **({"subject": report["subject"]} if "subject" in report else {}),
                **step_metadata,
            },
        },
        "summary": {
            "payload": summary or "Report generated.",
            "content_type": "text/plain",
            "label": "Report summary",
            "metadata": {
                "artifact_type": report_type,
                "title": "Report summary",
                "producer": producer,
                "render_hint": "text",
                "name": "report-summary.txt",
                **step_metadata,
            },
        },
        "structured": {
            "payload": {
                "summary": summary,
                "output_refs": result.get("output_refs")
                or result.get("outputRefs")
                or [],
                "diagnostics_ref": diagnostics_ref,
                "failure_class": failure,
                "provider_error_code": result.get("provider_error_code")
                or result.get("providerErrorCode"),
            },
            "content_type": "application/json",
            "label": "Report structured output",
            "metadata": {
                "artifact_type": report_type,
                "title": "Structured report data",
                "producer": producer,
                "render_hint": "json",
                "name": "report-structured.json",
                **step_metadata,
            },
        },
        "step_id": step_metadata.get("step_id"),
        "attempt": step_metadata.get("attempt"),
        "scope": step_metadata.get("scope") or "final",
    }
