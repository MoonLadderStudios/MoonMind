"""Unit tests for TemporalProposalActivities."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock

from moonmind.workflows.temporal.activity_runtime import (
    TemporalProposalActivities,
)


class TestProposalGenerate(unittest.IsolatedAsyncioTestCase):
    async def test_returns_empty_list_stub(self) -> None:
        activities = TemporalProposalActivities()
        result = await activities.proposal_generate({"principal": "test-user"})
        self.assertEqual(result, [])

    async def test_returns_empty_list_without_args(self) -> None:
        activities = TemporalProposalActivities()
        result = await activities.proposal_generate(None)
        self.assertEqual(result, [])


class TestProposalSubmit(unittest.IsolatedAsyncioTestCase):
    async def test_empty_candidates_returns_zeroes(self) -> None:
        activities = TemporalProposalActivities()
        result = await activities.proposal_submit({"candidates": [], "policy": {}})
        self.assertEqual(result["generated_count"], 0)
        self.assertEqual(result["submitted_count"], 0)
        self.assertEqual(result["errors"], [])

    async def test_valid_candidates_counted(self) -> None:
        activities = TemporalProposalActivities()
        candidates = [
            {
                "title": "Fix bug",
                "summary": "There is a bug in module X",
                "taskCreateRequest": {"payload": {"repository": "org/repo"}},
            },
            {
                "title": "Add feature",
                "summary": "Feature Y would be helpful",
                "taskCreateRequest": {"payload": {"repository": "org/repo"}},
            },
        ]
        result = await activities.proposal_submit(
            {"candidates": candidates, "policy": {}, "origin": {}}
        )
        self.assertEqual(result["generated_count"], 2)
        self.assertEqual(result["submitted_count"], 2)
        self.assertEqual(result["errors"], [])

    async def test_malformed_candidates_skipped(self) -> None:
        activities = TemporalProposalActivities()
        candidates: list[Any] = [
            {"title": "", "summary": "Missing title", "taskCreateRequest": {}},
            "not a dict",
            {
                "title": "Valid",
                "summary": "This one is valid",
                "taskCreateRequest": {"payload": {}},
            },
        ]
        result = await activities.proposal_submit(
            {"candidates": candidates, "policy": {}, "origin": {}}
        )
        self.assertEqual(result["generated_count"], 3)
        self.assertEqual(result["submitted_count"], 1)
        self.assertEqual(len(result["errors"]), 2)

    async def test_max_items_policy_respected(self) -> None:
        activities = TemporalProposalActivities()
        candidates = [
            {
                "title": f"Proposal {i}",
                "summary": f"Summary {i}",
                "taskCreateRequest": {"payload": {"repository": "org/repo"}},
            }
            for i in range(5)
        ]
        result = await activities.proposal_submit(
            {"candidates": candidates, "policy": {"max_items": 2}, "origin": {}}
        )
        self.assertEqual(result["generated_count"], 5)
        self.assertEqual(result["submitted_count"], 2)

    async def test_service_factory_called(self) -> None:
        mock_service = AsyncMock()
        factory = lambda: mock_service
        activities = TemporalProposalActivities(
            proposal_service_factory=factory,
        )
        candidates = [
            {
                "title": "Fix bug",
                "summary": "There is a bug",
                "taskCreateRequest": {"payload": {"repository": "org/repo"}},
            },
        ]
        result = await activities.proposal_submit(
            {
                "candidates": candidates,
                "policy": {},
                "origin": {"workflow_id": "wf-1", "temporal_run_id": "run-1"},
            }
        )
        self.assertEqual(result["submitted_count"], 1)
        mock_service.create_proposal.assert_awaited_once()

    async def test_service_failure_recorded(self) -> None:
        mock_service = AsyncMock()
        mock_service.create_proposal.side_effect = RuntimeError("DB down")
        factory = lambda: mock_service
        activities = TemporalProposalActivities(
            proposal_service_factory=factory,
        )
        candidates = [
            {
                "title": "Fix bug",
                "summary": "There is a bug",
                "taskCreateRequest": {"payload": {"repository": "org/repo"}},
            },
        ]
        result = await activities.proposal_submit(
            {"candidates": candidates, "policy": {}, "origin": {}}
        )
        self.assertEqual(result["submitted_count"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("DB down", result["errors"][0])


class TestProposalSubmitRuntimeStamping(unittest.IsolatedAsyncioTestCase):
    async def test_default_runtime_stamped_into_candidate(self) -> None:
        """When default_runtime is set and candidate has no runtime, stamp it."""
        mock_service = AsyncMock()
        activities = TemporalProposalActivities(
            proposal_service_factory=lambda: mock_service,
        )
        candidates = [
            {
                "title": "Fix bug",
                "summary": "Bug in module X",
                "taskCreateRequest": {
                    "payload": {
                        "repository": "org/repo",
                        "task": {"instructions": "fix it"},
                    }
                },
            },
        ]
        await activities.proposal_submit(
            {
                "candidates": candidates,
                "policy": {"default_runtime": "jules"},
                "origin": {},
            }
        )
        call_kwargs = mock_service.create_proposal.call_args.kwargs
        stamped = call_kwargs["task_create_request"]
        self.assertEqual(
            stamped["payload"]["task"]["runtime"]["mode"], "jules"
        )

    async def test_default_runtime_preserves_existing(self) -> None:
        """When candidate already specifies a runtime, do not overwrite."""
        mock_service = AsyncMock()
        activities = TemporalProposalActivities(
            proposal_service_factory=lambda: mock_service,
        )
        candidates = [
            {
                "title": "Fix bug",
                "summary": "Bug in module X",
                "taskCreateRequest": {
                    "payload": {
                        "repository": "org/repo",
                        "task": {
                            "instructions": "fix it",
                            "runtime": {"mode": "codex"},
                        },
                    }
                },
            },
        ]
        await activities.proposal_submit(
            {
                "candidates": candidates,
                "policy": {"default_runtime": "jules"},
                "origin": {},
            }
        )
        call_kwargs = mock_service.create_proposal.call_args.kwargs
        stamped = call_kwargs["task_create_request"]
        self.assertEqual(
            stamped["payload"]["task"]["runtime"]["mode"], "codex"
        )

    async def test_default_runtime_stamps_missing_task_node(self) -> None:
        """When payload exists but has no task node, create it."""
        mock_service = AsyncMock()
        activities = TemporalProposalActivities(
            proposal_service_factory=lambda: mock_service,
        )
        candidates = [
            {
                "title": "Fix bug",
                "summary": "Bug in module X",
                "taskCreateRequest": {
                    "payload": {"repository": "org/repo"}
                },
            },
        ]
        await activities.proposal_submit(
            {
                "candidates": candidates,
                "policy": {"default_runtime": "gemini_cli"},
                "origin": {},
            }
        )
        call_kwargs = mock_service.create_proposal.call_args.kwargs
        stamped = call_kwargs["task_create_request"]
        self.assertEqual(
            stamped["payload"]["task"]["runtime"]["mode"], "gemini_cli"
        )

    async def test_no_default_runtime_leaves_candidate_untouched(self) -> None:
        """When default_runtime is None, do not modify the candidate."""
        mock_service = AsyncMock()
        activities = TemporalProposalActivities(
            proposal_service_factory=lambda: mock_service,
        )
        candidates = [
            {
                "title": "Fix bug",
                "summary": "Bug in module X",
                "taskCreateRequest": {
                    "payload": {"repository": "org/repo"}
                },
            },
        ]
        await activities.proposal_submit(
            {
                "candidates": candidates,
                "policy": {},
                "origin": {},
            }
        )
        call_kwargs = mock_service.create_proposal.call_args.kwargs
        stamped = call_kwargs["task_create_request"]
        self.assertNotIn("task", stamped["payload"])

