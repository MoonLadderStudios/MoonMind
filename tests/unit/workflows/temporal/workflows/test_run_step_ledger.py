from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.schemas.resilience_policy_models import compile_resilience_policy
from moonmind.schemas.temporal_models import (
    STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
    STEP_EXECUTION_MANIFEST_CONTENT_TYPE,
    StepExecutionIdentityModel,
)
from moonmind.workflows.executions.prepared_context import (
    build_durable_retrieval_manifest_artifact,
)
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import (
    GateTransitionDecision,
    MoonMindRunWorkflow,
)

def _configure_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-run-1",
        run_id="run-1",
        task_queue="mm.workflow",
        search_attributes={"mm_owner_type": ["user"], "mm_owner_id": ["user-1"]},
    )
    logger = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        isEnabledFor=lambda *_args, **_kwargs: False,
    )
    memo_updates: list[dict] = []
    search_updates: list[object] = []
    monkeypatch.setattr(run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_module.workflow, "logger", logger)
    monkeypatch.setattr(
        run_module.workflow,
        "now",
        lambda: datetime(2026, 4, 7, 12, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(run_module.workflow, "upsert_memo", memo_updates.append)
    monkeypatch.setattr(
        run_module.workflow,
        "upsert_search_attributes",
        search_updates.append,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id == run_module.RUN_CANONICAL_STEP_STATUS_VOCAB_PATCH,
    )
    return memo_updates


def test_pre_cutover_review_retry_ignores_plan_routed_transition() -> None:
    transition = GateTransitionDecision(
        disposition="accept",
        routing_disposition="stop_at_control_gate",
        reason_code="no_remediation_successor",
    )

    assert MoonMindRunWorkflow._gate_transition_allows_review_retry(
        plan_routed_moonspec_remediation_enabled=False,
        transition=transition,
    )
    assert not MoonMindRunWorkflow._gate_transition_allows_review_retry(
        plan_routed_moonspec_remediation_enabled=True,
        transition=transition,
    )

def _ordered_nodes() -> list[dict]:
    return [
        {
            "id": "prepare",
            "tool": {"type": "agent_runtime", "name": "codex_cli"},
            "inputs": {"title": "Prepare workspace"},
        },
        {
            "id": "run-tests",
            "tool": {"type": "agent_runtime", "name": "codex_cli"},
            "inputs": {"title": "Run tests"},
        },
    ]

def _dependency_map() -> dict[str, list[str]]:
    return {"prepare": [], "run-tests": ["prepare"]}


def _checkpoint_create_result(payload: Any) -> dict[str, Any]:
    boundary = str(payload.get("boundary") or "unknown")
    checkpoint_id = str(payload.get("idempotencyKey") or f"checkpoint:{boundary}")
    workspace = payload.get("workspace")
    workspace_kind = (
        workspace.get("kind")
        if isinstance(workspace, dict)
        else "ephemeral_workspace_ref"
    )
    return {
        "checkpointRef": f"artifact://checkpoint/{boundary}",
        "checkpointId": checkpoint_id,
        "contentType": STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
        "workspaceKind": workspace_kind,
        "diagnosticRefs": [],
        "idempotencyKey": checkpoint_id,
    }


def _managed_checkpoint_capture_result(payload: Any) -> dict[str, Any]:
    return {
        "status": "captured",
        "workspace": {
            "kind": "worktree_archive",
            "baseCommit": "abc123",
            "archiveRef": "artifact://managed/archive",
            "archiveDigest": "sha256:" + ("a" * 64),
            "manifestRef": "artifact://managed/manifest",
            "manifestDigest": "sha256:" + ("b" * 64),
            "includesUntracked": True,
            "includesIgnoredFiles": False,
        },
        "diagnosticRefs": ["artifact://managed/manifest"],
        "idempotencyKey": payload["idempotencyKey"],
    }


def _resilience_policy_compile_result(payload: Any) -> dict[str, Any]:
    return compile_resilience_policy(
        compiled_at=datetime.fromisoformat(payload["compiledAt"]),
        workflow_id=payload["workflowId"],
        run_id=payload["runId"],
        policy_version=payload.get("policyVersion", 1),
        attempts={
            "stepMaxAttempts": 3,
            "stepNoProgressLimit": 2,
            "jobSelfHealMaxResets": 1,
        },
        timeouts={"stepTimeoutSeconds": 900, "stepIdleTimeoutSeconds": 300},
        provider_cooldown={
            "cooldownAfter429Seconds": payload.get("cooldownAfter429Seconds", 900),
            "providerProfileId": payload.get("providerProfileId"),
            "rateLimitPolicy": payload.get("rateLimitPolicy", {}),
        },
        checkpoints={
            "checkpointRequired": True,
            "requiredBoundaries": [
                "after_prepare",
                "before_execution",
                "after_execution",
            ],
        },
        idempotency={
            "sideEffectIdempotencyRequired": True,
            "keyStrategy": "step_execution_operation",
        },
        outbound_scanning={"highSecurityMode": False, "blockOnFinding": False},
        observability={
            "liveLogsTimelineEnabled": False,
            "structuredHistoryEnabled": True,
        },
        cost_attribution={
            "runtimeId": payload.get("runtimeId"),
            "model": payload.get("model"),
            "effort": payload.get("effort"),
        },
    ).model_dump(by_alias=True, mode="json")


def _resilience_policy_artifact_create_result() -> tuple[
    dict[str, str],
    dict[str, str],
]:
    return ({"artifact_id": "art_resilience_policy"}, {"upload_url": "unused"})


def _approval_policy_plan_payload() -> dict[str, Any]:
    return {
        "plan_version": "1.0",
        "metadata": {
            "title": "Approval policy plan",
            "created_at": "2026-04-08T00:00:00Z",
            "registry_snapshot": {
                "digest": "reg:sha256:" + ("a" * 64),
                "artifact_ref": "artifact://registry/1",
            },
        },
        "policy": {
            "failure_mode": "FAIL_FAST",
            "max_concurrency": 1,
            "approval_policy": {
                "enabled": True,
                "max_review_attempts": 1,
                "reviewer_model": "default",
                "review_timeout_seconds": 120,
                "skip_tool_types": [],
            },
        },
        "nodes": [
            {
                "id": "apply-patch",
                "tool": {
                    "type": "agent_runtime",
                    "name": "codex_cli",
                },
                "inputs": {"instructions": "Apply the patch"},
                "options": {},
            }
        ],
        "edges": [],
    }


def test_run_exposes_one_canonical_step_execution_manifest_record_surface() -> None:
    workflow_attrs = {
        name
        for name in dir(MoonMindRunWorkflow)
        if name.startswith("_record_step_execution_manifest")
    }

    assert workflow_attrs == {"_record_step_execution_manifest"}
    assert run_module.RUN_STEP_EXECUTION_MANIFEST_PATCH == (
        "run-step-attempt-manifest-v1"
    )


def test_step_execution_manifest_start_write_keeps_replay_patch_guard() -> None:
    source = Path(run_module.__file__).read_text()

    guard_assignment = (
        "step_execution_manifest_enabled = workflow.patched(\n"
        "                        RUN_STEP_EXECUTION_MANIFEST_PATCH\n"
        "                    )"
    )
    non_agent_guard = (
        'tool_type != "agent_runtime"\n'
        "                        and step_execution_manifest_enabled"
    )
    start_manifest_call = (
        "await self._record_step_execution_manifest(\n"
        "                            node_id,\n"
        '                            phase="start",'
    )

    assert source.index(guard_assignment) < source.index(non_agent_guard)
    assert source.index(non_agent_guard) < source.index(start_manifest_call)


@pytest.mark.asyncio
async def test_run_compiles_and_records_resilience_policy_before_step_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memo_updates = _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._target_runtime = "codex_cli"
    workflow._profile_snapshots = {
        "prof-1": {
            "profile_id": "prof-1",
            "runtime_id": "codex_cli",
            "status": "ready",
            "cooldownAfter429Seconds": 321,
            "rateLimitPolicy": {"strategy": "slot_cooldown"},
        }
    }
    activity_calls: list[dict[str, Any]] = []
    artifact_writes: list[dict[str, Any]] = []

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any):
        assert activity_type == "resilience.compile_policy"
        activity_calls.append(dict(payload))
        return compile_resilience_policy(
            compiled_at=datetime.fromisoformat(payload["compiledAt"]),
            workflow_id=payload["workflowId"],
            run_id=payload["runId"],
            policy_version=payload["policyVersion"],
            attempts={
                "stepMaxAttempts": 3,
                "stepNoProgressLimit": 2,
                "jobSelfHealMaxResets": 1,
            },
            timeouts={"stepTimeoutSeconds": 900, "stepIdleTimeoutSeconds": 300},
            provider_cooldown={
                "cooldownAfter429Seconds": payload["cooldownAfter429Seconds"],
                "providerProfileId": payload["providerProfileId"],
                "rateLimitPolicy": payload["rateLimitPolicy"],
            },
            checkpoints={
                "checkpointRequired": True,
                "requiredBoundaries": [
                    "after_prepare",
                    "before_execution",
                    "after_execution",
                ],
            },
            idempotency={
                "sideEffectIdempotencyRequired": True,
                "keyStrategy": "step_execution_operation",
            },
            outbound_scanning={"highSecurityMode": False, "blockOnFinding": False},
            observability={
                "liveLogsTimelineEnabled": False,
                "structuredHistoryEnabled": True,
            },
            cost_attribution={
                "runtimeId": payload["runtimeId"],
                "model": payload["model"],
                "effort": payload["effort"],
            },
        ).model_dump(by_alias=True, mode="json")

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        artifact_writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return "artifact://resilience/policy"

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)

    await workflow._compile_and_record_resilience_policy(
        parameters={
            "executionProfileRef": "prof-1",
            "model": "gpt-5",
            "effort": "high",
        }
    )

    assert activity_calls == [
        {
            "workflowId": "wf-run-1",
            "runId": "run-1",
            "policyVersion": 1,
            "compiledAt": "2026-04-07T12:00:00+00:00",
            "runtimeId": "codex_cli",
            "providerProfileId": "prof-1",
            "cooldownAfter429Seconds": 321,
            "rateLimitPolicy": {"strategy": "slot_cooldown"},
            "model": "gpt-5",
            "effort": "high",
        }
    ]
    assert artifact_writes[0]["name"] == "reports/resilience_policy.json"
    assert artifact_writes[0]["metadata_json"]["artifact_kind"] == (
        "resilience_policy_envelope"
    )
    assert artifact_writes[0]["payload"]["providerCooldown"][
        "cooldownAfter429Seconds"
    ] == 321
    assert artifact_writes[0]["payload"]["providerCooldown"]["rateLimitPolicy"] == {
        "strategy": "slot_cooldown"
    }
    assert workflow._resilience_policy_ref == {
        "policyId": artifact_writes[0]["payload"]["policyId"],
        "policyVersion": 1,
        "digest": artifact_writes[0]["payload"]["digest"],
        "contentType": run_module.RESILIENCE_POLICY_CONTENT_TYPE,
        "envelopeRef": "artifact://resilience/policy",
    }
    assert memo_updates[-1]["resilience_policy_ref"] == workflow._resilience_policy_ref


@pytest.mark.asyncio
async def test_resilience_policy_preserves_string_rate_limit_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # provider_profile.list serializes a DB-backed profile's rate-limit policy as
    # a bare strategy string (e.g. "backoff"), not a mapping. The compiled policy
    # must preserve it instead of dropping it to an empty mapping.
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._target_runtime = "codex_cli"
    workflow._profile_snapshots = {
        "prof-db": {
            "profile_id": "prof-db",
            "runtime_id": "codex_cli",
            "cooldown_after_429_seconds": 450,
            "rate_limit_policy": "backoff",
        }
    }
    activity_calls: list[dict[str, Any]] = []
    artifact_writes: list[dict[str, Any]] = []

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any):
        assert activity_type == "resilience.compile_policy"
        activity_calls.append(dict(payload))
        return _resilience_policy_compile_result(payload)

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        artifact_writes.append({"name": name, "payload": payload})
        return "artifact://resilience/policy"

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)

    await workflow._compile_and_record_resilience_policy(
        parameters={"executionProfileRef": "prof-db"}
    )

    assert activity_calls[0]["cooldownAfter429Seconds"] == 450
    assert activity_calls[0]["rateLimitPolicy"] == {"strategy": "backoff"}
    assert artifact_writes[0]["payload"]["providerCooldown"]["rateLimitPolicy"] == {
        "strategy": "backoff"
    }


@pytest.mark.asyncio
async def test_step_resilience_policy_ref_compiled_for_node_level_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A plan node that resolves to a provider profile different from the
    # run-level inherited profile must reference a policy compiled with that
    # node's cooldown/rate-limit values, not the run-level policy.
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id == run_module.RUN_STEP_RESILIENCE_POLICY_PATCH,
    )
    workflow = MoonMindRunWorkflow()
    workflow._target_runtime = "codex_cli"
    workflow._run_resilience_profile_id = "prof-run"
    run_ref = {
        "policyId": "resilience-policy-run",
        "policyVersion": 1,
        "digest": "run-digest",
        "contentType": run_module.RESILIENCE_POLICY_CONTENT_TYPE,
        "envelopeRef": "artifact://resilience/run",
    }
    workflow._resilience_policy_ref = run_ref
    workflow._resilience_policy_refs_by_profile = {"prof-run": run_ref}
    workflow._profile_snapshots = {
        "prof-run": {
            "profile_id": "prof-run",
            "runtime_id": "codex_cli",
            "cooldownAfter429Seconds": 100,
            "rateLimitPolicy": {"strategy": "queue"},
        },
        "prof-node": {
            "profile_id": "prof-node",
            "runtime_id": "codex_cli",
            "cooldown_after_429_seconds": 777,
            "rate_limit_policy": "backoff",
        },
    }
    activity_calls: list[dict[str, Any]] = []
    artifact_writes: list[dict[str, Any]] = []

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any):
        assert activity_type == "resilience.compile_policy"
        activity_calls.append(dict(payload))
        return _resilience_policy_compile_result(payload)

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        artifact_writes.append({"name": name, "payload": payload})
        return f"artifact://resilience/{len(artifact_writes)}"

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)

    await workflow._resolve_step_resilience_policy_ref(
        node_id="delegate-agent",
        execution_profile_ref="prof-node",
        parameters={"model": "gpt-5"},
    )

    assert activity_calls[0]["providerProfileId"] == "prof-node"
    assert activity_calls[0]["cooldownAfter429Seconds"] == 777
    assert activity_calls[0]["rateLimitPolicy"] == {"strategy": "backoff"}
    step_ref = workflow._step_resilience_policy_refs["delegate-agent"]
    assert step_ref != run_ref
    assert step_ref["envelopeRef"] == "artifact://resilience/1"
    assert workflow._resilience_policy_refs_by_profile["prof-node"] == step_ref
    assert artifact_writes[0]["name"] == "reports/resilience_policy_prof-node.json"
    assert (
        artifact_writes[0]["payload"]["providerCooldown"]["providerProfileId"]
        == "prof-node"
    )

    # A node that inherits the run-level profile keeps the run-level policy and
    # does not trigger another compilation.
    await workflow._resolve_step_resilience_policy_ref(
        node_id="inherits-run",
        execution_profile_ref="prof-run",
        parameters={"model": "gpt-5"},
    )
    assert "inherits-run" not in workflow._step_resilience_policy_refs
    assert len(activity_calls) == 1


@pytest.mark.asyncio
async def test_step_execution_manifest_prefers_per_step_resilience_policy_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    run_ref = {
        "policyId": "resilience-policy-run",
        "policyVersion": 1,
        "digest": "run-digest",
        "contentType": run_module.RESILIENCE_POLICY_CONTENT_TYPE,
        "envelopeRef": "artifact://resilience/run",
    }
    step_ref = {
        "policyId": "resilience-policy-node",
        "policyVersion": 1,
        "digest": "node-digest",
        "contentType": run_module.RESILIENCE_POLICY_CONTENT_TYPE,
        "envelopeRef": "artifact://resilience/node",
    }
    workflow._resilience_policy_ref = run_ref
    workflow._step_resilience_policy_refs = {"delegate-agent": step_ref}
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append({"name": name, "payload": payload})
        return "artifact://step/manifest"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "delegate-agent",
                "tool": {"type": "agent_runtime", "name": "codex"},
                "inputs": {"title": "Delegate agent"},
            },
            {
                "id": "plain-step",
                "tool": {"type": "agent_runtime", "name": "codex"},
                "inputs": {"title": "Plain step"},
            },
        ],
        dependency_map={"delegate-agent": [], "plain-step": []},
        updated_at=now,
    )
    workflow._mark_step_running("delegate-agent", updated_at=now, summary="run")
    workflow._mark_step_running("plain-step", updated_at=now, summary="run")

    await workflow._record_step_execution_manifest(
        "delegate-agent",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )
    await workflow._record_step_execution_manifest(
        "plain-step",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )

    # The overriding node references its per-step policy; the other falls back
    # to the run-level policy.
    assert writes[0]["payload"]["execution"]["resiliencePolicyRef"] == step_ref
    assert writes[1]["payload"]["execution"]["resiliencePolicyRef"] == run_ref


