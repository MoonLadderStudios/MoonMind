"""Assessment runtime readiness runs before planning, never on retained progress."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.exceptions import ApplicationError

from moonmind.workflows.temporal.workflows import run

PATCH = "run-assessment-runtime-readiness-v1"


def setup_gate(monkeypatch, result, *, patched=True, continued=None):
    monkeypatch.setattr(
        run.workflow, "patched", lambda marker: marker == PATCH and patched
    )
    monkeypatch.setattr(
        run.workflow, "info", lambda: SimpleNamespace(continued_run_id=continued)
    )
    monkeypatch.setattr(run.workflow, "upsert_memo", Mock())
    activity = AsyncMock(return_value=result)
    monkeypatch.setattr(run.workflow, "execute_activity", activity)
    monkeypatch.setattr(
        run.DEFAULT_ACTIVITY_CATALOG, "resolve_activity", lambda name: object()
    )
    instance = run.MoonMindRunWorkflow()
    instance._owner_type, instance._owner_id = "user", "trusted-owner"
    monkeypatch.setattr(
        instance,
        "_execute_kwargs_for_route",
        lambda route, **kwargs: {
            "task_queue": "mm.activity.agent_runtime",
            "retry_policy": SimpleNamespace(
                maximum_attempts=kwargs.get("max_attempts_override")
            ),
        },
    )
    return instance, activity


def parameters(key="workflow"):
    return {
        key: {"steps": [{"annotations": {"securityAssessment": {"kind": "web"}}}]},
        "assessmentScope": {"scopeId": "scope"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["workflow", "task"])
async def test_ready_gate_preserves_selected_inputs_and_trusted_owner(monkeypatch, key):
    instance, activity = setup_gate(
        monkeypatch,
        {
            "status": "ready",
            "diagnostics": [],
            "checks": [
                {
                    "component": "gitleaks",
                    "code": "scanner_available",
                    "status": "ready",
                }
            ],
        },
    )
    inputs = parameters(key)
    assert (
        await instance._run_planning_stage(
            parameters=inputs, input_ref=None, plan_ref="saved-plan"
        )
        == "saved-plan"
    )
    activity.assert_awaited_once()
    args, options = activity.await_args
    assert args == (
        "container_job.assessment_readiness",
        {
            "parameters": inputs,
            "owner": {"principalId": "trusted-owner", "principalType": "user"},
        },
    )
    assert options["retry_policy"].maximum_attempts == 1
    assert options["schedule_to_close_timeout"] == timedelta(minutes=11)
    assert options["start_to_close_timeout"] == timedelta(minutes=10)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"status": []},
        {"status": "ready", "diagnostics": [], "checks": [None]},
        {"status": "ready"},
        {"status": "ready", "diagnostics": "bad", "checks": []},
        {
            "status": "blocked",
            "diagnostics": [
                {
                    "code": "runtime_unavailable",
                    "action": "Restore the assessment runtime.",
                }
            ],
            "checks": [],
        },
        {"status": "unavailable", "diagnostics": [], "checks": []},
    ],
)
async def test_gate_fails_closed_before_planning(monkeypatch, result):
    instance, activity = setup_gate(monkeypatch, result)
    planning = AsyncMock()
    monkeypatch.setattr(run, "execute_typed_activity", planning)
    with pytest.raises(ApplicationError, match="[Aa]ssessment") as error:
        await instance._run_planning_stage(
            parameters=parameters(), input_ref=None, plan_ref=None
        )
    assert error.value.non_retryable
    planning.assert_not_awaited()
    activity.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_failure_has_safe_operator_action(monkeypatch):
    instance, activity = setup_gate(monkeypatch, None)
    activity.side_effect = RuntimeError("secret transport canary")
    with pytest.raises(ApplicationError, match="assessment runtime") as error:
        await instance._run_planning_stage(
            parameters=parameters(), input_ref=None, plan_ref="plan"
        )
    assert "secret transport canary" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["old-history", "continuation", "ordinary"])
async def test_gate_keeps_retained_progress_and_ordinary_runs_unchanged(
    monkeypatch, case
):
    instance, activity = setup_gate(
        monkeypatch,
        None,
        patched=case != "old-history",
        continued="previous" if case == "continuation" else None,
    )
    inputs = {} if case == "ordinary" else parameters()
    assert (
        await instance._run_planning_stage(
            parameters=inputs, input_ref=None, plan_ref="saved-plan"
        )
        == "saved-plan"
    )
    activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_diagnostic_gives_operator_action(monkeypatch):
    instance, _ = setup_gate(
        monkeypatch,
        {
            "status": "blocked",
            "checks": [],
            "diagnostics": [
                {
                    "code": "runtime_unavailable",
                    "action": "Restore the assessment runtime.",
                }
            ],
        },
    )
    with pytest.raises(
        ApplicationError, match="runtime_unavailable: Restore the assessment runtime"
    ):
        await instance._run_planning_stage(
            parameters=parameters(), input_ref=None, plan_ref="plan"
        )


@pytest.mark.asyncio
async def test_cancellation_is_not_reclassified_as_readiness_failure(monkeypatch):
    instance, activity = setup_gate(monkeypatch, None)
    activity.side_effect = run.exceptions.CancelledError("cancelled")
    with pytest.raises(run.exceptions.CancelledError):
        await instance._run_planning_stage(
            parameters=parameters(), input_ref=None, plan_ref="plan"
        )


@pytest.mark.asyncio
async def test_probe_omits_unrelated_large_inputs(monkeypatch):
    instance, activity = setup_gate(
        monkeypatch,
        {
            "status": "ready",
            "diagnostics": [],
            "checks": [
                {
                    "component": "gitleaks",
                    "code": "scanner_available",
                    "status": "ready",
                }
            ],
        },
    )
    inputs = parameters()
    inputs["instructions"] = "private-unrelated" * 100000
    inputs["runtime"] = "unrelated"
    inputs["workflow"]["steps"].append({"instructions": "private-unrelated" * 100000})
    step = inputs["workflow"]["steps"][0]
    step["instructions"] = inputs["instructions"]
    step["annotations"]["unrelated"] = "private-unrelated"
    step["annotations"]["securityAssessment"]["unrelated"] = inputs["instructions"]
    step["skill"] = {
        "id": "security-review",
        "args": {"assessment": {"target": "source"}, "other": inputs["instructions"]},
    }
    await instance._run_planning_stage(
        parameters=inputs, input_ref=None, plan_ref="plan"
    )
    sent = activity.await_args.args[1]["parameters"]
    assert sent == {
        "workflow": {
            "steps": [
                {
                    "annotations": {"securityAssessment": {"kind": "web"}},
                    "skill": {
                        "id": "security-review",
                        "args": {"assessment": {"target": "source"}},
                    },
                }
            ]
        },
        "assessmentScope": {"scopeId": "scope"},
    }


@pytest.mark.asyncio
async def test_oversized_assessment_is_rejected_before_dispatch(monkeypatch):
    instance, activity = setup_gate(monkeypatch, None)
    inputs = parameters()
    inputs["assessmentScope"]["oversized"] = "x" * 200000
    with pytest.raises(ApplicationError, match="[Cc]ompact|[Rr]educe"):
        await instance._run_planning_stage(
            parameters=inputs, input_ref=None, plan_ref="plan"
        )
    activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_runtime_fails_before_probe(monkeypatch):
    instance, activity = setup_gate(monkeypatch, None)
    monkeypatch.setattr(
        run.workflow,
        "patched",
        lambda marker: marker in {PATCH, "run-supported-runtime-admission-v1"},
    )

    def invalid(parameters):
        raise ValueError("Unsupported runtime")

    monkeypatch.setattr(run, "validate_workflow_runtime_targets", invalid)
    with pytest.raises(ApplicationError, match="Unsupported runtime"):
        await instance._run_planning_stage(
            parameters=parameters(), input_ref=None, plan_ref="plan"
        )
    activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_saved_preset_fails_before_probe(monkeypatch):
    instance, activity = setup_gate(monkeypatch, None)
    monkeypatch.setattr(
        run.workflow,
        "patched",
        lambda marker: marker
        in {
            PATCH,
            run.SAVED_PRESET_CAPABILITY_READINESS_PATCH,
            run.INSTANCE_PRESET_CAPABILITY_READINESS_PATCH,
        },
    )

    def invalid(parameters):
        raise ValueError("invalid preset")

    monkeypatch.setattr(run, "saved_preset_capability_check", invalid)
    with pytest.raises(
        ApplicationError, match="Saved schedule preset provenance is invalid"
    ):
        await instance._run_planning_stage(
            parameters=parameters(), input_ref=None, plan_ref="plan"
        )
    activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_ready_result_does_not_certify_scanner(monkeypatch):
    instance, _ = setup_gate(
        monkeypatch, {"status": "ready", "diagnostics": [], "checks": []}
    )
    with pytest.raises(ApplicationError):
        await instance._run_planning_stage(
            parameters=parameters(), input_ref=None, plan_ref="plan"
        )


@pytest.mark.asyncio
async def test_allowed_unknown_credentials_remain_observable(monkeypatch):
    check = {
        "component": "credentialMetadata",
        "status": "unverified",
        "code": "metadata_not_inspected",
    }
    instance, _ = setup_gate(
        monkeypatch, {"status": "ready", "diagnostics": [], "checks": [check]}
    )
    await instance._run_planning_stage(
        parameters=parameters(), input_ref=None, plan_ref="plan"
    )
    run.workflow.upsert_memo.assert_called_once_with(
        {"assessmentReadiness": {"status": "ready", "checks": [check]}}
    )
