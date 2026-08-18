"""Unit tests for agent dispatch helpers in MoonMind.UserWorkflow.

Pure unit tests — no Temporal test server needed.
"""

import inspect
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

pytest.importorskip("temporalio")

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
    SkillImplementationContract,
    SkillTerminalContract,
)
from moonmind.workflows.temporal.workflows.run import (
    RUN_AGENT_REQUIRED_CAPABILITIES_PROPAGATION_PATCH,
    RUN_ASSESSMENT_ATTACHMENT_HANDOFF_PATCH,
    RUN_ASSESSMENT_PARAMETER_INJECTION_PATCH,
    RUN_AUTO_PUBLISH_TERMINAL_CONTRACT_PATCH,
    RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH,
    RUN_CHECKPOINT_RECOVERY_STATE_MACHINE_PATCH,
    RUN_COMPILED_OMNIGENT_EXECUTION_INTENT_PATCH,
    RUN_EMPTY_AGENT_SKILLSET_SNAPSHOT_PATCH,
    RUN_EXISTING_SKILLSET_TERMINAL_CONTRACT_PATCH,
    RUN_EXTERNAL_PUBLISHED_BRANCH_REMEDIATION_PATCH,
    RUN_ISSUE_BRIEF_ATTACHMENT_HANDOFF_PATCH,
    RUN_JSON_ARTIFACT_WRITE_COMPLETE_PATCH,
    RUN_MOONSPEC_GATE_PREVIOUS_OUTPUTS_HANDOFF_PATCH,
    RUN_MOONSPEC_VERIFY_ATTACHMENT_HANDOFF_PATCH,
    RUN_OMNIGENT_AGENT_PROFILE_SNAPSHOT_COMPILER_PATCH,
    RUN_OMNIGENT_AUTHORED_SELECTION_COMPILER_PATCH,
    RUN_OMNIGENT_CHECKPOINT_BRANCH_TURN_REQUEST_PATCH,
    RUN_OMNIGENT_STOCK_AGENT_IDENTITY_PATCH,
    RUN_PR_RESOLVER_SKILL_OWNED_EXECUTION_PATCH,
    RUN_PUBLISH_MODE_REPOSITORY_OPERATION_PATCH,
    RUN_PUBLISHED_BRANCH_HANDOFF_PATCH,
    RUN_REPOSITORY_BOUND_NO_COMMIT_OUTCOME_PATCH,
    RUN_RESOLVED_SKILL_TERMINAL_CONTRACT_PATCH,
    RUN_SLOT_CONTINUITY_PATCH,
    RUN_STEP_EXECUTION_NAMING_PATCH,
    RUN_TRUSTED_NO_COMMIT_REPOSITORY_OUTCOME_PATCH,
    RUN_TRUSTED_PR_RESOLVER_NATIVE_BINDING_PATCH,
    MoonMindRunWorkflow,
)


def test_auto_publish_compiles_execution_bound_terminal_contract() -> None:
    info = SimpleNamespace(
        namespace="default",
        workflow_id="mm:auto-publish-contract",
        run_id="run-auto-publish-contract",
        parent=None,
    )
    with (
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=info,
        ),
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: (
                patch_id == RUN_AUTO_PUBLISH_TERMINAL_CONTRACT_PATCH
            ),
        ),
    ):
        request = MoonMindRunWorkflow()._build_agent_execution_request(
            node_inputs={
                "targetRuntime": "claude_code",
                "selectedSkill": "fix-comments",
            },
            node_id="fix-comments",
            tool_name="claude_code",
            workflow_parameters={"publishMode": "auto"},
        )

    assert request.terminal_contract is not None
    assert request.terminal_contract.contract_id == "auto_publish_terminal.v1"
    assert request.terminal_contract.relative_path == "artifacts/publish_result.json"


def test_auto_publish_external_provider_does_not_compile_workspace_contract() -> None:
    info = SimpleNamespace(
        namespace="default",
        workflow_id="mm:auto-publish-external",
        run_id="run-auto-publish-external",
        parent=None,
    )
    with (
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=info,
        ),
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: (
                patch_id == RUN_AUTO_PUBLISH_TERMINAL_CONTRACT_PATCH
            ),
        ),
    ):
        request = MoonMindRunWorkflow()._build_agent_execution_request(
            node_inputs={
                "targetRuntime": "jules",
                "selectedSkill": "fix-comments",
            },
            node_id="fix-comments",
            tool_name="jules",
            workflow_parameters={"publishMode": "auto"},
        )

    assert request.terminal_contract is None


def test_auto_publish_sandbox_authority_compiles_workspace_contract() -> None:
    info = SimpleNamespace(
        namespace="default",
        workflow_id="mm:auto-publish-sandbox",
        run_id="run-auto-publish-sandbox",
        parent=None,
    )
    with (
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=info,
        ),
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: (
                patch_id == RUN_AUTO_PUBLISH_TERMINAL_CONTRACT_PATCH
            ),
        ),
    ):
        request = MoonMindRunWorkflow()._build_agent_execution_request(
            node_inputs={
                "targetRuntime": "omnigent",
                "selectedSkill": "fix-comments",
            },
            node_id="fix-comments",
            tool_name="omnigent",
            workflow_parameters={"publishMode": "auto"},
        )

    assert request.terminal_contract is not None
    assert request.terminal_contract.contract_id == "auto_publish_terminal.v1"


def test_agent_request_propagates_required_capabilities_with_replay_gate() -> None:
    info = SimpleNamespace(
        namespace="default",
        workflow_id="mm:required-capabilities",
        run_id="run-required-capabilities",
        parent=None,
    )
    workflow_parameters = {
        "requiredCapabilities": ["git", "gh"],
        "workspaceSpec": {"repository": "MoonLadderStudios/MoonMind"},
    }

    with (
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=info,
        ),
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: (
                patch_id == RUN_AGENT_REQUIRED_CAPABILITIES_PROPAGATION_PATCH
            ),
        ),
    ):
        current = MoonMindRunWorkflow()._build_agent_execution_request(
            node_inputs={"runtime": {"mode": "omnigent"}},
            node_id="node-1",
            tool_name="omnigent",
            workflow_parameters=workflow_parameters,
        )

    with (
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=info,
        ),
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            return_value=False,
        ),
    ):
        replaying = MoonMindRunWorkflow()._build_agent_execution_request(
            node_inputs={"runtime": {"mode": "omnigent"}},
            node_id="node-1",
            tool_name="omnigent",
            workflow_parameters=workflow_parameters,
        )

    assert current.parameters["requiredCapabilities"] == ["git", "gh"]
    assert current.workspace_spec["repository"] == "MoonLadderStudios/MoonMind"
    assert current.parameters["repository"] == "MoonLadderStudios/MoonMind"
    assert "requiredCapabilities" not in replaying.parameters
    assert "repository" not in replaying.parameters


def _resolved_skill(skill_name: str) -> ResolvedSkillEntry:
    return ResolvedSkillEntry(
        skill_name=skill_name,
        provenance=AgentSkillProvenance(source_kind=AgentSkillSourceKind.BUILT_IN),
    )


class TestAgentKindForId(unittest.TestCase):
    """Verify the _agent_kind_for_id static method."""

    def test_managed_agent_ids(self) -> None:
        wf = MoonMindRunWorkflow()
        for agent_id in ("claude", "claude_code", "codex", "codex_cli"):
            self.assertEqual(wf._agent_kind_for_id(agent_id), "managed", f"{agent_id} should be managed")

    def test_external_agent_ids(self) -> None:
        wf = MoonMindRunWorkflow()
        for agent_id in ("jules", "custom_agent"):
            self.assertEqual(wf._agent_kind_for_id(agent_id), "external", f"{agent_id} should be external")

    def test_case_insensitive(self) -> None:
        wf = MoonMindRunWorkflow()
        self.assertEqual(wf._agent_kind_for_id("Gemini_CLI"), "managed")
        self.assertEqual(wf._agent_kind_for_id("Codex_CLI"), "managed")
        self.assertEqual(wf._agent_kind_for_id("CLAUDE_CODE"), "managed")

    def test_hyphenated_managed_agent_ids(self) -> None:
        wf = MoonMindRunWorkflow()
        for agent_id in ("claude-code", "codex-cli"):
            self.assertEqual(
                wf._agent_kind_for_id(agent_id),
                "managed",
                f"{agent_id} should be managed",
            )

class TestSlotContinuityMetadata(unittest.TestCase):
    def test_profile_runtime_mismatch_guard_is_replay_only(self) -> None:
        inherited_source = inspect.getsource(
            MoonMindRunWorkflow._inherited_execution_profile_ref
        )
        validated_source = inspect.getsource(
            MoonMindRunWorkflow._validated_execution_profile_ref
        )

        self.assertIn("self._workflow_is_replaying()", inherited_source)
        self.assertIn("self._workflow_is_replaying()", validated_source)
        self.assertIn("raise ValueError", inherited_source)
        self.assertIn("raise ValueError", validated_source)

    def test_step_execution_naming_marker_precedes_manifest_marker(self) -> None:
        source = inspect.getsource(MoonMindRunWorkflow._run_execution_stage)
        self.assertEqual(
            RUN_STEP_EXECUTION_NAMING_PATCH,
            "run-step-execution-naming-v1",
        )

        naming_index = source.index(
            "step_execution_naming_enabled = workflow.patched("
        )
        manifest_index = source.index(
            "step_execution_manifest_enabled = workflow.patched(",
            naming_index,
        )

        self.assertLess(naming_index, manifest_index)

    def test_json_artifact_write_complete_is_replay_patch_guarded(self) -> None:
        source = inspect.getsource(MoonMindRunWorkflow._write_json_artifact)
        self.assertEqual(
            RUN_JSON_ARTIFACT_WRITE_COMPLETE_PATCH,
            "run-json-artifact-write-complete-v1",
        )

        guard_index = source.index(
            "workflow.patched(RUN_JSON_ARTIFACT_WRITE_COMPLETE_PATCH)"
        )
        write_complete_index = source.index(
            '"artifact.write_complete"',
            guard_index,
        )

        self.assertLess(guard_index, write_complete_index)

    def test_assessment_parameter_injection_is_replay_patch_guarded(self) -> None:
        source = inspect.getsource(MoonMindRunWorkflow._build_agent_execution_request)
        self.assertEqual(
            RUN_ASSESSMENT_PARAMETER_INJECTION_PATCH,
            "run-assessment-parameter-injection-v1",
        )

        guard_index = source.index(
            "workflow.patched(RUN_ASSESSMENT_PARAMETER_INJECTION_PATCH)"
        )
        injection_index = source.index(
            "self._ensure_assessment_parameters(",
            guard_index,
        )

        self.assertLess(guard_index, injection_index)

    def test_checkpoint_recovery_request_shape_is_replay_patch_guarded(self) -> None:
        source = inspect.getsource(MoonMindRunWorkflow._build_agent_execution_request)
        self.assertEqual(
            RUN_CHECKPOINT_RECOVERY_STATE_MACHINE_PATCH,
            "run-checkpoint-recovery-state-machine-v1",
        )

        guard_index = source.index(
            "RUN_CHECKPOINT_RECOVERY_STATE_MACHINE_PATCH"
        )
        injection_index = source.index(
            "checkpoint_recovery = dict(self._recovery_source)", guard_index
        )

        self.assertLess(guard_index, injection_index)

    def test_omnigent_checkpoint_branch_turn_request_shape_is_replay_patch_guarded(
        self,
    ) -> None:
        source = inspect.getsource(MoonMindRunWorkflow._build_agent_execution_request)
        self.assertEqual(
            RUN_OMNIGENT_CHECKPOINT_BRANCH_TURN_REQUEST_PATCH,
            "run-omnigent-checkpoint-branch-turn-request-v1",
        )

        guard_index = source.index(
            "RUN_OMNIGENT_CHECKPOINT_BRANCH_TURN_REQUEST_PATCH"
        )
        prompt_index = source.index(
            "_apply_omnigent_checkpoint_branch_turn_prompt",
            guard_index,
        )

        self.assertLess(guard_index, prompt_index)

    def test_slot_continuity_patch_marker_precedes_launch_block_short_circuit(self) -> None:
        source = inspect.getsource(MoonMindRunWorkflow._run_execution_stage)
        self.assertEqual(RUN_SLOT_CONTINUITY_PATCH, "run-slot-continuity-v1")
        marker_index = source.index(
            "slot_continuity_enabled = workflow.patched("
        )
        launch_block_index = source.index(
            "if self._is_step_execution_launch_blocked(",
            marker_index,
        )

        self.assertLess(marker_index, launch_block_index)

    def test_marks_request_when_next_step_uses_same_managed_runtime(self) -> None:
        wf = MoonMindRunWorkflow()
        request = AgentExecutionRequest(
            agentKind="managed",
            agentId="codex_cli",
            correlationId="run-1",
            idempotencyKey="run-1:step-1",
        )
        ordered_nodes = [
            {
                "tool": {"type": "agent_runtime", "name": "codex_cli"},
                "inputs": {"runtime": {"mode": "codex_cli"}},
            },
            {
                "tool": {"type": "agent_runtime", "name": "codex_cli"},
                "inputs": {"runtime": {"mode": "codex_cli"}},
            },
        ]

        wf._mark_slot_continuity_for_next_step(
            request=request,
            ordered_nodes=ordered_nodes,
            current_index=1,
        )

        continuity = request.parameters["metadata"]["moonmind"]["slotContinuity"]
        self.assertTrue(continuity["reserveForImmediateFollowup"])

    def test_does_not_mark_request_when_next_step_uses_different_runtime(self) -> None:
        wf = MoonMindRunWorkflow()
        request = AgentExecutionRequest(
            agentKind="managed",
            agentId="codex_cli",
            correlationId="run-1",
            idempotencyKey="run-1:step-1",
        )
        ordered_nodes = [
            {
                "tool": {"type": "agent_runtime", "name": "codex_cli"},
                "inputs": {"runtime": {"mode": "codex_cli"}},
            },
            {
                "tool": {"type": "agent_runtime", "name": "claude_code"},
                "inputs": {"runtime": {"mode": "claude_code"}},
            },
        ]

        wf._mark_slot_continuity_for_next_step(
            request=request,
            ordered_nodes=ordered_nodes,
            current_index=1,
        )

        self.assertEqual(request.parameters, {})