@pytest.mark.asyncio
async def test_start_manifest_uses_launch_context_projection_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)
    workflow = MoonMindRunWorkflow()
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"artifact-write-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "collect-evidence",
                "tool": {"type": "agent_runtime", "name": "codex_cli"},
                "inputs": {"title": "Collect evidence"},
            }
        ],
        dependency_map={"collect-evidence": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "collect-evidence",
        updated_at=now,
        summary="Launching child runtime",
    )
    request = workflow._build_agent_execution_request(
        node_inputs={"runtime": {"mode": "codex_cli"}},
        node_id="collect-evidence",
        tool_name="codex_cli",
        workflow_parameters={
            "task": {
                "retrieval": {
                    "query": "step context bundle",
                    "returnedRefs": ["artifact://retrieved-doc"],
                },
                "memoryProposals": [
                    {
                        "proposalRef": "memory://proposal-1",
                        "state": "proposed",
                    }
                ],
                "steps": [{"id": "collect-evidence"}],
            }
        },
    )
    expected_context = request.parameters["metadata"]["moonmind"][
        "stepExecutionManifestProjection"
    ]["context"]

    await workflow._record_step_execution_manifest(
        "collect-evidence",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )

    assert writes[0]["name"] == (
        "reports/retrieval_manifests/collect-evidence_attempt_1.json"
    )
    assert writes[0]["payload"]["status"] == "captured"
    assert writes[0]["payload"]["retrievalManifestDigest"].startswith("sha256:")
    assert writes[0]["metadata_json"]["artifact_kind"] == "retrieval_manifest"
    assert writes[0]["metadata_json"]["retrievalStatus"] == "captured"
    assert writes[1]["name"] == (
        "reports/step_executions/collect-evidence_attempt_1.json"
    )
    expected_context = dict(expected_context)
    expected_context["retrievalManifestRef"] = "artifact-write-1"
    assert writes[1]["payload"]["context"] == expected_context
    assert writes[1]["payload"]["context"]["retrievalManifestRef"] == (
        "artifact-write-1"
    )
    assert writes[1]["payload"]["context"]["memoryManifestRef"].startswith(
        "attempt-memory-manifest://sha256:"
    )
    assert writes[1]["payload"]["context"] != {}


@pytest.mark.asyncio
async def test_checkpoint_branch_turn_manifest_persists_branch_artifact_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_module.RUN_CANONICAL_STEP_STATUS_VOCAB_PATCH,
            run_module.RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH,
        },
    )
    workflow = MoonMindRunWorkflow()
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"artifact-write-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "branch-implement",
                "tool": {"type": "agent_runtime", "name": "codex_cli"},
                "inputs": {"title": "Implement on checkpoint branch"},
            }
        ],
        dependency_map={"branch-implement": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "branch-implement",
        updated_at=now,
        summary="Launching branch turn",
    )
    branch_turn = {
        "branchId": "branch-1",
        "branchTurnId": "turn-1",
        "sourceWorkflowId": "source-wf",
        "sourceRunId": "source-run",
        "sourceLogicalStepId": "source-step",
        "sourceCheckpointRef": "artifact://checkpoint/source",
        "sourceCheckpointDigest": "sha256:" + "a" * 64,
        "instructionArtifactRef": "artifact://instructions/turn-1",
        "instructionDigest": "sha256:" + "b" * 64,
        "workspacePolicy": "fresh_branch_from_source",
        "runtimeContextPolicy": "fresh_agent_run",
        "gitWorkBranch": "mm/branch-1",
    }
    request = workflow._build_agent_execution_request(
        node_inputs={
            "runtime": {
                "mode": "codex_cli",
                "metadata": {"moonmind": {"checkpointBranchTurn": branch_turn}},
            },
            "instructionRef": "artifact://instructions/turn-1",
        },
        node_id="branch-implement",
        tool_name="codex_cli",
        workflow_parameters={"task": {"steps": [{"id": "branch-implement"}]}},
    )

    await workflow._record_step_execution_manifest(
        "branch-implement",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )

    assert writes[0]["name"] == (
        "reports/checkpoint_branches/branch-1/turns/turn-1/artifact_manifest.json"
    )
    assert writes[0]["metadata_json"]["artifact_kind"] == (
        "checkpoint_branch_turn_artifact_manifest"
    )
    assert {
        artifact["name"] for artifact in writes[0]["payload"]["artifacts"]
    } >= {
        "input.branch_turn.instructions.md",
        "runtime.branch_turn.agent_request.json",
        "output.branch_turn.step_execution_manifest.json",
        "output.branch_turn.diagnostics.json",
    }
    assert writes[1]["name"] == (
        "reports/step_executions/branch-implement_attempt_1.json"
    )
    manifest_payload = writes[1]["payload"]
    assert manifest_payload["reason"] == "checkpoint_branch"
    assert manifest_payload["branch"] == {
        "branchId": "branch-1",
        "branchTurnId": "turn-1",
        "rootCheckpointRef": "artifact://checkpoint/source",
        "gitWorkBranch": "mm/branch-1",
    }
    assert manifest_payload["context"]["builderVersion"] == (
        "branch-turn-context-builder-v1"
    )
    assert manifest_payload["context"]["branchArtifactManifestRef"] == (
        "artifact-write-1"
    )
    assert manifest_payload["execution"]["checkpointBranchTurn"][
        "artifactManifestRef"
    ] == "artifact-write-1"

    refreshed_request = await workflow._request_with_persisted_retrieval_ref(
        request,
        logical_step_id="branch-implement",
        attempt=1,
    )
    refreshed_metadata = refreshed_request.parameters["metadata"]["moonmind"]
    assert refreshed_metadata["checkpointBranchTurn"]["artifactManifestRef"] == (
        "artifact-write-1"
    )
    assert refreshed_metadata["stepExecutionManifestProjection"]["context"][
        "branchArtifactManifestRef"
    ] == "artifact-write-1"


@pytest.mark.asyncio
async def test_checkpoint_branch_turn_refresh_repersists_rebuilt_branch_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id == run_module.RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH,
    )
    workflow = MoonMindRunWorkflow()
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"art_branch_manifest_rebuilt_{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    branch_turn = {
        "branchId": "branch-1",
        "branchTurnId": "turn-1",
        "sourceWorkflowId": "source-wf",
        "sourceRunId": "source-run",
        "sourceLogicalStepId": "source-step",
        "sourceCheckpointRef": "artifact://checkpoint/source",
        "sourceCheckpointDigest": "sha256:" + "a" * 64,
        "instructionArtifactRef": "artifact://instructions/turn-1",
        "instructionDigest": "sha256:" + "b" * 64,
        "workspacePolicy": "fresh_branch_from_source",
        "runtimeContextPolicy": "fresh_agent_run",
    }
    request = workflow._build_agent_execution_request(
        node_inputs={
            "runtime": {
                "mode": "codex_cli",
                "metadata": {"moonmind": {"checkpointBranchTurn": branch_turn}},
            }
        },
        node_id="branch-implement",
        tool_name="codex_cli",
        workflow_parameters={
            "task": {
                "retrieval": {
                    "query": "branch context",
                    "returnedRefs": ["artifact://doc"],
                },
                "steps": [{"id": "branch-implement"}],
            }
        },
    )
    workflow._step_execution_retrieval_manifest_artifacts[
        ("branch-implement", 1)
    ]["persistedArtifactRef"] = "art_retrieval"
    workflow._step_execution_branch_artifact_manifests[
        ("branch-implement", 1)
    ]["persistedArtifactRef"] = "art_branch_manifest"

    refreshed = await workflow._request_with_persisted_retrieval_ref(
        request,
        logical_step_id="branch-implement",
        attempt=1,
    )

    moonmind_metadata = refreshed.parameters["metadata"]["moonmind"]
    execution_context = moonmind_metadata["executionContext"]
    branch_turn_metadata = moonmind_metadata["checkpointBranchTurn"]
    artifact_manifest = moonmind_metadata["checkpointBranchTurnArtifactManifest"]
    assert execution_context["retrievalManifestRef"] == "art_retrieval"
    assert branch_turn_metadata["contextBundleRef"] == execution_context[
        "contextBundleRef"
    ]
    assert branch_turn_metadata["contextBundleDigest"] == execution_context[
        "contextBundleDigest"
    ]
    assert artifact_manifest["contextBundleRef"] == execution_context[
        "contextBundleRef"
    ]
    assert artifact_manifest["contextBundleDigest"] == execution_context[
        "contextBundleDigest"
    ]
    assert branch_turn_metadata["artifactManifestDigest"] == artifact_manifest[
        "artifactManifestDigest"
    ]
    assert branch_turn_metadata["artifactManifestRef"] == (
        "art_branch_manifest_rebuilt_1"
    )
    assert artifact_manifest["persistedArtifactRef"] == "art_branch_manifest_rebuilt_1"
    assert writes[0]["name"] == (
        "reports/checkpoint_branches/branch-1/turns/turn-1/artifact_manifest.json"
    )
    assert writes[0]["payload"]["contextBundleRef"] == execution_context[
        "contextBundleRef"
    ]


@pytest.mark.asyncio
async def test_retrieval_manifest_persistence_writes_status_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"art_retrieval_{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    retrievals = [
        ("captured", {"query": "step context", "returnedRefs": ["artifact://doc"]}),
        ("skipped", {}),
        ("unavailable", {"selector": {"reason": "index_offline"}}),
    ]
    for index, (status, extra) in enumerate(retrievals, start=1):
        key = ("collect-evidence", index)
        workflow._step_execution_retrieval_manifest_artifacts[key] = (
            build_durable_retrieval_manifest_artifact({"status": status, **extra})
        )
        workflow._step_execution_context_projections[key] = {
            "retrievalManifestRef": workflow._step_execution_retrieval_manifest_artifacts[
                key
            ]["artifactRef"]
        }

        persisted_ref = await workflow._persist_step_execution_retrieval_manifest(
            "collect-evidence",
            attempt=index,
        )

        assert persisted_ref == f"art_retrieval_{index}"
        assert workflow._step_execution_context_projections[key][
            "retrievalManifestRef"
        ] == persisted_ref

    assert [write["payload"]["status"] for write in writes] == [
        "captured",
        "skipped",
        "unavailable",
    ]
    assert all(
        write["name"].startswith("reports/retrieval_manifests/")
        for write in writes
    )
    assert all(
        write["metadata_json"]["artifact_kind"] == "retrieval_manifest"
        for write in writes
    )
    assert all(
        write["payload"]["retrievalManifestDigest"].startswith("sha256:")
        for write in writes
    )


def test_canonical_step_checkpoint_writes_keep_replay_patch_guard() -> None:
    source = Path(run_module.__file__).read_text()

    assert run_module.RUN_CANONICAL_STEP_CHECKPOINTS_PATCH == (
        "run-canonical-step-checkpoints-v1"
    )
    assert run_module.RUN_EMIT_EPHEMERAL_STEP_CHECKPOINTS_PATCH == (
        "run-emit-ephemeral-step-checkpoints-v1"
    )
    guard = "if not workflow.patched(RUN_CANONICAL_STEP_CHECKPOINTS_PATCH):"
    ephemeral_guard = "RUN_EMIT_EPHEMERAL_STEP_CHECKPOINTS_PATCH"
    activity_call = "result = await self._create_step_checkpoint_via_activity("

    assert source.index(guard) < source.index(activity_call)
    assert source.index(ephemeral_guard) < source.index(activity_call)


def test_recovery_workspace_prepares_before_marking_failed_step_running() -> None:
    source = Path(run_module.__file__).read_text()

    prepare_call = (
        "await self._prepare_recovery_workspace_for_failed_step(node_id)\n"
        "                self._mark_step_running("
    )
    assert prepare_call in source


def _registry_payload() -> dict[str, Any]:
    return {
        "skills": [
            {
                "name": "repo.apply_patch",
                "version": "1.0.0",
                "description": "Apply patch",
                "inputs": {"schema": {"type": "object"}},
                "outputs": {"schema": {"type": "object"}},
                "executor": {
                    "activity_type": "mm.skill.execute",
                    "selector": {"mode": "by_capability"},
                },
                "requirements": {"capabilities": ["sandbox"]},
                "policies": {
                    "timeouts": {
                        "start_to_close_seconds": 1800,
                        "schedule_to_close_seconds": 3600,
                    },
                    "retries": {"max_attempts": 1},
                },
            }
        ]
    }

def test_run_initializes_latest_run_step_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    ledger = workflow.get_step_ledger()
    progress = workflow.get_progress()

    assert ledger["workflowId"] == "wf-run-1"
    assert ledger["runId"] == "run-1"
    assert ledger["runScope"] == "latest"
    assert [step["logicalStepId"] for step in ledger["steps"]] == [
        "prepare",
        "run-tests",
    ]
    assert ledger["steps"][0]["status"] == "ready"
    assert ledger["steps"][1]["status"] == "pending"
    assert progress["total"] == 2
    assert progress["ready"] == 1
    assert progress["pending"] == 1


def test_review_gate_retry_requires_reattempt_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()

    assert workflow._review_gate_retry_allowed(
        verdict=SimpleNamespace(
            verdict="ADDITIONAL_WORK_NEEDED",
            recommended_next_action="needs_human",
            recoverable_in_current_runtime=True,
        ),
        review_retry_count=0,
        max_review_attempts=2,
        consecutive_no_progress_attempts=0,
        max_consecutive_no_progress_attempts=2,
    ) is False

    assert workflow._review_gate_retry_allowed(
        verdict=SimpleNamespace(
            verdict="ADDITIONAL_WORK_NEEDED",
            recommended_next_action="blocked",
            recoverable_in_current_runtime=True,
        ),
        review_retry_count=0,
        max_review_attempts=2,
        consecutive_no_progress_attempts=0,
        max_consecutive_no_progress_attempts=2,
    ) is False

    assert workflow._review_gate_retry_allowed(
        verdict=SimpleNamespace(
            verdict="ADDITIONAL_WORK_NEEDED",
            recommended_next_action="reattempt_current_step",
            recoverable_in_current_runtime=True,
        ),
        review_retry_count=0,
        max_review_attempts=2,
        consecutive_no_progress_attempts=0,
        max_consecutive_no_progress_attempts=2,
    ) is True


def test_moonspec_verifier_resolves_only_exact_next_remediation_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    nodes = [
        {
            "id": "remediate-1",
            "annotations": {
                "issueImplementRole": "moonspec-remediation",
                "moonSpecRemediationAttempt": 1,
                "moonSpecRemediationMaxAttempts": 2,
            },
        },
        {
            "id": "verify-1",
            "annotations": {
                "issueImplementRole": "moonspec-verification-gate",
                "moonSpecRemediationAttempt": 1,
                "moonSpecRemediationMaxAttempts": 2,
            },
        },
        {
            "id": "remediate-2",
            "annotations": {
                "issueImplementRole": "moonspec-remediation",
                "moonSpecRemediationAttempt": 2,
                "moonSpecRemediationMaxAttempts": 2,
            },
        },
        {
            "id": "verify-2",
            "annotations": {
                "issueImplementRole": "moonspec-verification-gate",
                "moonSpecRemediationAttempt": 2,
                "moonSpecRemediationMaxAttempts": 2,
                "moonSpecFinalRemediationGate": True,
            },
        },
    ]
    for attempt in range(3, 7):
        nodes.extend(
            [
                {
                    "id": f"remediate-{attempt}",
                    "annotations": {
                        "issueImplementRole": "moonspec-remediation",
                        "moonSpecRemediationAttempt": attempt,
                        "moonSpecRemediationMaxAttempts": 2,
                    },
                },
                {
                    "id": f"verify-{attempt}",
                    "annotations": {
                        "issueImplementRole": "moonspec-verification-gate",
                        "moonSpecRemediationAttempt": attempt,
                        "moonSpecRemediationMaxAttempts": 2,
                        "moonSpecFinalRemediationGate": False,
                    },
                },
            ]
        )

    successor, reason = workflow._resolve_next_moonspec_remediation_step(
        ordered_nodes=nodes,
        current_index=1,
    )
    assert successor is not None
    assert successor.logical_step_id == "remediate-2"
    assert reason == "verification_requested_remediation"

    nodes[2]["annotations"]["moonSpecRemediationAttempt"] = 1
    assert workflow._resolve_next_moonspec_remediation_step(
        ordered_nodes=nodes,
        current_index=1,
    ) == (None, "no_remediation_successor")


def test_final_moonspec_verifier_has_no_remediation_successor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    final_verifier = {
        "id": "verify-6",
        "annotations": {
            "issueImplementRole": "moonspec-verification-gate",
            "moonSpecRemediationAttempt": 6,
            "moonSpecRemediationMaxAttempts": 6,
        },
    }

    assert workflow._resolve_next_moonspec_remediation_step(
        ordered_nodes=[final_verifier],
        current_index=0,
    ) == (None, "remediation_budget_exhausted")


def test_moonspec_remediation_budget_uses_actual_active_step_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    nodes = [
        {
            "id": f"remediate-{attempt}",
            "annotations": {
                "issueImplementRole": "moonspec-remediation",
                "moonSpecRemediationAttempt": attempt,
                "moonSpecRemediationMaxAttempts": 2,
            },
        }
        for attempt in range(1, 7)
    ]
    workflow._initialize_step_ledger(
        ordered_nodes=nodes,
        dependency_map={node["id"]: [] for node in nodes},
        updated_at=now,
    )
    workflow._mark_step_running("remediate-1", updated_at=now, summary="Started")
    workflow._mark_step_terminal(
        "remediate-1", status="completed", updated_at=now, summary="Done"
    )
    workflow._mark_step_running("remediate-2", updated_at=now, summary="Started")

    budget = workflow._moonspec_remediation_budget_metadata(
        ordered_nodes=nodes,
        current_attempt=6,
        max_attempts=2,
    )

    assert budget == {
        "maxAttempts": 2,
        "currentAttempt": 6,
        "attemptsStarted": 2,
        "attemptsCompleted": 1,
        "remainingAttempts": 0,
        "exhausted": True,
    }


