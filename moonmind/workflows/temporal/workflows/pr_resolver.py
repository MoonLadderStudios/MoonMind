"""Replay support for the superseded native PR-resolver state machine.

New resolver runs execute the exact resolved Skill bundle through
``MoonMind.AgentRun``. Keep this workflow registered only for histories that
already recorded ``MoonMind.PRResolver`` before the skill-owned cutover.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from hashlib import sha256
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError
from temporalio.workflow import ActivityCancellationType, ChildWorkflowCancellationType

with workflow.unsafe.imports_passed_through():
    from pr_resolver_core import (
        IMPLEMENTATION_CONTRACT,
        RESOLVER_CORE_DIGEST,
        RESOLVER_CORE_VERSION,
        ResolverEvent,
        ResolverPolicy,
        ResolverState,
        classify_snapshot,
        normalize_temporal_snapshot,
        reduce_resolver_state,
    )
    from moonmind.config.settings import settings
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.schemas.temporal_models import (
        PRResolverStartInput,
        PRResolverTerminalResultModel,
    )
    from moonmind.workflows.temporal.activity_catalog import (
        ARTIFACTS_TASK_QUEUE,
        INTEGRATIONS_TASK_QUEUE,
    )


WORKFLOW_NAME = "MoonMind.PRResolver"
PR_RESOLVER_IMPLEMENTATION_IDENTITY_PATCH = "pr-resolver-implementation-identity-v1"
PR_RESOLVER_SHARED_CORE_PATCH = "pr-resolver-shared-core-v1"
PR_RESOLVER_CI_FAILURE_PRECEDENCE_PATCH = (
    "pr-resolver-ci-failure-precedence-v1"
)
ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=5,
)

WAIT_CLASSIFICATIONS = frozenset(
    {"ci_running", "review_grace", "mergeability_transient"}
)
REMEDIATION_SKILLS = {
    "actionable_comments": "fix-comments",
    "ci_failures": "fix-ci",
    "merge_conflicts": "fix-merge-conflicts",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _workflow_patch_enabled(patch_id: str) -> bool:
    try:
        return workflow.patched(patch_id)
    except Exception as exc:
        if exc.__class__.__name__ == "_NotInWorkflowEventLoopError":
            return True
        raise


def pr_resolver_identity_selector(
    *,
    request: AgentExecutionRequest,
    node_inputs: Mapping[str, Any],
    workflow_parameters: Mapping[str, Any],
) -> tuple[str, str]:
    """Return the repository and authored PR selector at the resolver boundary."""

    skill_payload = node_inputs.get("skill")
    skill_payload = skill_payload if isinstance(skill_payload, Mapping) else {}
    skill_inputs = skill_payload.get("inputs")
    if not isinstance(skill_inputs, Mapping):
        nested = node_inputs.get("inputs")
        skill_inputs = nested if isinstance(nested, Mapping) else {}
    workspace = request.workspace_spec or {}
    repository = _text(
        skill_inputs.get("repo")
        or workspace.get("repository")
        or workspace.get("repo")
        or workflow_parameters.get("repository")
        or workflow_parameters.get("repo")
    )
    selector = _text(
        skill_inputs.get("pr")
        or node_inputs.get("pr")
        or skill_inputs.get("branch")
        or node_inputs.get("branch")
    )
    return repository, selector


def classify_pr_resolver_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministically classify a captured GitHub snapshot."""
    decision = classify_snapshot(normalize_temporal_snapshot(snapshot))
    return {
        "classification": decision.classification,
        "reasonCode": decision.reason_code,
    }