class TestAgentSkillSnapshotResolution(unittest.IsolatedAsyncioTestCase):
    async def test_auto_agent_node_resolves_empty_snapshot_before_launch(
        self,
    ) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        wf._step_ledger_rows = [{"logicalStepId": "node-1", "attempt": 1}]
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-wf-node-1",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/empty-node-1",
            skills=[],
        )
        info = SimpleNamespace(
            namespace="default",
            workflow_id="mm:717ea339-77ab-4e72-bd46-969b009b5508",
            run_id="019fd32f-cbfd-7846-bee9-79c87ebae5f8",
            parent=None,
        )

        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
                new=AsyncMock(return_value=resolved),
            ) as execute_activity,
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: (
                    patch_id == RUN_EMPTY_AGENT_SKILLSET_SNAPSHOT_PATCH
                ),
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
        ):
            resolved_ref = await wf._resolve_agent_node_skillset_ref(
                task_skills=None,
                node_inputs={
                    "runtime": {"mode": "omnigent"},
                    "selectedSkill": "auto",
                },
                node_id="node-1",
                existing_skillset_ref=None,
            )
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {"mode": "omnigent"},
                    "selectedSkill": "auto",
                },
                node_id="node-1",
                tool_name="omnigent",
                resolved_skillset_ref=resolved_ref,
            )

        self.assertEqual(resolved_ref, "artifact://skillsets/empty-node-1")
        selector = execute_activity.call_args.kwargs["args"][0]
        self.assertEqual(
            selector.model_dump(mode="json", exclude_none=True),
            {"sets": [], "include": [], "exclude": []},
        )
        self.assertEqual(request.resolved_skillset_ref, resolved_ref)
        self.assertIsNotNone(request.step_execution)
        self.assertEqual(request.step_execution.resolved_skillset_ref, resolved_ref)

    async def test_auto_agent_node_preserves_pre_patch_history(self) -> None:
        wf = MoonMindRunWorkflow()

        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
                new=AsyncMock(),
            ) as execute_activity,
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: (
                    patch_id != RUN_EMPTY_AGENT_SKILLSET_SNAPSHOT_PATCH
                ),
            ),
        ):
            resolved_ref = await wf._resolve_agent_node_skillset_ref(
                task_skills=None,
                node_inputs={"selectedSkill": "auto"},
                node_id="node-1",
                existing_skillset_ref=None,
            )

        self.assertIsNone(resolved_ref)
        execute_activity.assert_not_awaited()

    async def test_existing_resolved_skillset_supplies_owned_terminal_contract(
        self,
    ) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        existing_ref = "artifact://skillsets/pr-resolver-existing"
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-pr-resolver-existing",
            resolved_at=datetime.now(UTC),
            skills=[
                ResolvedSkillEntry(
                    skill_name="pr-resolver",
                    provenance=AgentSkillProvenance(
                        source_kind=AgentSkillSourceKind.BUILT_IN
                    ),
                    terminal_contract=SkillTerminalContract(
                        contract_id="pr_resolver_terminal.v1",
                        relative_path="var/pr_resolver/result.json",
                        expected_schema_version=(
                            "moonmind.pr-resolver-result.v1"
                        ),
                    ),
                )
            ],
        )
        parent = SimpleNamespace(
            workflow_id="merge-automation:owner",
            run_id="merge-run-1",
        )
        info = SimpleNamespace(
            namespace="default",
            workflow_id="resolver:pr:3353",
            run_id="resolver-run-1",
            parent=parent,
        )

        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.execute_typed_activity",
                new=AsyncMock(return_value=resolved.model_dump(mode="json")),
            ) as read_artifact,
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                return_value=True,
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
        ):
            resolved_ref = await wf._resolve_agent_node_skillset_ref(
                task_skills=None,
                node_inputs={"selectedSkill": "pr-resolver"},
                node_id="node-1",
                existing_skillset_ref=existing_ref,
            )
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "codex_cli",
                    "selectedSkill": "pr-resolver",
                },
                node_id="node-1",
                tool_name="codex_cli",
                resolved_skillset_ref=resolved_ref,
                workflow_parameters={
                    "mergeGate": {
                        "parentWorkflowId": parent.workflow_id,
                        "pullRequestUrl": (
                            "https://github.com/MoonLadderStudios/MoonMind/pull/3353"
                        ),
                    }
                },
            )

        self.assertEqual(resolved_ref, existing_ref)
        read_artifact.assert_awaited_once()
        self.assertIsNotNone(request.terminal_contract)
        self.assertEqual(
            request.terminal_contract.contract_id,
            "pr_resolver_terminal.v1",
        )
        self.assertIsNotNone(request.terminal_continuation_authority)
        self.assertEqual(
            request.terminal_continuation_authority.owner_workflow_id,
            parent.workflow_id,
        )

    async def test_existing_skillset_history_does_not_add_artifact_read(
        self,
    ) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        existing_ref = "artifact://skillsets/pr-resolver-existing"

        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.execute_typed_activity",
                new=AsyncMock(),
            ) as read_artifact,
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: (
                    patch_id
                    != RUN_EXISTING_SKILLSET_TERMINAL_CONTRACT_PATCH
                ),
            ),
        ):
            resolved_ref = await wf._resolve_agent_node_skillset_ref(
                task_skills=None,
                node_inputs={"selectedSkill": "pr-resolver"},
                node_id="node-1",
                existing_skillset_ref=existing_ref,
            )

        self.assertEqual(resolved_ref, existing_ref)
        read_artifact.assert_not_awaited()
        self.assertNotIn(
            "node-1",
            wf._resolved_skill_terminal_contract_by_step,
        )

    async def test_resolved_skill_terminal_contract_reaches_owned_agent_run(
        self,
    ) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-pr-resolver",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/pr-resolver",
            skills=[
                ResolvedSkillEntry(
                    skill_name="pr-resolver",
                    provenance=AgentSkillProvenance(
                        source_kind=AgentSkillSourceKind.BUILT_IN
                    ),
                    terminal_contract=SkillTerminalContract(
                        contract_id="pr_resolver_terminal.v1",
                        relative_path="var/pr_resolver/result.json",
                        expected_schema_version=(
                            "moonmind.pr-resolver-result.v1"
                        ),
                    ),
                )
            ],
        )
        parent = SimpleNamespace(
            workflow_id="merge-automation:owner",
            run_id="merge-run-1",
        )
        info = SimpleNamespace(
            namespace="default",
            workflow_id="resolver:pr:2189",
            run_id="resolver-run-1",
            parent=parent,
        )

        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
                new=AsyncMock(return_value=resolved),
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                return_value=True,
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
        ):
            resolved_ref = await wf._resolve_agent_node_skillset_ref(
                task_skills=None,
                node_inputs={"selectedSkill": "pr-resolver"},
                node_id="node-1",
                existing_skillset_ref=None,
            )
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "codex_cli",
                    "selectedSkill": "pr-resolver",
                },
                node_id="node-1",
                tool_name="codex_cli",
                resolved_skillset_ref=resolved_ref,
                workflow_parameters={
                    "mergeGate": {
                        "parentWorkflowId": parent.workflow_id,
                        "pullRequestUrl": (
                            "https://github.com/MoonLadderStudios/Tactics/pull/2189"
                        ),
                    }
                },
            )

        self.assertIsNotNone(request.terminal_contract)
        self.assertEqual(
            request.terminal_contract.contract_id,
            "pr_resolver_terminal.v1",
        )
        self.assertEqual(
            request.terminal_contract.execution_ref,
            "resolver:pr:2189:resolver-run-1:node-1:execution:1",
        )
        self.assertIsNotNone(request.terminal_continuation_authority)
        self.assertEqual(
            request.terminal_continuation_authority.owner_workflow_id,
            parent.workflow_id,
        )
        self.assertEqual(
            request.terminal_continuation_authority.owner_run_id,
            parent.run_id,
        )

    async def test_batch_dependabot_terminal_contract_reaches_agent_run(
        self,
    ) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-batch-dependabot",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/batch-dependabot",
            skills=[
                ResolvedSkillEntry(
                    skill_name="batch-dependabot-resolver",
                    provenance=AgentSkillProvenance(
                        source_kind=AgentSkillSourceKind.BUILT_IN
                    ),
                    terminal_contract=SkillTerminalContract(
                        contract_id="batch_dependabot_resolver_fanout.v1",
                        relative_path=(
                            "artifacts/batch_dependabot_resolver_result.json"
                        ),
                        expected_schema_version=(
                            "moonmind.batch-dependabot-resolver-result.v1"
                        ),
                    ),
                )
            ],
        )
        info = SimpleNamespace(
            namespace="default",
            workflow_id="batch:dependabot",
            run_id="batch-run-1",
            parent=None,
        )

        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
                new=AsyncMock(return_value=resolved),
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                return_value=True,
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
        ):
            resolved_ref = await wf._resolve_agent_node_skillset_ref(
                task_skills=None,
                node_inputs={"selectedSkill": "batch-dependabot-resolver"},
                node_id="node-1",
                existing_skillset_ref=None,
            )
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "codex_cli",
                    "selectedSkill": "batch-dependabot-resolver",
                },
                node_id="node-1",
                tool_name="codex_cli",
                resolved_skillset_ref=resolved_ref,
                workflow_parameters={},
            )

        self.assertIsNotNone(request.terminal_contract)
        self.assertEqual(
            request.terminal_contract.contract_id,
            "batch_dependabot_resolver_fanout.v1",
        )
        self.assertEqual(
            request.terminal_contract.execution_ref,
            "batch:dependabot:batch-run-1:node-1:execution:1",
        )
        self.assertIsNone(request.terminal_continuation_authority)

    async def test_existing_history_does_not_change_agent_request_shape(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-pr-resolver",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/pr-resolver",
            skills=[
                ResolvedSkillEntry(
                    skill_name="pr-resolver",
                    provenance=AgentSkillProvenance(
                        source_kind=AgentSkillSourceKind.BUILT_IN
                    ),
                    terminal_contract=SkillTerminalContract(
                        contract_id="pr_resolver_terminal.v1",
                        relative_path="var/pr_resolver/result.json",
                        expected_schema_version=(
                            "moonmind.pr-resolver-result.v1"
                        ),
                    ),
                )
            ],
        )

        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
                new=AsyncMock(return_value=resolved),
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: (
                    patch_id != RUN_RESOLVED_SKILL_TERMINAL_CONTRACT_PATCH
                ),
            ),
        ):
            await wf._resolve_agent_node_skillset_ref(
                task_skills=None,
                node_inputs={"selectedSkill": "pr-resolver"},
                node_id="node-1",
                existing_skillset_ref=None,
            )

        self.assertNotIn(
            "node-1",
            wf._resolved_skill_terminal_contract_by_step,
        )

    async def test_agent_node_resolves_effective_task_and_step_skills_before_launch(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-wf-step-1",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/step-1",
            skills=[],
        )

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
            new=AsyncMock(return_value=resolved),
        ) as execute_activity:
            ref = await wf._resolve_agent_node_skillset_ref(
                task_skills={
                    "include": [
                        {"name": "baseline"},
                        {"name": "remove-me"},
                    ],
                    "materializationMode": "hybrid",
                },
                node_inputs={
                    "skills": {
                        "include": [{"name": "step-only"}],
                        "exclude": ["remove-me"],
                    }
                },
                node_id="step-1",
                existing_skillset_ref=None,
            )

        self.assertEqual(ref, "artifact://skillsets/step-1")
        execute_activity.assert_awaited_once()
        args, kwargs = execute_activity.call_args
        self.assertEqual(args[0], "agent_skill.resolve")
        self.assertEqual(len(args), 1)
        activity_args = kwargs["args"]
        selector = activity_args[0]
        self.assertEqual(
            [entry.name for entry in selector.include],
            ["baseline", "step-only"],
        )
        self.assertEqual(selector.exclude, ["remove-me"])
        self.assertEqual(activity_args[1:], ["owner-1", None, False, False])

    async def test_agent_node_reads_canonical_node_skills_before_inputs_skills(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-wf-step-1",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/step-canonical",
            skills=[],
        )

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
            new=AsyncMock(return_value=resolved),
        ) as execute_activity:
            ref = await wf._resolve_agent_node_skillset_ref(
                task_skills={"include": [{"name": "baseline"}]},
                node_skills={
                    "include": [{"name": "canonical-step"}],
                    "exclude": ["legacy-input-step"],
                },
                node_inputs={
                    "skills": {
                        "include": [{"name": "legacy-input-step"}],
                    }
                },
                node_id="step-1",
                existing_skillset_ref=None,
            )

        self.assertEqual(ref, "artifact://skillsets/step-canonical")
        args, kwargs = execute_activity.call_args
        self.assertEqual(args, ("agent_skill.resolve",))
        activity_args = kwargs["args"]
        self.assertEqual(activity_args[1:], ["owner-1", None, False, False])
        selector = activity_args[0]
        self.assertEqual(
            [entry.name for entry in selector.include],
            ["baseline", "canonical-step"],
        )
        self.assertEqual(selector.exclude, ["legacy-input-step"])

    async def test_agent_node_uses_temporal_sdk_multi_arg_call_shape(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-wf-step-1",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/strict-call-shape",
            skills=[],
        )
        calls: list[tuple[str, list[Any]]] = []

        async def fake_execute_activity(
            activity: str,
            arg: Any = None,
            *,
            args: list[Any],
            **_kwargs: Any,
        ) -> ResolvedSkillSet:
            if arg is not None:
                raise AssertionError("multi-argument activities must use keyword args")
            calls.append((activity, args))
            return resolved

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
            new=fake_execute_activity,
        ):
            ref = await wf._resolve_agent_node_skillset_ref(
                task_skills={"include": [{"name": "baseline"}]},
                node_inputs={"workspaceRoot": "/workspace/repo"},
                node_id="step-1",
                existing_skillset_ref=None,
            )

        self.assertEqual(ref, "artifact://skillsets/strict-call-shape")
        self.assertEqual(calls[0][0], "agent_skill.resolve")
        activity_args = calls[0][1]
        self.assertEqual(activity_args[1:], ["owner-1", "/workspace/repo", False, False])

    async def test_agent_node_resolves_selected_skill_before_launch(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-wf-step-1",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/selected-skill",
            skills=[_resolved_skill("moonspec-breakdown")],
        )

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
            new=AsyncMock(return_value=resolved),
        ) as execute_activity:
            ref = await wf._resolve_agent_node_skillset_ref(
                task_skills=None,
                node_inputs={
                    "selectedSkill": "moonspec-breakdown",
                    "workspaceRoot": "/workspace/repo",
                },
                node_id="step-1",
                existing_skillset_ref=None,
            )

        self.assertEqual(ref, "artifact://skillsets/selected-skill")
        args, kwargs = execute_activity.call_args
        self.assertEqual(args, ("agent_skill.resolve",))
        selector = kwargs["args"][0]
        self.assertEqual(
            [entry.name for entry in selector.include],
            ["moonspec-breakdown"],
        )
        self.assertEqual(kwargs["args"][1:], ["owner-1", "/workspace/repo", False, False])

    async def test_new_pr_resolver_run_requires_skill_owned_execution(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-pr-resolver",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/pr-resolver",
            skills=[_resolved_skill("pr-resolver")],
        )

        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
                new=AsyncMock(return_value=resolved),
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                return_value=True,
            ),
        ):
            ref = await wf._resolve_agent_node_skillset_ref(
                task_skills=None,
                node_inputs={
                    "selectedSkill": "pr-resolver",
                    "workspaceRoot": "/workspace/repo",
                },
                node_id="resolver-step",
                existing_skillset_ref=None,
            )

        self.assertEqual(ref, "artifact://skillsets/pr-resolver")
        binding = wf._native_skill_binding_by_step["resolver-step"]
        self.assertFalse(binding["eligible"])
        self.assertEqual(binding["host"], "cli")
        self.assertEqual(binding["reasonCode"], "skill_owned_execution_required")
        self.assertEqual(binding["identity"]["skillName"], "pr-resolver")

    async def test_existing_pr_resolver_history_preserves_native_child_for_replay(
        self,
    ) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        legacy_entry = ResolvedSkillEntry(
            skill_name="pr-resolver",
            content_ref="art-legacy-pr-resolver",
            content_digest="sha256:legacy",
            provenance=AgentSkillProvenance(
                source_kind=AgentSkillSourceKind.BUILT_IN
            ),
            implementation=SkillImplementationContract(
                contract="pr-resolver-core/v1",
                supportedHosts=["cli", "temporal"],
                nativeHostEligible=True,
                nativeHostPolicy="moonmind_trusted",
            ),
        )
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-legacy-pr-resolver",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/legacy-pr-resolver",
            skills=[legacy_entry],
        )

        def replay_patch(patch_id: str) -> bool:
            if patch_id == RUN_PR_RESOLVER_SKILL_OWNED_EXECUTION_PATCH:
                return False
            return patch_id == RUN_TRUSTED_PR_RESOLVER_NATIVE_BINDING_PATCH

        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
                new=AsyncMock(return_value=resolved),
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=replay_patch,
            ),
        ):
            await wf._resolve_agent_node_skillset_ref(
                task_skills=None,
                node_inputs={"selectedSkill": "pr-resolver"},
                node_id="legacy-resolver-step",
                existing_skillset_ref=None,
            )

        binding = wf._native_skill_binding_by_step["legacy-resolver-step"]
        self.assertTrue(binding["eligible"])
        self.assertEqual(binding["host"], "temporal")
        self.assertEqual(binding["reasonCode"], "native_binding_accepted")

    async def test_agent_node_adds_selected_skill_to_task_level_selector(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-wf-step-1",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/selected-skill",
            skills=[
                _resolved_skill("jira-breakdown-orchestrate"),
                _resolved_skill("moonspec-breakdown"),
            ],
        )

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
            new=AsyncMock(return_value=resolved),
        ) as execute_activity:
            ref = await wf._resolve_agent_node_skillset_ref(
                task_skills={"include": [{"name": "jira-breakdown-orchestrate"}]},
                node_inputs={
                    "selectedSkill": "moonspec-breakdown",
                    "workspaceRoot": "/workspace/repo",
                },
                node_id="step-1",
                existing_skillset_ref=None,
            )

        self.assertEqual(ref, "artifact://skillsets/selected-skill")
        args, kwargs = execute_activity.call_args
        self.assertEqual(args, ("agent_skill.resolve",))
        selector = kwargs["args"][0]
        self.assertEqual(
            [entry.name for entry in selector.include],
            ["jira-breakdown-orchestrate", "moonspec-breakdown"],
        )

    async def test_agent_node_rejects_unresolved_selected_skill_before_launch(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-wf-step-1",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/selected-skill",
            skills=[_resolved_skill("jira-breakdown-orchestrate")],
        )

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
            new=AsyncMock(return_value=resolved),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "selected skill 'moonspec-breakdown' was not resolved",
            ):
                await wf._resolve_agent_node_skillset_ref(
                    task_skills={"include": [{"name": "jira-breakdown-orchestrate"}]},
                    node_inputs={
                        "selectedSkill": "moonspec-breakdown",
                        "workspaceRoot": "/workspace/repo",
                    },
                    node_id="step-1",
                    existing_skillset_ref=None,
                )

    async def test_agent_node_accepts_mapping_skill_resolution_result(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        resolved = {
            "snapshotId": "skillset-wf-step-1",
            "manifestRef": "artifact://skillsets/selected-skill",
            "skills": [{"skillName": "moonspec-breakdown"}],
        }

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
            new=AsyncMock(return_value=resolved),
        ) as execute_activity:
            ref = await wf._resolve_agent_node_skillset_ref(
                task_skills=None,
                node_inputs={
                    "selectedSkill": "moonspec-breakdown",
                    "workspaceRoot": "/workspace/repo",
                },
                node_id="step-1",
                existing_skillset_ref=None,
            )

        self.assertEqual(ref, "artifact://skillsets/selected-skill")
        execute_activity.assert_awaited_once()

    def test_resolved_skillset_field_accepts_missing_result(self) -> None:
        self.assertIsNone(
            MoonMindRunWorkflow._resolved_skillset_field(
                None,
                "manifest_ref",
                "manifestRef",
            )
        )

    async def test_agent_node_rejects_selected_skill_excluded_by_selector(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
            new=AsyncMock(),
        ) as execute_activity:
            with self.assertRaisesRegex(
                ValueError,
                "selected skill 'pr-resolver' cannot also be excluded",
            ):
                await wf._resolve_agent_node_skillset_ref(
                    task_skills={"exclude": ["pr-resolver"]},
                    node_inputs={"selectedSkill": "pr-resolver"},
                    node_id="step-1",
                    existing_skillset_ref=None,
                )

        execute_activity.assert_not_called()

    async def test_agent_node_rejects_selected_skill_excluded_by_object_selector(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"

        class EffectiveSelector:
            def model_dump(self, **_kwargs):
                return {"exclude": [{"name": "pr-resolver"}]}

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
            new=AsyncMock(),
        ) as execute_activity, patch(
            "moonmind.workflows.temporal.workflows.run.build_effective_workflow_skill_selectors",
            return_value=EffectiveSelector(),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "selected skill 'pr-resolver' cannot also be excluded",
            ):
                await wf._resolve_agent_node_skillset_ref(
                    task_skills=None,
                    node_inputs={
                        "skills": {"exclude": [{"name": "pr-resolver"}]},
                        "selectedSkill": "pr-resolver",
                    },
                    node_id="step-1",
                    existing_skillset_ref=None,
                )

        execute_activity.assert_not_called()

    async def test_agent_node_rejects_versioned_skill_selector_before_launch(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-wf-step-1",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/baseline",
            skills=[_resolved_skill("baseline")],
        )

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
            new=AsyncMock(return_value=resolved),
        ) as execute_activity:
            with self.assertRaisesRegex(
                ValidationError,
                "workflow.skills.include\\[\\] must not include semantic versions",
            ):
                await wf._resolve_agent_node_skillset_ref(
                    task_skills={
                        "include": [{"name": "baseline", "version": "9.9.9"}]
                    },
                    node_inputs={},
                    node_id="step-1",
                    existing_skillset_ref=None,
                )

        execute_activity.assert_not_called()

    async def test_agent_node_reuses_existing_skillset_ref_without_reresolution(self) -> None:
        wf = MoonMindRunWorkflow()

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
            new=AsyncMock(),
        ) as execute_activity:
            ref = await wf._resolve_agent_node_skillset_ref(
                task_skills={"include": [{"name": "baseline"}]},
                node_inputs={"skills": {"exclude": ["baseline"]}},
                node_id="step-1",
                existing_skillset_ref="artifact://skillsets/original",
            )

        self.assertEqual(ref, "artifact://skillsets/original")
        execute_activity.assert_not_called()

    async def test_agent_node_does_not_reuse_registry_snapshot_as_skillset_ref(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._owner_id = "owner-1"
        resolved = ResolvedSkillSet(
            snapshot_id="skillset-wf-step-1",
            resolved_at=datetime.now(UTC),
            manifest_ref="artifact://skillsets/resolved-from-intent",
            skills=[],
        )

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
            new=AsyncMock(return_value=resolved),
        ) as execute_activity:
            existing = wf._existing_agent_skillset_ref(
                parameters={},
                node={
                    "metadata": {
                        "registry_snapshot": {
                            "artifact_ref": "artifact://registry/not-a-skillset"
                        }
                    }
                },
                node_inputs={"skills": {"include": [{"name": "baseline"}]}},
            )
            ref = await wf._resolve_agent_node_skillset_ref(
                task_skills=None,
                node_inputs={"skills": {"include": [{"name": "baseline"}]}},
                node_id="step-1",
                existing_skillset_ref=existing,
            )

        self.assertIsNone(existing)
        self.assertEqual(ref, "artifact://skillsets/resolved-from-intent")
        execute_activity.assert_awaited_once()

    def test_existing_agent_skillset_ref_accepts_explicit_refs_only(self) -> None:
        ref = MoonMindRunWorkflow._existing_agent_skillset_ref(
            parameters={"resolvedSkillsetRef": "artifact://skillsets/from-task"},
            node={"resolvedSkillsetRef": "artifact://skillsets/from-node"},
            node_inputs={"resolved_skillset_ref": "artifact://skillsets/from-input"},
        )

        self.assertEqual(ref, "artifact://skillsets/from-input")

