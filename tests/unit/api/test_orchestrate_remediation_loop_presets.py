"""MoonSpec orchestrate presets reach the one workflow-owned remediation loop.

MoonLadderStudios/MoonMind#4268: the GitHub, Jira, and MoonSpec orchestrate
seed presets expand through the real catalog, and the resulting loop
declaration drives the production ``MoonMindRunWorkflow`` loop controller.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from api_service.services.presets.catalog import PresetCatalogService
from moonmind.workflows.temporal.remediation_loop import (
    RemediationLoopSpec,
    validate_remediation_loop_agent_instructions,
)
from moonmind.workflows.temporal.remediation_workspace_head import (
    RemediationWorkspaceHead,
)
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.api.test_presets_service import template_db

pytestmark = [pytest.mark.asyncio]

_SEED_DIR = Path(__file__).resolve().parents[3] / "api_service" / "data" / "presets"

_ORCHESTRATE_INPUTS: dict[str, dict[str, Any]] = {
    "github-issue-orchestrate": {
        "github_issue": {
            "repository": "MoonLadderStudios/MoonMind",
            "number": 4268,
            "url": "https://github.com/MoonLadderStudios/MoonMind/issues/4268",
        },
        "constraints": "Keep scope bounded.",
    },
    "jira-orchestrate": {
        "jira_issue_key": "MM-4268",
        "constraints": "Keep scope bounded.",
    },
    "moonspec-orchestrate": {
        "feature_request": "MM-4268: one bounded story",
        "constraints": "Keep scope bounded.",
    },
}

_LOOP_RUNTIME = {
    "mode": "codex_cli",
    "model": "gpt-5.6-sol",
    "effort": "high",
    "executionProfileRef": "codex_openai_oauth",
}


async def _expand(tmp_path, slug: str, **overrides: Any) -> list[dict[str, Any]]:
    inputs = {**_ORCHESTRATE_INPUTS[slug], **overrides}
    async with template_db(tmp_path) as session_maker, session_maker() as session:
        service = PresetCatalogService(session)
        await service.sync_seed_templates(seed_dir=_SEED_DIR)
        expanded = await service.expand_template(
            slug=slug,
            scope="global",
            scope_ref=None,
            inputs=inputs,
            context={},
        )
    return expanded["steps"]


def _loop_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        step
        for step in steps
        if isinstance((step.get("annotations") or {}).get("remediationLoop"), dict)
    ]


@pytest.mark.parametrize("slug", sorted(_ORCHESTRATE_INPUTS))
@pytest.mark.parametrize("run_verify", ["omitted", True])
async def test_orchestrate_presets_declare_one_loop_instead_of_copied_pairs(
    tmp_path, slug: str, run_verify: Any
) -> None:
    overrides = {} if run_verify == "omitted" else {"run_verify": run_verify}
    steps = await _expand(tmp_path, slug, **overrides)

    assert not any(
        (step.get("annotations") or {}).get("moonSpecRemediationAttempt") is not None
        for step in steps
    )
    assert not any("/clear" in str(step.get("instructions") or "") for step in steps)
    loop_steps = _loop_steps(steps)
    assert len(loop_steps) == 1
    loop_step = loop_steps[0]
    assert loop_step["annotations"]["issueImplementRole"] == (
        "moonspec-remediation-loop"
    )
    loop_index = steps.index(loop_step)
    verifier = steps[loop_index - 1]
    assert verifier["skill"]["id"] == "moonspec-verify"
    assert verifier["repositoryOperation"] == "read"

    spec = RemediationLoopSpec.model_validate(
        loop_step["annotations"]["remediationLoop"]
    )
    validate_remediation_loop_agent_instructions(spec)
    assert spec.workspace_policy == "continue_from_loop_head"
    assert spec.budgets.hard_max_attempts == 6
    assert spec.remediation_tool.inputs["selectedSkill"] == "moonspec-implement"
    assert spec.remediation_tool.inputs["repositoryOperation"] == "write"
    assert spec.verification_tool.inputs["selectedSkill"] == "moonspec-verify"
    assert spec.verification_tool.inputs["repositoryOperation"] == "read"
    assert spec.verification_tool.inputs["constraints"] == "Keep scope bounded."


@pytest.mark.parametrize("slug", sorted(_ORCHESTRATE_INPUTS))
async def test_orchestrate_run_verify_omitted_matches_true_and_false_drops_stage(
    tmp_path, slug: str
) -> None:
    for name in ("omitted", "true", "false"):
        (tmp_path / name).mkdir()
    omitted = await _expand(tmp_path / "omitted", slug)
    enabled = await _expand(tmp_path / "true", slug, run_verify=True)
    disabled = await _expand(tmp_path / "false", slug, run_verify=False)

    assert [step["title"] for step in omitted] == [step["title"] for step in enabled]
    disabled_titles = [step["title"] for step in disabled]
    for title in (
        "Verify completion",
        "Remediation loop controller",
        "Reconcile declarative docs",
    ):
        assert title in [step["title"] for step in enabled]
        assert title not in disabled_titles
    assert _loop_steps(disabled) == []

    def task_generation(steps: list[dict[str, Any]]) -> str:
        return next(
            step["instructions"]
            for step in steps
            if step["title"] == "Generate TDD task breakdown"
        )

    assert "verify" in task_generation(enabled).lower()
    assert "do not add a final" in task_generation(disabled)
    # Repository-required tests stay mandatory without the optional stage.
    assert "red-first unit tests" in task_generation(disabled)


class _Logger:
    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    warning = info


@pytest.fixture
def run_workflow(monkeypatch: pytest.MonkeyPatch) -> run_module.MoonMindRunWorkflow:
    workflow = run_module.MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "MoonLadderStudios/MoonMind"
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: False)
    monkeypatch.setattr(
        run_module.workflow, "now", lambda: datetime.now(timezone.utc)
    )
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        type(
            "WorkflowInfo",
            (),
            {
                "namespace": "default",
                "workflow_id": "wf-4268",
                "run_id": "run-1",
                "search_attributes": {},
                "parent": None,
            },
        ),
    )
    monkeypatch.setattr(run_module.workflow, "logger", _Logger())
    return workflow


def _controller_node(loop_step: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "controller",
        "tool": {"type": "agent_runtime", "name": "codex_cli"},
        "inputs": {"runtime": dict(_LOOP_RUNTIME)},
        "annotations": dict(loop_step["annotations"]),
    }


def _head(checkpoint: str, version: int) -> dict[str, object]:
    return {
        "loopId": "moonspec-orchestrate-remediation",
        "branchRef": "checkpoint-branch:moonspec-orchestrate-remediation",
        "rootCheckpointRef": "artifact://workspace/C0",
        "rootWorkspaceDigest": "sha256:c0",
        "rootWorkspaceIdentityDigest": "sha256:" + ("c" * 64),
        "headCheckpointRef": f"artifact://workspace/{checkpoint}",
        "headWorkspaceDigest": f"sha256:{checkpoint.lower()}",
        "headWorkspaceIdentityDigest": "sha256:" + ("c" * 64),
        "headAttemptOrdinal": version - 1,
        "headVersion": version,
    }


@pytest.mark.parametrize("slug", sorted(_ORCHESTRATE_INPUTS))
async def test_expanded_orchestrate_loop_skips_repairs_after_initial_pass(
    tmp_path, slug: str, run_workflow: run_module.MoonMindRunWorkflow
) -> None:
    loop_step = _loop_steps(await _expand(tmp_path, slug))[0]
    run_workflow._initialize_remediation_loop_controller(
        ordered_nodes=[_controller_node(loop_step)]
    )
    run_workflow._step_ledger_rows = []
    run_workflow._write_json_artifact = AsyncMock(
        return_value="artifact://decision/pass"
    )
    ordered_nodes: list[dict[str, Any]] = []

    admitted = await run_workflow._evaluate_dynamic_remediation_verification(
        ordered_nodes=ordered_nodes,
        verdict="FULLY_IMPLEMENTED",
        gate_result_ref="artifact://verification/V0",
        remaining_work_ref=None,
    )

    assert admitted is False
    assert ordered_nodes == []
    projection = run_workflow._publish_context["remediationLoop"]
    assert projection["status"] == "accepted"
    assert projection["attemptOrdinal"] == 0
    assert projection["materializedAttempts"] == []


@pytest.mark.parametrize("slug", sorted(_ORCHESTRATE_INPUTS))
async def test_expanded_orchestrate_loop_preserves_c0_c1_c2_and_verifies_c2(
    tmp_path, slug: str, run_workflow: run_module.MoonMindRunWorkflow
) -> None:
    loop_step = _loop_steps(await _expand(tmp_path, slug))[0]
    run_workflow._initialize_remediation_loop_controller(
        ordered_nodes=[_controller_node(loop_step)]
    )
    run_workflow._step_ledger_rows = []
    run_workflow._write_json_artifact = AsyncMock(
        side_effect=[
            "artifact://decision/D0",
            "artifact://decision/D1",
            "artifact://decision/D2",
        ]
    )
    ordered_nodes: list[dict[str, Any]] = []
    await run_workflow._evaluate_dynamic_remediation_verification(
        ordered_nodes=ordered_nodes,
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://verification/V0",
        remaining_work_ref="artifact://remaining/R0",
    )
    run_workflow._remediation_workspace_head = RemediationWorkspaceHead.model_validate(
        _head("C0", 1)
    )

    for ordinal, parent, child in ((1, "C0", "C1"), (2, "C1", "C2")):
        remediation, verification = ordered_nodes[-2:]
        assert remediation["annotations"]["moonSpecRemediationAttempt"] == ordinal
        assert remediation["inputs"]["selectedSkill"] == "moonspec-implement"
        assert remediation["inputs"]["repositoryOperation"] == "write"
        assert verification["inputs"]["selectedSkill"] == "moonspec-verify"
        assert verification["inputs"]["repositoryOperation"] == "read"
        assert verification["inputs"]["readOnlyWorkspaceHead"] is True
        remediation_inputs = dict(remediation["inputs"])
        run_workflow._inject_remediation_workspace_baseline(
            node=remediation,
            node_inputs=remediation_inputs,
        )
        assert remediation_inputs["remediationAttemptInput"]["baseCheckpointRef"] == (
            f"artifact://workspace/{parent}"
        )
        run_workflow._advance_remediation_workspace_head(
            node=remediation,
            node_inputs=remediation_inputs,
            execution_result={
                "outputs": {
                    "remediationAttemptOutput": {
                        "attemptEvidenceRef": f"artifact://attempt/{ordinal}",
                        "parentCheckpointRef": f"artifact://workspace/{parent}",
                        "parentWorkspaceDigest": f"sha256:{parent.lower()}",
                        "outputCheckpointRef": f"artifact://workspace/{child}",
                        "outputWorkspaceDigest": f"sha256:{child.lower()}",
                        "checkpointManifestRef": f"artifact://manifest/{child}",
                        "outcome": "candidate_captured",
                    }
                }
            },
            step_execution_id=f"wf-4268:run-1:loop:remediation:{ordinal}",
        )
        verification_inputs = dict(verification["inputs"])
        run_workflow._inject_remediation_verification_baseline(
            node=verification,
            node_inputs=verification_inputs,
        )
        assert verification_inputs["remediationWorkspaceHeadRef"] == (
            f"artifact://workspace/{child}"
        )
        admitted = await run_workflow._evaluate_dynamic_remediation_verification(
            ordered_nodes=ordered_nodes,
            verdict="ADDITIONAL_WORK_NEEDED" if ordinal == 1 else "FULLY_IMPLEMENTED",
            gate_result_ref=f"artifact://verification/V{ordinal}",
            remaining_work_ref=(
                f"artifact://remaining/R{ordinal}" if ordinal == 1 else None
            ),
        )
        assert admitted is (ordinal == 1)

    assert len(ordered_nodes) == 4
    projection = run_workflow._publish_context["remediationLoop"]
    assert projection["status"] == "accepted"
    assert projection["attemptOrdinal"] == 2
    assert projection["workspaceHeadRef"] == "artifact://workspace/C2"
    assert projection["latestVerificationRef"] == "artifact://verification/V2"
    assert projection["consumedBudgets"]["attempts"] == 2