@pytest.mark.parametrize(
    ("verdict", "recoverable", "disposition", "routing", "reason"),
    [
        ("ADDITIONAL_WORK_NEEDED", True, "accept", "advance_to_next_remediation", "verification_requested_remediation"),
        ("NO_DETERMINATION", True, "retry", "retry_current_verifier", "recoverable_no_determination"),
        ("NO_DETERMINATION", False, "accept", "stop_at_control_gate", "unrecoverable_no_determination"),
        ("BLOCKED", False, "accept", "stop_at_control_gate", "terminal_gate_verdict"),
        ("FAILED_UNRECOVERABLE", False, "accept", "stop_at_control_gate", "terminal_gate_verdict"),
        ("FULLY_IMPLEMENTED", False, "accept", "exit_remediation_loop", "verification_passed"),
    ],
)
def test_moonspec_gate_transition_matrix(
    monkeypatch: pytest.MonkeyPatch,
    verdict: str,
    recoverable: bool,
    disposition: str,
    routing: str,
    reason: str,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    nodes = [
        {
            "id": "remediate-1",
            "annotations": {"issueImplementRole": "moonspec-remediation", "moonSpecRemediationAttempt": 1, "moonSpecRemediationMaxAttempts": 2},
        },
        {
            "id": "verify-1",
            "annotations": {"issueImplementRole": "moonspec-verification-gate", "moonSpecRemediationAttempt": 1, "moonSpecRemediationMaxAttempts": 2},
        },
        {
            "id": "remediate-2",
            "annotations": {"issueImplementRole": "moonspec-remediation", "moonSpecRemediationAttempt": 2, "moonSpecRemediationMaxAttempts": 2},
        },
        {
            "id": "verify-2",
            "annotations": {"issueImplementRole": "moonspec-verification-gate", "moonSpecRemediationAttempt": 2, "moonSpecRemediationMaxAttempts": 2, "moonSpecFinalRemediationGate": True},
        },
    ]
    decision = workflow._resolve_gate_transition(
        verdict=SimpleNamespace(verdict=verdict, recoverable_in_current_runtime=recoverable),
        ordered_nodes=nodes,
        current_index=1,
    )
    assert (decision.disposition, decision.routing_disposition, decision.reason_code) == (
        disposition,
        routing,
        reason,
    )


def test_moonspec_gate_transition_handles_initial_final_and_malformed_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    nodes = [
        {
            "id": "initial-verify",
            "tool": {"name": "moonspec-verify"},
            "inputs": {"selectedSkill": "moonspec-verify"},
        },
        {
            "id": "remediate-1",
            "annotations": {"issueImplementRole": "moonspec-remediation", "moonSpecRemediationAttempt": 1, "moonSpecRemediationMaxAttempts": 1},
        },
        {
            "id": "verify-1",
            "annotations": {"issueImplementRole": "moonspec-verification-gate", "moonSpecRemediationAttempt": 1, "moonSpecRemediationMaxAttempts": 1, "moonSpecFinalRemediationGate": True},
        },
    ]
    verdict = SimpleNamespace(
        verdict="ADDITIONAL_WORK_NEEDED",
        recoverable_in_current_runtime=True,
    )

    initial = workflow._resolve_gate_transition(
        verdict=verdict,
        ordered_nodes=nodes,
        current_index=0,
    )
    assert initial.successor is not None
    assert initial.successor.logical_step_id == "remediate-1"

    final = workflow._resolve_gate_transition(
        verdict=verdict,
        ordered_nodes=nodes,
        current_index=2,
    )
    assert final.routing_disposition == "stop_at_control_gate"
    assert final.reason_code == "remediation_budget_exhausted"

    malformed_nodes = [*nodes, {**nodes[1], "id": "duplicate-remediation"}]
    malformed = workflow._resolve_gate_transition(
        verdict=verdict,
        ordered_nodes=malformed_nodes,
        current_index=0,
    )
    assert malformed.routing_disposition == "stop_at_control_gate"
    assert malformed.reason_code == "no_remediation_successor"

    ordinary = workflow._resolve_gate_transition(
        verdict=verdict,
        ordered_nodes=[{"id": "ordinary", "tool": {"name": "agent"}}],
        current_index=0,
    )
    assert ordinary.disposition == "generic"

    invalid = workflow._resolve_gate_transition(
        verdict=SimpleNamespace(
            verdict="NO_DETERMINATION",
            recoverable_in_current_runtime=False,
            invalid=True,
            degraded=False,
        ),
        ordered_nodes=nodes,
        current_index=2,
    )
    assert invalid.disposition == "invalid"
    assert invalid.reason_code == "invalid_gate_result"


def test_moonspec_gate_transition_emits_structured_observability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        workflow,
        "_get_logger",
        lambda: SimpleNamespace(
            info=lambda message, payload: events.append((message, payload))
        ),
    )
    transition = run_module.GateTransitionDecision(
        disposition="accept",
        routing_disposition="advance_to_next_remediation",
        reason_code="verification_requested_remediation",
        successor=run_module.MoonSpecRemediationSuccessor(
            logical_step_id="remediate-2",
            attempt=2,
            max_attempts=6,
            node_index=4,
        ),
    )
    workflow._record_moonspec_gate_transition_event(
        logical_step_id="verify-1",
        node={
            "annotations": {
                "issueImplementRole": "moonspec-verification-gate",
                "moonSpecRemediationAttempt": 1,
                "moonSpecRemediationMaxAttempts": 6,
            }
        },
        verdict="ADDITIONAL_WORK_NEEDED",
        transition=transition,
        review_retries_consumed=0,
    )
    assert events[0][0] == "moonspec_gate_transition %s"
    payload = json.loads(events[0][1])
    assert payload["nextLogicalStepId"] == "remediate-2"
    assert payload["reviewRetriesConsumed"] == 0


def test_run_progress_query_exposes_current_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    progress = workflow.get_progress()

    assert progress["runId"] == "run-1"
    assert progress["total"] == 2

def test_first_step_running_stamps_mm_started_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_mark_step_running`` is the closest existing semantic boundary for
    "real work began" — when a logical step first transitions to executing, the
    workflow must stamp ``mm_started_at``. Subsequent step transitions and
    retries must not move the timestamp."""
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)
    upserts: list[object] = []
    monkeypatch.setattr(
        run_module.workflow, "upsert_search_attributes", upserts.append
    )
    workflow = MoonMindRunWorkflow()
    first_now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    later = datetime(2026, 4, 7, 12, 1, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=first_now,
    )
    upserts.clear()

    workflow._mark_step_running(
        "prepare", updated_at=first_now, summary="Preparing"
    )
    assert workflow._started_at == first_now

    # Find the mm_started_at upsert. _set_state-driven upserts do not include
    # the semantic timestamp; the dedicated upsert from _mark_real_work_started
    # contains exactly one pair carrying the value.
    started_at_upserts = [
        pairs
        for pairs in upserts
        if any(
            getattr(p.key, "name", None) == run_module.MM_STARTED_AT_SEARCH_ATTRIBUTE
            for p in (pairs if isinstance(pairs, list) else [])
        )
    ]
    assert len(started_at_upserts) == 1

    workflow._mark_step_terminal(
        "prepare", status="completed", updated_at=first_now, summary="Done"
    )
    workflow._refresh_step_readiness(updated_at=later)
    workflow._mark_step_running(
        "run-tests", updated_at=later, summary="Running tests"
    )
    # mm_started_at is set exactly once; later step transitions never overwrite it.
    assert workflow._started_at == first_now
    started_at_upserts = [
        pairs
        for pairs in upserts
        if any(
            getattr(p.key, "name", None) == run_module.MM_STARTED_AT_SEARCH_ATTRIBUTE
            for p in (pairs if isinstance(pairs, list) else [])
        )
    ]
    assert len(started_at_upserts) == 1

def test_run_tracks_status_transitions_and_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )
    workflow._mark_step_running("prepare", updated_at=now, summary="Preparing workspace")
    workflow._mark_step_terminal(
        "prepare",
        status="completed",
        updated_at=now,
        summary="Workspace ready",
    )
    workflow._refresh_step_readiness(updated_at=now)
    workflow._mark_step_running("run-tests", updated_at=now, summary="Running tests")
    workflow._mark_step_waiting(
        "run-tests",
        status="reviewing",
        updated_at=now,
        waiting_reason="Awaiting structured review result",
        summary="Structured review in progress",
    )
    workflow._mark_step_terminal(
        "run-tests",
        status="failed",
        updated_at=now,
        summary="Tests failed",
        last_error="pytest failed",
    )
    workflow._mark_step_running("run-tests", updated_at=now, summary="Retrying tests")
    workflow._mark_step_waiting(
        "run-tests",
        status="awaiting_external",
        updated_at=now,
        waiting_reason="Awaiting child workflow progress",
        summary="Child runtime launched",
    )
    workflow._mark_step_terminal(
        "run-tests",
        status="canceled",
        updated_at=now,
        summary="Canceled by operator",
    )

    ledger = workflow.get_step_ledger()
    step = ledger["steps"][1]
    progress = workflow.get_progress()

    assert step["executionOrdinal"] == 2
    assert step["status"] == "canceled"
    assert step["waitingReason"] is None
    assert step["lastError"] == "pytest failed"
    assert progress["completed"] == 1
    assert progress["canceled"] == 1

def test_run_terminal_success_clears_previous_last_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )
    workflow._mark_step_running("prepare", updated_at=now, summary="Preparing workspace")
    workflow._mark_step_terminal(
        "prepare",
        status="failed",
        updated_at=now,
        summary="Workspace failed",
        last_error="pytest failed",
    )
    workflow._mark_step_running("prepare", updated_at=now, summary="Retrying workspace")
    workflow._mark_step_terminal(
        "prepare",
        status="completed",
        updated_at=now,
        summary="Workspace ready",
        last_error=None,
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["status"] == "completed"
    assert step["lastError"] is None

def test_run_missing_step_ledger_updates_do_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    workflow._mark_step_running("missing-step", updated_at=now, summary="Ignored")
    workflow._mark_step_waiting(
        "missing-step",
        status="reviewing",
        updated_at=now,
        waiting_reason="Ignored",
        summary="Ignored",
    )
    workflow._mark_step_terminal(
        "missing-step",
        status="failed",
        updated_at=now,
        summary="Ignored",
        last_error="ignored",
    )

    progress = workflow.get_progress()
    assert progress["ready"] == 1
    assert progress["pending"] == 1
    assert progress["executing"] == 0

def test_plan_dependency_map_rewrites_bundled_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    bundle_id = "wf-run-1:jules-bundle:step-1:step-2"

    dependency_map = workflow._plan_dependency_map(
        ordered_nodes=[
            {
                "id": "prepare",
                "tool": {"type": "skill", "name": "repo.prepare"},
            },
            {
                "id": bundle_id,
                "tool": {"type": "agent_runtime", "name": "jules"},
                "inputs": {"bundledNodeIds": ["step-1", "step-2"]},
            },
            {
                "id": "publish",
                "tool": {"type": "skill", "name": "repo.publish"},
            },
        ],
        edges=(
            SimpleNamespace(from_node="prepare", to_node="step-1"),
            SimpleNamespace(from_node="step-1", to_node="step-2"),
            SimpleNamespace(from_node="step-2", to_node="publish"),
        ),
    )

    assert dependency_map == {
        "prepare": [],
        bundle_id: ["prepare"],
        "publish": [bundle_id],
    }

def test_run_queries_remain_available_after_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )
    workflow._mark_step_running("prepare", updated_at=now, summary="Preparing workspace")
    workflow._mark_step_terminal(
        "prepare",
        status="skipped",
        updated_at=now,
        summary="Skipped after reuse",
    )
    workflow._state = run_module.STATE_COMPLETED

    assert workflow.get_progress()["skipped"] == 1
    assert workflow.get_step_ledger()["steps"][0]["status"] == "skipped"

def test_run_memo_updates_remain_compact(monkeypatch: pytest.MonkeyPatch) -> None:
    memo_updates = _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._title = "Ledger run"
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )
    workflow._summary = "Executing step ledger tests."
    workflow._update_memo()
    workflow._update_search_attributes()

    latest_memo = next(memo for memo in reversed(memo_updates) if "title" in memo)
    assert latest_memo["title"] == "Ledger run"
    assert latest_memo["summary"] == "Executing step ledger tests."
    assert "steps" not in latest_memo
    assert "progress" not in latest_memo
    assert "checks" not in latest_memo

def test_update_search_attributes_status_memo_is_patch_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memo_updates = _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._waiting_reason = "dependency_wait"
    workflow._attention_required = True

    workflow._update_search_attributes()

    assert memo_updates == []

    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id == run_module.RUN_STATUS_MEMO_UPSERT_PATCH,
    )
    workflow._update_search_attributes()

    assert memo_updates[-1] == {
        "waiting_reason": "dependency_wait",
        "attention_required": True,
    }

def test_run_memo_includes_current_step_order_when_step_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memo_updates = _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)
    workflow = MoonMindRunWorkflow()

    workflow._title = "Ledger run"
    workflow._summary = "Executing plan step 2/3"
    workflow._update_memo()
    assert "mm_current_step_order" not in memo_updates[-1]

    workflow._step_count = 2
    workflow._update_memo()
    assert memo_updates[-1]["mm_current_step_order"] == 2

def test_run_memo_surfaces_runtime_and_skill_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memo_updates = _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)
    workflow = MoonMindRunWorkflow()

    workflow._title = "Resolve PR #1633"
    workflow._summary = "Execution initialized."
    workflow._target_runtime = "codex_cli"
    workflow._target_skill = "pr-resolver"
    workflow._update_memo()

    assert memo_updates[-1]["targetRuntime"] == "codex_cli"
    assert memo_updates[-1]["targetSkill"] == "pr-resolver"


def test_run_search_attributes_encode_runtime_and_skill_as_keyword_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    upserts: list[object] = []
    monkeypatch.setattr(
        run_module.workflow,
        "upsert_search_attributes",
        upserts.append,
    )
    workflow = MoonMindRunWorkflow()

    workflow._title = "Resolve PR #1633"
    workflow._target_runtime = "codex_cli"
    workflow._target_skill = "pr-resolver"
    workflow._update_search_attributes()

    pairs_by_name = {
        pair.key.name: pair
        for pair in upserts[-1]
    }
    runtime_pair = pairs_by_name["mm_target_runtime"]
    skill_pair = pairs_by_name["mm_target_skill"]
    assert runtime_pair.key == run_module.SearchAttributeKey.for_keyword_list(
        "mm_target_runtime"
    )
    assert runtime_pair.value == ["codex_cli"]
    assert skill_pair.key == run_module.SearchAttributeKey.for_keyword_list(
        "mm_target_skill"
    )
    assert skill_pair.value == ["pr-resolver"]


def test_run_groups_child_lineage_and_evidence_into_step_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "delegate-agent",
                "tool": {"type": "agent_runtime", "name": "codex"},
                "inputs": {"title": "Delegate agent"},
            }
        ],
        dependency_map={"delegate-agent": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "delegate-agent",
        updated_at=now,
        summary="Launching child runtime",
    )
    workflow._record_step_result_evidence(
        "delegate-agent",
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "childWorkflowId": "wf-child-1",
                "childRunId": "run-child-1",
                "agentRunId": "550e8400-e29b-41d4-a716-446655440000",
                "outputSummaryRef": "art_summary_1",
                "outputAgentResultRef": "art_primary_1",
                "stdoutArtifactRef": "art_stdout_1",
                "stderrArtifactRef": "art_stderr_1",
                "mergedLogArtifactRef": "art_merged_1",
                "diagnosticsRef": "art_diag_1",
                "providerSnapshotRef": "art_provider_1",
                "externalStateRef": "artifact://omnigent/state",
                "outputRefs": [
                    "art_stdout_1",
                    "art_stderr_1",
                    "art_diag_1",
                    "art_primary_1",
                ],
            },
        },
        updated_at=now,
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["refs"] == {
        "childWorkflowId": "wf-child-1",
        "childRunId": "run-child-1",
        "agentRunId": "550e8400-e29b-41d4-a716-446655440000",
        "latestStepExecutionManifestRef": None,
        "stepExecutionManifestRefs": [],
        "latestStepExecutionCheckpointRef": None,
        "stepExecutionCheckpointRefs": [],
        "checkpointRefsByBoundary": {},
    }
    assert step["artifacts"] == {
        "outputSummary": "art_summary_1",
        "outputPrimary": "art_primary_1",
        "runtimeStdout": "art_stdout_1",
        "runtimeStderr": "art_stderr_1",
        "runtimeMergedLogs": "art_merged_1",
        "runtimeDiagnostics": "art_diag_1",
        "providerSnapshot": "art_provider_1",
        "externalStateRef": "artifact://omnigent/state",
        "stepExecutionManifestRef": None,
        "stepExecutionManifestRefs": [],
    }
    assert workflow._step_execution_compact_output_refs("delegate-agent") == {
        "summaryRef": "art_summary_1",
        "primaryRef": "art_primary_1",
        "stdoutRef": "art_stdout_1",
        "stderrRef": "art_stderr_1",
        "logsRef": "art_merged_1",
        "externalStateRef": "artifact://omnigent/state",
    }


def test_run_records_direct_report_outputs_as_accepted_step_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id == run_module.RUN_DIRECT_TOOL_REPORT_OUTPUTS_PATCH,
    )
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    execution_result = {
        "status": "COMPLETED",
        "primary_report_ref": "art_pentest_report_1",
        "summary_ref": "art_pentest_summary_1",
        "diagnostics_artifact_ref": "art_pentest_diag_1",
        "report_type": "security_pentest_report",
    }

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "pentest",
                "tool": {"type": "skill", "name": "security.pentest.run"},
                "inputs": {"title": "Run pentest"},
            }
        ],
        dependency_map={"pentest": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "pentest",
        updated_at=now,
        summary="Running pentest",
    )
    workflow._record_step_result_evidence(
        "pentest",
        execution_result=execution_result,
        updated_at=now,
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["artifacts"]["outputPrimary"] == "art_pentest_report_1"
    assert step["artifacts"]["outputSummary"] == "art_pentest_summary_1"
    assert step["artifacts"]["runtimeDiagnostics"] == "art_pentest_diag_1"
    assert workflow._step_has_accepted_output_evidence("pentest", execution_result)


def test_run_waiting_state_captures_child_workflow_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "delegate-agent",
                "tool": {"type": "agent_runtime", "name": "codex"},
                "inputs": {"title": "Delegate agent"},
            }
        ],
        dependency_map={"delegate-agent": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "delegate-agent",
        updated_at=now,
        summary="Launching child runtime",
    )
    workflow._mark_step_waiting(
        "delegate-agent",
        status="awaiting_external",
        updated_at=now,
        waiting_reason="Awaiting child workflow progress",
        summary="Child runtime launched",
        refs={"childWorkflowId": "wf-child-1"},
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["status"] == "awaiting_external"
    assert step["refs"] == {
        "childWorkflowId": "wf-child-1",
        "childRunId": None,
        "agentRunId": None,
        "latestStepExecutionManifestRef": None,
        "stepExecutionManifestRefs": [],
        "latestStepExecutionCheckpointRef": None,
        "stepExecutionCheckpointRefs": [],
        "checkpointRefsByBoundary": {},
    }


@pytest.mark.asyncio
async def test_run_records_step_execution_manifest_ref_when_work_begins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"artifact-attempt-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "delegate-agent",
                "tool": {"type": "agent_runtime", "name": "codex"},
                "inputs": {"title": "Delegate agent"},
            }
        ],
        dependency_map={"delegate-agent": []},
        updated_at=now,
    )

    workflow._mark_step_running(
        "delegate-agent",
        updated_at=now,
        summary="Launching child runtime",
    )
    await workflow._record_step_execution_manifest(
        "delegate-agent",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )
    workflow._record_step_result_evidence(
        "delegate-agent",
        execution_result={
            "status": "FAILED",
            "outputs": {
                "childWorkflowId": "wf-child-1",
                "childRunId": "run-child-1",
                "outputSummaryRef": "artifact://summary/attempt-1",
            },
        },
        updated_at=now,
    )
    workflow._mark_step_terminal(
        "delegate-agent",
        status="failed",
        updated_at=now,
        summary="Runtime failed",
    )
    workflow._mark_step_running(
        "delegate-agent",
        updated_at=now,
        summary="Retrying child runtime",
    )
    await workflow._record_step_execution_manifest(
        "delegate-agent",
        phase="start",
        updated_at=now,
        reason="runtime_recovered",
    )

    step = workflow.get_step_ledger()["steps"][0]
    assert step["executionOrdinal"] == 2
    assert step["refs"]["latestStepExecutionManifestRef"] == "artifact-attempt-2"
    assert step["refs"]["stepExecutionManifestRefs"] == [
        "artifact-attempt-1",
        "artifact-attempt-2",
    ]
    assert writes[0]["content_type"] == (
        "application/vnd.moonmind.step-execution+json;version=1"
    )
    assert writes[0]["payload"]["stepExecutionId"] == (
        "wf-run-1:run-1:delegate-agent:execution:1"
    )
    assert writes[0]["metadata_json"]["idempotencyKey"] == (
        "wf-run-1:run-1:delegate-agent:execution:1:manifest"
    )
    assert writes[0]["payload"]["reason"] == "initial_execution"
    assert writes[0]["payload"]["context"]["contextBundleRef"].startswith(
        "execution-context-bundle://sha256:"
    )
    assert writes[0]["payload"]["context"]["contextBundleDigest"].startswith(
        "sha256:"
    )
    assert writes[0]["payload"]["context"]["builderVersion"] == (
        "execution-context-builder-v1"
    )
    assert writes[0]["payload"]["execution"] == {
        "runtimeContextPolicy": "fresh_agent_run"
    }
    assert writes[0]["payload"]["outputs"] == {}
    assert writes[1]["payload"]["reason"] == "runtime_recovered"
    assert writes[1]["payload"]["execution"] == {
        "runtimeContextPolicy": "fresh_agent_run",
        "runtimeSessionReset": {
            "requestedPolicy": "reuse_session_new_epoch",
            "resolvedPolicy": "fresh_agent_run",
            "semantics": "new_epoch_cleared_context",
            "clearContext": True,
            "newEpoch": True,
            "runtimeId": "codex",
            "sourceExecutionOrdinal": {
                "workflowId": "wf-run-1",
                "runId": "run-1",
                "logicalStepId": "delegate-agent",
                "executionOrdinal": 1,
            },
            "availableCheckpointEvidence": {"available": False},
        },
        "childWorkflowId": "wf-child-1",
        "childRunId": "run-child-1",
    }
    assert writes[1]["payload"]["status"] == "blocked"
    assert writes[1]["payload"]["terminalDisposition"] == "blocked"
    assert writes[1]["payload"]["outputs"] == {
        "summary": "Workspace policy rejected before launch."
    }
    assert writes[1]["payload"]["workspace"]["policy"] == (
        "continue_from_previous_execution"
    )
    assert writes[1]["payload"]["workspace"]["sourceExecutionOrdinal"] == {
        "workflowId": "wf-run-1",
        "runId": "run-1",
        "logicalStepId": "delegate-agent",
        "executionOrdinal": 1,
    }
    assert "lineage" not in writes[1]["payload"]


@pytest.mark.asyncio
async def test_step_execution_manifest_includes_compact_resilience_policy_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    workflow._resilience_policy_ref = {
        "policyId": "resilience-policy-abc123",
        "policyVersion": 1,
        "digest": "abc123",
        "contentType": run_module.RESILIENCE_POLICY_CONTENT_TYPE,
        "envelopeRef": "artifact://resilience/policy",
    }
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return "artifact://step/manifest"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "delegate-agent",
                "tool": {"type": "agent_runtime", "name": "codex"},
                "inputs": {"title": "Delegate agent"},
            }
        ],
        dependency_map={"delegate-agent": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "delegate-agent",
        updated_at=now,
        summary="Launching child runtime",
    )

    await workflow._record_step_execution_manifest(
        "delegate-agent",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )

    assert writes[0]["payload"]["execution"]["resiliencePolicyRef"] == (
        workflow._resilience_policy_ref
    )
    assert "attempts" not in writes[0]["payload"]["execution"]["resiliencePolicyRef"]
    assert "timeouts" not in writes[0]["payload"]["execution"]["resiliencePolicyRef"]


@pytest.mark.asyncio
async def test_step_execution_manifest_merges_explicit_execution_with_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"artifact-attempt-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "delegate-external",
                "tool": {"type": "agent_runtime", "name": "jules"},
                "inputs": {"title": "Delegate external agent"},
            }
        ],
        dependency_map={"delegate-external": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "delegate-external",
        updated_at=now,
        summary="Launching external runtime",
    )
    workflow._record_step_result_evidence(
        "delegate-external",
        execution_result={
            "status": "RUNNING",
            "outputs": {
                "childWorkflowId": "wf-child-from-ledger",
                "childRunId": "run-child-from-ledger",
                "diagnosticsRef": "artifact://diagnostics/external",
            },
        },
        updated_at=now,
    )

    await workflow._record_step_execution_manifest(
        "delegate-external",
        phase="start",
        updated_at=now,
        reason="initial_execution",
        execution={
            "kind": "agent_runtime",
            "childWorkflowId": "wf-child-explicit",
        },
    )

    assert writes[0]["content_type"] == (
        "application/vnd.moonmind.step-execution+json;version=1"
    )
    assert writes[0]["metadata_json"]["artifact_kind"] == "step_execution_manifest"
    assert writes[0]["metadata_json"]["logicalStepId"] == "delegate-external"
    assert writes[0]["metadata_json"]["attempt"] == 1
    assert writes[0]["payload"]["context"]["contextBundleRef"].startswith(
        "execution-context-bundle://sha256:"
    )
    assert writes[0]["payload"]["execution"] == {
        "runtimeContextPolicy": "external_provider_continuation",
        "externalProviderContinuation": {
            "attemptIdentity": {
                "workflowId": "wf-run-1",
                "runId": "run-1",
                "logicalStepId": "delegate-external",
                "executionOrdinal": 1,
                "stepExecutionId": "wf-run-1:run-1:delegate-external:execution:1",
            },
            "contextRefs": {},
            "knownSideEffects": {"records": []},
            "checkpointEvidence": {"available": False},
        },
        "childWorkflowId": "wf-child-explicit",
        "childRunId": "run-child-from-ledger",
        "diagnosticsRef": "artifact://diagnostics/external",
        "kind": "agent_runtime",
    }


@pytest.mark.asyncio
async def test_external_continuation_manifest_records_side_effects_and_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under external_provider_continuation MoonMind still records attempt
    identity, context refs, known side effects, and available checkpoint
    evidence for the attempt even though it cannot reset the external runtime."""

    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"artifact-attempt-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "delegate-external",
                "tool": {"type": "agent_runtime", "name": "jules"},
                "inputs": {"title": "Delegate external agent"},
            }
        ],
        dependency_map={"delegate-external": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "delegate-external",
        updated_at=now,
        summary="Launching external runtime",
    )
    workflow._record_step_result_evidence(
        "delegate-external",
        execution_result={
            "status": "RUNNING",
            "outputs": {
                "childWorkflowId": "wf-child-external",
                "childRunId": "run-child-external",
            },
        },
        updated_at=now,
    )
    # Durable checkpoint evidence recorded on the step ledger row.
    workflow._step_ledger_rows[0]["stateCheckpointRef"] = (
        "artifact://checkpoints/external-step"
    )
    workflow._step_ledger_rows[0]["workspaceCheckpointRef"] = (
        "artifact://checkpoints/workspace"
    )
    workflow._step_ledger_rows[0]["stepCheckpointRef"] = "artifact://checkpoints/step"
    # A known, already-occurred external side effect from this attempt.
    workflow._record_step_side_effect(
        "delegate-external",
        effect_class="external_idempotent",
        operation="open_pull_request",
        target="github_pr",
        idempotency_key="pr-key-1",
        workflow_state_accepted=True,
    )

    await workflow._record_step_execution_manifest(
        "delegate-external",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )

    assert writes[0]["content_type"] == (
        "application/vnd.moonmind.step-execution+json;version=1"
    )
    execution = writes[0]["payload"]["execution"]
    assert execution["runtimeContextPolicy"] == "external_provider_continuation"
    # Attempt identity / context refs are still recorded.
    assert execution["childWorkflowId"] == "wf-child-external"
    assert execution["childRunId"] == "run-child-external"
    continuation = execution["externalProviderContinuation"]
    assert continuation["attemptIdentity"] == {
        "workflowId": "wf-run-1",
        "runId": "run-1",
        "logicalStepId": "delegate-external",
        "executionOrdinal": 1,
        "stepExecutionId": "wf-run-1:run-1:delegate-external:execution:1",
    }
    # Known side effects are recorded under the continuation path.
    assert continuation["knownSideEffects"] == {
        "records": [
            {
                "class": "external_idempotent",
                "kind": "normal",
                "operation": "open_pull_request",
                "target": "github_pr",
                "idempotencyKey": "pr-key-1",
                "disposition": "accepted",
                "workflowStateAccepted": True,
            }
        ]
    }
    # Available checkpoint evidence is recorded under the continuation path.
    assert continuation["checkpointEvidence"] == {
        "stateCheckpointRef": "artifact://checkpoints/external-step",
        "workspaceCheckpointRef": "artifact://checkpoints/workspace",
        "stepCheckpointRef": "artifact://checkpoints/step",
    }