class TestJiraAgentPublishHelpers(unittest.TestCase):
    def test_jira_issue_creator_agent_plan_makes_pr_publish_optional(self) -> None:
        nodes = [
            {
                "tool": {"type": "agent_runtime", "name": "codex_cli"},
                "inputs": {"selectedSkill": "jira-issue-creator"},
            }
        ]

        self.assertTrue(MoonMindRunWorkflow._pr_publish_optional_for_plan(nodes))

    def test_jira_issue_creator_metadata_on_inputs_makes_pr_publish_optional(self) -> None:
        nodes = [
            {
                "tool": {"type": "agent_runtime", "name": "codex_cli"},
                "inputs": {
                    "metadata": {"moonmind": {"selectedSkill": "jira-issue-creator"}}
                },
            }
        ]

        self.assertTrue(MoonMindRunWorkflow._pr_publish_optional_for_plan(nodes))

    def test_jira_issue_updater_agent_plan_makes_pr_publish_optional(self) -> None:
        nodes = [
            {
                "tool": {"type": "agent_runtime", "name": "codex_cli"},
                "inputs": {"selectedSkill": "jira-issue-updater"},
            }
        ]

        self.assertTrue(MoonMindRunWorkflow._pr_publish_optional_for_plan(nodes))

    def test_jira_verify_agent_plan_makes_pr_publish_optional(self) -> None:
        nodes = [
            {
                "tool": {"type": "agent_runtime", "name": "codex_cli"},
                "inputs": {"selectedSkill": "jira-verify"},
            }
        ]

        self.assertTrue(MoonMindRunWorkflow._pr_publish_optional_for_plan(nodes))

    def test_jira_pr_verify_agent_plan_makes_pr_publish_optional(self) -> None:
        nodes = [
            {
                "tool": {"type": "agent_runtime", "name": "codex_cli"},
                "inputs": {"selectedSkill": "jira-pr-verify"},
            }
        ]

        self.assertTrue(MoonMindRunWorkflow._pr_publish_optional_for_plan(nodes))

    def test_runtime_metadata_skill_does_not_affect_plan_publish_classification(
        self,
    ) -> None:
        nodes = [
            {
                "tool": {"type": "agent_runtime", "name": "codex_cli"},
                "inputs": {
                    "runtime": {
                        "metadata": {
                            "moonmind": {"selectedSkill": "jira-issue-creator"}
                        }
                    }
                },
            }
        ]

        self.assertFalse(MoonMindRunWorkflow._pr_publish_optional_for_plan(nodes))

    def test_mixed_agent_plan_still_requires_requested_pr_publish(self) -> None:
        nodes = [
            {
                "tool": {"type": "agent_runtime", "name": "codex_cli"},
                "inputs": {"selectedSkill": "jira-issue-creator"},
            },
            {
                "tool": {"type": "agent_runtime", "name": "codex_cli"},
                "inputs": {"selectedSkill": "moonspec-breakdown"},
            },
        ]

        self.assertFalse(MoonMindRunWorkflow._pr_publish_optional_for_plan(nodes))

    def test_publishable_changes_are_detected_from_agent_outputs(self) -> None:
        wf = MoonMindRunWorkflow()

        self.assertTrue(
            wf._execution_result_has_publishable_changes(
                {
                    "outputs": {
                        "acceptedRepositoryEvidence": {
                            "pushStatus": "pushed",
                            "evidenceRef": "artifact://repository/published",
                        }
                    }
                }
            )
        )
        self.assertFalse(
            wf._execution_result_has_publishable_changes(
                {
                    "outputs": {
                        "acceptedRepositoryEvidence": {
                            "repositoryChanged": False,
                            "evidenceRef": "artifact://repository/no-change",
                        }
                    }
                }
            )
        )

