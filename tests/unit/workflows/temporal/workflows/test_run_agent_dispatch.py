"""Unit tests for agent dispatch helpers in MoonMind.Run.

Pure unit tests — no Temporal test server needed.
"""

import unittest
from types import SimpleNamespace

import pytest

pytest.importorskip("temporalio")

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


class TestAgentKindForId(unittest.TestCase):
    """Verify the _agent_kind_for_id static method."""

    def test_managed_agent_ids(self) -> None:
        wf = MoonMindRunWorkflow()
        for agent_id in ("gemini_cli", "gemini_cli", "claude", "claude_code", "codex", "codex_cli"):
            self.assertEqual(wf._agent_kind_for_id(agent_id), "managed", f"{agent_id} should be managed")

    def test_external_agent_ids(self) -> None:
        wf = MoonMindRunWorkflow()
        for agent_id in ("jules", "openhands", "custom_agent"):
            self.assertEqual(wf._agent_kind_for_id(agent_id), "external", f"{agent_id} should be external")

    def test_case_insensitive(self) -> None:
        wf = MoonMindRunWorkflow()
        self.assertEqual(wf._agent_kind_for_id("Gemini_cli"), "managed")
        self.assertEqual(wf._agent_kind_for_id("CLAUDE_CODE"), "managed")

    def test_hyphenated_managed_agent_ids(self) -> None:
        wf = MoonMindRunWorkflow()
        for agent_id in ("gemini-cli", "claude-code", "codex-cli"):
            self.assertEqual(
                wf._agent_kind_for_id(agent_id),
                "managed",
                f"{agent_id} should be managed",
            )


class TestSlotContinuityMetadata(unittest.TestCase):
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
                "tool": {"type": "agent_runtime", "name": "gemini_cli"},
                "inputs": {"runtime": {"mode": "gemini_cli"}},
            },
        ]

        wf._mark_slot_continuity_for_next_step(
            request=request,
            ordered_nodes=ordered_nodes,
            current_index=1,
        )

        self.assertEqual(request.parameters, {})


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
                {"outputs": {"push_status": "pushed", "push_branch": "jira-edits"}}
            )
        )
        self.assertFalse(
            wf._execution_result_has_publishable_changes(
                {"outputs": {"push_status": "no_commits"}}
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

    def test_build_agent_execution_request_falls_back_on_invalid_profile_ref(self) -> None:
        """When a plan node carries a profile ID that doesn't match any known
        profile for the runtime (e.g. AI-hallucinated 'default:codex_cli'),
        the dispatcher should reject it and fall back to auto-selection."""
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
            request = wf._build_agent_execution_request(
                node_inputs={
                    "runtime": {
                        "mode": "codex",
                        "profileId": "default:codex_cli",  # Invalid / not in snapshots
                    },
                },
                node_id="node-invalid-profile",
                tool_name="pr-resolver",
            )

        self.assertEqual(request.agent_id, "codex")
        self.assertIsNone(request.execution_profile_ref)

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
            wf._accepted_review_summary("PASS", retry_count=2),
            "Approved after 2 retries",
        )


class TestFetchProfileSnapshots(unittest.TestCase):
    """Verify the _fetch_profile_snapshots method populates profile snapshots."""

    def test_fetch_profile_snapshots_populates_dict(self) -> None:
        """When provider_profile.list activity returns profiles, they should
        be stored in _profile_snapshots keyed by profile_id."""
        import asyncio
        from datetime import timedelta
        from temporalio.common import RetryPolicy
        from unittest.mock import patch

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
                    elif runtime_id == "gemini_cli":
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
        from temporalio.common import RetryPolicy
        from unittest.mock import patch

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
                    elif runtime_id == "gemini_cli":
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
        from temporalio.common import RetryPolicy
        from unittest.mock import patch

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
