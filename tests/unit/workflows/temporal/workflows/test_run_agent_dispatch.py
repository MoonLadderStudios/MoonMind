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