class TestMapAgentRunResult(unittest.TestCase):
    """Verify the _map_agent_run_result helper."""

    def test_success_result(self) -> None:
        wf = MoonMindRunWorkflow()
        result = wf._map_agent_run_result({
            "summary": "Done",
            "output_refs": ["ref1", "ref2"],
            "failure_class": None,
        })
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["outputs"]["summary"], "Done")
        self.assertEqual(result["outputs"]["output_refs"], ["ref1", "ref2"])
        self.assertEqual(result["outputs"]["error"], "")

    def test_failure_result(self) -> None:
        wf = MoonMindRunWorkflow()
        result = wf._map_agent_run_result({
            "summary": "Timed out",
            "output_refs": [],
            "failure_class": "execution_error",
        })
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["outputs"]["error"], "execution_error")

    def test_failure_result_preserves_provider_failure_fields(self) -> None:
        wf = MoonMindRunWorkflow()
        result = wf._map_agent_run_result({
            "summary": "http 401",
            "output_refs": [],
            "failure_class": "user_error",
            "providerErrorCode": "401",
            "retryRecommendation": "reauthenticate",
        })
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["outputs"]["error"], "user_error")
        self.assertEqual(result["outputs"]["providerErrorCode"], "401")
        self.assertEqual(
            result["outputs"]["retryRecommendation"],
            "reauthenticate",
        )

    def test_result_uses_camel_case_diagnostics_ref_output(self) -> None:
        wf = MoonMindRunWorkflow()
        result = wf._map_agent_run_result({
            "summary": "http 401",
            "output_refs": [],
            "failure_class": "user_error",
            "diagnostics_ref": "artifact://diagnostics/1",
        })

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(
            result["outputs"]["diagnosticsRef"],
            "artifact://diagnostics/1",
        )
        self.assertNotIn("diagnostics_ref", result["outputs"])

    def test_handles_pydantic_model(self) -> None:
        from moonmind.schemas.agent_runtime_models import AgentRunResult

        model = AgentRunResult(summary="All done", output_refs=["a"])
        wf = MoonMindRunWorkflow()
        result = wf._map_agent_run_result(model)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["outputs"]["summary"], "All done")

    def test_handles_pydantic_model_with_failure(self) -> None:
        from moonmind.schemas.agent_runtime_models import AgentRunResult

        model = AgentRunResult(failure_class="execution_error")
        wf = MoonMindRunWorkflow()
        result = wf._map_agent_run_result(model)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["outputs"]["error"], "execution_error")

    def test_preserves_merge_automation_disposition_metadata(self) -> None:
        from moonmind.schemas.agent_runtime_models import AgentRunResult

        model = AgentRunResult(
            summary="PR merged",
            metadata={
                "mergeAutomationDisposition": "merged",
                "headSha": "abc123",
            },
        )
        wf = MoonMindRunWorkflow()
        result = wf._map_agent_run_result(model)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["outputs"]["mergeAutomationDisposition"], "merged")
        self.assertEqual(result["outputs"]["headSha"], "abc123")

