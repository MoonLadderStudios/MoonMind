"""Unit tests for agent dispatch helpers in MoonMind.Run.

Pure unit tests — no Temporal test server needed.
"""

import unittest

import pytest

pytest.importorskip("temporalio")

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


class TestFetchProfileSnapshots(unittest.TestCase):
    """Verify the _fetch_profile_snapshots method populates profile snapshots."""

    def test_fetch_profile_snapshots_populates_dict(self) -> None:
        """When provider_profile.list activity returns profiles, they should
        be stored in _profile_snapshots keyed by profile_id."""
        import asyncio
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

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
        from unittest.mock import patch

        wf = MoonMindRunWorkflow()

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
