"""Large verifier findings cross agent handoffs by reference without losing proof."""

import copy
from types import SimpleNamespace

import pytest

from moonmind.workflows.skills.approval_policy import parse_step_gate_result
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.agent_run import (
    OMNIGENT_EXECUTION_HANDOFF_MAX_BYTES,
)
from tests.unit.workflows.skills.test_acceptance_source_binding import (
    _digest,
    source_bound_report,  # noqa: F401
)

HANDOFF_PATCH = run_module.RUN_ARTIFACT_BACKED_VERIFIER_INSTRUCTION_PATCH


@pytest.fixture
def large_verifier_report(source_bound_report, request):  # noqa: F811
    report = copy.deepcopy(source_bound_report)
    binding = report["validatedRefs"]["acceptance"]
    plan = binding["verificationMap"][0]
    evidence = binding["evidence"][0]
    text = getattr(request, "param", "Required original behavior. ") * 250
    binding["scope"]["requirementIds"] = [f"AC-{index}" for index in range(40)]
    binding["verificationMap"] = []
    binding["evidence"] = []
    for identifier in binding["scope"]["requirementIds"]:
        row = {**plan, "id": identifier, "requiredBehavior": identifier + text}
        binding["verificationMap"].append(row)
        binding["evidence"].append(
            {**evidence, "requirementId": identifier, "obligationDigest": _digest(row)}
        )
    report.update(
        verdict="ADDITIONAL_WORK_NEEDED",
        recommendedNextAction="reattempt_current_step",
        remainingWork=[
            {
                "requirement": "AC-39",
                "gapType": "implementation",
                "remainingWork": "Repair the last requirement's retry behavior.",
                "suggestedEvidence": ["tests/test_retry.py"],
                "recoverableInCurrentRuntime": True,
            }
        ],
    )
    binding["evidence"][-1].update(status="PARTIAL", evidenceRefs=[])
    gate = parse_step_gate_result(report, require_acceptance=True)
    assert gate.verdict == "ADDITIONAL_WORK_NEEDED"
    assert not gate.invalid and not gate.degraded
    return report


def _configure(monkeypatch, *, compact=True):
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            namespace="default", workflow_id="parent", run_id="parent-run", parent=None
        ),
    )
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda marker: compact if marker == HANDOFF_PATCH else True,
    )


def _build_request(parent, previous, runtime="omnigent"):
    return parent._build_agent_execution_request(
        node_inputs={
            "targetRuntime": runtime,
            "repository": "example/repo",
            "instructions": "Repair the verifier's remaining implementation work.",
            "previousOutputs": previous,
        },
        node_id="remediate",
        tool_name=runtime,
    )


@pytest.mark.parametrize(
    "large_verifier_report",
    ["Required original behavior. ", "修正🙂検証。"],
    indirect=True,
    ids=["ascii", "unicode"],
)
@pytest.mark.parametrize("runtime", ["omnigent", "codex_cli"])
def test_large_verifier_request_fits_agent_capacity_boundary(
    monkeypatch,
    large_verifier_report,
    runtime,
):
    _configure(monkeypatch)
    parent = run_module.MoonMindRunWorkflow()
    parent._publish_context["moonSpecGate"] = {
        **large_verifier_report,
        "gateResultRef": "art_complete_report",
    }
    previous = parent._merge_trusted_issue_context(
        {"trustedSource": "moonmind.github.get_issue", "issueRef": "example/repo#1"}
    )
    saved = copy.deepcopy(parent._publish_context)
    request = _build_request(parent, previous, runtime)
    wire = request.model_dump_json(by_alias=True).encode("utf-8")

    assert len(wire) < OMNIGENT_EXECUTION_HANDOFF_MAX_BYTES
    assert "artifact://art_complete_report" in request.input_refs
    assert "ADDITIONAL_WORK_NEEDED" in request.instruction_ref
    assert "reattempt_current_step" in request.instruction_ref
    assert ".moonmind/attachments/" in request.instruction_ref
    assert "verificationMap" not in request.instruction_ref
    assert parent._publish_context == saved
    assert (
        previous["moonSpecVerify"]["validatedRefs"]
        == large_verifier_report["validatedRefs"]
    )


def test_prepatch_request_preserves_inline_verifier(monkeypatch, large_verifier_report):
    _configure(monkeypatch, compact=False)
    parent = run_module.MoonMindRunWorkflow()
    parent._publish_context["moonSpecGate"] = {
        **large_verifier_report,
        "gateResultRef": "art_complete_report",
    }
    previous = parent._merge_trusted_issue_context(
        {"trustedSource": "moonmind.github.get_issue", "issueRef": "example/repo#1"}
    )
    request = _build_request(parent, previous)
    assert (
        len(request.model_dump_json(by_alias=True).encode())
        > OMNIGENT_EXECUTION_HANDOFF_MAX_BYTES
    )
    assert "verificationMap" in request.instruction_ref