def blocker_progress_signature(snapshot: Mapping[str, Any], classification: str) -> str:
    """Return a stable signature used to bound identical no-progress blockers."""

    parts = [
        _text(snapshot.get("headSha")),
        _text(snapshot.get("baseSha")),
        _text(classification),
    ]
    for blocker in snapshot.get("blockers", ()):
        if isinstance(blocker, Mapping):
            parts.extend((_text(blocker.get("kind")), _text(blocker.get("summary"))))
    return sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def build_pr_resolver_start_input(
    *,
    request: AgentExecutionRequest,
    node_inputs: Mapping[str, Any],
    workflow_parameters: Mapping[str, Any],
    parent_workflow_id: str,
    parent_run_id: str,
    principal: str,
    step_id: str,
    resolved_pull_request: Mapping[str, Any] | None = None,
    implementation_identity: Mapping[str, Any] | None = None,
    worker_capability: Mapping[str, Any] | None = None,
) -> PRResolverStartInput:
    """Build the resolver boundary from an already prepared agent request."""

    skill_payload = node_inputs.get("skill")
    skill_payload = skill_payload if isinstance(skill_payload, Mapping) else {}
    skill_inputs = skill_payload.get("inputs")
    if not isinstance(skill_inputs, Mapping):
        nested = node_inputs.get("inputs")
        skill_inputs = nested if isinstance(nested, Mapping) else {}
    repository, pr_value = pr_resolver_identity_selector(
        request=request,
        node_inputs=node_inputs,
        workflow_parameters=workflow_parameters,
    )
    merge_gate = workflow_parameters.get("mergeGate")
    merge_gate = merge_gate if isinstance(merge_gate, Mapping) else {}
    resolved = (
        resolved_pull_request if isinstance(resolved_pull_request, Mapping) else {}
    )
    try:
        pr_number = int(resolved.get("prNumber") or pr_value)
    except (TypeError, ValueError):
        pr_url_parts = _text(merge_gate.get("pullRequestUrl")).rstrip("/").split("/")
        try:
            pr_number = int(pr_url_parts[-1])
        except (ValueError, IndexError) as url_exc:
            raise ValueError(
                "branch-style pr-resolver selectors require a resolved mergeGate.pullRequestUrl"
            ) from url_exc
    if not repository:
        raise ValueError("pr-resolver requires inputs.repo or workspace repository")

    legacy_args = node_inputs.get("args")
    legacy_args = legacy_args if isinstance(legacy_args, Mapping) else {}
    merge_method = _text(
        node_inputs.get("mergeMethod")
        or skill_inputs.get("mergeMethod")
        or legacy_args.get("mergeMethod")
        or merge_gate.get("mergeMethod")
        or "squash"
    ).lower()
    pr_url = _text(resolved.get("prUrl") or merge_gate.get("pullRequestUrl")) or (
        f"https://github.com/{repository}/pull/{pr_number}"
    )
    policy = {
        "pollIntervalSeconds": skill_inputs.get("finalizeBackoffSeconds", 60),
        "maxElapsedSeconds": skill_inputs.get("finalizeMaxElapsedSeconds", 7200),
        "maxFinalizeAttempts": skill_inputs.get("finalizeMaxRetries", 60),
        "maxRemediationsPerType": skill_inputs.get("maxRemediationsPerType", 2),
        "maxIdenticalBlockersWithoutProgress": skill_inputs.get(
            "maxIdenticalBlockersWithoutProgress", 2
        ),
        "checks": merge_gate.get("checks", "required"),
        "automatedReview": merge_gate.get("automatedReview", "required"),
    }
    return PRResolverStartInput.model_validate(
        {
            "workflowType": WORKFLOW_NAME,
            "parentWorkflowId": parent_workflow_id,
            "parentRunId": parent_run_id,
            "principal": principal,
            "repository": repository,
            "prNumber": pr_number,
            "prUrl": pr_url,
            "mergeMethod": merge_method,
            "headSha": merge_gate.get("headSha"),
            "stepId": step_id,
            "correlationId": request.correlation_id,
            "baseAgentRequest": request.model_dump(by_alias=True, mode="json"),
            "policy": policy,
            "shadowMode": bool(skill_inputs.get("shadowMode", False)),
            "ownedByMergeAutomationGate": bool(merge_gate),
            "implementationIdentity": dict(implementation_identity or {}),
            "workerCapability": dict(worker_capability or {}),
        }
    )


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindPRResolverWorkflow:
    """Own the long-lived PR gate loop and dispatch bounded remediation children."""

    def __init__(self) -> None:
        self._input: PRResolverStartInput | None = None
        self._state = "initialize"
        self._head_sha: str | None = None
        self._base_sha: str | None = None
        self._classification: str | None = None
        self._finalize_attempts = 0
        self._remediation_counts = {skill: 0 for skill in REMEDIATION_SKILLS.values()}
        self._last_progress_signature: str | None = None
        self._identical_no_progress_count = 0
        self._active_remediation_child_id: str | None = None
        self._latest_snapshot: dict[str, Any] = {}
        self._timeline: list[dict[str, Any]] = []

    def _record_timeline(self, event: str, **details: Any) -> None:
        self._timeline.append(
            {
                "event": event,
                "at": workflow.now().isoformat(),
                "headSha": self._head_sha,
                "classification": self._classification,
                **details,
            }
        )
        self._timeline = self._timeline[-100:]

    @workflow.query(name="pr_resolver.state")
    def state(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "headSha": self._head_sha,
            "baseSha": self._base_sha,
            "classification": self._classification,
            "finalizeAttemptCount": self._finalize_attempts,
            "remediationCounts": dict(self._remediation_counts),
            "lastMeaningfulProgressSignature": self._last_progress_signature,
            "activeRemediationChildId": self._active_remediation_child_id,
            "latestSnapshot": dict(self._latest_snapshot),
            "timeline": list(self._timeline),
        }

    async def _read_snapshot(self, *, transition: str, attempt: int) -> dict[str, Any]:
        assert self._input is not None
        payload = {
            "repository": self._input.repository,
            "prNumber": self._input.pr_number,
            "prUrl": self._input.pr_url,
            "headSha": self._head_sha or self._input.head_sha or "",
            "idempotencyKey": (
                f"{workflow.info().workflow_id}:{transition}:"
                f"{self._head_sha or 'unknown'}:{attempt}"
            ),
            "policy": {
                "checks": self._input.policy.checks,
                "automatedReview": self._input.policy.automated_review,
            },
        }
        result = await workflow.execute_activity(
            "pr_resolver.read_snapshot",
            payload,
            task_queue=INTEGRATIONS_TASK_QUEUE,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=ACTIVITY_RETRY_POLICY,
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
        )
        snapshot = dict(result) if isinstance(result, Mapping) else {}
        self._latest_snapshot = snapshot
        self._head_sha = _text(snapshot.get("headSha")) or self._head_sha
        self._base_sha = _text(snapshot.get("baseSha")) or self._base_sha
        self._record_timeline("github_snapshot", transition=transition, attempt=attempt)
        return snapshot

    def _remediation_request(
        self, *, skill: str, snapshot: Mapping[str, Any]
    ) -> AgentExecutionRequest:
        assert self._input is not None
        base = AgentExecutionRequest.model_validate(self._input.base_agent_request)
        count = self._remediation_counts[skill]
        metadata = dict(base.parameters.get("metadata") or {})
        moonmind = dict(metadata.get("moonmind") or {})
        moonmind.update(
            {
                "selectedSkill": skill,
                "prResolver": {
                    "parentWorkflowId": workflow.info().workflow_id,
                    "repository": self._input.repository,
                    "prNumber": self._input.pr_number,
                    "headSha": self._head_sha,
                    "blockerClassification": self._classification,
                    "remediationAttempt": count,
                },
            }
        )
        metadata["moonmind"] = moonmind
        parameters = dict(base.parameters)
        parameters["metadata"] = metadata
        instruction = (
            f"Use the {skill} skill for exactly one bounded remediation action on "
            f"{self._input.pr_url} at head {self._head_sha or 'unknown'}. "
            "Commit and push only when that skill requires it. Do not poll, merge, "
            "or run the outer PR resolver loop. Return structured evidence of the "
            "remote change or blocker."
        )
        return base.model_copy(
            update={
                "instruction_ref": instruction,
                "idempotency_key": (
                    f"{workflow.info().workflow_id}:remediation:{skill}:"
                    f"{self._head_sha or 'unknown'}:{count}"
                ),
                "skill": {"name": skill, "inputs": {"pr": str(self._input.pr_number)}},
                "parameters": parameters,
            }
        )

    async def _publish_terminal(
        self,
        *,
        status: str,
        reason_code: str,
        reason: str,
        merge_sha: str | None = None,
    ) -> dict[str, Any]:
        assert self._input is not None
        disposition = status
        if status in {"manual_review", "canceled"}:
            disposition = "manual_review"
        elif status == "failed":
            disposition = "failed"
        terminal_payload = {
            "status": status,
            "mergeOutcome": status,
            "mergeAutomationDisposition": disposition,
            "reason": reason,
            "reasonCode": reason_code,
            "nextStep": "done"
            if status in {"merged", "already_merged"}
            else "manual_review",
            "repository": self._input.repository,
            "prNumber": self._input.pr_number,
            "prUrl": self._input.pr_url,
            "verifiedHeadSha": self._head_sha,
            "verifiedMergeSha": merge_sha,
            "finalizeAttemptCount": self._finalize_attempts,
            "remediationCounts": self._remediation_counts,
            "latestSnapshot": self._latest_snapshot,
            "timeline": self._timeline[-20:],
            "workflowId": workflow.info().workflow_id,
            "stepId": self._input.step_id,
            "correlationId": self._input.correlation_id,
        }
        if _workflow_patch_enabled(PR_RESOLVER_IMPLEMENTATION_IDENTITY_PATCH):
            identity = self._input.implementation_identity
            capability = self._input.worker_capability
            terminal_payload.update(
                {
                    "implementationContract": IMPLEMENTATION_CONTRACT,
                    "resolverCoreVersion": RESOLVER_CORE_VERSION,
                    "resolverCoreDigest": RESOLVER_CORE_DIGEST,
                    "resolvedSkillContentDigest": identity.get("contentDigest"),
                    "resolvedSkillSource": identity.get("sourceKind"),
                    "workerBuildSha": capability.get("buildSha"),
                    "workerImageDigest": capability.get("imageDigest"),
                    "workerDeploymentId": capability.get("deploymentId"),
                    "workflowRegistryFingerprint": capability.get(
                        "registryFingerprint"
                    ),
                    "taskQueue": capability.get("taskQueue"),
                    "workflowType": WORKFLOW_NAME,
                }
            )
        result = PRResolverTerminalResultModel.model_validate(
            terminal_payload
        ).model_dump(by_alias=True, mode="json")
        publication = await workflow.execute_activity(
            "pr_resolver.write_terminal_result",
            {
                "principal": self._input.principal,
                "terminalResult": result,
                "idempotencyKey": (
                    f"{workflow.info().workflow_id}:terminal:{status}:{reason_code}"
                ),
                "executionRef": {
                    "namespace": workflow.info().namespace,
                    "workflow_id": workflow.info().workflow_id,
                    "run_id": workflow.info().run_id,
                },
            },
            task_queue=ARTIFACTS_TASK_QUEUE,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=ACTIVITY_RETRY_POLICY,
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
        )
        publication = dict(publication) if isinstance(publication, Mapping) else {}
        result["publishEvidenceRef"] = publication.get("publishEvidenceRef")
        failure_class = None
        if status == "failed" or (
            status in {"manual_review", "canceled"}
            and not self._input.owned_by_merge_automation_gate
        ):
            failure_class = "execution_error"
        return {
            "outputRefs": {
                "prResolverResult": _text(publication.get("resultRef")),
                "publishEvidence": _text(publication.get("publishEvidenceRef")),
            },
            "publishEvidence": publication.get("publishEvidenceRef"),
            "summary": reason,
            "failureClass": failure_class,
            "metadata": {
                "mergeAutomationDisposition": disposition,
                "prResolverTerminalResult": result,
                "verifiedRemoteHead": self._head_sha,
            },
        }

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._input = PRResolverStartInput.model_validate(payload)
        self._head_sha = self._input.head_sha
        if self._input.canary_mode:
            return {
                "summary": "MoonMind.PRResolver deployment canary accepted.",
                "failureClass": None,
                "outputRefs": {},
                "metadata": {
                    "canary": True,
                    "workflowType": WORKFLOW_NAME,
                    "implementationIdentity": self._input.implementation_identity,
                    "workerCapability": self._input.worker_capability,
                },
            }
        self._base_sha = self._input.base_sha
        started_at = workflow.now()
        iteration = 0
        try:
            while True:
                iteration += 1
                elapsed = (workflow.now() - started_at).total_seconds()
                if elapsed >= self._input.policy.max_elapsed_seconds:
                    self._state = "failed"
                    return await self._publish_terminal(
                        status="failed",
                        reason_code="elapsed_budget_exhausted",
                        reason="PR resolver elapsed-time budget was exhausted.",
                    )

                self._state = "read_pr_snapshot"
                snapshot = await self._read_snapshot(
                    transition="snapshot", attempt=iteration
                )
                self._state = "classify_gate"
                classified = await workflow.execute_activity(
                    "pr_resolver.classify_gate",
                    {
                        "snapshot": snapshot,
                        "idempotencyKey": (
                            f"{workflow.info().workflow_id}:classify:"
                            f"{self._head_sha or 'unknown'}:{iteration}"
                        ),
                    },
                    task_queue=INTEGRATIONS_TASK_QUEUE,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=ACTIVITY_RETRY_POLICY,
                )
                decision = (
                    dict(classified)
                    if isinstance(classified, Mapping)
                    else classify_pr_resolver_snapshot(snapshot)
                )
                self._classification = decision["classification"]
                progress_signature = blocker_progress_signature(
                    snapshot, self._classification
                )
                transition = None
                if _workflow_patch_enabled(PR_RESOLVER_SHARED_CORE_PATCH):
                    transition = reduce_resolver_state(
                        previous_state=ResolverState(
                            finalize_attempts=self._finalize_attempts,
                            remediation_counts=tuple(
                                sorted(self._remediation_counts.items())
                            ),
                            identical_blocker_count=self._identical_no_progress_count,
                            last_progress_signature=self._last_progress_signature,
                        ),
                        snapshot=normalize_temporal_snapshot(snapshot),
                        policy=ResolverPolicy(
                            max_finalize_attempts=(
                                self._input.policy.max_finalize_attempts
                            ),
                            max_remediations_per_type=(
                                self._input.policy.max_remediations_per_type
                            ),
                            max_identical_blockers_without_progress=(
                                self._input.policy.max_identical_blockers_without_progress
                            ),
                        ),
                        event=ResolverEvent(
                            kind="snapshot",
                            progress_signature=progress_signature,
                        ),
                        known_ci_failures_precede_degraded=_workflow_patch_enabled(
                            PR_RESOLVER_CI_FAILURE_PRECEDENCE_PATCH
                        ),
                    )
                    self._classification = transition.decision.classification
                transition_reason = (
                    transition.decision.reason_code
                    if transition is not None
                    else _text(decision.get("reasonCode"))
                )
                self._record_timeline(
                    "gate_classified",
                    reasonCode=transition_reason,
                )

                if self._classification == "already_merged":
                    self._state = "verify_remote_merge"
                    verified = await workflow.execute_activity(
                        "pr_resolver.verify_merged",
                        {
                            "repository": self._input.repository,
                            "prNumber": self._input.pr_number,
                            "prUrl": self._input.pr_url,
                            "headSha": self._head_sha or "",
                            "idempotencyKey": f"{workflow.info().workflow_id}:verify:already-merged",
                        },
                        task_queue=INTEGRATIONS_TASK_QUEUE,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=ACTIVITY_RETRY_POLICY,
                    )
                    if isinstance(verified, Mapping) and verified.get("merged") is True:
                        self._state = "completed"
                        return await self._publish_terminal(
                            status="already_merged",
                            reason_code="already_merged",
                            reason="Pull request was independently verified as already merged.",
                            merge_sha=_text(verified.get("mergeSha")) or None,
                        )
                    self._classification = "mergeability_transient"

                if transition_reason == "finalize_budget_exhausted":
                    self._state = "failed"
                    return await self._publish_terminal(
                        status="failed",
                        reason_code="finalize_budget_exhausted",
                        reason="PR merge finalize-attempt budget was exhausted.",
                    )
                if transition_reason == "repeated_blocker_without_progress":
                    self._state = "manual_review"
                    return await self._publish_terminal(
                        status="manual_review",
                        reason_code="repeated_blocker_without_progress",
                        reason=(
                            "The same actionable blocker repeated without a remote "
                            "head change."
                        ),
                    )

                if self._classification == "ready_to_merge":
                    if self._input.shadow_mode:
                        self._state = "shadow_complete"
                        return await self._publish_terminal(
                            status="manual_review",
                            reason_code="shadow_ready_to_merge",
                            reason="Shadow resolver classified the pull request as ready; mutations were disabled.",
                        )
                    if transition is None:
                        if (
                            self._finalize_attempts
                            >= self._input.policy.max_finalize_attempts
                        ):
                            self._state = "failed"
                            return await self._publish_terminal(
                                status="failed",
                                reason_code="finalize_budget_exhausted",
                                reason=(
                                    "PR merge finalize-attempt budget was exhausted."
                                ),
                            )
                        self._finalize_attempts += 1
                    else:
                        self._finalize_attempts = transition.state.finalize_attempts
                    self._state = "finalize_merge"
                    self._record_timeline(
                        "finalize_started", attempt=self._finalize_attempts
                    )
                    finalize = await workflow.execute_activity(
                        "pr_resolver.finalize_merge",
                        {
                            "repository": self._input.repository,
                            "prNumber": self._input.pr_number,
                            "prUrl": self._input.pr_url,
                            "headSha": self._head_sha or "",
                            "mergeMethod": self._input.merge_method,
                            "policy": {
                                "checks": self._input.policy.checks,
                                "automatedReview": self._input.policy.automated_review,
                            },
                            "attempt": self._finalize_attempts,
                            "idempotencyKey": (
                                f"{workflow.info().workflow_id}:merge:"
                                f"{self._head_sha or 'unknown'}:{self._finalize_attempts}"
                            ),
                        },
                        task_queue=INTEGRATIONS_TASK_QUEUE,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=ACTIVITY_RETRY_POLICY,
                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                    )
                    self._state = "verify_remote_merge"
                    verified = await workflow.execute_activity(
                        "pr_resolver.verify_merged",
                        {
                            "repository": self._input.repository,
                            "prNumber": self._input.pr_number,
                            "prUrl": self._input.pr_url,
                            "headSha": self._head_sha or "",
                            "idempotencyKey": (
                                f"{workflow.info().workflow_id}:verify:"
                                f"{self._head_sha or 'unknown'}:{self._finalize_attempts}"
                            ),
                        },
                        task_queue=INTEGRATIONS_TASK_QUEUE,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=ACTIVITY_RETRY_POLICY,
                    )
                    if isinstance(verified, Mapping) and verified.get("merged") is True:
                        self._state = "completed"
                        return await self._publish_terminal(
                            status="merged",
                            reason_code="merged",
                            reason="Pull request merge was independently verified on GitHub.",
                            merge_sha=(
                                _text(verified.get("mergeSha"))
                                or (
                                    _text(finalize.get("mergeSha"))
                                    if isinstance(finalize, Mapping)
                                    else ""
                                )
                                or None
                            ),
                        )
                    await workflow.sleep(
                        timedelta(seconds=self._input.policy.poll_interval_seconds)
                    )
                    continue

                if self._classification in WAIT_CLASSIFICATIONS:
                    self._state = "durable_wait"
                    await workflow.sleep(
                        timedelta(seconds=self._input.policy.poll_interval_seconds)
                    )
                    continue

                skill = REMEDIATION_SKILLS.get(self._classification or "")
                if skill is not None:
                    if self._input.shadow_mode:
                        self._state = "shadow_complete"
                        return await self._publish_terminal(
                            status="manual_review",
                            reason_code=f"shadow_{self._classification}",
                            reason=f"Shadow resolver selected {skill}; child dispatch was disabled.",
                        )
                    if transition is None:
                        if progress_signature == self._last_progress_signature:
                            self._identical_no_progress_count += 1
                        else:
                            self._last_progress_signature = progress_signature
                            self._identical_no_progress_count = 0
                        if (
                            self._identical_no_progress_count
                            >= self._input.policy.max_identical_blockers_without_progress
                            or self._remediation_counts[skill]
                            >= self._input.policy.max_remediations_per_type
                        ):
                            self._state = "manual_review"
                            return await self._publish_terminal(
                                status="manual_review",
                                reason_code="repeated_blocker_without_progress",
                                reason=(
                                    "The same actionable blocker repeated without "
                                    "a remote head change."
                                ),
                            )
                        self._remediation_counts[skill] += 1
                    else:
                        self._remediation_counts = dict(
                            transition.state.remediation_counts
                        )
                        self._identical_no_progress_count = (
                            transition.state.identical_blocker_count
                        )
                        self._last_progress_signature = (
                            transition.state.last_progress_signature
                        )
                    request = self._remediation_request(skill=skill, snapshot=snapshot)
                    child_id = (
                        f"{workflow.info().workflow_id}:remediation:{skill}:"
                        f"{self._head_sha or 'unknown'}:{self._remediation_counts[skill]}"
                    )
                    self._active_remediation_child_id = child_id
                    self._state = f"run_{skill}"
                    self._record_timeline(
                        "remediation_started",
                        skill=skill,
                        childWorkflowId=child_id,
                    )
                    try:
                        child_result = await workflow.execute_child_workflow(
                            "MoonMind.AgentRun",
                            request,
                            id=child_id,
                            task_queue=settings.temporal.user_workflow_v2_task_queue,
                            cancellation_type=ChildWorkflowCancellationType.TRY_CANCEL,
                        )
                    finally:
                        self._active_remediation_child_id = None
                    self._record_timeline(
                        "remediation_completed",
                        skill=skill,
                        childWorkflowId=child_id,
                    )
                    failure = (
                        child_result.get("failureClass")
                        or child_result.get("failure_class")
                        if isinstance(child_result, Mapping)
                        else getattr(child_result, "failure_class", None)
                    )
                    if failure:
                        self._state = "failed"
                        return await self._publish_terminal(
                            status="failed",
                            reason_code="remediation_child_failed",
                            reason=f"Bounded {skill} remediation child failed.",
                        )
                    self._state = "verify_remote_head"
                    previous_head = self._head_sha
                    verified = await workflow.execute_activity(
                        "pr_resolver.verify_remote_head",
                        {
                            "repository": self._input.repository,
                            "prNumber": self._input.pr_number,
                            "prUrl": self._input.pr_url,
                            "headSha": previous_head or "",
                            "idempotencyKey": f"{child_id}:verify-head",
                        },
                        task_queue=INTEGRATIONS_TASK_QUEUE,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=ACTIVITY_RETRY_POLICY,
                    )
                    observed = (
                        _text(verified.get("headSha"))
                        if isinstance(verified, Mapping)
                        else ""
                    )
                    if observed and observed != previous_head:
                        self._head_sha = observed
                        self._last_progress_signature = None
                        self._identical_no_progress_count = 0
                    continue

                self._state = "manual_review"
                return await self._publish_terminal(
                    status="manual_review",
                    reason_code=decision["reasonCode"],
                    reason="PR resolver encountered a non-retryable or unknown blocker.",
                )
        except CancelledError:
            self._state = "canceled"
            raise
        except Exception:
            self._state = "failed"
            return await self._publish_terminal(
                status="failed",
                reason_code="hard_execution_failure",
                reason="PR resolver stopped after a non-retryable execution failure.",
            )