@pytest.mark.asyncio
async def test_run_records_legacy_start_manifest_status_when_status_vocab_unpatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: False)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"artifact-attempt-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "run-tests",
                "tool": {"type": "skill", "name": "repo.run_tests"},
                "inputs": {"title": "Run tests"},
            }
        ],
        dependency_map={"run-tests": []},
        updated_at=now,
    )

    workflow._mark_step_running("run-tests", updated_at=now, summary="Run tests")
    await workflow._record_step_execution_manifest(
        "run-tests",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )

    assert writes[0]["payload"]["status"] == "running"


@pytest.mark.asyncio
async def test_run_records_terminal_step_execution_manifest_with_result_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"artifact-attempt-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "run-tests",
                "tool": {"type": "skill", "name": "repo.run_tests"},
                "inputs": {"title": "Run tests"},
            }
        ],
        dependency_map={"run-tests": []},
        updated_at=now,
    )

    workflow._mark_step_running("run-tests", updated_at=now, summary="Run tests")
    await workflow._record_step_execution_manifest(
        "run-tests",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )
    workflow._record_step_result_evidence(
        "run-tests",
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "agentRunId": "agent-run-1",
                "outputSummaryRef": "artifact://summary/attempt-1",
                "stdoutArtifactRef": "artifact://stdout/attempt-1",
                "diagnosticsRef": "artifact://diagnostics/attempt-1",
            },
        },
        updated_at=now,
    )
    workflow._mark_step_terminal(
        "run-tests",
        status="completed",
        updated_at=now,
        summary="Done",
    )
    workflow._record_step_side_effect(
        "run-tests",
        effect_class="publication",
        operation="repo.publish_pr",
        target="PR-1",
        idempotency_key="wf-run-1:run-1:run-tests:execution:1:publish-pr",
        workflow_state_accepted=True,
    )
    await workflow._record_step_execution_manifest(
        "run-tests",
        phase="terminal",
        updated_at=now,
        reason="initial_execution",
        status="completed",
        terminal_disposition="accepted",
    )

    assert writes[0]["payload"]["status"] == "executing"
    assert writes[0]["payload"]["execution"] == {}
    assert writes[0]["payload"]["outputs"] == {}
    assert writes[1]["payload"]["status"] == "completed"
    assert writes[1]["payload"]["terminalDisposition"] == "accepted"
    assert writes[1]["payload"]["execution"] == {
        "agentRunId": "agent-run-1",
        "diagnosticsRef": "artifact://diagnostics/attempt-1",
    }
    assert writes[1]["payload"]["outputs"] == {
        "summaryRef": "artifact://summary/attempt-1",
        "stdoutRef": "artifact://stdout/attempt-1",
    }
    assert writes[1]["payload"]["sideEffects"] == {
        "records": [
            {
                "class": "publication",
                "kind": "normal",
                "operation": "repo.publish_pr",
                "target": "PR-1",
                "idempotencyKey": (
                    "wf-run-1:run-1:run-tests:execution:1:publish-pr"
                ),
                "workflowStateAccepted": True,
                "disposition": "accepted",
            }
        ],
        "summary": {
            "totalRecords": 1,
            "categories": {
                "git": 1,
                "external": 0,
                "artifact": 0,
                "publication": 1,
                "compensation": 0,
                "memory": 0,
                "retrieval": 0,
                "record": 1,
            },
            "byClass": {"publication": 1},
            "byDisposition": {"accepted": 2},
            "byKind": {"normal": 1},
        },
    }


@pytest.mark.asyncio
async def test_write_step_execution_manifest_requires_real_artifact_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"

    async def fake_execute_activity(
        activity_type: str,
        _payload: Any,
        **_kwargs: Any,
    ) -> tuple[dict[str, str], dict[str, str]]:
        assert activity_type == "artifact.create"
        return ({}, {"upload_url": "unused"})

    async def fake_execute_typed_activity(
        activity_type: str,
        _payload: Any,
        **_kwargs: Any,
    ) -> dict[str, bool]:
        raise AssertionError(f"unexpected typed activity: {activity_type}")

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_module, "execute_typed_activity", fake_execute_typed_activity)

    with pytest.raises(
        ValueError,
        match="artifact.create returned no artifact_id",
    ):
        await workflow._write_json_artifact(
            name="step-execution-manifest.json",
            payload={"schemaVersion": "v1"},
            content_type=STEP_EXECUTION_MANIFEST_CONTENT_TYPE,
        )


@pytest.mark.asyncio
async def test_write_json_artifact_supports_serialized_list_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"

    async def fake_execute_activity(
        activity_type: str,
        _payload: Any,
        **_kwargs: Any,
    ) -> list[dict[str, str]]:
        assert activity_type == "artifact.create"
        return [{"artifact_id": "art_list_payload"}, {"upload_url": "unused"}]

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> None:
        assert activity_type == "artifact.write_complete"
        assert payload.artifact_id == "art_list_payload"
        return None

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_module, "execute_typed_activity", fake_execute_typed_activity)

    assert (
        await workflow._write_json_artifact(
            name="step-execution-manifest.json",
            payload={"schemaVersion": "v1"},
            content_type=STEP_EXECUTION_MANIFEST_CONTENT_TYPE,
        )
        == "art_list_payload"
    )


