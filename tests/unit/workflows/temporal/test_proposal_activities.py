"""Unit tests for TemporalProposalActivities."""

from __future__ import annotations

import unittest
import contextlib
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from datetime import UTC, datetime
from types import SimpleNamespace

from moonmind.utils.logging import SecretRedactor
from moonmind.workflows.temporal.activity_runtime import (
    TemporalProposalActivities,
)
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.proposals.models import (
    WorkflowProposalOriginSource,
    WorkflowProposalStatus,
)
from moonmind.workflows.proposals.service import WorkflowProposalService
from moonmind.workflows.proposals.delivery import (
    ProposalDeliveryError,
    ProposalDeliveryService,
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
                    "workflow": {
                        "steps": [
                            {"id": "s1", "instructions": "Investigate failed proposal hooks"},
                            {"id": "s2", "instructions": "Add regression test coverage"},
                        ]
                    }
                },
            }
        )

        self.assertEqual(result, [])

    async def test_proposal_generate_creates_candidate_from_telemetry_signal(self) -> None:
        activities = TemporalProposalActivities()

        result = await activities.proposal_generate(
            {
                "workflow_id": "wf-mm-794",
                "repo": "MoonLadderStudios/MoonMind",
                "parameters": {
                    "workflow": {
                        "instructions": "Run the proposal telemetry checks",
                        "runtime": {"mode": "codex"},
                    }
                },
                "telemetrySignals": [
                    {
                        "type": "retry_exhausted",
                        "severity": "high",
                        "summary": "The run retried the same failed check twice.",
                        "retries": 2,
                        "diagnostics_ref": "artifact://diag-1",
                    }
                ],
            }
        )

        self.assertEqual(len(result), 1)
        candidate = result[0]
        self.assertEqual(
            candidate["title"],
            "[run_quality] Review retry signal from workflow wf-mm-794",
        )
        self.assertEqual(candidate["tags"], ["retry"])
        self.assertEqual(candidate["severity"], "high")
        self.assertEqual(candidate["signal"]["type"], "retry")
        self.assertEqual(candidate["signal"]["retries"], 2)
        self.assertEqual(candidate["signal"]["diagnostics_ref"], "artifact://diag-1")
        task = candidate["workflowCreateRequest"]["payload"]["workflow"]
        self.assertEqual(task["runtime"], {"mode": "codex"})
        self.assertIn(
            "The run retried the same failed check twice.",
            task["instructions"],
        )
        self.assertIn("artifact://diag-1", task["instructions"])

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
            result[0]["workflowCreateRequest"]["payload"]["workflow"]["instructions"],
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
            result[0]["workflowCreateRequest"]["payload"]["workflow"]["instructions"],
            "Add regression coverage for feature X",
        )

    async def test_proposal_generate_rejects_next_step_that_matches_workflow_title(self) -> None:
        activities = TemporalProposalActivities()

        result = await activities.proposal_generate(
            {
                "workflow_id": "test-wf-same-title",
                "parameters": {
                    "workflow": {
                        "title": "Fix proposal routing",
                        "instructions": "Fix proposal routing",
                    }
                },
                "proposalIdea": "Fix proposal routing",
            }
        )

        self.assertEqual(result, [])

    async def test_proposal_generate_preserves_compact_selectors_and_provenance(self) -> None:
        activities = TemporalProposalActivities()

        result = await activities.proposal_generate(
            {
                "workflow_id": "wf-preserve-provenance",
                "repo": "org/repo",
                "parameters": {
                    "workflow": {
                        "instructions": "Investigate flaky tests",
                        "skill": {"id": "auto", "args": {}},
                        "tool": {"type": "skill", "name": "auto", "version": "1.0"},
                        "skills": {"sets": ["runtime-quality"]},
                        "authoredPresets": [
                            {
                                "presetId": "runtime-followup",
                                "presetVersion": "2026-05-07",
                                "includePath": ["root", "quality"],
                            }
                        ],
                        "steps": [
                            {
                                "id": "step-1",
                                "title": "Inspect diagnostics",
                                "instructions": "Original diagnostic inspection",
                                "type": "tool",
                                "tool": {
                                    "type": "skill",
                                    "name": "fix-proposal",
                                    "version": "1.0",
                                },
                                "skills": {"include": [{"name": "runtime-quality"}]},
                                "source": {
                                    "kind": "preset-derived",
                                    "presetId": "runtime-followup",
                                    "includePath": ["root", "quality"],
                                    "originalStepId": "inspect-diagnostics",
                                },
                            },
                            {
                                "id": "step-2",
                                "title": "Run selected remediation skill",
                                "instructions": "Original skill remediation",
                                "type": "skill",
                                "skill": {"id": "fix-comments"},
                            },
                            {
                                "id": "step-3",
                                "title": "Run selected skill set",
                                "instructions": "Original skill-set remediation",
                                "type": "skill",
                                "skills": {"sets": ["review-fixers"]},
                                "source": {
                                    "kind": "preset-derived",
                                    "presetId": "runtime-followup",
                                },
                            },
                        ],
                    }
                },
                "proposalIdea": "Add regression coverage for proposal diagnostics",
            }
        )

        task = result[0]["workflowCreateRequest"]["payload"]["workflow"]
        self.assertEqual(task["tool"]["type"], "skill")
        self.assertEqual(task["skills"], {"sets": ["runtime-quality"]})
        self.assertEqual(
            task["authoredPresets"][0]["presetId"],
            "runtime-followup",
        )
        self.assertNotIn("steps", task)
        self.assertEqual(len(task["sourceSteps"]), 3)
        self.assertEqual(
            task["sourceSteps"][0]["source"]["originalStepId"],
            "inspect-diagnostics",
        )
        self.assertEqual(
            task["sourceSteps"][0]["skills"],
            {"include": [{"name": "runtime-quality"}]},
        )
        self.assertEqual(task["sourceSteps"][1]["skill"], {"id": "fix-comments"})
        self.assertEqual(task["sourceSteps"][2]["skills"], {"sets": ["review-fixers"]})
        for source_step in task["sourceSteps"]:
            self.assertNotIn("instructions", source_step)
        self.assertNotIn("materializedSkills", task)

    async def test_proposal_generate_does_not_fabricate_absent_provenance(self) -> None:
        activities = TemporalProposalActivities()

        result = await activities.proposal_generate(
            {
                "workflow_id": "wf-no-provenance",
                "repo": "org/repo",
                "parameters": {
                    "workflow": {
                        "instructions": "Investigate flaky tests",
                    }
                },
                "proposalIdea": "Add regression coverage for proposal diagnostics",
            }
        )

        task = result[0]["workflowCreateRequest"]["payload"]["workflow"]
        self.assertNotIn("authoredPresets", task)
        self.assertNotIn("steps", task)
        self.assertNotIn("sourceSteps", task)

    async def test_proposal_generate_does_not_touch_submission_service(self) -> None:
        factory = AsyncMock(side_effect=AssertionError("generation used service"))
        activities = TemporalProposalActivities(proposal_service_factory=factory)

        result = await activities.proposal_generate(
            {
                "workflow_id": "wf-side-effect-free",
                "repo": "org/repo",
                "parameters": {"workflow": {"instructions": "Fix proposal routing"}},
                "proposalIdea": "Add regression coverage",
            }
        )

        self.assertEqual(len(result), 1)
        factory.assert_not_called()

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
                "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
            },
            {
                "title": "Add feature",
                "summary": "Feature Y would be helpful",
                "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
            },
        ]
        result = await activities.proposal_submit(
            {"candidates": candidates, "policy": {}, "origin": {}}
        )
        self.assertEqual(result["generated_count"], 2)
        self.assertEqual(result["submitted_count"], 2)
        self.assertEqual(result["errors"], [])

    async def test_origin_requires_workflow_id_without_service(self) -> None:
        activities = TemporalProposalActivities()

        result = await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "Fix bug",
                        "summary": "There is a bug in module X",
                        "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
                    }
                ],
                "policy": {},
                "origin": {"temporal_run_id": "run-1"},
            }
        )

        self.assertEqual(result["generated_count"], 1)
        self.assertEqual(result["submitted_count"], 0)
        self.assertEqual(result["deliveredCount"], 0)
        self.assertEqual(
            result["errors"],
            ["origin.workflow_id is required for workflow proposal submission"],
        )
        self.assertEqual(
            result["validationErrors"],
            [
                {
                    "code": "proposal_validation_error",
                    "message": (
                        "origin.workflow_id is required for workflow proposal "
                        "submission"
                    ),
                }
            ],
        )

    async def test_valid_skill_tool_candidate_counted_after_contract_validation(self) -> None:
        activities = TemporalProposalActivities()
        candidates = [
            {
                "title": "Fix bug",
                "summary": "There is a bug in module X",
                "workflowCreateRequest": {
                    "type": "workflow",
                    "payload": {
                        "repository": "org/repo",
                        "workflow": {
                            "instructions": "Fix it",
                            "steps": [
                                {
                                    "type": "tool",
                                    "tool": {
                                        "type": "skill",
                                        "name": "fix-comments",
                                        "version": "1.0",
                                    },
                                }
                            ],
                        },
                    },
                },
            }
        ]

        result = await activities.proposal_submit(
            {"candidates": candidates, "policy": {}, "origin": {}}
        )

        self.assertEqual(result["generated_count"], 1)
        self.assertEqual(result["submitted_count"], 1)
        self.assertEqual(result["errors"], [])

    async def test_agent_runtime_tool_candidate_rejected_before_delivery(self) -> None:
        mock_service = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        candidates = [
            {
                "title": "Fix bug",
                "summary": "There is a bug in module X",
                "workflowCreateRequest": {
                    "type": "workflow",
                    "payload": {
                        "repository": "org/repo",
                        "workflow": {
                            "instructions": "Fix it",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "codex",
                            },
                        },
                    },
                },
            }
        ]

        result = await activities.proposal_submit(
            {"candidates": candidates, "policy": {}, "origin": {}}
        )

        self.assertEqual(result["generated_count"], 1)
        self.assertEqual(result["submitted_count"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("invalid workflowCreateRequest", result["errors"][0])
        mock_service.create_proposal.assert_not_called()

    async def test_malformed_candidates_skipped(self) -> None:
        activities = TemporalProposalActivities()
        candidates: list[Any] = [
            {"title": "", "summary": "Missing title", "workflowCreateRequest": {}},
            "not a dict",
            {
                "title": "Valid",
                "summary": "This one is valid",
                "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
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
                "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
            }
            for i in range(5)
        ]
        result = await activities.proposal_submit(
            {
                "candidates": candidates,
                "policy": {"maxItems": {"workflow_repo": 2}},
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
                "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
            },
            {
                "title": "Project 2",
                "summary": "Two",
                "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
            },
            {
                "title": "MoonMind 1",
                "summary": "Three",
                "category": "run_quality",
                "tags": ["loop_detected"],
                "severity": "medium",
                "workflowCreateRequest": {
                    "payload": {"repository": "MoonLadderStudios/MoonMind"}
                },
            },
            {
                "title": "MoonMind 2",
                "summary": "Four",
                "category": "run_quality",
                "tags": ["artifact_gap"],
                "severity": "medium",
                "workflowCreateRequest": {
                    "payload": {"repository": "MoonLadderStudios/MoonMind"}
                },
            },
        ]

        result = await activities.proposal_submit(
            {
                "candidates": candidates,
                "policy": {
                    "targets": ["workflow_repo", "moonmind"],
                    "maxItems": {"workflow_repo": 1, "moonmind": 2},
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
                "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
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
                "summary": "Proposal should remain workflow-repo-targeted for MoonMind repo runs.",
                "category": "run_quality",
                "tags": ["artifact_gap"],
                "workflowCreateRequest": {
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
            call_kwargs["workflow_create_request"]["payload"]["repository"],
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
                "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
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

    async def test_origin_metadata_uses_snake_case_trigger_keys(self) -> None:
        """origin_metadata uses snake_case trigger_repo/trigger_job_id for MM-597."""
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
                "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
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
        self.assertIn("trigger_repo", meta)
        self.assertIn("trigger_job_id", meta)
        self.assertNotIn("triggerRepo", meta)
        self.assertNotIn("triggerJobId", meta)
        self.assertEqual(meta["trigger_repo"], "org/repo")
        self.assertEqual(meta["trigger_job_id"], "run-1")

    async def test_workflow_origin_metadata_carries_canonical_source_and_id(self) -> None:
        mock_service = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "Fix bug",
                        "summary": "Bug in module X",
                        "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
                    }
                ],
                "policy": {},
                "origin": {
                    "workflow_id": "wf-1",
                    "temporal_run_id": "run-1",
                    "trigger_repo": "org/repo",
                    "trigger_job_id": "job-1",
                },
            }
        )

        call_kwargs = mock_service.create_proposal.await_args.kwargs
        self.assertEqual(call_kwargs["origin_source"], WorkflowProposalOriginSource.WORKFLOW)
        self.assertEqual(call_kwargs["origin_external_id"], "wf-1")
        self.assertEqual(call_kwargs["origin_metadata"]["source"], "workflow")
        self.assertEqual(call_kwargs["origin_metadata"]["id"], "wf-1")
        self.assertEqual(call_kwargs["origin_metadata"]["workflow_id"], "wf-1")

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
                "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
            },
        ]
        result = await activities.proposal_submit(
            {"candidates": candidates, "policy": {}, "origin": {}}
        )
        self.assertEqual(result["submitted_count"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("DB down", result["errors"][0])

    async def test_delivery_summary_preserves_failure_details_and_validation_keywords(
        self,
    ) -> None:
        mock_service = AsyncMock()
        mock_service.create_proposal.return_value = SimpleNamespace(
            external_key="MM-901",
            external_url="https://jira.example/browse/MM-901",
            provider_metadata={
                "delivery": {
                    "status": "failed",
                    "error": {
                        "code": "provider_rejected",
                        "sanitizedReason": "provider rejected delivery",
                        "retryable": False,
                    },
                }
            },
        )

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        result = await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "",
                        "summary": "Missing title",
                        "workflowCreateRequest": {},
                    },
                    {
                        "title": "Deliver proposal",
                        "summary": "Exercise failed delivery summaries",
                        "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
                    },
                ],
                "policy": {},
                "origin": {},
            }
        )

        self.assertEqual(result["deliveredCount"], 0)
        self.assertEqual(
            result["deliveryFailures"],
            [
                {
                    "provider": "github",
                    "code": "provider_rejected",
                    "sanitizedReason": "provider rejected delivery",
                    "retryable": False,
                    "message": "provider rejected delivery",
                }
            ],
        )
        self.assertIn("malformed", result["validationErrors"][0]["message"])

    async def test_blank_delivery_status_is_not_counted_as_delivered(self) -> None:
        mock_service = AsyncMock()
        mock_service.create_proposal.return_value = SimpleNamespace(
            external_key="MM-901",
            external_url="https://jira.example/browse/MM-901",
            provider_metadata={"delivery": {"status": ""}},
        )

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        result = await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "Deliver proposal",
                        "summary": "Blank status should not count as delivered",
                        "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
                    },
                ],
                "policy": {},
                "origin": {},
            }
        )

        self.assertEqual(result["submitted_count"], 1)
        self.assertEqual(result["deliveredCount"], 0)

    async def test_missing_workflow_origin_is_validation_error_before_delivery(self) -> None:
        mock_service = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        result = await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "Fix bug",
                        "summary": "Bug in module X",
                        "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
                    }
                ],
                "policy": {},
                "origin": {"temporal_run_id": "run-1"},
            }
        )

        self.assertEqual(result["submitted_count"], 0)
        self.assertEqual(result["validationErrors"][0]["code"], "proposal_validation_error")
        self.assertIn("workflow_id", result["validationErrors"][0]["message"])
        mock_service.create_proposal.assert_not_called()

    async def test_missing_workflow_repo_records_recoverable_delivery_failure(
        self,
    ) -> None:
        """MM-855/MM-853: unresolved workflow_repo routing is an auditable failure."""

        repo = AsyncMock()

        async def create_record(**kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(id=uuid4(), **kwargs)

        repo.create_proposal.side_effect = create_record
        service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

        @contextlib.asynccontextmanager
        async def factory():
            yield service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        result = await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "Record unresolved repository",
                        "summary": "Missing workflow repository should be audited.",
                        "workflowCreateRequest": {
                            "payload": {
                                "workflow": {"instructions": "Fix missing routing"}
                            }
                        },
                    }
                ],
                "policy": {"targets": ["workflow_repo"]},
                "origin": {"workflow_id": "wf-mm-855", "temporal_run_id": "run-1"},
            }
        )

        self.assertEqual(result["submitted_count"], 1)
        repo.create_proposal.assert_awaited_once()
        kwargs = repo.create_proposal.await_args.kwargs
        self.assertEqual(kwargs["repository"], "unresolved-workflow-repo")
        self.assertEqual(kwargs["resolved_policy"]["target"], "workflow_repo")
        failure = result["deliveryFailures"][0]
        self.assertEqual(failure["targetClass"], "workflow_repo")
        self.assertIn("repository is required", failure["sanitizedReason"])
        self.assertIn("recoverableNextAction", failure)

    async def test_missing_github_credentials_records_recoverable_delivery_failure(
        self,
    ) -> None:
        """MM-855/MM-853: credential authorization failures stay recoverable."""

        class MissingCredentialDelivery:
            async def deliver(self, request):
                raise ProposalDeliveryError(
                    "GitHub credentials do not cover repository",
                    provider="github",
                    destination=request.destination,
                    retryable=True,
                    next_action="Authorize GitHub delivery credentials for this repository.",
                )

        repo = AsyncMock()

        async def create_record(**kwargs: Any) -> SimpleNamespace:
            values = dict(kwargs)
            values.setdefault("external_key", None)
            values.setdefault("external_url", None)
            values.setdefault("delivered_at", None)
            values.setdefault("last_synced_at", None)
            values.setdefault("provider_metadata", {})
            values.setdefault("resolved_policy", {})
            return SimpleNamespace(id=uuid4(), **values)

        repo.create_proposal.side_effect = create_record
        service = WorkflowProposalService(
            repo,
            redactor=SecretRedactor([], "***"),
            delivery_service=MissingCredentialDelivery(),
        )
        service._emit_notification = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        result = await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "Deliver with missing credentials",
                        "summary": "Credential failures should be audited.",
                        "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
                    }
                ],
                "policy": {"targets": ["workflow_repo"]},
                "origin": {"workflow_id": "wf-mm-855", "temporal_run_id": "run-1"},
            }
        )

        self.assertEqual(result["submitted_count"], 1)
        self.assertEqual(result["deliveredCount"], 0)
        failure = result["deliveryFailures"][0]
        self.assertEqual(failure["provider"], "github")
        self.assertEqual(failure["destination"], "org/repo")
        self.assertIn("credentials", failure["sanitizedReason"])
        self.assertEqual(
            failure["recoverableNextAction"],
            "Authorize GitHub delivery credentials for this repository.",
        )

    async def test_disallowed_repository_records_delivery_failure(self) -> None:
        """MM-855/MM-853: unauthorized repositories fail closed before provider I/O."""

        class NoopGitHubProvider:
            async def search_issue(self, request):
                raise AssertionError("disallowed repository should fail before provider I/O")

            async def create_issue(self, request, rendered):
                raise AssertionError("disallowed repository should fail before provider I/O")

            async def update_issue(self, request, rendered, issue):
                raise AssertionError("disallowed repository should fail before provider I/O")

        repo = AsyncMock()

        async def create_record(**kwargs: Any) -> SimpleNamespace:
            values = dict(kwargs)
            values.setdefault("external_key", None)
            values.setdefault("external_url", None)
            values.setdefault("delivered_at", None)
            values.setdefault("last_synced_at", None)
            values.setdefault("provider_metadata", {})
            values.setdefault("resolved_policy", {})
            return SimpleNamespace(id=uuid4(), **values)

        repo.create_proposal.side_effect = create_record
        service = WorkflowProposalService(
            repo,
            redactor=SecretRedactor([], "***"),
            delivery_service=ProposalDeliveryService(github=NoopGitHubProvider()),
        )
        service._emit_notification = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        result = await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "Disallowed repository",
                        "summary": "Policy should block delivery.",
                        "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
                    }
                ],
                "policy": {
                    "targets": ["workflow_repo"],
                    "delivery": {
                        "provider": "github",
                        "github": {"allowedRepositories": ["org/allowed"]},
                    },
                },
                "origin": {"workflow_id": "wf-mm-855", "temporal_run_id": "run-1"},
            }
        )

        self.assertEqual(result["submitted_count"], 1)
        failure = result["deliveryFailures"][0]
        self.assertEqual(
            failure["sanitizedReason"],
            "GitHub repository is not allowed by proposal delivery policy",
        )
        self.assertEqual(failure["destination"], "org/repo")

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
                "workflowCreateRequest": {
                    "payload": {
                        "repository": "org/repo",
                        "workflow": {"instructions": "fix it"},
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
        stamped = call_kwargs["workflow_create_request"]
        self.assertEqual(
            stamped["payload"]["workflow"]["runtime"]["mode"], "jules"
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
                "workflowCreateRequest": {
                    "payload": {
                        "repository": "org/repo",
                        "workflow": {
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
        stamped = call_kwargs["workflow_create_request"]
        self.assertEqual(
            stamped["payload"]["workflow"]["runtime"]["mode"], "codex"
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
                "workflowCreateRequest": {
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
        stamped = call_kwargs["workflow_create_request"]
        self.assertEqual(
            stamped["payload"]["workflow"]["runtime"]["mode"], "gemini_cli"
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
                "workflowCreateRequest": {
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
        stamped = call_kwargs["workflow_create_request"]
        self.assertNotIn("task", stamped["payload"])

    async def test_managed_runtime_ids_are_normalized_before_submission(self) -> None:
        repo = AsyncMock()
        record = SimpleNamespace(
            id=uuid4(),
            status=WorkflowProposalStatus.OPEN,
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
            origin_source=WorkflowProposalOriginSource.WORKFLOW,
            origin_id=None,
            origin_metadata={},
            workflow_create_request={},
        )
        repo.create_proposal.return_value = record
        service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))
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
                "workflowCreateRequest": {
                    "type": "workflow",
                    "payload": {
                        "repository": "org/repo",
                        "targetRuntime": "codex_cli",
                        "workflow": {
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
        stamped = call_kwargs["workflow_create_request"]
        self.assertEqual(stamped["payload"]["targetRuntime"], "codex")
        self.assertEqual(stamped["payload"]["workflow"]["runtime"]["mode"], "claude")

class TestProposalSubmitPolicyResolution(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_delivery_provider_rejects_policy_before_service_call(self) -> None:
        mock_service = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        result = await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "Fix bug",
                        "summary": "Bug in module X",
                        "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
                    }
                ],
                "policy": {"delivery": {"provider": "slack"}},
                "origin": {"workflow_id": "wf-1"},
            }
        )

        self.assertEqual(result["submitted_count"], 0)
        self.assertEqual(result["generated_count"], 1)
        self.assertTrue(result["errors"])
        self.assertIn("invalid proposal policy", result["errors"][0])
        mock_service.create_proposal.assert_not_called()

    async def test_jira_delivery_policy_passes_provider_metadata_and_snake_case_origin(
        self,
    ) -> None:
        mock_service = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        result = await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "Fix bug",
                        "summary": "Bug in module X",
                        "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
                    }
                ],
                "policy": {
                    "delivery": {
                        "provider": "jira",
                        "jira": {
                            "projectKey": "MM",
                            "issueType": "Task",
                            "labels": ["moonmind"],
                        },
                    }
                },
                "origin": {
                    "workflow_id": "wf-1",
                    "temporal_run_id": "run-1",
                    "trigger_repo": "org/repo",
                    "trigger_job_id": "job-1",
                },
            }
        )

        self.assertEqual(result["submitted_count"], 1)
        call_kwargs = mock_service.create_proposal.await_args.kwargs
        self.assertEqual(call_kwargs["provider"], "jira")
        self.assertEqual(call_kwargs["origin_external_id"], "wf-1")
        self.assertEqual(
            call_kwargs["provider_metadata"],
            {
                "jira": {
                    "project_key": "MM",
                    "issue_type": "Task",
                    "labels": ["moonmind"],
                }
            },
        )
        self.assertEqual(
            call_kwargs["origin_metadata"],
            {
                "source": "workflow",
                "id": "wf-1",
                "workflow_id": "wf-1",
                "temporal_run_id": "run-1",
                "trigger_repo": "org/repo",
                "trigger_job_id": "job-1",
                "signal": {"severity": "normal", "type": "follow_up"},
            },
        )
        self.assertNotIn("triggerRepo", call_kwargs["origin_metadata"])
        self.assertIn("delivery_decisions", result)
        self.assertEqual(result["delivery_decisions"][0]["provider"], "jira")

    async def test_auto_delivery_provider_uses_configured_default(self) -> None:
        mock_service = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        with patch(
            "moonmind.workflows.temporal.activity_runtime.settings.workflow_proposals.proposal_delivery_provider_default",
            "jira",
        ):
            result = await activities.proposal_submit(
                {
                    "candidates": [
                        {
                            "title": "Fix bug",
                            "summary": "Bug in module X",
                            "workflowCreateRequest": {
                                "payload": {"repository": "org/repo"}
                            },
                        }
                    ],
                    "policy": {"delivery": {"provider": "auto"}},
                    "origin": {"workflow_id": "wf-1"},
                }
            )

        self.assertEqual(result["submitted_count"], 1)
        call_kwargs = mock_service.create_proposal.await_args.kwargs
        self.assertEqual(call_kwargs["provider"], "jira")
        self.assertEqual(result["delivery_decisions"][0]["provider"], "jira")

    async def test_moonmind_target_rewrites_repository_after_gates(self) -> None:
        mock_service = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        result = await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "Fix loop",
                        "summary": "Loop detected",
                        "category": "run_quality",
                        "tags": ["loop_detected"],
                        "severity": "high",
                        "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
                    }
                ],
                "policy": {
                    "targets": ["moonmind"],
                    "minSeverityForMoonMind": "medium",
                },
                "origin": {
                    "workflow_id": "wf-1",
                    "temporal_run_id": "run-1",
                    "trigger_repo": "org/repo",
                    "trigger_job_id": "job-1",
                },
            }
        )

        self.assertEqual(result["submitted_count"], 1)
        call_kwargs = mock_service.create_proposal.await_args.kwargs
        self.assertEqual(
            call_kwargs["workflow_create_request"]["payload"]["repository"],
            "MoonLadderStudios/MoonMind",
        )
        self.assertEqual(call_kwargs["category"], "run_quality")
        self.assertEqual(call_kwargs["tags"], ["loop_detected"])
        self.assertEqual(result["delivery_decisions"][0]["target"], "moonmind")

    async def test_submit_preserves_telemetry_signal_metadata(self) -> None:
        mock_service = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        result = await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "Fix retry loop",
                        "summary": "Retry signal was detected",
                        "category": "run_quality",
                        "tags": ["retry"],
                        "severity": "high",
                        "signal": {
                            "type": "retry",
                            "severity": "high",
                            "summary": "The run retried the same failed check twice.",
                            "retries": 2,
                            "diagnostics_ref": "artifact://diag-1",
                        },
                        "workflowCreateRequest": {
                            "payload": {"repository": "MoonLadderStudios/MoonMind"}
                        },
                    }
                ],
                "policy": {
                    "targets": ["moonmind"],
                    "minSeverityForMoonMind": "medium",
                },
                "origin": {
                    "workflow_id": "wf-mm-794",
                    "temporal_run_id": "run-mm-794",
                    "trigger_repo": "org/repo",
                    "trigger_job_id": "job-mm-794",
                },
            }
        )

        self.assertEqual(result["submitted_count"], 1)
        call_kwargs = mock_service.create_proposal.await_args.kwargs
        self.assertEqual(call_kwargs["origin_metadata"]["trigger_repo"], "org/repo")
        self.assertEqual(call_kwargs["origin_metadata"]["trigger_job_id"], "job-mm-794")
        self.assertEqual(
            call_kwargs["origin_metadata"]["signal"],
            {
                "type": "retry",
                "severity": "high",
                "summary": "The run retried the same failed check twice.",
                "retries": 2,
                "diagnostics_ref": "artifact://diag-1",
                "tags": ["retry"],
            },
        )

    async def test_project_target_preserves_candidate_repository(self) -> None:
        mock_service = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        result = await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "Project follow-up",
                        "summary": "Project-scoped cleanup",
                        "category": "tests",
                        "tags": ["tests"],
                        "severity": "high",
                        "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
                    }
                ],
                "policy": {
                    "targets": ["workflow_repo", "moonmind"],
                    "minSeverityForMoonMind": "medium",
                },
                "origin": {"workflow_id": "wf-1", "temporal_run_id": "run-1"},
            }
        )

        self.assertEqual(result["submitted_count"], 1)
        call_kwargs = mock_service.create_proposal.await_args.kwargs
        self.assertEqual(
            call_kwargs["workflow_create_request"]["payload"]["repository"],
            "org/repo",
        )
        self.assertEqual(call_kwargs["resolved_policy"]["target"], "workflow_repo")
        self.assertEqual(result["delivery_decisions"][0]["target"], "workflow_repo")

    async def test_resolved_policy_records_capacity_gates_and_runtime_decision(
        self,
    ) -> None:
        mock_service = AsyncMock()

        @contextlib.asynccontextmanager
        async def factory():
            yield mock_service

        activities = TemporalProposalActivities(proposal_service_factory=factory)
        result = await activities.proposal_submit(
            {
                "candidates": [
                    {
                        "title": "Fix loop",
                        "summary": "Loop detected",
                        "category": "run_quality",
                        "tags": ["loop_detected"],
                        "severity": "high",
                        "workflowCreateRequest": {"payload": {"repository": "org/repo"}},
                    }
                ],
                "policy": {
                    "targets": ["moonmind"],
                    "maxItems": {"moonmind": 2},
                    "minSeverityForMoonMind": "medium",
                    "defaultRuntime": "codex",
                    "delivery": {"provider": "jira", "jira": {"projectKey": "MM"}},
                },
                "origin": {"workflow_id": "wf-1", "temporal_run_id": "run-1"},
            }
        )

        self.assertEqual(result["submitted_count"], 1)
        resolved = mock_service.create_proposal.await_args.kwargs["resolved_policy"]
        self.assertEqual(resolved["provider"], "jira")
        self.assertEqual(resolved["target"], "moonmind")
        self.assertEqual(resolved["repository"], "MoonLadderStudios/MoonMind")
        self.assertEqual(resolved["default_runtime"], "codex")
        self.assertTrue(resolved["default_runtime_applied"])
        self.assertEqual(resolved["capacity"]["moonmind"]["limit"], 2)
        self.assertEqual(resolved["capacity"]["moonmind"]["accepted"], 1)
        self.assertEqual(resolved["gates"]["moonmind"]["severity_floor"], "medium")
        self.assertTrue(resolved["gates"]["moonmind"]["qualified"])
        self.assertEqual(resolved["delivery"]["provider"], "jira")


class TestProposalActivityCatalog(unittest.TestCase):
    def test_proposal_activity_retry_metadata_is_bounded(self) -> None:
        catalog = build_default_activity_catalog()

        generate = catalog.resolve_activity("proposal.generate")
        submit = catalog.resolve_activity("proposal.submit")

        self.assertEqual(generate.retries.max_attempts, 3)
        self.assertLessEqual(generate.retries.max_interval_seconds, 120)
        self.assertEqual(submit.retries.max_attempts, 3)
        self.assertLessEqual(submit.retries.max_interval_seconds, 60)
