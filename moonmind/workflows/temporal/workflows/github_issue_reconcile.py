from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

DEFAULT_ACTIVITY_CATALOG = build_default_activity_catalog()


@workflow.defn(name="MoonMind.GitHubIssueReconcile")
class MoonMindGitHubIssueReconcileWorkflow:
    """Bounded periodic reconciliation of interrupted issue handoffs (#4182).

    Runs through the existing Temporal workflow/activity boundary (no new
    always-on container): resolves the ``github_issue.reconcile_handoffs``
    activity route from the canonical catalog and executes one bounded run
    for the requested repository. Duplicate observations stay bounded via
    the schedule's skip-on-overlap policy; the activity itself is
    retry-safe across independent reconcilers.
    """

    @workflow.run
    async def run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        repository = str(payload.get("repository") or payload.get("repo") or "")
        workflow.set_current_details(f"Reconciling interrupted issue handoffs for {repository or '<unscoped>'}")
        workflow.upsert_search_attributes(
            {
                "SessionStatus": ["reconciling"],
                "IsDegraded": [False],
            }
        )
        route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("github_issue.reconcile_handoffs")
        try:
            result = await workflow.execute_activity(
                "github_issue.reconcile_handoffs",
                payload,
                task_queue=route.task_queue,
                start_to_close_timeout=timedelta(seconds=route.timeouts.start_to_close_seconds),
                schedule_to_close_timeout=timedelta(seconds=route.timeouts.schedule_to_close_seconds),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=5),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=route.retries.max_interval_seconds),
                    maximum_attempts=route.retries.max_attempts,
                    non_retryable_error_types=list(route.retries.non_retryable_error_codes),
                ),
                summary="Reconcile interrupted GitHub issue handoffs",
            )
        except Exception:
            workflow.set_current_details("GitHub issue reconcile failed")
            workflow.upsert_search_attributes(
                {
                    "SessionStatus": ["failed"],
                    "IsDegraded": [True],
                }
            )
            raise
        normalized = dict(result or {})
        degraded = bool(normalized.get("diagnostics", {}).get("actionableFailures")) if isinstance(normalized.get("diagnostics"), dict) else False
        workflow.set_current_details("GitHub issue reconcile complete")
        workflow.upsert_search_attributes(
            {
                "SessionStatus": ["completed"],
                "IsDegraded": [degraded],
            }
        )
        return normalized