def test_run_uses_deterministic_output_primary_fallback_for_generic_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "run-tests",
                "tool": {"type": "skill", "name": "repo.run_tests"},
                "inputs": {"title": "Run tests"},
            }
        ],
        dependency_map={"run-tests": []},
        updated_at=now,
    )
    workflow._record_step_result_evidence(
        "run-tests",
        execution_result={
            "status": "FAILED",
            "outputs": {
                "outputRefs": [
                    "art_stdout_1",
                    "art_primary_1",
                    "art_secondary_1",
                ],
                "outputAgentResultRef": "art_agent_result_1",
                "stdoutArtifactRef": "art_stdout_1",
                "diagnosticsRef": "art_diag_1",
            },
        },
        updated_at=now,
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["artifacts"]["outputPrimary"] == "art_primary_1"
    assert step["artifacts"]["runtimeStdout"] == "art_stdout_1"
    assert step["artifacts"]["runtimeDiagnostics"] == "art_diag_1"

def test_run_projects_workload_artifacts_and_metadata_from_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "workload-step",
                "tool": {
                    "type": "skill",
                    "name": "container.run_workload",
                },
                "inputs": {"title": "Run workload"},
            }
        ],
        dependency_map={"workload-step": []},
        updated_at=now,
    )
    workflow._record_step_result_evidence(
        "workload-step",
        execution_result={
            "status": "FAILED",
            "outputs": {
                "stdoutRef": "art_stdout_1",
                "stderrRef": "art_stderr_1",
                "diagnosticsRef": "art_diag_1",
                "outputRefs": {
                    "runtime.stdout": "art_stdout_1",
                    "runtime.stderr": "art_stderr_1",
                    "runtime.diagnostics": "art_diag_1",
                    "output.summary": "art_summary_1",
                    "test.report": "art_report_1",
                },
                "workloadMetadata": {
                    "agentRunId": "wf-1",
                    "stepId": "workload-step",
                    "attempt": 1,
                    "toolName": "container.run_workload",
                    "profileId": "local-python",
                    "imageRef": "python:3.12-slim",
                    "status": "failed",
                    "exitCode": 7,
                    "durationSeconds": 4.25,
                    "sessionContext": {
                        "sessionId": "session-1",
                        "sessionEpoch": 3,
                    },
                },
            },
        },
        updated_at=now,
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["refs"]["agentRunId"] == "wf-1"
    assert step["artifacts"]["runtimeStdout"] == "art_stdout_1"
    assert step["artifacts"]["runtimeStderr"] == "art_stderr_1"
    assert step["artifacts"]["runtimeDiagnostics"] == "art_diag_1"
    assert step["artifacts"]["outputSummary"] == "art_summary_1"
    assert step["artifacts"]["outputPrimary"] == "art_report_1"
    assert step["workload"]["profileId"] == "local-python"
    assert step["workload"]["imageRef"] == "python:3.12-slim"
    assert step["workload"]["sessionContext"] == {
        "sessionId": "session-1",
        "sessionEpoch": 3,
    }

def test_run_accepts_tuple_output_refs_and_ignores_string_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "run-tests",
                "tool": {"type": "skill", "name": "repo.run_tests"},
                "inputs": {"title": "Run tests"},
            }
        ],
        dependency_map={"run-tests": []},
        updated_at=now,
    )
    workflow._record_step_result_evidence(
        "run-tests",
        execution_result={
            "status": "FAILED",
            "outputs": {
                "outputRefs": "art_primary_1",
                "output_refs": (
                    "art_stdout_1",
                    "art_primary_1",
                    "",
                    7,
                ),
                "stdoutArtifactRef": "art_stdout_1",
            },
        },
        updated_at=now,
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["artifacts"]["outputPrimary"] == "art_primary_1"
    assert step["artifacts"]["runtimeStdout"] == "art_stdout_1"


def test_run_records_prepared_refs_and_idempotent_checkpoint_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 30, tzinfo=UTC)
    task_payload = {
        "inputAttachments": [{"artifactId": "objective-artifact"}],
        "steps": [
            {
                "id": "implement",
                "inputAttachments": [{"artifactId": "step-artifact"}],
            }
        ],
    }

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "implement",
                "tool": {"type": "agent_runtime", "name": "codex"},
                "inputs": {"title": "Implement"},
            }
        ],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._capture_prepared_input_refs({"task": task_payload})
    workflow._record_step_result_evidence(
        "implement",
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "outputRefs": ["artifact://output"],
                "latestCheckpointRef": "artifact://runtime/checkpoint",
            },
        },
        updated_at=now,
    )
    workflow._mark_step_terminal(
        "implement",
        status="completed",
        updated_at=now,
        summary="Implemented",
    )

    first_ref = workflow._record_step_checkpoint_evidence(
        "implement",
        updated_at=now,
    )
    second_ref = workflow._record_step_checkpoint_evidence(
        "implement",
        updated_at=now,
    )

    step = workflow.get_step_ledger()["steps"][0]
    assert workflow.get_step_ledger()["preparedArtifactRefs"] == [
        "prepared-context://objective/objective-artifact",
        "artifact://objective-artifact",
        "prepared-context://steps/implement/step-artifact",
        "artifact://step-artifact",
    ]
    assert first_ref == second_ref == "artifact://runtime/checkpoint"
    assert step["stateCheckpointRef"] == "artifact://runtime/checkpoint"
    assert step["recoveryPreservation"]["eligible"] is True
    assert step["recoveryPreservation"]["reason"] == "complete"


@pytest.mark.asyncio
async def test_run_routes_step_checkpoint_create_through_activity_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    captured: dict[str, Any] = {}
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )

    async def fake_execute_activity(
        activity: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured["activity"] = activity
        captured["payload"] = payload
        captured["kwargs"] = kwargs
        return {
            "checkpointRef": "artifact://checkpoint/created",
            "checkpointId": (
                "wf-run-1:run-1:implement:execution:1:checkpoint:after_execution"
            ),
            "contentType": STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
            "workspaceKind": "git_patch",
            "diagnosticRefs": [],
            "idempotencyKey": (
                "wf-run-1:run-1:implement:execution:1:checkpoint:after_execution"
            ),
        }

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    identity = StepExecutionIdentityModel(
        workflowId="wf-run-1",
        runId="run-1",
        logicalStepId="implement",
        executionOrdinal=1,
    )

    result = await workflow._create_step_checkpoint_via_activity(
        identity=identity,
        boundary="after_execution",
        task_input_snapshot_ref="artifact://task-input",
        workspace={
            "kind": "git_patch",
            "baseCommit": "abc123",
            "patchRef": "artifact://patch",
            "createdAt": "2026-06-13T12:00:00+00:00",
        },
        created_at=now,
        plan_digest="sha256:plan",
        step_outputs={"summaryRef": "artifact://summary"},
    )

    assert result["checkpointRef"] == "artifact://checkpoint/created"
    assert captured["activity"] == "step_checkpoint.create"
    assert captured["payload"]["workspace"]["patchRef"] == "artifact://patch"
    assert captured["payload"]["idempotencyKey"].endswith(
        ":checkpoint:after_execution"
    )
    step = workflow.get_step_ledger()["steps"][0]
    assert step["stepCheckpointRef"] == "artifact://checkpoint/created"
    assert step.get("stateCheckpointRef") is None
    assert step["refs"]["latestStepExecutionCheckpointRef"] == (
        "artifact://checkpoint/created"
    )
    assert step["refs"]["stepExecutionCheckpointRefs"] == [
        "artifact://checkpoint/created"
    ]
    assert step["refs"]["checkpointRefsByBoundary"] == {
        "after_execution": "artifact://checkpoint/created"
    }
    assert workflow._step_checkpoint_refs_by_boundary["implement"] == {
        "after_execution": "artifact://checkpoint/created"
    }
    assert "implement" not in workflow._step_checkpoint_refs
    assert "workspacePath" not in captured["payload"]
    assert "diff" not in json.dumps(captured["payload"], sort_keys=True)


@pytest.mark.asyncio
async def test_run_records_canonical_boundary_checkpoint_and_manifest_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_module.RUN_CANONICAL_STEP_CHECKPOINTS_PATCH,
            run_module.RUN_STEP_EXECUTION_MANIFEST_PATCH,
        },
    )
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    workflow._input_ref = "artifact://task-input"
    workflow._plan_ref = "artifact://plan"
    workflow._prepared_artifact_refs = ["artifact://prepared"]
    workflow._step_workspace_capture_inputs["implement"] = {
        "workspacePath": "/work/agent_jobs/run-1/repo",
        "baseCommit": "abc123",
        "kind": "git_patch",
    }
    captured: list[dict[str, Any]] = []
    manifest_writes: list[dict[str, Any]] = []

    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "implement",
        updated_at=now,
        summary="Implementing",
    )

    async def fake_execute_activity(
        activity: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured.append({"activity": activity, "payload": payload, "kwargs": kwargs})
        if activity == "workspace.capture_checkpoint":
            boundary = payload["boundary"]
            return {
                "status": "captured",
                "workspace": {
                    "kind": "git_patch",
                    "baseCommit": payload["baseCommit"],
                    "patchRef": f"artifact://patch/{boundary}",
                    "manifestRef": f"artifact://patch-manifest/{boundary}",
                    "createdAt": "2026-06-13T12:00:00+00:00",
                },
                "diagnosticRefs": [f"artifact://patch-manifest/{boundary}"],
            }
        assert activity == "step_checkpoint.create"
        boundary = payload["boundary"]
        return {
            "checkpointRef": f"artifact://checkpoint/{boundary}",
            "checkpointId": payload["idempotencyKey"],
            "contentType": STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
            "workspaceKind": payload["workspace"]["kind"],
            "diagnosticRefs": [],
            "idempotencyKey": payload["idempotencyKey"],
        }

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        manifest_writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"artifact://manifest/{len(manifest_writes)}"

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)

    await workflow._record_canonical_step_checkpoint(
        "implement",
        boundary="before_execution",
        updated_at=now,
    )
    await workflow._record_canonical_step_checkpoint(
        "implement",
        boundary="after_execution",
        updated_at=now,
        step_outputs={"summaryRef": "artifact://summary"},
    )
    await workflow._record_step_execution_manifest(
        "implement",
        phase="start",
        updated_at=now,
        reason="initial_execution",
    )

    assert [(call["activity"], call["payload"]["boundary"]) for call in captured] == [
        ("workspace.capture_checkpoint", "before_execution"),
        ("step_checkpoint.create", "before_execution"),
        ("workspace.capture_checkpoint", "after_execution"),
        ("step_checkpoint.create", "after_execution"),
    ]
    create_calls = [
        call for call in captured if call["activity"] == "step_checkpoint.create"
    ]
    assert create_calls[0]["payload"]["taskInputSnapshotRef"] == "artifact://task-input"
    assert create_calls[0]["payload"]["planRef"] == "artifact://plan"
    assert create_calls[0]["payload"]["preparedInputRefs"] == ["artifact://prepared"]
    assert create_calls[0]["payload"]["workspace"] == {
        "kind": "git_patch",
        "baseCommit": "abc123",
        "patchRef": "artifact://patch/before_execution",
        "manifestRef": "artifact://patch-manifest/before_execution",
        "createdAt": "2026-06-13T12:00:00+00:00",
    }
    assert create_calls[1]["payload"]["workspace"]["patchRef"] == (
        "artifact://patch/after_execution"
    )
    assert create_calls[1]["payload"]["diagnosticRefs"] == [
        "artifact://patch-manifest/after_execution"
    ]
    step = workflow.get_step_ledger()["steps"][0]
    assert step["refs"]["latestStepExecutionCheckpointRef"] == (
        "artifact://checkpoint/after_execution"
    )
    assert step["refs"]["stepExecutionCheckpointRefs"] == [
        "artifact://checkpoint/before_execution",
        "artifact://checkpoint/after_execution",
    ]
    assert step["refs"]["checkpointRefsByBoundary"] == {
        "before_execution": "artifact://checkpoint/before_execution",
        "after_execution": "artifact://checkpoint/after_execution",
    }
    workspace = manifest_writes[-1]["payload"]["workspace"]
    assert workspace["checkpointBeforeRef"] == (
        "artifact://checkpoint/before_execution"
    )
    assert workspace["checkpointAfterRef"] == "artifact://checkpoint/after_execution"
    assert "diff" not in json.dumps(
        [call["payload"] for call in captured],
        sort_keys=True,
    ).lower()


@pytest.mark.asyncio
async def test_run_records_pre_execution_checkpoint_from_node_workspace_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_module.RUN_CANONICAL_STEP_CHECKPOINTS_PATCH,
            run_module.RUN_MANAGED_CHECKPOINT_AUTHORITY_PATCH,
        },
    )
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    captured: list[dict[str, Any]] = []

    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "implement",
        updated_at=now,
        summary="Implementing",
    )
    workflow._record_step_workspace_capture_input(
        "implement",
        {
            "workspaceRoot": "/work/agent_jobs/run-1/repo",
            "baseCommit": "abc123",
        },
    )

    async def fake_execute_activity(
        activity: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured.append({"activity": activity, "payload": payload, "kwargs": kwargs})
        if activity == "workspace.capture_checkpoint":
            return {
                "status": "captured",
                "workspace": {
                    "kind": "git_patch",
                    "baseCommit": payload["baseCommit"],
                    "patchRef": "artifact://patch/before_execution",
                    "manifestRef": "artifact://patch-manifest/before_execution",
                    "createdAt": "2026-06-13T12:00:00+00:00",
                },
                "diagnosticRefs": ["artifact://patch-manifest/before_execution"],
            }
        assert activity == "step_checkpoint.create"
        return {
            "checkpointRef": "artifact://checkpoint/before_execution",
            "checkpointId": payload["idempotencyKey"],
            "contentType": STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
            "workspaceKind": payload["workspace"]["kind"],
            "diagnosticRefs": [],
            "idempotencyKey": payload["idempotencyKey"],
        }

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)

    result = await workflow._record_canonical_step_checkpoint(
        "implement",
        boundary="before_execution",
        updated_at=now,
    )

    assert result == "artifact://checkpoint/before_execution"
    assert [(call["activity"], call["payload"]["boundary"]) for call in captured] == [
        ("workspace.capture_checkpoint", "before_execution"),
        ("step_checkpoint.create", "before_execution"),
    ]
    assert captured[0]["payload"]["workspacePath"] == "/work/agent_jobs/run-1/repo"
    assert captured[1]["payload"]["workspace"]["patchRef"] == (
        "artifact://patch/before_execution"
    )


@pytest.mark.asyncio
async def test_run_degrades_managed_checkpoint_without_sandbox_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_module.RUN_CANONICAL_STEP_CHECKPOINTS_PATCH,
            run_module.RUN_MANAGED_CHECKPOINT_AUTHORITY_PATCH,
        },
    )
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running("implement", updated_at=now, summary="Implementing")
    workflow._record_step_workspace_capture_input(
        "implement",
        {
            "agentKind": "managed",
            "agentId": "codex_cli",
            "workspaceRoot": "/work/agent_jobs/managed-run-1/repo",
            "baseCommit": "abc123",
            "checkpointKind": "git_patch",
        },
    )

    async def unexpected_activity(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("managed workspace must not reach a sandbox activity")

    monkeypatch.setattr(run_module.workflow, "execute_activity", unexpected_activity)

    result = await workflow._record_canonical_step_checkpoint(
        "implement", boundary="after_execution", updated_at=now
    )

    assert result is None
    assert workflow._step_checkpoint_capture_outcomes["implement"] == {
        "status": "unsupported",
        "failureCode": "CHECKPOINT_CAPABILITY_UNSUPPORTED",
        "boundary": "after_execution",
        "captureAuthority": "managed_runtime",
        "captureActivity": None,
        "capabilityCriticality": "recoverability_only",
    }
    assert workflow._step_workspace_capture_inputs["implement"][
        "captureAuthority"
    ] == "managed_runtime"


@pytest.mark.asyncio
async def test_managed_checkpoint_capability_gap_reaches_finalization_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_module.RUN_CANONICAL_STEP_CHECKPOINTS_PATCH,
            run_module.RUN_MANAGED_CHECKPOINT_AUTHORITY_PATCH,
        },
    )
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running("implement", updated_at=now, summary="Implementing")
    workflow._record_step_workspace_capture_input(
        "implement",
        {
            "agentKind": "managed",
            "agentId": "codex_cli",
            "workspaceRoot": "/work/agent_jobs/managed-run-1/repo",
            "baseCommit": "abc123",
        },
    )

    async def unexpected_activity(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("managed workspace must not reach a sandbox activity")

    monkeypatch.setattr(run_module.workflow, "execute_activity", unexpected_activity)

    await workflow._finalize_after_execution_checkpoint("implement", updated_at=now)

    step = workflow.get_step_ledger()["steps"][0]
    assert step["finalizationOutcome"] == {
        "status": "unsupported",
        "phase": "after_execution_checkpoint",
        "criticality": "recoverability_only",
        "failureCode": "CHECKPOINT_CAPABILITY_UNSUPPORTED",
        "terminalFailureCode": None,
        "retryCount": 0,
        "checkpointRef": None,
        "message": "Checkpoint capture is unsupported by this runtime.",
        "updatedAt": "2026-06-13T12:00:00Z",
    }


@pytest.mark.asyncio
async def test_managed_checkpoint_defers_until_runtime_locator_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 7, 14, 5, 25, tzinfo=UTC)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "node-1", "inputs": {"title": "Investigate"}}],
        dependency_map={"node-1": []},
        updated_at=now,
    )
    workflow._mark_step_running("node-1", updated_at=now, summary="Investigating")
    workflow._record_step_workspace_capture_input(
        "node-1",
        {
            "agentKind": "managed",
            "agentId": "codex_cli",
        },
    )

    async def unexpected_activity(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("capture must wait for the AgentRun workspace locator")

    monkeypatch.setattr(run_module.workflow, "execute_activity", unexpected_activity)

    checkpoint_ref = await workflow._record_canonical_step_checkpoint(
        "node-1", boundary="after_prepare", updated_at=now
    )

    assert checkpoint_ref is None
    assert workflow._step_checkpoint_capture_outcomes["node-1"] == {
        "status": "deferred",
        "failureCode": "CHECKPOINT_WORKSPACE_LOCATOR_UNAVAILABLE",
        "boundary": "after_prepare",
        "captureAuthority": "managed_runtime",
        "captureActivity": "agent_runtime.capture_workspace_checkpoint",
        "capabilityCriticality": "recoverability_only",
    }


@pytest.mark.asyncio
async def test_managed_checkpoint_locator_guard_replays_previous_activity_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id
        != run_module.RUN_MANAGED_CHECKPOINT_LOCATOR_GUARD_PATCH,
    )
    workflow = MoonMindRunWorkflow()
    workflow._record_step_workspace_capture_input(
        "node-1",
        {
            "agentKind": "managed",
            "agentId": "codex_cli",
        },
    )
    captured: list[dict[str, Any]] = []

    async def previous_capture_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append({"activity": activity_type, "payload": payload})
        return {
            "status": "captured",
            "workspace": {"kind": "worktree_archive"},
            "diagnosticRefs": [],
        }

    monkeypatch.setattr(
        run_module.workflow,
        "execute_activity",
        previous_capture_activity,
    )
    identity = StepExecutionIdentityModel(
        workflowId="workflow-1",
        runId="run-1",
        logicalStepId="node-1",
        executionOrdinal=1,
    )

    await workflow._capture_canonical_step_checkpoint_workspace(
        "node-1", identity=identity, boundary="after_prepare"
    )

    assert [call["activity"] for call in captured] == [
        "agent_runtime.capture_workspace_checkpoint"
    ]
    assert "workspaceLocator" not in captured[0]["payload"]


