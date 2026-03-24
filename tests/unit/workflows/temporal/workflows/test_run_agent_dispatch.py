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