class TestBuildAgentExecutionRequest(unittest.TestCase):
    """Verify the _build_agent_execution_request helper."""

    def test_build_agent_execution_request_propagates_steps(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        # We must mock workflow.info() because it's called inside _build_agent_execution_request
        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch("moonmind.workflows.temporal.workflows.run.workflow.info", return_value=MockInfo()):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "steps": [
                        {"id": "step-1", "instructions": "Do part A"},
                        {"id": "step-2", "instructions": "Do part B"}
                    ],
                    "targetRuntime": "codex",
                    "model": "gpt-4o",
                },
                node_id="node-xyz",
                tool_name="test_tool"
            )

        self.assertEqual(request.agent_id, "codex")
        self.assertEqual(request.parameters.get("model"), "gpt-4o")
        self.assertEqual(request.parameters.get("steps"), [
            {"id": "step-1", "instructions": "Do part A"},
            {"id": "step-2", "instructions": "Do part B"}
        ])

    def test_compiled_omnigent_intent_requirement_is_replay_safe(self) -> None:
        wf = MoonMindRunWorkflow()
        info = SimpleNamespace(
            namespace="default",
            workflow_id="test-wf-id",
            run_id="test-run-id",
            parent=None,
        )
        node_inputs = {
            "runtime": {
                "mode": "omnigent",
                "executionProfileRef": "provider-profile-1",
            },
            "instructions": "Implement the step.",
        }

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=info,
        ), patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: (
                patch_id == RUN_COMPILED_OMNIGENT_EXECUTION_INTENT_PATCH
            ),
        ):
            current = wf._build_agent_execution_request(
                node_inputs=node_inputs,
                node_id="omnigent-step",
                tool_name="omnigent",
            )

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=info,
        ), patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            return_value=False,
        ):
            replay = wf._build_agent_execution_request(
                node_inputs=node_inputs,
                node_id="omnigent-step",
                tool_name="omnigent",
            )

        self.assertEqual(current.execution_intent_requirement, "required")
        self.assertEqual(replay.execution_intent_requirement, "legacy_history")

    def test_validated_remediation_controller_becomes_typed_runtime_intent(
        self,
    ) -> None:
        from moonmind.workflows.temporal.remediation_loop import RemediationLoopSpec

        wf = MoonMindRunWorkflow()
        wf._remediation_loop_spec = RemediationLoopSpec.model_validate(
            {
                "loopId": "loop-typed",
                "remediationTool": {
                    "name": "remediate-issue",
                    "inputs": {"instructions": "Fix remaining gaps."},
                },
                "verificationTool": {
                    "name": "github-issue-verify",
                    "inputs": {"instructions": "Verify the issue."},
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
        info = SimpleNamespace(
            namespace="default",
            workflow_id="test-wf-id",
            run_id="test-run-id",
            parent=None,
        )
        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=info,
        ), patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: (
                patch_id == RUN_COMPILED_OMNIGENT_EXECUTION_INTENT_PATCH
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "omnigent",
                        "executionProfileRef": "provider-profile-1",
                    },
                    "instructions": "Remediate the issue.",
                },
                node_id="remediation-step",
                tool_name="omnigent",
            )

        assert request.remediation_loop is not None
        self.assertEqual(request.remediation_loop.loop_id, "loop-typed")
        self.assertEqual(
            request.remediation_loop.verifier_owner,
            "github-issue-verify",
        )
        self.assertEqual(request.remediation_loop.remediator_owner, "remediate-issue")
        self.assertEqual(request.remediation_loop.max_attempts, 3)
        self.assertNotIn("remediationLoop", request.parameters.get("annotations", {}))

    def test_build_agent_execution_request_propagates_runtime_command_metadata(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        runtime_command = {
            "kind": "slash_command",
            "source": "leading_slash",
            "sourcePath": "objective.instructions",
            "command": "review",
            "rawCommand": "/review",
            "args": "",
            "instructionBody": "Check this branch.",
            "targetRuntime": "codex_cli",
            "detectionStatus": "detected",
            "hintStatus": "hinted",
            "recognitionMode": "hinted_runtime_passthrough",
            "requiresRuntimeRecognition": True,
            "hintCatalogVersion": "2026-05-13",
            "detectionPhase": "submit",
        }

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "instructions": "/review\nCheck this branch.",
                    "runtime": {"mode": "codex_cli"},
                    "runtimeCommand": runtime_command,
                },
                node_id="node-runtime-command",
                tool_name="codex_cli",
            )

        self.assertEqual(request.instruction_ref, "/review\nCheck this branch.")
        self.assertIsNotNone(request.runtime_command)
        self.assertEqual(request.runtime_command.command, "review")
        self.assertEqual(request.runtime_command.raw_command, "/review")

    def test_build_agent_execution_request_strips_removed_runtime_command_marker(
        self,
    ) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        marker_terms = ("runtime", "capability", "version")
        removed_marker = (
            marker_terms[0]
            + marker_terms[1][:1].upper()
            + marker_terms[1][1:]
            + marker_terms[2][:1].upper()
            + marker_terms[2][1:]
        )
        runtime_command = {
            "kind": "slash_command",
            "source": "leading_slash",
            "sourcePath": "objective.instructions",
            "command": "review",
            "rawCommand": "/review",
            "args": "",
            "instructionBody": "Check this branch.",
            "targetRuntime": "codex_cli",
            "detectionStatus": "detected",
            "hintStatus": "hinted",
            "recognitionMode": "hinted_runtime_passthrough",
            "requiresRuntimeRecognition": True,
            removed_marker: "2026-05-13",
            "hintCatalogVersion": "2026-05-13",
            "detectionPhase": "submit",
        }

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "instructions": "/review\nCheck this branch.",
                    "runtime": {"mode": "codex_cli"},
                    "runtimeCommand": runtime_command,
                },
                node_id="node-runtime-command",
                tool_name="codex_cli",
            )

        self.assertIsNotNone(request.runtime_command)
        self.assertEqual(request.runtime_command.command, "review")
        self.assertEqual(request.runtime_command.hint_catalog_version, "2026-05-13")

    def test_build_agent_execution_request_includes_step_execution_launch_policy(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "instructions": "Implement this step.",
                    "runtime": {"mode": "codex_cli"},
                    "resolvedSkillsetRef": "artifact://skillsets/resolved",
                    "selectedSkill": "jira-implement",
                },
                node_id="node-runtime-attempt",
                tool_name="codex_cli",
                attempt_reason="runtime_recovered",
            )

        self.assertIsNotNone(request.step_execution)
        step_execution = request.step_execution
        assert step_execution is not None
        self.assertEqual(step_execution.workflow_id, "test-wf-id")
        self.assertEqual(step_execution.run_id, "test-run-id")
        self.assertEqual(step_execution.logical_step_id, "node-runtime-attempt")
        self.assertEqual(step_execution.execution_ordinal, 1)
        self.assertEqual(step_execution.reason, "runtime_recovered")
        self.assertEqual(
            step_execution.step_execution_id,
            "test-wf-id:test-run-id:node-runtime-attempt:execution:1",
        )
        self.assertEqual(step_execution.runtime_context_policy, "fresh_agent_run")
        self.assertEqual(
            step_execution.context_bundle_ref,
            request.parameters["metadata"]["moonmind"]["executionContext"][
                "contextBundleRef"
            ],
        )
        self.assertEqual(
            step_execution.skill_source_policy["repoSkills"],
            "resolver_policy_enforced",
        )
        self.assertEqual(
            step_execution.skill_source_policy["checkedInSkillMutation"],
            "prohibited",
        )

    def test_build_agent_execution_request_compiles_trusted_remediation_authority(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        authority = {
            "loopId": "loop-1", "branchRef": "checkpoint-branch:loop-1",
            "attemptOrdinal": 3, "baseCheckpointRef": "artifact://workspace/C2",
            "baseWorkspaceDigest": "sha256:" + "a" * 64,
            "expectedHeadVersion": 3,
            "headAuthorityRef": "artifact://loop-head/3",
            "executionProfileRef": "codex-profile",
            "hostProfileRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@1",
            "workspaceCapabilitySnapshot": {"locatorKind": "sandbox", "restore": True},
        }
        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "omnigent", "executionProfileRef": "codex-profile",
                    }
                },
                node_id="remediate", tool_name="omnigent", step_execution=3,
                trusted_remediation_authority=authority,
            )
        binding = request.remediation_workspace
        assert binding is not None
        self.assertNotIn("currentHeadCheckpointRef", binding)
        self.assertEqual(binding["workflowId"], "test-wf-id")
        self.assertEqual(binding["runId"], "test-run-id")
        self.assertEqual(binding["logicalStepId"], "remediate")
        self.assertEqual(binding["stepExecutionId"], request.step_execution.step_execution_id)
        self.assertEqual(
            binding["destinationWorkspaceLocator"], request.workspace_spec["workspaceLocator"]
        )

    def test_build_agent_execution_request_rejects_untrusted_remediation_block(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ), self.assertRaisesRegex(ValueError, "trusted runtime controller"):
            wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {"mode": "omnigent"},
                    "remediationWorkspace": {"loopId": "caller-authored"},
                },
                node_id="remediate", tool_name="omnigent",
            )

    def test_build_agent_execution_request_rejects_authored_runtime_authority(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ), self.assertRaisesRegex(ValueError, "workflow-owned controller state"):
            wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "omnigent",
                        "metadata": {
                            "moonmind": {
                                "remediationWorkspaceAuthority": {"loopId": "forged"}
                            }
                        },
                    }
                },
                node_id="remediate",
                tool_name="omnigent",
            )

    def test_build_agent_execution_request_rejects_malformed_controller_authority(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ), self.assertRaisesRegex(ValueError, "authority is malformed"):
            wf._build_agent_execution_request(
                node_inputs={"runtime": {"mode": "omnigent"}},
                node_id="remediate",
                tool_name="omnigent",
                trusted_remediation_authority="corrupt",
            )

    def test_build_agent_execution_request_launches_checkpoint_branch_turn_context(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            namespace = "default"
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

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

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ), patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: (
                patch_id
                in {
                    RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH,
                    RUN_OMNIGENT_CHECKPOINT_BRANCH_TURN_REQUEST_PATCH,
                }
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "codex_cli",
                        "metadata": {
                            "moonmind": {"checkpointBranchTurn": branch_turn}
                        },
                    },
                },
                node_id="branch-implement",
                tool_name="codex_cli",
                attempt_reason="runtime_recovered",
            )

        assert request.step_execution is not None
        self.assertEqual(request.step_execution.reason, "checkpoint_branch")
        self.assertEqual(
            request.step_execution.runtime_context_policy,
            "fresh_agent_run",
        )
        self.assertEqual(
            request.step_execution.branch,
            {
                "branchId": "branch-1",
                "branchTurnId": "turn-1",
                "rootCheckpointRef": "artifact://checkpoint/source",
                "gitWorkBranch": "mm/branch-1",
            },
        )
        assert request.step_execution.branch_artifact_manifest is not None
        self.assertEqual(request.instruction_ref, "artifact://instructions/turn-1")
        moonmind_metadata = request.parameters["metadata"]["moonmind"]
        execution_context = moonmind_metadata["executionContext"]
        self.assertEqual(execution_context["reason"], "checkpoint_branch")
        self.assertEqual(
            execution_context["builderVersion"],
            "branch-turn-context-builder-v1",
        )
        self.assertEqual(execution_context["branch"]["branchId"], "branch-1")
        self.assertEqual(execution_context["branch"]["branchTurnId"], "turn-1")
        self.assertEqual(
            execution_context["instructionRefs"],
            ["artifact://instructions/turn-1"],
        )
        self.assertEqual(
            moonmind_metadata["checkpointBranchTurn"]["artifactManifestDigest"],
            moonmind_metadata["checkpointBranchTurnArtifactManifest"][
                "artifactManifestDigest"
            ],
        )
        self.assertEqual(
            request.step_execution.branch_artifact_manifest["artifactManifestDigest"],
            moonmind_metadata["checkpointBranchTurnArtifactManifest"][
                "artifactManifestDigest"
            ],
        )
        artifact_names = {
            artifact["name"]
            for artifact in moonmind_metadata["checkpointBranchTurnArtifactManifest"][
                "artifacts"
            ]
        }
        self.assertIn(
            "output.branch_turn.step_execution_manifest.json",
            artifact_names,
        )
        self.assertEqual(
            moonmind_metadata["stepExecutionManifestProjection"]["context"][
                "branch"
            ]["branchId"],
            "branch-1",
        )
        self.assertEqual(
            wf._step_execution_branch_projections[("branch-implement", 1)][
                "branchTurnId"
            ],
            "turn-1",
        )

    def test_checkpoint_branch_turn_source_preserves_before_execution_boundary(self) -> None:
        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        source = wf._checkpoint_branch_turn_source_checkpoint(
            {
                "sourceWorkflowId": "source-wf",
                "sourceRunId": "source-run",
                "sourceLogicalStepId": "source-step",
            },
            context_workspace={
                "checkpointBeforeRef": "artifact://checkpoint/before",
                "checkpointBeforeDigest": "sha256:" + "a" * 64,
            },
            node_id="repair",
            wf_info=MockInfo(),
        )

        self.assertEqual(source["checkpointRef"], "artifact://checkpoint/before")
        self.assertEqual(source["checkpointBoundary"], "before_execution")

    def test_build_agent_execution_request_routes_omnigent_branch_instruction_ref(
        self,
    ) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            namespace = "default"
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        branch_turn = {
            "branchId": "branch-omnigent",
            "branchTurnId": "turn-omnigent",
            "sourceWorkflowId": "source-wf",
            "sourceRunId": "source-run",
            "sourceLogicalStepId": "source-step",
            "sourceCheckpointRef": "artifact://checkpoint/source",
            "sourceCheckpointDigest": "sha256:" + "a" * 64,
            "instructionArtifactRef": "artifact://instructions/turn-omnigent",
            "instructionDigest": "sha256:" + "b" * 64,
            "workspacePolicy": "fresh_branch_from_source",
            "runtimeContextPolicy": "fresh_agent_run",
            "gitWorkBranch": "mm/branch-omnigent",
        }

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ), patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: (
                patch_id
                in {
                    RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH,
                    RUN_OMNIGENT_CHECKPOINT_BRANCH_TURN_REQUEST_PATCH,
                }
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "omnigent",
                        "omnigent": {
                            "endpointRef": "default",
                            "prompt": {"text": "stale inline prompt"},
                            "capture": {"workspaceFiles": True},
                        },
                        "metadata": {
                            "moonmind": {"checkpointBranchTurn": branch_turn}
                        },
                    },
                },
                node_id="branch-omnigent",
                tool_name="omnigent",
                attempt_reason="runtime_recovered",
            )

        self.assertEqual(request.agent_kind, "external")
        self.assertEqual(request.agent_id, "omnigent")
        self.assertIsNone(request.instruction_ref)
        self.assertEqual(
            request.idempotency_key,
            "test-wf-id:branch-omnigent:turn-omnigent:omnigent",
        )
        self.assertEqual(
            request.parameters["omnigent"]["prompt"],
            {"instructionRef": "artifact://instructions/turn-omnigent"},
        )
        self.assertEqual(
            request.parameters["omnigent"]["capture"],
            {"workspaceFiles": True},
        )
        self.assertEqual(request.workspace_spec["branch"], "mm/branch-omnigent")
        self.assertEqual(
            request.workspace_spec["startingBranch"],
            "mm/branch-omnigent",
        )
        self.assertEqual(
            request.step_execution.runtime_context_policy,
            "fresh_agent_run",
        )
        moonmind_metadata = request.parameters["metadata"]["moonmind"]
        self.assertEqual(
            moonmind_metadata["executionContext"]["priorEvidenceRefs"],
            [],
        )
        self.assertEqual(
            moonmind_metadata["checkpointBranchTurn"]["branchId"],
            "branch-omnigent",
        )

    def test_build_agent_execution_request_preserves_omnigent_request_shape_unpatched(
        self,
    ) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            namespace = "default"
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        branch_turn = {
            "branchId": "branch-omnigent",
            "branchTurnId": "turn-omnigent",
            "sourceWorkflowId": "source-wf",
            "sourceRunId": "source-run",
            "sourceLogicalStepId": "source-step",
            "sourceCheckpointRef": "artifact://checkpoint/source",
            "sourceCheckpointDigest": "sha256:" + "a" * 64,
            "instructionArtifactRef": "artifact://instructions/turn-omnigent",
            "instructionDigest": "sha256:" + "b" * 64,
            "workspacePolicy": "fresh_branch_from_source",
            "runtimeContextPolicy": "fresh_agent_run",
            "gitWorkBranch": "mm/branch-omnigent",
        }

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ), patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: (
                patch_id == RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "omnigent",
                        "workspaceSpec": {"repository": "https://example/repo.git"},
                        "omnigent": {
                            "endpointRef": "default",
                            "prompt": {"text": "legacy prompt"},
                        },
                        "metadata": {
                            "moonmind": {"checkpointBranchTurn": branch_turn}
                        },
                    },
                },
                node_id="branch-omnigent",
                tool_name="omnigent",
                attempt_reason="runtime_recovered",
            )

        self.assertEqual(
            request.instruction_ref,
            "artifact://instructions/turn-omnigent",
        )
        self.assertEqual(
            request.idempotency_key,
            "test-wf-id:branch-omnigent:test-run-id",
        )
        self.assertEqual(
            request.parameters["omnigent"]["prompt"],
            {"text": "legacy prompt"},
        )
        self.assertNotIn("branch", request.workspace_spec)
        self.assertNotIn("startingBranch", request.workspace_spec)

    def test_build_agent_execution_request_rejects_non_object_omnigent_block(
        self,
    ) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            namespace = "default"
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ), pytest.raises(
            ValueError,
            match=r"node\[bad-omnigent\]\.omnigent must be an object",
        ):
            wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "omnigent",
                        "omnigent": "not-an-object",
                    },
                },
                node_id="bad-omnigent",
                tool_name="omnigent",
                attempt_reason="runtime_recovered",
            )

    def test_github_3453_authored_omnigent_runtime_compiles_profile_bound_request(
        self,
    ) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()
        wf._profile_snapshots = {
            "codex-oauth-profile": {
                "profile_id": "codex-oauth-profile",
                "runtime_id": "codex_cli",
            }
        }

        class MockInfo:
            namespace = "default"
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        authored_selection = {
            "executionTargetRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@1",
            "agent": {"harnessOverride": "codex-native"},
            "capture": {"required": True, "retentionDays": 30},
        }
        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ), patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: patch_id
            in {
                RUN_OMNIGENT_AUTHORED_SELECTION_COMPILER_PATCH,
                RUN_OMNIGENT_STOCK_AGENT_IDENTITY_PATCH,
            },
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "omnigent",
                        "executionProfileRef": "codex-oauth-profile",
                    },
                },
                workflow_parameters={"omnigent": authored_selection},
                node_id="omnigent-implement",
                tool_name="auto",
                resolved_skillset_ref="artifact://skills/resolved-1",
            )

        self.assertEqual(request.agent_kind, "external")
        self.assertEqual(request.agent_id, "omnigent")
        self.assertEqual(request.execution_profile_ref, "codex-oauth-profile")
        self.assertEqual(
            request.resolved_skillset_ref,
            "artifact://skills/resolved-1",
        )
        expected_selection = dict(authored_selection)
        expected_selection["agent"] = {
            "harnessOverride": "codex-native",
            "agentName": "codex-native-ui",
        }
        self.assertEqual(request.parameters["omnigent"], expected_selection)
        self.assertNotIn("hostId", request.parameters["omnigent"])
        self.assertNotIn("credentialGeneration", request.parameters["omnigent"])
        self.assertNotIn("providerLeaseId", request.parameters["omnigent"])
        self.assertNotIn(
            "_moonmindProfileAuthorization",
            request.parameters["omnigent"],
        )

    def test_agent_profile_snapshot_compiles_legacy_rerun_selection(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._profile_snapshots = {
            "codex-openai-oauth": {
                "profile_id": "codex-openai-oauth",
                "runtime_id": "codex_cli",
            }
        }

        class MockInfo:
            namespace = "default"
            workflow_id = "mm:agent-profile-rerun"
            run_id = "rerun-1"

        snapshot = {
            "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
            "profileId": "omnigent-bootstrap-default",
            "version": 1,
            "digest": "sha256:" + "a" * 64,
            "providerProfileRef": "codex-openai-oauth",
            "executionProfileRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@1",
            "agentId": "upstream-codex-agent",
            "document": {
                "endpointRef": "default",
                "harness": "codex-native",
            },
        }
        legacy_selection = {
            "executionTargetRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@1",
            "agentProfileRef": "omnigent-bootstrap-default@1",
            "executionProfileRef": "omnigent-codex@1",
            "agent": {"agentId": "upstream-codex-agent"},
        }

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ), patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: patch_id
            in {
                RUN_OMNIGENT_AUTHORED_SELECTION_COMPILER_PATCH,
                RUN_OMNIGENT_AGENT_PROFILE_SNAPSHOT_COMPILER_PATCH,
            },
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "omnigent",
                        "executionProfileRef": "codex-openai-oauth",
                    },
                },
                workflow_parameters={
                    "omnigent": legacy_selection,
                    "agentProfileSnapshot": snapshot,
                },
                node_id="node-1",
                tool_name="omnigent",
            )

        self.assertEqual(
            request.parameters["omnigent"],
            {
                "endpointRef": "default",
                "executionTargetRef": "omnigent-codex@1",
                "launchPolicyRef": "codex-on-demand@1",
                "agent": {
                    "harnessOverride": "codex-native",
                    "agentId": "upstream-codex-agent",
                    "agentName": "codex",
                },
            },
        )
        self.assertEqual(request.execution_profile_ref, "codex-openai-oauth")

    def test_agent_profile_snapshot_rejects_conflicting_legacy_authority(self) -> None:
        wf = MoonMindRunWorkflow()

        class MockInfo:
            namespace = "default"
            workflow_id = "mm:agent-profile-rerun"
            run_id = "rerun-1"

        snapshot = {
            "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
            "profileId": "omnigent-bootstrap-default",
            "version": 1,
            "digest": "sha256:" + "a" * 64,
            "providerProfileRef": "codex-openai-oauth",
            "executionProfileRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@1",
            "agentId": "upstream-codex-agent",
            "document": {
                "endpointRef": "default",
                "harness": "codex-native",
            },
        }

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ), patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            return_value=True,
        ), pytest.raises(
            ValueError,
            match="agentProfileRef conflicts with agentProfileSnapshot",
        ):
            wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "omnigent",
                        "executionProfileRef": "codex-openai-oauth",
                    },
                },
                workflow_parameters={
                    "omnigent": {
                        "agentProfileRef": "forged-profile@9",
                    },
                    "agentProfileSnapshot": snapshot,
                },
                node_id="node-1",
                tool_name="omnigent",
            )

    def test_github_3453_compiler_rejects_authored_authority_without_fallback(
        self,
    ) -> None:
        authority_cases = [
            ("hostId", "host-authored"),
            ("dockerVolume", "volume-authored"),
            ("credentialGeneration", 7),
            ("providerLeaseId", "lease-authored"),
            ("absoluteBindSource", "/host/private"),
            ("registrationToken", "token-authored"),
            ("_moonmindProfileAuthorization", {"profile": "forged"}),
        ]
        wf = MoonMindRunWorkflow()

        class MockInfo:
            namespace = "default"
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        for authority_key, authority_value in authority_cases:
            with self.subTest(authority_key=authority_key), patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=MockInfo(),
            ), patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: (
                    patch_id == RUN_OMNIGENT_AUTHORED_SELECTION_COMPILER_PATCH
                ),
            ), pytest.raises(ValueError, match="trusted authority"):
                wf._build_agent_execution_request(
                    node_inputs={
                        "runtime": {
                            "mode": "omnigent",
                            "executionProfileRef": "codex-oauth-profile",
                        },
                    },
                    workflow_parameters={
                        "omnigent": {
                            "agent": {"harnessOverride": "codex-native"},
                            "productIntent": {authority_key: authority_value},
                        }
                    },
                    node_id="omnigent-reject-authority",
                    tool_name="auto",
                )

    def test_checkpoint_branch_turn_requires_source_identity_for_explicit_checkpoint_ref(
        self,
    ) -> None:
        wf = MoonMindRunWorkflow()

        class MockInfo:
            namespace = "default"
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        branch_turn = {
            "branchId": "branch-1",
            "branchTurnId": "turn-1",
            "sourceCheckpointRef": "artifact://checkpoint/source",
            "sourceCheckpointDigest": "sha256:" + "a" * 64,
            "instructionArtifactRef": "artifact://instructions/turn-1",
            "instructionDigest": "sha256:" + "b" * 64,
            "workspacePolicy": "fresh_branch_from_source",
            "runtimeContextPolicy": "fresh_agent_run",
        }

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ), patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: (
                patch_id == RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH
            ),
        ), pytest.raises(ValueError, match="source_checkpoint.workflowId"):
            wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "codex_cli",
                        "metadata": {
                            "moonmind": {"checkpointBranchTurn": branch_turn}
                        },
                    },
                },
                node_id="branch-implement",
                tool_name="codex_cli",
                attempt_reason="runtime_recovered",
            )

    def test_managed_reattempt_records_session_reset_launch_evidence(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()
        now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
        wf._initialize_step_ledger(
            ordered_nodes=[
                {
                    "id": "implement",
                    "tool": {"type": "agent_runtime", "name": "codex_cli"},
                    "inputs": {"title": "Implement"},
                }
            ],
            dependency_map={"implement": []},
            updated_at=now,
        )
        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            return_value=False,
        ):
            wf._mark_step_running("implement", updated_at=now, summary="Attempt 1")
            wf._record_step_result_evidence(
                "implement",
                execution_result={
                    "status": "FAILED",
                    "outputs": {
                        "latestCheckpointRef": "artifact://checkpoint/attempt-1"
                    },
                },
                updated_at=now,
            )
            wf._mark_step_terminal(
                "implement",
                status="failed",
                updated_at=now,
                summary="Attempt 1 failed",
            )
            wf._mark_step_running("implement", updated_at=now, summary="Attempt 2")

        class MockInfo:
            namespace = "default"
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ), patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            return_value=False,
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "instructions": "Retry in a clean context.",
                    "runtime": {"mode": "codex_cli"},
                },
                node_id="implement",
                tool_name="codex_cli",
                attempt_reason="runtime_recovered",
            )

        step_execution = request.step_execution
        assert step_execution is not None
        self.assertEqual(step_execution.runtime_context_policy, "fresh_agent_run")
        self.assertEqual(step_execution.execution_ordinal, 2)
        self.assertEqual(
            step_execution.runtime_session_reset,
            {
                "requestedPolicy": "reuse_session_new_epoch",
                "resolvedPolicy": "fresh_agent_run",
                "semantics": "new_epoch_cleared_context",
                "clearContext": True,
                "newEpoch": True,
                "runtimeId": "codex_cli",
                "sourceExecutionOrdinal": {
                    "workflowId": "test-wf-id",
                    "runId": "test-run-id",
                    "logicalStepId": "implement",
                    "executionOrdinal": 1,
                },
                "availableCheckpointEvidence": {
                    "stateCheckpointRef": "artifact://checkpoint/attempt-1"
                },
            },
        )

    def test_external_request_records_continuation_evidence(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()
        wf._prepared_artifact_refs = ["prepared-context://steps/delegate/input"]
        wf._step_side_effect_records["delegate"] = [
            {
                "effectClass": "external_provider",
                "operation": "provider_run_started",
                "target": "jules",
                "disposition": "occurred",
            }
        ]

        class MockInfo:
            namespace = "default"
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "instructions": "Continue delegated work.",
                    "runtime": {"mode": "jules"},
                },
                node_id="delegate",
                tool_name="jules",
            )

        step_execution = request.step_execution
        assert step_execution is not None
        continuation = step_execution.external_provider_continuation
        assert continuation is not None
        self.assertEqual(
            continuation["attemptIdentity"],
            {
                "workflowId": "test-wf-id",
                "runId": "test-run-id",
                "logicalStepId": "delegate",
                "executionOrdinal": 1,
                "stepExecutionId": "test-wf-id:test-run-id:delegate:execution:1",
            },
        )
        self.assertEqual(
            continuation["contextRefs"]["contextBundleRef"],
            step_execution.context_bundle_ref,
        )
        self.assertEqual(
            continuation["knownSideEffects"]["records"][0]["operation"],
            "provider_run_started",
        )
        self.assertEqual(continuation["checkpointEvidence"], {"available": False})

    def test_build_agent_execution_request_propagates_story_output_handoff(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        story_output = {
            "mode": "jira",
            "handoff": "artifact",
            "requiresStoryBreakdownArtifactRef": True,
            "storyBreakdownPath": "artifacts/story-breakdowns/demo/stories.json",
            "storyBreakdownMarkdownPath": (
                "artifacts/story-breakdowns/demo/stories.md"
            ),
        }

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {"mode": "codex_cli"},
                    "storyOutput": story_output,
                    "storyBreakdownPath": (
                        "artifacts/story-breakdowns/demo/stories.json"
                    ),
                    "storyBreakdownMarkdownPath": (
                        "artifacts/story-breakdowns/demo/stories.md"
                    ),
                },
                node_id="node-story-output",
                tool_name="moonspec-breakdown",
            )

        self.assertEqual(request.parameters["storyOutput"], story_output)
        self.assertEqual(
            request.parameters["storyBreakdownPath"],
            "artifacts/story-breakdowns/demo/stories.json",
        )
        self.assertEqual(
            request.parameters["storyBreakdownMarkdownPath"],
            "artifacts/story-breakdowns/demo/stories.md",
        )

    def test_build_agent_execution_request_marks_hyphenated_codex_runtime_managed(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {"mode": "codex-cli"},
                },
                node_id="node-codex",
                tool_name="pr-resolver",
            )

        self.assertEqual(request.agent_id, "codex-cli")
        self.assertEqual(request.agent_kind, "managed")

    def test_build_agent_execution_request_infers_minimax_provider_for_claude(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {"mode": "claude", "model": "MiniMax-M2.7"},
                },
                node_id="node-claude",
                tool_name="auto",
            )

        self.assertEqual(request.agent_id, "claude")
        self.assertEqual(request.parameters.get("model"), "MiniMax-M2.7")
        self.assertEqual(request.profile_selector.provider_id, "minimax")

    def test_build_agent_execution_request_propagates_resolved_skillset_ref(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "codex",
                },
                node_id="node-skills",
                tool_name="auto",
                resolved_skillset_ref="skills:snap:12345",
            )

        self.assertEqual(request.agent_id, "codex")
        self.assertEqual(request.resolved_skillset_ref, "skills:snap:12345")

    def test_build_agent_execution_request_propagates_publish_base_branch(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "codex",
                    "repository": "MoonLadderStudios/MoonMind",
                    "startingBranch": "codex/fix-pr-publish-base-branch",
                    "targetBranch": "codex/fix-pr-publish-base-branch",
                    "publishMode": "pr",
                    "publishBaseBranch": "main",
                },
                node_id="node-publish",
                tool_name="auto",
            )

        self.assertEqual(request.parameters["publishBaseBranch"], "main")
        self.assertEqual(request.workspace_spec["publishBaseBranch"], "main")

    def test_build_agent_execution_request_passes_compact_skill_payload(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "codex",
                    "selectedSkill": "issue-implement",
                    "skill": {
                        "name": "issue-implement",
                        "contentRef": "art_skill",
                        "contentDigest": "sha256:skill",
                        "inputContractDigest": "sha256:contract",
                        "requiredCapabilities": ["repo.lock"],
                        "inputs": {"issue": "MM-1052"},
                    },
                },
                node_id="node-skills",
                tool_name="auto",
                resolved_skillset_ref="skills:snap:12345",
            )

        self.assertEqual(
            request.skill,
            {
                "name": "issue-implement",
                "contentRef": "art_skill",
                "contentDigest": "sha256:skill",
                "inputContractDigest": "sha256:contract",
                "requiredCapabilities": ["repo.lock"],
                "inputs": {"issue": "MM-1052"},
            },
        )
        self.assertEqual(request.parameters["skill"], request.skill)
        self.assertEqual(
            request.parameters["metadata"]["moonmind"]["selectedSkill"],
            "issue-implement",
        )

    def test_build_agent_execution_request_uses_provider_profile_as_execution_profile_ref(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "codex",
                        "providerProfile": "codex-provider-profile",
                    },
                },
                node_id="node-profile",
                tool_name="pr-resolver",
            )

        self.assertEqual(request.agent_id, "codex")
        self.assertEqual(request.execution_profile_ref, "codex-provider-profile")

    def test_build_agent_execution_request_leaves_profile_unset_when_not_explicit(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "codex",
                    },
                },
                node_id="node-default-profile",
                tool_name="speckit-orchestrate",
            )

        self.assertEqual(request.agent_id, "codex")
        self.assertIsNone(request.execution_profile_ref)

    def test_build_agent_execution_request_inherits_parent_runtime_profile(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()
        wf._profile_snapshots = {
            "codex_default": {
                "profile_id": "codex_default",
                "runtime_id": "codex_cli",
            },
        }

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "codex",
                    },
                },
                node_id="node-inherit-profile",
                tool_name="pr-resolver",
                workflow_parameters={
                    "task": {
                        "runtime": {
                            "mode": "codex",
                            "executionProfileRef": "codex_default",
                        },
                    },
                },
            )

        self.assertEqual(request.agent_id, "codex")
        self.assertEqual(request.execution_profile_ref, "codex_default")

    def test_build_agent_execution_request_rejects_cross_runtime_profile(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()
        wf._profile_snapshots = {
            "codex_default": {
                "profile_id": "codex_default",
                "runtime_id": "codex_cli",
            },
        }

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "targets runtime 'codex_cli' but child runtime is 'claude_code'",
            ):
                wf._build_agent_execution_request(
                    node_inputs={
                        "runtime": {
                            "mode": "claude",
                        },
                    },
                    node_id="node-cross-runtime-profile",
                    tool_name="pr-resolver",
                    workflow_parameters={
                        "task": {
                            "runtime": {
                                "mode": "codex",
                                "executionProfileRef": "codex_default",
                            },
                        },
                    },
                )

    def test_build_agent_execution_request_rejects_unknown_inherited_profile(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()
        wf._profile_snapshots = {
            "codex_default": {
                "profile_id": "codex_default",
                "runtime_id": "codex_cli",
            },
        }

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            with self.assertRaisesRegex(ValueError, "not a known profile"):
                wf._build_agent_execution_request(
                    node_inputs={
                        "runtime": {
                            "mode": "codex",
                        },
                    },
                    node_id="node-unknown-inherited-profile",
                    tool_name="pr-resolver",
                    workflow_parameters={
                        "task": {
                            "runtime": {
                                "mode": "codex",
                                "executionProfileRef": "hallucinated-profile",
                            },
                        },
                    },
                )

    def test_build_agent_execution_request_prefers_runtime_block_profile_over_top_level(self) -> None:
        """Runtime planner sets profile fields inside the runtime block; these
        should take precedence over top-level node_inputs keys which may have
        been corrupted by AI-generated plan modifications."""
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "profileId": "stale-top-level-profile",
                    "executionProfileRef": "stale-top-level-ref",
                    "runtime": {
                        "mode": "codex",
                        "profileId": "runtime-planner-profile",
                    },
                },
                node_id="node-profile-priority",
                tool_name="pr-resolver",
            )

        self.assertEqual(request.agent_id, "codex")
        self.assertEqual(request.execution_profile_ref, "runtime-planner-profile")

    def test_build_agent_execution_request_inherits_workflow_priority(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "codex",
                    },
                },
                node_id="node-priority",
                tool_name="pr-resolver",
                workflow_parameters={"priority": 10},
            )

        self.assertEqual(request.parameters["priority"], 10)

    def test_build_agent_execution_request_preserves_explicit_zero_priority(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "codex",
                        "priority": 0,
                    },
                },
                node_id="node-priority-zero",
                tool_name="pr-resolver",
                workflow_parameters={"priority": 10},
            )

        self.assertEqual(request.parameters["priority"], 0)

    def test_build_agent_execution_request_records_queue_metadata(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"
            start_time = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "codex",
                    },
                },
                node_id="node-queue-order",
                tool_name="pr-resolver",
                queue_order=7,
            )

        moonmind = request.parameters["metadata"]["moonmind"]
        self.assertEqual(moonmind["queueOrder"], 7)
        self.assertEqual(moonmind["queuedAt"], "2026-06-22T10:00:00+00:00")

    def test_build_agent_execution_request_rejects_invalid_profile_ref(self) -> None:
        """When a plan node carries a profile ID that doesn't match any known
        profile for the runtime (e.g. AI-hallucinated 'default:codex_cli'),
        the dispatcher should reject it instead of falling back to auto-selection."""
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()
        # Simulate profile snapshots synced from the provider profile manager
        wf._profile_snapshots = {"codex_openrouter_qwen36_plus", "codex_default"}

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            with self.assertRaisesRegex(ValueError, "not a known profile"):
                wf._build_agent_execution_request(
                    node_inputs={
                        "runtime": {
                            "mode": "codex",
                            "profileId": "default:codex_cli",
                        },
                    },
                    node_id="node-invalid-profile",
                    tool_name="pr-resolver",
                )

    def test_build_agent_execution_request_accepts_valid_profile_ref(self) -> None:
        """When a plan node carries a profile ID that is in the known snapshots,
        it should be passed through."""
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()
        wf._profile_snapshots = {"codex_openrouter_qwen36_plus", "codex_default"}

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "codex",
                        "profileId": "codex_openrouter_qwen36_plus",
                    },
                },
                node_id="node-valid-profile",
                tool_name="pr-resolver",
            )

        self.assertEqual(request.agent_id, "codex")
        self.assertEqual(request.execution_profile_ref, "codex_openrouter_qwen36_plus")

    def test_build_agent_execution_request_carries_selected_skill_in_metadata(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "codex",
                    "selectedSkill": "pr-resolver",
                },
                node_id="node-selected-skill",
                tool_name="codex",
            )

        metadata = request.parameters.get("metadata") or {}
        moonmind = metadata.get("moonmind") or {}
        self.assertEqual(moonmind.get("selectedSkill"), "pr-resolver")

    def test_build_agent_execution_request_carries_remediation_cadence_metadata(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "codex",
                    "title": "Verify remediation attempt 2 of 6",
                    "verify_artifact_path": "artifacts/jira-implement-verify.json",
                    "annotations": {
                        "issueImplementRole": "moonspec-verification-gate",
                        "moonSpecRemediationAttempt": 2,
                        "moonSpecRemediationMaxAttempts": 6,
                    },
                },
                node_id="verify-remediation-2",
                tool_name="codex",
            )

        metadata = request.parameters.get("metadata") or {}
        moonmind = metadata.get("moonmind") or {}
        self.assertEqual(
            moonmind.get("remediationCadence"),
            {
                "cadence": "attempt_scoped_remediation_verification",
                "role": "moonspec-verification-gate",
                "attempt": 2,
                "attemptArtifactPath": "reports/remediation_attempt-2.json",
                "verificationArtifactPath": "reports/remediation_verification-2.json",
                "maxAttempts": 6,
                "latestVerificationPath": "artifacts/jira-implement-verify.json",
            },
        )

    def test_build_agent_execution_request_carries_report_output_contract(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            namespace = "default"
            workflow_id = "parent-wf-id"
            run_id = "parent-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "codex",
                },
                node_id="node-report",
                tool_name="codex",
                workflow_parameters={
                    "reportOutput": {
                        "enabled": True,
                        "required": True,
                        "reportType": "integration_test_report",
                    }
                },
            )

        report_output = request.parameters["reportOutput"]
        self.assertEqual(report_output["reportType"], "integration_test_report")
        self.assertEqual(
            report_output["executionRef"],
            {
                "namespace": "default",
                "workflow_id": "parent-wf-id",
                "run_id": "parent-run-id",
            },
        )
        metadata = request.parameters.get("metadata") or {}
        self.assertEqual(metadata["moonmind"]["reportOutput"], report_output)

    def test_build_agent_execution_request_preserves_retry_feedback_in_instructions_fields(
        self,
    ) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        for instruction_key in ("instructions", "instructionRef"):
            with self.subTest(instruction_key=instruction_key):
                retried_inputs = wf._inject_review_feedback_into_inputs(
                    tool_type="agent_runtime",
                    original_inputs={
                        instruction_key: "Implement the requested change.",
                        "targetRuntime": "jules",
                    },
                    attempt=1,
                    feedback="Fix the missing import before retrying.",
                    issues=(),
                )

                with patch(
                    "moonmind.workflows.temporal.workflows.run.workflow.info",
                    return_value=MockInfo(),
                ):
                    request = wf._build_agent_execution_request(
                        node_inputs=retried_inputs,
                        node_id="node-review-retry",
                        tool_name="jules",
                    )

                self.assertIn("REVIEW FEEDBACK (attempt 1)", request.instruction_ref)
                self.assertIn(
                    "Fix the missing import before retrying.",
                    request.instruction_ref,
                )

    def test_build_agent_execution_request_overrides_stale_selected_skill(self) -> None:
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

        class MockInfo:
            workflow_id = "test-wf-id"
            run_id = "test-run-id"

        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=MockInfo(),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "codex",
                    "selectedSkill": "pr-resolver",
                    "runtime": {
                        "metadata": {
                            "moonmind": {"selectedSkill": "auto"},
                        }
                    },
                },
                node_id="node-selected-skill-override",
                tool_name="codex",
            )

        metadata = request.parameters.get("metadata") or {}
        moonmind = metadata.get("moonmind") or {}
        self.assertEqual(moonmind.get("selectedSkill"), "pr-resolver")