def test_run_derives_managed_authority_from_agent_id() -> None:
    workflow = MoonMindRunWorkflow()

    workflow._record_step_workspace_capture_input(
        "implement",
        {
            "agentId": "codex_cli",
            "workspaceRoot": "/work/agent_jobs/managed-run-1/repo",
            "baseCommit": "abc123",
        },
    )

    assert workflow._step_workspace_capture_inputs["implement"][
        "captureAuthority"
    ] == "managed_runtime"


def test_run_records_capability_snapshot_without_identity_checkpoint_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id == run_module.RUN_RUNTIME_EXECUTION_CAPABILITIES_PATCH,
    )
    workflow = MoonMindRunWorkflow()

    workflow._record_step_workspace_capture_input(
        "implement",
        {
            "agentKind": "external",
            "agentId": "jules",
            "workspaceRoot": "/provider/workspace",
            "baseCommit": "abc123",
            "checkpointKind": "git_patch",
            "checkpointCriticality": "required",
        },
    )

    capture = workflow._step_workspace_capture_inputs["implement"]
    assert capture["captureAuthority"] == "external_provider"
    assert capture["criticality"] == "unsupported"
    assert capture["runtimeCapabilities"]["runtimeId"] == "jules"
    assert capture["kind"] == "git_patch"


@pytest.mark.asyncio
async def test_managed_checkpoint_replay_preserves_pre_authority_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id == run_module.RUN_CANONICAL_STEP_CHECKPOINTS_PATCH,
    )
    workflow = MoonMindRunWorkflow()
    workflow._record_step_workspace_capture_input(
        "implement",
        {
            "agentKind": "managed",
            "agentId": "codex_cli",
            "workspaceRoot": "/work/agent_jobs/managed-run-1/repo",
            "baseCommit": "abc123",
            "checkpointKind": "git_patch",
        },
    )
    captured: list[dict[str, Any]] = []

    async def fake_execute_activity(
        activity: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append({"activity": activity, "payload": payload})
        return {
            "status": "captured",
            "workspace": {"kind": payload["kind"]},
            "diagnosticRefs": [],
        }

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    identity = StepExecutionIdentityModel(
        workflowId="workflow-1",
        runId="run-1",
        logicalStepId="implement",
        executionOrdinal=1,
    )

    result = await workflow._capture_canonical_step_checkpoint_workspace(
        "implement", identity=identity, boundary="after_execution"
    )

    assert result == {"workspace": {"kind": "git_patch"}, "diagnosticRefs": []}
    assert [call["activity"] for call in captured] == [
        "workspace.capture_checkpoint"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("criticality", "expected_status", "attention_required"),
    [
        ("required", "failed", True),
        ("recoverability_only", "degraded", False),
    ],
)
async def test_after_execution_finalization_failure_preserves_primary_outcome(
    monkeypatch: pytest.MonkeyPatch,
    criticality: str,
    expected_status: str,
    attention_required: bool,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running("implement", updated_at=now, summary="Implementing")
    workflow._step_workspace_capture_inputs["implement"] = {
        "workspacePath": "/work/agent_jobs/run-1/repo",
        "criticality": criticality,
    }
    row = workflow._step_ledger_row_for("implement")
    assert row is not None
    row["artifacts"] = {
        "outputPrimary": "artifact://result",
        "runtimeMergedLogs": "artifact://logs",
        "runtimeDiagnostics": "artifact://diagnostics",
    }
    workflow._record_primary_execution_outcome(
        "implement",
        execution_result={"status": "COMPLETED"},
        result_status="COMPLETED",
        recorded_at=now,
    )

    async def fail_checkpoint(*args: Any, **kwargs: Any) -> None:
        try:
            raise ValueError(
                "checkpoint service unavailable password=raw-secret "
                "Authorization: Bearer raw-token"
            )
        except ValueError as cause:
            raise RuntimeError("Activity task failed") from cause

    monkeypatch.setattr(workflow, "_record_canonical_step_checkpoint", fail_checkpoint)
    await workflow._finalize_after_execution_checkpoint("implement", updated_at=now)

    step = workflow.get_step_ledger()["steps"][0]
    assert step["executionOutcome"] == {
        "status": "succeeded",
        "resultRef": "artifact://result",
        "outputRefs": [
            "artifact://result",
            "artifact://logs",
            "artifact://diagnostics",
        ],
        "diagnosticsRef": "artifact://diagnostics",
        "workspaceLocator": "/work/agent_jobs/run-1/repo",
        "recordedAt": "2026-06-13T12:00:00Z",
    }
    assert step["finalizationOutcome"]["status"] == expected_status
    assert step["finalizationOutcome"]["failureCode"] == (
        "FINALIZATION_CHECKPOINT_FAILED"
    )
    assert step["finalizationOutcome"]["phase"] == "after_execution_checkpoint"
    assert step["finalizationOutcome"]["message"] == (
        "checkpoint service unavailable password=[REDACTED] "
        "[REDACTED_AUTHORIZATION]"
    )
    assert "raw-secret" not in str(step["finalizationOutcome"])
    assert "raw-token" not in str(step["finalizationOutcome"])
    assert workflow._attention_required is attention_required
    assert workflow._summary.startswith("Execution succeeded; finalization failed")
    completion = workflow._determine_publish_completion(
        parameters={"publishMode": "none"}
    )
    if criticality == "required":
        assert completion[0] == "failed"
        assert completion[2] is True
    else:
        assert completion[0] == "success"


@pytest.mark.asyncio
async def test_after_execution_finalization_is_idempotent_and_does_not_execute_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running("implement", updated_at=now, summary="Implementing")
    workflow._step_workspace_capture_inputs["implement"] = {
        "workspacePath": "/work/agent_jobs/run-1/repo",
        "criticality": "required",
    }
    checkpoint_calls = 0

    async def checkpoint(*args: Any, **kwargs: Any) -> str:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        return "artifact://checkpoint/after_execution"

    monkeypatch.setattr(workflow, "_record_canonical_step_checkpoint", checkpoint)
    await workflow._finalize_after_execution_checkpoint("implement", updated_at=now)
    await workflow._finalize_after_execution_checkpoint("implement", updated_at=now)

    step = workflow.get_step_ledger()["steps"][0]
    assert checkpoint_calls == 1
    assert step["executionOrdinal"] == 1
    assert step["finalizationOutcome"]["status"] == "succeeded"
    assert step["finalizationOutcome"]["checkpointRef"] == (
        "artifact://checkpoint/after_execution"
    )


@pytest.mark.asyncio
async def test_publish_none_skips_required_prepublication_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 7, 14, 7, 14, tzinfo=UTC)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "queue-issues", "inputs": {"title": "Queue issues"}}],
        dependency_map={"queue-issues": []},
        updated_at=now,
    )

    async def unexpected_checkpoint(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("publishMode none has no pre-publication boundary")

    monkeypatch.setattr(
        workflow,
        "_record_canonical_step_checkpoint",
        unexpected_checkpoint,
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)

    failed = await workflow._record_prepublication_checkpoint(
        "queue-issues",
        publish_mode="none",
        updated_at=now,
    )

    assert failed is False
    assert workflow._attention_required is False


@pytest.mark.asyncio
async def test_legacy_history_preserves_prepublication_checkpoint_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 7, 14, 7, 14, tzinfo=UTC)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "queue-issues", "inputs": {"title": "Queue issues"}}],
        dependency_map={"queue-issues": []},
        updated_at=now,
    )
    calls: list[str] = []

    async def checkpoint(
        _logical_step_id: str,
        *,
        boundary: str,
        updated_at: datetime,
    ) -> str:
        calls.append(boundary)
        return "artifact://checkpoint/before_publication"

    monkeypatch.setattr(workflow, "_record_canonical_step_checkpoint", checkpoint)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: False)

    failed = await workflow._record_prepublication_checkpoint(
        "queue-issues",
        publish_mode="none",
        updated_at=now,
    )

    assert failed is False
    assert calls == ["before_publication"]


@pytest.mark.asyncio
async def test_prepublication_checkpoint_failure_blocks_publish_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 7, 14, 7, 14, tzinfo=UTC)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "assess", "inputs": {"title": "Assess"}}],
        dependency_map={"assess": []},
        updated_at=now,
    )
    workflow._last_publish_repair_request = SimpleNamespace(agent_kind="managed")

    async def fail_checkpoint(*_args: Any, **_kwargs: Any) -> None:
        raise asyncio.QueueFull

    monkeypatch.setattr(workflow, "_record_canonical_step_checkpoint", fail_checkpoint)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)

    failed = await workflow._record_prepublication_checkpoint(
        "assess",
        publish_mode="pr",
        updated_at=now,
    )

    assert failed is True
    assert workflow._publish_status == "failed"
    assert workflow._publish_reason == (
        "Execution succeeded; finalization failed during the pre-publication checkpoint."
    )
    assert workflow.get_step_ledger()["steps"][0]["finalizationOutcome"]["message"] == (
        "QueueFull"
    )
    assert workflow._publish_repair_is_available(parameters={"publishMode": "pr"}) is False
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: False)
    assert workflow._publish_repair_is_available(parameters={"publishMode": "pr"}) is True


@pytest.mark.asyncio
async def test_after_execution_finalization_retry_preserves_retry_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running("implement", updated_at=now, summary="Implementing")
    workflow._step_workspace_capture_inputs["implement"] = {
        "workspacePath": "/work/agent_jobs/run-1/repo",
        "criticality": "required",
    }
    checkpoint_calls = 0

    async def checkpoint(*args: Any, **kwargs: Any) -> str:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == 1:
            raise RuntimeError("checkpoint service unavailable")
        return "artifact://checkpoint/after_execution"

    monkeypatch.setattr(workflow, "_record_canonical_step_checkpoint", checkpoint)
    await workflow._finalize_after_execution_checkpoint("implement", updated_at=now)
    await workflow._finalize_after_execution_checkpoint("implement", updated_at=now)
    await workflow._finalize_after_execution_checkpoint("implement", updated_at=now)

    step = workflow.get_step_ledger()["steps"][0]
    assert checkpoint_calls == 2
    assert step["finalizationOutcome"]["status"] == "succeeded"
    assert step["finalizationOutcome"]["retryCount"] == 1
    assert step["finalizationOutcome"]["checkpointRef"] == (
        "artifact://checkpoint/after_execution"
    )
    assert workflow._attention_required is False
    assert workflow._summary == "Execution succeeded; finalization retry completed."


@pytest.mark.asyncio
async def test_run_uses_external_omnigent_identity_for_checkpoint_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id == run_module.RUN_CANONICAL_STEP_CHECKPOINTS_PATCH,
    )
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    node_inputs = {
        "agentKind": "External",
        "agentId": "Omnigent",
        "workspaceRoot": "/work/agent_jobs/run-1/repo",
        "baseCommit": "abc123",
    }
    captured: list[dict[str, Any]] = []

    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": node_inputs}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "implement",
        updated_at=now,
        summary="Implementing",
    )
    request = workflow._build_agent_execution_request(
        node_inputs=dict(node_inputs),
        node_id="implement",
        tool_name="external",
        step_execution=1,
    )
    workflow._record_step_workspace_capture_input("implement", node_inputs)

    async def fake_execute_activity(
        activity: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured.append({"activity": activity, "payload": payload, "kwargs": kwargs})
        if activity == "workspace.capture_checkpoint":
            assert payload["kind"] == "worktree_archive"
            assert payload["workspaceLocator"]["kind"] == "sandbox"
            return _managed_checkpoint_capture_result(payload)
        assert activity == "step_checkpoint.create"
        return _checkpoint_create_result(payload)

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)

    result = await workflow._record_canonical_step_checkpoint(
        "implement",
        boundary="before_execution",
        updated_at=now,
    )

    assert request.agent_kind == "external"
    assert request.agent_id == "Omnigent"
    assert workflow._step_external_agent_ids["implement"] == "omnigent"
    assert request.step_execution is not None
    assert request.step_execution.runtime_selection == {
        "runtimeId": "Omnigent",
        "agentKind": "external",
    }
    assert result == "artifact://checkpoint/before_execution"
    assert [(call["activity"], call["payload"]["boundary"]) for call in captured] == [
        ("workspace.capture_checkpoint", "before_execution"),
        ("step_checkpoint.create", "before_execution"),
    ]
    assert captured[0]["payload"]["kind"] == "worktree_archive"
    assert captured[1]["payload"]["workspace"]["kind"] == "worktree_archive"
    assert "patchRef" not in captured[1]["payload"]["workspace"]
    assert "archiveRef" in captured[1]["payload"]["workspace"]


def test_run_derives_external_omnigent_identity_from_runtime_selection() -> None:
    workflow = MoonMindRunWorkflow()

    workflow._record_step_workspace_capture_input(
        "implement",
        {
            "runtime": {"mode": "Omnigent"},
            "workspaceRoot": "/work/agent_jobs/run-1/repo",
            "baseCommit": "abc123",
        },
    )

    assert workflow._step_external_agent_ids["implement"] == "omnigent"
    capture = workflow._step_workspace_capture_inputs["implement"]
    assert capture["workspacePath"] == "/work/agent_jobs/run-1/repo"
    assert capture["baseCommit"] == "abc123"
    assert capture["captureAuthority"] == "moonmind_sandbox"
    assert capture["runtimeCapabilities"]["runtimeId"] == "omnigent"
    assert "kind" not in capture


@pytest.mark.asyncio
async def test_run_keeps_non_omnigent_and_legacy_checkpoint_capture_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id == run_module.RUN_CANONICAL_STEP_CHECKPOINTS_PATCH,
    )
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    captured: list[dict[str, Any]] = []

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {"id": "external-control", "inputs": {"title": "External control"}},
            {"id": "legacy", "inputs": {"title": "Legacy shape"}},
        ],
        dependency_map={"external-control": [], "legacy": []},
        updated_at=now,
    )
    for logical_step_id in ("external-control", "legacy"):
        workflow._mark_step_running(
            logical_step_id,
            updated_at=now,
            summary="Implementing",
        )
    workflow._record_step_workspace_capture_input(
        "external-control",
        {
            "agentKind": "external",
            "agentId": "jules",
            "workspaceRoot": "/work/agent_jobs/run-1/repo",
            "baseCommit": "abc123",
        },
    )
    workflow._record_step_workspace_capture_input(
        "legacy",
        {
            "workspaceRoot": "/work/agent_jobs/run-1/repo",
            "baseCommit": "abc123",
        },
    )

    async def fake_execute_activity(
        activity: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured.append({"activity": activity, "payload": payload, "kwargs": kwargs})
        if activity == "workspace.capture_checkpoint":
            return {
                "status": "captured",
                "workspace": {
                    "kind": payload["kind"],
                    "baseCommit": payload["baseCommit"],
                    "patchRef": f"artifact://patch/{payload['identity']['logicalStepId']}",
                    "manifestRef": (
                        f"artifact://patch-manifest/{payload['identity']['logicalStepId']}"
                    ),
                    "createdAt": "2026-06-13T12:00:00+00:00",
                },
                "diagnosticRefs": [
                    f"artifact://patch-manifest/{payload['identity']['logicalStepId']}"
                ],
            }
        assert activity == "step_checkpoint.create"
        return _checkpoint_create_result(payload)

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)

    await workflow._record_canonical_step_checkpoint(
        "external-control",
        boundary="before_execution",
        updated_at=now,
    )
    await workflow._record_canonical_step_checkpoint(
        "legacy",
        boundary="before_execution",
        updated_at=now,
    )

    capture_kinds = [
        call["payload"]["kind"]
        for call in captured
        if call["activity"] == "workspace.capture_checkpoint"
    ]
    # A newly recognized external runtime cannot silently enter local capture;
    # only the explicit legacy sandbox path retains its replay behavior.
    assert capture_kinds == ["git_patch"]


@pytest.mark.asyncio
async def test_run_skips_no_capture_ephemeral_checkpoint_for_pre_emit_histories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id == run_module.RUN_CANONICAL_STEP_CHECKPOINTS_PATCH,
    )
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    workflow._input_ref = "artifact://task-input"
    workflow._plan_ref = "artifact://plan"

    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "implement",
        updated_at=now,
        summary="Implementing",
    )

    async def fake_execute_activity(
        activity: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        raise AssertionError(f"unexpected activity: {activity} {payload}")

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)

    result = await workflow._record_canonical_step_checkpoint(
        "implement",
        boundary="after_prepare",
        updated_at=now,
    )

    assert result is None


@pytest.mark.asyncio
async def test_run_no_capture_ephemeral_checkpoint_skips_previous_retry_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_module.RUN_CANONICAL_STEP_CHECKPOINTS_PATCH,
            run_module.RUN_EMIT_EPHEMERAL_STEP_CHECKPOINTS_PATCH,
        },
    )
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    workflow._input_ref = "artifact://task-input"
    workflow._plan_ref = "artifact://plan"
    captured: list[dict[str, Any]] = []

    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "implement",
        updated_at=now,
        summary="Implementing",
    )
    workflow._previous_step_checkpoint_refs["implement"] = (
        "artifact://checkpoint/after_prepare"
    )

    async def fake_execute_activity(
        activity: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append({"activity": activity, "payload": payload})
        raise AssertionError(f"unexpected activity: {activity}")

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)

    result = await workflow._record_canonical_step_checkpoint(
        "implement",
        boundary="before_execution",
        updated_at=now,
    )

    assert result is None
    assert captured == []


