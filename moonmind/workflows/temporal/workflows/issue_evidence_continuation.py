"""Delayed proof collection inside the existing UserWorkflow publication owner.

This is not another workflow or scheduler. Temporal history and the existing
remediation decision/claim remain the owners of identity, budgets, and recovery.
Only the evidence Activity reads external state; the same configured verifier
interprets downloaded bytes. CI conclusions never become acceptance here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.workflow import ChildWorkflowCancellationType

from moonmind.workflows.temporal.remediation_loop import (
    RemediationLoopPhase,
    apply_continuation_decision,
    decide_remediation_continuation,
    materialize_attempt_nodes,
    record_semantic_progress,
    record_verification_evidence,
    start_remediation_attempt,
)


def _semantic_gap_signature(owner: Any, outputs: Mapping[str, Any], parsed: Any) -> str:
    """Hash canonical unresolved obligations, excluding artifact/report churn."""
    import hashlib
    import json

    gaps = None
    for source in owner._moonspec_verify_sources(outputs):
        remaining = source.get("remainingWork")
        if isinstance(remaining, list):
            gaps = remaining
            break
    if gaps is None:
        gaps = parsed.issues
    normalized = []
    for item in gaps:
        if not isinstance(item, Mapping):
            continue
        ids = item.get("requirementIds")
        if not isinstance(ids, list):
            ids = [item.get("requirementId") or item.get("requirement")]
        ids = sorted({value for value in ids if isinstance(value, str) and value})
        if ids:
            normalized.append(
                {
                    "requirementIds": ids,
                    "gapType": item.get("gapType"),
                    "status": item.get("status"),
                }
            )
    canonical = sorted(
        json.dumps(gap, sort_keys=True, separators=(",", ":")) for gap in normalized
    ) or ["unstructured_unresolved_work"]
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(canonical, separators=(",", ":")).encode()
        ).hexdigest()
    )


async def _persist(owner: Any, record: dict[str, Any]) -> None:
    ref = await owner._write_json_artifact(
        name="reports/issue_evidence_continuation.json",
        payload=record,
    )
    if not ref:
        raise ValueError("Evidence continuation decision was not persisted")
    owner._publish_context["issueEvidenceContinuation"] = {**record, "decisionRef": ref}
    owner._update_memo()


async def _activity(owner: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from moonmind.workflows.temporal.workflows.run import DEFAULT_ACTIVITY_CATALOG

    route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("github_issue.collect_evidence")
    result = await workflow.execute_activity(
        "github_issue.collect_evidence",
        payload,
        **owner._execute_kwargs_for_route(route),
    )
    if not isinstance(result, Mapping):
        raise TypeError("Issue evidence reader returned no structured observation")
    return dict(result)


async def _verify(
    owner: Any,
    *,
    node: Mapping[str, Any],
    parameters: Mapping[str, Any],
    observation: Mapping[str, Any],
    ordinal: int,
    read_only: bool = True,
) -> dict[str, Any]:
    inputs = dict(node.get("inputs") or {})
    runtime = dict(inputs.get("runtime") or {})
    runtime["publishMode"] = "none" if read_only else "branch"
    inputs.update(
        runtime=runtime,
        publishMode=runtime["publishMode"],
        repositoryOperation="read" if read_only else "write",
    )
    node_id = f"{node['id']}:published-evidence:{ordinal}"
    inputs["instructions"] = str(inputs.get("instructions") or "") + (
        "\n\nReassess the same published candidate using the attached exact-head CI "
        "artifact manifest and actual artifact bytes. Read the original source and "
        "current required proof; previous BLOCKED classifications are historical. "
        "Do not modify code, rerun already-completed builds, submit duplicate CI, "
        "change requirements, publish, merge, or close the issue. CI status alone "
        "is not acceptance. Preserve actual failed samples and explicit live/rendered "
        "requirements. Bind all judgments to the supplied candidate and evidenceSetDigest."
    )
    if not read_only:
        inputs["instructions"] = str(
            (node.get("inputs") or {}).get("instructions") or ""
        ) + (
            "\nRepair only the current source-bound implementation defects in the attached "
            "verifier report. Continue the same PR branch and retain passed evidence. "
            "Do not create another PR, merge, close the issue, change requirements, "
            "or repeat unavailable infrastructure setup. Checkpoint and publish the "
            "candidate to its existing nonprotected branch for the owning verifier."
        )
    inputs["workspaceSpec"] = {
        **dict(inputs.get("workspaceSpec") or {}),
        "repository": owner._repo,
        "branch": owner._publish_context["branch"],
        "startingBranch": owner._publish_context["branch"],
        "targetBranch": owner._publish_context["branch"],
        "repositoryTarget": {
            "provider": "git",
            "repository": {"name": owner._repo},
            "branch": {"name": owner._publish_context["branch"]},
            "revision": {"kind": "git_commit", "commitSha": observation["headSha"]},
        },
    }
    skillset = await owner._resolve_agent_node_skillset_ref(
        task_skills=None,
        node_skills=node.get("skills"),
        node_inputs=inputs,
        node_id=node_id,
        existing_skillset_ref=owner._existing_agent_skillset_ref(
            parameters=parameters,
            node=node,
            node_inputs=inputs,
        ),
    )
    request = owner._build_agent_execution_request(
        node_inputs=inputs,
        node_id=node_id,
        node_annotations=owner._node_annotations_mapping(node),
        tool_name=str((node.get("tool") or {}).get("name") or ""),
        resolved_skillset_ref=skillset,
        workflow_parameters=parameters,
        attempt_reason="evidence_arrived",
    )
    refs = list(observation.get("evidenceRefs") or [])
    manifest = str(
        observation.get("manifestRef")
        or observation.get("evidenceManifestRef")
        or observation.get("artifactRef")
        or ""
    )
    if manifest:
        refs.insert(0, manifest)
    params = dict(request.parameters)
    params.update(
        evidenceManifestRef=manifest,
        evidenceSet=dict(observation.get("evidenceSet") or {}),
        evidenceSetDigest=observation.get("evidenceSetDigest"),
        acceptanceSubject=observation.get("subject"),
        publishMode="none" if read_only else "branch",
        repositoryOperation="read" if read_only else "write",
    )
    request = request.model_copy(
        update={
            "parameters": params,
            "input_refs": list(dict.fromkeys([*request.input_refs, *refs])),
        }
    )
    child_id = (
        f"{workflow.info().workflow_id}:issue-evidence:{ordinal}"
        if read_only
        else f"{workflow.info().workflow_id}:issue-evidence-repair:{ordinal}"
    )
    owner._active_agent_child_workflow_id = child_id
    owner._active_agent_id = request.agent_id
    try:
        result = await workflow.execute_child_workflow(
            "MoonMind.AgentRun",
            request,
            id=child_id,
            task_queue=owner._workflow_child_task_queue(),
            cancellation_type=ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED,
        )
    finally:
        owner._active_agent_child_workflow_id = None
        owner._active_agent_id = None
    mapped = await owner._hydrate_acceptance_result(owner._map_agent_run_result(result))
    if mapped.get("status") != "COMPLETED":
        raise ValueError(
            "Evidence-only verifier did not complete; preserved candidate remains unaccepted"
        )
    return dict(mapped.get("outputs") or {})


async def resume_published_issue_evidence(
    owner: Any, *, parameters: Mapping[str, Any], ordered_nodes: list[dict[str, Any]]
) -> None:
    context = owner._publish_context
    gate = context.get("moonSpecGate") or {}
    if (
        not owner._moonspec_draft_publication_reason
        or not owner._assessment_context
        or owner._publish_mode(parameters) == "none"
        or owner._cancel_requested
        or gate.get("recommendedNextAction") == "needs_human"
        or gate.get("verdict") == "FAILED_UNRECOVERABLE"
    ):
        return
    spec, state = owner._remediation_loop_spec, owner._remediation_loop_state
    node = next(
        (
            row
            for row in reversed(ordered_nodes)
            if owner._moonspec_step_role(row) == "moonspec-verification-gate"
        ),
        None,
    )
    # Without an admitted verifier and its existing budgets there is no new
    # authority to invent a verification lane or reset an exhausted attempt.
    if spec is None or state is None or node is None:
        return
    import re

    match = re.fullmatch(
        r"https://github\.com/([^/]+/[^/]+)/pull/([1-9][0-9]*)/?",
        str(context.get("pullRequestUrl") or ""),
    )
    head = str(context.get("headSha") or "")
    if not match or match[1].casefold() != str(owner._repo).casefold() or not head:
        return
    prior = dict(context.get("issueEvidenceContinuation") or {})
    started = prior.get("startedAt") or workflow.now().isoformat()
    # Existing loop wall-clock budget bounds external evidence waits too.
    from datetime import datetime

    budget_origin = (
        getattr(workflow.info(), "start_time", datetime.fromisoformat(started))
        if spec.budgets.max_wall_clock_seconds
        else datetime.fromisoformat(started)
    )
    deadline = budget_origin + timedelta(
        seconds=spec.budgets.max_wall_clock_seconds or 3600
    )
    record = {
        **prior,
        "repository": owner._repo,
        "prNumber": int(match[2]),
        "headSha": head,
        "startedAt": started,
        "ownerWorkflowId": workflow.info().workflow_id,
        "implementationAttempts": state.consumed_budgets.attempts,
        "evidenceRetries": state.consumed_budgets.evidence_retries,
    }
    payload = {
        "repository": owner._repo,
        "prNumber": int(match[2]),
        "headSha": head,
        "workflowId": workflow.info().workflow_id,
        "runId": workflow.info().run_id,
        "principal": owner._principal(),
    }

    async def run_under_claim() -> None:
        nonlocal state, gate, head, node
        while not owner._cancel_requested and workflow.now() < deadline:
            await owner._wait_if_paused_at_safe_boundary()
            if owner._cancel_requested:
                return
            observation = await _activity(owner, payload)
            status = observation.get("status")
            if (
                observation.get("headSha") != head
                or observation.get("observedHeadSha", head) != head
                or observation.get("pullRequestOpen") is False
                or status in {"moved", "closed", "cancelled", "held"}
            ):
                await _persist(
                    owner,
                    {
                        **record,
                        "status": "blocked",
                        "nextAction": "reconcile-candidate",
                        "summary": "Published candidate identity or authority changed",
                    },
                )
                return
            semantic = observation.get("semanticDigest") or observation.get(
                "evidenceSetDigest"
            )
            if (
                status != "ready"
                or not semantic
                or semantic == record.get("consumedEvidenceDigest")
            ):
                await _persist(
                    owner,
                    {
                        **record,
                        "status": "waiting",
                        "nextAction": "collect-evidence",
                        "runIds": observation.get("runIds", []),
                        "gaps": observation.get("gaps", []),
                    },
                )
                try:
                    await workflow.wait_condition(
                        lambda: owner._cancel_requested,
                        timeout=min(timedelta(seconds=60), deadline - workflow.now()),
                    )
                except asyncio.TimeoutError:
                    pass
                continue
            if (
                not record.get("verificationDueAfterImplementation")
                and state.consumed_budgets.evidence_retries
                >= spec.budgets.max_evidence_retries
            ):
                await _persist(
                    owner,
                    {
                        **record,
                        "status": "blocked",
                        "nextAction": "evidence-budget-exhausted",
                    },
                )
                return
            # Persist admission before dispatch. Temporal command history owns
            # duplicate-delivery replay; bytes arriving twice never dispatch twice.
            charge_evidence_judgment = not record.get(
                "verificationDueAfterImplementation"
            )
            evidence_retries = state.consumed_budgets.evidence_retries
            ordinal = int(record.get("verificationExecutions") or 0) + 1
            budgets = state.consumed_budgets
            state = state.model_copy(
                update={
                    "consumed_budgets": budgets,
                    "phase": RemediationLoopPhase.VERIFICATION_RUNNING,
                }
            )
            owner._remediation_loop_state = state
            record.update(
                consumedEvidenceDigest=semantic,
                evidenceRetries=evidence_retries,
                verificationExecutions=ordinal,
                verificationDueAfterImplementation=False,
                evidenceSetDigest=observation.get("evidenceSetDigest"),
                manifestRef=observation.get("manifestRef")
                or observation.get("evidenceManifestRef")
                or observation.get("artifactRef"),
                status="verifying",
                nextAction="verify",
                runIds=observation.get("runIds", []),
            )
            await _persist(owner, record)
            outputs = await _verify(
                owner,
                node=node,
                parameters=parameters,
                observation=observation,
                ordinal=ordinal,
            )
            parsed = owner._moonspec_verify_gate_result(outputs)
            while (
                (parsed.invalid or parsed.degraded)
                and state.consumed_budgets.contract_repairs
                < spec.budgets.max_contract_repairs
            ):
                # Malformed reporting consumes only its existing repair budget.
                # It cannot rerun implementation or replenish evidence retries.
                repaired_budgets = state.consumed_budgets.model_copy(
                    update={
                        "contract_repairs": state.consumed_budgets.contract_repairs + 1,
                    }
                )
                state = state.model_copy(update={"consumed_budgets": repaired_budgets})
                owner._remediation_loop_state = state
                ordinal += 1
                record.update(
                    verificationExecutions=ordinal,
                    contractRepairs=repaired_budgets.contract_repairs,
                    status="repairing-report",
                    nextAction="repair-report",
                )
                await _persist(owner, record)
                repair_node = dict(node)
                repair_inputs = dict(node.get("inputs") or {})
                repair_inputs["instructions"] = str(
                    repair_inputs.get("instructions") or ""
                ) + (
                    "\nRepair only the malformed structured report against the exact same supplied "
                    "candidate/evidence. Preserve genuine failures and explicit stops; do not "
                    "change code, rerun workload, or invent/drop requirements. Previous diagnostic: "
                    + str(parsed.downgrade_reason or "Malformed gate contract")
                )
                repair_node["inputs"] = repair_inputs
                outputs = await _verify(
                    owner,
                    node=repair_node,
                    parameters=parameters,
                    observation=observation,
                    ordinal=ordinal,
                )
                parsed = owner._moonspec_verify_gate_result(outputs)
            provisional = bool(
                parsed.verdict in {"NO_DETERMINATION", "BLOCKED"}
                and not parsed.invalid
                and not parsed.degraded
                and parsed.recommended_next_action != "needs_human"
                and observation.get("collectionComplete") is False
                and (observation.get("pendingRunIds") or observation.get("gaps"))
            )
            # A partial observation is not a terminal evidence judgment. Record
            # every dispatch, but do not spend the one-shot terminal proof budget
            # merely because the required job has not delivered its bytes yet.
            # No counters are decremented or reset; cosmetic evidence never
            # reaches this branch and the owner's wall clock/claim still bound it.
            if charge_evidence_judgment and not provisional:
                budgets = state.consumed_budgets.model_copy(
                    update={
                        "evidence_retries": state.consumed_budgets.evidence_retries + 1,
                    }
                )
                state = state.model_copy(update={"consumed_budgets": budgets})
                owner._remediation_loop_state = state
            record.update(
                evidenceRetries=state.consumed_budgets.evidence_retries,
                verificationDueAfterImplementation=bool(
                    provisional and not charge_evidence_judgment
                ),
            )
            projection = (
                dict(parsed.validated_refs or {}).get("acceptanceProjection") or {}
            )
            gate_ref = owner._bounded_story_loop_artifact_ref(
                projection.get("reportRef")
                or outputs.get("sourceMoonSpecVerifyArtifactRef")
                or owner._moonspec_verify_gate_result_ref(outputs)
            )
            if not gate_ref:
                raise ValueError(
                    "Evidence-only verifier did not publish its durable gate"
                )
            binding = dict(parsed.validated_refs or {}).get("acceptance") or {}
            if parsed.verdict == "FULLY_IMPLEMENTED" and (
                binding.get("subject") != observation.get("subject")
                or binding.get("evidenceSetDigest")
                != observation.get("evidenceSetDigest")
            ):
                await _persist(
                    owner,
                    {
                        **record,
                        "status": "blocked",
                        "nextAction": "repair-report",
                        "summary": "Verifier did not bind the actual current candidate and downloaded evidence",
                    },
                )
                return
            await owner._wait_if_paused_at_safe_boundary()
            if owner._cancel_requested:
                return
            fresh = await _activity(owner, {**payload, "action": "verify_candidate"})
            if (
                fresh.get("headSha") != head
                or fresh.get("observedHeadSha", head) != head
                or fresh.get("pullRequestOpen") is False
                or fresh.get("status") not in {"ready", "current"}
            ):
                await _persist(
                    owner,
                    {
                        **record,
                        "status": "blocked",
                        "nextAction": "reconcile-candidate",
                    },
                )
                return
            owner._record_moonspec_verify_gate(
                node_id=f"{node['id']}:published-evidence:{ordinal}", outputs=outputs
            )
            if parsed.verdict == "ADDITIONAL_WORK_NEEDED":
                signature = _semantic_gap_signature(owner, outputs, parsed)
                state = record_semantic_progress(
                    state, progress_ref=gate_ref, progress_signature=signature
                )
            state = record_verification_evidence(state, verification_ref=gate_ref)
            decision = decide_remediation_continuation(
                spec=spec,
                state=state,
                verdict=parsed.verdict,
                gate_result_ref=gate_ref,
                remaining_work_ref=owner._bounded_story_loop_artifact_ref(
                    parsed.remaining_work_ref
                ),
                recoverable_evidence=False,
                recommended_next_action=parsed.recommended_next_action,
            )
            decision_ref = await owner._write_json_artifact(
                name="reports/issue_evidence_gate_decision.json",
                payload=decision.model_dump(by_alias=True, mode="json"),
            )
            state = apply_continuation_decision(
                state,
                decision=decision,
                decision_ref=owner._bounded_story_loop_artifact_ref(decision_ref),
            )
            owner._remediation_loop_state = state
            owner._sync_remediation_loop_projection()
            record.update(
                status=(
                    "accepted" if parsed.verdict == "FULLY_IMPLEMENTED" else "blocked"
                ),
                nextAction=(
                    "review"
                    if parsed.verdict == "FULLY_IMPLEMENTED"
                    else (
                        "continue-implementation"
                        if decision.continue_loop
                        else "collect-evidence"
                    )
                ),
                gateResultRef=gate_ref,
                verdict=parsed.verdict,
            )
            await _persist(owner, record)
            if parsed.verdict != "FULLY_IMPLEMENTED":
                if provisional:
                    # A partial delivery is not terminal. Preserve this judgment
                    # and wait for different actual bytes; the next dispatch
                    # still checks the existing evidence budget above.
                    record.update(status="waiting", nextAction="collect-evidence")
                    await _persist(owner, record)
                    continue
                if not (
                    decision.continue_loop
                    and state.phase == RemediationLoopPhase.REMEDIATION_PENDING
                ):
                    return
                # The existing loop owns code-repair authority and budgets; a
                # genuine verifier finding enters its admitted same-PR pair.
                state = start_remediation_attempt(state)
                owner._remediation_loop_state = state
                repair, node = materialize_attempt_nodes(
                    spec=spec,
                    ordinal=state.attempt_ordinal,
                    workspace_head_ref=state.workspace_head_ref,
                    runtime=owner._remediation_loop_runtime_block(),
                    remediation_inputs={
                        "gateResultRef": gate_ref,
                        "remainingWorkRef": gate_ref,
                    },
                )
                record.update(
                    status="implementing",
                    nextAction="continue-implementation",
                    implementationAttempts=state.consumed_budgets.attempts,
                )
                await _persist(owner, record)
                repair_observation = {
                    **observation,
                    "evidenceRefs": [gate_ref, *observation.get("evidenceRefs", [])],
                }
                repaired = await _verify(
                    owner,
                    node=repair,
                    parameters=parameters,
                    observation=repair_observation,
                    ordinal=state.attempt_ordinal,
                    read_only=False,
                )
                await owner._record_publish_result_from_execution(
                    parameters={**parameters, "publishMode": "branch"},
                    execution_result={"status": "COMPLETED", "outputs": repaired},
                )
                new_head = owner._workflow_verified_published_head()
                if (
                    not new_head
                    or new_head[0] != context.get("branch")
                    or new_head[1] == head
                ):
                    await _persist(
                        owner,
                        {
                            **record,
                            "status": "blocked",
                            "nextAction": "reconcile-candidate",
                            "summary": "Implementation repair did not confirm a new candidate on the same PR branch",
                        },
                    )
                    return
                head = new_head[1]
                payload["headSha"] = head
                record.update(
                    headSha=head,
                    verificationDueAfterImplementation=True,
                    consumedEvidenceDigest=None,
                    status="waiting",
                    nextAction="collect-evidence",
                )
                await _persist(owner, record)
                continue
            ready = await _activity(
                owner,
                {
                    **payload,
                    "action": "mark_ready",
                    "automationOwnedDraft": bool(
                        context.get("moonSpecDraftPublication")
                    ),
                    "userHold": owner._paused or owner._cancel_requested,
                    "gateResultRef": gate_ref,
                    "reportDigest": (
                        dict(parsed.validated_refs or {}).get("acceptanceProjection")
                        or {}
                    ).get("reportDigest"),
                    "evidenceSetDigest": observation.get("evidenceSetDigest"),
                },
            )
            if (
                ready.get("status") != "ready"
                or ready.get("headSha") != head
                or ready.get("observedHeadSha", head) != head
            ):
                await _persist(
                    owner,
                    {
                        **record,
                        "status": "blocked",
                        "nextAction": "confirm-publication",
                    },
                )
                return
            owner._moonspec_draft_publication_reason = None
            owner._plan_blocked_message = None
            owner._attention_required = False
            context.pop("publicationBlockedBy", None)
            context.pop("moonSpecDraftPublication", None)
            await _persist(
                owner, {**record, "status": "accepted", "nextAction": "review"}
            )
            return
        if not owner._cancel_requested:
            await _persist(
                owner,
                {
                    **record,
                    "status": "blocked",
                    "nextAction": "collect-evidence",
                    "summary": "Existing evidence wait budget expired; candidate and proof remain preserved",
                },
            )

    lease = (owner._trusted_issue_context or {}).get("issueClaimLease")
    if lease:
        from moonmind.workflows.temporal.github_issue_lease_workflow import (
            execute_with_issue_lease,
        )
        from moonmind.workflows.temporal.workflows.run import DEFAULT_ACTIVITY_CATALOG

        async def renew(value):
            route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                "github_issue.renew_claim"
            )
            return await workflow.execute_activity(
                "github_issue.renew_claim",
                value,
                **owner._execute_kwargs_for_route(route),
            )

        await execute_with_issue_lease(
            lease=lease, execute=run_under_claim, renew=renew
        )
    else:
        await run_under_claim()
