"""Aggregate dynamic-loop resource boundaries for MoonMind#5177."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.workflows.temporal.remediation_loop import RemediationLoopPhase
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.workflows.temporal.test_remediation_loop import _spec, _state

RESOURCE_PATCH = "run-remediation-aggregate-resource-budget-v1"
NOW = datetime(2026, 11, 9, tzinfo=UTC)


def parent(monkeypatch, *, limits=None, enabled=True):
    patches = (
        {RESOURCE_PATCH, "run-materialize-evidence-retry-v1"} if enabled else set()
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda patch: patch in patches)
    monkeypatch.setattr(run_module.workflow, "now", lambda: NOW)
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="repair", run_id="run-1", task_queue="mm.workflow.user.v2"
        ),
    )
    run = run_module.MoonMindRunWorkflow()
    spec = _spec(6)
    run._remediation_loop_spec = spec.model_copy(
        update={"budgets": spec.budgets.model_copy(update=limits or {})}
    )
    run._remediation_loop_state = _state(attempts=1).model_copy(
        update={"phase": RemediationLoopPhase.VERIFICATION_RUNNING}
    )
    run._remediation_loop_runtime = {"mode": "codex_cli"}
    run._remediation_loop_controller_node_id = "verify"
    run._write_json_artifact = AsyncMock(return_value="artifact://decision")
    run._update_memo = lambda: None
    run._publish_context["confirmedEffects"] = {
        "pullRequestUrl": "https://example.test/pr/1"
    }
    return run


def result(run, *, step="verify", ordinal=1, tokens=100, cost=1.5):
    run._step_ledger_rows = [
        {"logicalStepId": step, "attempt": ordinal, "artifacts": {}, "refs": {}}
    ]
    run._rebuild_step_ledger_index()
    run._record_step_result_evidence(
        step,
        execution_result=run._map_legacy_agent_run_result(
            {
                "metadata": {
                    "usage": {"total_tokens": tokens, "costUsd": cost},
                    "moonSpecVerify": {
                        "validatedRefs": {"resourceUsage": {"tokens": 0}}
                    },
                }
            }
        ),
        updated_at=NOW,
    )


async def evaluate(run, *, verdict="ADDITIONAL_WORK_NEEDED", nodes=None):
    return await run._evaluate_dynamic_remediation_verification(
        ordered_nodes=[] if nodes is None else nodes,
        verdict=verdict,
        gate_result_ref="artifact://gate/current",
        remaining_work_ref="artifact://remaining/original",
        recoverable_evidence=True,
        logical_step_id="verify",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limits,reason",
    [
        ({"token_budget": 100}, "token_budget_exhausted"),
        ({"provider_budget": 1}, "provider_budget_exhausted"),
        ({"cost_budget": 1}, "cost_budget_exhausted"),
    ],
)
@pytest.mark.parametrize("verdict", ["ADDITIONAL_WORK_NEEDED", "NO_DETERMINATION"])
async def test_runtime_usage_exhaustion_stops_successor_and_retains_work(
    monkeypatch, limits, reason, verdict
):
    run = parent(monkeypatch, limits=limits)
    result(run)
    nodes = []
    assert not await evaluate(run, verdict=verdict, nodes=nodes)
    assert not nodes
    state = run._remediation_loop_state
    assert state.phase == RemediationLoopPhase.STOPPED_REMAINING_WORK
    assert state.continuation_reason == reason
    assert state.workspace_head_ref == "artifact://workspace/C1"
    assert state.latest_verification_ref == "artifact://gate/current"
    assert state.consumed_budgets.attempts == 1
    assert state.consumed_budgets.evidence_retries == 0
    assert run._publish_context["confirmedEffects"]["pullRequestUrl"].endswith("/1")


@pytest.mark.asyncio
async def test_repeated_receipt_does_not_double_charge_or_reset_observed_usage(
    monkeypatch,
):
    run = parent(monkeypatch, limits={"token_budget": 150})
    result(run, tokens=100)
    result(run, tokens=100)
    result(run, tokens=0)
    assert await evaluate(run)
    # New execution, including an infrastructure retry, adds to original usage.
    run._remediation_loop_state = run._remediation_loop_state.model_copy(
        update={"phase": RemediationLoopPhase.VERIFICATION_RUNNING}
    )
    result(run, ordinal=2, tokens=50)
    assert not await evaluate(run)
    assert run._remediation_loop_state.continuation_reason == "token_budget_exhausted"


@pytest.mark.asyncio
async def test_success_at_budget_limit_remains_accepted_and_old_histories_keep_routing(
    monkeypatch,
):
    run = parent(monkeypatch, limits={"token_budget": 100})
    result(run)
    assert not await evaluate(run, verdict="FULLY_IMPLEMENTED")
    assert run._remediation_loop_state.phase == RemediationLoopPhase.ACCEPTED
    legacy = parent(monkeypatch, limits={"token_budget": 100}, enabled=False)
    result(legacy)
    assert await evaluate(legacy)


@pytest.mark.asyncio
async def test_runtime_usage_and_original_limits_survive_continue_as_new(monkeypatch):
    run = parent(monkeypatch, limits={"token_budget": 150})
    result(run, tokens=100)
    run._original_input_payload = {
        "initial_parameters": {"sourceEvidence": "artifact://original"}
    }
    payload = run._build_remediation_loop_continue_as_new_input(ordered_nodes=[])
    restored = parent(monkeypatch, limits={"token_budget": 150})
    restored._remediation_loop_continuation = payload["remediation_loop_continuation"]
    restored._restore_remediation_loop_continuation(ordered_nodes=[])
    assert restored._remediation_loop_state.resource_usage.totals()["tokens"] == 100
    assert (
        restored._remediation_loop_state.workspace_head_ref == "artifact://workspace/C1"
    )
    assert restored._remediation_loop_state.continue_as_new_count == 1
    assert payload["initial_parameters"]["sourceEvidence"] == "artifact://original"
    result(restored, ordinal=2, tokens=50)
    assert not await evaluate(restored)
    assert (
        restored._remediation_loop_state.continuation_reason == "token_budget_exhausted"
    )


@pytest.mark.asyncio
async def test_evidence_retry_consumes_resource_budget_without_another_writer(
    monkeypatch,
):
    run = parent(monkeypatch, limits={"token_budget": 100})
    result(run, tokens=40)
    nodes = [{"id": "verify", "inputs": {"instructions": "Verify only"}}]
    assert await evaluate(run, verdict="NO_DETERMINATION", nodes=nodes)
    retry = next(node for node in nodes if "verification-evidence" in node["id"])
    assert retry["inputs"]["readOnlyWorkspaceHead"] is True
    result(run, step=retry["id"], tokens=60)
    assert not await evaluate(run, verdict="NO_DETERMINATION")
    state = run._remediation_loop_state
    assert state.consumed_budgets.attempts == 1
    assert state.consumed_budgets.evidence_retries == 1
    assert state.resource_usage.totals()["tokens"] == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["wall", "provider", "tokens", "cost", "cancel"])
async def test_dispatch_admission_checks_exhaustion_unknown_metering_and_cancellation(
    monkeypatch, kind
):
    limits = (
        {"max_wall_clock_seconds": 30}
        if kind == "wall"
        else (
            {f"{'token' if kind == 'tokens' else kind}_budget": 1}
            if kind != "cancel"
            else {}
        )
    )
    run = parent(monkeypatch, limits=limits)
    run._write_json_artifact.return_value = "art_resource_stop"
    run._step_ledger_rows = [{"logicalStepId": "verify", "attempt": 1}]
    run._rebuild_step_ledger_index()
    assert await run._admit_remediation_resource_execution("verify")
    # Reconcile a lost acknowledgement under the reserved identity.
    assert await run._admit_remediation_resource_execution("verify")
    assert run._remediation_loop_state.resource_usage.totals()["provider"] == 1
    run._step_ledger_rows[0]["attempt"] = 2
    monkeypatch.setattr(run_module.workflow, "now", lambda: NOW + timedelta(seconds=30))
    run._cancel_requested = kind == "cancel"
    assert not await run._admit_remediation_resource_execution("verify")
    state = run._remediation_loop_state
    assert state.resource_usage.totals()["provider"] == 1
    assert state.workspace_head_ref == "artifact://workspace/C1"
    if kind != "cancel":
        assert state.phase == RemediationLoopPhase.STOPPED_REMAINING_WORK
        assert state.continuation_decision_ref == "artifact://art_resource_stop"
        assert run._publish_context["publicationBlockedBy"] == (
            "remediation_resource_budget"
        )
        assert (
            state.continuation_reason
            == {
                "wall": "wall_clock_budget_exhausted",
                "provider": "provider_budget_exhausted",
                "tokens": "tokens_usage_unavailable",
                "cost": "cost_usage_unavailable",
            }[kind]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limits,reason",
    [
        ({"token_budget": 100}, "token_budget_exhausted"),
        ({"cost_budget": 1}, "cost_budget_exhausted"),
    ],
)
async def test_partial_flattened_metadata_cannot_mask_runtime_usage(
    monkeypatch, limits, reason
):
    run = parent(monkeypatch, limits=limits)
    run._step_ledger_rows = [{"logicalStepId": "verify", "attempt": 1}]
    run._rebuild_step_ledger_index()
    run._record_step_result_evidence(
        "verify",
        execution_result={
            "outputs": {
                "total_tokens": 0,
                "costEstimateUsd": 0,
                "usage": {"total_tokens": 100, "costUsd": 1.5},
            }
        },
        updated_at=NOW,
    )
    assert not await evaluate(run)
    assert run._remediation_loop_state.continuation_reason == reason


@pytest.mark.asyncio
async def test_rollover_without_historical_metering_cannot_reset_an_admitted_budget(
    monkeypatch,
):
    run = parent(monkeypatch, limits={"token_budget": 100})
    run._remediation_loop_state = run._remediation_loop_state.model_copy(
        update={"continue_as_new_count": 1}
    )
    assert not await run._admit_remediation_resource_execution("verify")
    state = run._remediation_loop_state
    assert state.continuation_reason == "prior_resource_usage_unavailable"
    assert state.workspace_head_ref == "artifact://workspace/C1"
    assert state.resource_usage.complete_history is False
    assert state.resource_usage.totals()["provider"] == 0


@pytest.mark.parametrize("dimension", ["tokenBudget", "costBudget"])
@pytest.mark.parametrize("amount", [0, 100])
@pytest.mark.parametrize("supplied_continuation", [False, True])
def test_unenforceable_resource_dimension_rejected_at_admission(
    monkeypatch, dimension, amount, supplied_continuation
):
    run = parent(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda patch: True)
    spec = _spec(6).model_dump(by_alias=True, mode="json")
    spec["budgets"][dimension] = amount
    if supplied_continuation:
        # Caller-controlled payload fields do not make a fresh execution an
        # already admitted Temporal continuation, even with valid carried state.
        run._remediation_loop_continuation = {
            "schemaVersion": 1,
            "state": _state(attempts=1).model_dump(by_alias=True, mode="json"),
            "orderedNodes": [{"id": "verify"}],
            "stepLedgerRows": [],
        }
    with pytest.raises(ValueError, match=f"{dimension}.*unsupported"):
        run._initialize_remediation_loop_controller(
            ordered_nodes=[{"id": "verify", "annotations": {"remediationLoop": spec}}],
            require_agent_instructions=False,
        )


@pytest.mark.asyncio
async def test_active_dispatch_and_materialized_pair_share_original_deadline(
    monkeypatch,
):
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    run = parent(monkeypatch, limits={"max_wall_clock_seconds": 20})
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch: patch
        in {
            RESOURCE_PATCH,
            run_module.RUN_REMEDIATION_ACTIVE_RESOURCE_DEADLINE_PATCH,
            "run-materialize-evidence-retry-v1",
        },
    )
    assert await run._admit_remediation_resource_execution("verify")
    monkeypatch.setattr(run_module.workflow, "now", lambda: NOW + timedelta(seconds=12))
    nodes = []
    assert await evaluate(run, nodes=nodes)
    assert len(nodes) == 2
    assert all(
        node["inputs"]["timeoutPolicy"]["max_timeout_seconds"] == 8 for node in nodes
    )
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="test",
        idempotencyKey="test",
    )
    bounded = run._bound_remediation_execution_request(
        request, logical_step_id="verify", attempt_id="attempt"
    )
    assert bounded.execution_budget.execution_deadline == NOW + timedelta(seconds=20)
    assert bounded.execution_budget.remaining_seconds(NOW + timedelta(seconds=19)) == 1
    assert bounded.execution_budget.remaining_seconds(NOW + timedelta(seconds=20)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("active_timeout", [False, True])
async def test_published_evidence_uses_original_resource_owner(
    monkeypatch, active_timeout
):
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.workflows.temporal.workflows import (
        issue_evidence_continuation as continuation,
    )

    run = parent(
        monkeypatch, limits={"max_wall_clock_seconds": 20, "provider_budget": 2}
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    run._repo = "example/repo"
    run._publish_context["branch"] = "candidate"
    assert await run._admit_remediation_resource_execution("verify")
    monkeypatch.setattr(run_module.workflow, "now", lambda: NOW + timedelta(seconds=12))
    run._resolve_agent_node_skillset_ref = AsyncMock(return_value="artifact://skills")
    run._existing_agent_skillset_ref = lambda **_: None
    run._build_agent_execution_request = lambda **_: AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="test",
        idempotencyKey="test",
    )
    run._workflow_child_task_queue = lambda: "test"
    run._hydrate_acceptance_result = AsyncMock(
        return_value={"status": "COMPLETED", "outputs": {}}
    )
    child = AsyncMock(return_value={})
    monkeypatch.setattr(run_module.workflow, "execute_child_workflow", child)
    kwargs = dict(
        node={"id": "verify", "inputs": {}},
        parameters={},
        observation={"headSha": "a" * 40},
    )
    if active_timeout:

        async def timed_out(*args):
            monkeypatch.setattr(
                run_module.workflow, "now", lambda: NOW + timedelta(seconds=20)
            )
            return {"status": "FAILED", "outputs": {}}

        run._hydrate_acceptance_result = timed_out
        with pytest.raises(continuation._ResourceBudgetStop):
            await continuation._verify(run, ordinal=1, **kwargs)
        assert (
            run._remediation_loop_state.continuation_reason
            == "wall_clock_budget_exhausted"
        )
        assert child.await_count == 1
        return
    await continuation._verify(run, ordinal=1, **kwargs)
    request = child.await_args.args[1]
    assert request.execution_budget.execution_deadline == NOW + timedelta(seconds=20)
    assert run._remediation_loop_state.resource_usage.totals()["provider"] == 2
    with pytest.raises(continuation._ResourceBudgetStop, match="resource budget"):
        await continuation._verify(run, ordinal=2, **kwargs)
    assert child.await_count == 1
    assert (
        run._remediation_loop_state.continuation_reason == "provider_budget_exhausted"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_parent_pause_stops_at_loop_deadline_without_resume(monkeypatch, cancel):
    run = parent(monkeypatch, limits={"max_wall_clock_seconds": 3})
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    assert await run._admit_remediation_resource_execution("verify")
    run._paused = True
    run._update_search_attributes = lambda: None
    clock = [NOW]
    monkeypatch.setattr(run_module.workflow, "now", lambda: clock[0])
    async def wait(*args, timeout=None):
        assert timeout == timedelta(seconds=3)
        clock[0] += timeout
        run._cancel_requested = cancel
        raise TimeoutError
    monkeypatch.setattr(run_module.workflow, "wait_condition", wait)
    await run._wait_if_paused_at_safe_boundary(remediation_step_id="verify")
    assert run._paused
    assert not await run._admit_remediation_resource_execution("verify")
    assert run._remediation_loop_state.resource_usage.totals()["provider"] == 1
    assert run._remediation_loop_state.continuation_reason == (None if cancel else "wall_clock_budget_exhausted")


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [False, True])
async def test_unrelated_or_accepted_parent_pause_keeps_its_own_authority(monkeypatch, accepted):
    run = parent(monkeypatch, limits={"max_wall_clock_seconds": 3})
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    assert await run._admit_remediation_resource_execution("verify")
    if accepted:
        run._remediation_loop_state = run._remediation_loop_state.model_copy(update={"phase": RemediationLoopPhase.ACCEPTED})
    monkeypatch.setattr(run_module.workflow, "now", lambda: NOW + timedelta(seconds=4))
    run._paused = True
    run._update_search_attributes = lambda: None
    wait = AsyncMock()
    monkeypatch.setattr(run_module.workflow, "wait_condition", wait)
    await run._wait_if_paused_at_safe_boundary(remediation_step_id="verify" if accepted else None)
    assert "timeout" not in wait.await_args.kwargs
    assert run._remediation_loop_state.continuation_reason is None
