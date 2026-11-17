"""Producer-level routing into the canonical turn boundary.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane 7/11]).

``tests/unit/omnigent/test_canonical_turn_routing.py`` proves the boundary
behaves correctly once it is handed a source. Every source value there is
supplied by the test, so it cannot catch a *producer* that reaches the provider
under the wrong source -- or under no claimed command at all.

These tests assert the routing instead: the run workflow's dispatch loop is the
only source authority, and every production realizer delivers through the one
wrapper that derives the source. Nothing here tells the production code which
source to use.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from moonmind.omnigent.control_plane import TurnSource
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
)
from moonmind.omnigent.harness_platform.support import KNOWN_REALIZERS
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.realizers.turn_delivery import canonical_turn_source
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.remediation_loop import (
    ConsumedRemediationBudgets,
    RemediationLoopPhase,
    RemediationLoopSpec,
    RemediationLoopState,
    materialize_attempt_nodes,
)
from moonmind.workflows.temporal.remediation_workspace_head import (
    RemediationWorkspaceHead,
)
from moonmind.workflows.temporal.workflows.run import (
    RUN_ACCEPTED_PUBLISHED_BRANCH_HANDOFF_PATCH,
    RUN_CANONICAL_TURN_LINEAGE_PATCH,
    RUN_OMNIGENT_EXECUTION_PLAN_REF_PATCH,
    MoonMindRunWorkflow,
)

PROVIDER_PROFILE_REF = "codex"


# --- Retained admitted Codex execution plan ---------------------------------


def _codex_execution_plan():
    """Read retained authority without reopening current new-plan admission."""

    fixture = json.loads(
        (
            Path(__file__).parents[2]
            / "fixtures/omnigent/retained_profile_bound_plans/codex-on-demand-1.json"
        ).read_text()
    )
    binding = fixture["request"]["omnigentExecutionPlan"]
    plan = OmnigentExecutionPlanEnvelope.model_validate_json(
        fixture["artifacts"][binding["planArtifactRef"]]
    )
    assert plan.planRef == binding["planRef"]
    assert (
        plan.payload.credentialBindings["primary-model"].providerProfileRef
        == PROVIDER_PROFILE_REF
    )
    return plan


@pytest.fixture(scope="module")
def plan():
    return _codex_execution_plan()


#: The one production run workflow whose dispatch loop launches remediation
#: attempts. These identities are the ones ``workflows/run.py`` derives from
#: ``workflow.info()``; nothing here hand-builds a canonical turn source.
WORKFLOW_ID = "wf-canonical-turn"
TEMPORAL_RUN_ID = "run-canonical-turn"
LOOP_ID = "issue-implementation-remediation"
BASE_LOGICAL_STEP_ID = "implement-the-issue"
BASE_STEP_EXECUTION_ID = (
    f"{WORKFLOW_ID}:{TEMPORAL_RUN_ID}:{BASE_LOGICAL_STEP_ID}:execution:1"
)


class _MockWorkflowInfo:
    workflow_id = WORKFLOW_ID
    run_id = TEMPORAL_RUN_ID


def _loop_spec() -> RemediationLoopSpec:
    """The authored remediation-loop contract a run workflow admits."""

    return RemediationLoopSpec.model_validate(
        {
            "kind": "remediation_loop",
            "loopId": LOOP_ID,
            "remediationTool": {
                "type": "skill",
                "name": "auto",
                "inputs": {"instructions": "Fix the remaining verified gaps."},
            },
            "verificationTool": {
                "type": "skill",
                "name": "moonspec-verify",
                "inputs": {"instructions": "Verify the remediated candidate."},
            },
            "workspacePolicy": "continue_from_loop_head",
            "budgets": {"hardMaxAttempts": 3},
            "terminalPolicy": {
                "fullyImplemented": "advance",
                "additionalWorkNeeded": "continue_when_allowed",
                "blocked": "stop",
                "noDetermination": "retry_evidence_or_stop",
                "failedUnrecoverable": "stop",
            },
            "sideEffectPolicy": "workflow_owned",
            "publicationPolicy": "evaluate_after_terminal",
        }
    )


def _loop_head(*, base_step_execution_id: str | None) -> RemediationWorkspaceHead:
    """The workflow-owned loop head naming the attempt a remediation repairs."""

    return RemediationWorkspaceHead(
        loopId=LOOP_ID,
        branchRef=f"checkpoint-branch:{LOOP_ID}",
        rootCheckpointRef="artifact://workspace/C0",
        rootWorkspaceDigest="sha256:" + "a" * 64,
        headCheckpointRef="artifact://workspace/C0",
        headWorkspaceDigest="sha256:" + "a" * 64,
        headStepExecutionId=base_step_execution_id,
        headAttemptOrdinal=0,
        headVersion=1,
    )


def _run_workflow(
    *, attempt_ordinal: int = 1, base_step_execution_id: str | None
) -> MoonMindRunWorkflow:
    """A run workflow with the loop state its controller actually owns."""

    wf = MoonMindRunWorkflow()
    wf._remediation_loop_spec = _loop_spec()
    wf._remediation_loop_state = RemediationLoopState(
        loopId=LOOP_ID,
        attemptOrdinal=attempt_ordinal,
        phase=RemediationLoopPhase.REMEDIATION_RUNNING,
        consumedBudgets=ConsumedRemediationBudgets(attempts=attempt_ordinal),
    )
    wf._remediation_workspace_head = _loop_head(
        base_step_execution_id=base_step_execution_id
    )
    return wf


def _base_node(*, publish_mode: str) -> dict:
    """An ordinary authored Omnigent step -- the attempt remediation repairs."""

    return {
        "id": BASE_LOGICAL_STEP_ID,
        "title": "Implement the issue",
        "tool": {"type": "agent_runtime", "name": "omnigent"},
        "inputs": {
            "instructions": "Implement the issue.",
            "runtime": {
                "mode": "omnigent",
                "executionProfileRef": PROVIDER_PROFILE_REF,
            },
            "repositoryOperation": "write",
            "publishMode": publish_mode,
        },
    }


def _remediation_node(*, ordinal: int, publish_mode: str) -> dict:
    """The attempt node the production loop materializer compiles."""

    remediation, _verification = materialize_attempt_nodes(
        spec=_loop_spec(),
        ordinal=ordinal,
        workspace_head_ref="artifact://loop-head/1",
        runtime={"mode": "omnigent", "executionProfileRef": PROVIDER_PROFILE_REF},
        remediation_inputs={"publishMode": publish_mode},
    )
    return remediation


def _dispatch_request(
    wf: MoonMindRunWorkflow,
    node: dict,
    *,
    plan_ref: str,
) -> AgentExecutionRequest:
    """Build the request exactly as the run workflow's dispatch loop does.

    ``_record_canonical_turn_lineage`` is the controller attestation the
    dispatch loop performs immediately before building the request; nothing in
    this test names a turn source or hand-builds remediation authority.
    """

    node_id = str(node["id"])
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=_MockWorkflowInfo(),
    ), patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        side_effect=lambda patch_id: patch_id
        in {RUN_OMNIGENT_EXECUTION_PLAN_REF_PATCH, RUN_CANONICAL_TURN_LINEAGE_PATCH},
    ):
        wf._record_canonical_turn_lineage(node=node, node_id=node_id)
        return wf._build_agent_execution_request(
            node_inputs=dict(node["inputs"]),
            node_id=node_id,
            tool_name="omnigent",
            workflow_parameters={"executionPlanRef": plan_ref},
            step_execution=1,
        )


# --- The launching controller is the only source authority -------------------


def test_turn_source_is_derived_from_controller_attested_lineage(plan) -> None:
    """Only workflow-owned loop state makes a dispatch a REMEDIATION turn."""

    base = _dispatch_request(
        _run_workflow(base_step_execution_id=BASE_STEP_EXECUTION_ID),
        _base_node(publish_mode="none"),
        plan_ref=plan.planRef,
    )
    remediation = _dispatch_request(
        _run_workflow(base_step_execution_id=BASE_STEP_EXECUTION_ID),
        _remediation_node(ordinal=1, publish_mode="none"),
        plan_ref=plan.planRef,
    )

    assert canonical_turn_source(base) is TurnSource.INITIAL
    assert canonical_turn_source(remediation) is TurnSource.REMEDIATION
    # The source is the controller's attestation, not the dead remediation
    # workspace seam: no production dispatch populates that field.
    assert base.remediation_workspace is None
    assert remediation.remediation_workspace is None
    lineage = remediation.step_execution.canonical_turn_lineage
    assert lineage is not None
    assert lineage.base_step_execution_id == BASE_STEP_EXECUTION_ID


def test_a_forged_remediation_node_without_loop_state_gets_no_lineage(plan) -> None:
    """Plan annotations alone cannot claim remediation authority."""

    wf = MoonMindRunWorkflow()
    wf._remediation_loop_state = None
    request = _dispatch_request(
        wf,
        _remediation_node(ordinal=1, publish_mode="none"),
        plan_ref=plan.planRef,
    )

    assert request.step_execution.canonical_turn_lineage is None
    assert canonical_turn_source(request) is TurnSource.INITIAL


# --- RW-5: the production remediation producer -------------------------------


@pytest.mark.parametrize("current_history", [False, True])
def test_published_handoff_replay_keeps_legacy_metadata_path(current_history) -> None:
    wf = _run_workflow(base_step_execution_id=None)
    wf._publish_context.update(
        pushStatus="pushed", branch="legacy-head", headSha="a" * 40
    )
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        side_effect=lambda patch_id: current_history
        and patch_id == RUN_ACCEPTED_PUBLISHED_BRANCH_HANDOFF_PATCH,
    ):
        # Current requests cannot establish authority using unaccepted metadata.
        expected = None if current_history else ("legacy-head", "a" * 40)
        assert wf._workflow_verified_published_head() == expected
        payload = wf._build_remediation_loop_continue_as_new_input(ordered_nodes=[])
        assert "acceptedPublishedHead" not in payload["remediation_loop_continuation"]


# --- Every production realizer routes through the one wrapper ----------------


#: Every trusted execution realizer, keyed by the ref the registry admits. A new
#: realizer added to ``KNOWN_REALIZERS`` without an entry here fails the first
#: test below rather than silently escaping the routing assertion.
_PRODUCTION_REALIZERS = {
    GenericOmnigentHostRealizer.ref: GenericOmnigentHostRealizer,
}


def test_every_trusted_realizer_is_covered_by_the_routing_assertion() -> None:
    assert set(_PRODUCTION_REALIZERS) == set(KNOWN_REALIZERS)


@pytest.mark.parametrize(
    "realizer_class", sorted(_PRODUCTION_REALIZERS.values(), key=lambda cls: cls.ref)
)
def test_no_production_realizer_names_its_own_turn_source(realizer_class) -> None:
    """The wrapper derives the source; a realizer may not assert one."""

    tree = ast.parse(textwrap.dedent(inspect.getsource(realizer_class.execute)))
    body = tree.body[0].body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        body = body[1:]  # the docstring may legitimately name the vocabulary
    code = "\n".join(ast.unparse(node) for node in body)
    assert "deliver_canonical_turn(" in code
    assert "turn_source" not in code
    assert "TurnSource." not in code