@pytest.mark.asyncio
async def test_run_skips_no_capture_ephemeral_checkpoint_when_emit_patch_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_module.RUN_CANONICAL_STEP_CHECKPOINTS_PATCH,
            run_module.RUN_EMIT_EPHEMERAL_STEP_CHECKPOINTS_PATCH,
        },
    )
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    workflow._input_ref = "artifact://task-input"
    workflow._plan_ref = "artifact://plan"
    captured: list[dict[str, Any]] = []

    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "implement",
        updated_at=now,
        summary="Implementing",
    )

    async def fake_execute_activity(
        activity: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append({"activity": activity, "payload": payload})
        raise AssertionError(f"unexpected activity: {activity}")

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)

    result = await workflow._record_canonical_step_checkpoint(
        "implement",
        boundary="after_prepare",
        updated_at=now,
    )

    assert result is None
    assert captured == []


@pytest.mark.asyncio
async def test_run_skips_captured_ephemeral_checkpoint_for_pre_emit_histories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id == run_module.RUN_CANONICAL_STEP_CHECKPOINTS_PATCH,
    )
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    workflow._input_ref = "artifact://task-input"
    workflow._plan_ref = "artifact://plan"
    workflow._step_workspace_capture_inputs["implement"] = {
        "workspacePath": "/work/agent_jobs/run-1/repo",
        "kind": "worktree_archive",
    }
    captured: list[dict[str, Any]] = []

    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "implement",
        updated_at=now,
        summary="Implementing",
    )

    async def fake_execute_activity(
        activity: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append({"activity": activity, "payload": payload})
        if activity == "workspace.capture_checkpoint":
            return {
                "status": "captured",
                "workspace": {
                    "kind": "ephemeral_workspace_ref",
                    "workspaceRef": "artifact://diagnostic/ephemeral",
                },
                "diagnosticRefs": ["artifact://diagnostic/ephemeral"],
            }
        raise AssertionError(f"unexpected activity: {activity}")

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)

    result = await workflow._record_canonical_step_checkpoint(
        "implement",
        boundary="after_execution",
        updated_at=now,
    )

    assert result is None
    assert [call["activity"] for call in captured] == ["workspace.capture_checkpoint"]
    step = workflow.get_step_ledger()["steps"][0]
    assert step.get("stepCheckpointRef") is None
    assert step["refs"]["stepExecutionCheckpointRefs"] == []
    assert step["refs"]["checkpointRefsByBoundary"] == {}


@pytest.mark.asyncio
async def test_run_skips_captured_ephemeral_checkpoint_when_emit_patch_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_module.RUN_CANONICAL_STEP_CHECKPOINTS_PATCH,
            run_module.RUN_EMIT_EPHEMERAL_STEP_CHECKPOINTS_PATCH,
        },
    )
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    workflow._input_ref = "artifact://task-input"
    workflow._plan_ref = "artifact://plan"
    workflow._step_workspace_capture_inputs["implement"] = {
        "workspacePath": "/work/agent_jobs/run-1/repo",
        "kind": "worktree_archive",
    }
    captured: list[dict[str, Any]] = []

    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "implement",
        updated_at=now,
        summary="Implementing",
    )

    async def fake_execute_activity(
        activity: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append({"activity": activity, "payload": payload})
        if activity == "workspace.capture_checkpoint":
            return {
                "status": "captured",
                "workspace": {
                    "kind": "ephemeral_workspace_ref",
                    "workspaceRef": "artifact://diagnostic/ephemeral",
                },
                "diagnosticRefs": ["artifact://diagnostic/ephemeral"],
            }
        raise AssertionError(f"unexpected activity: {activity}")

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)

    result = await workflow._record_canonical_step_checkpoint(
        "implement",
        boundary="after_execution",
        updated_at=now,
    )

    assert result is None
    assert [call["activity"] for call in captured] == ["workspace.capture_checkpoint"]
    step = workflow.get_step_ledger()["steps"][0]
    assert step.get("stepCheckpointRef") is None
    assert step["refs"]["stepExecutionCheckpointRefs"] == []
    assert step["refs"]["checkpointRefsByBoundary"] == {}


@pytest.mark.asyncio
async def test_run_skips_canonical_boundary_checkpoint_when_unpatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    captured: list[str] = []

    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "implement",
        updated_at=now,
        summary="Implementing",
    )

    async def fake_execute_activity(
        activity: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append(activity)
        return {}

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)

    result = await workflow._record_canonical_step_checkpoint(
        "implement",
        boundary="before_execution",
        updated_at=now,
    )

    assert result is None
    assert captured == []


def test_run_marks_completed_step_without_checkpoint_ineligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 35, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "plan", "inputs": {"title": "Plan"}}],
        dependency_map={"plan": []},
        updated_at=now,
    )
    workflow._mark_step_terminal(
        "plan",
        status="completed",
        updated_at=now,
        summary="Planned",
    )
    workflow._record_step_result_evidence(
        "plan",
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "outputPrimaryRef": "artifact://plan-output",
            },
        },
        updated_at=now,
    )
    workflow._record_step_checkpoint_evidence("plan", updated_at=now)

    step = workflow.get_step_ledger()["steps"][0]
    assert step["recoveryPreservation"]["eligible"] is False
    assert step["recoveryPreservation"]["reason"] == "missing_state_checkpoint"
    assert step.get("stateCheckpointRef") is None


def test_run_clears_stale_checkpoint_ref_before_successful_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 36, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running("implement", updated_at=now, summary="Implementing")
    workflow._record_step_result_evidence(
        "implement",
        execution_result={
            "status": "FAILED",
            "outputs": {
                "outputPrimaryRef": "artifact://failed-output",
                "stateCheckpointRef": "artifact://stale-checkpoint",
            },
        },
        updated_at=now,
    )
    workflow._mark_step_terminal(
        "implement",
        status="failed",
        updated_at=now,
        summary="Failed",
        last_error="execution_error",
    )

    workflow._mark_step_running("implement", updated_at=now, summary="Retrying")
    workflow._record_step_result_evidence(
        "implement",
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "outputPrimaryRef": "artifact://successful-output",
            },
        },
        updated_at=now,
    )
    workflow._mark_step_terminal(
        "implement",
        status="completed",
        updated_at=now,
        summary="Implemented",
    )
    workflow._record_step_checkpoint_evidence("implement", updated_at=now)

    step = workflow.get_step_ledger()["steps"][0]
    assert step.get("stateCheckpointRef") is None
    assert step["artifacts"]["outputPrimary"] == "artifact://successful-output"
    assert step["recoveryPreservation"]["eligible"] is False
    assert step["recoveryPreservation"]["reason"] == "missing_state_checkpoint"


def test_run_reads_nested_workload_metadata_from_legacy_workload_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "workload-step",
                "tool": {"type": "skill", "name": "container.run_workload"},
                "inputs": {"title": "Run workload"},
            }
        ],
        dependency_map={"workload-step": []},
        updated_at=now,
    )
    workflow._record_step_result_evidence(
        "workload-step",
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "workloadResult": {
                    "metadata": {
                        "stdout": "large bounded stdout must not be ledger metadata",
                        "workload": {
                            "agentRunId": "wf-legacy",
                            "stepId": "workload-step",
                            "profileId": "local-python",
                        },
                    }
                }
            },
        },
        updated_at=now,
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["refs"]["agentRunId"] == "wf-legacy"
    assert step["workload"]["agentRunId"] == "wf-legacy"
    assert step["workload"]["stepId"] == "workload-step"
    assert step["workload"]["profileId"] == "local-python"
    assert "stdout" not in step["workload"]

@pytest.mark.asyncio
async def test_run_execution_stage_propagates_agent_child_cancellation_under_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    plan_payload = _approval_policy_plan_payload()
    plan_payload["policy"]["failure_mode"] = "CONTINUE"
    artifact_sequence = iter(range(1, 20))

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "resilience.compile_policy":
            return _resilience_policy_compile_result(payload)
        if activity_type == "provider_profile.list":
            return {"profiles": []}
        if activity_type == "artifact.create":
            if str(payload.get("name") or "").startswith(
                "reports/resilience_policy"
            ):
                return _resilience_policy_artifact_create_result()
            return (
                {"artifact_id": f"art_{next(artifact_sequence)}"},
                {"upload_url": "unused"},
            )
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: Any,
        **_kwargs: Any,
    ) -> Any:
        raise run_module.CancelledError("parent cancellation")

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            artifact_ref = getattr(payload, "artifact_ref", None)
            if artifact_ref == "art_plan_1":
                return json.dumps(plan_payload).encode("utf-8")
            if artifact_ref == "artifact://registry/1":
                return json.dumps(_registry_payload()).encode("utf-8")
        if activity_type == "artifact.write_complete":
            return {"ok": True}
        raise AssertionError(f"unexpected typed activity: {activity_type}")

    monkeypatch.setattr(
        run_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)

    with pytest.raises(run_module.CancelledError, match="parent cancellation"):
        await workflow._run_execution_stage(
            parameters={"repo": "MoonLadderStudios/MoonMind"},
            plan_ref="art_plan_1",
        )

    step = workflow.get_step_ledger()["steps"][0]
    assert step["status"] == "awaiting_external"


def test_agent_child_cancellation_propagation_preserves_pre_patch_histories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancellation = run_module.CancelledError("parent cancellation")
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: False)
    assert not MoonMindRunWorkflow._should_propagate_agent_child_cancellation(
        cancellation
    )

    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: (
            patch_id == run_module.RUN_PROPAGATE_AGENT_CHILD_CANCELLATION_PATCH
        ),
    )
    assert MoonMindRunWorkflow._should_propagate_agent_child_cancellation(cancellation)