class TestReviewGateHelpers(unittest.TestCase):
    def test_review_gate_skips_matching_tool_identifier(self) -> None:
        wf = MoonMindRunWorkflow()
        approval_policy = SimpleNamespace(
            enabled=True,
            skip_tool_types=("repo.publish",),
        )

        is_active = wf._review_gate_active(
            approval_policy=approval_policy,
            tool_type="skill",
            tool_name="repo.publish",
        )

        self.assertFalse(is_active)

    def test_review_gate_still_skips_matching_tool_type(self) -> None:
        wf = MoonMindRunWorkflow()
        approval_policy = SimpleNamespace(
            enabled=True,
            skip_tool_types=("agent_runtime",),
        )

        is_active = wf._review_gate_active(
            approval_policy=approval_policy,
            tool_type="agent_runtime",
            tool_name="jules",
        )

        self.assertFalse(is_active)

    def test_accepted_review_summary_pluralizes_retries(self) -> None:
        wf = MoonMindRunWorkflow()

        self.assertEqual(
            wf._accepted_review_summary("FULLY_IMPLEMENTED", retry_count=2),
            "Approved after 2 retries",
        )

class TestFetchProfileSnapshots(unittest.TestCase):
    """Verify the _fetch_profile_snapshots method populates profile snapshots."""

    def test_fetch_profile_snapshots_populates_dict(self) -> None:
        """When provider_profile.list activity returns profiles, they should
        be stored in _profile_snapshots keyed by profile_id."""
        import asyncio
        from datetime import timedelta
        from unittest.mock import patch

        from temporalio.common import RetryPolicy

        wf = MoonMindRunWorkflow()

        # Provide a mock _execute_kwargs_for_route that returns valid kwargs
        wf._execute_kwargs_for_route = lambda route: {
            "task_queue": "mm.activity.artifacts",
            "start_to_close_timeout": timedelta(seconds=60),
            "schedule_to_close_timeout": timedelta(seconds=120),
            "retry_policy": RetryPolicy(maximum_attempts=1),
        }

        async def run_test() -> None:
            async def mock_execute_activity(
                activity_name: str, *args: object, **kwargs: object
            ) -> object:
                if activity_name == "provider_profile.list":
                    runtime_id = args[0].get("runtime_id") if args else kwargs.get("runtime_id")
                    if runtime_id == "codex_cli":
                        return {
                            "profiles": [
                                {
                                    "profile_id": "codex_openrouter_qwen36_plus",
                                    "runtime_id": "codex_cli",
                                    "provider_id": "openrouter",
                                },
                                {
                                    "profile_id": "codex_default",
                                    "runtime_id": "codex_cli",
                                    "provider_id": "openai",
                                },
                            ]
                        }
                    elif runtime_id == "claude_code":
                        return {
                            "profiles": [
                                {
                                    "profile_id": "claude_anthropic_sonnet",
                                    "runtime_id": "claude_code",
                                    "provider_id": "anthropic",
                                },
                            ]
                        }
                    elif runtime_id == "claude_code":
                        return {"profiles": []}
                return {}

            with patch(
                "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
                side_effect=mock_execute_activity,
            ):
                await wf._fetch_profile_snapshots()

            self.assertIn("codex_openrouter_qwen36_plus", wf._profile_snapshots)
            self.assertIn("codex_default", wf._profile_snapshots)
            self.assertIn("claude_anthropic_sonnet", wf._profile_snapshots)
            self.assertNotIn("default:codex_cli", wf._profile_snapshots)

        asyncio.run(run_test())

    def test_fetch_profile_snapshots_tolerates_activity_failure(self) -> None:
        """When provider_profile.list activity fails, _fetch_profile_snapshots
        should not raise and should continue with whatever profiles it got."""
        import asyncio
        from datetime import timedelta
        from unittest.mock import patch

        from temporalio.common import RetryPolicy

        wf = MoonMindRunWorkflow()

        wf._execute_kwargs_for_route = lambda route: {
            "task_queue": "mm.activity.artifacts",
            "start_to_close_timeout": timedelta(seconds=60),
            "schedule_to_close_timeout": timedelta(seconds=120),
            "retry_policy": RetryPolicy(maximum_attempts=1),
        }

        async def run_test() -> None:
            async def mock_execute_activity(
                activity_name: str, *args: object, **kwargs: object
            ) -> object:
                if activity_name == "provider_profile.list":
                    runtime_id = args[0].get("runtime_id") if args else kwargs.get("runtime_id")
                    if runtime_id == "codex_cli":
                        raise RuntimeError("DB connection failed")
                    elif runtime_id == "claude_code":
                        return {
                            "profiles": [
                                {
                                    "profile_id": "claude_anthropic_sonnet",
                                    "runtime_id": "claude_code",
                                    "provider_id": "anthropic",
                                },
                            ]
                        }
                    elif runtime_id == "claude_code":
                        return {"profiles": []}
                return {}

            with patch(
                "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
                side_effect=mock_execute_activity,
            ):
                # Should not raise — best-effort fetch
                await wf._fetch_profile_snapshots()

            # codex_cli profiles are missing, but claude_code profiles should be there
            self.assertIn("claude_anthropic_sonnet", wf._profile_snapshots)
            self.assertNotIn("codex_openrouter_qwen36_plus", wf._profile_snapshots)

        asyncio.run(run_test())

    def test_fetch_profile_snapshots_empty_result_does_not_set_snapshots(self) -> None:
        """When all activity calls return empty profiles, _profile_snapshots
        should NOT be set, so validation is skipped (best-effort behavior)."""
        import asyncio
        from datetime import timedelta
        from unittest.mock import patch

        from temporalio.common import RetryPolicy

        wf = MoonMindRunWorkflow()

        wf._execute_kwargs_for_route = lambda route: {
            "task_queue": "mm.activity.artifacts",
            "start_to_close_timeout": timedelta(seconds=60),
            "schedule_to_close_timeout": timedelta(seconds=120),
            "retry_policy": RetryPolicy(maximum_attempts=1),
        }

        async def run_test() -> None:
            async def mock_execute_activity(
                activity_name: str, *args: object, **kwargs: object
            ) -> object:
                return {"profiles": []}

            with patch(
                "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
                side_effect=mock_execute_activity,
            ):
                await wf._fetch_profile_snapshots()

            # Should NOT be set when no data was fetched
            self.assertFalse(hasattr(wf, "_profile_snapshots"))

        asyncio.run(run_test())