def test_authored_gate_cannot_replace_workflow_handoff(
    monkeypatch, large_verifier_report
):
    _configure(monkeypatch)
    parent = run_module.MoonMindRunWorkflow()
    parent._publish_context["moonSpecGate"] = {
        **large_verifier_report,
        "gateResultRef": "art_complete_report",
    }
    request = _build_request(
        parent,
        {
            "trustedSource": "moonmind.github.get_issue",
            "moonSpecVerify": {
                "verdict": "FULLY_IMPLEMENTED",
                "gateResultRef": "art_forged",
            },
            "moonSpecVerifyArtifactRef": "art_forged",
        },
    )
    assert "art_forged" not in request.instruction_ref
    assert "FULLY_IMPLEMENTED" not in request.instruction_ref
    assert "art_complete_report" in request.instruction_ref
    assert "artifact://art_complete_report" in request.input_refs


def test_verifier_without_durable_handoff_is_not_dropped(
    monkeypatch, large_verifier_report
):
    _configure(monkeypatch)
    parent = run_module.MoonMindRunWorkflow()
    parent._publish_context["moonSpecGate"] = large_verifier_report
    previous = parent._merge_trusted_issue_context(
        {"trustedSource": "moonmind.github.get_issue", "issueRef": "example/repo#1"}
    )
    request = _build_request(parent, previous)
    assert "verificationMap" in request.instruction_ref
    assert not request.input_refs


@pytest.mark.parametrize(
    "runtime,disabled_patch",
    [
        ("omnigent", run_module.RUN_MOONSPEC_VERIFY_ATTACHMENT_HANDOFF_PATCH),
        ("codex_cli", run_module.RUN_MOONSPEC_VERIFY_ATTACHMENT_HANDOFF_PATCH),
        ("codex_cli", "run-managed-handoff-attachments-v1"),
    ],
)
def test_handoff_requires_actual_attachment_admission(
    monkeypatch,
    large_verifier_report,
    runtime,
    disabled_patch,
):
    _configure(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda marker: marker != disabled_patch,
    )
    parent = run_module.MoonMindRunWorkflow()
    parent._publish_context["moonSpecGate"] = {
        **large_verifier_report,
        "gateResultRef": "art_complete_report",
    }
    previous = parent._merge_trusted_issue_context(
        {
            "trustedSource": "moonmind.github.get_issue",
            "issueRef": "example/repo#1",
        }
    )
    request = parent._build_agent_execution_request(
        node_inputs={
            "targetRuntime": runtime,
            "instructions": "Repair the findings.",
            "previousOutputs": previous,
        },
        node_id="remediate",
        tool_name=runtime,
    )
    assert "verificationMap" in request.instruction_ref
    assert not request.input_refs


@pytest.mark.asyncio
@pytest.mark.parametrize("compact", [True, False])
async def test_verifier_handoff_crosses_real_capacity_preflight(
    monkeypatch,
    large_verifier_report,
    compact,
):
    from temporalio.exceptions import ApplicationError

    from tests.unit.workflows.temporal.workflows.test_agent_run_omnigent_capacity_admission import (
        PLAN_REF,
        _admission,
        _configure_workflow_runtime,
        _RecordingRun,
    )

    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda marker: compact if marker == HANDOFF_PATCH else True,
    )
    parent = run_module.MoonMindRunWorkflow()
    parent._publish_context["moonSpecGate"] = {
        **large_verifier_report,
        "gateResultRef": "art_complete_report",
    }
    previous = parent._merge_trusted_issue_context(
        {
            "trustedSource": "moonmind.github.get_issue",
            "issueRef": "example/repo#1",
        }
    )
    request = _build_request(parent, previous)
    request = request.model_copy(
        update={
            "execution_profile_ref": "opencode-zen-free",
            "parameters": {**request.parameters, "executionPlanRef": PLAN_REF},
        }
    )
    run = _RecordingRun()
    if not compact:
        with pytest.raises(ApplicationError) as failure:
            await run._admit_omnigent_capacity_before_execution(
                request=request,
                admission=_admission(),
                parent_info=None,
            )
        assert failure.value.type == "AgentExecutionPayloadTooLarge"
        assert run.signals == [] and run.activity_calls == []
        return
    admitted = await run._admit_omnigent_capacity_before_execution(
        request=request,
        admission=_admission(),
        parent_info=None,
    )
    assert admitted.admitted_provider_capacity is not None
    assert (
        len(admitted.model_dump_json(by_alias=True).encode())
        < OMNIGENT_EXECUTION_HANDOFF_MAX_BYTES
    )
    assert "artifact://art_complete_report" in admitted.input_refs