@pytest.mark.asyncio
async def test_run_execution_stage_marks_step_reviewing_and_records_passed_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    review_snapshots: list[dict[str, Any]] = []
    written_review_payloads: list[dict[str, Any]] = []
    review_artifact_ids = iter(("art_review_1",))
    step_execution_artifact_ids = iter(
        ("art_attempt_1", "art_attempt_1_terminal")
    )

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "resilience.compile_policy":
            return _resilience_policy_compile_result(payload)
        if activity_type == "provider_profile.list":
            return {"profiles": []}
        if activity_type == "artifact.create":
            if str(payload.get("name") or "").startswith("reports/resilience_policy"):
                return _resilience_policy_artifact_create_result()
            if str(payload.get("name") or "").startswith("reports/step_execution"):
                return (
                    {"artifact_id": next(step_execution_artifact_ids)},
                    {"upload_url": "unused"},
                )
            return ({"artifact_id": next(review_artifact_ids)}, {"upload_url": "unused"})
        if activity_type == "step.review":
            step = workflow.get_step_ledger()["steps"][0]
            review_snapshots.append(step)
            return {
                "verdict": "PASS",
                "confidence": 0.91,
                "feedback": None,
                "issues": [],
            }
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: Any,
        **_kwargs: Any,
    ) -> Any:
        return {
            "summary": "Patch applied cleanly",
            "metadata": {"outputSummaryRef": "art_summary_1"},
            "output_refs": [],
        }

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            artifact_ref = getattr(payload, "artifact_ref", None)
            if artifact_ref == "art_plan_1":
                return json.dumps(_approval_policy_plan_payload()).encode("utf-8")
            if artifact_ref == "artifact://registry/1":
                return json.dumps(_registry_payload()).encode("utf-8")
        if activity_type == "artifact.write_complete":
            if getattr(payload, "content_type", "") == (
                "application/vnd.moonmind.step-execution+json;version=1"
            ):
                written_review_payloads.append(
                    json.loads(payload.payload.decode("utf-8"))
                )
                return {"ok": True}
            written_review_payloads.append(json.loads(payload.payload.decode("utf-8")))
            return {"ok": True}
        raise AssertionError(f"unexpected typed activity: {activity_type}")

    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-run-review-1",
        run_id="run-review-1",
        task_queue="mm.workflow",
        search_attributes={"mm_owner_type": ["user"], "mm_owner_id": ["owner-1"]},
    )
    monkeypatch.setattr(run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    assert review_snapshots
    assert review_snapshots[0]["status"] == "reviewing"
    assert review_snapshots[0]["checks"] == [
        {
            "kind": "approval_policy",
            "status": "pending",
            "summary": "Structured review in progress",
            "retryCount": 0,
            "artifactRef": None,
        }
    ]
    step = workflow.get_step_ledger()["steps"][0]
    assert step["status"] == "completed"
    assert step["checks"][0] == {
        "kind": "approval_policy",
        "status": "passed",
        "summary": "Approved by structured review",
        "retryCount": 0,
        "artifactRef": "art_review_1",
        "gateResultRef": "art_review_1",
        "gateVerdict": "FULLY_IMPLEMENTED",
        "confidence": 0.91,
        "validatedRefs": {},
        "invalidatedRefs": [],
        "remainingWorkRef": None,
        "targetLogicalStepId": None,
        "workspacePolicyRecommendation": None,
        "recommendedNextAction": "advance",
        "invalid": False,
        "degraded": False,
    }
    review_payloads = [
        payload
        for payload in written_review_payloads
        if payload.get("schemaVersion") == "v1" and "verdict" in payload
    ]
    assert review_payloads[0]["verdict"] == "FULLY_IMPLEMENTED"
    assert review_payloads[0]["recommendedNextAction"] == "advance"
    attempt_payloads = [
        payload for payload in written_review_payloads if payload.get("stepExecutionId")
    ]
    assert attempt_payloads[0]["stepExecutionId"] == (
        "wf-run-review-1:run-review-1:apply-patch:execution:1"
    )
    assert attempt_payloads[-1]["checks"][0]["gateResultRef"] == "art_review_1"
    assert attempt_payloads[-1]["checks"][0]["gateVerdict"] == "FULLY_IMPLEMENTED"
    assert step["artifacts"]["stepExecutionManifestRef"] == "art_attempt_1_terminal"

@pytest.mark.asyncio
async def test_run_execution_stage_retries_failed_reviews_with_feedback_and_retry_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    written_review_payloads: list[dict[str, Any]] = []
    skill_inputs: list[dict[str, Any]] = []
    plan_payload = _approval_policy_plan_payload()
    plan_payload["policy"]["approval_policy"][
        "max_consecutive_no_progress_attempts"
    ] = 1
    review_artifact_ids = iter(("art_review_1", "art_review_2"))
    step_execution_artifact_ids = iter(
        (
            "art_attempt_1",
            "art_attempt_2",
            "art_attempt_2_terminal",
        )
    )
    review_verdicts = iter(
        (
            {
                "verdict": "FAIL",
                "confidence": 0.84,
                "feedback": "Tests still fail because the import is missing.",
                "issues": [
                    {
                        "severity": "error",
                        "description": "Missing import",
                        "evidence": "stderr tail",
                    }
                ],
                "remainingWorkRef": "art_remaining_work_1",
                "recommendedNextAction": "reattempt_current_step",
            },
            {
                "verdict": "PASS",
                "confidence": 0.93,
                "feedback": None,
                "issues": [],
            },
        )
    )

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "resilience.compile_policy":
            return _resilience_policy_compile_result(payload)
        if activity_type == "provider_profile.list":
            return {"profiles": []}
        if activity_type == "artifact.create":
            if str(payload.get("name") or "").startswith("reports/resilience_policy"):
                return _resilience_policy_artifact_create_result()
            if str(payload.get("name") or "").startswith("reports/step_execution"):
                return (
                    {"artifact_id": next(step_execution_artifact_ids)},
                    {"upload_url": "unused"},
                )
            return ({"artifact_id": next(review_artifact_ids)}, {"upload_url": "unused"})
        if activity_type == "step.review":
            return next(review_verdicts)
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_child_workflow(
        _workflow_type: str,
        request: Any,
        **_kwargs: Any,
    ) -> Any:
        skill_inputs.append({"instructions": request.instruction_ref})
        return {
            "summary": "Patch applied cleanly",
            "metadata": {
                "outputSummaryRef": f"art_summary_{len(skill_inputs)}",
                "stateCheckpointRef": f"art_checkpoint_{len(skill_inputs)}",
            },
            "output_refs": [],
        }

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            artifact_ref = getattr(payload, "artifact_ref", None)
            if artifact_ref == "art_plan_1":
                return json.dumps(plan_payload).encode("utf-8")
            if artifact_ref == "artifact://registry/1":
                return json.dumps(_registry_payload()).encode("utf-8")
        if activity_type == "artifact.write_complete":
            if getattr(payload, "content_type", "") == (
                "application/vnd.moonmind.step-execution+json;version=1"
            ):
                return {"ok": True}
            written_review_payloads.append(json.loads(payload.payload.decode("utf-8")))
            return {"ok": True}
        raise AssertionError(f"unexpected typed activity: {activity_type}")

    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-run-review-2",
        run_id="run-review-2",
        task_queue="mm.workflow",
        search_attributes={"mm_owner_type": ["user"], "mm_owner_id": ["owner-1"]},
    )
    monkeypatch.setattr(run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    assert len(skill_inputs) == 2
    assert "Tests still fail because the import is missing." not in skill_inputs[0]["instructions"]
    assert "Tests still fail because the import is missing." in skill_inputs[1]["instructions"]
    step = workflow.get_step_ledger()["steps"][0]
    assert step["executionOrdinal"] == 2
    assert step["status"] == "completed"
    assert step["checks"][0] == {
        "kind": "approval_policy",
        "status": "passed",
        "summary": "Approved after 1 retry",
        "retryCount": 1,
        "artifactRef": "art_review_2",
        "gateResultRef": "art_review_2",
        "gateVerdict": "FULLY_IMPLEMENTED",
        "confidence": 0.93,
        "validatedRefs": {},
        "invalidatedRefs": [],
        "remainingWorkRef": None,
        "targetLogicalStepId": None,
        "workspacePolicyRecommendation": None,
        "recommendedNextAction": "advance",
        "invalid": False,
        "degraded": False,
    }
    review_payloads = [
        payload
        for payload in written_review_payloads
        if payload.get("schemaVersion") == "v1" and "verdict" in payload
    ]
    assert review_payloads[0]["verdict"] == "ADDITIONAL_WORK_NEEDED"
    assert review_payloads[0]["remainingWorkRef"] == "art_remaining_work_1"
    assert review_payloads[0]["recommendedNextAction"] == "reattempt_current_step"
    assert review_payloads[1]["verdict"] == "FULLY_IMPLEMENTED"
    assert step["artifacts"]["stepExecutionManifestRef"] == "art_attempt_2_terminal"
    assert step["artifacts"]["stepExecutionManifestRefs"] == [
        "art_attempt_1",
        "art_attempt_2",
        "art_attempt_2_terminal",
    ]


@pytest.mark.asyncio
async def test_run_execution_stage_stops_downstream_handoff_when_gate_budget_exhausts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    invoked_skills: list[str] = []
    step_execution_payloads: list[dict[str, Any]] = []

    plan_payload = _approval_policy_plan_payload()
    plan_payload["policy"]["failure_mode"] = "CONTINUE"
    plan_payload["policy"]["approval_policy"]["max_review_attempts"] = 0
    plan_payload["nodes"] = [
        {
            "id": "implement",
            "tool": {"type": "agent_runtime", "name": "repo.apply_patch"},
            "inputs": {"targetRuntime": "codex_cli", "instructions": "Apply patch"},
            "options": {},
        },
        {
            "id": "publish",
            "tool": {"type": "agent_runtime", "name": "repo.publish"},
            "inputs": {"targetRuntime": "codex_cli", "instructions": "Publish"},
            "options": {},
        },
    ]
    plan_payload["edges"] = [{"from": "implement", "to": "publish"}]
    registry_payload = _registry_payload()
    registry_payload["skills"].append(
        {
            **registry_payload["skills"][0],
            "name": "repo.publish",
        }
    )
    artifact_ids = iter(
        (
            "art_attempt_1",
            "art_review_1",
            "art_attempt_1_terminal",
        )
    )

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "resilience.compile_policy":
            return _resilience_policy_compile_result(payload)
        if activity_type == "provider_profile.list":
            return {"profiles": []}
        if activity_type == "artifact.create":
            if str(payload.get("name") or "").startswith("reports/resilience_policy"):
                return _resilience_policy_artifact_create_result()
            return ({"artifact_id": next(artifact_ids)}, {"upload_url": "unused"})
        if activity_type == "step.review":
            return {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "confidence": "medium",
                "feedback": "Tests still fail.",
                "issues": [],
                "remainingWorkRef": "art_remaining_work_1",
                "recommendedNextAction": "needs_human",
            }
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: Any,
        **kwargs: Any,
    ) -> Any:
        invoked_skills.append(str(kwargs["id"]).rsplit(":agent:", 1)[1])
        return {
            "summary": "Patch applied",
            "metadata": {"outputSummaryRef": "art_summary_1"},
            "output_refs": [],
        }

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            artifact_ref = getattr(payload, "artifact_ref", None)
            if artifact_ref == "art_plan_1":
                return json.dumps(plan_payload).encode("utf-8")
            if artifact_ref == "artifact://registry/1":
                return json.dumps(registry_payload).encode("utf-8")
        if activity_type == "artifact.write_complete":
            decoded = json.loads(payload.payload.decode("utf-8"))
            if getattr(payload, "content_type", "") == (
                "application/vnd.moonmind.step-execution+json;version=1"
            ):
                step_execution_payloads.append(decoded)
            return {"ok": True}
        raise AssertionError(f"unexpected typed activity: {activity_type}")

    monkeypatch.setattr(
        run_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    assert invoked_skills == ["implement"]
    step = workflow.get_step_ledger()["steps"][0]
    assert step["status"] == "failed"
    assert workflow._publish_status == "not_required"
    assert workflow._publish_reason == (
        "Structured gate stopped before downstream handoff."
    )
    failed_check = step["checks"][0]
    assert failed_check["gateResultRef"] == "art_review_1"
    assert failed_check["gateVerdict"] == "ADDITIONAL_WORK_NEEDED"
    assert failed_check["confidence"] == "medium"
    assert failed_check["remainingWorkRef"] == "art_remaining_work_1"
    assert failed_check["recommendedNextAction"] == "needs_human"
    assert step_execution_payloads[-1]["terminalDisposition"] == (
        "failed_with_remaining_work"
    )
    assert step_execution_payloads[-1]["checks"][0]["gateResultRef"] == "art_review_1"
    assert step_execution_payloads[-1]["checks"][0]["gateVerdict"] == (
        "ADDITIONAL_WORK_NEEDED"
    )
    assert step_execution_payloads[-1]["checks"][0]["remainingWorkRef"] == (
        "art_remaining_work_1"
    )
    assert step_execution_payloads[-1]["budget"] == {
        "gate": "approval_policy",
        "maxAttempts": 1,
        "attemptsConsumed": 1,
        "maxExecutions": 1,
        "executionsConsumed": 1,
        "retriesConsumed": 0,
        "remainingExecutions": 0,
        "additionalStopDimension": {
            "type": "consecutive_no_progress_attempts",
            "limit": 1,
            "consumed": 1,
            "remaining": 0,
            "exhausted": True,
        },
        "stopRules": [
            "structured_gate_verdict_required",
            "accepted_output_evidence_required",
            "consecutive_no_progress_attempts_exhaustion_stops_before_publication",
            "budget_exhaustion_stops_before_publication",
        ],
        "exhausted": True,
        "gateVerdict": "ADDITIONAL_WORK_NEEDED",
        "recommendedNextAction": "needs_human",
    }


@pytest.mark.asyncio
async def test_run_execution_stage_stops_downstream_handoff_when_no_progress_budget_exhausts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    invoked_skills: list[str] = []
    step_execution_payloads: list[dict[str, Any]] = []

    plan_payload = _approval_policy_plan_payload()
    plan_payload["policy"]["failure_mode"] = "CONTINUE"
    plan_payload["policy"]["approval_policy"]["max_review_attempts"] = 2
    plan_payload["policy"]["approval_policy"][
        "max_consecutive_no_progress_attempts"
    ] = 1
    plan_payload["nodes"] = [
        {
            "id": "implement",
            "tool": {"type": "agent_runtime", "name": "repo.apply_patch"},
            "inputs": {"targetRuntime": "codex_cli", "instructions": "Apply patch"},
            "options": {},
        },
        {
            "id": "publish",
            "tool": {"type": "agent_runtime", "name": "repo.publish"},
            "inputs": {"targetRuntime": "codex_cli", "instructions": "Publish"},
            "options": {},
        },
    ]
    plan_payload["edges"] = [{"from": "implement", "to": "publish"}]
    registry_payload = _registry_payload()
    registry_payload["skills"].append(
        {
            **registry_payload["skills"][0],
            "name": "repo.publish",
        }
    )
    artifact_ids = iter(
        (
            "art_attempt_1",
            "art_review_1",
            "art_attempt_1_terminal",
        )
    )

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "resilience.compile_policy":
            return _resilience_policy_compile_result(payload)
        if activity_type == "provider_profile.list":
            return {"profiles": []}
        if activity_type == "artifact.create":
            if str(payload.get("name") or "").startswith("reports/resilience_policy"):
                return _resilience_policy_artifact_create_result()
            return ({"artifact_id": next(artifact_ids)}, {"upload_url": "unused"})
        if activity_type == "step.review":
            return {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "confidence": "medium",
                "feedback": "No material progress.",
                "issues": [],
                "remainingWorkRef": "art_remaining_work_1",
                "recommendedNextAction": "needs_human",
            }
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: Any,
        **kwargs: Any,
    ) -> Any:
        invoked_skills.append(str(kwargs["id"]).rsplit(":agent:", 1)[1])
        return {
            "summary": "Patch applied",
            "metadata": {"outputSummaryRef": "art_summary_1"},
            "output_refs": [],
        }

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            artifact_ref = getattr(payload, "artifact_ref", None)
            if artifact_ref == "art_plan_1":
                return json.dumps(plan_payload).encode("utf-8")
            if artifact_ref == "artifact://registry/1":
                return json.dumps(registry_payload).encode("utf-8")
        if activity_type == "artifact.write_complete":
            decoded = json.loads(payload.payload.decode("utf-8"))
            if getattr(payload, "content_type", "") == (
                "application/vnd.moonmind.step-execution+json;version=1"
            ):
                step_execution_payloads.append(decoded)
            return {"ok": True}
        raise AssertionError(f"unexpected typed activity: {activity_type}")

    monkeypatch.setattr(
        run_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    assert invoked_skills == ["implement"]
    step = workflow.get_step_ledger()["steps"][0]
    assert step["status"] == "failed"
    assert workflow._publish_status == "not_required"
    assert workflow._publish_reason == (
        "Structured gate stopped before downstream handoff."
    )
    budget = step_execution_payloads[-1]["budget"]
    assert step_execution_payloads[-1]["terminalDisposition"] == (
        "failed_with_remaining_work"
    )
    assert budget["maxAttempts"] == 3
    assert budget["attemptsConsumed"] == 1
    assert budget["remainingExecutions"] == 2
    assert budget["additionalStopDimension"] == {
        "type": "consecutive_no_progress_attempts",
        "limit": 1,
        "consumed": 1,
        "remaining": 0,
        "exhausted": True,
    }
    assert budget["exhausted"] is True
    assert budget["gateVerdict"] == "ADDITIONAL_WORK_NEEDED"


@pytest.mark.asyncio
async def test_run_execution_stage_continues_independent_nodes_after_gate_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    invoked_skills: list[str] = []
    step_execution_payloads: list[dict[str, Any]] = []

    plan_payload = _approval_policy_plan_payload()
    plan_payload["policy"]["failure_mode"] = "CONTINUE"
    plan_payload["policy"]["approval_policy"]["max_review_attempts"] = 0
    plan_payload["policy"]["approval_policy"]["skip_tool_types"] = ["repo.publish"]
    plan_payload["nodes"] = [
        {
            "id": "implement",
            "tool": {"type": "agent_runtime", "name": "repo.apply_patch"},
            "inputs": {"targetRuntime": "codex_cli", "instructions": "Apply patch"},
            "options": {},
        },
        {
            "id": "publish",
            "tool": {"type": "agent_runtime", "name": "repo.publish"},
            "inputs": {"targetRuntime": "codex_cli", "instructions": "Publish independent report"},
            "options": {},
        },
    ]
    plan_payload["edges"] = []
    registry_payload = _registry_payload()
    registry_payload["skills"].append(
        {
            **registry_payload["skills"][0],
            "name": "repo.publish",
        }
    )
    artifact_ids = iter(
        (
            "art_attempt_1",
            "art_review_1",
            "art_attempt_1_terminal",
            "art_attempt_2",
            "art_attempt_2_terminal",
        )
    )

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "resilience.compile_policy":
            return _resilience_policy_compile_result(payload)
        if activity_type == "provider_profile.list":
            return {"profiles": []}
        if activity_type == "artifact.create":
            if str(payload.get("name") or "").startswith("reports/resilience_policy"):
                return _resilience_policy_artifact_create_result()
            return ({"artifact_id": next(artifact_ids)}, {"upload_url": "unused"})
        if activity_type == "step.review":
            return {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "confidence": "medium",
                "feedback": "Tests still fail.",
                "issues": [],
                "remainingWorkRef": "art_remaining_work_1",
                "recommendedNextAction": "needs_human",
            }
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: Any,
        **kwargs: Any,
    ) -> Any:
        invoked_skills.append(str(kwargs["id"]).rsplit(":agent:", 1)[1])
        return {
            "summary": "Step complete",
            "metadata": {"outputSummaryRef": "art_summary_1"},
            "output_refs": [],
        }

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            artifact_ref = getattr(payload, "artifact_ref", None)
            if artifact_ref == "art_plan_1":
                return json.dumps(plan_payload).encode("utf-8")
            if artifact_ref == "artifact://registry/1":
                return json.dumps(registry_payload).encode("utf-8")
        if activity_type == "artifact.write_complete":
            decoded = json.loads(payload.payload.decode("utf-8"))
            if getattr(payload, "content_type", "") == (
                "application/vnd.moonmind.step-execution+json;version=1"
            ):
                step_execution_payloads.append(decoded)
            return {"ok": True}
        raise AssertionError(f"unexpected typed activity: {activity_type}")

    monkeypatch.setattr(
        run_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    assert invoked_skills == ["implement", "publish"]
    ledger = workflow.get_step_ledger()["steps"]
    assert ledger[0]["status"] == "failed"
    assert ledger[1]["status"] == "completed"
    assert workflow._publish_status == "not_required"
    assert workflow._publish_reason == (
        "Structured gate stopped before downstream handoff."
    )
    terminal_payloads = [
        payload
        for payload in step_execution_payloads
        if payload.get("terminalDisposition")
    ]
    assert [payload["logicalStepId"] for payload in terminal_payloads] == [
        "implement",
        "publish",
    ]
    assert terminal_payloads[0]["terminalDisposition"] == (
        "failed_with_remaining_work"
    )
    assert terminal_payloads[-1]["terminalDisposition"] == "accepted"


@pytest.mark.asyncio
async def test_run_execution_stage_retries_agent_runtime_reviews_with_feedback_in_instruction_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    written_review_payloads: list[dict[str, Any]] = []
    child_requests: list[Any] = []
    review_artifact_ids = iter(("art_review_1", "art_review_2"))
    step_execution_artifact_ids = iter(
        (
            "art_attempt_1",
            "art_attempt_2",
            "art_attempt_2_terminal",
        )
    )
    review_verdicts = iter(
        (
            {
                "verdict": "FAIL",
                "confidence": 0.84,
                "feedback": "Add the missing validation before retrying.",
                "issues": [],
            },
            {
                "verdict": "PASS",
                "confidence": 0.93,
                "feedback": None,
                "issues": [],
            },
        )
    )

    plan_payload = _approval_policy_plan_payload()
    plan_payload["nodes"] = [
        {
            "id": "delegate-agent",
            "tool": {"type": "agent_runtime", "name": "jules"},
            "inputs": {
                "targetRuntime": "jules",
                "instructions": "Implement the requested change.",
            },
            "options": {},
        }
    ]

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "resilience.compile_policy":
            return _resilience_policy_compile_result(payload)
        if activity_type == "provider_profile.list":
            return {"profiles": []}
        if activity_type == "artifact.create":
            if str(payload.get("name") or "").startswith("reports/resilience_policy"):
                return _resilience_policy_artifact_create_result()
            if str(payload.get("name") or "").startswith("reports/step_execution"):
                return (
                    {"artifact_id": next(step_execution_artifact_ids)},
                    {"upload_url": "unused"},
                )
            return ({"artifact_id": next(review_artifact_ids)}, {"upload_url": "unused"})
        if activity_type == "step.review":
            return next(review_verdicts)
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            artifact_ref = getattr(payload, "artifact_ref", None)
            if artifact_ref == "art_plan_1":
                return json.dumps(plan_payload).encode("utf-8")
        if activity_type == "artifact.write_complete":
            if getattr(payload, "content_type", "") == (
                "application/vnd.moonmind.step-execution+json;version=1"
            ):
                return {"ok": True}
            written_review_payloads.append(json.loads(payload.payload.decode("utf-8")))
            return {"ok": True}
        raise AssertionError(f"unexpected typed activity: {activity_type}")

    async def fake_execute_child_workflow(
        workflow_name: str,
        request: Any,
        **_kwargs: Any,
    ) -> Any:
        assert workflow_name == "MoonMind.AgentRun"
        child_requests.append(request)
        return {
            "summary": "Agent run completed",
            "output_refs": ["art_output_1"],
            "failure_class": None,
            "metadata": {"stateCheckpointRef": f"art_checkpoint_{len(child_requests)}"},
        }

    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-run-review-agent-1",
        run_id="run-review-agent-1",
        task_queue="mm.workflow",
        search_attributes={"mm_owner_type": ["user"], "mm_owner_id": ["owner-1"]},
    )
    monkeypatch.setattr(run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    assert len(child_requests) == 2
    assert child_requests[0].instruction_ref == "Implement the requested change."
    assert "REVIEW FEEDBACK (attempt 1)" in child_requests[1].instruction_ref
    assert (
        "Add the missing validation before retrying."
        in child_requests[1].instruction_ref
    )
    step = workflow.get_step_ledger()["steps"][0]
    assert step["executionOrdinal"] == 2
    assert step["status"] == "completed"
    assert step["checks"][0] == {
        "kind": "approval_policy",
        "status": "passed",
        "summary": "Approved after 1 retry",
        "retryCount": 1,
        "artifactRef": "art_review_2",
        "gateResultRef": "art_review_2",
        "gateVerdict": "FULLY_IMPLEMENTED",
        "confidence": 0.93,
        "validatedRefs": {},
        "invalidatedRefs": [],
        "remainingWorkRef": None,
        "targetLogicalStepId": None,
        "workspacePolicyRecommendation": None,
        "recommendedNextAction": "advance",
        "invalid": False,
        "degraded": False,
    }
    review_payloads = [
        payload
        for payload in written_review_payloads
        if payload.get("schemaVersion") == "v1" and "verdict" in payload
    ]
    assert review_payloads[0]["verdict"] == "ADDITIONAL_WORK_NEEDED"
    assert review_payloads[0]["recommendedNextAction"] == "reattempt_current_step"
    assert review_payloads[1]["verdict"] == "FULLY_IMPLEMENTED"
    assert step["artifacts"]["stepExecutionManifestRef"] == "art_attempt_2_terminal"
