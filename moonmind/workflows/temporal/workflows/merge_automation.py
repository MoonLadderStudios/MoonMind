"""Parent-owned Temporal workflow for post-publish merge automation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import SearchAttributeKey, SearchAttributePair
from temporalio.exceptions import CancelledError
from temporalio.workflow import ActivityCancellationType, ChildWorkflowCancellationType

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.temporal_models import (
        MergeAutomationStartInput,
        ReadinessBlockerModel,
    )
    from moonmind.schemas.temporal_activity_models import ArtifactWriteCompleteInput
    from moonmind.workflows.temporal.activity_catalog import (
        ARTIFACTS_TASK_QUEUE,
        INTEGRATIONS_TASK_QUEUE,
        WORKFLOW_TASK_QUEUE,
    )
    from moonmind.workflows.temporal.typed_execution import execute_typed_activity
    from moonmind.workflows.temporal.workflows.merge_gate import (
        DEFAULT_ACTIVITY_RETRY_POLICY,
        TERMINAL_BLOCKER_KINDS,
        _effective_expire_at,
        build_resolver_run_request,
        classify_readiness,
        deterministic_resolver_idempotency_key,
        legacy_resolver_idempotency_key,
    )


WORKFLOW_NAME = "MoonMind.MergeAutomation"
STATE_WAITING = "waiting"
STATE_EXECUTING = "executing"
STATE_BLOCKED = "blocked"
STATE_MERGED = "merged"
STATE_ALREADY_MERGED = "already_merged"
STATE_EXPIRED = "expired"
STATE_FAILED = "failed"
STATE_CANCELED = "canceled"
DISPOSITION_REENTER_GATE = "reenter_gate"
DISPOSITION_MERGED = "merged"
DISPOSITION_ALREADY_MERGED = "already_merged"
DISPOSITION_MANUAL_REVIEW = "manual_review"
DISPOSITION_FAILED = "failed"
SUCCESS_DISPOSITIONS = frozenset({DISPOSITION_MERGED, DISPOSITION_ALREADY_MERGED})
NON_SUCCESS_DISPOSITIONS = frozenset({DISPOSITION_MANUAL_REVIEW, DISPOSITION_FAILED})
ALLOWED_DISPOSITIONS = SUCCESS_DISPOSITIONS | NON_SUCCESS_DISPOSITIONS | {
    DISPOSITION_REENTER_GATE
}
MAX_PUBLISHED_ARTIFACT_REFS = 20


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindMergeAutomationWorkflow:
    """Wait for PR readiness, run pr-resolver as a child, and return a parent outcome."""

    def __init__(self) -> None:
        self._status = STATE_WAITING
        self._input: MergeAutomationStartInput | None = None
        self._blockers: list[ReadinessBlockerModel] = []
        self._resolver_child_workflow_ids: list[str] = []
        self._gate_snapshot_artifact_refs: list[str] = []
        self._resolver_attempt_artifact_refs: list[str] = []
        self._summary_artifact_ref: str | None = None
        self._post_merge_jira_resolution_artifact_ref: str | None = None
        self._post_merge_jira_transition_artifact_ref: str | None = None
        self._post_merge_jira_result: dict[str, Any] | None = None
        self._external_event_count = 0
        self._refresh_tracked_head_sha_on_next_evaluation = False
        self._summary: str | None = None

    def _summary_payload(self) -> dict[str, Any]:
        pr = self._input.pull_request if self._input is not None else None
        artifact_refs = {
            "summary": self._summary_artifact_ref,
            "gateSnapshots": self._published_artifact_refs(
                self._gate_snapshot_artifact_refs
            ),
            "resolverAttempts": self._published_artifact_refs(
                self._resolver_attempt_artifact_refs
            ),
        }
        if self._post_merge_jira_resolution_artifact_ref:
            artifact_refs["postMergeJiraResolution"] = (
                self._post_merge_jira_resolution_artifact_ref
            )
        if self._post_merge_jira_transition_artifact_ref:
            artifact_refs["postMergeJiraTransition"] = (
                self._post_merge_jira_transition_artifact_ref
            )
        payload = {
            "status": self._status,
            "prNumber": pr.number if pr is not None else None,
            "prUrl": pr.url if pr is not None else None,
            "cycles": len(self._resolver_child_workflow_ids),
            "resolverChildWorkflowIds": list(self._resolver_child_workflow_ids),
            "latestHeadSha": pr.head_sha if pr is not None else None,
            "blockers": [
                blocker.model_dump(by_alias=True, mode="json")
                for blocker in self._blockers
            ],
            "artifactRefs": artifact_refs,
        }
        if self._summary:
            payload["summary"] = self._summary
        if self._post_merge_jira_result is not None:
            payload["postMergeJira"] = dict(self._post_merge_jira_result)
        return payload

    @staticmethod
    def _published_artifact_refs(refs: list[str]) -> list[str]:
        return list(refs[-MAX_PUBLISHED_ARTIFACT_REFS:])

    @staticmethod
    def _artifact_id_from_ref(artifact_ref: Any) -> str:
        if isinstance(artifact_ref, Mapping):
            return str(
                artifact_ref.get("artifact_id") or artifact_ref.get("artifactId") or ""
            )
        return str(
            getattr(artifact_ref, "artifact_id", "")
            or getattr(artifact_ref, "artifactId", "")
        )

    def _principal(self) -> str:
        if self._input is None:
            return "merge-automation"
        return self._input.principal or self._input.parent_workflow_id

    def _resolver_owner_type(self) -> str:
        return "system" if self._principal() == "system" else "user"

    def _resolver_search_attributes(self) -> dict[str, list[str]]:
        attributes = {
            "mm_owner_type": [self._resolver_owner_type()],
            "mm_owner_id": [self._principal()],
            "mm_entry": ["run"],
        }
        if self._input is not None:
            attributes["mm_repo"] = [self._input.pull_request.repo]
        return attributes

    async def _write_json_artifact(self, *, name: str, payload: dict[str, Any]) -> str | None:
        try:
            artifact_ref, _upload_desc = await workflow.execute_activity(
                "artifact.create",
                {
                    "principal": self._principal(),
                    "name": name,
                    "content_type": "application/json",
                },
                task_queue=ARTIFACTS_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=60),
                schedule_to_close_timeout=timedelta(seconds=120),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            artifact_id = self._artifact_id_from_ref(artifact_ref)
            if not artifact_id:
                return None
            await execute_typed_activity(
                "artifact.write_complete",
                ArtifactWriteCompleteInput(
                    principal=self._principal(),
                    artifact_id=artifact_id,
                    payload=(json.dumps(payload, sort_keys=True, indent=2) + "\n").encode(
                        "utf-8"
                    ),
                    content_type="application/json",
                ),
                task_queue=ARTIFACTS_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=60),
                schedule_to_close_timeout=timedelta(seconds=120),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            return artifact_id
        except CancelledError:
            raise
        except Exception:
            return None

    async def _write_gate_snapshot(self, *, evidence_ready: bool) -> None:
        snapshot_name = (
            "artifacts/merge_automation/gate_snapshots/"
            f"{len(self._resolver_child_workflow_ids)}.json"
        )
        artifact_id = await self._write_json_artifact(
            name=snapshot_name,
            payload={
                "status": self._status,
                "ready": evidence_ready,
                "summary": self._summary_payload(),
            },
        )
        if artifact_id:
            self._gate_snapshot_artifact_refs.append(artifact_id)

    async def _write_resolver_attempt(
        self, *, workflow_id: str, result: Any | None = None
    ) -> None:
        payload: dict[str, Any] = {
            "status": self._status,
            "workflowId": workflow_id,
            "attempt": len(self._resolver_child_workflow_ids),
            "summary": self._summary_payload(),
        }
        if isinstance(result, Mapping):
            payload["result"] = {
                "status": result.get("status"),
                "mergeAutomationDisposition": result.get("mergeAutomationDisposition"),
                "headSha": result.get("headSha"),
            }
        attempt_name = (
            "artifacts/merge_automation/resolver_attempts/"
            f"{len(self._resolver_child_workflow_ids)}.json"
        )
        artifact_id = await self._write_json_artifact(name=attempt_name, payload=payload)
        if artifact_id:
            self._resolver_attempt_artifact_refs.append(artifact_id)

    async def _finish(self) -> dict[str, Any]:
        payload = self._summary_payload()
        artifact_id = await self._write_json_artifact(
            name="reports/merge_automation_summary.json",
            payload=payload,
        )
        if artifact_id:
            self._summary_artifact_ref = artifact_id
            payload = self._summary_payload()
        self._publish_visibility()
        return payload

    def _publish_visibility(self) -> None:
        workflow.upsert_memo({"summary": self._summary_payload()})
        workflow.upsert_search_attributes(
            [
                SearchAttributePair(SearchAttributeKey.for_keyword("mm_state"), self._status),
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("mm_entry"),
                    "merge_automation",
                ),
            ]
        )

    @workflow.signal(name="merge_automation.external_event")
    def external_event(self, _payload: dict[str, Any]) -> None:
        self._external_event_count += 1

    @workflow.query
    def summary(self) -> dict[str, Any]:
        return self._summary_payload()

    @staticmethod
    def _resolver_disposition(resolver_result: Any) -> str:
        if not isinstance(resolver_result, Mapping):
            return ""
        return str(resolver_result.get("mergeAutomationDisposition") or "").strip()

    @staticmethod
    def _head_sha_from_mapping(payload: Mapping[str, Any]) -> str:
        for key in ("headSha", "head_sha", "latestHeadSha", "latest_head_sha"):
            candidate = str(payload.get(key) or "").strip()
            if candidate:
                return candidate
        for key in ("pullRequest", "pull_request", "mergeGate", "merge_gate"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                candidate = MoonMindMergeAutomationWorkflow._head_sha_from_mapping(nested)
                if candidate:
                    return candidate
        return ""

    def _refresh_tracked_head_sha(self, payload: Any) -> bool:
        if self._input is None or not isinstance(payload, Mapping):
            return False
        head_sha = self._head_sha_from_mapping(payload)
        if not head_sha:
            return False
        self._input.pull_request.head_sha = head_sha
        return True

    async def _failed_resolver_summary(
        self,
        *,
        summary: str,
        blocker_kind: str,
    ) -> dict[str, Any]:
        self._status = STATE_FAILED
        self._summary = summary
        self._blockers = [
            ReadinessBlockerModel.model_validate(
                {
                    "kind": blocker_kind,
                    "summary": summary,
                    "retryable": False,
                    "source": "pr-resolver",
                }
            )
        ]
        self._publish_visibility()
        return await self._finish()

    async def _complete_post_merge_jira(
        self,
        *,
        resolver_disposition: str,
    ) -> bool:
        if self._input is None:
            return True
        if not self._input.config.post_merge_jira.enabled:
            return True
        decision = await workflow.execute_activity(
            "merge_automation.complete_post_merge_jira",
            {
                "parentWorkflowId": self._input.parent_workflow_id,
                "parentRunId": self._input.parent_run_id,
                "resolverDisposition": resolver_disposition,
                "pullRequest": self._input.pull_request.model_dump(
                    by_alias=True, mode="json"
                ),
                "jiraIssueKey": self._input.jira_issue_key,
                "postMergeJira": self._input.config.post_merge_jira.model_dump(
                    by_alias=True, mode="json"
                ),
                "candidateContext": {
                    "publishContextIssueKey": self._input.jira_issue_key,
                    "prMetadataKeys": [self._input.jira_issue_key]
                    if self._input.jira_issue_key
                    else [],
                },
            },
            start_to_close_timeout=timedelta(minutes=2),
            task_queue=INTEGRATIONS_TASK_QUEUE,
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
        )
        decision_map = dict(decision) if isinstance(decision, Mapping) else {}
        self._post_merge_jira_result = {
            key: value
            for key, value in decision_map.items()
            if key not in {"issueResolution", "transition", "artifactRefs"}
        }
        resolution = decision_map.get("issueResolution")
        if isinstance(resolution, Mapping):
            artifact_id = await self._write_json_artifact(
                name="artifacts/merge_automation/post_merge_jira_resolution.json",
                payload=dict(resolution),
            )
            if artifact_id:
                self._post_merge_jira_resolution_artifact_ref = artifact_id
        transition = decision_map.get("transition")
        if isinstance(transition, Mapping):
            artifact_id = await self._write_json_artifact(
                name="artifacts/merge_automation/post_merge_jira_transition.json",
                payload=dict(transition),
            )
            if artifact_id:
                self._post_merge_jira_transition_artifact_ref = artifact_id

        if self._post_merge_jira_resolution_artifact_ref:
            self._post_merge_jira_result["artifactRefs"] = {
                "resolution": self._post_merge_jira_resolution_artifact_ref
            }
        if self._post_merge_jira_transition_artifact_ref:
            self._post_merge_jira_result.setdefault("artifactRefs", {})[
                "transition"
            ] = self._post_merge_jira_transition_artifact_ref

        status = str(decision_map.get("status") or "").strip()
        required = bool(decision_map.get("required", True))
        if required and status in {"blocked", "failed"}:
            reason = str(
                decision_map.get("reason")
                or "Required post-merge Jira completion did not succeed."
            ).strip()
            self._status = STATE_FAILED
            self._summary = reason
            self._blockers = [
                ReadinessBlockerModel.model_validate(
                    {
                        "kind": DISPOSITION_FAILED,
                        "summary": reason,
                        "retryable": False,
                        "source": "jira",
                    }
                )
            ]
            self._publish_visibility()
            return False
        return True

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._input = MergeAutomationStartInput.model_validate(payload)
        self._status = STATE_WAITING
        expire_at = _effective_expire_at(self._input, started_at=workflow.now())
        self._publish_visibility()

        while True:
            if expire_at is not None and workflow.now() >= expire_at:
                self._status = STATE_EXPIRED
                self._publish_visibility()
                return await self._finish()

            evaluation = await workflow.execute_activity(
                "merge_automation.evaluate_readiness",
                self._input.model_dump(by_alias=True, mode="json"),
                start_to_close_timeout=timedelta(minutes=2),
                task_queue=INTEGRATIONS_TASK_QUEUE,
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
            evidence = classify_readiness(
                evaluation if isinstance(evaluation, Mapping) else {},
                tracked_head_sha=self._input.pull_request.head_sha,
            )
            if self._refresh_tracked_head_sha_on_next_evaluation:
                self._refresh_tracked_head_sha_on_next_evaluation = False
                if self._refresh_tracked_head_sha(evaluation):
                    evidence = classify_readiness(
                        evaluation if isinstance(evaluation, Mapping) else {},
                        tracked_head_sha=self._input.pull_request.head_sha,
                    )
            self._blockers = list(evidence.blockers)
            await self._write_gate_snapshot(evidence_ready=evidence.ready)
            if evidence.pull_request_merged:
                self._refresh_tracked_head_sha(evaluation)
                if not await self._complete_post_merge_jira(
                    resolver_disposition=DISPOSITION_ALREADY_MERGED
                ):
                    return await self._finish()
                self._status = STATE_ALREADY_MERGED
                self._publish_visibility()
                return await self._finish()
            if evidence.ready:
                self._status = STATE_EXECUTING
                self._publish_visibility()
                resolver_request = build_resolver_run_request(
                    parent_workflow_id=self._input.parent_workflow_id,
                    pull_request=self._input.pull_request,
                    jira_issue_key=self._input.jira_issue_key,
                    merge_method=self._input.config.resolver.merge_method,
                    resolver_template=self._input.resolver_template,
                )
                resolver_workflow_id_factory = (
                    deterministic_resolver_idempotency_key
                    if workflow.patched("merge-automation-hashed-resolver-child-id")
                    else legacy_resolver_idempotency_key
                )
                resolver_workflow_id = resolver_workflow_id_factory(
                    parent_workflow_id=self._input.parent_workflow_id,
                    repo=self._input.pull_request.repo,
                    pr_number=self._input.pull_request.number,
                    head_sha=self._input.pull_request.head_sha,
                )
                resolver_workflow_id = (
                    f"{resolver_workflow_id}:{len(self._resolver_child_workflow_ids) + 1}"
                )
                self._resolver_child_workflow_ids.append(resolver_workflow_id)
                try:
                    resolver_result = await workflow.execute_child_workflow(
                        "MoonMind.Run",
                        resolver_request,
                        id=resolver_workflow_id,
                        task_queue=WORKFLOW_TASK_QUEUE,
                        search_attributes=self._resolver_search_attributes(),
                        cancellation_type=ChildWorkflowCancellationType.TRY_CANCEL,
                        static_summary="Resolving pull request for merge automation",
                        static_details=f"Resolve {self._input.pull_request.url}",
                    )
                except CancelledError:
                    self._status = STATE_CANCELED
                    self._summary = (
                        "Merge automation canceled while resolver child was active."
                    )
                    await self._write_resolver_attempt(workflow_id=resolver_workflow_id)
                    self._publish_visibility()
                    return await self._finish()
                await self._write_resolver_attempt(
                    workflow_id=resolver_workflow_id,
                    result=resolver_result,
                )
                resolver_status = str(
                    (resolver_result or {}).get("status")
                    if isinstance(resolver_result, Mapping)
                    else ""
                ).strip()
                resolver_disposition = self._resolver_disposition(resolver_result)
                if resolver_status != "success":
                    return await self._failed_resolver_summary(
                        summary="pr-resolver child run did not complete successfully.",
                        blocker_kind=DISPOSITION_FAILED,
                    )
                if not resolver_disposition:
                    return await self._failed_resolver_summary(
                        summary=(
                            "pr-resolver child result missing "
                            "mergeAutomationDisposition."
                        ),
                        blocker_kind="resolver_disposition_invalid",
                    )
                if resolver_disposition not in ALLOWED_DISPOSITIONS:
                    return await self._failed_resolver_summary(
                        summary=(
                            "pr-resolver child result has unsupported "
                            "mergeAutomationDisposition: "
                            f"{resolver_disposition}"
                        ),
                        blocker_kind="resolver_disposition_invalid",
                    )
                if (
                    resolver_disposition == DISPOSITION_REENTER_GATE
                ):
                    self._refresh_tracked_head_sha_on_next_evaluation = (
                        not self._refresh_tracked_head_sha(resolver_result)
                    )
                    self._status = STATE_WAITING
                    self._publish_visibility()
                    continue
                if resolver_disposition == DISPOSITION_ALREADY_MERGED:
                    if not await self._complete_post_merge_jira(
                        resolver_disposition=resolver_disposition
                    ):
                        return await self._finish()
                    self._status = STATE_ALREADY_MERGED
                    self._publish_visibility()
                    return await self._finish()
                if resolver_disposition == DISPOSITION_MERGED:
                    if not await self._complete_post_merge_jira(
                        resolver_disposition=resolver_disposition
                    ):
                        return await self._finish()
                    self._status = STATE_MERGED
                    self._publish_visibility()
                    return await self._finish()
                if resolver_disposition == DISPOSITION_MANUAL_REVIEW:
                    return await self._failed_resolver_summary(
                        summary="pr-resolver requested manual review.",
                        blocker_kind=DISPOSITION_MANUAL_REVIEW,
                    )
                if resolver_disposition == DISPOSITION_FAILED:
                    return await self._failed_resolver_summary(
                        summary="pr-resolver reported failure.",
                        blocker_kind=DISPOSITION_FAILED,
                    )

            if any(blocker.kind in TERMINAL_BLOCKER_KINDS for blocker in self._blockers):
                self._status = STATE_BLOCKED
                self._publish_visibility()
                return await self._finish()

            self._status = STATE_WAITING
            self._publish_visibility()
            try:
                target_event_count = self._external_event_count
                await workflow.wait_condition(
                    lambda: self._external_event_count > target_event_count,
                    timeout=timedelta(
                        seconds=self._input.config.timeouts.fallback_poll_seconds
                    ),
                )
            except TimeoutError:
                # Expected fallback poll wake-up when no external signal arrives.
                pass