class TestEnsureAssessmentParameters(unittest.TestCase):
    """Verify assessment-verdict handoff path propagation into agent parameters."""

    def test_propagates_from_top_level_node_inputs(self) -> None:
        wf = MoonMindRunWorkflow()
        parameters: dict[str, Any] = {}
        wf._ensure_assessment_parameters(
            parameters=parameters,
            node_inputs={"assessment_artifact_path": "artifacts/a.json"},
        )
        self.assertEqual(
            parameters["assessment_artifact_path"], "artifacts/a.json"
        )

    def test_propagates_from_nested_skill_inputs(self) -> None:
        wf = MoonMindRunWorkflow()
        parameters: dict[str, Any] = {}
        wf._ensure_assessment_parameters(
            parameters=parameters,
            node_inputs={"inputs": {"assessmentArtifactPath": "artifacts/b.json"}},
        )
        self.assertEqual(
            parameters["assessment_artifact_path"], "artifacts/b.json"
        )

    def test_noop_when_path_absent(self) -> None:
        wf = MoonMindRunWorkflow()
        parameters: dict[str, Any] = {}
        wf._ensure_assessment_parameters(
            parameters=parameters,
            node_inputs={"inputs": {"other": "value"}},
        )
        self.assertNotIn("assessment_artifact_path", parameters)

    def test_does_not_overwrite_existing(self) -> None:
        wf = MoonMindRunWorkflow()
        parameters: dict[str, Any] = {"assessment_artifact_path": "existing.json"}
        wf._ensure_assessment_parameters(
            parameters=parameters,
            node_inputs={"assessment_artifact_path": "artifacts/new.json"},
        )
        self.assertEqual(parameters["assessment_artifact_path"], "existing.json")

    def test_propagates_issue_brief_path_for_durable_handoff(self) -> None:
        wf = MoonMindRunWorkflow()
        parameters: dict[str, Any] = {}
        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: (
                patch_id == RUN_ISSUE_BRIEF_ATTACHMENT_HANDOFF_PATCH
            ),
        ):
            wf._ensure_assessment_parameters(
                parameters=parameters,
                node_inputs={
                    "assessment_artifact_path": "artifacts/assessment.json",
                    "brief_artifact_path": "artifacts/brief.json",
                },
            )

        self.assertEqual(
            parameters,
            {
                "assessment_artifact_path": "artifacts/assessment.json",
                "brief_artifact_path": "artifacts/brief.json",
            },
        )

    def test_preserves_assessment_ref_across_intervening_outputs(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._record_assessment_context(
            {
                "assessmentArtifactRef": "art_assessment_1",
                "assessmentVerdict": "FULLY_IMPLEMENTED",
            }
        )

        merged = wf._merge_trusted_issue_context({"summary": "classification done"})

        self.assertEqual(merged["assessmentArtifactRef"], "art_assessment_1")
        self.assertEqual(merged["assessmentVerdict"], "FULLY_IMPLEMENTED")
        self.assertEqual(merged["summary"], "classification done")

    def test_assessment_context_binds_assessed_workspace_identity(self) -> None:
        wf = MoonMindRunWorkflow()
        request = AgentExecutionRequest(
            agentKind="managed",
            agentId="codex_cli",
            correlationId="workflow-1",
            idempotencyKey="assessment-1",
            workspaceSpec={
                "repository": "MoonLadderStudios/MoonMind",
                "startingBranch": "main",
            },
        )

        wf._record_assessment_context(
            {
                "assessmentArtifactRef": "art_assessment_1",
                "assessmentVerdict": "FULLY_IMPLEMENTED",
            },
            request=request,
        )

        self.assertEqual(
            wf._assessment_context["assessedRepository"],
            "MoonLadderStudios/MoonMind",
        )
        self.assertEqual(wf._assessment_context["assessedBranch"], "main")

    def test_assessment_context_is_included_for_agent_handoff(self) -> None:
        context = MoonMindRunWorkflow._trusted_previous_outputs_context(
            {
                "trustedSource": "moonmind.github.get_issue",
                "issueRef": "MoonLadderStudios/MoonMind#3620",
                "assessmentArtifactRef": "art_assessment_1",
                "assessmentVerdict": "PARTIALLY_IMPLEMENTED",
            },
            include_assessment=True,
        )

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context["assessmentArtifactRef"], "art_assessment_1")
        self.assertEqual(context["assessmentVerdict"], "PARTIALLY_IMPLEMENTED")

    def test_external_agent_receives_assessment_as_durable_attachment(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._record_assessment_context(
            {
                "assessmentArtifactRef": "art_assessment_1",
                "assessmentVerdict": "PARTIALLY_IMPLEMENTED",
            }
        )

        refs = wf._append_durable_handoff_attachment_refs(
            ["artifact://prepared_input"],
            agent_kind="external",
        )

        self.assertEqual(
            refs,
            ["artifact://prepared_input", "artifact://art_assessment_1"],
        )

    def test_external_agent_ignores_absent_optional_handoff_refs(self) -> None:
        wf = MoonMindRunWorkflow()
        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: (
                patch_id == RUN_ISSUE_BRIEF_ATTACHMENT_HANDOFF_PATCH
            ),
        ):
            refs = wf._append_durable_handoff_attachment_refs(
                ["artifact://prepared_input"],
                agent_kind="external",
            )

        self.assertEqual(refs, ["artifact://prepared_input"])

    def test_managed_agent_does_not_receive_external_attachment(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._record_assessment_context(
            {"assessmentArtifactRef": "art_assessment_1"}
        )

        refs = wf._append_durable_handoff_attachment_refs(
            [], agent_kind="managed"
        )

        self.assertEqual(refs, [])

    def test_external_request_carries_assessment_context_and_attachment(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._record_assessment_context(
            {
                "assessmentArtifactRef": "art_assessment_1",
                "assessmentVerdict": "PARTIALLY_IMPLEMENTED",
            }
        )
        info = SimpleNamespace(
            namespace="default",
            workflow_id="mm:assessment-handoff",
            run_id="run-assessment-handoff",
            parent=None,
        )
        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: (
                    patch_id == RUN_ASSESSMENT_ATTACHMENT_HANDOFF_PATCH
                ),
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "omnigent",
                    "instructions": "Implement the remaining issue gaps.",
                    "previousOutputs": {
                        "trustedSource": "moonmind.github.get_issue",
                        "issueRef": "MoonLadderStudios/MoonMind#3620",
                        "assessmentArtifactRef": "art_assessment_1",
                        "assessmentVerdict": "PARTIALLY_IMPLEMENTED",
                    },
                },
                node_id="implement",
                tool_name="omnigent",
            )

        self.assertIn("artifact://art_assessment_1", request.input_refs)
        self.assertIn("PARTIALLY_IMPLEMENTED", request.instruction_ref or "")
        self.assertIn(".moonmind/attachments/", request.instruction_ref or "")

    def test_external_request_carries_issue_brief_attachment(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._assessment_context.update(
            {
                "assessmentArtifactRef": "art_assessment_1",
                "briefArtifactRef": "art_brief_1",
                "assessmentVerdict": "PARTIALLY_IMPLEMENTED",
            }
        )
        info = SimpleNamespace(
            namespace="default",
            workflow_id="mm:issue-brief-handoff",
            run_id="run-issue-brief-handoff",
            parent=None,
        )
        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: patch_id
                in {
                    RUN_ASSESSMENT_ATTACHMENT_HANDOFF_PATCH,
                    RUN_ISSUE_BRIEF_ATTACHMENT_HANDOFF_PATCH,
                },
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "omnigent",
                    "repository": "MoonLadderStudios/MoonMind",
                    "instructions": "Verify the implementation.",
                    "previousOutputs": {
                        "trustedSource": "moonmind.github.get_issue",
                        "assessmentArtifactRef": "art_assessment_1",
                        "briefArtifactRef": "art_brief_1",
                    },
                },
                node_id="verify",
                tool_name="omnigent",
            )

        self.assertIn("artifact://art_assessment_1", request.input_refs)
        self.assertIn("artifact://art_brief_1", request.input_refs)
        self.assertIn("issue-brief JSON", request.instruction_ref or "")

    def test_external_request_carries_controlling_verifier_attachment(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._assessment_context.update(
            {
                "assessmentArtifactRef": "art_assessment_1",
                "briefArtifactRef": "art_brief_1",
                "assessmentVerdict": "PARTIALLY_IMPLEMENTED",
            }
        )
        wf._publish_context["moonSpecGate"] = {
            "verdict": "FULLY_IMPLEMENTED",
            "recommendedNextAction": "advance",
            "gateResultRef": "art_verify_1",
        }
        info = SimpleNamespace(
            namespace="default",
            workflow_id="mm:verify-handoff",
            run_id="run-verify-handoff",
            parent=None,
        )
        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: patch_id
                in {
                    RUN_ASSESSMENT_ATTACHMENT_HANDOFF_PATCH,
                    RUN_ISSUE_BRIEF_ATTACHMENT_HANDOFF_PATCH,
                    RUN_MOONSPEC_GATE_PREVIOUS_OUTPUTS_HANDOFF_PATCH,
                    RUN_MOONSPEC_VERIFY_ATTACHMENT_HANDOFF_PATCH,
                },
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "omnigent",
                    "repository": "MoonLadderStudios/MoonMind",
                    "instructions": "Create the verified pull request.",
                    "previousOutputs": {
                        "trustedSource": "moonmind.github.get_issue",
                        "assessmentArtifactRef": "art_assessment_1",
                        "briefArtifactRef": "art_brief_1",
                        "moonSpecVerify": {
                            "verdict": "FULLY_IMPLEMENTED",
                            "recommendedNextAction": "advance",
                            "gateResultRef": "art_verify_1",
                        },
                        "moonSpecVerifyArtifactRef": "art_verify_1",
                    },
                },
                node_id="pull-request",
                tool_name="omnigent",
            )

        self.assertIn("artifact://art_verify_1", request.input_refs)
        self.assertIn("moonSpecVerifyArtifactRef", request.instruction_ref or "")
        self.assertIn("FULLY_IMPLEMENTED", request.instruction_ref or "")

    def test_trusted_full_assessment_authorizes_verified_no_commit_result(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._assessment_context.update(
            {
                "assessmentArtifactRef": "art_assessment_1",
                "assessmentVerdict": "FULLY_IMPLEMENTED",
                "assessedRepository": "MoonLadderStudios/MoonMind",
                "assessedBranch": "main",
            }
        )
        info = SimpleNamespace(
            namespace="default",
            workflow_id="mm:trusted-no-commit",
            run_id="run-trusted-no-commit",
            parent=None,
        )
        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: (
                    patch_id
                    in {
                        RUN_TRUSTED_NO_COMMIT_REPOSITORY_OUTCOME_PATCH,
                        RUN_REPOSITORY_BOUND_NO_COMMIT_OUTCOME_PATCH,
                    }
                ),
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "omnigent",
                    "repository": "MoonLadderStudios/MoonMind",
                    "startingBranch": "main",
                    "repositoryOperation": "write",
                    "publishMode": "pr",
                    # Authored inputs cannot replace this reserved policy.
                    "repositoryOutcomePolicy": {
                        "allowNoCommit": False,
                        "authority": "user",
                    },
                },
                node_id="implement",
                tool_name="omnigent",
            )

        self.assertEqual(
            request.parameters["repositoryOutcomePolicy"],
            {
                "schemaVersion": "repository-outcome-policy/v2",
                "allowNoCommit": True,
                "authority": "trusted_assessment",
                "assessmentVerdict": "FULLY_IMPLEMENTED",
                "assessmentArtifactRef": "art_assessment_1",
                "assessedRepository": "MoonLadderStudios/MoonMind",
                "assessedBranch": "main",
            },
        )

    def test_assessment_for_another_repository_does_not_authorize_no_commit(
        self,
    ) -> None:
        wf = MoonMindRunWorkflow()
        wf._assessment_context.update(
            {
                "assessmentArtifactRef": "art_assessment_1",
                "assessmentVerdict": "FULLY_IMPLEMENTED",
                "assessedRepository": "another/repository",
                "assessedBranch": "main",
            }
        )
        info = SimpleNamespace(
            namespace="default",
            workflow_id="mm:repository-bound-assessment",
            run_id="run-repository-bound-assessment",
            parent=None,
        )
        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                return_value=True,
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "omnigent",
                    "repository": "MoonLadderStudios/MoonMind",
                    "startingBranch": "main",
                    "repositoryOperation": "write",
                    "publishMode": "pr",
                },
                node_id="implement",
                tool_name="omnigent",
            )

        self.assertNotIn("repositoryOutcomePolicy", request.parameters)

    def test_replay_preserves_pre_binding_no_commit_request_shape(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._assessment_context.update(
            {
                "assessmentArtifactRef": "art_assessment_1",
                "assessmentVerdict": "FULLY_IMPLEMENTED",
            }
        )
        info = SimpleNamespace(
            namespace="default",
            workflow_id="mm:replay-no-commit-v1",
            run_id="run-replay-no-commit-v1",
            parent=None,
        )
        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: (
                    patch_id == RUN_TRUSTED_NO_COMMIT_REPOSITORY_OUTCOME_PATCH
                ),
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "omnigent",
                    "repository": "MoonLadderStudios/MoonMind",
                    "repositoryOperation": "write",
                    "publishMode": "pr",
                },
                node_id="implement",
                tool_name="omnigent",
            )

        self.assertEqual(
            request.parameters["repositoryOutcomePolicy"],
            {
                "schemaVersion": "repository-outcome-policy/v1",
                "allowNoCommit": True,
                "authority": "trusted_assessment",
                "assessmentVerdict": "FULLY_IMPLEMENTED",
                "assessmentArtifactRef": "art_assessment_1",
            },
        )

    def test_partial_assessment_does_not_authorize_no_commit_result(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._assessment_context.update(
            {
                "assessmentArtifactRef": "art_assessment_1",
                "assessmentVerdict": "PARTIALLY_IMPLEMENTED",
            }
        )
        info = SimpleNamespace(
            namespace="default",
            workflow_id="mm:untrusted-no-commit",
            run_id="run-untrusted-no-commit",
            parent=None,
        )
        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                return_value=True,
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "omnigent",
                    "repository": "MoonLadderStudios/MoonMind",
                    "repositoryOperation": "write",
                    "publishMode": "pr",
                },
                node_id="implement",
                tool_name="omnigent",
            )

        self.assertNotIn("repositoryOutcomePolicy", request.parameters)

    def test_downstream_request_uses_verified_published_branch(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._publish_context.update(
            {
                "pushStatus": "pushed",
                "branch": "moonmind-job-2602f4e9",
                "headSha": "2b3b49127a73785807b32ea7f2cc4deee376fdc0",
                "baseRef": "main",
            }
        )
        info = SimpleNamespace(
            namespace="default",
            workflow_id="mm:published-branch-handoff",
            run_id="run-published-branch-handoff",
            parent=None,
        )
        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: (
                    patch_id == RUN_PUBLISHED_BRANCH_HANDOFF_PATCH
                ),
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "omnigent",
                    "repository": "MoonLadderStudios/MoonMind",
                    "repositoryTarget": {
                        "provider": "git",
                        "repository": {"name": "MoonLadderStudios/MoonMind"},
                        "branch": {"name": "main"},
                        "connectionRef": "repository-connection:git-default",
                    },
                    "startingBranch": "main",
                    "targetBranch": "generated-plan-branch",
                },
                node_id="verify",
                tool_name="omnigent",
            )

        assert request.workspace_spec is not None
        self.assertEqual(
            request.workspace_spec["startingBranch"],
            "moonmind-job-2602f4e9",
        )
        self.assertEqual(
            request.workspace_spec["targetBranch"],
            "moonmind-job-2602f4e9",
        )
        self.assertEqual(
            request.workspace_spec["repositoryTarget"]["revision"]["commitSha"],
            "2b3b49127a73785807b32ea7f2cc4deee376fdc0",
        )

    def test_remediation_request_writes_to_verified_published_branch(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._publish_context.update(
            {
                "pushStatus": "pushed",
                "branch": "moonmind-job-2602f4e9",
                "headSha": "2b3b49127a73785807b32ea7f2cc4deee376fdc0",
                "baseRef": "main",
            }
        )
        info = SimpleNamespace(
            namespace="default",
            workflow_id="mm:published-branch-remediation",
            run_id="run-published-branch-remediation",
            parent=None,
        )
        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: (
                    patch_id == RUN_PUBLISHED_BRANCH_HANDOFF_PATCH
                ),
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "omnigent",
                    "repository": "MoonLadderStudios/MoonMind",
                    "repositoryOperation": "write",
                    "startingBranch": "main",
                    "targetBranch": "generated-plan-branch",
                },
                node_id="remediate",
                tool_name="omnigent",
            )

        assert request.workspace_spec is not None
        self.assertEqual(request.workspace_spec["startingBranch"], "main")
        self.assertEqual(
            request.workspace_spec["targetBranch"],
            "moonmind-job-2602f4e9",
        )
        self.assertEqual(request.parameters["repositoryOperation"], "write")

    def test_pr_publish_request_defaults_to_write_on_published_branch(self) -> None:
        wf = MoonMindRunWorkflow()
        wf._publish_context.update(
            {
                "pushStatus": "pushed",
                "branch": "moonmind-job-24a73665",
                "headSha": "b651c9e47fe6e7696ef575b6969ed7d682c78338",
                "baseRef": "main",
            }
        )
        info = SimpleNamespace(
            namespace="default",
            workflow_id="mm:6103dddf-2e68-4185-9481-5668d5916642",
            run_id="019fe3a7-4522-78a5-af30-9ea9a5a03311",
            parent=None,
        )
        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=info,
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: patch_id
                in {
                    RUN_PUBLISHED_BRANCH_HANDOFF_PATCH,
                    RUN_PUBLISH_MODE_REPOSITORY_OPERATION_PATCH,
                },
            ),
        ):
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "omnigent",
                    "repository": "MoonLadderStudios/MoonMind",
                    "startingBranch": "main",
                    "targetBranch": "generated-plan-branch",
                    "publishMode": "pr",
                },
                node_id="implement",
                tool_name="omnigent",
            )

        assert request.workspace_spec is not None
        self.assertEqual(request.workspace_spec["startingBranch"], "main")
        self.assertEqual(
            request.workspace_spec["targetBranch"],
            "moonmind-job-24a73665",
        )
        self.assertEqual(request.parameters["repositoryOperation"], "write")

    def test_pushed_result_records_downstream_branch_authority(self) -> None:
        wf = MoonMindRunWorkflow()
        with patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: (
                patch_id
                in {
                    RUN_PUBLISHED_BRANCH_HANDOFF_PATCH,
                    RUN_EXTERNAL_PUBLISHED_BRANCH_REMEDIATION_PATCH,
                }
            ),
        ):
            wf._record_execution_context(
                node_id="implement",
                execution_result={
                    "outputs": {
                        "push_status": "pushed",
                        "push_branch": "moonmind-job-2602f4e9",
                        "baseBranch": "main",
                        "push_head_sha": (
                            "2b3b49127a73785807b32ea7f2cc4deee376fdc0"
                        ),
                    }
                },
            )

        self.assertEqual(wf._publish_context["pushStatus"], "pushed")
        self.assertEqual(
            wf._publish_context["branch"],
            "moonmind-job-2602f4e9",
        )
        self.assertEqual(
            wf._publish_context["headSha"],
            "2b3b49127a73785807b32ea7f2cc4deee376fdc0",
        )
        self.assertEqual(wf._publish_context["baseRef"], "main")
