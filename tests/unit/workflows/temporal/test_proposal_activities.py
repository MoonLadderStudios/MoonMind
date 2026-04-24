"""Unit tests for TemporalProposalActivities."""

from __future__ import annotations

import unittest
import contextlib
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4
from datetime import UTC, datetime
from types import SimpleNamespace

from moonmind.utils.logging import SecretRedactor
from moonmind.workflows.temporal.activity_runtime import (
    TemporalProposalActivities,
)
from moonmind.workflows.task_proposals.models import (
    TaskProposalOriginSource,
    TaskProposalStatus,
)
from moonmind.workflows.task_proposals.service import TaskProposalService

class TestProposalGenerate(unittest.IsolatedAsyncioTestCase):
    async def test_returns_empty_list_stub(self) -> None:
        activities = TemporalProposalActivities()
        result = await activities.proposal_generate({"principal": "test-user"})
        self.assertEqual(result, [])

    async def test_returns_empty_list_without_args(self) -> None:
        activities = TemporalProposalActivities()
        result = await activities.proposal_generate(None)
        self.assertEqual(result, [])

    async def test_proposal_generate_uses_explicit_next_step_title(self) -> None:
        activities = TemporalProposalActivities()

        req_normal = {
            "workflow_id": "test-wf-12345",
            "parameters": {"instructions": "Implement feature X"},
            "proposalIdea": "Add regression coverage for feature X",
        }
        res_normal = await activities.proposal_generate(req_normal)
        self.assertEqual(len(res_normal), 1)
        self.assertEqual(
            res_normal[0]["title"],
            "[run_quality] Add regression coverage for feature X",
        )
        self.assertIn("Proposed next step", res_normal[0]["summary"])

    async def test_proposal_generate_reads_next_step_from_result_payload(self) -> None:
        activities = TemporalProposalActivities()

        req = {
            "workflow_id": "8e3b0e11-4a1d-40c9-94fc-0d3f2a1b9c8e",
            "parameters": {"instructions": "Fix bug in Y"},
            "result": {"next_step": "Audit adjacent error handling paths"},
        }
        res_uuid = await activities.proposal_generate(req)
        self.assertEqual(
            res_uuid[0]["title"],
            "[run_quality] Audit adjacent error handling paths",
        )

    async def test_proposal_generate_reads_top_level_next_step_snake_case(self) -> None:
        activities = TemporalProposalActivities()

        req = {
            "workflow_id": "snake-case-next-step",
            "parameters": {"instructions": "Fix bug in Y"},
            "next_step": "Audit adjacent error handling paths",
        }

        result = await activities.proposal_generate(req)

        self.assertEqual(
            result[0]["title"],
            "[run_quality] Audit adjacent error handling paths",
        )

    async def test_proposal_generate_truncates_long_explicit_idea(self) -> None:
        activities = TemporalProposalActivities()

        long_idea = "A" * 400
        req_long = {
            "workflow_id": "very-long-id-12345678",
            "parameters": {"instructions": "Implement feature X"},
            "proposalIdea": long_idea,
        }
        res_long = await activities.proposal_generate(req_long)
        title = res_long[0]["title"]
        self.assertLessEqual(len(title), 194)
        self.assertTrue(title.startswith("[run_quality] AAA"))

    async def test_proposal_generate_returns_empty_without_distinct_next_step(self) -> None:
        activities = TemporalProposalActivities()

        result = await activities.proposal_generate(
            {
                "workflow_id": "test-wf-steps-1",
                "parameters": {
                    "task": {
                        "steps": [
                            {"id": "s1", "instructions": "Investigate failed proposal hooks"},
                            {"id": "s2", "instructions": "Add regression test coverage"},
                        ]
                    }
                },
            }
        )

        self.assertEqual(result, [])

    async def test_proposal_generate_rejects_structured_proposal_text(self) -> None:
        activities = TemporalProposalActivities()

        result = await activities.proposal_generate(
            {
                "workflow_id": "test-wf-structured",
                "parameters": {"instructions": "Investigate failed proposal hooks"},
                "proposalIdea": {"unexpected": "object"},
            }
        )

        self.assertEqual(result, [])

    async def test_proposal_generate_aligns_promoted_task_instructions(self) -> None:
        activities = TemporalProposalActivities()

        result = await activities.proposal_generate(
            {
                "workflow_id": "test-wf-follow-up",
                "parameters": {"instructions": "Implement feature X"},
                "proposalIdea": "Add regression coverage for feature X",
            }
        )

        self.assertEqual(
            result[0]["taskCreateRequest"]["payload"]["task"]["instructions"],
            "Add regression coverage for feature X\n\n"
            "Context from the completed task:\n"
            "Implement feature X",
        )

    async def test_proposal_generate_uses_proposal_idea_when_original_instructions_missing(
        self,
    ) -> None:
        activities = TemporalProposalActivities()

        result = await activities.proposal_generate(
            {
                "workflow_id": "test-wf-empty-instructions",
                "parameters": {},
                "proposalIdea": "Add regression coverage for feature X",
            }
        )

        self.assertEqual(
            result[0]["taskCreateRequest"]["payload"]["task"]["instructions"],
            "Add regression coverage for feature X",
        )

    async def test_proposal_generate_rejects_next_step_that_matches_workflow_title(self) -> None:
        activities = TemporalProposalActivities()

        result = await activities.proposal_generate(
            {
                "workflow_id": "test-wf-same-title",
                "parameters": {
                    "task": {
                        "title": "Fix proposal routing",
                        "instructions": "Fix proposal routing",
                    }
                },
                "proposalIdea": "Fix proposal routing",
            }
        )

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
            {
                "candidates": candidates,
                "policy": {"maxItems": {"project": 2}},
                "origin": {},
            }
        )
        self.assertEqual(result["generated_count"], 5)
        self.assertEqual(result["submitted_count"], 2)

    async def test_raw_task_policy_uses_per_target_caps(self) -> None:
        mock_service = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(
            proposal_service_factory=factory,
        )
        candidates = [
            {
                "title": "Project 1",
                "summary": "One",
                "taskCreateRequest": {"payload": {"repository": "org/repo"}},
            },
            {
                "title": "Project 2",
                "summary": "Two",
                "taskCreateRequest": {"payload": {"repository": "org/repo"}},
            },
            {
                "title": "MoonMind 1",
                "summary": "Three",
                "taskCreateRequest": {
                    "payload": {"repository": "MoonLadderStudios/MoonMind"}
                },
            },
            {
                "title": "MoonMind 2",
                "summary": "Four",
                "taskCreateRequest": {
                    "payload": {"repository": "MoonLadderStudios/MoonMind"}
                },
            },
        ]

        result = await activities.proposal_submit(
            {
                "candidates": candidates,
                "policy": {
                    "targets": ["project", "moonmind"],
                    "maxItems": {"project": 1, "moonmind": 2},
                    "minSeverityForMoonMind": "medium",
                },
                "origin": {},
            }
        )

        self.assertEqual(result["generated_count"], 4)
        self.assertEqual(result["submitted_count"], 3)
        self.assertEqual(mock_service.create_proposal.await_count, 3)

    async def test_defaults_apply_when_policy_missing(self) -> None:
        mock_service = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(
            proposal_service_factory=factory,
        )
        candidates = [
            {
                "title": f"Proposal {i}",
                "summary": f"Summary {i}",
                "taskCreateRequest": {"payload": {"repository": "org/repo"}},
            }
            for i in range(5)
        ]

        result = await activities.proposal_submit(
            {
                "candidates": candidates,
                "policy": {},
                "origin": {},
            }
        )

        self.assertEqual(result["generated_count"], 5)
        self.assertEqual(result["submitted_count"], 3)
        self.assertEqual(mock_service.create_proposal.await_count, 3)

    async def test_moonmind_repo_candidate_still_submits_as_project_by_default(self) -> None:
        mock_service = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(
            proposal_service_factory=factory,
        )
        candidates = [
            {
                "title": "[run_quality] Follow-up: Fix proposal routing",
                "summary": "Proposal should remain project-targeted for MoonMind repo runs.",
                "category": "run_quality",
                "tags": ["artifact_gap"],
                "taskCreateRequest": {
                    "payload": {"repository": "MoonLadderStudios/MoonMind"}
                },
            }
        ]

        result = await activities.proposal_submit(
            {
                "candidates": candidates,
                "policy": {},
                "origin": {},
            }
        )

        self.assertEqual(result["generated_count"], 1)
        self.assertEqual(result["submitted_count"], 1)
        mock_service.create_proposal.assert_awaited_once()
        call_kwargs = mock_service.create_proposal.await_args.kwargs
        self.assertEqual(
            call_kwargs["task_create_request"]["payload"]["repository"],
            "MoonLadderStudios/MoonMind",
        )

    async def test_service_factory_called(self) -> None:
        mock_service = AsyncMock()
        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service
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

    async def test_origin_metadata_uses_camelcase_trigger_keys(self) -> None:
        """origin_metadata must use camelCase triggerRepo/triggerJobId per spec."""
        mock_service = AsyncMock()
        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service
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
        await activities.proposal_submit(
            {
                "candidates": candidates,
                "policy": {},
                "origin": {
                    "workflow_id": "wf-1",
                    "temporal_run_id": "run-1",
                    "trigger_repo": "org/repo",
                },
            }
        )
        call_kwargs = mock_service.create_proposal.call_args.kwargs
        meta = call_kwargs["origin_metadata"]
        self.assertIn("triggerRepo", meta)
        self.assertIn("triggerJobId", meta)
        self.assertNotIn("trigger_repo", meta)
        self.assertNotIn("trigger_job_id", meta)
        self.assertEqual(meta["triggerRepo"], "org/repo")
        self.assertEqual(meta["triggerJobId"], "run-1")

    async def test_service_failure_recorded(self) -> None:
        mock_service = AsyncMock()
        mock_service.create_proposal.side_effect = RuntimeError("DB down")
        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service
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
        @contextlib.asynccontextmanager
        async def mock_factory():
            yield mock_service
        activities = TemporalProposalActivities(
            proposal_service_factory=mock_factory,
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
        @contextlib.asynccontextmanager
        async def mock_factory():
            yield mock_service
        activities = TemporalProposalActivities(
            proposal_service_factory=mock_factory,
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
        @contextlib.asynccontextmanager
        async def mock_factory():
            yield mock_service
        activities = TemporalProposalActivities(
            proposal_service_factory=mock_factory,
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
        @contextlib.asynccontextmanager
        async def mock_factory():
            yield mock_service
        activities = TemporalProposalActivities(
            proposal_service_factory=mock_factory,
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

    async def test_managed_runtime_ids_are_normalized_before_submission(self) -> None:
        repo = AsyncMock()
        record = SimpleNamespace(
            id=uuid4(),
            status=TaskProposalStatus.OPEN,
            title="Fix bug",
            summary="Bug in module X",
            category="tests",
            tags=["tests"],
            repository="org/repo",
            proposed_by_worker_id="worker-1",
            proposed_by_user_id=None,
            promoted_at=None,
            promoted_by_user_id=None,
            decided_by_user_id=None,
            decision_note=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            origin_source=TaskProposalOriginSource.WORKFLOW,
            origin_id=None,
            origin_metadata={},
            task_create_request={},
        )
        repo.create_proposal.return_value = record
        service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))
        service._emit_notification = AsyncMock()

        @contextlib.asynccontextmanager
        async def mock_factory():
            yield service

        activities = TemporalProposalActivities(
            proposal_service_factory=mock_factory,
        )
        candidates = [
            {
                "title": "Fix bug",
                "summary": "Bug in module X",
                "taskCreateRequest": {
                    "type": "task",
                    "payload": {
                        "repository": "org/repo",
                        "targetRuntime": "codex_cli",
                        "task": {
                            "instructions": "fix it",
                            "runtime": "claude_code",
                        },
                    }
                },
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
        self.assertEqual(result["errors"], [])
        call_kwargs = repo.create_proposal.await_args.kwargs
        stamped = call_kwargs["task_create_request"]
        self.assertEqual(stamped["payload"]["targetRuntime"], "codex")
        self.assertEqual(stamped["payload"]["task"]["runtime"]["mode"], "claude")
