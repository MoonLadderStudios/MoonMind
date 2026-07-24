"""Unit tests for Temporal execution lifecycle API endpoints."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import AsyncMock, Mock, call, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.service import RPCError, RPCStatusCode

from api_service.api.routers.executions import (
    _get_service,
    get_temporal_client_adapter,
    _artifact_id_from_ref,
    _build_original_workflow_input_snapshot_payload,
    _build_recurring_target,
    _checkpoint_failed_step_execution,
    _execution_recurrence_provenance,
    _expand_goal_preset_for_workflow_submission,
    _extract_cost_estimate_usd,
    _effective_user_roles,
    _hydrate_related_run_metadata,
    _merge_workflow_preserving_artifact_instructions,
    _canonical_recovery_manifest_ref,
    _recovery_evidence_disabled_reason,
    _recovery_manifest_ref_from_record,
    _recovery_manifest_summary_allows_resume,
    _recovery_not_available_reason,
    _reject_recovery_manifest_mismatch,
    _reuse_original_task_input_snapshot_from_source,
    _workflow_input_snapshot_descriptor_from_record,
    _normalize_task_steps,
    _normalize_task_tool,
    _resolve_step_runtime_selections,
    _step_execution_detail_payload,
    _detect_optional_temporal_search_attributes,
    _derive_task_title,
    _OPTIONAL_TEMPORAL_SEARCH_ATTRIBUTES_CACHE_TTL_SECONDS,
    _optional_temporal_search_attributes_cache,
    get_temporal_client,
    _serialize_execution,
    _verified_output_branch,
    router,
    update_execution as update_execution_route,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalExecutionCloseStatus,
    TemporalArtifact,
    TemporalArtifactEncryption,
    TemporalArtifactRedactionLevel,
    TemporalArtifactLink,
    TemporalArtifactRetentionClass,
    TemporalArtifactStorageBackend,
    TemporalArtifactStatus,
    TemporalArtifactUploadMode,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionRecord,
    TemporalWorkflowType,
)
from api_service.services.recurring_workflows_service import RecurringWorkflowValidationError
from moonmind.config.settings import settings
from moonmind.workflows.temporal.publication_recovery import (
    publication_operation_key,
    publication_recovery_workflow_id,
)
from moonmind.workflows.temporal.client import WorkflowStartResult
from moonmind.workflows.temporal.service import ExecutionDependencySummary
from moonmind.workflows.temporal import (
    TemporalExecutionNotFoundError,
    TemporalExecutionValidationError,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
from moonmind.schemas.temporal_models import (
    ExecutionMergeAutomationResolverChildModel,
    ExecutionProgressModel,
    FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE,
    RecoverFromFailedStepRequest,
    RecoverFromSelectedStepRequest,
    StepExecutionDetailModel,
    StepExecutionManifestModel,
    StepLedgerSnapshotModel,
    UpdateExecutionRequest,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.services.control_stop_continuation import (
    ControlStopContinuationReservation,
)
from moonmind.workflows.executions.control_stop_continuation import (
    ContinuationBudgetGrant,
)

_TARGET_SEARCH_ATTRIBUTE_TYPE = int(IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST)


class _ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _ExecuteResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarRows:
        return _ScalarRows(self._rows)

    def scalar_one_or_none(self) -> object | None:
        return self._rows[0] if self._rows else None


def _phase_11_manifest(**overrides: Any) -> StepExecutionManifestModel:
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
    payload: dict[str, Any] = {
        "workflowId": "wf-phase-11",
        "runId": "run-phase-11",
        "logicalStepId": "implement",
        "executionOrdinal": 2,
        "stepExecutionId": "wf-phase-11:run-phase-11:implement:execution:2",
        "reason": "recover_from_failed_step",
        "status": "failed",
        "terminalDisposition": "retryable",
        "startedAt": now,
        "updatedAt": now,
        "context": {
            "contextBundleRef": "artifact://context/bundle",
            "retrievalManifestRef": "artifact://retrieval/manifest",
            "memoryManifestRef": "artifact://memory/manifest",
        },
        "workspace": {
            "checkpointBeforeRef": "artifact://checkpoint/before",
            "stateCheckpointRef": "artifact://checkpoint/state",
            "providerLeaseDiagnosticsRef": "artifact://diagnostics/provider-lease",
        },
        "checks": [
            {
                "kind": "implementation",
                "status": "passed",
                "verdict": "FULLY_IMPLEMENTED",
                "artifactRef": "artifact://checks/verify",
                "summary": "Gate passed.",
            }
        ],
        "sideEffects": {
            "summaryRef": "artifact://side-effects/summary",
            "status": "available",
            "summary": "Publish skipped.",
        },
        "outputs": {"summary": "Step failed after producing ref-only evidence."},
        "lineage": {
            "sourceWorkflowId": "wf-source",
            "sourceRunId": "run-source",
            "sourceLogicalStepId": "implement",
            "sourceExecutionOrdinal": 1,
            "lineageExecutionOrdinal": 2,
        },
    }
    payload.update(overrides)
    return StepExecutionManifestModel.model_validate(payload)


def _artifact_session(rows: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(execute=AsyncMock(return_value=_ExecuteResult(rows)))


class _SnapshotReuseSession:
    def __init__(
        self,
        *,
        canonical: TemporalExecutionCanonicalRecord | None = None,
        existing_link: object | None = None,
    ) -> None:
        self._canonical = canonical
        self._existing_link = existing_link
        self.added: list[object] = []
        self.get = AsyncMock(return_value=canonical)

    async def execute(self, _statement: object) -> _ExecuteResult:
        rows = [self._existing_link] if self._existing_link is not None else []
        return _ExecuteResult(rows)

    def add(self, value: object) -> None:
        self.added.append(value)

def _completed_attachment_artifact(
    artifact_id: str,
    *,
    content_type: str = "image/png",
    size_bytes: int = 10,
    created_by_principal: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        artifact_id=artifact_id,
        status=TemporalArtifactStatus.COMPLETE,
        content_type=content_type,
        size_bytes=size_bytes,
        created_by_principal=created_by_principal,
    )


def test_verified_output_branch_projects_terminal_checkpoint_evidence() -> None:
    result = _verified_output_branch({
        "publish": {"status": "failed"},
        "publishContext": {
            "terminalPublication": {
                "intent": "terminal_checkpoint",
                "status": "pushed",
                "branchName": "mm/run/workflow/cp-123/terminal-recovered-work",
                "headSha": "abc123",
                "baseBranch": "main",
                "remoteVerified": True,
            }
        },
    })
    assert result == {
        "name": "mm/run/workflow/cp-123/terminal-recovered-work",
        "headSha": "abc123",
        "baseBranch": "main",
        "intent": "terminal_checkpoint",
        "status": "pushed",
    }


def test_verified_output_branch_rejects_unverified_claim() -> None:
    assert _verified_output_branch({
        "publishContext": {
            "terminalPublication": {
                "status": "pushed",
                "branchName": "mm/unverified",
                "remoteVerified": False,
            }
        }
    }) is None


def test_verified_output_branch_coerces_unknown_intent() -> None:
    result = _verified_output_branch({
        "publishContext": {
            "terminalPublication": {
                "intent": "future_intent",
                "status": "pushed",
                "branchName": "mm/recovered",
                "remoteVerified": True,
            }
        }
    })

    assert result is not None
    assert result["intent"] == "normal"


def test_mm_1129_derive_task_title_synthesizes_issue_title_from_instructions() -> None:
    title = _derive_task_title(
        {
            "instructions": (
                "Implement this Jira issue:\n"
                "MM-1129 titles should include description text while title "
                "length remains available."
            )
        }
    )

    assert (
        title
        == "Implement this Jira issue: MM-1129 titles should include description "
        "text while title length remains available."
    )


def test_mm_1129_derive_task_title_bounds_instruction_fallback() -> None:
    title = _derive_task_title(
        {
            "instructions": (
                "Implement this Jira issue "
                + " ".join(f"token {index}" for index in range(1000))
            )
        }
    )

    assert title is not None
    assert len(title) == 150
    assert "token-999" not in title


def test_derive_task_title_enriches_generated_preset_label() -> None:
    title = _derive_task_title(
        {
            "title": "GitHub Issue Implement",
            "taskTemplate": {"slug": "github-issue-implement"},
            "inputs": {
                "github_issue": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "number": 3143,
                    "title": "Improve generated workflow titles",
                }
            },
        }
    )

    assert title == (
        "GitHub Issue Implement: MoonLadderStudios/MoonMind#3143 — "
        "Improve generated workflow titles"
    )


def test_derive_task_title_preserves_explicit_instruction_only_title() -> None:
    title = _derive_task_title(
        {
            "title": "My custom title",
            "instructions": "Do the work",
        }
    )

    assert title == "My custom title"


def test_step_execution_detail_payload_exposes_phase_11_ref_only_evidence_summary() -> None:
    payload = _step_execution_detail_payload(
        _phase_11_manifest(),
        manifest_artifact_ref="artifact://manifest/implement-2",
    )

    evidence = payload["stepEvidence"]

    assert evidence["logicalStepId"] == "implement"
    assert evidence["checkpointRefsByBoundary"]["before_execution"] == {
        "category": "checkpoint",
        "status": "available",
        "artifactRef": "artifact://checkpoint/before",
        "boundary": "before_execution",
        "reasonCode": None,
        "label": "Before execution checkpoint",
        "summary": None,
    }
    assert evidence["contextBundleRef"]["artifactRef"] == "artifact://context/bundle"
    assert evidence["retrievalManifestRef"]["status"] == "available"
    assert evidence["memoryManifestRef"]["artifactRef"] == "artifact://memory/manifest"
    assert evidence["gateSummary"]["verdict"] == "FULLY_IMPLEMENTED"
    assert evidence["terminalDisposition"] == "retryable"
    assert evidence["sideEffectSummary"]["artifactRefs"] == {
        "summaryRef": "artifact://side-effects/summary"
    }
    assert evidence["diagnosticRefs"][0]["kind"] == "provider_lease"

    rendered = json.dumps(payload, default=str)
    for forbidden in (
        "diff --git",
        "raw stdout",
        "raw stderr",
        "providerPayload",
        "token=secret",
        "raw verification report",
        "checkpoint archive content",
    ):
        assert forbidden not in rendered


def test_step_execution_detail_payload_exposes_typed_recovery_eligibility() -> None:
    eligible = _step_execution_detail_payload(
        _phase_11_manifest(),
        manifest_artifact_ref="artifact://manifest/implement-2",
    )["recoveryEligibility"]

    assert eligible["eligible"] is False
    assert eligible["defaultAction"] == "full_retry"
    assert eligible["checkpointRef"] == "artifact://checkpoint/before"
    assert eligible["checkpointBoundary"] == "before_execution"
    assert eligible["operatorGuidance"] == "full_retry"
    assert eligible["disabledReasonCode"] == "CHECKPOINT_CAPABILITY_SNAPSHOT_MISSING"

    ineligible = _step_execution_detail_payload(
        _phase_11_manifest(workspace={"stateCheckpointRef": "artifact://checkpoint/state"}),
        manifest_artifact_ref="artifact://manifest/implement-2",
    )["recoveryEligibility"]

    assert ineligible["eligible"] is False
    assert ineligible["defaultAction"] == "full_retry"
    assert ineligible["disabledReasonCode"] == "CHECKPOINT_ARTIFACT_INVALID"
    assert ineligible["evidence"][0]["status"] == "missing"


def test_step_execution_detail_payload_tolerates_nullable_manifest_sections() -> None:
    payload = _step_execution_detail_payload(
        _phase_11_manifest(
            workspace=None,
            execution=None,
            outputs=None,
            sideEffects=None,
            checks=None,
        ),
        manifest_artifact_ref="artifact://manifest/implement-2",
    )

    assert payload["stepEvidence"]["checkpointRefsByBoundary"] == {}
    assert payload["stepEvidence"]["sideEffectSummary"] is None
    assert payload["stepEvidence"]["diagnosticRefs"] == []
    assert payload["recoveryEligibility"]["eligible"] is False
    assert (
        payload["recoveryEligibility"]["disabledReasonCode"]
        == "CHECKPOINT_ARTIFACT_INVALID"
    )


def test_step_execution_detail_payload_reads_preserved_steps_from_recovery_source() -> None:
    payload = _step_execution_detail_payload(
        _phase_11_manifest(
            recoverySource={
                "sourceWorkflowId": "wf-source",
                "sourceRunId": "run-source",
                "preservedSteps": [
                    {
                        "logicalStepId": "plan",
                        "title": "Plan",
                        "sourceExecutionOrdinal": 1,
                        "stateCheckpointRef": "artifact://checkpoint/plan-state",
                        "outputRefs": {"summaryRef": "artifact://output/plan-summary"},
                    }
                ],
            },
        ),
        manifest_artifact_ref="artifact://manifest/implement-2",
    )

    preserved = payload["preservedStepProvenance"]
    detail = StepExecutionDetailModel.model_validate(payload)

    assert preserved == [
        {
            "logicalStepId": "plan",
            "title": "Plan",
            "sourceWorkflowId": "wf-source",
            "sourceRunId": "run-source",
            "sourceExecutionOrdinal": 1,
            "stateCheckpointRef": "artifact://checkpoint/plan-state",
            "outputRefs": {"summaryRef": "artifact://output/plan-summary"},
        }
    ]
    assert detail.preserved_step_provenance[0].logical_step_id == "plan"
    assert detail.preserved_step_provenance[0].source_workflow_id == "wf-source"


def test_step_execution_detail_payload_exposes_environment_fix_guidance() -> None:
    payload = _step_execution_detail_payload(
        _phase_11_manifest(
            status="blocked",
            terminalDisposition="blocked",
            workspace={
                "providerLeaseDiagnosticsRef": "artifact://diagnostics/provider-lease",
                "sidecarDiagnosticsRef": "artifact://diagnostics/sidecar",
                "ghcrDiagnosticsRef": "artifact://diagnostics/ghcr",
                "preflightDiagnosticsRef": "artifact://diagnostics/preflight",
            },
            outputs={"summary": "Environment setup failed."},
        ),
        manifest_artifact_ref="artifact://manifest/implement-2",
    )

    recovery = payload["recoveryEligibility"]
    diagnostic_kinds = {
        item["kind"] for item in payload["stepEvidence"]["diagnosticRefs"]
    }

    assert recovery["eligible"] is False
    assert recovery["defaultAction"] == "fix_environment"
    assert recovery["disabledReasonCode"] == "environment_invalid"
    assert recovery["operatorGuidance"] == "fix_environment"
    assert diagnostic_kinds == {"provider_lease", "sidecar", "ghcr", "preflight"}
    assert "source-code" not in json.dumps(payload, default=str).lower()


def test_mm842_task_steps_accept_ordered_steps_without_graph_metadata() -> None:
    task_payload = {
        "steps": [
            {"id": "plan", "instructions": "Plan the work."},
            {"id": "implement", "instructions": "Implement the work."},
        ]
    }

    steps = _normalize_task_steps(task_payload)

    assert [step["id"] for step in steps] == ["plan", "implement"]
    assert all("dependsOn" not in step for step in steps)
    assert all("depends_on" not in step for step in steps)
    assert all("dependencies" not in step for step in steps)


def test_task_skill_normalization_preserves_input_contract_metadata() -> None:
    task_payload = {
        "steps": [
            {
                "id": "validate",
                "skill": {
                    "id": "schema.skill",
                    "inputs": {"repository": "MoonLadderStudios/MoonMind"},
                    "inputSchema": {
                        "type": "object",
                        "properties": {"repository": {"type": "string"}},
                    },
                    "uiSchema": {
                        "repository": {"widget": "github.repository-picker"},
                    },
                    "defaults": {"branch": "main"},
                    "inputContractDigest": "sha256:contract",
                    "contentDigest": "sha256:content",
                    "contentRef": "artifact:skill-contract",
                },
            }
        ]
    }

    steps = _normalize_task_steps(task_payload)

    skill = steps[0]["skill"]
    assert skill["inputs"] == {"repository": "MoonLadderStudios/MoonMind"}
    assert skill["inputSchema"]["properties"]["repository"]["type"] == "string"
    assert skill["uiSchema"]["repository"]["widget"] == "github.repository-picker"
    assert skill["defaults"] == {"branch": "main"}
    assert skill["inputContractDigest"] == "sha256:contract"
    assert skill["contentDigest"] == "sha256:content"
    assert skill["contentRef"] == "artifact:skill-contract"


def test_task_tool_normalization_preserves_skill_input_contract_metadata() -> None:
    normalized = _normalize_task_tool(
        {
            "skill": {
                "id": "schema.skill",
                "inputs": {"issue": "MM-1057"},
                "inputSchema": {
                    "type": "object",
                    "properties": {"issue": {"type": "string"}},
                },
                "uiSchema": {"issue": {"widget": "jira.issue-picker"}},
                "defaults": {"repository": "MoonLadderStudios/MoonMind"},
                "inputContractDigest": "sha256:contract",
                "contentDigest": "sha256:content",
                "contentRef": "artifact:skill-contract",
            },
        }
    )

    assert normalized == {
        "type": "skill",
        "name": "schema.skill",
        "inputs": {"issue": "MM-1057"},
        "inputSchema": {
            "type": "object",
            "properties": {"issue": {"type": "string"}},
        },
        "uiSchema": {"issue": {"widget": "jira.issue-picker"}},
        "defaults": {"repository": "MoonLadderStudios/MoonMind"},
        "inputContractDigest": "sha256:contract",
        "contentDigest": "sha256:content",
        "contentRef": "artifact:skill-contract",
    }


@pytest.mark.parametrize("field_name", ["dependsOn", "depends_on", "dependencies"])
def test_mm842_task_steps_reject_non_empty_step_graph_metadata(field_name: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _normalize_task_steps(
            {
                "steps": [
                    {"id": "plan", "instructions": "Plan."},
                    {
                        "id": "implement",
                        "instructions": "Implement.",
                        field_name: ["plan"],
                    },
                ]
            }
        )

    detail = exc_info.value.detail
    assert detail["code"] == "invalid_execution_request"
    assert f"payload.workflow.steps[1].{field_name}" in detail["message"]
    assert "ordered by their steps[] position" in detail["message"]


@pytest.mark.parametrize("field_name", ["dependsOn", "depends_on", "dependencies"])
def test_mm842_task_steps_strip_empty_step_graph_metadata(field_name: str) -> None:
    task_payload = {
        "steps": [
            {
                "id": "plan",
                "instructions": "Plan.",
                field_name: [],
            }
        ]
    }

    steps = _normalize_task_steps(task_payload)

    assert field_name not in steps[0]
    assert "dependsOn" not in steps[0]
    assert "depends_on" not in steps[0]
    assert "dependencies" not in steps[0]


def test_mm842_task_steps_reject_non_empty_active_edges() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _normalize_task_steps(
            {
                "steps": [{"id": "plan", "instructions": "Plan."}],
                "edges": [{"from": "plan", "to": "implement"}],
            }
        )

    detail = exc_info.value.detail
    assert detail["code"] == "invalid_execution_request"
    assert "payload.workflow.edges" in detail["message"]
    assert "ordered by their steps[] position" in detail["message"]


def test_mm842_task_steps_strip_empty_active_edges() -> None:
    task_payload = {
        "steps": [{"id": "plan", "instructions": "Plan."}],
        "edges": [],
    }

    _normalize_task_steps(task_payload)

    assert "edges" not in task_payload


@pytest.mark.asyncio
async def test_task_step_runtime_selection_is_normalized_and_resolved() -> None:
    steps = _normalize_task_steps(
        {
            "steps": [
                {
                    "id": "review",
                    "instructions": "Review with a step model.",
                    "runtime": {
                        "mode": "claude_code",
                        "model": "gemini-step-model",
                        "effort": "low",
                    },
                }
            ]
        }
    )

    await _resolve_step_runtime_selections(
        steps=steps,
        task_runtime={"mode": "codex_cli", "effort": "high"},
        task_target_runtime="codex_cli",
        task_profile_id=None,
        session=None,
    )

    assert steps[0]["runtime"] == {
        "mode": "claude_code",
        "model": "gemini-step-model",
        "effort": "low",
        "requestedModel": "gemini-step-model",
        "modelSource": "task_override",
    }


def test_task_step_runtime_preserves_omnigent_selection() -> None:
    steps = _normalize_task_steps(
        {
            "steps": [
                {
                    "id": "implement",
                    "runtime": {
                        "mode": "omnigent",
                        "omnigent": {
                            "executionTargetRef": "omnigent-codex@1",
                            "launchPolicyRef": "codex-on-demand@1",
                        },
                    },
                }
            ]
        }
    )

    assert steps[0]["runtime"]["omnigent"] == {
        "executionTargetRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@1",
    }


@pytest.mark.asyncio
async def test_step_runtime_inherits_task_profile_default_model() -> None:
    steps = _normalize_task_steps(
        {
            "steps": [
                {
                    "id": "implement",
                    "instructions": "Implement using inherited profile defaults.",
                    "runtime": {"effort": "low"},
                }
            ]
        }
    )
    session = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(default_model="codex-profile-default")
        )
    )

    await _resolve_step_runtime_selections(
        steps=steps,
        task_runtime={"mode": "codex_cli", "effort": "high"},
        task_target_runtime="codex_cli",
        task_profile_id="profile-codex",
        session=session,
    )

    assert steps[0]["runtime"] == {
        "effort": "low",
        "mode": "codex_cli",
        "model": "codex-profile-default",
        "modelSource": "provider_profile_default",
        "inheritedProfileId": "profile-codex",
    }


@pytest.mark.asyncio
async def test_step_runtime_normalizes_explicit_profile_without_session() -> None:
    steps = _normalize_task_steps(
        {
            "steps": [
                {
                    "id": "review",
                    "instructions": "Review with a profile ref in a unit test.",
                    "runtime": {
                        "mode": "claude_code",
                        "profileId": "profile-gemini",
                    },
                }
            ]
        }
    )

    await _resolve_step_runtime_selections(
        steps=steps,
        task_runtime=None,
        task_target_runtime="codex_cli",
        task_profile_id=None,
        session=None,
    )

    assert steps[0]["runtime"]["mode"] == "claude_code"
    assert steps[0]["runtime"]["profileId"] == "profile-gemini"
    assert steps[0]["runtime"]["providerProfile"] == "profile-gemini"


def _mm639_authored_task_payload() -> dict[str, Any]:
    return {
        "title": "MM-639 durable snapshot",
        "instructions": "Preserve the original authored task input for MM-639.",
        "inputAttachments": [
            {
                "artifactId": "art-objective",
                "filename": "objective.png",
                "contentType": "image/png",
                "sizeBytes": 123,
            }
        ],
        "runtime": {
            "mode": "codex_cli",
            "model": "gpt-5.4",
            "effort": "medium",
            "profileId": "profile-codex",
        },
        "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}},
        "git": {
            "repository": "MoonLadderStudios/MoonMind",
            "branch": "feature/mm-639",
        },
        "dependencies": ["MM-638"],
        "appliedStepTemplates": [
            {
                "slug": "jira-orchestrate",
                "version": "1",
                "inputs": {"issueKey": "MM-639"},
                "stepIds": ["step-1", "step-2"],
                "composition": {
                    "slug": "jira-orchestrate",
                    "includes": [
                        {"slug": "jira-fetch", "version": "1"},
                    ],
                },
            }
        ],
        "authoredPresets": [
            {
                "presetSlug": "jira-orchestrate",
                "presetDigest": "digest-1",
                "inputBindings": {"issueKey": "MM-639"},
            }
        ],
        "steps": [
            {
                "id": "step-1",
                "title": "Fetch issue",
                "instructions": "Fetch Jira issue MM-639.",
                "dependsOn": [],
                "templateStepId": "tpl:jira-orchestrate:fetch",
                "presetProvenance": {
                    "presetSlug": "jira-orchestrate",
                    "presetDigest": "digest-1",
                },
            },
            {
                "id": "step-2",
                "title": "Implement",
                "instructions": "Implement MM-639.",
                "dependsOn": ["step-1"],
                "inputAttachments": [
                    {
                        "artifactId": "art-step",
                        "filename": "step.png",
                        "contentType": "image/png",
                        "sizeBytes": 456,
                    }
                ],
                "presetProvenance": {
                    "presetSlug": "jira-orchestrate",
                    "presetDigest": "digest-1",
                    "sourceStepId": "tpl:jira-orchestrate:implement",
                },
                "detachedFromPreset": True,
            },
        ],
    }


@pytest.mark.asyncio
async def test_goal_preset_submission_expands_before_planner(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path}/goal_preset_submission.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            task_payload = {
                "title": "MM-747 goal",
                "goal": "Complete Jira issue MM-747 using preset scheduling.",
            }
            await _expand_goal_preset_for_workflow_submission(
                task_payload=task_payload,
                request_payload={
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                },
                session=session,
                user=SimpleNamespace(id=uuid4(), is_superuser=False),
            )
    finally:
        await engine.dispose()

    assert task_payload["taskTemplate"] == {
        "slug": "jira-implement",
        "scope": "global",
        "presetDigest": task_payload["appliedStepTemplates"][0]["presetDigest"],
    }
    assert task_payload["inputs"]["jira_issue_key"] == "MM-747"
    assert task_payload["presetSchedule"]["presetSlug"] == "jira-implement"
    assert task_payload["steps"][0]["title"] == "Load Jira preset brief"
    assert task_payload["steps"][1]["title"] == "Assess existing implementation state"
    assert task_payload["appliedStepTemplates"][0]["slug"] == "jira-implement"


@pytest.mark.asyncio
async def test_explicit_github_issue_template_expands_before_planner(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path}/explicit_github_preset.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            task_payload = {
                "title": "Implement GitHub issue",
                "instructions": (
                    "Implement GitHub issue MoonLadderStudios/MoonMind#3143."
                ),
                "taskTemplate": {
                    "slug": "github-issue-implement",
                    "scope": "global",
                },
                "inputs": {
                    "github_issue": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "number": 3143,
                    },
                    "run_verify": True,
                },
            }
            await _expand_goal_preset_for_workflow_submission(
                task_payload=task_payload,
                request_payload={
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                },
                session=session,
                user=SimpleNamespace(id=uuid4(), is_superuser=False),
            )
    finally:
        await engine.dispose()

    assert len(task_payload["steps"]) == 9
    assert task_payload["steps"][0]["tool"]["id"] == (
        "github.load_issue_preset_brief"
    )
    assert task_payload["steps"][-1]["tool"]["id"] == (
        "github.update_issue_status"
    )
    assert task_payload["appliedStepTemplates"][0]["slug"] == (
        "github-issue-implement"
    )
    assert "Closes MoonLadderStudios/MoonMind#3143" in task_payload["steps"][-2][
        "instructions"
    ]


@pytest.mark.asyncio
async def test_explicit_template_rejects_another_users_personal_scope() -> None:
    owner_id = uuid4()
    caller_id = uuid4()
    task_payload = {
        "taskTemplate": {
            "slug": "private-preset",
            "scope": "personal",
            "scopeRef": str(owner_id),
        }
    }

    with pytest.raises(HTTPException) as exc_info:
        await _expand_goal_preset_for_workflow_submission(
            task_payload=task_payload,
            request_payload={},
            session=object(),
            user=SimpleNamespace(id=caller_id, is_superuser=False),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "template_scope_forbidden"


@pytest.mark.asyncio
async def test_explicit_template_maps_missing_preset_to_not_found(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path}/missing_explicit_preset.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await _expand_goal_preset_for_workflow_submission(
                    task_payload={
                        "taskTemplate": {
                            "slug": "missing-explicit-preset",
                            "scope": "global",
                        }
                    },
                    request_payload={},
                    session=session,
                    user=SimpleNamespace(id=uuid4(), is_superuser=False),
                )
            with pytest.raises(HTTPException) as validation_exc_info:
                await _expand_goal_preset_for_workflow_submission(
                    task_payload={
                        "taskTemplate": {
                            "slug": "github-issue-implement",
                            "scope": "global",
                        },
                        "inputs": {},
                    },
                    request_payload={},
                    session=session,
                    user=SimpleNamespace(id=uuid4(), is_superuser=False),
                )
    finally:
        await engine.dispose()

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "template_not_found"
    assert validation_exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_explicit_template_preserves_branch_and_checkpoint_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakePresetCatalogService:
        def __init__(self, session: Any) -> None:
            self.session = session

        async def expand_template(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {
                "steps": [{"id": "step-1", "title": "Run", "instructions": "Run"}],
                "appliedTemplate": {
                    "slug": kwargs["slug"],
                    "inputs": kwargs["inputs"],
                    "stepIds": ["step-1"],
                    "appliedAt": "2026-07-12T00:00:00+00:00",
                },
                "checkpointBranching": {"enabled": True},
            }

    monkeypatch.setattr(
        "api_service.services.presets.catalog.PresetCatalogService",
        FakePresetCatalogService,
    )
    task_payload = {
        "taskTemplate": {"slug": "branch-aware", "scope": "global"},
        "git": {"branch": "release/next"},
        "checkpointBranching": {"enabled": False},
    }

    await _expand_goal_preset_for_workflow_submission(
        task_payload=task_payload,
        request_payload={"repository": "MoonLadderStudios/MoonMind"},
        session=object(),
        user=SimpleNamespace(id=uuid4(), is_superuser=False),
    )

    assert captured["context"]["branch"] == "release/next"
    assert task_payload["checkpointBranching"] == {"enabled": False}


@pytest.mark.asyncio
async def test_goal_preset_submission_uses_default_runtime_for_composite_context(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.workflow, "default_runtime", "claude_code")
    db_url = f"sqlite+aiosqlite:///{tmp_path}/goal_preset_default_runtime.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            task_payload = {
                "title": "Break down feature",
                "goal": "Split docs/Design.md into Jira stories.",
            }
            await _expand_goal_preset_for_workflow_submission(
                task_payload=task_payload,
                request_payload={"repository": "MoonLadderStudios/MoonMind"},
                session=session,
                user=SimpleNamespace(id=uuid4(), is_superuser=False),
            )
    finally:
        await engine.dispose()

    downstream_task = task_payload["steps"][3]["jiraOrchestration"]["task"]
    assert task_payload["presetSchedule"]["presetSlug"] == "jira-breakdown-orchestrate"
    assert downstream_task["repository"] == "MoonLadderStudios/MoonMind"
    assert downstream_task["runtime"] == {"mode": "claude_code"}

@pytest.mark.asyncio
async def test_goal_preset_submission_carries_expanded_checkpoint_branching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.workflows.executions.preset_goal_scheduler import GoalPresetSchedule

    class FakePresetCatalogService:
        def __init__(self, session: Any) -> None:
            self.session = session

        async def expand_template(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "steps": [{"id": "step-1", "title": "Run", "instructions": "Run"}],
                "appliedTemplate": {
                    "slug": kwargs["slug"],
                    "inputs": kwargs["inputs"],
                    "stepIds": ["step-1"],
                    "appliedAt": "2026-07-04T00:00:00+00:00",
                },
                "capabilities": [],
                "checkpointBranching": {
                    "enabled": True,
                    "triggers": ["failed_step"],
                    "maxBranchesPerCheckpoint": 2,
                    "maxTurnsPerBranch": 3,
                    "promotionPolicy": "approval_gated",
                    "defaultWorkspacePolicy": (
                        "apply_previous_execution_diff_to_clean_baseline"
                    ),
                    "runtimeContextPolicy": "fresh_agent_run",
                    "publishMode": "none",
                    "sideEffectPolicy": "isolated",
                    "branchTemplates": [
                        {
                            "label": "minimal_fix",
                            "instructionsRef": "art_template_minimal_fix",
                        }
                    ],
                },
            }

        async def sync_seed_templates(self, seed_dir: Path) -> None:
            raise AssertionError("seed sync should not be needed")

    monkeypatch.setattr(
        "api_service.api.routers.executions.schedule_preset_from_goal",
        lambda goal: GoalPresetSchedule(
            goal=goal,
            slug="checkpoint-enabled",
            inputs={},
            reason="test",
            issue_key=None,
        ),
    )
    monkeypatch.setattr(
        "api_service.services.presets.catalog.PresetCatalogService",
        FakePresetCatalogService,
    )

    task_payload = {"goal": "Run checkpoint-enabled preset"}
    await _expand_goal_preset_for_workflow_submission(
        task_payload=task_payload,
        request_payload={"repository": "MoonLadderStudios/MoonMind"},
        session=object(),
        user=SimpleNamespace(id=uuid4(), is_superuser=False),
    )

    assert task_payload["checkpointBranching"]["enabled"] is True
    assert task_payload["checkpointBranching"]["triggers"] == ["failed_step"]


class _QueryHandle:
    def __init__(
        self,
        *,
        progress=None,
        ledger=None,
        summary=None,
        error: Exception | None = None,
        delay_seconds: float = 0,
    ) -> None:
        self._progress = progress
        self._ledger = ledger
        self._summary = summary
        self._error = error
        self._delay_seconds = delay_seconds

    async def query(self, name: str):
        if self._delay_seconds > 0:
            await asyncio.sleep(self._delay_seconds)
        if self._error is not None:
            raise self._error
        if name == "get_progress":
            return self._progress
        if name == "get_step_ledger":
            return self._ledger
        if name == "summary":
            return self._summary
        raise AssertionError(f"Unexpected query name: {name}")

def _override_query_client(
    app: FastAPI,
    *,
    progress=None,
    ledger=None,
    summary=None,
    error: Exception | None = None,
    delay_seconds: float = 0,
) -> SimpleNamespace:
    handles: dict[str, _QueryHandle] = {}

    def get_workflow_handle(workflow_id: str) -> _QueryHandle:
        if workflow_id not in handles:
            handles[workflow_id] = _QueryHandle(
                progress=progress,
                ledger=ledger,
                summary=summary,
                error=error,
                delay_seconds=delay_seconds,
            )
        return handles[workflow_id]

    client = SimpleNamespace(get_workflow_handle=Mock(side_effect=get_workflow_handle))
    app.dependency_overrides[get_temporal_client] = lambda: client
    return client

def _override_user_dependencies(
    app: FastAPI,
    *,
    is_superuser: bool,
    roles: list[str] | None = None,
) -> SimpleNamespace:
    mock_user = SimpleNamespace(
        id=uuid4(),
        email="executions@example.com",
        is_active=True,
        is_superuser=is_superuser,
        roles=list(roles or []),
    )
    user_dependencies = {
        dep.call
        for route in router.routes
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if dep.call.__name__ == "_current_user_fallback"
    }
    if not user_dependencies:
        user_dependencies = {get_current_user()}

    def _current_user() -> SimpleNamespace:
        return mock_user

    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = _current_user
    return mock_user

def _empty_session_override() -> SimpleNamespace:
    return SimpleNamespace()

def _build_execution_record(
    *,
    workflow_type: TemporalWorkflowType = TemporalWorkflowType.USER_WORKFLOW,
    state: MoonMindWorkflowState = MoonMindWorkflowState.EXECUTING,
    owner_id: str = "user-123",
    has_workflow_input_snapshot: bool = True,
) -> SimpleNamespace:
    now = datetime.now(UTC)
    entry = (
        "manifest" if workflow_type is TemporalWorkflowType.MANIFEST_INGEST else "run"
    )
    return SimpleNamespace(
        namespace="moonmind",
        workflow_id="mm:wf-1",
        run_id="run-2",
        workflow_type=workflow_type,
        state=state,
        close_status=None,
        search_attributes={
            "mm_owner_id": owner_id,
            "mm_owner_type": "user" if owner_id != "system" else "system",
            "mm_entry": entry,
            "mm_repo": "Moon/Mind",
            "mm_continue_as_new_cause": "manual_rerun",
        },
        memo={
            "title": "Temporal task",
            "summary": "Waiting on review.",
            "continue_as_new_cause": "manual_rerun",
            "latest_temporal_run_id": "run-2",
            **(
                {
                    "task_input_snapshot_ref": "art_snapshot_1",
                    "task_input_snapshot_version": 1,
                    "task_input_snapshot_source_kind": "create",
                }
                if workflow_type is TemporalWorkflowType.USER_WORKFLOW
                and has_workflow_input_snapshot
                else {}
            ),
        },
        artifact_refs=["art_123"],
        finish_outcome_code=None,
        finish_summary_json=None,
        manifest_ref=(
            "art_manifest_1"
            if workflow_type is TemporalWorkflowType.MANIFEST_INGEST
            else None
        ),
        plan_ref=(
            "art_plan_1"
            if workflow_type is TemporalWorkflowType.MANIFEST_INGEST
            else None
        ),
        parameters=(
            {
                "requestedBy": {"type": "user", "id": "user-1"},
                "executionPolicy": {
                    "failurePolicy": "best_effort",
                    "maxConcurrency": 3,
                },
                "manifestNodes": [
                    {"nodeId": "node-a", "state": "ready"},
                    {"nodeId": "node-b", "state": "running"},
                ],
            }
            if workflow_type is TemporalWorkflowType.MANIFEST_INGEST
            else {}
        ),
        paused=False,
        waiting_reason=None,
        attention_required=False,
        created_at=now,
        started_at=now,
        updated_at=now,
        closed_at=None,
        owner_id=owner_id,
        owner_type="user" if owner_id != "system" else "system",
        entry=entry,
        integration_state=None,
    )


def _failed_run_manifest_payload() -> dict[str, Any]:
    return {
        "schemaVersion": "v1",
        "contentType": FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE,
        "workflowId": "mm:wf-1",
        "runId": "run-2",
        "failedLogicalStepId": "step-2",
        "failedExecutionOrdinal": 2,
        "validation": {
            "result": "valid",
            "checkpointRef": "artifact://checkpoint/1",
            "boundary": "before_recovery_restoration",
        },
        "resumeAllowed": True,
        "recoveryEligibility": {
            "eligible": True,
            "defaultAction": "resume_from_checkpoint",
            "checkpointRef": "artifact://checkpoint/1",
            "operatorGuidance": "resume",
        },
        "createdAt": datetime.now(UTC),
    }


def test_recovery_manifest_ref_reads_finish_summary_manifest_ref() -> None:
    record = _build_execution_record()
    record.finish_summary_json = {
        "recoveryManifest": {
            "manifestRef": "artifact://recovery/manifest-1",
        }
    }

    assert _recovery_manifest_ref_from_record(record) == "artifact://recovery/manifest-1"


def _valid_recovery_manifest_summary(*, include_manifest_ref: bool = True) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schemaVersion": "v1",
        "resumeAllowed": True,
        "validationResult": "valid",
        "failedLogicalStepId": "implement",
        "checkpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
    }
    if include_manifest_ref:
        manifest["manifestRef"] = "artifact://recovery/manifest"
    return manifest


def test_recovery_manifest_summary_requires_manifest_ref() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.memo = {**record.memo, "plan_artifact_ref": {"artifact_id": "artifact://plan/source"}}

    record.finish_summary_json = {"recoveryManifest": _valid_recovery_manifest_summary()}
    assert _recovery_manifest_summary_allows_resume(record) is True

    record.finish_summary_json = {
        "recoveryManifest": _valid_recovery_manifest_summary(include_manifest_ref=False)
    }
    assert _recovery_manifest_summary_allows_resume(record) is False


def test_recovery_evidence_disabled_when_manifest_ref_missing() -> None:
    """A compact summary that lacks a manifest ref must not advertise resume.

    Without the manifest ref the POST resume path would immediately 409 with
    ``recovery_manifest_missing``, so the summary fast-path must stop enabling
    resume and the record must fall back to the full-evidence checks (which it
    cannot satisfy here), leaving resume disabled.
    """
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.memo = {**record.memo, "plan_artifact_ref": {"artifact_id": "artifact://plan/source"}}

    record.finish_summary_json = {"recoveryManifest": _valid_recovery_manifest_summary()}
    assert _recovery_evidence_disabled_reason(record) is None

    record.finish_summary_json = {
        "recoveryManifest": _valid_recovery_manifest_summary(include_manifest_ref=False)
    }
    assert _recovery_evidence_disabled_reason(record) is not None


def test_canonical_recovery_manifest_ref_rejects_request_override() -> None:
    record = _build_execution_record()
    record.finish_summary_json = {
        "recoveryManifest": {
            "manifestRef": "artifact://recovery/emitted",
        }
    }
    request = RecoverFromFailedStepRequest(
        idempotencyKey="recover-1",
        recoveryCheckpointRef="artifact://checkpoint/1",
        failedRunRecoveryManifestRef="artifact://recovery/request",
    )

    with pytest.raises(HTTPException) as exc:
        _canonical_recovery_manifest_ref(request, record)

    assert exc.value.status_code == 409
    assert exc.value.detail["reason"] == "recovery_manifest_inconsistent"
    assert exc.value.detail["fields"] == ["failedRunRecoveryManifestRef"]


def test_reject_recovery_manifest_mismatch_accepts_selected_step_request() -> None:
    request = RecoverFromSelectedStepRequest(
        idempotencyKey="recover-1",
        sourceWorkflowId="mm:wf-1",
        sourceRunId="run-2",
        selectedStartStepId="step-1",
        recoveryCheckpointRef="artifact://checkpoint/1",
    )

    _reject_recovery_manifest_mismatch(
        request,
        canonical=_build_execution_record(),
        manifest_payload=_failed_run_manifest_payload(),
        checkpoint_ref="artifact://checkpoint/1",
    )


def test_serialize_execution_includes_finish_summary_projection_fields():
    record = _build_execution_record(state=MoonMindWorkflowState.COMPLETED)
    record.close_status = TemporalExecutionCloseStatus.COMPLETED
    record.finish_outcome_code = "NO_CHANGES"
    record.finish_summary_json = {
        "schemaVersion": "v1",
        "finishOutcome": {
            "code": "NO_CHANGES",
            "stage": "publish",
            "reason": "publish skipped: no local changes",
        },
        "publish": {
            "status": "skipped",
            "reasonCode": "no_changes",
            "reason": "no local changes",
        },
    }

    payload = _serialize_execution(record).model_dump(by_alias=True, mode="json")

    assert payload["finishOutcomeCode"] == "NO_COMMIT"
    assert payload["finishSummary"]["finishOutcome"]["code"] == "NO_COMMIT"
    assert (
        payload["finishSummary"]["finishOutcome"]["reason"]
        == "No repository commit was needed."
    )
    assert payload["finishSummary"]["publish"]["reasonCode"] == "no_commit"
    assert (
        payload["finishSummary"]["publish"]["reason"]
        == "No repository changes were available to commit or publish."
    )

def test_serialize_execution_maps_no_commit_to_completed_dashboard_status():
    record = _build_execution_record(state=MoonMindWorkflowState.NO_COMMIT)
    record.close_status = TemporalExecutionCloseStatus.COMPLETED

    payload = _serialize_execution(record).model_dump(by_alias=True, mode="json")

    assert payload["state"] == "no_commit"
    assert payload["dashboardStatus"] == "completed"
    assert payload["status"] == "completed"
    assert payload["temporalStatus"] == "completed"
    assert payload["closeStatus"] == "completed"

def test_serialize_execution_includes_bounded_progress_without_step_details() -> None:
    record = _build_execution_record()
    record.memo = {
        **record.memo,
        "progress": {
            "total": 6,
            "pending": 2,
            "ready": 0,
            "executing": 1,
            "awaitingExternal": 0,
            "reviewing": 0,
            "completed": 3,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Run test suite",
            "updatedAt": "2026-04-04T18:11:15Z",
            "steps": [{"title": "too much detail"}],
            "logs": "must not leak",
            "artifacts": ["artifact-ref"],
            "stdout": "raw stdout",
            "stderr": "raw stderr",
            "diagnostics": {"providerPayload": "raw"},
        },
    }

    payload = _serialize_execution(record).model_dump(by_alias=True, mode="json")

    assert payload["progress"] == {
        "total": 6,
        "pending": 2,
        "ready": 0,
        "executing": 1,
        "awaitingExternal": 0,
        "reviewing": 0,
        "completed": 3,
        "failed": 0,
        "skipped": 0,
        "canceled": 0,
        "currentStepTitle": "Run test suite",
        "updatedAt": "2026-04-04T18:11:15Z",
    }

def test_serialize_execution_ignores_non_mapping_memo_for_progress() -> None:
    record = _build_execution_record()
    record.memo = None

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["progress"] is None


def test_serialize_execution_nulls_progress_for_legacy_rows() -> None:
    payload = _serialize_execution(_build_execution_record()).model_dump(by_alias=True)

    assert payload["progress"] is None


def test_serialize_execution_normalizes_legacy_progress_keys() -> None:
    record = _build_execution_record()
    record.memo = {
        **record.memo,
        "progress": {
            "total": 3,
            "pending": 1,
            "ready": 0,
            "running": 1,
            "awaitingExternal": 0,
            "reviewing": 0,
            "succeeded": 1,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Run tests",
        },
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["progress"]["executing"] == 1
    assert payload["progress"]["completed"] == 1
    assert payload["progress"]["currentStepTitle"] == "Run tests"

def _override_temporal_client(app: FastAPI) -> AsyncMock:
    client = AsyncMock()
    app.dependency_overrides[get_temporal_client] = lambda: client
    return client

@pytest.fixture
def client() -> Iterator[tuple[TestClient, AsyncMock, SimpleNamespace]]:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    user = _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        yield test_client, service, user

    app.dependency_overrides.clear()

def _client_with_service() -> Iterator[tuple[TestClient, AsyncMock]]:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        yield test_client, mock_service

    app.dependency_overrides.clear()

def test_list_executions_passes_temporal_filters_for_admin() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.list_executions.return_value = SimpleNamespace(
        items=[],
        next_page_token=None,
        count=0,
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)

    owner_id = uuid4()
    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "workflowType": "MoonMind.UserWorkflow",
                "state": "executing",
                "entry": "run",
                "ownerType": "user",
                "ownerId": str(owner_id),
                "repo": "Moon/Mind",
                "integration": "github",
                "pageSize": 25,
                "nextPageToken": "token-123",
            },
        )

    assert response.status_code == 200
    kwargs = mock_service.list_executions.await_args.kwargs
    assert kwargs["workflow_type"] == "MoonMind.UserWorkflow"
    assert kwargs["state"] == "executing"
    assert kwargs["entry"] == "run"
    assert kwargs["owner_type"] == "user"
    assert kwargs["owner_id"] == str(owner_id)
    assert kwargs["repo"] == "Moon/Mind"
    assert kwargs["integration"] == "github"
    assert kwargs["page_size"] == 25
    assert kwargs["next_page_token"] == "token-123"


def test_create_task_shaped_execution_keeps_integration_as_metadata(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "integration": "jira",
                "targetRuntime": "codex_cli",
                "task": {
                    "title": "Run Jira Implement for MM-770",
                    "instructions": "Complete Jira issue MM-770.",
                    "steps": [
                        {
                            "id": "step-1",
                            "title": "Implement",
                            "instructions": "Implement MM-770.",
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    kwargs = service.create_execution.await_args.kwargs
    assert kwargs["integration"] == "jira"
    assert "integration" not in kwargs["initial_parameters"]


def test_list_executions_temporal_query_includes_target_runtime_filter() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_target_runtime": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "targetRuntime": "codex_cli",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert 'WorkflowType="MoonMind.UserWorkflow"' in query
    assert 'mm_entry="user_workflow"' in query
    assert 'mm_target_runtime="codex_cli"' in query
    temporal_client.list_workflows.assert_called_once()
    assert (
        temporal_client.list_workflows.call_args.kwargs["query"]
        == temporal_client.count_workflows.await_args.kwargs["query"]
    )

def test_list_executions_source_temporal_degrades_count_failure() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service

    class _EmptyCanonicalResult:
        def scalars(self) -> "_EmptyCanonicalResult":
            return self

        def all(self) -> list[TemporalExecutionCanonicalRecord]:
            return []

    class _Session:
        async def execute(self, _stmt: object) -> _EmptyCanonicalResult:
            return _EmptyCanonicalResult()

    app.dependency_overrides[get_async_session] = lambda: _Session()
    _override_user_dependencies(app, is_superuser=True)

    async def _memo():
        return {"title": "Count degraded"}

    workflow = SimpleNamespace(
        id="mm:wf-count-degraded",
        run_id="run-temporal",
        namespace="default",
        workflow_type="MoonMind.UserWorkflow",
        status="RUNNING",
        start_time=datetime(2026, 4, 4, 18, 0, tzinfo=UTC),
        close_time=None,
        execution_time=None,
        search_attributes={
            "mm_state": "executing",
            "mm_owner_id": "system",
            "mm_owner_type": "system",
            "mm_entry": "run",
        },
        memo=_memo,
    )

    class _WorkflowIterator:
        current_page = [workflow]
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(custom_attributes={})
            )
        ),
        count_workflows=AsyncMock(side_effect=RuntimeError("count unavailable")),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={"source": "temporal", "ownerType": "system"},
        )

    assert response.status_code == 200
    body = response.json()
    assert [item["workflowId"] for item in body["items"]] == ["mm:wf-count-degraded"]
    assert body["count"] is None
    assert body["countMode"] == "estimated_or_unknown"
    assert body["degradedCount"] is True
    temporal_client.list_workflows.assert_called_once()
    temporal_client.count_workflows.assert_awaited_once()

def test_list_executions_temporal_query_includes_canonical_state_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_target_runtime": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                        "mm_target_skill": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "stateIn": "completed,failed",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert 'WorkflowType="MoonMind.UserWorkflow"' in query
    assert 'mm_entry="user_workflow"' in query
    # ``completed`` and ``failed`` are terminal states; the executions list
    # filter resolves them to the Temporal ``ExecutionStatus`` so closed
    # workflows whose ``mm_state`` search attribute was never updated still
    # match the user's selection.
    assert 'ExecutionStatus="Completed"' in query
    assert 'ExecutionStatus="Failed"' in query
    assert 'ExecutionStatus="Terminated"' in query
    assert 'ExecutionStatus="TimedOut"' in query
    assert 'mm_state="completed"' not in query
    assert 'mm_state="failed"' not in query


def test_list_executions_temporal_query_anchors_non_terminal_state_to_running_status() -> None:
    """Selecting ``AWAITING DEP`` must not match closed workflows whose
    ``mm_state`` search attribute was left at ``waiting_on_dependencies``
    when they were canceled or failed.
    """

    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_target_runtime": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "stateIn": "waiting_on_dependencies",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert 'mm_state="waiting_on_dependencies"' in query
    assert 'ExecutionStatus="Running"' in query
    assert 'ExecutionStatus="Failed"' not in query
    assert 'ExecutionStatus="Canceled"' not in query


def test_list_executions_temporal_query_mixes_terminal_and_non_terminal_state_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_target_runtime": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "stateIn": "waiting_on_dependencies,canceled",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert 'mm_state="waiting_on_dependencies"' in query
    assert 'ExecutionStatus="Running"' in query
    assert 'ExecutionStatus="Canceled"' in query


def test_list_executions_temporal_query_supports_repeated_canonical_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_target_runtime": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                        "mm_target_skill": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params=[
                ("source", "temporal"),
                ("targetRuntimeIn", "codex_cli"),
                ("targetRuntimeIn", "claude_code"),
                ("targetRuntimeIn", ""),
                ("repoIn", "Moon/Mind,moon/sidecar"),
                ("repoIn", "Moon/Mind"),
            ],
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert '(mm_target_runtime="codex_cli" OR mm_target_runtime="claude_code")' in query
    assert '(mm_repo="Moon/Mind" OR mm_repo="moon/sidecar")' in query


def test_list_executions_temporal_query_includes_legacy_no_commit_visibility_alias() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(custom_attributes={})
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={"source": "temporal", "state": "no_commit"},
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert '(mm_state="no_commit" OR mm_state="no_changes")' in query


def test_list_executions_temporal_query_excludes_legacy_no_commit_visibility_alias() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(custom_attributes={})
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={"source": "temporal", "stateNotIn": "no_commit"},
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert 'mm_state!="no_commit"' in query
    assert 'mm_state!="no_changes"' in query


def test_list_executions_temporal_query_ignores_empty_canonical_state_for_legacy_state() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_target_runtime": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                        "mm_target_skill": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params=[
                ("source", "temporal"),
                ("state", "completed"),
                ("stateIn", ""),
            ],
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert 'ExecutionStatus="Completed"' in query
    assert 'mm_state="completed"' not in query

def test_list_executions_temporal_query_rejects_contradictory_canonical_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(),
        list_workflows=Mock(),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "stateIn": "completed",
                "stateNotIn": "canceled",
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_execution_query",
        "message": "Cannot combine stateIn and stateNotIn.",
    }
    temporal_client.count_workflows.assert_not_called()

def test_list_executions_temporal_query_includes_canonical_runtime_skill_and_repo_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_target_runtime": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                        "mm_target_skill": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "targetRuntimeIn": "codex_cli,claude_code",
                "targetSkillIn": "moonspec-implement",
                "repoIn": "Moon/Mind",
                "repoExact": "owner/repo",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert '(mm_target_runtime="codex_cli" OR mm_target_runtime="claude_code")' in query
    assert 'mm_target_skill="moonspec-implement"' in query
    assert 'mm_repo="owner/repo"' in query
    assert 'mm_repo="Moon/Mind"' not in query

def test_list_executions_temporal_runtime_skill_filters_degrade_when_unregistered() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(custom_attributes={})
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "targetRuntime": "codex_cli",
                "targetSkillIn": "moonspec-implement",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["count"] is None
    assert body["countMode"] == "estimated_or_unknown"
    assert body["degradedCount"] is True
    temporal_client.count_workflows.assert_not_called()
    temporal_client.list_workflows.assert_not_called()

@pytest.mark.asyncio
async def test_optional_temporal_search_attribute_detection_rejects_wrong_type() -> None:
    _optional_temporal_search_attributes_cache.clear()
    temporal_client = SimpleNamespace(
        namespace="default",
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_target_runtime": SimpleNamespace(type=1),
                        "mm_target_skill": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
    )

    usable = await _detect_optional_temporal_search_attributes(temporal_client)

    assert "mm_target_runtime" not in usable
    assert "mm_target_skill" in usable


@pytest.mark.asyncio
async def test_optional_temporal_search_attribute_detection_uses_fresh_cache() -> None:
    _optional_temporal_search_attributes_cache.clear()
    operator_service = SimpleNamespace(
        list_search_attributes=AsyncMock(
            return_value=SimpleNamespace(
                custom_attributes={
                    "mm_target_runtime": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    "mm_target_skill": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                }
            )
        )
    )
    temporal_client = SimpleNamespace(
        namespace="default",
        operator_service=operator_service,
    )

    first = await _detect_optional_temporal_search_attributes(temporal_client)
    second = await _detect_optional_temporal_search_attributes(temporal_client)

    assert first == frozenset({"mm_target_runtime", "mm_target_skill"})
    assert second == first
    operator_service.list_search_attributes.assert_awaited_once()


@pytest.mark.asyncio
async def test_optional_temporal_search_attribute_detection_falls_back_to_stale_cache() -> None:
    _optional_temporal_search_attributes_cache.clear()
    operator_service = SimpleNamespace(
        list_search_attributes=AsyncMock(side_effect=RuntimeError("temporal unavailable"))
    )
    temporal_client = SimpleNamespace(
        namespace="default",
        operator_service=operator_service,
    )
    cache_key = ("default", id(operator_service))
    loop_time = asyncio.get_running_loop().time()
    cached = frozenset({"mm_target_runtime"})
    _optional_temporal_search_attributes_cache[cache_key] = (
        loop_time - _OPTIONAL_TEMPORAL_SEARCH_ATTRIBUTES_CACHE_TTL_SECONDS - 1.0,
        cached,
    )

    usable = await _detect_optional_temporal_search_attributes(temporal_client)

    assert usable == cached
    operator_service.list_search_attributes.assert_awaited_once()

def test_list_executions_temporal_query_prefers_canonical_filters_over_legacy_exact_params() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_target_runtime": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                        "mm_target_skill": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "state": "executing",
                "stateIn": "completed",
                "repo": "legacy/repo",
                "repoExact": "owner/repo",
                "targetRuntime": "codex_cli",
                "targetRuntimeIn": "claude_code",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert (
        '(ExecutionStatus="Completed")' in query
        or 'ExecutionStatus="Completed"' in query
    )
    assert 'mm_state="executing"' not in query
    assert 'mm_repo="owner/repo"' in query
    assert 'mm_repo="legacy/repo"' not in query
    assert 'mm_target_runtime="codex_cli"' in query
    assert 'mm_target_runtime="claude_code"' not in query

def test_list_executions_temporal_query_includes_canonical_date_bounds() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_title": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scheduledFrom": "2026-05-01",
                "scheduledTo": "2026-05-05",
                "updatedFrom": "2026-05-04",
                "updatedTo": "2026-05-08",
                "createdFrom": "2026-05-02",
                "createdTo": "2026-05-06",
                "finishedFrom": "2026-05-03",
                "finishedTo": "2026-05-07",
                "scheduledBlank": "exclude",
                "finishedBlank": "exclude",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert "mm_scheduled_for IS NOT NULL" in query
    assert 'mm_scheduled_for>="2026-05-01T00:00:00Z"' in query
    assert 'mm_scheduled_for<="2026-05-05T23:59:59.999999Z"' in query
    assert (
        '(CloseTime IS NOT NULL AND CloseTime>="2026-05-04T00:00:00Z" '
        'AND CloseTime<="2026-05-08T23:59:59.999999Z")'
    ) in query
    assert (
        '(CloseTime IS NULL AND mm_scheduled_for IS NOT NULL '
        'AND mm_scheduled_for>="2026-05-04T00:00:00Z" '
        'AND mm_scheduled_for<="2026-05-08T23:59:59.999999Z")'
    ) in query
    assert (
        '(CloseTime IS NULL AND mm_scheduled_for IS NULL '
        'AND StartTime>="2026-05-04T00:00:00Z" '
        'AND StartTime<="2026-05-08T23:59:59.999999Z")'
    ) in query
    assert 'StartTime>="2026-05-02T00:00:00Z"' in query
    assert 'StartTime<="2026-05-06T23:59:59.999999Z"' in query
    assert "CloseTime IS NOT NULL" in query
    assert 'CloseTime>="2026-05-03T00:00:00Z"' in query
    assert 'CloseTime<="2026-05-07T23:59:59.999999Z"' in query

def test_list_executions_temporal_query_includes_blank_date_filter_semantics() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_title": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scheduledFrom": "2026-05-01",
                "scheduledBlank": "include",
                "finishedBlank": "include",
            },
        )

    assert response.status_code == 200
    query = temporal_client.count_workflows.await_args.kwargs["query"]
    assert '(mm_scheduled_for IS NULL OR (mm_scheduled_for>="2026-05-01T00:00:00Z"))' in query
    assert "CloseTime IS NULL" in query

def test_list_executions_temporal_query_supports_sort_and_text_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_title": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "repoContains": "Moon",
                "workflowIdContains": "wf-",
                "titleContains": "release",
                "sort": "createdAt",
                "sortDir": "asc",
            },
        )

    assert response.status_code == 200
    count_query = temporal_client.count_workflows.await_args.kwargs["query"]
    list_query = temporal_client.list_workflows.call_args.kwargs["query"]
    assert 'mm_repo STARTS_WITH "Moon"' in count_query
    assert 'WorkflowId STARTS_WITH "wf-"' in count_query
    assert 'mm_title = "release"' in count_query
    assert "ORDER BY" not in count_query
    assert list_query.endswith("ORDER BY StartTime ASC")


def test_list_executions_temporal_query_omits_order_by_for_keyword_list_target_sort() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_target_runtime": SimpleNamespace(
                            type=_TARGET_SEARCH_ATTRIBUTE_TYPE
                        ),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "targetRuntime": "codex_cli",
                "sort": "targetRuntime",
                "sortDir": "asc",
            },
        )

    assert response.status_code == 200
    list_query = temporal_client.list_workflows.call_args.kwargs["query"]
    assert 'mm_target_runtime="codex_cli"' in list_query
    assert "ORDER BY" not in list_query


def test_list_executions_temporal_query_title_filter_ands_word_tokens() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_title": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={"source": "temporal", "titleContains": "Post-Merge Jira"},
        )

    assert response.status_code == 200
    count_query = temporal_client.count_workflows.await_args.kwargs["query"]
    # The free-text title is tokenized and ANDed so every typed word must be a
    # member of the mm_title KeywordList.
    assert 'mm_title = "post"' in count_query
    assert 'mm_title = "merge"' in count_query
    assert 'mm_title = "jira"' in count_query
    assert "LIKE" not in count_query


def test_list_executions_temporal_query_rejects_title_filter_without_tokens() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)
    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_title": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(),
        list_workflows=Mock(),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={"source": "temporal", "titleContains": "!!!"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_query"
    assert "titleContains must contain at least one alphanumeric word token" in response.json()["detail"]["message"]
    temporal_client.count_workflows.assert_not_called()


def test_list_executions_temporal_query_uses_workflow_id_prefix_filter() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)

    class _WorkflowIterator:
        current_page: list[object] = []
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "workflowIdContains": "wf-",
                "sort": "workflowId",
            },
        )

    assert response.status_code == 200
    count_query = temporal_client.count_workflows.await_args.kwargs["query"]
    list_query = temporal_client.list_workflows.call_args.kwargs["query"]
    assert 'WorkflowId STARTS_WITH "wf-"' in count_query
    assert list_query.endswith("ORDER BY WorkflowId DESC")


def test_execution_sort_fields_do_not_expose_task_id_alias() -> None:
    from api_service.api.routers import executions as executions_module

    assert "workflowId" in executions_module._EXECUTION_SORT_FIELDS
    assert "taskId" not in executions_module._EXECUTION_SORT_FIELDS

def test_list_executions_temporal_query_rejects_invalid_filter_bounds() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)
    temporal_client = SimpleNamespace(count_workflows=AsyncMock(), list_workflows=Mock())
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        invalid_blank = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "scheduledBlank": "maybe",
            },
        )
        invalid_range = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "createdFrom": "2026-05-06",
                "createdTo": "2026-05-01",
            },
        )
        invalid_updated_range = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "updatedFrom": "2026-05-06",
                "updatedTo": "2026-05-01",
            },
        )
        invalid_sort = test_client.get(
            "/api/executions",
            params={"source": "temporal", "sort": "workflowType"},
        )

    assert invalid_blank.status_code == 422
    assert invalid_blank.json()["detail"]["code"] == "invalid_execution_query"
    assert "include, exclude" in invalid_blank.json()["detail"]["message"]
    assert invalid_range.status_code == 422
    assert "createdFrom must be before or equal to createdTo" in invalid_range.json()["detail"]["message"]
    assert invalid_updated_range.status_code == 422
    assert "updatedFrom must be before or equal to updatedTo" in invalid_updated_range.json()["detail"]["message"]
    assert invalid_sort.status_code == 422
    assert "sort must be one of" in invalid_sort.json()["detail"]["message"]
    temporal_client.count_workflows.assert_not_called()


def test_list_executions_temporal_query_validates_progress_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=True)
    temporal_client = SimpleNamespace(count_workflows=AsyncMock(), list_workflows=Mock())
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        contradictory_bucket = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "progressBucketIn": "in_progress",
                "progressBucketNotIn": "complete",
            },
        )
        invalid_percent = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "progressPctFrom": "75",
                "progressPctTo": "25",
            },
        )
        invalid_signal = test_client.get(
            "/api/executions",
            params={"source": "temporal", "progressSignalIn": "awaitingExternal"},
        )

    assert contradictory_bucket.status_code == 422
    assert contradictory_bucket.json()["detail"]["message"] == (
        "Cannot combine progressBucketIn and progressBucketNotIn."
    )
    assert invalid_percent.status_code == 422
    assert (
        "progressPctFrom must be before or equal to progressPctTo"
        in invalid_percent.json()["detail"]["message"]
    )
    assert invalid_signal.status_code == 422
    assert (
        "progressSignalIn must use supported values"
        in invalid_signal.json()["detail"]["message"]
    )
    temporal_client.count_workflows.assert_not_called()


def test_list_executions_source_temporal_filters_and_sorts_progress_from_bounded_summary() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service

    class _EmptyCanonicalResult:
        def scalars(self) -> "_EmptyCanonicalResult":
            return self

        def all(self) -> list[TemporalExecutionCanonicalRecord]:
            return []

    class _Session:
        async def execute(self, _stmt: object) -> _EmptyCanonicalResult:
            return _EmptyCanonicalResult()

    app.dependency_overrides[get_async_session] = lambda: _Session()
    _override_user_dependencies(app, is_superuser=True)

    def _workflow(
        workflow_id: str,
        title: str,
        progress: dict[str, object] | None,
    ) -> SimpleNamespace:
        async def _memo() -> dict[str, object]:
            return {
                "title": title,
                "summary": title,
                **({"progress": progress} if progress is not None else {}),
            }

        return SimpleNamespace(
            id=workflow_id,
            run_id=f"run-{workflow_id}",
            namespace="default",
            workflow_type="MoonMind.UserWorkflow",
            status="COMPLETED",
            start_time=datetime(2026, 4, 4, 18, 0, tzinfo=UTC),
            close_time=datetime(2026, 4, 4, 18, 5, tzinfo=UTC),
            execution_time=None,
            search_attributes={
                "mm_state": "completed",
                "mm_owner_id": "system",
                "mm_owner_type": "system",
                "mm_entry": "run",
            },
            memo=_memo,
        )

    high = _workflow(
        "wf-high",
        "High progress",
        {
            "total": 4,
            "pending": 0,
            "ready": 0,
            "completed": 3,
            "failed": 1,
            "executing": 0,
            "awaitingExternal": 0,
            "reviewing": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Run tests",
            "updatedAt": "2026-04-04T18:11:15Z",
        },
    )
    low = _workflow(
        "wf-low",
        "Low progress",
        {
            "total": 4,
            "pending": 0,
            "ready": 0,
            "completed": 1,
            "failed": 0,
            "executing": 1,
            "awaitingExternal": 0,
            "reviewing": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Implement",
            "updatedAt": "2026-04-04T18:09:15Z",
        },
    )
    blank = _workflow("wf-blank", "Blank progress", None)

    class _WorkflowIterator:
        current_page = [low, blank, high]
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    progress_by_workflow_id = {
        "wf-high": {
            "total": 4,
            "pending": 0,
            "ready": 0,
            "completed": 3,
            "failed": 1,
            "executing": 0,
            "awaitingExternal": 0,
            "reviewing": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Run tests",
            "updatedAt": "2026-04-04T18:11:15Z",
        },
        "wf-low": {
            "total": 4,
            "pending": 0,
            "ready": 0,
            "completed": 1,
            "failed": 0,
            "executing": 1,
            "awaitingExternal": 0,
            "reviewing": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Implement",
            "updatedAt": "2026-04-04T18:09:15Z",
        },
        "wf-blank": None,
    }

    def _workflow_handle(workflow_id: str) -> _QueryHandle:
        return _QueryHandle(progress=progress_by_workflow_id[workflow_id])

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=3)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
        get_workflow_handle=Mock(side_effect=_workflow_handle),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "ownerType": "system",
                "progressPctFrom": "25",
                "progressPctTo": "100",
                "progressSignalIn": "has_failed_steps",
                "progressStepTitleContains": "tests",
                "sort": "progressPct",
                "sortDir": "desc",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert [item["workflowId"] for item in body["items"]] == ["wf-high"]
    count_query = temporal_client.count_workflows.await_args.kwargs["query"]
    list_query = temporal_client.list_workflows.call_args.kwargs["query"]
    assert "progress" not in count_query
    assert "ORDER BY" not in list_query


def test_list_executions_source_temporal_hydrates_live_progress_by_default() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service

    class _EmptyCanonicalResult:
        def scalars(self) -> "_EmptyCanonicalResult":
            return self

        def all(self) -> list[TemporalExecutionCanonicalRecord]:
            return []

    class _Session:
        async def execute(self, _stmt: object) -> _EmptyCanonicalResult:
            return _EmptyCanonicalResult()

    app.dependency_overrides[get_async_session] = lambda: _Session()
    _override_user_dependencies(app, is_superuser=True)

    async def _memo():
        return {
            "title": "Live workflow",
            "summary": "Running tests.",
        }

    workflow = SimpleNamespace(
        id="mm:wf-live",
        run_id="run-temporal",
        namespace="default",
        workflow_type="MoonMind.UserWorkflow",
        status="RUNNING",
        start_time=datetime(2026, 4, 4, 18, 0, tzinfo=UTC),
        close_time=None,
        execution_time=None,
        search_attributes={
            "mm_state": "executing",
            "mm_owner_id": "system",
            "mm_owner_type": "system",
            "mm_entry": "run",
        },
        memo=_memo,
    )

    class _WorkflowIterator:
        current_page = [workflow]
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = _override_query_client(
        app,
        progress={
            "total": 3,
            "pending": 0,
            "ready": 0,
            "executing": 1,
            "awaitingExternal": 0,
            "reviewing": 0,
            "completed": 2,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Run tests",
            "updatedAt": "2026-04-04T18:11:15Z",
            "runId": "run-live",
        },
    )
    temporal_client.count_workflows = AsyncMock(return_value=SimpleNamespace(count=1))
    temporal_client.list_workflows = Mock(return_value=_WorkflowIterator())

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={"source": "temporal", "ownerType": "system"},
        )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["workflowId"] == "mm:wf-live"
    assert item["runId"] == "run-live"
    assert item["progress"]["currentStepTitle"] == "Run tests"
    assert item["progress"]["completed"] == 2
    assert item["progress"]["total"] == 3
    temporal_client.get_workflow_handle.assert_called_once_with("mm:wf-live")


def test_list_executions_source_temporal_hydrates_live_progress_concurrently() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service

    class _EmptyCanonicalResult:
        def scalars(self) -> "_EmptyCanonicalResult":
            return self

        def all(self) -> list[TemporalExecutionCanonicalRecord]:
            return []

    class _Session:
        async def execute(self, _stmt: object) -> _EmptyCanonicalResult:
            return _EmptyCanonicalResult()

    app.dependency_overrides[get_async_session] = lambda: _Session()
    _override_user_dependencies(app, is_superuser=True)

    async def _memo():
        return {
            "title": "Live workflow",
            "summary": "Running tests.",
        }

    def _workflow(workflow_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=workflow_id,
            run_id=f"run-{workflow_id}",
            namespace="default",
            workflow_type="MoonMind.UserWorkflow",
            status="RUNNING",
            start_time=datetime(2026, 4, 4, 18, 0, tzinfo=UTC),
            close_time=None,
            execution_time=None,
            search_attributes={
                "mm_state": "executing",
                "mm_owner_id": "system",
                "mm_owner_type": "system",
                "mm_entry": "run",
            },
            memo=_memo,
        )

    class _WorkflowIterator:
        current_page = [_workflow("mm:wf-one"), _workflow("mm:wf-two")]
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    active_queries = 0
    max_active_queries = 0

    class _ConcurrentQueryHandle:
        def __init__(self, workflow_id: str) -> None:
            self._workflow_id = workflow_id

        async def query(self, name: str) -> dict[str, object]:
            nonlocal active_queries, max_active_queries
            assert name == "get_progress"
            active_queries += 1
            max_active_queries = max(max_active_queries, active_queries)
            try:
                await asyncio.sleep(0)
                return {
                    "total": 2,
                    "pending": 0,
                    "ready": 0,
                    "executing": 1,
                    "awaitingExternal": 0,
                    "reviewing": 0,
                    "completed": 1,
                    "failed": 0,
                    "skipped": 0,
                    "canceled": 0,
                    "currentStepTitle": self._workflow_id,
                    "updatedAt": "2026-04-04T18:11:15Z",
                    "runId": f"live-{self._workflow_id}",
                }
            finally:
                active_queries -= 1

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=2)),
        list_workflows=Mock(return_value=_WorkflowIterator()),
        get_workflow_handle=Mock(
            side_effect=lambda workflow_id: _ConcurrentQueryHandle(workflow_id)
        ),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={"source": "temporal", "ownerType": "system"},
        )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["runId"] for item in items] == ["live-mm:wf-one", "live-mm:wf-two"]
    assert max_active_queries == 2


def test_list_executions_source_temporal_hydrates_live_progress_for_filters() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service

    class _EmptyCanonicalResult:
        def scalars(self) -> "_EmptyCanonicalResult":
            return self

        def all(self) -> list[TemporalExecutionCanonicalRecord]:
            return []

    class _Session:
        async def execute(self, _stmt: object) -> _EmptyCanonicalResult:
            return _EmptyCanonicalResult()

    app.dependency_overrides[get_async_session] = lambda: _Session()
    _override_user_dependencies(app, is_superuser=True)

    async def _memo():
        return {
            "title": "Live workflow",
            "summary": "Running tests.",
        }

    workflow = SimpleNamespace(
        id="mm:wf-live",
        run_id="run-temporal",
        namespace="default",
        workflow_type="MoonMind.UserWorkflow",
        status="RUNNING",
        start_time=datetime(2026, 4, 4, 18, 0, tzinfo=UTC),
        close_time=None,
        execution_time=None,
        search_attributes={
            "mm_state": "executing",
            "mm_owner_id": "system",
            "mm_owner_type": "system",
            "mm_entry": "run",
        },
        memo=_memo,
    )

    class _WorkflowIterator:
        current_page = [workflow]
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = _override_query_client(
        app,
        progress={
            "total": 3,
            "pending": 0,
            "ready": 0,
            "executing": 1,
            "awaitingExternal": 0,
            "reviewing": 0,
            "completed": 2,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Run tests",
            "updatedAt": "2026-04-04T18:11:15Z",
            "runId": "run-live",
        },
    )
    temporal_client.count_workflows = AsyncMock(return_value=SimpleNamespace(count=1))
    temporal_client.list_workflows = Mock(return_value=_WorkflowIterator())

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions",
            params={
                "source": "temporal",
                "ownerType": "system",
                "progressPctFrom": "50",
                "progressStepTitleContains": "tests",
            },
        )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["workflowId"] for item in items] == ["mm:wf-live"]
    assert items[0]["runId"] == "run-live"
    assert items[0]["progress"]["currentStepTitle"] == "Run tests"
    temporal_client.get_workflow_handle.assert_called_once_with("mm:wf-live")


def test_execution_facets_exclude_requested_facet_filter_and_keep_workflow_scope() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=False)

    async def _memo():
        return {}

    workflow = SimpleNamespace(
        search_attributes={
            "mm_target_runtime": ["claude_code"],
            "mm_state": "executing",
        },
        memo=_memo,
    )

    class _WorkflowIterator:
        current_page = [workflow]
        next_page_token: bytes | None = None

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(
                return_value=SimpleNamespace(
                    custom_attributes={
                        "mm_target_runtime": SimpleNamespace(type=_TARGET_SEARCH_ATTRIBUTE_TYPE),
                    }
                )
            )
        ),
        count_workflows=AsyncMock(
            side_effect=[SimpleNamespace(count=7), SimpleNamespace(count=0)]
        ),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions/facets",
            params={
                "source": "temporal",
                "facet": "targetRuntime",
                "stateIn": "executing",
                "targetRuntimeIn": "codex_cli",
            },
        )

    assert response.status_code == 200
    base_query = temporal_client.list_workflows.call_args.kwargs["query"]
    assert 'WorkflowType="MoonMind.UserWorkflow"' in base_query
    assert 'mm_entry="user_workflow"' in base_query
    assert "mm_owner_id=" in base_query
    assert 'mm_state="executing"' in base_query
    assert "mm_target_runtime" not in base_query
    body = response.json()
    assert body["facet"] == "targetRuntime"
    assert body["items"] == [{"value": "claude_code", "label": "Claude Code", "count": 7}]
    assert body["blankCount"] == 0
    assert body["source"] == "authoritative"


def test_execution_metrics_cost_extraction_unwraps_search_attribute_lists() -> None:
    assert (
        _extract_cost_estimate_usd(
            {
                "mm_cost_estimate_usd": [
                    None,
                    "2.50",
                ]
            }
        )
        == 2.5
    )


def test_execution_metrics_status_filter_limits_metric_buckets() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = _empty_session_override
    _override_user_dependencies(app, is_superuser=False)

    class _WorkflowIterator:
        current_page: list[object] = []

        async def fetch_next_page(self) -> None:
            return None

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(
            side_effect=[SimpleNamespace(count=5), SimpleNamespace(count=3)]
        ),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions/metrics",
            params={"source": "temporal", "stateIn": "failed"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["totalRuns"] == 5
    assert body["completedRuns"] == 0
    assert body["failedRuns"] == 3
    assert body["canceledRuns"] == 0
    assert body["terminalRuns"] == 3
    assert body["successRate"] == 0
    count_queries = [
        call.kwargs["query"] for call in temporal_client.count_workflows.await_args_list
    ]
    assert len(count_queries) == 2
    assert all('ExecutionStatus="Failed"' in query for query in count_queries)
    assert temporal_client.list_workflows.call_args.kwargs["query"].count(
        'ExecutionStatus="Failed"'
    ) == 1


def test_execution_metrics_counts_workflows_concurrently() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = _empty_session_override
    _override_user_dependencies(app, is_superuser=False)

    class _WorkflowIterator:
        current_page: list[object] = []

        async def fetch_next_page(self) -> None:
            return None

    active_calls = 0
    max_active_calls = 0

    async def _count_workflows(*, query: str) -> SimpleNamespace:
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        await asyncio.sleep(0)
        active_calls -= 1
        return SimpleNamespace(count=1)

    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(side_effect=_count_workflows),
        list_workflows=Mock(return_value=_WorkflowIterator()),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions/metrics",
            params={"source": "temporal"},
        )

    assert response.status_code == 200
    assert temporal_client.count_workflows.await_count == 4
    assert max_active_calls > 1


def test_execution_status_facet_counts_static_status_values_with_workflow_scope() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=False)
    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=1)),
        list_workflows=Mock(),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions/facets",
            params={"source": "temporal", "facet": "status", "pageSize": 2},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [
        {"value": "scheduled", "label": "Scheduled", "count": 1},
        {"value": "initializing", "label": "Initializing", "count": 1},
    ]
    first_count_query = temporal_client.count_workflows.await_args_list[0].kwargs["query"]
    assert 'WorkflowType="MoonMind.UserWorkflow"' in first_count_query
    assert 'mm_entry="user_workflow"' in first_count_query
    assert "mm_owner_id=" in first_count_query
    assert body["truncated"] is True

def test_execution_status_facet_supports_real_pagination() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=False)
    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=1)),
        list_workflows=Mock(),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        first_page = test_client.get(
            "/api/executions/facets",
            params={"source": "temporal", "facet": "status", "pageSize": 2},
        )
        second_page = test_client.get(
            "/api/executions/facets",
            params={
                "source": "temporal",
                "facet": "status",
                "pageSize": 2,
                "nextPageToken": first_page.json()["nextPageToken"],
            },
        )

    assert first_page.status_code == 200
    assert first_page.json()["nextPageToken"] == base64.b64encode(b"2").decode("utf-8")
    assert second_page.status_code == 200
    assert second_page.json()["items"] == [
        {
            "value": "waiting_on_dependencies",
            "label": "Waiting On Dependencies",
            "count": 1,
        },
        {"value": "planning", "label": "Planning", "count": 1},
    ]
    assert second_page.json()["nextPageToken"] == base64.b64encode(b"4").decode("utf-8")
    temporal_client.list_workflows.assert_not_called()

def test_execution_facets_reject_malformed_next_page_token() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_user_dependencies(app, is_superuser=False)
    temporal_client = SimpleNamespace(
        count_workflows=AsyncMock(return_value=SimpleNamespace(count=0)),
        list_workflows=Mock(),
    )
    app.dependency_overrides[get_temporal_client] = lambda: temporal_client

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions/facets",
            params={
                "source": "temporal",
                "facet": "targetRuntime",
                "nextPageToken": "not base64",
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_execution_query",
        "message": "nextPageToken must be a valid base64 token.",
    }
    temporal_client.list_workflows.assert_not_called()

def test_list_executions_rejects_non_admin_owner_type_override() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions", params={"ownerType": "system"})

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "execution_forbidden"
    mock_service.list_executions.assert_not_awaited()

def test_step_ledger_contract_models_serialize_using_public_aliases() -> None:
    progress = ExecutionProgressModel.model_validate(
        {
            "total": 1,
            "pending": 0,
            "ready": 0,
            "executing": 0,
            "awaitingExternal": 0,
            "reviewing": 0,
            "completed": 1,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Prepare workspace",
            "updatedAt": "2026-04-07T12:00:00Z",
        }
    )
    snapshot = StepLedgerSnapshotModel.model_validate(
        {
            "workflowId": "wf-1",
            "runId": "run-1",
            "runScope": "latest",
            "steps": [
                {
                    "logicalStepId": "prepare",
                    "order": 1,
                    "title": "Prepare workspace",
                    "tool": {"type": "skill", "name": "repo.prepare"},
                    "dependsOn": [],
                    "status": "completed",
                    "waitingReason": None,
                    "attentionRequired": False,
                    "attempt": 1,
                    "startedAt": "2026-04-07T12:00:00Z",
                    "updatedAt": "2026-04-07T12:00:00Z",
                    "summary": "Workspace prepared",
                    "checks": [],
                    "refs": {
                        "childWorkflowId": None,
                        "childRunId": None,
                        "agentRunId": None,
                    },
                    "artifacts": {
                        "outputSummary": None,
                        "outputPrimary": None,
                        "runtimeStdout": None,
                        "runtimeStderr": None,
                        "runtimeMergedLogs": None,
                        "runtimeDiagnostics": None,
                        "providerSnapshot": None,
                    },
                    "lastError": None,
                }
            ],
        }
    )

    assert progress.model_dump(by_alias=True, mode="json")["awaitingExternal"] == 0
    dumped_snapshot = snapshot.model_dump(by_alias=True, mode="json")
    assert dumped_snapshot["runScope"] == "latest"
    assert dumped_snapshot["steps"][0]["logicalStepId"] == "prepare"

def test_list_executions_uses_owner_id_without_owner_type_for_non_admin() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.list_executions.return_value = SimpleNamespace(
        items=[],
        next_page_token=None,
        count=0,
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    mock_user = _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions")

    assert response.status_code == 200
    kwargs = mock_service.list_executions.await_args.kwargs
    assert kwargs["owner_id"] == str(mock_user.id)
    assert kwargs["owner_type"] is None

def test_list_executions_allows_explicit_user_owner_type_for_non_admin() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.list_executions.return_value = SimpleNamespace(
        items=[],
        next_page_token=None,
        count=0,
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    mock_user = _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions", params={"ownerType": "user"})

    assert response.status_code == 200
    kwargs = mock_service.list_executions.await_args.kwargs
    assert kwargs["owner_id"] == str(mock_user.id)
    assert kwargs["owner_type"] == "user"

def test_create_task_shaped_execution_rejects_invalid_required_capabilities() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "requiredCapabilities": 1,
                    "workflow": {
                        "instructions": "Ship the Temporal integration.",
                    },
                },
            },
        )

    assert response.status_code == 422
    assert (
        response.json()["detail"]["message"]
        == "payload.requiredCapabilities must be a JSON array of strings."
    )
    mock_service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_rejects_missing_task_payload(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={"type": "task", "payload": {"repository": "Moon/Mind"}},
    )

    assert response.status_code == 422
    assert (
        response.json()["detail"]["message"]
        == "Task-shaped Temporal submit requests require payload.task."
    )
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_missing_workflow_payload(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={"type": "workflow", "payload": {"repository": "Moon/Mind"}},
    )

    assert response.status_code == 422
    assert (
        response.json()["detail"]["message"]
        == "Workflow-shaped Temporal submit requests require payload.workflow."
    )
    service.create_execution.assert_not_awaited()


def test_create_workflow_execution_rejects_task_template_version(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Run MM-916 preset submission.",
                    "taskTemplate": {
                        "slug": "jira-implement",
                        "version": "1",
                    },
                }
            },
        },
    )

    assert response.status_code == 422
    assert "semantic versions" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_workflow_execution_rejects_applied_template_version(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Run MM-916 applied preset submission.",
                    "appliedStepTemplates": [
                        {
                            "slug": "jira-implement",
                            "version": "1",
                        }
                    ],
                }
            },
        },
    )

    assert response.status_code == 422
    assert "semantic versions" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_workflow_execution_rejects_tool_version(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Run MM-916 tool submission.",
                    "tool": {
                        "type": "skill",
                        "name": "pr-resolver",
                        "version": "1",
                    },
                }
            },
        },
    )

    assert response.status_code == 422
    assert "semantic versions" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_workflow_execution_rejects_skill_version(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Run MM-916 skill submission.",
                    "skill": {
                        "name": "pr-resolver",
                        "version": "1",
                    },
                }
            },
        },
    )

    assert response.status_code == 422
    assert "semantic versions" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_workflow_execution_rejects_versions_from_all_capability_selectors(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "workflow": {
                    "instructions": "Run pr-resolver for PR 2680.",
                    "tool": {
                        "type": "skill",
                        "name": "pr-resolver",
                        "version": "1.0.0",
                        "inputs": {"pr": "2680"},
                    },
                    "skills": {
                        "include": [
                            {"name": "pr-resolver", "version": "1.0.0"},
                        ],
                    },
                    "taskTemplate": {
                        "slug": "jira-implement",
                        "presetVersion": "1.0.0",
                    },
                    "presetSchedule": {
                        "presetSlug": "jira-implement",
                        "version": "1.0.0",
                    },
                    "authoredPresets": [
                        {
                            "presetSlug": "jira-implement",
                            "presetVersion": "1.0.0",
                        },
                    ],
                    "appliedStepTemplates": [
                        {
                            "slug": "jira-implement",
                            "version": "1.0.0",
                            "composition": {
                                "includes": [
                                    {
                                        "presetSlug": "quality-checks",
                                        "presetVersion": "1.0.0",
                                    }
                                ],
                            },
                        }
                    ],
                    "steps": [
                        {
                            "id": "test",
                            "type": "tool",
                            "instructions": "Run tests.",
                            "tool": {
                                "id": "repo.run_tests",
                                "toolVersion": "1.0.0",
                                "inputs": {"target": "unit"},
                            },
                        },
                        {
                            "id": "fix",
                            "type": "skill",
                            "instructions": "Fix CI.",
                            "skill": {
                                "id": "fix-ci",
                                "skillVersion": "1.0.0",
                                "args": {"pr": "2680"},
                            },
                        },
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "semantic versions" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_more_than_10_dependencies(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    
    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Ship the Temporal integration.",
                    "dependsOn": [f"dep-{i}" for i in range(11)]
                },
            },
        },
    )

    assert response.status_code == 422
    assert "payload.workflow.dependsOn can have a maximum of 10 items" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_rejects_more_than_50_steps(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    steps = [{"title": f"Step {i}", "instructions": f"Do step {i}."} for i in range(51)]
    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Too many steps.",
                    "steps": steps,
                },
            },
        },
    )

    assert response.status_code == 422
    assert "payload.workflow.steps can have a maximum of 50 items" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_rejects_explicit_skill_step_without_skill_payload(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    """MM-569: explicit `type: skill` steps must require a skill sub-payload."""
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Run a skill step without a payload.",
                    "steps": [
                        {
                            "id": "missing-skill",
                            "title": "Missing skill payload",
                            "type": "skill",
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert (
        "payload.workflow.steps[0].skill is required for Skill steps"
        in response.json()["detail"]["message"]
    )
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_normalizes_skill_inputs(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MM-1058: new Skill authoring payloads emit inputs and preserve digests."""
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    async def _identity_validate_skill_step_inputs(*, initial_parameters, **_kwargs):
        return SimpleNamespace(
            valid=True,
            parameters=initial_parameters,
            error_dicts=lambda: [],
        )

    monkeypatch.setattr(
        "api_service.api.routers.executions.validate_skill_step_inputs",
        _identity_validate_skill_step_inputs,
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Run a skill step without args.",
                    "steps": [
                        {
                            "id": "skill-empty-args",
                            "title": "Skill with inputs",
                            "type": "skill",
                            "skill": {
                                "id": "noop",
                                "inputs": {},
                                "inputContractDigest": "sha256:saved",
                            },
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201, response.text
    create_kwargs = service.create_execution.await_args.kwargs
    initial_parameters = create_kwargs["initial_parameters"]
    normalized_steps = initial_parameters["workflow"]["steps"]
    assert len(normalized_steps) == 1
    assert normalized_steps[0]["skill"] == {
        "id": "noop",
        "inputs": {},
        "inputContractDigest": "sha256:saved",
    }


def test_create_task_shaped_execution_accepts_legacy_skill_args_as_inputs(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    """MM-1058: legacy Skill args remain accepted but normalize to inputs."""
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Run a legacy skill step.",
                    "steps": [
                        {
                            "id": "skill-legacy-args",
                            "type": "skill",
                            "skill": {"id": "noop", "args": {"issueKey": "MM-1047"}},
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201, response.text
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    normalized_steps = initial_parameters["workflow"]["steps"]
    assert normalized_steps[0]["skill"] == {
        "id": "noop",
        "inputs": {"issueKey": "MM-1047"},
    }


def test_create_task_shaped_execution_records_skill_digest_mismatch_diagnostic(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MM-1058: digest mismatches produce backend diagnostics without values."""
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    async def _identity_validate_skill_step_inputs(*, initial_parameters, **_kwargs):
        return SimpleNamespace(
            valid=True,
            parameters=initial_parameters,
            error_dicts=lambda: [],
        )

    monkeypatch.setattr(
        "api_service.api.routers.executions.validate_skill_step_inputs",
        _identity_validate_skill_step_inputs,
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Run a stale draft skill step.",
                    "steps": [
                        {
                            "id": "skill-stale-digest",
                            "type": "skill",
                            "skill": {
                                "id": "noop",
                                "inputs": {"issueKey": "MM-1058"},
                                "inputContractDigest": "sha256:old",
                                "currentInputContractDigest": "sha256:new",
                            },
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201, response.text
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    normalized_step = initial_parameters["workflow"]["steps"][0]
    assert normalized_step["skill"] == {
        "id": "noop",
        "inputs": {"issueKey": "MM-1058"},
        "inputContractDigest": "sha256:old",
        "currentInputContractDigest": "sha256:new",
    }
    assert normalized_step["diagnostics"][0] == {
        "code": "skill_input_contract_digest_mismatch",
        "severity": "warning",
        "path": "payload.workflow.steps[0].skill.inputContractDigest",
        "message": (
            "Skill input contract changed since this draft was saved; submitted "
            "values were preserved and revalidated against the current contract."
        ),
        "recoverable": True,
    }
    assert "MM-1058" not in str(normalized_step["diagnostics"])


def test_create_task_shaped_execution_rejects_attachments_when_policy_disabled(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", False)

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Review the uploaded screenshot.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000000",
                            "filename": "wireframe.png",
                            "contentType": "image/png",
                            "sizeBytes": 128,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "attachment policy is disabled" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_rejects_unknown_attachment_fields(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Review the uploaded screenshot.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000000",
                            "filename": "wireframe.png",
                            "contentType": "image/png",
                            "sizeBytes": 128,
                            "caption": "unsupported future field",
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "unsupported fields" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_rejects_unsupported_runtime_with_attachments(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    execute = AsyncMock(
        return_value=_ExecuteResult(
            [
                SimpleNamespace(
                    artifact_id="art_01IMAGEINPUT0000000000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=128,
                )
            ]
        )
    )
    test_client.app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        execute=execute
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "targetRuntime": "unsupported_runtime",
                "workflow": {
                    "instructions": "Review the uploaded screenshot.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000000",
                            "filename": "wireframe.png",
                            "contentType": "image/png",
                            "sizeBytes": 128,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "Unsupported targetRuntime" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_preserves_omnigent_selection(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "targetRuntime": "omnigent",
                "omnigent": {
                    "executionTargetRef": "on-demand-docker",
                    "launchPolicyRef": "codex-on-demand@1",
                },
                "workflow": {
                    "instructions": "Run through Omnigent.",
                    "runtime": {
                        "mode": "omnigent",
                        "executionProfileRef": "codex-oauth-profile",
                    },
                },
            },
        },
    )

    assert response.status_code == 201, response.text
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["targetRuntime"] == "omnigent"
    assert initial_parameters["omnigent"] == {
        "executionTargetRef": "on-demand-docker",
        "launchPolicyRef": "codex-on-demand@1",
    }
    assert initial_parameters["workflow"]["runtime"] == {
        "mode": "omnigent",
        "executionProfileRef": "codex-oauth-profile",
    }


def test_create_task_shaped_execution_rejects_unsupported_step_runtime(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "targetRuntime": "codex_cli",
                "workflow": {
                    "instructions": "Validate step runtime early.",
                    "steps": [
                        {
                            "id": "bad-runtime",
                            "instructions": "This should fail before launch.",
                            "runtime": {"mode": "gemni_cli"},
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert (
        "Unsupported payload.workflow.steps[0].runtime.mode"
        in response.json()["detail"]["message"]
    )
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_fetches_unique_attachments_in_one_query(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    execute = AsyncMock(
        return_value=_ExecuteResult(
            [
                SimpleNamespace(
                    artifact_id="art_01OBJECTIVEINPUT00000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=10,
                ),
                SimpleNamespace(
                    artifact_id="art_01STEPINPUT000000000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=20,
                ),
            ]
        )
    )
    test_client.app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        execute=execute
    )
    execute = AsyncMock(
        return_value=_ExecuteResult(
            [
                SimpleNamespace(
                    artifact_id="art_01IMAGEINPUT0000000000001",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=128,
                ),
                SimpleNamespace(
                    artifact_id="art_01IMAGEINPUT0000000000002",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/webp",
                    size_bytes=256,
                ),
            ]
        )
    )
    test_client.app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        execute=execute
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Review uploaded screenshots.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000001",
                            "filename": "one.png",
                            "contentType": "image/png",
                            "sizeBytes": 128,
                        },
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000002",
                            "filename": "two.webp",
                            "contentType": "image/webp",
                            "sizeBytes": 256,
                        },
                    ],
                },
            },
        },
    )

    assert response.status_code == 201, response.json()
    execute.assert_awaited_once()
    service.create_execution.assert_awaited_once()

def test_create_task_shaped_execution_rejects_svg_attachment_type(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    monkeypatch.setattr(
        settings.workflow,
        "agent_job_attachment_allowed_content_types",
        ("image/png", "image/jpeg", "image/webp", "image/svg+xml"),
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Review the uploaded screenshot.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000000",
                            "filename": "wireframe.svg",
                            "contentType": "image/svg+xml",
                            "sizeBytes": 128,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "image/svg+xml is not supported" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_rejects_attachment_policy_limits(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_max_count", 1)

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Review uploaded screenshots.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000001",
                            "filename": "one.png",
                            "contentType": "image/png",
                            "sizeBytes": 128,
                        },
                        {
                            "artifactId": "art_01IMAGEINPUT0000000000002",
                            "filename": "two.png",
                            "contentType": "image/png",
                            "sizeBytes": 128,
                        },
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "too many input attachments" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_dedupes_and_normalizes_dependencies(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Ship the Temporal integration.",
                    "dependsOn": ["dep-1", " dep-2 ", "", "dep-1", "dep-3"]
                },
            },
        },
    )

    assert response.status_code == 201, response.json()
    service.create_execution.assert_awaited_once()
    kwargs = service.create_execution.call_args.kwargs
    assert kwargs["initial_parameters"]["workflow"]["dependsOn"] == ["dep-1", "dep-2", "dep-3"]

def test_create_task_shaped_execution_prefers_task_depends_on(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "dependsOn": ["legacy-dep"],
                "workflow": {
                    "instructions": "Ship the Temporal integration.",
                    "dependsOn": []
                },
            },
        },
    )

    assert response.status_code == 201
    service.create_execution.assert_awaited_once()
    kwargs = service.create_execution.call_args.kwargs
    assert "dependsOn" not in kwargs["initial_parameters"]["workflow"]

def test_create_task_shaped_execution_applies_default_publish_mode(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "default_publish_mode", "pr")
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "workflow": {
                    "instructions": "Fix the failing workflow.",
                    "runtime": {"mode": "codex"},
                },
            },
        },
    )

    assert response.status_code == 201
    service.create_execution.assert_awaited_once()
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "pr"
    assert initial_parameters["workflow"]["publish"]["mode"] == "pr"

def test_create_task_shaped_execution_allows_jira_orchestrate_pr_publish(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "publishMode": "pr",
                "workflow": {
                    "instructions": "Run Jira Orchestrate for THOR-352.",
                    "tool": {"type": "skill", "name": "jira-orchestrate"},
                    "skill": {"id": "jira-orchestrate"},
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "pr"},
                },
            },
        },
    )

    assert response.status_code == 201
    service.create_execution.assert_awaited_once()
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "pr"
    assert initial_parameters["workflow"]["publish"]["mode"] == "pr"


def test_create_task_shaped_execution_preserves_pr_base_branch(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "publishMode": "pr",
                "workflow": {
                    "instructions": "Run Jira Orchestrate for MM-825.",
                    "tool": {"type": "skill", "name": "jira-orchestrate"},
                    "runtime": {"mode": "codex"},
                    "git": {"branch": "main"},
                    "publish": {"mode": "pr"},
                },
            },
        },
    )

    assert response.status_code == 201
    service.create_execution.assert_awaited_once()
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "pr"
    assert initial_parameters["workflow"]["git"] == {"branch": "main"}


def test_create_task_shaped_execution_rejects_generated_jira_pr_head_as_base_branch(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "publishMode": "pr",
                "workflow": {
                    "instructions": "Run Jira Orchestrate for MM-825.",
                    "tool": {"type": "skill", "name": "jira-orchestrate"},
                    "runtime": {"mode": "codex"},
                    "git": {"branch": "moonmind/jira-orchestrate-mm-825"},
                    "publish": {"mode": "pr"},
                },
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_request"
    assert "PR base branch" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_generated_jira_pr_head_in_publish_base(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "publishMode": "pr",
                "workflow": {
                    "instructions": "Run Jira Orchestrate for MM-825.",
                    "tool": {"type": "skill", "name": "jira-orchestrate"},
                    "runtime": {"mode": "codex"},
                    "git": {"branch": "main"},
                    "publish": {
                        "mode": "pr",
                        "prBaseBranch": "moonmind/jira-orchestrate-mm-825",
                    },
                },
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_request"
    assert (
        "payload.workflow.publish.prBaseBranch"
        in response.json()["detail"]["message"]
    )
    service.create_execution.assert_not_awaited()


@pytest.mark.parametrize(
    "branch",
    [
        "run-jira-orchestrate-for-mm-824-complete-8ad823ae",
        "jira-implement-mm-697",
    ],
)
def test_create_task_shaped_execution_rejects_runtime_generated_jira_pr_head_names(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    branch: str,
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "publishMode": "pr",
                "workflow": {
                    "instructions": "Run Jira Orchestrate for MM-824.",
                    "tool": {"type": "skill", "name": "jira-orchestrate"},
                    "runtime": {"mode": "codex"},
                    "git": {"branch": branch},
                    "publish": {"mode": "pr"},
                },
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_request"
    assert "payload.workflow.git.branch" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_allows_jira_orchestrate_first_step_skill_pr_publish(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "publishMode": "pr",
                "workflow": {
                    "instructions": "Run Jira Orchestrate for THOR-352.",
                    "tool": {"type": "skill", "name": "jira-issue-updater"},
                    "skill": {"id": "jira-issue-updater"},
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "pr"},
                    "steps": [
                        {
                            "id": "tpl:jira-orchestrate:1:01",
                            "title": "Move Jira issue",
                            "instructions": "Transition THOR-352 to In Progress.",
                            "skill": {"id": "jira-issue-updater", "args": {}},
                        }
                    ],
                    "appliedStepTemplates": [
                        {
                            "slug": "jira-orchestrate",
                            "stepIds": ["tpl:jira-orchestrate:1:01"],
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201, response.json()
    service.create_execution.assert_awaited_once()
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "pr"
    assert initial_parameters["workflow"]["publish"]["mode"] == "pr"
    assert initial_parameters["workflow"]["skill"]["name"] == "jira-issue-updater"


def test_create_task_shaped_execution_allows_pr_publish_for_jira_updater(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "publishMode": "pr",
                "workflow": {
                    "title": (
                        "Change Jira issue MM-657 to status In Progress before "
                        "implementation starts."
                    ),
                    "instructions": (
                        "Change Jira issue MM-657 to status In Progress before "
                        "implementation starts."
                    ),
                    "tool": {"type": "skill", "name": "jira-issue-updater"},
                    "skill": {"id": "jira-issue-updater"},
                    "runtime": {"mode": "claude_code"},
                    "publish": {"mode": "pr"},
                },
            },
        },
    )

    assert response.status_code == 201, response.json()
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "pr"
    assert initial_parameters["workflow"]["publish"]["mode"] == "pr"


def test_create_task_shaped_execution_defaults_jira_updater_publish_none(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "publishMode": "pr",
                "workflow": {
                    "title": "Change Jira issue MM-657 to status In Progress.",
                    "instructions": "Change Jira issue MM-657 to status In Progress.",
                    "tool": {"type": "skill", "name": "jira-issue-updater"},
                    "runtime": {"mode": "claude_code"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "none"
    assert initial_parameters["workflow"]["publish"]["mode"] == "none"


def test_create_task_shaped_execution_allows_jira_orchestrate_publish_none(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "publishMode": "none",
                "workflow": {
                    "instructions": "Run Jira Orchestrate for THOR-352.",
                    "tool": {"type": "skill", "name": "jira-orchestrate"},
                    "skill": {"id": "jira-orchestrate"},
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "none"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "none"
    assert initial_parameters["workflow"]["publish"]["mode"] == "none"
    assert initial_parameters["requiredCapabilities"] == []

def test_create_task_shaped_execution_preserves_report_output_contract(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "reportOutput": {
                    "enabled": True,
                    "required": True,
                    "reportType": "integration_test_report",
                    "title": "Integration test report",
                },
                "workflow": {
                    "instructions": "Run the integration test suite.",
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "none"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["reportOutput"] == {
        "enabled": True,
        "required": True,
        "reportType": "integration_test_report",
        "title": "Integration test report",
    }
    assert initial_parameters["workflow"]["reportOutput"] == initial_parameters[
        "reportOutput"
    ]
    task_instructions = initial_parameters["workflow"]["instructions"]
    assert "MoonMind report output contract" in task_instructions
    assert "answer that request directly in the final report body" in task_instructions


def test_create_task_report_output_defaults_primary_path_to_markdown_suffix(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "reportOutput": {
                    "enabled": True,
                    "required": True,
                    "reportType": "agent_run_report",
                    "primaryPath": "reports/final-report",
                },
                "workflow": {
                    "instructions": "Generate a report.",
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "none"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert (
        initial_parameters["reportOutput"]["primaryPath"]
        == "reports/final-report.md"
    )
    assert (
        "Also write the same report to `reports/final-report.md`"
        in initial_parameters["workflow"]["instructions"]
    )


def test_create_task_report_output_rejects_primary_path_over_limit_after_suffix(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    primary_path = "a" * 512

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "reportOutput": {
                    "enabled": True,
                    "required": True,
                    "reportType": "agent_run_report",
                    "primaryPath": primary_path,
                },
                "workflow": {
                    "instructions": "Generate a report.",
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "none"},
                },
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["message"] == (
        "reportOutput.primaryPath must be 512 characters or fewer."
    )
    service.create_execution.assert_not_awaited()


def test_create_task_report_output_preserves_explicit_primary_path_suffix(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "reportOutput": {
                    "enabled": True,
                    "required": True,
                    "reportType": "agent_run_report",
                    "primaryPath": "reports/final-report.txt",
                },
                "workflow": {
                    "instructions": "Generate a report.",
                    "runtime": {"mode": "codex"},
                    "publish": {"mode": "none"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert (
        initial_parameters["reportOutput"]["primaryPath"]
        == "reports/final-report.txt"
    )


def test_create_task_shaped_execution_prefers_task_publish_mode_alias_over_top_publish(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "publish": {
                    "mode": "branch",
                    "commitMessage": "Top-level publish details",
                },
                "workflow": {
                    "instructions": "Fix the failing workflow.",
                    "runtime": {"mode": "codex"},
                    "publish_mode": "none",
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.call_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "none"
    assert initial_parameters["workflow"]["publish"] == {
        "mode": "none",
        "commitMessage": "Top-level publish details",
    }

def test_create_task_shaped_execution_rejects_falsy_non_string_publish_mode(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "publishMode": False,
                "workflow": {
                    "instructions": "Fix the failing workflow.",
                    "runtime": {"mode": "codex"},
                },
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["message"] == (
        "publish.mode must be one of: auto, branch, none, pr"
    )
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_preserves_remediation_payload(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "workflow": {
                    "instructions": "Investigate the failed run.",
                    "runtime": {"mode": "codex"},
                    "remediation": {
                        "target": {"workflowId": "mm:target-workflow"},
                        "mode": "snapshot_then_follow",
                        "authorityMode": "observe_only",
                        "trigger": {"type": "manual"},
                    },
                },
            },
        },
    )

    assert response.status_code == 201
    service.create_execution.assert_awaited_once()
    kwargs = service.create_execution.call_args.kwargs
    assert kwargs["initial_parameters"]["workflow"]["remediation"] == {
        "target": {"workflowId": "mm:target-workflow"},
        "mode": "snapshot_then_follow",
        "authorityMode": "observe_only",
        "trigger": {"type": "manual"},
    }

def test_create_task_shaped_execution_preserves_malformed_remediation_for_service_validation(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "workflow": {
                    "instructions": "Investigate the failed run.",
                    "runtime": {"mode": "codex"},
                    "remediation": "mm:target-workflow",
                },
            },
        },
    )

    assert response.status_code == 201
    service.create_execution.assert_awaited_once()
    kwargs = service.create_execution.call_args.kwargs
    assert kwargs["initial_parameters"]["workflow"]["remediation"] == "mm:target-workflow"

def test_create_remediation_convenience_route_expands_to_task_create_contract(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions/mm:target-workflow/remediation",
        json={
            "repository": "MoonLadderStudios/MoonMind",
            "instructions": "Investigate the target execution.",
            "runtime": {"mode": "codex"},
            "remediation": {
                "mode": "snapshot",
                "authorityMode": "observe_only",
                "trigger": {"type": "manual"},
            },
        },
    )

    assert response.status_code == 201
    service.create_execution.assert_awaited_once()
    kwargs = service.create_execution.call_args.kwargs
    assert kwargs["workflow_type"] == "MoonMind.UserWorkflow"
    assert kwargs["initial_parameters"]["workflow"]["instructions"] == (
        "Investigate the target execution."
    )
    assert kwargs["initial_parameters"]["workflow"]["runtime"] == {"mode": "codex_cli"}
    assert kwargs["initial_parameters"]["workflow"]["remediation"] == {
        "target": {"workflowId": "mm:target-workflow"},
        "mode": "snapshot",
        "authorityMode": "observe_only",
        "trigger": {"type": "manual"},
    }
    assert kwargs["initial_parameters"]["publishMode"] == "pr"
    assert kwargs["initial_parameters"]["workflow"]["publish"]["mode"] == "pr"

def test_create_remediation_convenience_route_uses_top_level_overrides(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    scheduled_for = datetime.now(UTC) + timedelta(minutes=5)
    record = _build_execution_record(state=MoonMindWorkflowState.SCHEDULED)
    record.scheduled_for = scheduled_for
    service.create_execution.return_value = record

    response = test_client.post(
        "/api/executions/mm:target-workflow/remediation",
        json={
            "repository": "MoonLadderStudios/MoonMind",
            "priority": 7,
            "maxAttempts": 5,
            "schedule": {
                "mode": "once",
                "scheduledFor": scheduled_for.isoformat(),
            },
            "instructions": "Top-level instructions",
            "runtime": {"mode": "codex"},
            "publish_mode": "none",
            "remediation": {
                "mode": "snapshot",
                "authorityMode": "observe_only",
                "trigger": {"type": "manual"},
            },
            "workflow": {
                "instructions": "Nested instructions",
                "runtime": {"mode": "jules"},
                "remediation": {
                    "mode": "snapshot_then_follow",
                    "authorityMode": "limited_write",
                },
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    initial_parameters = called_kwargs["initial_parameters"]
    assert called_kwargs["scheduled_for"] == scheduled_for
    assert initial_parameters["priority"] == 7
    assert initial_parameters["maxAttempts"] == 5
    assert initial_parameters["workflow"]["instructions"] == "Top-level instructions"
    assert initial_parameters["workflow"]["runtime"] == {"mode": "codex_cli"}
    assert initial_parameters["workflow"]["remediation"] == {
        "target": {"workflowId": "mm:target-workflow"},
        "mode": "snapshot",
        "authorityMode": "observe_only",
        "trigger": {"type": "manual"},
    }
    assert initial_parameters["publishMode"] == "none"
    assert initial_parameters["workflow"]["publish"]["mode"] == "none"

def test_create_remediation_convenience_route_rejects_malformed_remediation(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions/mm:target-workflow/remediation",
        json={
            "repository": "MoonLadderStudios/MoonMind",
            "instructions": "Investigate the target execution.",
            "remediation": "mm:target-workflow",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_execution_request",
        "message": "workflow.remediation must be an object",
    }
    service.create_execution.assert_not_awaited()

def test_list_remediations_for_target_returns_compact_inbound_links(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    now = datetime.now(UTC)
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )
    service.list_remediations_for_target.return_value = [
        SimpleNamespace(
            remediation_workflow_id="mm:remediation-1",
            remediation_run_id="run-remediation-1",
            target_workflow_id="mm:target-workflow",
            target_run_id="run-target",
            mode="snapshot_then_follow",
            authority_mode="approval_gated",
            status="awaiting_approval",
            active_lock_scope="target_execution",
            active_lock_holder="mm:remediation-1",
            latest_action_summary="Proposed session interrupt",
            outcome=None,
            context_artifact_ref="art_context",
            created_at=now,
            updated_at=now,
        )
    ]

    response = test_client.get(
        "/api/executions/mm:target-workflow/remediations",
        params={"direction": "inbound"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "direction": "inbound",
        "items": [
            {
                "remediationWorkflowId": "mm:remediation-1",
                "remediationRunId": "run-remediation-1",
                "targetWorkflowId": "mm:target-workflow",
                "targetRunId": "run-target",
                "mode": "snapshot_then_follow",
                "authorityMode": "approval_gated",
                "status": "awaiting_approval",
                "activeLockScope": "target_execution",
                "activeLockHolder": "mm:remediation-1",
                "latestActionSummary": "Proposed session interrupt",
                "resolution": None,
                "contextArtifactRef": "art_context",
                "selectedSteps": None,
                "currentTargetState": None,
                "allowedActions": None,
                "evidenceDegraded": None,
                "unavailableEvidenceClasses": None,
                "liveObservation": None,
                "lockOutcome": None,
                "approvalState": {
                    "requestId": "mm:remediation-1:approval",
                    "actionKind": None,
                    "riskTier": None,
                    "preconditions": None,
                    "blastRadius": None,
                    "decision": "pending",
                    "decisionActor": None,
                    "decisionAt": None,
                    "canDecide": True,
                    "auditRef": None,
                },
                "checkpointBranches": [],
                "createdAt": now.isoformat().replace("+00:00", "Z"),
                "updatedAt": now.isoformat().replace("+00:00", "Z"),
            }
        ],
    }
    service.list_remediations_for_target.assert_awaited_once_with("mm:target-workflow")
    service.list_remediation_targets.assert_not_called()

def test_list_remediations_for_remediation_returns_compact_outbound_links(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    now = datetime.now(UTC)
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )
    service.list_remediation_targets.return_value = [
        SimpleNamespace(
            remediation_workflow_id="mm:remediation-1",
            remediation_run_id="run-remediation-1",
            target_workflow_id="mm:target-workflow",
            target_run_id="run-target",
            mode="snapshot",
            authority_mode="observe_only",
            status="created",
            active_lock_scope=None,
            active_lock_holder=None,
            latest_action_summary=None,
            outcome="resolved",
            context_artifact_ref=None,
            created_at=now,
            updated_at=now,
        )
    ]

    response = test_client.get(
        "/api/executions/mm:remediation-1/remediations",
        params={"direction": "outbound"},
    )

    assert response.status_code == 200
    assert response.json()["direction"] == "outbound"
    assert response.json()["items"][0] == {
        "remediationWorkflowId": "mm:remediation-1",
        "remediationRunId": "run-remediation-1",
        "targetWorkflowId": "mm:target-workflow",
        "targetRunId": "run-target",
        "mode": "snapshot",
        "authorityMode": "observe_only",
        "status": "created",
        "activeLockScope": None,
        "activeLockHolder": None,
        "latestActionSummary": None,
        "resolution": "resolved",
        "contextArtifactRef": None,
        "selectedSteps": None,
        "currentTargetState": None,
        "allowedActions": None,
        "evidenceDegraded": None,
        "unavailableEvidenceClasses": None,
        "liveObservation": None,
        "lockOutcome": None,
        "approvalState": None,
        "checkpointBranches": [],
        "createdAt": now.isoformat().replace("+00:00", "Z"),
        "updatedAt": now.isoformat().replace("+00:00", "Z"),
    }
    service.list_remediation_targets.assert_awaited_once_with("mm:remediation-1")
    service.list_remediations_for_target.assert_not_called()

def test_list_remediations_for_remediation_returns_rich_operator_metadata(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    now = datetime.now(UTC)
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )
    service.list_remediation_targets.return_value = [
        SimpleNamespace(
            remediation_workflow_id="mm:remediation-rich",
            remediation_run_id="run-remediation-rich",
            target_workflow_id="mm:target-rich",
            target_run_id="run-target-rich",
            mode="snapshot_then_follow",
            authority_mode="approval_gated",
            status="awaiting_approval",
            active_lock_scope="target_execution",
            active_lock_holder="mm:remediation-rich",
            latest_action_summary="Proposed session interrupt",
            outcome="precondition_failed",
            context_artifact_ref="art_context_rich",
            selected_steps=["collect-context", "repair-runtime"],
            current_target_state="awaiting_external",
            allowed_actions=["inspect_context", "request_approval"],
            evidence_degraded=True,
            unavailable_evidence_classes=["runtime_stderr", "provider_snapshot"],
            live_observation={
                "status": "active",
                "label": "Live observation active",
                "sequenceCursor": "stdout:42",
                "reconnectState": "reconnected",
                "epoch": "run-target-rich:2",
                "fallbackReason": "Durable context remains authoritative.",
                "rawPath": "/var/lib/moonmind/raw-context.json",
            },
            lock_outcome={
                "state": "conflict",
                "holder": "mm:remediation-rich",
                "releasedAt": None,
            },
            approval_state={
                "requestId": "approval-rich",
                "actionKind": "session_interrupt",
                "riskTier": "high",
                "preconditions": "Target run is still awaiting an external session.",
                "blastRadius": "One managed runtime session.",
                "decision": "pending",
                "canDecide": True,
                "auditRef": "audit-rich",
            },
            checkpoint_branch_links=[
                {
                    "workflowId": "mm:target-rich",
                    "branchId": "cbr-rich",
                    "branchTurnId": "cbt-rich",
                    "checkpointRef": "artifact://checkpoints/rich",
                    "contextArtifactRef": "art_context_rich",
                    "loopId": "loop-rich",
                    "rootCheckpointRef": "artifact://workspace/C0",
                    "rootWorkspaceDigest": "sha256:root",
                    "headCheckpointRef": "artifact://workspace/C2",
                    "headWorkspaceDigest": "sha256:head",
                    "headStepExecutionId": "step:2",
                    "headAttemptOrdinal": 2,
                    "headVersion": 3,
                    "headStatus": "verified_incomplete",
                    "latestVerificationRef": "artifact://verification/V2",
                    "latestVerificationVerdict": "ADDITIONAL_WORK_NEEDED",
                    "remainingWorkRef": "artifact://verification/V2#remainingWork",
                    "nextActionBaseline": {
                        "checkpointRef": "artifact://workspace/C2",
                        "workspaceDigest": "sha256:head",
                        "headVersion": 3,
                    },
                }
            ],
            created_at=now,
            updated_at=now,
        )
    ]

    response = test_client.get(
        "/api/executions/mm:remediation-rich/remediations",
        params={"direction": "outbound"},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["selectedSteps"] == ["collect-context", "repair-runtime"]
    assert item["currentTargetState"] == "awaiting_external"
    assert item["allowedActions"] == ["inspect_context", "request_approval"]
    assert item["evidenceDegraded"] is True
    assert item["unavailableEvidenceClasses"] == [
        "runtime_stderr",
        "provider_snapshot",
    ]
    assert item["liveObservation"] == {
        "status": "active",
        "label": "Live observation active",
        "sequenceCursor": "stdout:42",
        "reconnectState": "reconnected",
        "epoch": "run-target-rich:2",
        "fallbackReason": "Durable context remains authoritative.",
    }
    assert item["lockOutcome"] == {
        "state": "conflict",
        "holder": "mm:remediation-rich",
        "releasedAt": None,
    }
    assert item["approvalState"] == {
        "requestId": "approval-rich",
        "actionKind": "session_interrupt",
        "riskTier": "high",
        "preconditions": "Target run is still awaiting an external session.",
        "blastRadius": "One managed runtime session.",
        "decision": "pending",
        "decisionActor": None,
        "decisionAt": None,
        "canDecide": True,
        "auditRef": "audit-rich",
    }
    assert item["checkpointBranches"] == [
        {
            "workflowId": "mm:target-rich",
            "branchId": "cbr-rich",
            "branchTurnId": "cbt-rich",
            "operation": None,
            "idempotencyKey": None,
            "checkpointRef": "artifact://checkpoints/rich",
            "contextArtifactRef": "art_context_rich",
            "loopId": "loop-rich",
            "rootCheckpointRef": "artifact://workspace/C0",
            "rootWorkspaceDigest": "sha256:root",
            "headCheckpointRef": "artifact://workspace/C2",
            "headWorkspaceDigest": "sha256:head",
            "headStepExecutionId": "step:2",
            "headAttemptOrdinal": 2,
            "headVersion": 3,
            "headStatus": "verified_incomplete",
            "latestVerificationRef": "artifact://verification/V2",
            "latestVerificationVerdict": "ADDITIONAL_WORK_NEEDED",
            "supersedesCheckpointRef": None,
            "remainingWorkRef": "artifact://verification/V2#remainingWork",
            "nextActionBaseline": {
                "checkpointRef": "artifact://workspace/C2",
                "workspaceDigest": "sha256:head",
                "headVersion": 3,
            },
            "createdAt": None,
        }
    ]
    assert "/var/lib/moonmind/raw-context.json" not in json.dumps(item)
    service.list_remediation_targets.assert_awaited_once_with("mm:remediation-rich")
    service.list_remediations_for_target.assert_not_called()

def test_list_remediations_rejects_unknown_direction(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )

    response = test_client.get(
        "/api/executions/mm:target-workflow/remediations",
        params={"direction": "sideways"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_remediation_direction"

def test_record_remediation_approval_decision_calls_trusted_service(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )
    service.record_remediation_approval_decision.return_value = {
        "accepted": True,
        "workflowId": "mm:remediation-1",
        "requestId": "approval-1",
        "decision": "approved",
    }

    response = test_client.post(
        "/api/executions/mm:remediation-1/remediation/approvals/approval-1",
        json={"decision": "approved", "comment": "Reviewed."},
    )

    assert response.status_code == 200
    assert response.json() == {
        "accepted": True,
        "workflowId": "mm:remediation-1",
        "requestId": "approval-1",
        "decision": "approved",
    }
    service.record_remediation_approval_decision.assert_awaited_once_with(
        remediation_workflow_id="mm:remediation-1",
        request_id="approval-1",
        decision="approved",
        comment="Reviewed.",
        actor=user.email,
    )

def test_record_remediation_approval_decision_rejects_unknown_decision(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )

    response = test_client.post(
        "/api/executions/mm:remediation-1/remediation/approvals/approval-1",
        json={"decision": "maybe"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == (
        "invalid_remediation_approval_decision"
    )
    service.record_remediation_approval_decision.assert_not_awaited()

def test_create_task_shaped_execution_maps_instructions_and_tool_for_temporal(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "priority": 2,
            "maxAttempts": 4,
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "codex",
                "requiredCapabilities": ["git"],
                "workflow": {
                    "instructions": "Fix failing Temporal run.",
                    "runtime": {
                        "mode": "codex",
                        "model": "gpt-5-codex",
                        "effort": "high",
                    },
                    "skill": {
                        "id": "pr-resolver",
                        "args": {"repo": "MoonLadderStudios/MoonMind", "pr": "42"},
                    },
                    "git": {
                        "startingBranch": "feature/resolve-pr",
                        "branch": "codex/pr-resolver",
                    },
                },
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    initial_parameters = called_kwargs["initial_parameters"]

    assert initial_parameters["instructions"] == "Fix failing Temporal run."
    assert initial_parameters["workflow"]["tool"]["type"] == "skill"
    assert initial_parameters["workflow"]["tool"]["name"] == "pr-resolver"
    assert "version" not in initial_parameters["workflow"]["tool"]
    assert initial_parameters["workflow"]["inputs"] == {
        "repo": "MoonLadderStudios/MoonMind",
        "pr": "42",
    }
    assert initial_parameters["workflow"]["skill"] == {
        "name": "pr-resolver",
    }
    assert initial_parameters["workflow"]["git"] == {
        "startingBranch": "feature/resolve-pr",
        "branch": "codex/pr-resolver",
    }

def test_create_task_shaped_execution_preserves_proposal_and_skill_intent(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "workflow": {
                    "instructions": "Improve managed-session proposals.",
                    "runtime": {"mode": "codex"},
                    "proposeTasks": True,
                    "proposalPolicy": {
                        "targets": ["workflow_repo", "moonmind"],
                        "maxItems": {"workflow_repo": 2, "moonmind": 1},
                        "minSeverityForMoonMind": "medium",
                        "defaultRuntime": "claude_code",
                    },
                        "skills": {
                            "sets": ["deployment-default", "proposal-quality"],
                            "include": [{"name": "moonmind-doc-writer"}],
                            "exclude": ["legacy-proposer"],
                            "materializationMode": "hybrid",
                        },
                    "steps": [
                        {
                            "id": "review",
                            "instructions": "Review the proposal contract.",
                            "skills": {
                                "sets": ["docs-review"],
                                "include": [{"name": "architecture-review"}],
                            },
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]

    assert "proposeTasks" not in initial_parameters
    assert "proposalPolicy" not in initial_parameters
    assert initial_parameters["workflow"]["proposeTasks"] is True
    assert initial_parameters["workflow"]["proposalPolicy"] == {
        "targets": ["workflow_repo", "moonmind"],
        "maxItems": {"workflow_repo": 2, "moonmind": 1},
        "minSeverityForMoonMind": "medium",
        "defaultRuntime": "claude_code",
    }
    assert initial_parameters["workflow"]["skills"] == {
        "sets": ["deployment-default", "proposal-quality"],
        "include": [{"name": "moonmind-doc-writer"}],
        "exclude": ["legacy-proposer"],
        "materializationMode": "hybrid",
    }
    assert initial_parameters["workflow"]["steps"][0]["skills"] == {
        "sets": ["docs-review"],
        "include": [{"name": "architecture-review"}],
    }

def test_create_task_shaped_execution_rejects_invalid_proposal_policy(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Improve managed-session proposals.",
                    "proposalPolicy": {
                        "targets": ["side-channel"],
                    },
                },
            },
        },
    )

    assert response.status_code == 422
    assert "workflow.proposalPolicy.targets" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_accepts_provider_profile_alias() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.create_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                default_model="gpt-5-codex",
            )
        )
    )
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex",
                    "workflow": {
                        "instructions": "Fix failing Temporal run.",
                        "runtime": {
                            "mode": "codex",
                            "providerProfile": "codex-provider-profile",
                        },
                    },
                },
            },
        )

    assert response.status_code == 201
    initial_parameters = mock_service.create_execution.await_args.kwargs["initial_parameters"]
    assert initial_parameters["profileId"] == "codex-provider-profile"
    assert initial_parameters["model"] == "gpt-5-codex"
    assert initial_parameters["modelSource"] == "provider_profile_default"
    app.dependency_overrides.clear()


def test_create_task_shaped_execution_resolves_model_tier_against_profile() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.create_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                profile_id="codex-provider-profile",
                default_model=None,
                default_effort=None,
                model_tiers=[
                    {"label": "Review", "model": "gpt-5-mini", "effort": "low"},
                    {"label": "Implement", "model": "gpt-5.5", "effort": "high"},
                ],
                default_model_tier=1,
            )
        )
    )
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex",
                    "workflow": {
                        "instructions": "Implement MM-1170.",
                        "runtime": {
                            "mode": "codex",
                            "providerProfile": "codex-provider-profile",
                            "modelTier": 2,
                        },
                    },
                },
            },
        )

    assert response.status_code == 201
    initial_parameters = mock_service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["model"] == "gpt-5.5"
    assert initial_parameters["effort"] == "high"
    assert initial_parameters["modelSource"] == "requested_tier"
    assert initial_parameters["modelTierResolution"] == {
        "requestedModelTier": 2,
        "effectiveModelTier": 2,
        "tierLabel": "Implement",
        "fallbackReason": None,
        "resolvedModel": "gpt-5.5",
        "resolvedEffort": "high",
        "modelSource": "requested_tier",
        "effortSource": "requested_tier",
        "effortApplicationStatus": "unknown",
        "previewMismatch": False,
        "providerProfileId": "codex-provider-profile",
    }
    app.dependency_overrides.clear()


def test_create_task_shaped_execution_records_tier_preview_mismatch() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.create_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                profile_id="codex-provider-profile",
                default_model="legacy-default",
                default_model_tier=1,
                model_tiers=[
                    {
                        "label": "Plan",
                        "model": "codex-plan-current",
                        "effort": "low",
                    },
                    {
                        "label": "Implement",
                        "model": "codex-implement-current",
                        "effort": "high",
                    },
                ],
            )
        )
    )
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex",
                    "workflow": {
                        "instructions": "Run with a stale advisory preview.",
                        "runtime": {
                            "mode": "codex",
                            "providerProfile": "codex-provider-profile",
                            "modelTier": 2,
                            "tierPreview": {
                                "requestedTier": 2,
                                "effectiveTier": 2,
                                "model": "codex-implement-stale",
                                "effort": "high",
                                "fallbackReason": None,
                            },
                        },
                    },
                },
            },
        )

    assert response.status_code == 201
    initial_parameters = mock_service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["model"] == "codex-implement-current"
    assert initial_parameters["effort"] == "high"
    assert initial_parameters["modelSource"] == "requested_tier"
    assert initial_parameters["modelTierResolution"] == {
        "requestedModelTier": 2,
        "effectiveModelTier": 2,
        "tierLabel": "Implement",
        "fallbackReason": None,
        "resolvedModel": "codex-implement-current",
        "resolvedEffort": "high",
        "modelSource": "requested_tier",
        "effortSource": "requested_tier",
        "effortApplicationStatus": "unknown",
        "previewMismatch": True,
        "providerProfileId": "codex-provider-profile",
    }
    app.dependency_overrides.clear()


def test_create_task_shaped_execution_rejects_strict_unavailable_model_tier() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.create_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                profile_id="codex-provider-profile",
                default_model=None,
                default_effort=None,
                model_tiers=[
                    {"label": "Review", "model": "gpt-5-mini", "effort": "low"},
                    {"label": "Implement", "model": "gpt-5.5", "effort": "high"},
                ],
                default_model_tier=1,
            )
        )
    )
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=False)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex",
                    "workflow": {
                        "instructions": "Implement MM-1170.",
                        "runtime": {
                            "mode": "codex",
                            "providerProfile": "codex-provider-profile",
                            "modelTier": 3,
                            "tierFallback": "strict",
                        },
                    },
                },
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["message"] == "requested_model_tier_unavailable"
    mock_service.create_execution.assert_not_awaited()
    app.dependency_overrides.clear()


def test_create_task_shaped_execution_preserves_task_title_and_publish_overrides(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "title": "Fix login redirect",
                    "instructions": "Update OAuth callback behavior.",
                    "publish": {
                        "mode": "pr",
                        "prTitle": "PR: Ensure OAuth redirect is correct",
                        "prBody": "Adds integration tests and updates callback routing.",
                    },
                },
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    initial_parameters = called_kwargs["initial_parameters"]

    assert initial_parameters["workflow"]["title"] == "Fix login redirect"
    assert initial_parameters["workflow"]["publish"]["prTitle"] == "PR: Ensure OAuth redirect is correct"
    assert initial_parameters["workflow"]["publish"]["prBody"] == "Adds integration tests and updates callback routing."

def test_create_task_shaped_execution_preserves_merge_automation_request(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "codex_cli",
                "publishMode": "pr",
                "mergeAutomation": {
                    "enabled": True,
                    "mergeMethod": "squash",
                    "fallbackPollSeconds": 60,
                },
                "workflow": {
                    "instructions": "Implement and publish a pull request.",
                    "runtime": {"mode": "codex_cli"},
                    "publish": {"mode": "pr"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["publishMode"] == "pr"
    assert initial_parameters["workflow"]["publish"]["mode"] == "pr"
    assert initial_parameters["mergeAutomation"] == {
        "enabled": True,
        "mergeMethod": "squash",
        "fallbackPollSeconds": 60,
    }

def test_create_task_shaped_execution_preserves_nested_merge_automation_request(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "codex_cli",
                "workflow": {
                    "instructions": "Implement and publish a pull request.",
                    "runtime": {"mode": "codex_cli"},
                    "publish": {
                        "mode": "pr",
                        "mergeAutomation": {
                            "enabled": True,
                            "mergeMethod": "rebase",
                        },
                    },
                    "mergeAutomation": {
                        "enabled": True,
                        "automatedReview": "optional",
                    },
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["workflow"]["mergeAutomation"] == {
        "enabled": True,
        "automatedReview": "optional",
    }
    assert initial_parameters["workflow"]["publish"]["mergeAutomation"] == {
        "enabled": True,
        "mergeMethod": "rebase",
    }

def test_serialize_execution_exposes_merge_automation_as_publish_mode() -> None:
    record = _build_execution_record()
    record.parameters = {
        "publishMode": "pr",
        "workflow": {
            "publish": {
                "mode": "pr",
                "mergeAutomation": {"enabled": True},
            },
        },
    }

    payload = _serialize_execution(record)

    assert payload.publish_mode == "pr_with_merge_automation"
    assert "mergeAutomationSelected" not in payload.model_dump(by_alias=True)


def test_serialize_execution_exposes_proposal_outcome_summary() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.PROPOSALS)
    record.memo["proposals"] = {
        "requested": True,
        "generatedCount": 2,
        "submittedCount": 2,
        "deliveredCount": 1,
        "validationErrors": [
            {"code": "proposal_missing_task", "message": "proposal skipped: [REDACTED]"}
        ],
        "deliveryFailures": [
            {
                "provider": "jira",
                "code": "delivery_failed",
                "message": "delivery failed: [REDACTED]",
            }
        ],
        "externalLinks": [
            {
                "provider": "jira",
                "externalKey": "MM-901",
                "externalUrl": "https://jira.example/browse/MM-901",
            }
        ],
        "dedupUpdates": [
            {
                "provider": "github",
                "externalKey": "42",
                "created": False,
                "duplicateSource": "existing-open-issue",
            }
        ],
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["state"] == "proposals"
    assert payload["dashboardStatus"] == "running"
    assert payload["proposalSummary"]["deliveredCount"] == 1
    assert payload["proposalSummary"]["externalLinks"][0]["externalKey"] == "MM-901"
    assert payload["proposalOutcomes"][0]["provider"] == "jira"
    assert (
        payload["proposalOutcomes"][0]["externalUrl"]
        == "https://jira.example/browse/MM-901"
    )


def test_serialize_execution_deduplicates_proposal_outcomes_by_external_key() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.PROPOSALS)
    record.memo["proposals"] = {
        "externalLinks": [
            {
                "provider": "jira",
                "externalKey": "MM-901",
                "externalUrl": "https://jira.example/browse/MM-901",
            }
        ],
        "dedupUpdates": [
            {
                "provider": "jira",
                "externalKey": "MM-901",
                "created": False,
                "duplicateSource": "existing-open-issue",
            }
        ],
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["proposalOutcomes"] == [
        {
            "provider": "jira",
            "externalKey": "MM-901",
            "externalUrl": "https://jira.example/browse/MM-901",
            "deliveryStatus": "updated",
            "created": False,
            "duplicateSource": "existing-open-issue",
        }
    ]

def test_serialize_execution_includes_failed_proposal_outcomes() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.COMPLETED)
    record.memo["proposals"] = {
        "deliveryFailures": [
            {
                "provider": "jira",
                "externalKey": "MM-902",
                "code": "delivery_failed",
                "message": "delivery failed: [REDACTED]",
            }
        ],
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["proposalOutcomes"] == [
        {
            "provider": "jira",
            "externalKey": "MM-902",
            "code": "delivery_failed",
            "message": "delivery failed: [REDACTED]",
            "deliveryStatus": "failed",
        }
    ]


def test_serialize_execution_projects_observability_from_finish_summary() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.COMPLETED)
    record.close_status = TemporalExecutionCloseStatus.COMPLETED
    record.memo["worker_id"] = "worker-7"
    record.memo["finishSummary"] = {
        "timestamps": {"durationMs": 12345},
        "finishOutcome": {"code": "NO_CHANGES", "reason": "No local changes"},
        "runQuality": {
            "code": "flaky_test_detected",
            "reason": "A flaky test was retried before passing.",
            "tags": ["flaky_test", "retry"],
            "severity": "high",
        },
        "proposals": {"requested": True, "submittedCount": 1},
        "cost": {"status": "not_recorded", "amountUsd": None},
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["runMetrics"] == {
        "durationMs": 12345,
        "outcomeCode": "NO_COMMIT",
        "success": True,
        "successRateSample": {"success": 1, "sampleSize": 1},
        "cost": {"status": "not_recorded", "amountUsd": None},
    }
    assert payload["improvementSignals"][0]["tags"] == ["flaky_test", "retry"]
    assert payload["recommendedNextAction"] == "Review generated improvement proposals."
    assert payload["logContext"] == {
        "workflowId": "mm:wf-1",
        "runId": "run-2",
        "workerId": "worker-7",
        "namespace": "moonmind",
    }


def test_serialize_execution_omits_success_rate_sample_for_active_run() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["runMetrics"]["success"] is False
    assert payload["runMetrics"]["successRateSample"] == {
        "success": 0,
        "sampleSize": 0,
    }


def test_serialize_execution_handles_mixed_timezone_duration_inputs() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.close_status = TemporalExecutionCloseStatus.FAILED
    record.started_at = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
    record.updated_at = datetime(2026, 6, 4, 12, 1)

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["runMetrics"]["durationMs"] is None
    assert payload["runMetrics"]["successRateSample"] == {
        "success": 0,
        "sampleSize": 1,
    }


def test_serialize_execution_exposes_snake_case_publish_merge_automation_as_publish_mode() -> None:
    record = _build_execution_record()
    record.parameters = {
        "publishMode": "pr",
        "publish": {
            "mode": "pr",
            "merge_automation": {"enabled": True},
        },
    }

    payload = _serialize_execution(record)

    assert payload.publish_mode == "pr_with_merge_automation"
    assert "mergeAutomationSelected" not in payload.model_dump(by_alias=True)

def test_serialize_execution_keeps_plain_pr_publish_mode_without_merge_automation() -> None:
    record = _build_execution_record()
    record.parameters = {"publishMode": "pr", "mergeAutomation": {"enabled": False}}

    payload = _serialize_execution(record)

    assert payload.publish_mode == "pr"
    assert "mergeAutomationSelected" not in payload.model_dump(by_alias=True)

def test_create_task_shaped_execution_preserves_story_output_contract(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "title": "Break down task proposal design",
                    "instructions": "Extract stories from docs/Workflows/WorkflowProposalSystem.md.",
                    "storyOutput": {
                        "mode": "jira",
                        "jira": {
                            "projectKey": "MM",
                            "issueTypeId": "10001",
                            "issueTypeName": "Story",
                            "dependencyMode": "linear_blocker_chain",
                            "labels": ["moonmind"],
                        },
                    },
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs["initial_parameters"]
    assert initial_parameters["storyOutput"] == {
        "mode": "jira",
        "jira": {
            "projectKey": "MM",
            "issueTypeId": "10001",
            "issueTypeName": "Story",
            "dependencyMode": "linear_blocker_chain",
            "labels": ["moonmind"],
        },
    }
    assert initial_parameters["workflow"]["storyOutput"] == initial_parameters["storyOutput"]

def test_create_task_shaped_execution_defaults_partial_story_output_mode(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "title": "Break down task proposal design",
                    "instructions": "Extract stories from docs/Workflows/WorkflowProposalSystem.md.",
                    "storyOutput": {
                        "jira": {
                            "projectKey": "MM",
                            "issueTypeId": "10001",
                        },
                    },
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs["initial_parameters"]
    assert initial_parameters["storyOutput"] == {
        "mode": "jira",
        "jira": {
            "projectKey": "MM",
            "issueTypeId": "10001",
        },
    }

def test_create_task_shaped_execution_defaults_runtime_into_parameters(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "workflow": {
                    "title": "Resolve queued PR",
                    "instructions": "Run pr-resolver for the branch.",
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["targetRuntime"] == "codex_cli"
    assert initial_parameters["workflow"]["runtime"]["mode"] == "codex_cli"


def test_create_task_shaped_execution_normalizes_scalar_step_runtime_fields(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()
    test_client.app.dependency_overrides[get_async_session] = lambda: None

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "codex_cli",
                "workflow": {
                    "title": "Preserve scalar runtime fields",
                    "instructions": "Normalize step runtime metadata.",
                    "steps": [
                        {
                            "id": "scalar-runtime",
                            "instructions": "Use a step profile.",
                            "runtime": {
                                "mode": "CLAUDE",
                                "model": 42,
                                "effort": True,
                                "profileId": 123,
                            },
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    runtime = initial_parameters["workflow"]["steps"][0]["runtime"]
    assert runtime["mode"] == "claude_code"
    assert runtime["model"] == "42"
    assert runtime["requestedModel"] == "42"
    assert runtime["modelSource"] == "task_override"
    assert runtime["effort"] == "True"
    assert runtime["profileId"] == "123"
    assert runtime["providerProfile"] == "123"


def test_mm1171_create_execution_preserves_runtime_tier_intent(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()
    test_client.app.dependency_overrides[get_async_session] = lambda: None

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "codex_cli",
                "workflow": {
                    "title": "Preserve MM-1168 tier intent",
                    "instructions": "Run a portable tiered workflow.",
                    "runtime": {
                        "providerProfileRef": "codex-openai",
                        "profileSelector": {"providerId": "openai"},
                        "modelTier": 2,
                        "tierFallback": "clamp",
                    },
                    "steps": [
                        {
                            "id": "implement",
                            "instructions": "Implement.",
                            "runtime": {
                                "providerProfileRef": "codex-openai",
                                "profileSelector": {"providerId": "openai"},
                                "modelTier": 3,
                                "tierFallback": "strict",
                            },
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201, response.json()
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["profileSelector"] == {"providerId": "openai"}
    assert initial_parameters["runtime"]["profileSelector"] == {"providerId": "openai"}
    workflow_runtime = initial_parameters["workflow"]["runtime"]
    assert workflow_runtime["providerProfileRef"] == "codex-openai"
    assert workflow_runtime["profileSelector"] == {"providerId": "openai"}
    assert workflow_runtime["modelTier"] == 2
    assert workflow_runtime["tierFallback"] == "clamp"
    assert "requestedModel" not in workflow_runtime
    assert "model" not in workflow_runtime

    step_runtime = initial_parameters["workflow"]["steps"][0]["runtime"]
    assert step_runtime["providerProfileRef"] == "codex-openai"
    assert step_runtime["profileSelector"] == {"providerId": "openai"}
    assert step_runtime["modelTier"] == 3
    assert step_runtime["tierFallback"] == "strict"
    assert "requestedModel" not in step_runtime


def test_mm1171_create_execution_rejects_invalid_runtime_tier_intent(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()
    test_client.app.dependency_overrides[get_async_session] = lambda: None

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "codex_cli",
                "workflow": {
                    "title": "Reject bad tier",
                    "instructions": "Run a workflow.",
                    "runtime": {"modelTier": "2"},
                },
            },
        },
    )

    assert response.status_code == 422
    assert "modelTier" in response.json()["detail"]["message"]

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "codex_cli",
                "workflow": {
                    "title": "Reject bad fallback",
                    "instructions": "Run a workflow.",
                    "steps": [
                        {
                            "id": "verify",
                            "instructions": "Verify.",
                            "runtime": {"modelTier": 1, "tierFallback": "soft"},
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "tierFallback" in response.json()["detail"]["message"]

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "codex_cli",
                "workflow": {
                    "title": "Reject bad hard override audit",
                    "instructions": "Run a workflow.",
                    "runtime": {
                        "modelTier": 1,
                        "model": "gpt-x",
                        "hardOverrideAudit": "yes",
                    },
                },
            },
        },
    )

    assert response.status_code == 422
    assert "hardOverrideAudit" in response.json()["detail"]["message"]


def test_create_task_shaped_execution_preserves_preset_schedule_provenance(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "codex_cli",
                "workflow": {
                    "title": "MM-747 goal",
                    "instructions": "Complete Jira issue MM-747.",
                    "steps": [
                        {
                            "id": "step-1",
                            "title": "Implement",
                            "instructions": "Implement MM-747.",
                        }
                    ],
                    "taskTemplate": {
                        "slug": "jira-implement",
                        "scope": "global",
                    },
                    "presetSchedule": {
                        "source": "goal",
                        "reason": "jira_issue_goal",
                        "presetSlug": "jira-implement",
                        "presetDigest": "digest-1",
                        "jiraIssueKey": "MM-747",
                    },
                    "appliedStepTemplates": [
                        {
                            "slug": "jira-implement",
                            "stepIds": ["step-1"],
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    task = initial_parameters["workflow"]
    assert task["taskTemplate"] == {
        "slug": "jira-implement",
        "scope": "global",
    }
    assert task["presetSchedule"] == {
        "source": "goal",
        "reason": "jira_issue_goal",
        "presetSlug": "jira-implement",
        "presetDigest": "digest-1",
        "jiraIssueKey": "MM-747",
    }
    assert task["appliedStepTemplates"][0]["slug"] == "jira-implement"


def test_create_task_shaped_execution_preserves_steps_and_uses_step_title_defaults(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "workflow": {
                    "runtime": {
                        "mode": "claude_code",
                    },
                    "steps": [
                        {
                            "id": "tpl:demo:1:01",
                            "title": "Clarify the create-task recovery plan",
                            "instructions": "Audit the regression and list the missing controls.",
                            "skill": {
                                "id": "speckit-clarify",
                                "args": {"feature": "workflow-start"},
                                "requiredCapabilities": ["git", "github"],
                            },
                        },
                        {
                            "id": "tpl:demo:1:02",
                            "title": "Implement the restored builder",
                            "instructions": "Restore presets and multi-step submission.",
                            "runtime": {
                                "mode": "codex_cli",
                                "model": "gpt-5.4",
                                "effort": "high",
                            },
                        },
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    initial_parameters = called_kwargs["initial_parameters"]

    assert called_kwargs["title"] == "Clarify the create-task recovery plan"
    assert (
        called_kwargs["summary"]
        == "Audit the regression and list the missing controls."
    )
    assert initial_parameters["stepCount"] == 2
    assert initial_parameters["workflow"]["steps"] == [
        {
            "id": "tpl:demo:1:01",
            "title": "Clarify the create-task recovery plan",
            "instructions": "Audit the regression and list the missing controls.",
            "skill": {
                "id": "speckit-clarify",
                "inputs": {"feature": "workflow-start"},
                "requiredCapabilities": ["git", "github"],
            },
        },
        {
            "id": "tpl:demo:1:02",
            "title": "Implement the restored builder",
            "instructions": "Restore presets and multi-step submission.",
            "runtime": {
                "mode": "codex_cli",
                "model": "gpt-5.4",
                "effort": "high",
                "requestedModel": "gpt-5.4",
                "modelSource": "task_override",
            },
        },
    ]

def test_create_task_shaped_execution_preserves_recursive_preset_metadata(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "workflow": {
                    "title": "Compile recursive presets",
                    "instructions": "Run the compiled task.",
                    "runtime": {"mode": "codex_cli"},
                    "publish": {"mode": "pr"},
                    "jira": {"issueKey": "MM-630"},
                    "authoredPresets": [
                        {
                            "presetSlug": "root-preset",
                            "presetDigest": "digest-1",
                            "includePath": ["root-preset"],
                        },
                        {
                            "presetSlug": "child-preset",
                            "presetDigest": "digest-1",
                            "alias": "checks",
                            "inputMapping": {"target": "recursive presets"},
                            "includePath": [
                                "root-preset",
                                "checks:child-preset",
                            ],
                        },
                    ],
                    "appliedStepTemplates": [
                        {
                            "slug": "root-preset",
                            "stepIds": [
                                "tpl:root-preset:1:01",
                                "tpl:child-preset:1:01",
                            ],
                            "composition": {
                                "slug": "root-preset",
                                "path": ["root-preset"],
                                "stepIds": [
                                    "tpl:root-preset:1:01",
                                    "tpl:child-preset:1:01",
                                ],
                                "includes": [
                                    {
                                        "slug": "child-preset",
                                        "alias": "checks",
                                        "path": [
                                            "root-preset",
                                            "checks:child-preset",
                                        ],
                                        "stepIds": ["tpl:child-preset:1:01"],
                                    }
                                ],
                            },
                        }
                    ],
                    "steps": [
                        {
                            "id": "tpl:root-preset:1:01",
                            "title": "Prepare task",
                            "instructions": "Prepare the task context.",
                            "source": {
                                "kind": "preset-derived",
                                "presetSlug": "root-preset",
                                "presetDigest": "digest-1",
                                "includePath": ["root-preset"],
                                "originalStepId": "prepare-task",
                            },
                        },
                        {
                            "id": "tpl:child-preset:1:01",
                            "title": "Run checks",
                            "instructions": "Run recursive preset checks.",
                            "source": {
                                "kind": "preset-derived",
                                "presetSlug": "child-preset",
                                "presetDigest": "digest-1",
                                "includePath": [
                                    "root-preset",
                                    "checks:child-preset",
                                ],
                                "originalStepId": "run-checks",
                            },
                        },
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    task = service.create_execution.await_args.kwargs["initial_parameters"]["workflow"]
    assert task["steps"][0]["source"]["presetSlug"] == "root-preset"
    assert task["steps"][0]["source"]["originalStepId"] == "prepare-task"
    assert task["steps"][1]["source"]["includePath"] == [
        "root-preset",
        "checks:child-preset",
    ]
    assert task["steps"][1]["source"]["originalStepId"] == "run-checks"
    assert [preset["presetSlug"] for preset in task["authoredPresets"]] == [
        "root-preset",
        "child-preset",
    ]
    assert task["authoredPresets"][1]["inputMapping"] == {
        "target": "recursive presets"
    }
    assert task["appliedStepTemplates"][0]["composition"]["includes"][0]["alias"] == (
        "checks"
    )
    assert task["runtime"] == {"mode": "codex_cli"}
    assert task["publish"] == {"mode": "pr"}


def test_create_task_shaped_execution_preserves_manual_and_preset_step_order(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Run mixed task.",
                    "authoredPresets": [
                        {
                            "presetSlug": "parent-flow",
                            "presetDigest": "digest-1",
                            "includePath": ["parent-flow"],
                        }
                    ],
                    "steps": [
                        {
                            "id": "manual-before",
                            "type": "skill",
                            "instructions": "Manual before.",
                            "skill": {"id": "auto"},
                        },
                        {
                            "id": "tpl:parent-flow:1:01:abcdef12",
                            "type": "skill",
                            "instructions": "Preset one.",
                            "skill": {"id": "auto"},
                            "source": {
                                "kind": "preset-derived",
                                "presetSlug": "parent-flow",
                                "presetDigest": "digest-1",
                                "includePath": ["parent-flow"],
                                "originalStepId": "preset-derived-1",
                            },
                        },
                        {
                            "id": "tpl:parent-flow:1:02:abcdef12",
                            "type": "skill",
                            "instructions": "Preset two.",
                            "skill": {"id": "auto"},
                            "source": {
                                "kind": "preset-derived",
                                "presetSlug": "parent-flow",
                                "presetDigest": "digest-1",
                                "includePath": ["parent-flow"],
                                "originalStepId": "preset-derived-2",
                            },
                        },
                        {
                            "id": "manual-after",
                            "type": "skill",
                            "instructions": "Manual after.",
                            "skill": {"id": "auto"},
                        },
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    task = service.create_execution.await_args.kwargs["initial_parameters"]["workflow"]
    assert [step["id"] for step in task["steps"]] == [
        "manual-before",
        "tpl:parent-flow:1:01:abcdef12",
        "tpl:parent-flow:1:02:abcdef12",
        "manual-after",
    ]
    assert task["steps"][0].get("source") in (None, {"kind": "manual"})
    assert task["steps"][3].get("source") in (None, {"kind": "manual"})
    assert task["steps"][1]["source"]["originalStepId"] == "preset-derived-1"
    assert task["steps"][2]["source"]["originalStepId"] == "preset-derived-2"


def test_create_task_shaped_execution_preserves_detached_edited_step_source(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    detached_source = {
        "kind": "detached",
        "presetSlug": "quality-flow",
        "presetDigest": "digest-1",
        "includePath": ["root-flow", "quality-flow"],
        "originalStepId": "lint-target",
    }
    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Run edited preset step.",
                    "steps": [
                        {
                            "id": "edited-lint-step",
                            "type": "skill",
                            "instructions": "Edited lint instructions.",
                            "skill": {"id": "auto"},
                            "source": detached_source,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    task = service.create_execution.await_args.kwargs["initial_parameters"]["workflow"]
    assert task["steps"][0]["instructions"] == "Edited lint instructions."
    assert task["steps"][0]["source"] == detached_source

def test_create_task_shaped_execution_does_not_fabricate_manual_preset_metadata(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "title": "Manual task",
                    "instructions": "Run one manual step.",
                    "steps": [
                        {
                            "id": "manual-1",
                            "title": "Manual step",
                            "instructions": "Do the manual work.",
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    task = service.create_execution.await_args.kwargs["initial_parameters"]["workflow"]
    assert "authoredPresets" not in task
    assert "appliedStepTemplates" not in task
    assert "source" not in task["steps"][0]

def test_create_task_shaped_execution_rejects_pr_resolver_without_structured_selector(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Verify MM-940 and move to DONE if it is a PASS",
                    "runtime": {"mode": "claude_code"},
                    "tool": {
                        "type": "skill",
                        "name": "pr-resolver",
                    },
                    "git": {"branch": "main"},
                }
            },
        },
    )

    assert response.status_code == 422
    assert (
        response.json()["detail"]["message"]
        == "pr-resolver workflow requires a structured PR selector: "
        "payload.workflow.inputs.pr, payload.workflow.inputs.branch, "
        "payload.workflow.tool.inputs.pr/branch, or "
        "payload.workflow.git.startingBranch, or a non-default "
        "payload.workflow.git.branch."
    )
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_allows_pr_resolver_with_starting_branch(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "runtime": {"mode": "claude_code"},
                    "tool": {
                        "type": "skill",
                        "name": "pr-resolver",
                    },
                    "git": {"startingBranch": "feature/resolve-pr"},
                }
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    assert called_kwargs["title"] == "PR Resolver: feature/resolve-pr"
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["workflow"]["title"] == "PR Resolver: feature/resolve-pr"
    assert initial_parameters["workflow"]["git"] == {
        "startingBranch": "feature/resolve-pr",
    }
    assert initial_parameters["knownRefs"] == ["feature/resolve-pr"]


def test_create_task_shaped_execution_allows_pr_resolver_with_non_default_git_branch(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "runtime": {"mode": "claude_code"},
                    "tool": {
                        "type": "skill",
                        "name": "pr-resolver",
                    },
                    "git": {"branch": "feature/resolve-pr"},
                }
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    assert called_kwargs["title"] == "PR Resolver: feature/resolve-pr"
    initial_parameters = called_kwargs["initial_parameters"]
    assert initial_parameters["workflow"]["title"] == "PR Resolver: feature/resolve-pr"
    assert initial_parameters["workflow"]["git"] == {
        "branch": "feature/resolve-pr",
    }
    assert initial_parameters["knownRefs"] == ["feature/resolve-pr"]


def test_create_task_shaped_execution_allows_pr_resolver_with_numeric_pr_selector(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "runtime": {"mode": "claude_code"},
                    "tool": {
                        "type": "skill",
                        "name": "pr-resolver",
                    },
                    "inputs": {"pr": 2733},
                }
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["workflow"]["inputs"] == {"pr": 2733}


def test_create_task_shaped_execution_synthesizes_generic_title(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "title": "Run",
                    "runtime": {"mode": "claude_code"},
                    "tool": {
                        "type": "skill",
                        "name": "jira-verify",
                        "inputs": {"issueKey": "KANDY-123"},
                    },
                }
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    assert called_kwargs["title"] == "Jira Verify: KANDY-123"
    initial_parameters = called_kwargs["initial_parameters"]
    assert initial_parameters["workflow"]["title"] == "Jira Verify: KANDY-123"


def test_create_task_shaped_execution_enriches_github_issue_preset_title(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "title": "GitHub Issue Implement",
                    "instructions": "Implement the selected GitHub issue.",
                    "runtime": {"mode": "claude_code"},
                    "appliedStepTemplates": [
                        {
                            "slug": "github-issue-implement",
                            "stepIds": ["step-1"],
                        }
                    ],
                    "tool": {
                        "type": "skill",
                        "name": "github.load_issue_preset_brief",
                    },
                    "steps": [
                        {
                            "id": "step-1",
                            "title": "Load GitHub issue brief",
                            "instructions": "Load the selected GitHub issue.",
                            "tool": {
                                "type": "skill",
                                "name": "github.load_issue_preset_brief",
                            },
                        }
                    ],
                    "inputs": {
                        "github_issue": {
                            "repository": "MoonLadderStudios/MoonMind",
                            "number": 3143,
                            "title": "Improve generated workflow titles",
                        },
                        "github_issue_ref": "MoonLadderStudios/MoonMind#3143",
                    },
                }
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    expected = (
        "GitHub Issue Implement: MoonLadderStudios/MoonMind#3143 — "
        "Improve generated workflow titles"
    )
    assert called_kwargs["title"] == expected
    assert called_kwargs["initial_parameters"]["workflow"]["title"] == expected


def test_create_task_shaped_execution_synthesizes_issue_pr_title_without_repo(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "title": "Run",
                    "runtime": {"mode": "claude_code"},
                    "repository": "MoonLadderStudios/MoonMind",
                    "tool": {
                        "type": "skill",
                        "name": "jira-pr-verify",
                        "inputs": {
                            "issueKey": "KANDY-123",
                            "pullRequestUrl": "https://github.com/org/repo/pull/456",
                            "repository": "org/repo",
                        },
                    },
                }
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    assert called_kwargs["title"] == "Jira PR Verify: KANDY-123 — PR #456"
    initial_parameters = called_kwargs["initial_parameters"]
    assert (
        initial_parameters["workflow"]["title"]
        == "Jira PR Verify: KANDY-123 — PR #456"
    )


def test_create_task_shaped_execution_keeps_meaningful_title_after_synthesis(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "title": "Verify auth redirect fix",
                    "runtime": {"mode": "claude_code"},
                    "tool": {
                        "type": "skill",
                        "name": "jira-pr-verify",
                        "inputs": {
                            "issueKey": "KANDY-123",
                            "pullRequestUrl": "https://github.com/org/repo/pull/456",
                        },
                    },
                }
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    assert called_kwargs["title"] == "Verify auth redirect fix"
    initial_parameters = called_kwargs["initial_parameters"]
    assert initial_parameters["workflow"]["title"] == "Verify auth redirect fix"


def _pentest_workflow_payload(
    *,
    tool_inputs: dict[str, object] | None = None,
    step_inputs: dict[str, object] | None = None,
    authorized_principals: list[str] | None = None,
) -> dict[str, object]:
    safe_inputs: dict[str, object] = {
        "target": "https://lab.example.test",
        "scope_artifact_ref": "art_scope_valid",
        "objective": "Recon only",
        "operation_mode": "recon_only",
        "runner_profile_id": "pentestgpt-claude-oauth",
        "execution_profile_ref": "pentestgpt_claude_oauth",
        "time_budget_minutes": 60,
        "evidence_level": "standard",
    }
    if authorized_principals is not None:
        safe_inputs["approved_scope"] = {
            "authorized_principals": authorized_principals,
        }
    if tool_inputs:
        safe_inputs.update(tool_inputs)
    step_safe_inputs = dict(safe_inputs)
    if step_inputs:
        step_safe_inputs.update(step_inputs)
    return {
        "type": "workflow",
        "payload": {
            "workflow": {
                "title": "Pentest recon",
                "instructions": "Run the approved pentest.",
                "tool": {
                    "type": "skill",
                    "name": "security.pentest.run",
                    "inputs": safe_inputs,
                },
                "steps": [
                    {
                        "id": "step-pentest",
                        "type": "tool",
                        "instructions": "Run the curated pentest tool.",
                        "tool": {
                            "type": "skill",
                            "name": "security.pentest.run",
                            "inputs": step_safe_inputs,
                        },
                    }
                ],
            }
        },
    }

def test_create_task_shaped_execution_rejects_disabled_pentest_submission(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, user = client
    user.roles = ["admin"]
    monkeypatch.setattr(settings.pentest, "enabled", False)

    response = test_client.post("/api/executions", json=_pentest_workflow_payload())

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_request"
    assert "disabled" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

@pytest.mark.parametrize("role", ["admin", "security_operator"])
def test_create_task_shaped_execution_accepts_authorized_pentest_roles(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    test_client, service, user = client
    user.roles = [role]
    monkeypatch.setattr(settings.pentest, "enabled", True)
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post("/api/executions", json=_pentest_workflow_payload())

    assert response.status_code == 201
    workflow = service.create_execution.await_args.kwargs["initial_parameters"][
        "workflow"
    ]
    assert workflow["tool"] == {
        "type": "skill",
        "name": "security.pentest.run",
        "inputs": {
            "target": "https://lab.example.test",
            "scope_artifact_ref": "art_scope_valid",
            "objective": "Recon only",
            "operation_mode": "recon_only",
            "runner_profile_id": "pentestgpt-claude-oauth",
            "execution_profile_ref": "pentestgpt_claude_oauth",
            "time_budget_minutes": 60,
            "evidence_level": "standard",
        },
    }

@pytest.mark.parametrize("roles", [[], [""], ["developer"], ["viewer", "auditor"]])
def test_create_task_shaped_execution_rejects_non_security_pentest_roles(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    roles: list[str],
) -> None:
    test_client, service, user = client
    user.roles = roles
    monkeypatch.setattr(settings.pentest, "enabled", True)

    response = test_client.post("/api/executions", json=_pentest_workflow_payload())

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_request"
    assert "admin or security_operator" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_rejects_scope_member_without_pentest_role(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, user = client
    user.roles = ["developer"]
    monkeypatch.setattr(settings.pentest, "enabled", True)

    response = test_client.post(
        "/api/executions",
        json=_pentest_workflow_payload(authorized_principals=[str(user.id)]),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_request"
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_rejects_pentest_privileged_overrides(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, user = client
    user.roles = ["security_operator"]
    monkeypatch.setattr(settings.pentest, "enabled", True)

    response = test_client.post(
        "/api/executions",
        json=_pentest_workflow_payload(
            tool_inputs={
                "image": "unsafe:latest",
                "provider_secret": "secret-value",
                "env": {"TOKEN": "secret-value"},
                "network": "host",
                "mounts": ["/:/host"],
                "capabilities": ["SYS_ADMIN"],
                "raw_command": "sh -lc id",
            }
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_request"
    message = response.json()["detail"]["message"]
    assert "privileged" in message
    assert "secret-value" not in message

def test_create_task_shaped_execution_rejects_pentest_provider_runtime_state_override(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, user = client
    user.roles = ["security_operator"]
    monkeypatch.setattr(settings.pentest, "enabled", True)

    response = test_client.post(
        "/api/executions",
        json=_pentest_workflow_payload(
            tool_inputs={
                "provider_runtime_state": {
                    "pentestgpt_claude_oauth": {"available_slots": 100}
                }
            }
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_request"
    assert "provider_runtime_state" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_pentest_role_resolution_reads_object_values_and_request_claims() -> None:
    request = SimpleNamespace(
        state=SimpleNamespace(
            claims={
                "realm_access": {"roles": ["security_operator"]},
                "resource_access": {
                    "moonmind": {"roles": ["workflow_submitter"]},
                },
            }
        ),
        scope={},
    )
    user = SimpleNamespace(
        is_superuser=False,
        roles=[SimpleNamespace(name="viewer")],
        groups=[SimpleNamespace(role="auditor")],
    )

    assert _effective_user_roles(user, request=request) == {
        "auditor",
        "security_operator",
        "viewer",
        "workflow_submitter",
    }

def test_pentest_safe_preset_exposes_only_ordinary_inputs() -> None:
    import yaml

    preset_path = Path("api_service/data/presets/pentest-recon.yaml")
    preset = yaml.safe_load(preset_path.read_text())
    schema_props = set(preset["annotations"]["inputSchema"]["properties"])
    exposed_inputs = {item["name"] for item in preset["inputs"]}
    required_inputs = {
        item["name"] for item in preset["inputs"] if item.get("required") is True
    }
    step_inputs = preset["steps"][0]["tool"]["inputs"]
    privileged = {
        "image",
        "provider_secret",
        "provider_secret_ref",
        "env",
        "environment",
        "network",
        "mount",
        "mounts",
        "capability",
        "capabilities",
        "command",
        "raw_command",
    }

    assert schema_props == {
        "target",
        "scope_artifact_ref",
        "objective",
        "operation_mode",
        "evidence_level",
        "time_budget_minutes",
        "execution_profile_ref",
    }
    assert exposed_inputs == schema_props
    assert preset["annotations"]["inputSchema"]["required"] == ["target"]
    assert required_inputs == {"target"}
    assert preset["annotations"]["uiSchema"]["scope_artifact_ref"]["advanced"] is True
    assert preset["annotations"]["uiSchema"]["operation_mode"]["advanced"] is True
    assert privileged.isdisjoint(schema_props)
    assert privileged.isdisjoint(exposed_inputs)
    assert step_inputs["runner_profile_id"] == "pentestgpt-claude-oauth"
    assert privileged.isdisjoint(step_inputs)

def test_create_task_shaped_submit_accepts_task_payload_pr_resolver(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "task",
            "payload": {
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "claude_code",
                "task": {
                    "instructions": "Resolve the current branch PR.",
                    "runtime": {"mode": "claude_code"},
                    "publish": {"mode": "none"},
                    "skills": {"include": [{"name": "pr-resolver"}]},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["requestType"] == "task"
    assert initial_parameters["workflow"]["skills"] == {
        "include": [{"name": "pr-resolver"}]
    }
    assert initial_parameters["workflow"]["publish"] == {"mode": "none"}

def test_create_task_shaped_execution_inherits_caller_runtime(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.create_execution.return_value = _build_execution_record()
    service.describe_execution.return_value = SimpleNamespace(
        workflow_id="mm:parent-task",
        owner_id=str(user.id),
        parameters={
            "targetRuntime": "codex",
            "model": "gpt-5.4",
            "effort": "high",
            "workflow": {
                "runtime": {
                    "executionProfileRef": "codex_default",
                }
            },
        },
        memo={},
        search_attributes={},
    )

    response = test_client.post(
        "/api/executions",
        headers={
            "X-MoonMind-Task-Workflow-Id": "mm:parent-task",
            "X-MoonMind-Agent-Run-Identifier": "agent-run-1",
        },
        json={
            "type": "workflow",
            "payload": {
                "runtimeInheritance": "caller",
                "repository": "MoonLadderStudios/MoonMind",
                "requiredCapabilities": ["gh"],
                "workflow": {
                    "title": "feature/example",
                    "instructions": "Resolve PR #42 on branch `feature/example`.",
                    "skill": {"name": "pr-resolver"},
                    "inputs": {"repo": "MoonLadderStudios/MoonMind", "pr": "42"},
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["targetRuntime"] == "codex_cli"
    assert initial_parameters["model"] == "gpt-5.4"
    assert initial_parameters["effort"] == "high"
    runtime = initial_parameters["workflow"]["runtime"]
    assert runtime == {
        "mode": "codex_cli",
        "model": "gpt-5.4",
        "effort": "high",
        "executionProfileRef": "codex_default",
    }


def test_create_task_shaped_execution_rejects_caller_inheritance_for_user(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "runtimeInheritance": "caller",
                "workflow": {
                    "title": "feature/example",
                    "instructions": "Resolve PR #42 on branch `feature/example`.",
                    "skill": {"name": "pr-resolver"},
                    "inputs": {"repo": "MoonLadderStudios/MoonMind", "pr": "42"},
                },
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "runtime_inheritance_requires_workflow_principal",
        "message": 'runtimeInheritance="caller" requires a workflow-scoped principal.',
    }
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_forwards_input_attachments(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MM-367: objective and step attachment refs reach MoonMind.UserWorkflow parameters."""

    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    execute = AsyncMock(
        return_value=_ExecuteResult(
            [
                SimpleNamespace(
                    artifact_id="art_01OBJECTIVEINPUT00000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=10,
                ),
                SimpleNamespace(
                    artifact_id="art_01STEPINPUT000000000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=20,
                ),
            ]
        )
    )
    test_client.app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        execute=execute
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "workflow": {
                    "instructions": "Inspect submitted screenshots.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01OBJECTIVEINPUT00000000",
                            "filename": "same-name.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                    "steps": [
                        {
                            "instructions": "Inspect the step screenshot.",
                            "inputAttachments": [
                                {
                                    "artifactId": "art_01STEPINPUT000000000000",
                                    "filename": "same-name.png",
                                    "contentType": "image/png",
                                    "sizeBytes": 20,
                                }
                            ],
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["workflow"]["inputAttachments"] == [
        {
            "artifactId": "art_01OBJECTIVEINPUT00000000",
            "filename": "same-name.png",
            "contentType": "image/png",
            "sizeBytes": 10,
        }
    ]
    assert initial_parameters["workflow"]["steps"][0]["inputAttachments"] == [
        {
            "artifactId": "art_01STEPINPUT000000000000",
            "filename": "same-name.png",
            "contentType": "image/png",
            "sizeBytes": 20,
        }
    ]
    assert initial_parameters["workflow"]["steps"][0]["id"] == "step-1"

def test_create_task_shaped_execution_normalizes_snake_case_input_attachments(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MM-367: router normalization accepts Pydantic field-name aliases."""

    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    execute = AsyncMock(
        return_value=_ExecuteResult(
            [
                SimpleNamespace(
                    artifact_id="art_01OBJECTIVEINPUT00000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=10,
                ),
                SimpleNamespace(
                    artifact_id="art_01STEPINPUT000000000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=20,
                ),
            ]
        )
    )
    test_client.app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        execute=execute
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "workflow": {
                    "instructions": "Inspect submitted screenshots.",
                    "input_attachments": [
                        {
                            "artifactId": "art_01OBJECTIVEINPUT00000000",
                            "filename": "objective.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                    "steps": [
                        {
                            "instructions": "Inspect the step screenshot.",
                            "input_attachments": [
                                {
                                    "artifactId": "art_01STEPINPUT000000000000",
                                    "filename": "step.png",
                                    "contentType": "image/png",
                                    "sizeBytes": 20,
                                }
                            ],
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["workflow"]["inputAttachments"] == [
        {
            "artifactId": "art_01OBJECTIVEINPUT00000000",
            "filename": "objective.png",
            "contentType": "image/png",
            "sizeBytes": 10,
        }
    ]
    step_payload = initial_parameters["workflow"]["steps"][0]
    assert step_payload["id"] == "step-1"
    assert step_payload["inputAttachments"] == [
        {
            "artifactId": "art_01STEPINPUT000000000000",
            "filename": "step.png",
            "contentType": "image/png",
            "sizeBytes": 20,
        }
    ]
    assert "input_attachments" not in step_payload

def test_create_task_shaped_execution_uses_nonblank_step_id_fallback(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    execute = AsyncMock(
        return_value=_ExecuteResult(
            [
                SimpleNamespace(
                    artifact_id="art_01STEPINPUT000000000000",
                    status=TemporalArtifactStatus.COMPLETE,
                    content_type="image/png",
                    size_bytes=20,
                ),
            ]
        )
    )
    test_client.app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        execute=execute
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "workflow": {
                    "instructions": "Inspect submitted screenshot.",
                    "steps": [
                        {
                            "id": "   ",
                            "stepRef": "review-step",
                            "instructions": "Inspect the step screenshot.",
                            "inputAttachments": [
                                {
                                    "artifactId": "art_01STEPINPUT000000000000",
                                    "filename": "step.png",
                                    "contentType": "image/png",
                                    "sizeBytes": 20,
                                }
                            ],
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert initial_parameters["workflow"]["steps"][0]["id"] == "review-step"

def test_create_task_shaped_execution_preserves_canonical_mm627_task_shape(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            SimpleNamespace(
                artifact_id="art_01MM627OBJECTIVE000000000",
                status=TemporalArtifactStatus.COMPLETE,
                content_type="image/png",
                size_bytes=10,
            ),
            SimpleNamespace(
                artifact_id="art_01MM627STEP00000000000000",
                status=TemporalArtifactStatus.COMPLETE,
                content_type="image/png",
                size_bytes=20,
            ),
        ]
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "workflow": {
                    "title": "MM-627 canonical task payload",
                    "instructions": "Preserve the submitted task exactly.",
                    "dependsOn": ["mm:dep-1"],
                    "runtime": {
                        "mode": "codex",
                        "model": "gpt-5-codex",
                        "effort": "high",
                    },
                    "publish": {"mode": "pr", "baseBranch": "main"},
                    "git": {"branch": "feature/mm-627"},
                    "inputAttachments": [
                        {
                            "artifactId": "art_01MM627OBJECTIVE000000000",
                            "filename": "objective.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                    "steps": [
                        {
                            "id": "step-1",
                            "title": "Inspect step",
                            "instructions": "Inspect the step screenshot.",
                            "source": {
                                "kind": "jira",
                                "issueKey": "MM-627",
                            },
                            "storyOutput": {"mode": "jira"},
                            "jiraOrchestration": {
                                "issueKey": "MM-627",
                                "preset": "jira-orchestrate",
                            },
                            "inputAttachments": [
                                {
                                    "artifactId": "art_01MM627STEP00000000000000",
                                    "filename": "step.png",
                                    "contentType": "image/png",
                                    "sizeBytes": 20,
                                }
                            ],
                        }
                    ],
                    "storyOutput": {"mode": "jira"},
                    "authoredPresets": [
                        {"slug": "jira-orchestrate"}
                    ],
                    "appliedStepTemplates": [
                        {
                            "slug": "jira-implementation",
                            "stepIds": ["step-1"],
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    initial_parameters = service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    task = initial_parameters["workflow"]
    assert task["git"] == {"branch": "feature/mm-627"}
    assert task["runtime"] == {
        "mode": "codex_cli",
        "model": "gpt-5-codex",
        "effort": "high",
    }
    assert task["publish"]["mode"] == "pr"
    assert task["dependsOn"] == ["mm:dep-1"]
    assert task["storyOutput"] == {"mode": "jira"}
    assert task["authoredPresets"] == [{"slug": "jira-orchestrate"}]
    assert task["appliedStepTemplates"] == [
        {
            "slug": "jira-implementation",
            "stepIds": ["step-1"],
        }
    ]
    assert task["inputAttachments"][0]["artifactId"] == "art_01MM627OBJECTIVE000000000"
    assert task["steps"][0]["id"] == "step-1"
    assert task["steps"][0]["source"] == {"kind": "jira", "issueKey": "MM-627"}
    assert task["steps"][0]["inputAttachments"][0]["artifactId"] == (
        "art_01MM627STEP00000000000000"
    )

@pytest.mark.parametrize(
    "task_payload",
    [
        {"instructions": "Run task.", "git": {"targetBranch": "feature/legacy"}},
        {"instructions": "Run task.", "targetBranch": "feature/legacy"},
    ],
)
def test_create_task_shaped_execution_rejects_legacy_target_branch_aliases(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    task_payload: dict[str, Any],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "workflow": task_payload,
            },
        },
    )

    assert response.status_code == 422
    assert "targetBranch" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_rejects_non_string_repository(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": {"owner": "Moon", "name": "Mind"},
                "targetRuntime": "codex",
                "workflow": {"instructions": "Run task."},
            },
        },
    )

    assert response.status_code == 422
    assert "repository" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_rejects_attachment_declared_for_multiple_targets(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            SimpleNamespace(
                artifact_id="art_01MM627DUPLICATE00000000",
                status=TemporalArtifactStatus.COMPLETE,
                content_type="image/png",
                size_bytes=10,
            )
        ]
    )

    attachment = {
        "artifactId": "art_01MM627DUPLICATE00000000",
        "filename": "same.png",
        "contentType": "image/png",
        "sizeBytes": 10,
    }
    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "workflow": {
                    "instructions": "Run task.",
                    "inputAttachments": [attachment],
                    "steps": [
                        {
                            "id": "step-1",
                            "instructions": "Run step.",
                            "inputAttachments": [attachment],
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "declared more than once" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_duplicate_attachment_declaration_for_same_target(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            SimpleNamespace(
                artifact_id="art_01MM627DUPLICATE00000001",
                status=TemporalArtifactStatus.COMPLETE,
                content_type="image/png",
                size_bytes=10,
            )
        ]
    )

    attachment = {
        "artifactId": "art_01MM627DUPLICATE00000001",
        "filename": "same.png",
        "contentType": "image/png",
        "sizeBytes": 10,
    }
    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "workflow": {
                    "instructions": "Run task.",
                    "inputAttachments": [attachment, attachment],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "declared more than once" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


@pytest.mark.parametrize(
    ("artifact_status", "message_fragment"),
    [
        (TemporalArtifactStatus.PENDING_UPLOAD, "pending_upload"),
        (TemporalArtifactStatus.FAILED, "failed"),
        (TemporalArtifactStatus.DELETED, "deleted"),
    ],
)
def test_create_task_shaped_execution_rejects_unfinalized_input_attachment_refs(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    artifact_status: TemporalArtifactStatus,
    message_fragment: str,
) -> None:
    """MM-628: binary input refs must be finalized before execution creation."""

    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    artifact_id = f"art_01MM628{artifact_status.value.upper():0<18}"[:30]
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            SimpleNamespace(
                artifact_id=artifact_id,
                status=artifact_status,
                content_type="image/png",
                size_bytes=10,
                created_by_principal=str(_user.id),
            )
        ]
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "workflow": {
                    "instructions": "Review binary input.",
                    "inputAttachments": [
                        {
                            "artifactId": artifact_id,
                            "filename": "input.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert message_fragment in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_missing_input_attachment_artifact(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session([])

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "workflow": {
                    "instructions": "Review binary input.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01MM628MISSING000000000",
                            "filename": "input.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "was not found" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_other_users_completed_input_attachment_ref(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MM-628: another user's completed artifact cannot be attached to a new execution."""

    test_client, service, user = client
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak")
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    service.create_execution.return_value = _build_execution_record()
    artifact_id = "art_01MM628WRONGOWNER0000000"
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            _completed_attachment_artifact(
                artifact_id,
                created_by_principal=f"other-{user.id}",
            )
        ]
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "workflow": {
                    "instructions": "Review binary input.",
                    "inputAttachments": [
                        {
                            "artifactId": artifact_id,
                            "filename": "input.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "not authorized" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_service_owned_attachment_for_user(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MM-628: service ownership does not make an artifact attachable by any user."""

    test_client, service, _user = client
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak")
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    artifact_id = "art_01MM628SERVICEOWNER0000"
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            _completed_attachment_artifact(
                artifact_id,
                created_by_principal="service:artifact-generator",
            )
        ]
    )

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "workflow": {
                    "instructions": "Review binary input.",
                    "inputAttachments": [
                        {
                            "artifactId": artifact_id,
                            "filename": "input.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert "not authorized" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_create_task_shaped_execution_rejects_embedded_attachment_data(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    """MM-367: workflow-shaped submit rejects inline image payloads in refs."""

    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "repository": "Moon/Mind",
                "targetRuntime": "codex",
                "workflow": {
                    "instructions": "Inspect submitted screenshot.",
                    "inputAttachments": [
                        {
                            "artifactId": "art_01INLINEINPUT0000000000",
                            "filename": "inline.png",
                            "contentType": "image/png",
                            "sizeBytes": 10,
                            "dataUrl": "data:image/png;base64,AAAA",
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_request"
    assert "unsupported fields" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()

def test_create_task_shaped_execution_derives_pr_resolver_title_from_tool_inputs(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": {
                    "runtime": {"mode": "claude_code"},
                    "tool": {
                        "type": "skill",
                        "name": "PR-Resolver",
                        "inputs": {"startingBranch": "feature/from-tool-inputs"},
                    },
                }
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    assert called_kwargs["title"] == "PR Resolver: feature/from-tool-inputs"
    initial_parameters = called_kwargs["initial_parameters"]
    assert initial_parameters["workflow"]["title"] == "PR Resolver: feature/from-tool-inputs"

def test_create_task_shaped_execution_once_schedule_sets_start_delay(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    scheduled_for = datetime.now(UTC) + timedelta(minutes=5)
    record = _build_execution_record(state=MoonMindWorkflowState.SCHEDULED)
    record.scheduled_for = scheduled_for
    service.create_execution.return_value = record

    response = test_client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "schedule": {
                    "mode": "once",
                    "scheduledFor": scheduled_for.isoformat(),
                },
                "workflow": {
                    "instructions": "Run this later",
                },
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    assert called_kwargs["scheduled_for"] == scheduled_for
    start_delay = called_kwargs["start_delay"]
    assert start_delay is not None
    assert 200 <= start_delay.total_seconds() <= 300
    assert response.json()["scheduledFor"] is not None

def test_create_task_shaped_recurring_schedule_normalizes_proposal_intent(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override
    next_run_at = datetime.now(UTC) + timedelta(hours=1)

    with patch(
        "api_service.services.recurring_workflows_service.RecurringWorkflowsService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            return_value=SimpleNamespace(
                id=uuid4(),
                name="Inline schedule",
                cron="0 * * * *",
                timezone="UTC",
                next_run_at=next_run_at,
            )
        )

        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "proposeTasks": True,
                    "proposalPolicy": {"targets": ["moonmind"]},
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                    },
                    "workflow": {
                        "instructions": "Run this on a schedule",
                        "proposeTasks": True,
                        "proposalPolicy": {
                            "targets": ["workflow_repo"],
                            "defaultRuntime": "claude_code",
                        },
                    },
                },
            },
        )

    assert response.status_code == 201, response.json()
    target = service.create_definition.await_args.kwargs["target"]
    assert target["workflowType"] == "MoonMind.UserWorkflow"
    stored_payload = target["initialParameters"]
    assert "schedule" not in stored_payload
    assert "proposeTasks" not in stored_payload
    assert "proposalPolicy" not in stored_payload
    assert stored_payload["workflow"]["proposeTasks"] is True
    assert stored_payload["workflow"]["proposalPolicy"] == {
        "targets": ["workflow_repo"],
        "defaultRuntime": "claude_code",
    }

def test_create_task_shaped_recurring_schedule_uses_root_proposal_fallbacks(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override
    next_run_at = datetime.now(UTC) + timedelta(hours=1)

    with patch(
        "api_service.services.recurring_workflows_service.RecurringWorkflowsService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            return_value=SimpleNamespace(
                id=uuid4(),
                name="Inline schedule",
                cron="0 * * * *",
                timezone="UTC",
                next_run_at=next_run_at,
            )
        )

        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "proposeTasks": True,
                    "proposalPolicy": {"targets": ["moonmind"]},
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                    },
                    "workflow": {
                        "instructions": "Run this on a schedule",
                    },
                },
            },
        )

    assert response.status_code == 201, response.json()
    target = service.create_definition.await_args.kwargs["target"]
    assert target["workflowType"] == "MoonMind.UserWorkflow"
    stored_payload = target["initialParameters"]
    assert "schedule" not in stored_payload
    assert "proposeTasks" not in stored_payload
    assert "proposalPolicy" not in stored_payload
    assert stored_payload["workflow"]["proposeTasks"] is True
    assert stored_payload["workflow"]["proposalPolicy"] == {"targets": ["moonmind"]}


def test_create_task_shaped_recurring_schedule_preserves_runtime_selection(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override
    next_run_at = datetime.now(UTC) + timedelta(hours=1)

    with patch(
        "api_service.services.recurring_workflows_service.RecurringWorkflowsService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            return_value=SimpleNamespace(
                id=uuid4(),
                name="Dependabot schedule",
                cron="0 * * * *",
                timezone="UTC",
                next_run_at=next_run_at,
            )
        )

        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "targetRuntime": "codex",
                    "providerProfile": "codex-profile",
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                    },
                    "workflow": {
                        "instructions": "Resolve Dependabot PRs.",
                        "skill": {"name": "batch-dependabot-resolver"},
                        "runtime": {
                            "mode": "codex",
                            "model": "gpt-5.3-codex-spark",
                            "effort": "xhigh",
                            "profileId": "   ",
                        },
                    },
                },
            },
        )

    assert response.status_code == 201, response.json()
    target = service.create_definition.await_args.kwargs["target"]
    stored_payload = target["initialParameters"]
    stored_runtime = stored_payload["workflow"]["runtime"]

    assert stored_payload["targetRuntime"] == "codex_cli"
    assert stored_payload["model"] == "gpt-5.3-codex-spark"
    assert stored_payload["requestedModel"] == "gpt-5.3-codex-spark"
    assert stored_payload["modelSource"] == "task_override"
    assert stored_payload["effort"] == "xhigh"
    assert stored_payload["profileId"] == "codex-profile"
    assert stored_runtime["mode"] == "codex_cli"
    assert stored_runtime["model"] == "gpt-5.3-codex-spark"
    assert stored_runtime["effort"] == "xhigh"
    assert stored_runtime["profileId"] == "codex-profile"
    assert stored_runtime["providerProfile"] == "codex-profile"


def test_create_task_shaped_recurring_schedule_rejects_invalid_step_runtime_tier(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override

    with patch(
        "api_service.services.recurring_workflows_service.RecurringWorkflowsService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock()

        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "targetRuntime": "codex",
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                    },
                    "workflow": {
                        "instructions": "Resolve Dependabot PRs.",
                        "runtime": {"mode": "codex"},
                        "steps": [
                            {
                                "id": "bad-tier",
                                "instructions": "Run invalid tier.",
                                "runtime": {"modelTier": "2"},
                            }
                        ],
                    },
                },
            },
        )

    assert response.status_code == 422
    assert "payload.workflow.steps[0].runtime.modelTier" in response.json()["detail"]["message"]
    service.create_definition.assert_not_awaited()


def test_create_task_shaped_recurring_schedule_passes_metadata_and_response(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override
    definition_id = uuid4()
    next_run_at = datetime.now(UTC) + timedelta(hours=1)
    policy = {"overlap": {"mode": "skip"}}

    with patch(
        "api_service.services.recurring_workflows_service.RecurringWorkflowsService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            return_value=SimpleNamespace(
                id=definition_id,
                name="Nightly workflow",
                cron="0 2 * * *",
                timezone="America/New_York",
                next_run_at=next_run_at,
            )
        )

        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "schedule": {
                        "mode": "recurring",
                        "name": "Nightly workflow",
                        "description": "Run overnight",
                        "enabled": False,
                        "cron": "0 2 * * *",
                        "timezone": "America/New_York",
                        "scopeType": "personal",
                        "policy": policy,
                    },
                    "workflow": {
                        "instructions": "Run this on a schedule",
                    },
                },
            },
        )

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["scheduled"] is True
    assert body["definitionId"] == str(definition_id)
    assert body["name"] == "Nightly workflow"
    assert body["cron"] == "0 2 * * *"
    assert body["timezone"] == "America/New_York"
    assert body["redirectPath"] == f"/schedules/{definition_id}"
    called_kwargs = service.create_definition.await_args.kwargs
    assert called_kwargs["description"] == "Run overnight"
    assert called_kwargs["enabled"] is False
    assert called_kwargs["scope_type"] == "personal"
    assert called_kwargs["policy"] == policy


def test_create_recurring_schedule_accepts_snake_case_target_aliases() -> None:
    target = _build_recurring_target(
        {
            "workflow_type": "MoonMind.ManifestIngest",
            "schedule": {
                "mode": "recurring",
                "cron": "0 * * * *",
            },
            "manifest_ref": "artifact://manifest/1",
            "failure_policy": "best_effort",
            "initial_parameters": {
                "action": "plan",
                "options": {"dryRun": True},
            },
        }
    )

    assert target["workflowType"] == "MoonMind.ManifestIngest"
    assert target["manifestArtifactRef"] == "artifact://manifest/1"
    assert target["failurePolicy"] == "best_effort"
    assert target["initialParameters"] == {
        "action": "plan",
        "options": {"dryRun": True},
    }


def test_recurring_target_preserves_omnigent_selection_in_initial_parameters() -> None:
    target = _build_recurring_target(
        {
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {
                "workflow": {
                    "instructions": "Run nightly",
                    "runtime": {
                        "mode": "omnigent",
                        "executionProfileRef": "codex-oauth-profile",
                    },
                }
            },
            "omnigent": {
                "executionTargetRef": "on-demand-docker",
                "launchPolicyRef": "codex-on-demand@1",
            },
        },
        runtime_metadata={"targetRuntime": "omnigent"},
    )

    assert target["initialParameters"]["targetRuntime"] == "omnigent"
    assert target["initialParameters"]["omnigent"] == {
        "executionTargetRef": "on-demand-docker",
        "launchPolicyRef": "codex-on-demand@1",
    }
    assert target["initialParameters"]["workflow"]["runtime"] == {
        "mode": "omnigent",
        "executionProfileRef": "codex-oauth-profile",
    }


def test_create_task_shaped_recurring_schedule_lifts_snake_case_artifact_aliases(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override
    next_run_at = datetime.now(UTC) + timedelta(hours=1)

    with patch(
        "api_service.services.recurring_workflows_service.RecurringWorkflowsService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            return_value=SimpleNamespace(
                id=uuid4(),
                name="Inline schedule",
                cron="0 * * * *",
                timezone="UTC",
                next_run_at=next_run_at,
            )
        )

        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "input_artifact_ref": "artifact://input/1",
                    "plan_artifact_ref": "artifact://plan/1",
                    "failure_policy": "fail_fast",
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                    },
                    "workflow": {"instructions": "Run this on a schedule"},
                },
            },
        )

    assert response.status_code == 201, response.json()
    target = service.create_definition.await_args.kwargs["target"]
    assert target["inputArtifactRef"] == "artifact://input/1"
    assert target["planArtifactRef"] == "artifact://plan/1"
    assert target["failurePolicy"] == "fail_fast"


def test_create_task_shaped_recurring_schedule_preserves_missing_policy(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override
    next_run_at = datetime.now(UTC) + timedelta(hours=1)

    with patch(
        "api_service.services.recurring_workflows_service.RecurringWorkflowsService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            return_value=SimpleNamespace(
                id=uuid4(),
                name="Inline schedule",
                cron="0 * * * *",
                timezone="UTC",
                next_run_at=next_run_at,
            )
        )

        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                    },
                    "workflow": {
                        "instructions": "Run this on a schedule",
                    },
                },
            },
        )

    assert response.status_code == 201, response.json()
    assert service.create_definition.await_args.kwargs["policy"] is None


def test_create_task_shaped_recurring_schedule_rejects_global_scope_for_non_operator(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override

    with patch(
        "api_service.services.recurring_workflows_service.RecurringWorkflowsService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock()

        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                        "scopeType": "global",
                    },
                    "workflow": {
                        "instructions": "Run this on a schedule",
                    },
                },
            },
        )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "operator_role_required",
        "message": "Operator privileges are required for global schedules.",
    }
    service.create_definition.assert_not_awaited()


def test_create_task_shaped_recurring_schedule_validation_maps_to_422(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, _service, _user = client
    test_client.app.dependency_overrides[get_async_session] = _empty_session_override

    with patch(
        "api_service.services.recurring_workflows_service.RecurringWorkflowsService"
    ) as service_cls:
        service = service_cls.return_value
        service.create_definition = AsyncMock(
            side_effect=RecurringWorkflowValidationError(
                "target.workflowType is required"
            )
        )

        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "schedule": {
                        "mode": "recurring",
                        "cron": "0 * * * *",
                    },
                    "workflow": {
                        "instructions": "Run this on a schedule",
                    },
                },
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_recurring_workflow",
        "message": "target.workflowType is required",
    }

def test_create_execution_surfaces_domain_validation_errors(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.side_effect = TemporalExecutionValidationError(
        "Unsupported workflow type: MoonMind.Unknown"
    )

    response = test_client.post(
        "/api/executions",
        json={"workflowType": "MoonMind.Unknown"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_request"


def test_create_execution_rejects_user_workflow_without_plan_source(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.post(
        "/api/executions",
        json={
            "workflowType": "MoonMind.UserWorkflow",
            "title": "Run",
            "initialParameters": {},
        },
    )

    assert response.status_code == 422
    service.create_execution.assert_not_awaited()


def test_create_execution_routes_directly_to_temporal(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    record = _build_execution_record()
    record.memo["title"] = "Test direct temporal"
    service.create_execution.return_value = record

    response = test_client.post(
        "/api/executions",
        json={
            "workflowType": "MoonMind.UserWorkflow",
            "title": "Test direct temporal",
            "initialParameters": {
                "workflow": {"instructions": "Test direct temporal"}
            },
        },
    )

    assert response.status_code == 201
    assert response.json()["title"] == "Test direct temporal"
    service.create_execution.assert_awaited_once()

def test_create_execution_persists_task_input_snapshot_for_direct_run_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    record = _build_execution_record(has_workflow_input_snapshot=False)
    record.parameters = {
        "repository": "Moon/Mind",
        "targetRuntime": "codex_cli",
        "workflow": {
            "title": "Direct run",
            "instructions": "Implement the persisted direct run.",
        },
    }
    service.create_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=False)
    session = AsyncMock()
    app.dependency_overrides[get_async_session] = lambda: session
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard, "temporal_workflow_editing_enabled", True
    )

    async def _persist_snapshot(**kwargs) -> str:
        assert kwargs["payload"] == {
            "repository": "Moon/Mind",
            "targetRuntime": "codex_cli",
            "requiredCapabilities": [],
        }
        assert kwargs["task_payload"] == {
            "title": "Direct run",
            "instructions": "Implement the persisted direct run.",
        }
        assert kwargs["source_kind"] == "create"
        target_record = kwargs["record"]
        target_record.memo = {
            **dict(target_record.memo or {}),
            "task_input_snapshot_ref": "art_snapshot_direct",
            "task_input_snapshot_version": 1,
            "task_input_snapshot_source_kind": "create",
        }
        return "art_snapshot_direct"

    persist_mock = AsyncMock(side_effect=_persist_snapshot)
    monkeypatch.setattr(
        "api_service.api.routers.executions._persist_original_workflow_input_snapshot",
        persist_mock,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "workflowType": "MoonMind.UserWorkflow",
                "title": "Direct run",
                "initialParameters": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex_cli",
                    "workflow": {
                        "title": "Conflicting retry",
                        "instructions": "Do not snapshot the replay payload.",
                    },
                },
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["taskInputSnapshot"]["available"] is True
    assert body["taskInputSnapshot"]["artifactRef"] == "art_snapshot_direct"
    assert body["actions"]["canUpdateInputs"] is True
    persist_mock.assert_awaited_once()
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(record)


def test_task_submission_snapshot_uses_input_artifact_for_stripped_step_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    record = _build_execution_record(has_workflow_input_snapshot=False)
    service.create_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=False)
    session = AsyncMock()
    app.dependency_overrides[get_async_session] = lambda: session
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard, "temporal_workflow_editing_enabled", True
    )

    captured: dict[str, object] = {}

    async def _persist_snapshot(**kwargs) -> str:
        captured.update(kwargs)
        target_record = kwargs["record"]
        target_record.memo = {
            **dict(target_record.memo or {}),
            "task_input_snapshot_ref": "art_snapshot_hydrated_create",
            "task_input_snapshot_version": 1,
            "task_input_snapshot_source_kind": "create",
        }
        return "art_snapshot_hydrated_create"

    persist_mock = AsyncMock(side_effect=_persist_snapshot)
    monkeypatch.setattr(
        "api_service.api.routers.executions._persist_original_workflow_input_snapshot_from_parameters",
        persist_mock,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex_cli",
                    "inputArtifactRef": "art-full-input",
                    "workflow": {
                        "instructions": "Top level stays inline.",
                        "runtime": {"mode": "codex_cli"},
                        "steps": [
                            {
                                "id": "step-1",
                                "instructions": "Top level stays inline.",
                            },
                            {"id": "step-2", "title": "Stripped later step"},
                        ],
                    },
                },
            },
        )

    assert response.status_code == 201
    persist_mock.assert_awaited_once()
    assert captured["parameters"]["workflow"]["steps"][1] == {
        "id": "step-2",
        "title": "Stripped later step",
    }
    assert captured["input_artifact_ref"] == "art-full-input"
    assert captured["source_kind"] == "create"
    session.commit.assert_awaited_once()


def test_create_execution_enforces_idempotency(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client
    service.create_execution.return_value = _build_execution_record()

    response = test_client.post(
        "/api/executions",
        json={
            "workflowType": "MoonMind.UserWorkflow",
            "idempotencyKey": "idem-123",
            "initialParameters": {
                "workflow": {"instructions": "Test idempotent create"}
            },
        },
    )

    assert response.status_code == 201
    called_kwargs = service.create_execution.await_args.kwargs
    assert called_kwargs["idempotency_key"] == "idem-123"

def test_list_executions_rejects_non_admin_cross_owner_queries(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, _user = client

    response = test_client.get("/api/executions", params={"ownerId": str(uuid4())})

    assert response.status_code == 403
    assert (
        response.json()["detail"]["message"]
        == "Cannot list executions for another user."
    )
    service.list_executions.assert_not_awaited()

def test_describe_execution_hides_foreign_workflow_visibility(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = SimpleNamespace(
        owner_id=str(uuid4()),
        workflow_id="mm:foreign",
    )

    response = test_client.get("/api/executions/mm:foreign")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "execution_not_found"
    assert str(user.id) != service.describe_execution.return_value.owner_id

def test_describe_execution_allows_search_attribute_owner_id_fallback(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    record = _build_execution_record(owner_id=str(user.id))
    record.owner_id = ""
    record.search_attributes["mm_owner_id"] = [str(user.id)]
    service.describe_execution.return_value = record

    response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    assert response.json()["workflowId"] == "mm:wf-1"

def test_describe_execution_source_temporal_uses_projection_fallback_when_sync_fails(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )

    async def _raise_sync_failure(*_args, **_kwargs):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr("api_service.api.routers.executions.RPCError", RuntimeError)
    monkeypatch.setattr(
        "api_service.core.sync.fetch_and_sync_execution",
        _raise_sync_failure,
    )

    response = test_client.get("/api/executions/mm:wf-1", params={"source": "temporal"})

    assert response.status_code == 200
    assert response.json()["workflowId"] == "mm:wf-1"
    assert service.describe_execution.await_args.kwargs["include_orphaned"] is True

def test_describe_execution_rolls_back_session_when_temporal_sync_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    user = _override_user_dependencies(app, is_superuser=False)
    service.describe_execution.return_value = _build_execution_record(
        owner_id=str(user.id)
    )
    service.list_dependents.return_value = []
    service.enrich_dependency_summaries.return_value = []
    app.dependency_overrides[_get_service] = lambda: service
    _override_query_client(app, progress={"total": 0})

    session = AsyncMock()
    session.commit.side_effect = RuntimeError("db flush failed")

    async def _override_session():
        yield session

    async def _sync_success(*_args, **_kwargs):
        return None

    app.dependency_overrides[get_async_session] = _override_session
    monkeypatch.setattr(
        "api_service.core.sync.fetch_and_sync_execution",
        _sync_success,
    )

    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/executions/mm:wf-1", params={"source": "temporal"}
        )

    assert response.status_code == 200
    session.rollback.assert_awaited_once()
    assert service.describe_execution.await_args.kwargs["include_orphaned"] is True

def test_describe_execution_source_temporal_returns_503_when_no_fallback_record(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    service.describe_execution.side_effect = TemporalExecutionNotFoundError(
        "Workflow execution mm:wf-missing was not found"
    )

    async def _raise_sync_failure(*_args, **_kwargs):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr("api_service.api.routers.executions.RPCError", RuntimeError)
    monkeypatch.setattr(
        "api_service.core.sync.fetch_and_sync_execution",
        _raise_sync_failure,
    )

    response = test_client.get(
        "/api/executions/mm:wf-missing",
        params={"source": "temporal"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "temporal_unavailable"

def test_update_execution_invalid_update_name_returns_contract_error(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = SimpleNamespace(owner_id=str(user.id))
    service.update_execution.side_effect = TemporalExecutionValidationError(
        "Unsupported update name: UnknownUpdate"
    )

    response = test_client.post(
        "/api/executions/mm:test/update",
        json={"updateName": "UnknownUpdate"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_update_request"

def test_signal_execution_invalid_signal_name_returns_contract_error(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    service.describe_execution.return_value = SimpleNamespace(owner_id=str(user.id))
    service.signal_execution.side_effect = TemporalExecutionValidationError(
        "Unsupported signal name: UnknownSignal"
    )

    response = test_client.post(
        "/api/executions/mm:test/signal",
        json={"signalName": "UnknownSignal"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "signal_rejected"

def test_signal_execution_routes_send_message_and_serializes_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.AWAITING_EXTERNAL)
    record.memo["intervention_audit"] = [
        {
            "action": "send_message",
            "transport": "temporal_update",
            "summary": "Operator message sent.",
            "detail": "Continue with provider profiles.",
            "createdAt": "2026-03-31T01:02:03Z",
        }
    ]
    mock_service.describe_execution.return_value = record
    mock_service.signal_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/signal",
            json={
                "signalName": "SendMessage",
                "payload": {"message": "Continue with provider profiles."},
            },
        )

    assert response.status_code == 202
    called = mock_service.signal_execution.await_args.kwargs
    assert called["signal_name"] == "SendMessage"
    assert called["payload"] == {"message": "Continue with provider profiles."}
    body = response.json()
    assert body["actions"]["canReject"] is True
    assert body["actions"]["canSendMessage"] is True
    assert body["interventionAudit"][0]["action"] == "send_message"
    assert body["interventionAudit"][0]["detail"] == "Continue with provider profiles."


def test_signal_execution_rejects_blocked_send_message_without_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.AWAITING_EXTERNAL)
    mock_service.describe_execution.return_value = record
    mock_service.signal_execution.side_effect = TemporalExecutionValidationError(
        "Blocked outbound content: credential at execution.send_message.message"
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/signal",
            json={
                "signalName": "SendMessage",
                "payload": {"message": "Please use token=blocked-secret-value"},
            },
        )

    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["code"] == "signal_rejected"
    dumped = json.dumps(body)
    assert "execution.send_message.message" in dumped
    assert "blocked-secret-value" not in dumped
    called = mock_service.signal_execution.await_args.kwargs
    assert called["signal_name"] == "SendMessage"


def test_signal_execution_routes_clean_send_message_body_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.AWAITING_EXTERNAL)
    mock_service.describe_execution.return_value = record
    mock_service.signal_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)

    message = "Continue with Provider Profiles exactly as written."
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/signal",
            json={"signalName": "SendMessage", "payload": {"message": message}},
        )

    assert response.status_code == 202
    called = mock_service.signal_execution.await_args.kwargs
    assert called["signal_name"] == "SendMessage"
    assert called["payload"] == {"message": message}


def test_signal_execution_routes_skip_dependency_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.WAITING_ON_DEPENDENCIES)
    record.memo["intervention_audit"] = [
        {
            "action": "skip_dependency_wait",
            "transport": "temporal_update",
            "summary": "Dependency wait skipped by operator.",
            "createdAt": "2026-03-31T01:02:03Z",
        }
    ]
    mock_service.describe_execution.return_value = record
    mock_service.signal_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/signal",
            json={"signalName": "SkipDependencyWait", "payload": {}},
        )

    assert response.status_code == 202
    called = mock_service.signal_execution.await_args.kwargs
    assert called["signal_name"] == "SkipDependencyWait"
    assert called["payload"] == {}
    body = response.json()
    assert body["actions"]["canSkipDependencyWait"] is False
    assert body["interventionAudit"][0]["action"] == "skip_dependency_wait"

def test_cancel_execution_passes_reject_action_to_service() -> None:
    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            state=MoonMindWorkflowState.AWAITING_EXTERNAL
        )
        rejected = _build_execution_record(state=MoonMindWorkflowState.CANCELED)
        rejected.close_status = "canceled"
        rejected.memo["intervention_audit"] = [
            {
                "action": "reject",
                "transport": "temporal_cancel",
                "summary": "Rejected by operator.",
                "createdAt": "2026-03-31T01:02:03Z",
            }
        ]
        service.cancel_execution.return_value = rejected

        response = test_client.post(
            "/api/executions/mm:wf-1/cancel",
            json={
                "action": "reject",
                "graceful": True,
                "reason": "Rejected by operator.",
            },
        )

        assert response.status_code == 202
        called = service.cancel_execution.await_args.kwargs
        assert called["action"] == "reject"
        assert called["graceful"] is True
        assert called["reason"] == "Rejected by operator."
        assert response.json()["interventionAudit"][0]["action"] == "reject"

def test_cancel_execution_authorizes_projection_only_child_target() -> None:
    for test_client, service in _client_with_service():
        child = _build_execution_record(state=MoonMindWorkflowState.AWAITING_SLOT)
        child.workflow_id = (
            "resolver:mm:parent:pr:1634:head:"
            "5ed0c032789b901b99da93eaa4877de6609fdf35:1"
        )
        canceled = _build_execution_record(state=MoonMindWorkflowState.CANCELED)
        canceled.workflow_id = child.workflow_id
        canceled.close_status = "canceled"
        service.describe_cancel_target_execution.return_value = child
        service.cancel_execution.return_value = canceled

        response = test_client.post(
            f"/api/executions/{child.workflow_id}/cancel",
            json={"graceful": True, "reason": "stop child"},
        )

        assert response.status_code == 202
        service.describe_cancel_target_execution.assert_awaited_once_with(
            child.workflow_id
        )
        called = service.cancel_execution.await_args.kwargs
        assert called["workflow_id"] == child.workflow_id
        assert called["reason"] == "stop child"
        assert called["graceful"] is True

def test_cancel_execution_authorizes_projection_only_nested_parent(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    child = _build_execution_record(
        state=MoonMindWorkflowState.AWAITING_SLOT,
        owner_id="",
    )
    child.workflow_id = "mm:parent:agent:child-1"
    child.search_attributes = {}
    child.owner_type = None

    parent = _build_execution_record(owner_id=str(user.id))
    parent.workflow_id = "mm:parent"

    canceled = _build_execution_record(state=MoonMindWorkflowState.CANCELED)
    canceled.workflow_id = child.workflow_id
    canceled.close_status = "canceled"

    service.describe_cancel_target_execution.return_value = child
    service.describe_execution.return_value = parent
    service.cancel_execution.return_value = canceled

    response = test_client.post(
        f"/api/executions/{child.workflow_id}/cancel",
        json={"graceful": True, "reason": "stop nested child"},
    )

    assert response.status_code == 202
    service.describe_execution.assert_awaited_once_with(
        "mm:parent",
        include_orphaned=True,
    )
    called = service.cancel_execution.await_args.kwargs
    assert called["workflow_id"] == child.workflow_id
    assert called["reason"] == "stop nested child"

def test_serialize_execution_treats_system_owner_id_as_system_owner_type() -> None:
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={},
        owner_id="system",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.UserWorkflow"),
        state=MoonMindWorkflowState.INITIALIZING,
        workflow_id="wf-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        created_at="2026-03-06T00:00:00Z",
        started_at="2026-03-06T00:00:00Z",
        updated_at="2026-03-06T00:00:00Z",
        closed_at=None,
        integration_state=None,
    )

    payload = _serialize_execution(record)

    assert payload.owner_type == "system"
    assert payload.owner_id == "system"

def test_serialize_execution_leaves_immediate_run_unscheduled() -> None:
    created_at = datetime(2026, 3, 6, 0, 0, tzinfo=UTC)
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.UserWorkflow"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="wf-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        scheduled_for=None,
        created_at=created_at,
        started_at=created_at,
        updated_at=created_at,
        closed_at=None,
        integration_state=None,
    )

    payload = _serialize_execution(record)

    assert payload.scheduled_for is None
    assert payload.created_at == created_at

def test_serialize_execution_surfaces_recurring_schedule_provenance() -> None:
    created_at = datetime(2026, 3, 6, 0, 0, tzinfo=UTC)
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.UserWorkflow"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="wf-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        scheduled_for=None,
        created_at=created_at,
        started_at=created_at,
        updated_at=created_at,
        closed_at=None,
        integration_state=None,
        parameters={"system": {"recurrence": {"definitionId": "schedule-alpha"}}},
    )

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["recurrence"] == {
        "definitionId": "schedule-alpha",
        "href": "/schedules/schedule-alpha",
    }


def test_serialize_execution_surfaces_recurring_schedule_provenance_from_memo() -> None:
    created_at = datetime(2026, 3, 6, 0, 0, tzinfo=UTC)
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={"definitionId": "schedule-from-memo"},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.UserWorkflow"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="wf-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        scheduled_for=None,
        created_at=created_at,
        started_at=created_at,
        updated_at=created_at,
        closed_at=None,
        integration_state=None,
        parameters={},
    )

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["recurrence"] == {
        "definitionId": "schedule-from-memo",
        "href": "/schedules/schedule-from-memo",
    }


def test_execution_recurrence_provenance_ignores_non_mapping_params() -> None:
    assert _execution_recurrence_provenance(None) is None
    assert _execution_recurrence_provenance(None, {"definitionId": "memo-schedule"}) == {
        "definitionId": "memo-schedule",
        "href": "/schedules/memo-schedule",
    }


def test_serialize_execution_falls_back_to_updated_at_without_scheduled_time() -> None:
    updated_at = datetime(2026, 3, 6, 0, 0, tzinfo=UTC)
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.UserWorkflow"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="wf-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        scheduled_for=None,
        created_at=None,
        updated_at=updated_at,
        closed_at=None,
        integration_state=None,
    )

    payload = _serialize_execution(record)

    assert payload.created_at == updated_at
    assert payload.scheduled_for is None

def test_serialize_execution_surfaces_runtime_model_effort_priority_from_parameters() -> None:
    """Ensure runtime/model/effort/priority stored in record.parameters are surfaced."""
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={"title": "RT test", "summary": "OK"},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.UserWorkflow"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="mm:rt-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        created_at="2026-03-19T00:00:00Z",
        started_at="2026-03-19T00:00:00Z",
        updated_at="2026-03-19T00:00:00Z",
        closed_at=None,
        integration_state=None,
        parameters={
            "targetRuntime": "codex",
            "model": "gpt-5-codex",
            "effort": "high",
            "priority": 4,
        },
        paused=False,
        waiting_reason=None,
        attention_required=False,
    )

    payload = _serialize_execution(record)

    # Verify Python field values
    assert payload.target_runtime == "codex"
    assert payload.model == "gpt-5-codex"
    assert payload.effort == "high"
    assert payload.priority == 4

    # Verify JSON serialization uses camelCase aliases (what the frontend sees)
    dumped = payload.model_dump(by_alias=True)
    assert dumped["targetRuntime"] == "codex"
    assert dumped["model"] == "gpt-5-codex"
    assert dumped["effort"] == "high"
    assert dumped["priority"] == 4

def test_serialize_execution_handles_missing_and_malformed_priority_sources() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = None

    payload = _serialize_execution(record)

    assert payload.priority is None

    record.parameters = {"task": "not-a-task-payload"}
    payload = _serialize_execution(record)

    assert payload.priority is None

def test_serialize_execution_surfaces_runtime_from_nested_parameters_runtime_key() -> None:
    """Some payloads store mode under parameters.runtime.mode without top-level targetRuntime."""
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={"title": "Nested RT", "summary": "OK"},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.UserWorkflow"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="mm:rt-nested",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        created_at="2026-03-19T00:00:00Z",
        started_at="2026-03-19T00:00:00Z",
        updated_at="2026-03-19T00:00:00Z",
        closed_at=None,
        integration_state=None,
        parameters={
            "runtime": {"mode": "claude_code", "model": "claude-sonnet-test"},
        },
        paused=False,
        waiting_reason=None,
        attention_required=False,
    )

    payload = _serialize_execution(record)

    assert payload.target_runtime == "claude_code"
    dumped = payload.model_dump(by_alias=True)
    assert dumped["targetRuntime"] == "claude_code"

def test_serialize_execution_surfaces_runtime_fields_from_task_runtime_payload() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "workflow": {
            "instructions": "Reconstruct this draft.",
            "runtime": {
                "mode": "claude_code",
                "model": "claude-3.7-sonnet",
                "effort": "low",
                "profileId": "profile:claude-default",
            },
        },
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["targetRuntime"] == "claude_code"
    assert dumped["model"] == "claude-3.7-sonnet"
    assert dumped["resolvedModel"] == "claude-3.7-sonnet"
    assert dumped["effort"] == "low"
    assert dumped["profileId"] == "profile:claude-default"

def test_serialize_execution_falls_back_to_resolved_model_alias() -> None:
    """When `params['model']` is missing, the resolvedModel alias should populate
    both `model` and `resolvedModel` so the workflow detail UI displays consistently."""
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "targetRuntime": "codex_cli",
        "resolvedModel": "gpt-5-codex",
        "modelSource": "runtime_default",
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["model"] == "gpt-5-codex"
    assert dumped["resolvedModel"] == "gpt-5-codex"
    assert dumped["modelSource"] == "runtime_default"

def test_serialize_execution_falls_back_to_requested_model_when_resolved_missing() -> None:
    """If only the user-requested model is recorded, surface it on the detail
    page so the Model fact is rendered consistently."""
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "targetRuntime": "codex_cli",
        "requestedModel": "gpt-5-codex",
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["model"] == "gpt-5-codex"
    assert dumped["resolvedModel"] == "gpt-5-codex"
    assert dumped["requestedModel"] == "gpt-5-codex"

def test_serialize_execution_surfaces_task_template_slug_as_primary_skill() -> None:
    record = _build_execution_record(
        state=MoonMindWorkflowState.WAITING_ON_DEPENDENCIES
    )
    record.parameters = {
        "targetRuntime": "codex_cli",
        "workflow": {
            "title": "Run Jira Orchestrate for MM-501",
            "instructions": "Use the existing Jira Orchestrate workflow.",
            "taskTemplate": {
                "slug": "jira-orchestrate",
            },
        },
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["targetSkill"] == "jira-orchestrate"
    assert dumped["taskSkills"] == ["jira-orchestrate"]
    assert dumped["skillRuntime"]["selectedSkills"] == ["jira-orchestrate"]

def test_serialize_execution_prefers_preset_slug_over_child_skill_display() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "targetRuntime": "codex_cli",
        "workflow": {
            "instructions": "Run Jira Implement for MM-901.",
            "taskTemplate": {
                "slug": "jira-implement",
            },
            "tool": {
                "type": "skill",
                "name": "jira-issue-updater",
            },
            "skill": {
                "id": "jira-issue-updater",
                "args": {},
            },
            "appliedStepTemplates": [
                {
                    "slug": "jira-implement",
                    "stepIds": ["tpl:jira-implement:08"],
                }
            ],
            "steps": [
                {
                    "id": "tpl:jira-implement:08",
                    "title": "Finalize Jira status",
                    "instructions": "Update Jira with implementation status.",
                    "skill": {
                        "id": "jira-issue-updater",
                        "args": {},
                    },
                }
            ],
        },
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["targetSkill"] == "jira-implement"
    assert dumped["taskSkills"] == ["jira-implement"]
    assert dumped["skillRuntime"]["selectedSkills"] == ["jira-implement"]

def test_serialize_execution_surfaces_applied_template_slug_as_primary_skill() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "targetRuntime": "codex_cli",
        "workflow": {
            "instructions": "Run the applied preset.",
            "appliedStepTemplates": [
                {
                    "slug": "jira-orchestrate",
                    "stepIds": ["tpl:jira-orchestrate"],
                }
            ],
        },
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["targetSkill"] == "jira-orchestrate"
    assert dumped["taskSkills"] == ["jira-orchestrate"]

def test_serialize_execution_uses_latest_applied_template_as_primary_skill() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "targetRuntime": "codex_cli",
        "workflow": {
            "instructions": "Run the latest applied preset.",
            "appliedStepTemplates": [
                {
                    "slug": "initial-preset",
                    "stepIds": ["tpl:initial-preset"],
                },
                {
                    "slug": "latest-preset",
                    "stepIds": ["tpl:latest-preset"],
                },
            ],
        },
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["targetSkill"] == "latest-preset"
    assert dumped["taskSkills"] == ["latest-preset"]
    assert dumped["skillRuntime"]["selectedSkills"] == ["latest-preset"]

def test_serialize_execution_surfaces_compact_skill_runtime_metadata() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "resolvedSkillsetRef": "artifact:resolved-skills-1",
        "workflow": {
            "instructions": "Inspect skill runtime evidence.",
            "skills": {
                "sets": ["operator-default"],
                "include": [{"name": "pr-resolver", "version": "1.2.0"}],
                "materializationMode": "hybrid",
            },
        },
        "skillsMaterialized": {
            "activeSkills": ["pr-resolver"],
            "skills": [
                {
                    "name": "pr-resolver",
                    "version": "1.2.0",
                    "source_kind": "deployment",
                    "content_ref": "artifact:skill-body-1",
                    "content_digest": "sha256:abc",
                    "body": "FULL SKILL BODY SHOULD NOT LEAK",
                }
            ],
            "materializationMode": "hybrid",
            "visiblePath": ".agents/skills",
            "backingPath": "../skills_active",
            "readOnly": True,
            "manifestPath": "artifact:manifest-1",
            "promptIndexRef": "artifact:prompt-index-1",
            "activationSummaryRef": "artifact:activation-summary-1",
        },
    }

    payload = _serialize_execution(record)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["taskSkills"] == ["operator-default", "pr-resolver"]
    skill_runtime = dumped["skillRuntime"]
    assert skill_runtime["resolvedSkillsetRef"] == "artifact:resolved-skills-1"
    assert skill_runtime["selectedSkills"] == ["pr-resolver"]
    assert skill_runtime["selectedEvidence"][0] == {
        "name": "pr-resolver",
        "sourceKind": "deployment",
        "sourcePath": None,
        "contentRef": "artifact:skill-body-1",
        "contentDigest": "sha256:abc",
    }
    assert "selectedVersions" not in skill_runtime
    assert skill_runtime["sourceProvenance"][0] == {
        "name": "pr-resolver",
        "sourceKind": "deployment",
        "sourcePath": None,
    }
    assert skill_runtime["materializationMode"] == "hybrid"
    assert skill_runtime["visiblePath"] == ".agents/skills"
    assert skill_runtime["backingPath"] == "../skills_active"
    assert skill_runtime["readOnly"] is True
    assert skill_runtime["manifestRef"] == "artifact:manifest-1"
    assert skill_runtime["promptIndexRef"] == "artifact:prompt-index-1"
    assert skill_runtime["activationSummaryRef"] == "artifact:activation-summary-1"
    assert skill_runtime["lifecycleIntent"] == {
        "source": "proposal",
        "selectors": ["operator-default", "pr-resolver"],
        "resolvedSkillsetRef": "artifact:resolved-skills-1",
        "resolutionMode": "snapshot-reuse",
        "explanation": "Execution reuses the resolved skill snapshot unless explicit re-resolution is requested.",
    }
    assert "FULL SKILL BODY SHOULD NOT LEAK" not in str(dumped["skillRuntime"])

def test_serialize_execution_preserves_direct_skill_source_provenance() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "workflow": {"instructions": "Inspect skill runtime evidence."},
        "skillsMaterialized": {
            "selectedSkills": ["pr-resolver", "fix-ci"],
            "selectedVersions": [
                {"name": "pr-resolver", "version": "1.2.0"},
                {
                    "name": "fix-ci",
                    "version": "2",
                    "source_kind": "deployment",
                    "source_path": ".agents/skills/fix-ci",
                },
            ],
            "sourceProvenance": [
                {
                    "name": "pr-resolver",
                    "sourceKind": "repo",
                    "sourcePath": ".agents/skills/pr-resolver",
                }
            ],
        },
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["skillRuntime"]["sourceProvenance"] == [
        {
            "name": "pr-resolver",
            "sourceKind": "repo",
            "sourcePath": ".agents/skills/pr-resolver",
        },
        {
            "name": "fix-ci",
            "sourceKind": "deployment",
            "sourcePath": ".agents/skills/fix-ci",
        },
    ]


def test_serialize_execution_handles_missing_skill_materialization_metadata() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "workflow": {
            "instructions": "Inspect skill runtime evidence.",
            "skills": {"sets": ["operator-default"]},
        },
        "skillRuntime": None,
        "skillsMaterialized": None,
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["taskSkills"] == ["operator-default"]
    assert payload["skillRuntime"]["selectedSkills"] == ["operator-default"]
    assert payload["skillRuntime"]["selectedEvidence"] == []
    assert payload["skillRuntime"]["sourceProvenance"] == []


def test_serialize_execution_accepts_snake_case_skill_materialization_metadata() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "workflow": {
            "instructions": "Inspect skill runtime evidence.",
            "skills": {"materialization_mode": "hybrid"},
        },
        "skillsMaterialized": {
            "activeSkills": ["pr-resolver"],
            "visible_path": ".agents/skills",
            "backing_path": "../skills_active",
            "read_only": False,
            "manifest_ref": "artifact:manifest-1",
            "prompt_index_ref": "artifact:prompt-index-1",
            "activation_summary_ref": "artifact:activation-summary-1",
        },
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)
    skill_runtime = payload["skillRuntime"]

    assert skill_runtime["materializationMode"] == "hybrid"
    assert skill_runtime["visiblePath"] == ".agents/skills"
    assert skill_runtime["backingPath"] == "../skills_active"
    assert skill_runtime["readOnly"] is False
    assert skill_runtime["manifestRef"] == "artifact:manifest-1"
    assert skill_runtime["promptIndexRef"] == "artifact:prompt-index-1"
    assert skill_runtime["activationSummaryRef"] == "artifact:activation-summary-1"

def test_serialize_execution_surfaces_skill_lifecycle_intent_for_schedule_defaults() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.SCHEDULED)
    record.parameters = {
        "workflow": {
            "instructions": "Run this later.",
            "skills": {"sets": ["nightly"], "materializationMode": "hybrid"},
        },
        "skillLifecycleIntent": {
            "source": "schedule",
            "resolutionMode": "selector-based",
            "explanation": "Scheduled run resolves selected skills when it starts.",
        },
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    lifecycle = payload["skillRuntime"]["lifecycleIntent"]
    assert lifecycle["source"] == "schedule"
    assert lifecycle["selectors"] == ["nightly"]
    assert lifecycle["resolutionMode"] == "selector-based"
    assert lifecycle["explanation"] == "Scheduled run resolves selected skills when it starts."

def test_serialize_execution_marks_lifecycle_defaults_as_inherited_defaults() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.SCHEDULED)
    record.parameters = {
        "workflow": {"instructions": "Run this later."},
        "skillLifecycleIntent": {"source": "schedule"},
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    lifecycle = payload["skillRuntime"]["lifecycleIntent"]
    assert lifecycle["source"] == "schedule"
    assert lifecycle["selectors"] == []
    assert lifecycle["resolvedSkillsetRef"] is None
    assert lifecycle["resolutionMode"] == "inherited-defaults"
    assert lifecycle["explanation"] == "Execution inherits deployment skill defaults explicitly."

def test_serialize_execution_ignores_stale_waiting_reason_for_executing_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.memo = {
        "title": "Temporal task",
        "summary": "Launching agent...",
        "waiting_reason": "provider_profile_slot",
    }
    record.waiting_reason = None
    monkeypatch.setattr(settings.temporal_dashboard, "debug_fields_enabled", True)

    payload = _serialize_execution(
        record,
        user=SimpleNamespace(is_superuser=True, id=record.owner_id),
    )

    assert payload.state == "executing"
    assert payload.waiting_reason is None
    assert payload.debug_fields is not None
    assert payload.debug_fields.waiting_reason is None

def test_serialize_execution_surfaces_agent_run_id_from_memo() -> None:
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={"title": "Agent run", "summary": "OK", "agentRunId": "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d"},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.UserWorkflow"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="mm:agent-run-1",
        namespace="moonmind",
        run_id="temporal-run-1",
        artifact_refs=[],
        created_at="2026-03-19T00:00:00Z",
        started_at="2026-03-19T00:00:00Z",
        updated_at="2026-03-19T00:00:00Z",
        closed_at=None,
        integration_state=None,
        parameters={},
        paused=False,
        waiting_reason=None,
        attention_required=False,
    )

    payload = _serialize_execution(record)

    assert payload.agent_run_id == "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d"
    dumped = payload.model_dump(by_alias=True)
    assert "task" + "RunId" not in dumped
    assert dumped["agentRunId"] == "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d"

def test_serialize_execution_surfaces_dependency_metadata() -> None:
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={
            "title": "Dependent task",
            "summary": "Waiting on dependencies.",
            "depends_on": ["mm:dep-1", "mm:dep-2"],
            "has_dependencies": True,
            "dependency_wait_occurred": True,
            "dependency_wait_duration_ms": 1500,
            "dependency_resolution": "success",
        },
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.UserWorkflow"),
        state=MoonMindWorkflowState.WAITING_ON_DEPENDENCIES,
        workflow_id="mm:task-deps-1",
        namespace="moonmind",
        run_id="temporal-run-1",
        artifact_refs=[],
        created_at="2026-03-19T00:00:00Z",
        started_at="2026-03-19T00:00:00Z",
        updated_at="2026-03-19T00:00:00Z",
        closed_at=None,
        integration_state=None,
        parameters={"workflow": {"dependsOn": ["mm:dep-1", "mm:dep-2"]}},
        paused=False,
        waiting_reason=None,
        attention_required=False,
    )

    payload = _serialize_execution(record)

    dumped = payload.model_dump(by_alias=True)
    assert dumped["dependsOn"] == ["mm:dep-1", "mm:dep-2"]
    assert dumped["hasDependencies"] is True
    assert dumped["dependencyWaitOccurred"] is True
    assert dumped["dependencyWaitDurationMs"] == 1500
    assert dumped["dependencyResolution"] == "success"
    assert dumped["failedDependencyId"] is None

def test_serialize_execution_repository_ignores_mapping_values_and_uses_first_scalar() -> None:
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={
            "title": "Repo test",
            "summary": "OK",
            "repository": {"owner": "Moon", "name": "Mind"},
        },
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.UserWorkflow"),
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="mm:repo-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        created_at="2026-03-31T00:00:00Z",
        started_at="2026-03-31T00:00:00Z",
        updated_at="2026-03-31T00:00:00Z",
        closed_at=None,
        integration_state=None,
        parameters={"repository": ["Moon/Mind", "Ignored/Repo"]},
        paused=False,
        waiting_reason=None,
        attention_required=False,
    )

    payload = _serialize_execution(record)

    assert payload.repository == "Moon/Mind"
    dumped = payload.model_dump(by_alias=True)
    assert dumped["repository"] == "Moon/Mind"

def test_describe_execution_exposes_workflow_and_run_identity() -> None:
    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record()

        response = test_client.get("/api/executions/mm:wf-1")

        assert response.status_code == 200
        payload = response.json()
        assert payload["workflowId"] == "mm:wf-1"
        assert "taskId" not in payload
        assert payload["runId"] == "run-2"
        assert "temporalRunId" not in payload
        assert payload["latestRunView"] is True
        assert payload["continueAsNewCause"] == "manual_rerun"
        assert payload["stepsHref"] == "/api/executions/mm:wf-1/steps"

def test_describe_execution_includes_latest_run_progress() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        progress={
            "total": 3,
            "pending": 0,
            "ready": 1,
            "executing": 1,
            "awaitingExternal": 0,
            "reviewing": 0,
            "completed": 1,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Run tests",
            "updatedAt": "2026-04-08T12:00:00Z",
        },
    )
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stepsHref"] == "/api/executions/mm:wf-1/steps"
    assert payload["progress"] == {
        "total": 3,
        "pending": 0,
        "ready": 1,
        "executing": 1,
        "awaitingExternal": 0,
        "reviewing": 0,
        "completed": 1,
        "failed": 0,
        "skipped": 0,
        "canceled": 0,
        "currentStepTitle": "Run tests",
        "updatedAt": "2026-04-08T12:00:00Z",
    }

def test_describe_execution_includes_live_merge_automation_summary() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    record.parameters = {
        "publishMode": "pr",
        "mergeAutomation": {"enabled": True},
    }
    record.memo = {
        **record.memo,
        "merge_automation": {
            "enabled": True,
            "status": "awaiting_child",
            "childWorkflowId": "merge-automation:mm:wf-1:pr:1614:head:abc123",
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        progress={
            "total": 1,
            "pending": 0,
            "ready": 0,
            "executing": 0,
            "awaitingExternal": 1,
            "reviewing": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": None,
            "updatedAt": "2026-04-08T12:00:00Z",
        },
        summary={
            "status": "waiting",
            "prNumber": 1614,
            "prUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/1614",
            "latestHeadSha": "abc123",
            "blockers": [
                {
                    "kind": "checks_failed",
                    "summary": "Required checks are failing.",
                    "source": "github",
                    "retryable": True,
                }
            ],
            "resolverChildWorkflowIds": [
                "resolver:mm:wf-1:pr:1614:head:abc123:1"
            ],
            "artifactRefs": {
                "gateSnapshots": ["gate-artifact"],
                "resolverAttempts": None,
            },
        },
        ledger={
            "workflowId": "resolver:mm:wf-1:pr:1614:head:abc123:1",
            "runId": "resolver-run",
            "runScope": "latest",
            "steps": [
                {
                    "logicalStepId": "node-1",
                    "order": 1,
                    "title": "codex_cli",
                    "tool": {"type": "agent_runtime", "name": "codex_cli"},
                    "dependsOn": [],
                    "status": "executing",
                    "attempt": 1,
                    "updatedAt": "2026-04-08T12:00:00Z",
                    "refs": {"agentRunId": "resolver-agent-run"},
                    "artifacts": {},
                    "checks": [],
                }
            ],
        },
    )
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    merge_automation = response.json()["mergeAutomation"]
    assert merge_automation["workflowId"] == "merge-automation:mm:wf-1:pr:1614:head:abc123"
    assert merge_automation["status"] == "waiting"
    assert merge_automation["blockers"][0]["summary"] == "Required checks are failing."
    assert merge_automation["artifactRefs"]["gateSnapshots"] == ["gate-artifact"]
    assert merge_automation["artifactRefs"]["resolverAttempts"] == []
    assert merge_automation["resolverChildren"] == [
        {
            "workflowId": "resolver:mm:wf-1:pr:1614:head:abc123:1",
            "agentRunId": "resolver-agent-run",
            "status": "executing",
            "detailHref": (
                "/workflows/resolver%3Amm%3Awf-1%3Apr%3A1614%3Ahead%3Aabc123%3A1"
                "?source=temporal"
            ),
        }
    ]

def test_describe_execution_queries_resolver_children_concurrently() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    record.parameters = {
        "publishMode": "pr",
        "mergeAutomation": {"enabled": True},
    }
    record.memo = {
        **record.memo,
        "merge_automation": {
            "enabled": True,
            "status": "awaiting_child",
            "childWorkflowId": "merge-automation:mm:wf-1:pr:1614:head:abc123",
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    resolver_ids = [
        "resolver:mm:wf-1:pr:1614:head:abc123:1",
        "resolver:mm:wf-1:pr:1614:head:abc123:2",
        "resolver:mm:wf-1:pr:1614:head:abc123:3",
    ]
    _override_query_client(
        app,
        progress={"total": 1},
        summary={
            "status": "waiting",
            "resolverChildWorkflowIds": resolver_ids,
        },
    )
    _override_user_dependencies(app, is_superuser=True)

    started: list[str] = []
    all_started = asyncio.Event()

    async def fake_child_observability(
        *,
        temporal_client,
        workflow_id: str,
    ) -> ExecutionMergeAutomationResolverChildModel:
        started.append(workflow_id)
        if len(started) == len(resolver_ids):
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=1)
        return ExecutionMergeAutomationResolverChildModel(
            workflow_id=workflow_id,
            status="executing",
            detail_href=f"/workflows/{workflow_id}",
        )

    with patch(
        "api_service.api.routers.executions._resolver_child_observability",
        side_effect=fake_child_observability,
    ):
        with TestClient(app) as test_client:
            response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    assert started == resolver_ids
    assert [
        child["workflowId"]
        for child in response.json()["mergeAutomation"]["resolverChildren"]
    ] == resolver_ids

def test_describe_execution_prefers_progress_query_run_id_when_newer_latest_run() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        progress={
            "runId": "run-99",
            "total": 3,
            "pending": 0,
            "ready": 1,
            "executing": 1,
            "awaitingExternal": 0,
            "reviewing": 0,
            "completed": 1,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Run tests",
            "updatedAt": "2026-04-08T12:00:00Z",
        },
    )
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runId"] == "run-99"
    assert "temporalRunId" not in payload
    assert payload["progress"] == {
        "total": 3,
        "pending": 0,
        "ready": 1,
        "executing": 1,
        "awaitingExternal": 0,
        "reviewing": 0,
        "completed": 1,
        "failed": 0,
        "skipped": 0,
        "canceled": 0,
        "currentStepTitle": "Run tests",
        "updatedAt": "2026-04-08T12:00:00Z",
    }
    assert "runId" not in payload["progress"]

def test_describe_execution_leaves_progress_null_when_query_fails() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(app, error=RuntimeError("query unavailable"))
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stepsHref"] == "/api/executions/mm:wf-1/steps"
    assert payload["progress"] is None

def test_describe_execution_bounds_slow_live_progress_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        progress={"total": 99},
    )
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "live_query_timeout_seconds",
        0.01,
    )
    monkeypatch.setattr(
        settings.temporal,
        "temporal_authoritative_read_enabled",
        False,
    )
    observed_timeouts: list[float] = []

    async def fail_fast_timeout(awaitable, *, timeout: float):
        observed_timeouts.append(timeout)
        awaitable.close()
        raise TimeoutError

    with (
        patch(
            "api_service.api.routers.executions.asyncio.wait_for",
            side_effect=fail_fast_timeout,
        ),
        patch(
            "api_service.api.routers.executions._hydrate_execution_report_projection",
            new_callable=AsyncMock,
            side_effect=lambda execution, **_kwargs: execution,
        ),
        patch(
            "api_service.api.routers.executions._resolve_agent_run_ids_from_managed_store",
            return_value={},
        ),
        TestClient(app) as test_client,
    ):
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    assert observed_timeouts == [0.01]
    assert response.json()["progress"] is None

def test_describe_execution_skips_live_progress_query_for_terminal_runs() -> None:
    from api_service.db.models import TemporalExecutionCloseStatus

    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.close_status = TemporalExecutionCloseStatus.FAILED
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        progress={
            "total": 99,
            "failed": 99,
        },
    )
    _override_user_dependencies(app, is_superuser=True)

    async def passthrough_report_projection(execution, **_kwargs):
        return execution

    with (
        patch(
            "api_service.api.routers.executions._load_execution_progress",
            new_callable=AsyncMock,
        ) as load_progress,
        patch(
            "api_service.api.routers.executions._enrich_execution_merge_automation",
            new_callable=AsyncMock,
            side_effect=lambda execution, **_kwargs: execution,
        ) as enrich_merge_automation,
        patch(
            "api_service.api.routers.executions._hydrate_execution_report_projection",
            side_effect=passthrough_report_projection,
        ),
        TestClient(app) as test_client,
    ):
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["temporalStatus"] == "failed"
    assert payload["closeStatus"] == "failed"
    assert payload["progress"] is None
    load_progress.assert_not_awaited()
    enrich_merge_automation.assert_awaited_once()

def test_describe_execution_steps_href_uses_configured_detail_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "detail_endpoint",
        "/gateway/api/executions/{workflowId}",
    )
    payload = _serialize_execution(_build_execution_record()).model_dump(by_alias=True)
    assert payload["stepsHref"] == "/gateway/api/executions/mm:wf-1/steps"

def test_describe_execution_does_not_query_progress_for_manifest_workflows() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        workflow_type=TemporalWorkflowType.MANIFEST_INGEST
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    query_client = _override_query_client(app, progress={"total": 99})
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["progress"] is None
    assert payload["stepsHref"] is None
    assert query_client.get_workflow_handle.call_count <= 1

def test_get_execution_steps_returns_latest_run_ledger() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={
            "workflowId": "mm:wf-1",
            "runId": "run-99",
            "runScope": "latest",
            "steps": [
                {
                    "logicalStepId": "run-tests",
                    "order": 1,
                    "title": "Run tests",
                    "tool": {"type": "skill", "name": "repo.run_tests"},
                    "dependsOn": [],
                    "status": "executing",
                    "waitingReason": None,
                    "attentionRequired": False,
                    "attempt": 2,
                    "startedAt": "2026-04-08T12:00:00Z",
                    "updatedAt": "2026-04-08T12:01:00Z",
                    "summary": "Running pytest",
                    "checks": [],
                    "refs": {
                        "childWorkflowId": None,
                        "childRunId": None,
                        "agentRunId": "agent-run-1",
                    },
                    "artifacts": {
                        "outputSummary": None,
                        "outputPrimary": None,
                        "runtimeStdout": "artifact://stdout",
                        "runtimeStderr": None,
                        "runtimeMergedLogs": None,
                        "runtimeDiagnostics": None,
                        "providerSnapshot": None,
                    },
                    "workload": {
                        "agentRunId": "agent-run-1",
                        "stepId": "run-tests",
                        "attempt": 2,
                        "toolName": "container.run_workload",
                        "profileId": "local-python",
                        "imageRef": "python:3.12-slim",
                        "status": "completed",
                        "exitCode": 0,
                        "durationSeconds": 8.5,
                        "sessionContext": {
                            "sessionId": "session-1",
                            "sessionEpoch": 4,
                        },
                    },
                    "lastError": None,
                }
            ],
        },
    )
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflowId"] == "mm:wf-1"
    assert payload["runId"] == "run-99"
    assert payload["runScope"] == "latest"
    assert payload["steps"][0]["executionOrdinal"] == 2
    assert "task" + "RunId" not in payload["steps"][0]["refs"]
    assert "task" + "RunId" not in payload["steps"][0]["workload"]
    assert payload["steps"][0]["refs"]["agentRunId"] == "agent-run-1"
    assert payload["steps"][0]["workload"]["agentRunId"] == "agent-run-1"
    assert payload["steps"][0]["workload"]["profileId"] == "local-python"
    assert payload["steps"][0]["workload"]["imageRef"] == "python:3.12-slim"
    assert payload["steps"][0]["workload"]["sessionContext"] == {
        "sessionId": "session-1",
        "sessionEpoch": 4,
    }
    assert payload["steps"][0]["timing"] == {
        "startedAt": "2026-04-08T12:00:00Z",
        "endedAt": None,
        "durationMs": None,
        "elapsedMs": 60000,
        "serverNow": "2026-04-08T12:01:00Z",
        "precision": "live",
        "preserved": False,
    }

def test_get_execution_steps_enriches_missing_agent_run_ids_once() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service

    def _ledger_step(
        *,
        logical_step_id: str,
        order: int,
        tool_type: str,
        child_workflow_id: str,
    ) -> dict[str, object]:
        return {
            "logicalStepId": logical_step_id,
            "order": order,
            "title": logical_step_id,
            "tool": {
                "type": tool_type,
                "name": (
                    "codex_cli"
                    if tool_type == "agent_runtime"
                    else "repo.run_tests"
                ),
            },
            "dependsOn": [],
            "status": "awaiting_external",
            "waitingReason": "Awaiting child workflow progress",
            "attentionRequired": False,
            "attempt": 1,
            "startedAt": "2026-04-08T12:00:00Z",
            "updatedAt": "2026-04-08T12:01:00Z",
            "summary": "Awaiting child workflow",
            "checks": [],
            "refs": {
                "childWorkflowId": child_workflow_id,
                "childRunId": None,
                "agentRunId": None,
            },
            "artifacts": {
                "outputSummary": None,
                "outputPrimary": None,
                "runtimeStdout": None,
                "runtimeStderr": None,
                "runtimeMergedLogs": None,
                "runtimeDiagnostics": None,
                "providerSnapshot": None,
            },
            "lastError": None,
        }

    _override_query_client(
        app,
        ledger={
            "workflowId": "mm:wf-1",
            "runId": "run-99",
            "runScope": "latest",
            "steps": [
                _ledger_step(
                    logical_step_id="delegate-agent",
                    order=1,
                    tool_type="agent_runtime",
                    child_workflow_id="mm:wf-1:agent:delegate-agent",
                ),
                _ledger_step(
                    logical_step_id="second-agent",
                    order=2,
                    tool_type="agent_runtime",
                    child_workflow_id="mm:wf-1:agent:second-agent",
                ),
                _ledger_step(
                    logical_step_id="run-tests",
                    order=3,
                    tool_type="skill",
                    child_workflow_id="mm:wf-1:tool:run-tests",
                ),
            ],
        },
    )
    _override_user_dependencies(app, is_superuser=True)
    to_thread_calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    async def _fake_to_thread(
        func: object, /, *args: object, **kwargs: object
    ) -> dict[str, str]:
        to_thread_calls.append((func, args, kwargs))
        return {
            "mm:wf-1:agent:delegate-agent": "agent-run-1",
            "mm:wf-1:agent:second-agent": "agent-run-2",
        }

    with patch(
        "api_service.api.routers.executions.asyncio.to_thread",
        new=_fake_to_thread,
    ):
        with TestClient(app) as test_client:
            response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 200
    payload = response.json()
    assert "task" + "RunId" not in payload["steps"][0]["refs"]
    assert "task" + "RunId" not in payload["steps"][1]["refs"]
    assert "task" + "RunId" not in payload["steps"][2]["refs"]
    assert payload["steps"][0]["refs"]["agentRunId"] == "agent-run-1"
    assert payload["steps"][1]["refs"]["agentRunId"] == "agent-run-2"
    assert len(to_thread_calls) == 1
    assert to_thread_calls[0][1] == (
        (
            "mm:wf-1:agent:delegate-agent",
            "mm:wf-1:agent:second-agent",
        ),
    )


def _step_execution_manifest_payload(
    *,
    artifact_ref: str,
    attempt: int,
    status: str = "completed",
) -> dict[str, object]:
    return {
        "schemaVersion": "v1",
        "stepExecutionId": f"mm:wf-1:run-99:implement:execution:{attempt}",
        "workflowId": "mm:wf-1",
        "runId": "run-99",
        "logicalStepId": "implement",
        "executionOrdinal": attempt,
        "executionScope": "run",
        "lineage": {
            "sourceWorkflowId": "mm:source",
            "sourceRunId": "source-run",
            "sourceLogicalStepId": "implement",
            "sourceExecutionOrdinal": attempt,
            "relationship": "recover_from_failed_step",
            "lineageExecutionOrdinal": attempt + 1,
        },
        "reason": "recover_from_failed_step" if attempt > 1 else "initial_execution",
        "status": status,
        "terminalDisposition": "accepted" if status == "completed" else "retryable",
        "startedAt": "2026-05-19T10:00:00Z",
        "updatedAt": "2026-05-19T10:01:00Z",
        "input": {"preparedInputRef": f"art-input-{attempt}"},
        "context": {
            "contextBundleRef": f"art-context-{attempt}",
            "retrievalManifestRef": f"artifact://retrieval-manifests/{attempt}",
            "memoryManifestRef": f"attempt-memory-manifest://sha256:{attempt}",
        },
        "workspace": {
            "workspacePolicy": "continue_from_previous_execution",
            "baselineRef": f"art-workspace-{attempt}",
            "gitDisposition": "candidate",
        },
        "execution": {
            "childWorkflowId": f"child-{attempt}",
            "childRunId": f"child-run-{attempt}",
            "agentRunId": f"agent-run-{attempt}",
        },
        "outputs": {
            "summary": f"Attempt {attempt} summary",
            "outputSummaryRef": f"art-summary-{attempt}",
            "outputPrimaryRef": f"art-output-{attempt}",
        },
        "checks": [
            {
                "kind": "quality_gate",
                "status": "passed" if status == "completed" else "failed",
                "artifactRef": f"art-check-{attempt}",
            }
        ],
        "sideEffects": {
            "gitDisposition": "candidate",
            "publicationRef": f"art-publish-{attempt}",
        },
        "dependencyEffects": {"invalidatedStepRefs": [artifact_ref]},
        "budget": {"budgetRef": f"art-budget-{attempt}"},
    }


def test_get_execution_step_executions_returns_bounded_manifest_history() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={
            "workflowId": "mm:wf-1",
            "runId": "run-99",
            "runScope": "latest",
            "steps": [
                {
                    "logicalStepId": "implement",
                    "order": 1,
                    "title": "Implement",
                    "tool": {"type": "skill", "name": "jira-implement"},
                    "dependsOn": [],
                    "status": "completed",
                    "waitingReason": None,
                    "attentionRequired": False,
                    "attempt": 2,
                    "startedAt": "2026-05-19T10:00:00Z",
                    "updatedAt": "2026-05-19T10:01:00Z",
                    "summary": "Done",
                    "checks": [],
                    "refs": {
                        "childWorkflowId": None,
                        "childRunId": None,
                        "agentRunId": None,
                        "latestStepExecutionManifestRef": "art-attempt-2",
                        "stepExecutionManifestRefs": ["art-attempt-1", "art-attempt-2"],
                    },
                    "artifacts": {},
                    "lastError": None,
                }
            ],
        },
    )
    user = _override_user_dependencies(app, is_superuser=True)

    async def _read_artifact(**kwargs):
        artifact_id = kwargs["artifact_id"]
        payload = _step_execution_manifest_payload(
            artifact_ref=artifact_id,
            attempt=1 if artifact_id == "art-attempt-1" else 2,
        )
        return SimpleNamespace(artifact_id=artifact_id), json.dumps(payload).encode()

    artifact_service = SimpleNamespace(read=AsyncMock(side_effect=_read_artifact))
    app.dependency_overrides[get_async_session] = _empty_session_override

    with patch(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            response = test_client.get(
                "/api/executions/mm:wf-1/steps/implement/step-executions"
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflowId"] == "mm:wf-1"
    assert payload["runId"] == "run-99"
    assert payload["logicalStepId"] == "implement"
    assert [item["executionOrdinal"] for item in payload["stepExecutions"]] == [1, 2]
    assert payload["stepExecutions"][1]["manifestRefs"] == {
        "manifestArtifactRef": "art-attempt-2"
    }
    assert payload["stepExecutions"][1]["runtimeChildRefs"] == {
        "childWorkflowId": "child-2",
        "childRunId": "child-run-2",
        "agentRunId": "agent-run-2",
    }
    assert payload["stepExecutions"][1]["workspacePolicy"] == (
        "continue_from_previous_execution"
    )
    assert payload["stepExecutions"][1]["gitDisposition"] == "candidate"
    assert payload["stepExecutions"][1]["qualityGateVerdict"] == "passed"
    assert payload["stepExecutions"][1]["timing"] == {
        "startedAt": "2026-05-19T10:00:00Z",
        "endedAt": "2026-05-19T10:01:00Z",
        "durationMs": 60000,
        "elapsedMs": 60000,
        "serverNow": "2026-05-19T10:01:00Z",
        "precision": "fallback",
        "preserved": False,
    }
    assert "summary" not in payload["stepExecutions"][1]["outputRefs"]
    assert artifact_service.read.await_args_list[0] == call(
        artifact_id="art-attempt-1",
        principal=str(user.id),
        allow_restricted_raw=True,
    )


def test_get_execution_step_executions_sanitizes_failed_attempt_summary() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={
            "workflowId": "mm:wf-1",
            "runId": "run-99",
            "runScope": "latest",
            "steps": [
                {
                    "logicalStepId": "implement",
                    "order": 1,
                    "title": "Implement",
                    "tool": {"type": "skill", "name": "jira-implement"},
                    "dependsOn": [],
                    "status": "failed",
                    "waitingReason": None,
                    "attentionRequired": True,
                    "attempt": 1,
                    "startedAt": "2026-05-19T10:00:00Z",
                    "updatedAt": "2026-05-19T10:01:00Z",
                    "summary": "Failed",
                    "checks": [],
                    "refs": {
                        "childWorkflowId": None,
                        "childRunId": None,
                        "agentRunId": None,
                        "latestStepExecutionManifestRef": "art-attempt-1",
                        "stepExecutionManifestRefs": ["art-attempt-1"],
                    },
                    "artifacts": {},
                    "lastError": None,
                }
            ],
        },
    )
    _override_user_dependencies(app, is_superuser=True)
    raw_token = "ghp_123456789012345678901234567890123456"
    payload = _step_execution_manifest_payload(
        artifact_ref="art-attempt-1",
        attempt=1,
        status="failed",
    )
    payload["outputs"] = {
        "summary": f"failed with token={raw_token}",
        "diagnosticsRef": "art-diagnostics-1",
    }
    artifact_service = SimpleNamespace(
        read=AsyncMock(
            return_value=(
                SimpleNamespace(artifact_id="art-attempt-1"),
                json.dumps(payload).encode(),
            )
        )
    )
    app.dependency_overrides[get_async_session] = _empty_session_override

    with patch(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            response = test_client.get(
                "/api/executions/mm:wf-1/steps/implement/step-executions"
            )

    assert response.status_code == 200
    dumped = response.text
    assert raw_token not in dumped
    assert "token=[REDACTED]" in dumped
    assert response.json()["stepExecutions"][0]["outputRefs"] == {
        "diagnosticsRef": "art-diagnostics-1"
    }


def test_get_execution_step_execution_returns_bounded_detail_refs() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={
            "workflowId": "mm:wf-1",
            "runId": "run-99",
            "runScope": "latest",
            "steps": [
                {
                    "logicalStepId": "implement",
                    "order": 1,
                    "title": "Implement",
                    "tool": {"type": "skill", "name": "jira-implement"},
                    "dependsOn": [],
                    "status": "completed",
                    "waitingReason": None,
                    "attentionRequired": False,
                    "attempt": 2,
                    "startedAt": "2026-05-19T10:00:00Z",
                    "updatedAt": "2026-05-19T10:01:00Z",
                    "summary": "Done",
                    "checks": [],
                    "refs": {
                        "childWorkflowId": None,
                        "childRunId": None,
                        "agentRunId": None,
                        "latestStepExecutionManifestRef": "art-attempt-2",
                        "stepExecutionManifestRefs": ["art-attempt-1", "art-attempt-2"],
                    },
                    "artifacts": {},
                    "lastError": None,
                }
            ],
        },
    )
    _override_user_dependencies(app, is_superuser=True)
    payload = _step_execution_manifest_payload(
        artifact_ref="art-attempt-2",
        attempt=2,
    )
    artifact_service = SimpleNamespace(
        read=AsyncMock(
            side_effect=[
                (
                    SimpleNamespace(artifact_id="art-attempt-1"),
                    json.dumps(
                        _step_execution_manifest_payload(
                            artifact_ref="art-attempt-1",
                            attempt=1,
                        )
                    ).encode(),
                ),
                (
                    SimpleNamespace(artifact_id="art-attempt-2"),
                    json.dumps(payload).encode(),
                ),
            ]
        )
    )
    app.dependency_overrides[get_async_session] = _empty_session_override

    with patch(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            response = test_client.get(
                "/api/executions/mm:wf-1/steps/implement/step-executions/2"
            )

    assert response.status_code == 200
    body = response.json()
    assert body["executionOrdinal"] == 2
    assert body["sourceExecutionOrdinal"] == 2
    assert body["lineage"]["relationship"] == "recover_from_failed_step"
    assert body["inputRefs"] == {"preparedInputRef": "art-input-2"}
    assert body["contextRefs"] == {
        "contextBundleRef": "art-context-2",
        "retrievalManifestRef": "artifact://retrieval-manifests/2",
        "memoryManifestRef": "attempt-memory-manifest://sha256:2",
    }
    assert "retrievalContent" not in body["contextRefs"]
    assert "providerPayload" not in response.text
    assert body["workspaceRefs"] == {
        "baselineRef": "art-workspace-2",
    }
    assert body["executionRefs"] == {
        "childWorkflowId": "child-2",
        "childRunId": "child-run-2",
        "agentRunId": "agent-run-2",
    }
    assert body["checkRefs"] == [{"artifactRef": "art-check-2"}]
    assert body["sideEffectRefs"] == {"publicationRef": "art-publish-2"}
    assert body["dependencyEffectRefs"] == {
        "invalidatedStepRefs": ["art-attempt-2"]
    }
    assert "outputs" not in body


def test_get_execution_step_executions_degraded_older_ref_uses_per_ref_ordinal() -> None:
    """A degraded older ref must keep its per-ref ordinal, not the row-level
    latest attempt count, so it cannot shadow the valid latest attempt or
    produce duplicate ordinals (PR #2530 review P2)."""
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={
            "workflowId": "mm:wf-1",
            "runId": "run-99",
            "runScope": "latest",
            "steps": [
                {
                    "logicalStepId": "implement",
                    "order": 1,
                    "title": "Implement",
                    "tool": {"type": "skill", "name": "jira-implement"},
                    "dependsOn": [],
                    "status": "completed",
                    "waitingReason": None,
                    "attentionRequired": False,
                    # Row-level latest attempt count is 2; the older ref is
                    # degraded. The buggy path mapped it onto ordinal 2.
                    "attempt": 2,
                    "startedAt": "2026-05-19T10:00:00Z",
                    "updatedAt": "2026-05-19T10:01:00Z",
                    "summary": "Done",
                    "checks": [],
                    "refs": {
                        "childWorkflowId": None,
                        "childRunId": None,
                        "agentRunId": None,
                        "latestStepExecutionManifestRef": "art-attempt-2",
                        "stepExecutionManifestRefs": ["art-attempt-1", "art-attempt-2"],
                    },
                    "artifacts": {},
                    "lastError": None,
                }
            ],
        },
    )
    _override_user_dependencies(app, is_superuser=True)

    async def _read_artifact(**kwargs):
        artifact_id = kwargs["artifact_id"]
        if artifact_id == "art-attempt-1":
            # Malformed body -> degraded projection.
            return SimpleNamespace(artifact_id=artifact_id), b"{not-json"
        payload = _step_execution_manifest_payload(
            artifact_ref=artifact_id,
            attempt=2,
        )
        return SimpleNamespace(artifact_id=artifact_id), json.dumps(payload).encode()

    artifact_service = SimpleNamespace(read=AsyncMock(side_effect=_read_artifact))
    app.dependency_overrides[get_async_session] = _empty_session_override

    with patch(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            list_response = test_client.get(
                "/api/executions/mm:wf-1/steps/implement/step-executions"
            )
            detail_latest = test_client.get(
                "/api/executions/mm:wf-1/steps/implement/step-executions/2"
            )
            detail_degraded = test_client.get(
                "/api/executions/mm:wf-1/steps/implement/step-executions/1"
            )

    assert list_response.status_code == 200
    items = list_response.json()["stepExecutions"]
    ordinals = [item["executionOrdinal"] for item in items]
    # No duplicate ordinals: degraded older ref keeps ordinal 1.
    assert ordinals == [1, 2]
    degraded_item = items[0]
    assert degraded_item["manifestArtifactRef"] == "art-attempt-1"
    assert degraded_item["executionOrdinal"] == 1
    assert degraded_item["stepExecutionId"].endswith(":execution:1:invalid")
    assert (
        degraded_item["compatibilityDecision"]["failureCode"]
        == "malformed_step_execution_manifest"
    )
    valid_item = items[1]
    assert valid_item["manifestArtifactRef"] == "art-attempt-2"
    assert valid_item["executionOrdinal"] == 2
    assert valid_item.get("compatibilityDecision") is None
    assert valid_item["status"] == "completed"

    # The valid latest attempt must win at ordinal 2, not the degraded ref.
    assert detail_latest.status_code == 200
    latest_body = detail_latest.json()
    assert latest_body["executionOrdinal"] == 2
    assert latest_body["status"] == "completed"
    assert latest_body.get("compatibilityDecision") is None

    # The degraded older ref is addressable at its own ordinal 1.
    assert detail_degraded.status_code == 200
    degraded_body = detail_degraded.json()
    assert degraded_body["executionOrdinal"] == 1
    assert (
        degraded_body["compatibilityDecision"]["failureCode"]
        == "malformed_step_execution_manifest"
    )


def test_get_execution_step_executions_preserves_artifact_authorization() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={
            "workflowId": "mm:wf-1",
            "runId": "run-99",
            "runScope": "latest",
            "steps": [
                {
                    "logicalStepId": "implement",
                    "order": 1,
                    "title": "Implement",
                    "tool": {},
                    "dependsOn": [],
                    "status": "failed",
                    "waitingReason": None,
                    "attentionRequired": False,
                    "attempt": 1,
                    "startedAt": "2026-05-19T10:00:00Z",
                    "updatedAt": "2026-05-19T10:01:00Z",
                    "summary": None,
                    "checks": [],
                    "refs": {
                        "childWorkflowId": None,
                        "childRunId": None,
                        "agentRunId": None,
                        "latestStepExecutionManifestRef": "art-attempt-1",
                        "stepExecutionManifestRefs": ["art-attempt-1"],
                    },
                    "artifacts": {},
                    "lastError": None,
                }
            ],
        },
    )
    _override_user_dependencies(app, is_superuser=True)
    artifact_service = SimpleNamespace(
        read=AsyncMock(side_effect=TemporalArtifactAuthorizationError())
    )
    app.dependency_overrides[get_async_session] = _empty_session_override

    with patch(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            response = test_client.get(
                "/api/executions/mm:wf-1/steps/implement/step-executions"
            )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "step_execution_manifest_unauthorized"

def test_get_execution_steps_returns_503_for_temporal_rpc_errors() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        error=RPCError("Connection failed", RPCStatusCode.UNAVAILABLE, None),
    )
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "temporal_unavailable"

def test_get_execution_steps_returns_503_for_slow_temporal_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={"workflowId": "mm:wf-1", "runId": "run-99", "steps": []},
        delay_seconds=0.2,
    )
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "live_query_timeout_seconds",
        0.01,
    )
    observed_timeouts: list[float] = []

    async def fail_fast_timeout(awaitable, *, timeout: float):
        observed_timeouts.append(timeout)
        awaitable.close()
        raise TimeoutError

    with (
        patch(
            "api_service.api.routers.executions.asyncio.wait_for",
            side_effect=fail_fast_timeout,
        ),
        TestClient(app) as test_client,
    ):
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 503
    assert observed_timeouts == [0.01]
    assert response.json()["detail"]["code"] == "temporal_unavailable"

def test_get_execution_steps_uses_projection_fallback_when_temporal_query_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    user = _override_user_dependencies(app, is_superuser=False)
    record = _build_execution_record(owner_id=str(user.id))
    record.memo = {
        **record.memo,
        "summary": "Executing plan step 1/1: implement",
    }
    record.parameters = {
        "workflow": {
            "steps": [
                {
                    "id": "implement",
                    "title": "Implement fix",
                    "type": "skill",
                    "skill": {"id": "pr-resolver"},
                },
            ],
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    session = AsyncMock()
    app.dependency_overrides[get_async_session] = lambda: session
    _override_query_client(
        app,
        error=RPCError(
            "Unable to query workflow due to Workflow Task in failed state.",
            RPCStatusCode.FAILED_PRECONDITION,
            None,
        ),
    )
    monkeypatch.setattr(settings.temporal, "temporal_authoritative_read_enabled", True)

    async def _raise_sync_failure(*_args, **_kwargs):
        raise RPCError("Temporal query failed", RPCStatusCode.FAILED_PRECONDITION, None)

    monkeypatch.setattr(
        "api_service.core.sync.fetch_and_sync_execution",
        _raise_sync_failure,
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflowId"] == "mm:wf-1"
    assert payload["steps"][0]["logicalStepId"] == "implement"
    assert payload["steps"][0]["status"] == "executing"
    session.rollback.assert_awaited_once()
    assert mock_service.describe_execution.await_args.kwargs["include_orphaned"] is True

def test_get_execution_steps_falls_back_to_stored_task_steps_when_temporal_query_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    record.memo = {
        **record.memo,
        "summary": "Executing plan step 2/2: moonspec-implement",
    }
    record.parameters = {
        "workflow": {
            "steps": [
                {
                    "id": "fetch-issue",
                    "title": "Fetch issue",
                    "type": "tool",
                    "tool": {"id": "jira.get_issue"},
                },
                {
                    "id": "implement",
                    "title": "Implement issue",
                    "type": "skill",
                    "skill": {"id": "moonspec-implement"},
                    "dependsOn": ["fetch-issue"],
                },
            ],
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={"workflowId": "mm:wf-1", "runId": "run-99", "steps": []},
        delay_seconds=0.2,
    )
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "live_query_timeout_seconds",
        0.01,
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflowId"] == "mm:wf-1"
    assert payload["runId"] == "run-2"
    assert [step["logicalStepId"] for step in payload["steps"]] == [
        "fetch-issue",
        "implement",
    ]
    assert payload["steps"][0]["tool"] == {
        "type": "tool",
        "name": "jira.get_issue",
    }
    assert payload["steps"][1]["tool"]["name"] == "moonspec-implement"
    assert payload["steps"][1]["dependsOn"] == ["fetch-issue"]
    assert payload["steps"][1]["status"] == "executing"
    assert payload["steps"][1]["executionOrdinal"] == 1

def test_get_execution_steps_fallback_prefers_structured_step_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    record.memo = {
        **record.memo,
        "summary": "Executing plan step 1/2: fetch-issue",
        "mm_current_step_order": 2,
    }
    record.parameters = {
        "workflow": {
            "steps": [
                {
                    "id": "fetch-issue",
                    "title": "Fetch issue",
                    "type": "tool",
                    "tool": {"id": "jira.get_issue"},
                },
                {
                    "id": "implement",
                    "title": "Implement issue",
                    "type": "skill",
                    "skill": {"id": "moonspec-implement"},
                    "dependsOn": ["fetch-issue"],
                },
            ],
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={"workflowId": "mm:wf-1", "runId": "run-99", "steps": []},
        delay_seconds=0.2,
    )
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "live_query_timeout_seconds",
        0.01,
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 200
    payload = response.json()
    # The structured memo field wins over the stale summary string.
    assert payload["steps"][1]["status"] == "executing"
    assert payload["steps"][0]["status"] == "ready"

def test_get_execution_steps_fallback_preserves_independent_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    record.memo = {
        **record.memo,
        "summary": "Executing plan step 1/3: alpha",
    }
    record.parameters = {
        "workflow": {
            "steps": [
                {
                    "id": "alpha",
                    "title": "Alpha",
                    "type": "tool",
                    "tool": {"id": "tool.alpha"},
                },
                {
                    "id": "beta",
                    "title": "Beta",
                    "type": "tool",
                    "tool": {"id": "tool.beta"},
                },
                {
                    "id": "gamma",
                    "title": "Gamma",
                    "type": "tool",
                    "tool": {"id": "tool.gamma"},
                },
            ],
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={"workflowId": "mm:wf-1", "runId": "run-99", "steps": []},
        delay_seconds=0.2,
    )
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "live_query_timeout_seconds",
        0.01,
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 200
    payload = response.json()
    # No step declared ``dependsOn`` so the fallback must not fabricate a chain
    # — independent steps should remain runnable in parallel.
    assert payload["steps"][0]["dependsOn"] == []
    assert payload["steps"][1]["dependsOn"] == []
    assert payload["steps"][2]["dependsOn"] == []
    assert payload["steps"][0]["status"] == "executing"
    assert payload["steps"][1]["status"] == "ready"
    assert payload["steps"][2]["status"] == "ready"


def test_get_execution_steps_uses_terminal_step_ledger_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.close_status = TemporalExecutionCloseStatus.FAILED
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        ledger={"workflowId": "mm:wf-1", "runId": "run-99", "steps": []},
    )
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "live_query_timeout_seconds",
        0.01,
    )
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "terminal_step_ledger_query_timeout_seconds",
        12.0,
    )
    observed_timeouts: list[float] = []

    async def passthrough_timeout(
        awaitable,
        timeout: float | None = None,
        *_args,
        **_kwargs,
    ):
        if timeout is not None:
            observed_timeouts.append(timeout)
        return await awaitable

    with (
        patch(
            "api_service.api.routers.executions.asyncio.wait_for",
            side_effect=passthrough_timeout,
        ),
        TestClient(app) as test_client,
    ):
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 200
    assert observed_timeouts == [12.0]


def test_get_execution_steps_does_not_use_projection_fallback_for_terminal_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.close_status = TemporalExecutionCloseStatus.FAILED
    record.memo = {
        **record.memo,
        "summary": "Executing plan step 2/3: verify",
        "mm_current_step_order": 2,
    }
    record.parameters = {
        "workflow": {
            "steps": [
                {
                    "id": "plan",
                    "title": "Plan",
                    "type": "skill",
                    "skill": {"id": "plan"},
                },
                {
                    "id": "verify",
                    "title": "Verify",
                    "type": "skill",
                    "skill": {"id": "verify"},
                },
                {
                    "id": "publish",
                    "title": "Publish",
                    "type": "skill",
                    "skill": {"id": "publish"},
                },
            ],
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(
        app,
        error=RPCError("Connection failed", RPCStatusCode.UNAVAILABLE, None),
    )
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "terminal_step_ledger_query_timeout_seconds",
        12.0,
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "temporal_unavailable"


def test_get_execution_steps_returns_500_for_invalid_ledger_payload() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(app, ledger={})
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "invalid_execution_query_payload"

def test_get_execution_steps_rejects_unsupported_workflow_types() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        workflow_type=TemporalWorkflowType.MANIFEST_INGEST
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_query_client(app, ledger={})
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1/steps")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_execution_query"

def test_describe_execution_includes_report_projection_when_latest_report_artifacts_exist() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        get=AsyncMock(return_value=None),
        rollback=AsyncMock(),
    )
    _override_temporal_client(app)
    user = _override_user_dependencies(app, is_superuser=True)

    primary_artifact = SimpleNamespace(
        artifact_id='art-primary',
        status=TemporalArtifactStatus.COMPLETE,
        sha256='sha-primary',
        size_bytes=128,
        content_type='text/markdown',
        encryption=TemporalArtifactEncryption.NONE,
        metadata_json={
            'report_type': 'security_pentest_report',
            'report_scope': 'final',
            'finding_counts': {'total': 3},
            'severity_counts': {'high': 1},
        },
    )
    summary_artifact = SimpleNamespace(
        artifact_id='art-summary',
        status=TemporalArtifactStatus.COMPLETE,
        sha256='sha-summary',
        size_bytes=64,
        content_type='application/json',
        encryption=TemporalArtifactEncryption.NONE,
        metadata_json={
            'report_type': 'security_pentest_report',
            'report_scope': 'final',
        },
    )
    structured_artifact = SimpleNamespace(
        artifact_id='art-structured',
        status=TemporalArtifactStatus.COMPLETE,
        sha256='sha-structured',
        size_bytes=96,
        content_type='application/json',
        encryption=TemporalArtifactEncryption.NONE,
        metadata_json={
            'report_type': 'security_pentest_report',
            'report_scope': 'final',
        },
    )
    evidence_artifact = SimpleNamespace(
        artifact_id='art-evidence',
        status=TemporalArtifactStatus.COMPLETE,
        sha256='sha-evidence',
        size_bytes=256,
        content_type='application/zstd',
        encryption=TemporalArtifactEncryption.NONE,
        metadata_json={
            'report_type': 'security_pentest_report',
            'report_scope': 'final',
        },
    )
    artifact_service = SimpleNamespace(
        list_for_execution=AsyncMock(
            side_effect=[
                [primary_artifact],
                [summary_artifact],
                [structured_artifact],
                [evidence_artifact],
            ]
        )
    )

    with patch(
        'api_service.api.routers.executions.get_temporal_artifact_service',
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            response = test_client.get('/api/executions/mm:wf-1')

    assert response.status_code == 200
    payload = response.json()
    assert artifact_service.list_for_execution.await_args_list == [
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.primary',
            latest_only=True,
        ),
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.summary',
            latest_only=True,
        ),
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.structured',
            latest_only=True,
        ),
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.evidence',
            latest_only=True,
        ),
    ]
    assert payload['reportProjection'] == {
        'hasReport': True,
        'latestReportRef': {
            'artifact_ref_v': 1,
            'artifact_id': 'art-primary',
        },
        'latestReportSummaryRef': {
            'artifact_ref_v': 1,
            'artifact_id': 'art-summary',
        },
        'reportArtifactRefs': {
            'report.primary': {
                'artifact_ref_v': 1,
                'artifact_id': 'art-primary',
            },
            'report.summary': {
                'artifact_ref_v': 1,
                'artifact_id': 'art-summary',
            },
            'report.structured': {
                'artifact_ref_v': 1,
                'artifact_id': 'art-structured',
            },
            'report.evidence': {
                'artifact_ref_v': 1,
                'artifact_id': 'art-evidence',
            },
        },
        'reportType': 'security_pentest_report',
        'reportStatus': 'final',
        'findingCounts': {'total': 3},
        'severityCounts': {'high': 1},
    }

def test_describe_execution_report_projection_degrades_safely_when_no_report_exists() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        get=AsyncMock(return_value=None),
        rollback=AsyncMock(),
    )
    _override_temporal_client(app)
    user = _override_user_dependencies(app, is_superuser=True)

    artifact_service = SimpleNamespace(list_for_execution=AsyncMock(return_value=[]))

    with patch(
        'api_service.api.routers.executions.get_temporal_artifact_service',
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            response = test_client.get('/api/executions/mm:wf-1')

    assert response.status_code == 200
    payload = response.json()
    assert artifact_service.list_for_execution.await_args_list == [
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.primary',
            latest_only=True,
        ),
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.summary',
            latest_only=True,
        ),
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.structured',
            latest_only=True,
        ),
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.evidence',
            latest_only=True,
        ),
    ]
    assert payload['reportProjection'] == {'hasReport': False}

def test_describe_execution_report_projection_ignores_incomplete_report_artifacts() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        get=AsyncMock(return_value=None),
        rollback=AsyncMock(),
    )
    _override_temporal_client(app)
    user = _override_user_dependencies(app, is_superuser=True)

    pending_primary = SimpleNamespace(
        artifact_id='art-primary-pending',
        status=TemporalArtifactStatus.PENDING_UPLOAD,
        sha256='sha-primary-pending',
        size_bytes=256,
        content_type='text/markdown',
        encryption=TemporalArtifactEncryption.NONE,
        metadata_json={'report_type': 'security_pentest_report', 'report_scope': 'final'},
    )
    pending_summary = SimpleNamespace(
        artifact_id='art-summary-pending',
        status=TemporalArtifactStatus.PENDING_UPLOAD,
        sha256='sha-summary-pending',
        size_bytes=64,
        content_type='application/json',
        encryption=TemporalArtifactEncryption.NONE,
        metadata_json={'report_type': 'security_pentest_report', 'report_scope': 'final'},
    )
    artifact_service = SimpleNamespace(
        list_for_execution=AsyncMock(
            side_effect=[[pending_primary], [pending_summary], [], []]
        )
    )

    with patch(
        'api_service.api.routers.executions.get_temporal_artifact_service',
        return_value=artifact_service,
    ):
        with TestClient(app) as test_client:
            response = test_client.get('/api/executions/mm:wf-1')

    assert response.status_code == 200
    payload = response.json()
    assert artifact_service.list_for_execution.await_args_list == [
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.primary',
            latest_only=True,
        ),
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.summary',
            latest_only=True,
        ),
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.structured',
            latest_only=True,
        ),
        call(
            namespace='moonmind',
            workflow_id='mm:wf-1',
            run_id='run-2',
            principal=str(user.id),
            link_type='report.evidence',
            latest_only=True,
        ),
    ]
    assert payload['reportProjection'] == {'hasReport': False}

def test_describe_execution_hydrates_provider_profile_metadata() -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record()
    record.parameters = {"profileId": "profile:gemini-default"}
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                provider_id="google",
                provider_label="Google",
            )
        ),
        rollback=AsyncMock(),
    )
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["profileId"] == "profile:gemini-default"
    assert payload["providerId"] == "google"
    assert payload["providerLabel"] == "Google"
    app.dependency_overrides.clear()

def test_describe_execution_falls_back_to_managed_run_store_agent_run_id(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
) -> None:
    test_client, service, user = client
    record = _build_execution_record(owner_id=str(user.id))
    record.memo = {"title": "Temporal task", "summary": "Waiting on review."}
    record.parameters = {}
    record.search_attributes = {
        "mm_owner_id": str(user.id),
        "mm_owner_type": "user",
        "mm_entry": "run",
    }
    service.describe_execution.return_value = record

    to_thread_calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    async def _fake_to_thread(
        func: object, /, *args: object, **kwargs: object
    ) -> dict[str, str]:
        to_thread_calls.append((func, args, kwargs))
        return {"mm:wf-1": "550e8400-e29b-41d4-a716-446655440000"}

    with patch(
        "api_service.api.routers.executions.asyncio.to_thread",
        new=_fake_to_thread,
    ):
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    assert "task" + "RunId" not in response.json()
    assert response.json()["agentRunId"] == "550e8400-e29b-41d4-a716-446655440000"
    assert len(to_thread_calls) == 1
    assert to_thread_calls[0][1] == (("mm:wf-1",),)

def test_request_rerun_update_response_includes_continue_as_new_cause() -> None:
    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record()
        service.update_execution.return_value = {
            "accepted": True,
            "applied": "continue_as_new",
            "message": "Rerun requested. Execution continued as new run.",
            "continue_as_new_cause": "manual_rerun",
        }

        response = test_client.post(
            "/api/executions/mm:wf-1/update",
            json={
                "updateName": "RequestRerun",
                "idempotencyKey": "rerun-1",
            },
        )

        assert response.status_code == 200
        assert response.json()["continueAsNewCause"] == "manual_rerun"

def test_request_rerun_update_redirects_response_to_created_rerun_execution() -> None:
    for test_client, service in _client_with_service():
        source_record = _build_execution_record()
        rerun_record = _build_execution_record()
        rerun_record.workflow_id = "mm:rerun-created"
        rerun_record.run_id = "run-rerun"
        rerun_record.memo = {
            **rerun_record.memo,
            "latest_temporal_run_id": "run-rerun",
        }
        service.describe_execution.side_effect = [source_record, rerun_record]
        service.update_execution.return_value = {
            "accepted": True,
            "applied": "continue_as_new",
            "message": "Rerun requested. New execution created.",
            "continue_as_new_cause": "manual_rerun",
            "workflow_id": "mm:rerun-created",
        }

        response = test_client.post(
            "/api/executions/mm:wf-1/update",
            json={
                "updateName": "RequestRerun",
                "idempotencyKey": "rerun-1",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["execution"]["workflowId"] == "mm:rerun-created"
        assert body["execution"]["redirectPath"] == (
            "/workflows/mm:rerun-created?source=temporal"
        )
        assert service.describe_execution.await_args_list[-1].args == (
            "mm:rerun-created",
        )


@pytest.mark.asyncio
async def test_request_rerun_update_flushes_snapshot_reuse_before_serializing_response(
    tmp_path,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path}/rerun_update_response.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            service = TemporalExecutionService(session)
            service._client_adapter.start_workflow = AsyncMock(
                return_value=SimpleNamespace(run_id="run-source")
            )
            service._client_adapter.cancel_workflow = AsyncMock()
            service._client_adapter.update_workflow = AsyncMock()

            user = SimpleNamespace(
                id=uuid4(),
                email="rerun@example.com",
                is_active=True,
                is_superuser=False,
            )
            created = await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=user.id,
                title="Rerun source",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={"workflow": {"instructions": "Do the work."}},
                idempotency_key=None,
            )
            source_workflow_id = created.workflow_id
            await service.cancel_execution(
                workflow_id=source_workflow_id,
                reason="terminal source",
                graceful=True,
            )

            session.add(
                TemporalArtifact(
                    artifact_id="art_snapshot_route_flush",
                    storage_key="tests/art_snapshot_route_flush.json",
                    storage_backend=TemporalArtifactStorageBackend.S3,
                    encryption=TemporalArtifactEncryption.NONE,
                    status=TemporalArtifactStatus.COMPLETE,
                    retention_class=TemporalArtifactRetentionClass.LONG,
                    redaction_level=TemporalArtifactRedactionLevel.NONE,
                    upload_mode=TemporalArtifactUploadMode.SINGLE_PUT,
                    metadata_json={},
                )
            )
            source_records = []
            for record_type in (
                TemporalExecutionCanonicalRecord,
                TemporalExecutionRecord,
            ):
                source_record = await session.get(record_type, source_workflow_id)
                assert source_record is not None
                source_record.memo = {
                    **dict(source_record.memo or {}),
                    "task_input_snapshot_ref": "art_snapshot_route_flush",
                    "task_input_snapshot_version": 1,
                    "task_input_snapshot_source_kind": "create",
                }
                source_record.artifact_refs = [
                    *list(source_record.artifact_refs or []),
                    "art_snapshot_route_flush",
                ]
                source_records.append(source_record)
            await session.commit()
            for source_record in source_records:
                await session.refresh(source_record)

            response = await update_execution_route(
                workflow_id=source_workflow_id,
                payload=UpdateExecutionRequest(updateName="RequestRerun"),
                response=Response(),
                service=service,
                session=session,
                user=user,
                _actions_enabled=None,
            )

        assert response.accepted is True
        assert response.execution.workflow_id != source_workflow_id
        assert response.execution.redirect_path == (
            f"/workflows/{response.execution.workflow_id}?source=temporal"
        )
    finally:
        await engine.dispose()


def test_request_rerun_update_snapshot_hydrates_instructions_from_input_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    source_record = _build_execution_record(has_workflow_input_snapshot=False)
    rerun_record = _build_execution_record(has_workflow_input_snapshot=False)
    rerun_record.workflow_id = "mm:rerun-created"
    rerun_record.run_id = "run-rerun"
    rerun_record.input_ref = "art-full-input"
    rerun_record.parameters = {
        "repository": "Moon/Mind",
        "targetRuntime": "codex_cli",
        "workflow": {
            "title": "Hydrated rerun",
            "steps": [
                {"id": "step-1", "title": "First"},
                {"id": "step-2", "title": "Second"},
            ],
        },
    }
    service.describe_execution.side_effect = [source_record, rerun_record]
    service.update_execution.return_value = {
        "accepted": True,
        "applied": "continue_as_new",
        "message": "Rerun requested. New execution created.",
        "continue_as_new_cause": "manual_rerun",
        "workflow_id": "mm:rerun-created",
    }
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    user = _override_user_dependencies(app, is_superuser=True)
    session = AsyncMock()
    app.dependency_overrides[get_async_session] = lambda: session
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard, "temporal_workflow_editing_enabled", True
    )
    artifact_payload = {
        "repository": "Moon/Mind",
        "targetRuntime": "codex_cli",
        "workflow": {
            "title": "Hydrated rerun",
            "instructions": "Top-level rerun instructions.",
            "steps": [
                {
                    "id": "step-1",
                    "title": "First",
                    "instructions": "First step instructions.",
                },
                {
                    "id": "step-2",
                    "title": "Second",
                    "instructions": "Second step instructions.",
                },
            ],
        },
    }
    artifact_service = SimpleNamespace(
        read=AsyncMock(
            return_value=(
                SimpleNamespace(artifact_id="art-full-input"),
                json.dumps(artifact_payload).encode("utf-8"),
            )
        )
    )
    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda _session: artifact_service,
    )
    captured_workflow_payload: dict[str, object] = {}

    async def _persist_snapshot(**kwargs) -> str:
        captured_workflow_payload.update(kwargs["task_payload"])
        target_record = kwargs["record"]
        target_record.memo = {
            **dict(target_record.memo or {}),
            "task_input_snapshot_ref": "art_snapshot_hydrated",
            "task_input_snapshot_version": 1,
            "task_input_snapshot_source_kind": "rerun",
        }
        return "art_snapshot_hydrated"

    persist_mock = AsyncMock(side_effect=_persist_snapshot)
    monkeypatch.setattr(
        "api_service.api.routers.executions._persist_original_workflow_input_snapshot",
        persist_mock,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/update",
            json={
                "updateName": "RequestRerun",
                "idempotencyKey": "rerun-1",
                "inputArtifactRef": "artifact://input/art-full-input",
                "parametersPatch": rerun_record.parameters,
            },
        )

    assert response.status_code == 200
    artifact_service.read.assert_awaited_once_with(
        artifact_id="art-full-input",
        principal=str(user.id),
        allow_restricted_raw=True,
    )
    persist_mock.assert_awaited_once()
    assert captured_workflow_payload["instructions"] == "Top-level rerun instructions."
    steps = captured_workflow_payload["steps"]
    assert steps[0]["instructions"] == "First step instructions."
    assert steps[1]["instructions"] == "Second step instructions."
    session.commit.assert_awaited_once()


def test_task_input_snapshot_artifact_id_strips_input_prefix_without_scheme() -> None:
    assert _artifact_id_from_ref("input/art-full-input") == "art-full-input"
    assert _artifact_id_from_ref("artifact://input/art-full-input") == "art-full-input"


def test_task_input_snapshot_merge_preserves_step_deletions() -> None:
    merged = _merge_workflow_preserving_artifact_instructions(
        {
            "steps": [
                {"id": "step-1", "title": "First", "instructions": "Original first"},
                {"id": "step-2", "title": "Second", "instructions": "Original second"},
            ]
        },
        {"steps": [{"id": "step-1", "title": "First edited"}]},
    )

    assert merged["steps"] == [
        {"id": "step-1", "title": "First edited", "instructions": "Original first"}
    ]


def test_original_task_input_snapshot_payload_preserves_mm639_authored_fields() -> None:
    task_payload = _mm639_authored_task_payload()

    payload = _build_original_workflow_input_snapshot_payload(
        source_kind="create",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex_cli",
            "requiredCapabilities": ["git", "jira"],
        },
        task_payload=task_payload,
        attachment_refs=[
            {
                "artifactId": "art-objective",
                "targetKind": "objective",
            },
            {
                "artifactId": "art-step",
                "targetKind": "step",
                "stepId": "step-2",
                "stepOrdinal": 1,
            },
        ],
    )

    authored = payload["draft"]["authoredWorkflowInput"]
    assert authored["traceability"]["jiraIssueKey"] == "MM-639"
    assert authored["objective"]["instructions"] == (
        "Preserve the original authored task input for MM-639."
    )
    assert authored["objective"]["inputAttachments"][0]["artifactId"] == (
        "art-objective"
    )
    assert authored["runtime"] == task_payload["runtime"]
    assert authored["publish"] == task_payload["publish"]
    assert authored["repository"] == "MoonLadderStudios/MoonMind"
    assert authored["branch"] == "feature/mm-639"
    assert authored["dependencyDeclarations"] == ["MM-638"]
    assert authored["presetApplicationMetadata"] == task_payload[
        "appliedStepTemplates"
    ]
    assert authored["pinnedPresetBindings"] == task_payload["authoredPresets"]
    assert authored["includeTreeSummary"] == [
        {
            "presetSlug": "jira-orchestrate",
            "presetDigest": None,
            "includedSlug": "jira-fetch",
            "includedDigest": None,
        }
    ]
    assert authored["finalSubmittedOrder"] == [
        {"stepId": "step-1", "ordinal": 0},
        {"stepId": "step-2", "ordinal": 1},
    ]
    assert authored["perStepProvenance"][1] == {
        "stepId": "step-2",
        "ordinal": 1,
        "presetProvenance": task_payload["steps"][1]["presetProvenance"],
    }
    assert authored["detachmentState"] == [
        {"stepId": "step-2", "ordinal": 1, "detached": True}
    ]
    assert authored["steps"][1]["inputAttachments"][0]["artifactId"] == "art-step"
    assert payload["attachmentRefs"][1]["stepId"] == "step-2"


def test_missing_attachment_aware_snapshot_descriptor_is_degraded_explicitly() -> None:
    record = _build_execution_record(
        has_workflow_input_snapshot=False,
    )
    record.parameters = {
        "workflow": {
            "instructions": "Attachment-aware task without a snapshot.",
            "inputAttachments": [
                {
                    "artifactId": "art-objective",
                    "filename": "objective.png",
                    "contentType": "image/png",
                }
            ],
        }
    }

    descriptor = _workflow_input_snapshot_descriptor_from_record(record)

    assert descriptor.available is False
    assert descriptor.reconstruction_mode == "degraded_read_only"
    assert descriptor.disabled_reasons["draft"] == (
        "original_task_input_snapshot_missing"
    )
    assert descriptor.disabled_reasons["attachments"] == (
        "original_task_input_snapshot_missing"
    )


def test_missing_legacy_attachment_ref_snapshot_descriptor_is_degraded() -> None:
    record = _build_execution_record(
        has_workflow_input_snapshot=False,
    )
    record.parameters = {
        "workflow": {
            "instructions": "Legacy attachment-aware task without a snapshot.",
            "attachmentRefs": [
                {
                    "artifactRef": "artifact://input/objective-image",
                    "filename": "objective.png",
                    "contentType": "image/png",
                }
            ],
            "steps": [
                {
                    "id": "inspect",
                    "attachmentRefs": [
                        {
                            "artifactRef": "artifact://input/step-image",
                            "filename": "step.png",
                            "contentType": "image/png",
                        }
                    ],
                }
            ],
        }
    }

    descriptor = _workflow_input_snapshot_descriptor_from_record(record)

    assert descriptor.available is False
    assert descriptor.reconstruction_mode == "degraded_read_only"
    assert descriptor.disabled_reasons["draft"] == (
        "original_task_input_snapshot_missing"
    )
    assert descriptor.disabled_reasons["attachments"] == (
        "original_task_input_snapshot_missing"
    )


def test_task_editing_update_route_emits_attempt_and_result_metrics() -> None:
    metrics = Mock()
    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record()
        service.update_execution.return_value = {
            "accepted": True,
            "applied": "next_safe_point",
            "message": "Inputs scheduled.",
        }

        with patch(
            "api_service.api.routers.executions.get_metrics_emitter",
            return_value=metrics,
        ):
            response = test_client.post(
                "/api/executions/mm:wf-1/update",
                json={
                    "updateName": "UpdateInputs",
                    "inputArtifactRef": "artifact://input/new",
                    "parametersPatch": {
                        "workflow": {"instructions": "Edited instructions."}
                    },
                },
            )

        assert response.status_code == 200
        metric_calls = [
            call
            for call in metrics.increment.call_args_list
            if call.args[0] == "temporal_workflow_editing.event"
        ]
        assert len(metric_calls) == 2
        assert metric_calls[0].kwargs["tags"] == {
            "event": "submit_attempt",
            "update_name": "UpdateInputs",
            "workflow_type": "MoonMind.UserWorkflow",
            "state": "executing",
        }
        assert metric_calls[1].kwargs["tags"] == {
            "event": "submit_result",
            "update_name": "UpdateInputs",
            "workflow_type": "MoonMind.UserWorkflow",
            "state": "executing",
            "result": "success",
            "applied": "next_safe_point",
        }

def test_task_editing_update_route_emits_failure_reason_metrics() -> None:
    metrics = Mock()
    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record()
        service.update_execution.side_effect = TemporalExecutionValidationError(
            "Workflow state changed and rerun is no longer available."
        )

        with patch(
            "api_service.api.routers.executions.get_metrics_emitter",
            return_value=metrics,
        ):
            response = test_client.post(
                "/api/executions/mm:wf-1/update",
                json={"updateName": "RequestRerun"},
            )

        assert response.status_code == 422
        metric_calls = [
            call
            for call in metrics.increment.call_args_list
            if call.args[0] == "temporal_workflow_editing.event"
        ]
        assert metric_calls[1].kwargs["tags"] == {
            "event": "submit_result",
            "update_name": "RequestRerun",
            "workflow_type": "MoonMind.UserWorkflow",
            "state": "executing",
            "result": "failure",
            "reason": "validation",
        }

def test_list_executions_preserves_logical_identity_fields() -> None:
    for test_client, service in _client_with_service():
        service.list_executions.return_value = SimpleNamespace(
            items=[_build_execution_record()],
            next_page_token="cursor-1",
            count=1,
        )

        response = test_client.get("/api/executions")

        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 1
        assert payload["nextPageToken"] == "cursor-1"
        item = payload["items"][0]
        assert item["workflowId"] == "mm:wf-1"
        assert "taskId" not in item
        assert item["runId"] == "run-2"
        assert "temporalRunId" not in item
        assert item["latestRunView"] is True
        assert item["continueAsNewCause"] == "manual_rerun"
        for detail_only_key in (
            "memo",
            "searchAttributes",
            "inputParameters",
            "taskInstructions",
            "artifactRefs",
            "finishSummary",
            "debugFields",
            "runMetrics",
            "logContext",
        ):
            assert detail_only_key not in item


def test_list_executions_reads_progress_from_persisted_finish_summary() -> None:
    for test_client, service in _client_with_service():
        record = _build_execution_record(state=MoonMindWorkflowState.COMPLETED)
        record.close_status = TemporalExecutionCloseStatus.COMPLETED
        record.finish_summary_json = {
            "progress": {
                "total": 4,
                "pending": 0,
                "ready": 0,
                "executing": 0,
                "awaitingExternal": 0,
                "reviewing": 0,
                "completed": 4,
                "failed": 0,
                "skipped": 0,
                "canceled": 0,
                "currentStepTitle": "Verify compact response",
            }
        }
        service.list_executions.return_value = SimpleNamespace(
            items=[record],
            next_page_token=None,
            count=1,
        )

        response = test_client.get("/api/executions")

        assert response.status_code == 200
        progress = response.json()["items"][0]["progress"]
        assert progress["total"] == 4
        assert progress["completed"] == 4
        assert progress["currentStepTitle"] == "Verify compact response"


def test_describe_manifest_execution_exposes_bounded_manifest_fields() -> None:
    """Manifest ingest detail should expose refs, policy, and bounded counts."""

    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            workflow_type=TemporalWorkflowType.MANIFEST_INGEST
        )

        response = test_client.get("/api/executions/mm:wf-1")

        assert response.status_code == 200
        payload = response.json()
        assert payload["workflowType"] == "MoonMind.ManifestIngest"
        assert payload["manifestArtifactRef"] == "art_manifest_1"
        assert payload["planArtifactRef"] == "art_plan_1"
        assert payload["executionPolicy"]["maxConcurrency"] == 3
        assert payload["counts"]["ready"] == 1
        assert payload["counts"]["running"] == 1

def test_describe_execution_enriches_dependency_summaries_without_dunder_dict() -> None:
    for test_client, service in _client_with_service():
        record = _build_execution_record()
        record.parameters = {"workflow": {"dependsOn": ["mm:dep-1"]}}
        service.describe_execution.return_value = record
        service.enrich_dependency_summaries.side_effect = [
            [
                ExecutionDependencySummary(
                    workflow_id="mm:dep-1",
                    title="Dependency",
                    summary="done",
                    state="completed",
                    close_status="completed",
                    workflow_type="MoonMind.UserWorkflow",
                )
            ],
            [
                ExecutionDependencySummary(
                    workflow_id="mm:dep-2",
                    title="Dependent",
                    summary="waiting",
                    state="executing",
                    close_status=None,
                    workflow_type="MoonMind.UserWorkflow",
                )
            ],
        ]
        service.list_dependents.return_value = [
            SimpleNamespace(dependent_workflow_id="mm:dep-2")
        ]

        response = test_client.get("/api/executions/mm:wf-1")

        assert response.status_code == 200
        payload = response.json()
        assert payload["prerequisites"] == [
            {
                "workflowId": "mm:dep-1",
                "title": "Dependency",
                "summary": "done",
                "state": "completed",
                "closeStatus": "completed",
                "workflowType": "MoonMind.UserWorkflow",
            }
        ]
        assert payload["dependents"] == [
            {
                "workflowId": "mm:dep-2",
                "title": "Dependent",
                "summary": "waiting",
                "state": "executing",
                "closeStatus": None,
                "workflowType": "MoonMind.UserWorkflow",
            }
        ]

def test_manifest_update_route_passes_manifest_specific_fields() -> None:
    """Manifest-specific update requests should be forwarded unchanged to the service."""

    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            workflow_type=TemporalWorkflowType.MANIFEST_INGEST
        )
        service.update_execution.return_value = {
            "accepted": True,
            "applied": "next_safe_point",
            "message": "Manifest update accepted and will be applied at the next safe point.",
        }

        response = test_client.post(
            "/api/executions/mm:wf-1/update",
            json={
                "updateName": "UpdateManifest",
                "newManifestArtifactRef": "art_manifest_2",
                "mode": "REPLACE_FUTURE",
                "idempotencyKey": "manifest-update-1",
            },
        )

        assert response.status_code == 200
        called = service.update_execution.await_args.kwargs
        assert called["update_name"] == "UpdateManifest"
        assert called["new_manifest_artifact_ref"] == "art_manifest_2"
        assert called["mode"] == "REPLACE_FUTURE"

def test_manifest_status_route_returns_bounded_snapshot() -> None:
    """Manifest status route should return the service snapshot unchanged."""

    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            workflow_type=TemporalWorkflowType.MANIFEST_INGEST
        )
        service.describe_manifest_status.return_value = {
            "workflowId": "mm:wf-1",
            "state": "executing",
            "phase": "executing",
            "paused": False,
            "maxConcurrency": 3,
            "failurePolicy": "best_effort",
            "counts": {
                "pending": 0,
                "ready": 1,
                "running": 1,
                "completed": 0,
                "failed": 0,
                "canceled": 0,
            },
        }

        response = test_client.get("/api/executions/mm:wf-1/manifest-status")

        assert response.status_code == 200
        assert response.json()["counts"]["running"] == 1

def test_manifest_nodes_route_returns_page_payload() -> None:
    """Manifest node page route should preserve cursor and count fields."""

    for test_client, service in _client_with_service():
        service.describe_execution.return_value = _build_execution_record(
            workflow_type=TemporalWorkflowType.MANIFEST_INGEST
        )
        service.list_manifest_nodes.return_value = {
            "items": [
                {
                    "nodeId": "node-b",
                    "state": "running",
                    "workflowType": "MoonMind.UserWorkflow",
                }
            ],
            "nextCursor": "cursor-1",
            "count": 1,
        }

        response = test_client.get(
            "/api/executions/mm:wf-1/manifest-nodes",
            params={"state": "running", "limit": 25},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert body["nextCursor"] == "cursor-1"
        assert body["items"][0]["nodeId"] == "node-b"
        assert body["items"][0]["workflowType"] == "MoonMind.UserWorkflow"

def test_describe_execution_includes_actions_and_debug_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        state=MoonMindWorkflowState.AWAITING_EXTERNAL
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard, "temporal_workflow_editing_enabled", True
    )
    monkeypatch.setattr(settings.temporal_dashboard, "debug_fields_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["dashboardStatus"] == "awaiting_action"
    assert body["waitingReason"] == "Waiting on review."
    assert body["attentionRequired"] is True
    assert body["actions"]["canApprove"] is True
    assert body["actions"]["canResume"] is True
    assert body["actions"]["canCancel"] is True
    assert body["actions"]["canBypassDependencies"] is False
    assert body["actions"]["canUpdateInputs"] is False
    assert body["debugFields"]["workflowId"] == "mm:wf-1"
    assert body["redirectPath"] == "/workflows/mm:wf-1?source=temporal"

def test_describe_execution_exposes_dependency_bypass_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        state=MoonMindWorkflowState.WAITING_ON_DEPENDENCIES
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["canBypassDependencies"] is True
    assert "canBypassDependencies" not in body["actions"]["disabledReasons"]

@pytest.mark.parametrize(
    "state",
    [
        MoonMindWorkflowState.SCHEDULED,
        MoonMindWorkflowState.WAITING_ON_DEPENDENCIES,
        MoonMindWorkflowState.AWAITING_SLOT,
        MoonMindWorkflowState.PLANNING,
        MoonMindWorkflowState.PROPOSALS,
        MoonMindWorkflowState.FINALIZING,
    ],
)
def test_describe_execution_exposes_pause_for_non_terminal_pause_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    state: MoonMindWorkflowState,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(state=state)
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == state.value
    assert body["actions"]["canPause"] is True
    assert "canPause" not in body["actions"]["disabledReasons"]

def test_describe_execution_exposes_lifecycle_resume_for_operator_paused_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.waiting_reason = "operator_paused"
    record.attention_required = True
    record.memo = {
        **record.memo,
        "waiting_reason": "operator_paused",
        "attention_required": True,
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "executing"
    assert body["waitingReason"] == "operator_paused"
    assert body["attentionRequired"] is True
    assert body["actions"]["canResume"] is True
    assert body["actions"]["canPause"] is False
    assert "canResume" not in body["actions"]["disabledReasons"]
    assert body["actions"]["disabledReasons"]["canPause"] == "already_paused"

def test_describe_execution_exposes_temporal_workflow_editing_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.input_ref = "artifact://input/current"
    record.plan_ref = "artifact://plan/current"
    record.parameters = {
        "targetRuntime": "codex_cli",
        "model": "gpt-5.4",
        "workflow": {"git": {"repository": "Moon/Mind"}},
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "temporal_workflow_editing_enabled",
        True,
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["workflowId"] == "mm:wf-1"
    assert body["workflowType"] == "MoonMind.UserWorkflow"
    assert body["inputArtifactRef"] == "artifact://input/current"
    assert body["planArtifactRef"] == "artifact://plan/current"
    assert body["inputParameters"]["targetRuntime"] == "codex_cli"
    assert body["actions"]["canUpdateInputs"] is True
    assert body["actions"]["canEditForRerun"] is False
    assert body["actions"]["canRerun"] is False

def test_describe_execution_exposes_edit_for_rerun_for_failed_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        state=MoonMindWorkflowState.FAILED
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["canUpdateInputs"] is False
    assert body["actions"]["canEditForRerun"] is True
    assert body["actions"]["canRerun"] is True

def test_describe_execution_exposes_failed_step_recovery_distinct_from_lifecycle_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.memo = {
        **record.memo,
        "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
        "resume_failed_step_id": "implement",
        "resume_completed_step_refs": ["artifact://completed/plan"],
        "recovery_workspace_checkpoint_ref": "artifact://workspace/before-implement",
        "resume_plan_digest": "sha256:resume-plan",
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["canResume"] is False
    assert body["actions"]["canResumeFromFailedStep"] is True
    assert "canRecoverFromFailedStep" not in body["actions"]
    assert body["resume"]["available"] is True
    assert (
        body["resume"]["checkpointRef"]
        == "artifact://resume-checkpoints/source/checkpoint-v1"
    )
    assert body["resume"]["failedStepId"] == "implement"
    assert body["resume"]["sourceRunId"] == "run-2"


def test_describe_execution_enables_failed_step_recovery_from_valid_manifest_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.memo = {
        **record.memo,
        "plan_artifact_ref": {"artifact_id": "artifact://plan/source"},
    }
    record.finish_summary_json = {
        "recoveryManifest": {
            "schemaVersion": "v1",
            "contentType": FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE,
            "resumeAllowed": True,
            "failedLogicalStepId": "implement",
            "failedExecutionOrdinal": 1,
            "validationResult": "valid",
            "checkpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
            "manifestRef": "artifact://recovery/manifest",
        }
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["canResumeFromFailedStep"] is True
    assert body["resume"]["available"] is True
    assert (
        body["resume"]["checkpointRef"]
        == "artifact://resume-checkpoints/source/checkpoint-v1"
    )
    assert body["resume"]["failedStepId"] == "implement"
    assert body["resume"]["disabledReason"] is None


def test_describe_execution_exposes_target_attachment_and_recovery_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.parameters = {
        "workflow": {
            "instructions": "Review the screenshot.",
            "attachmentRefs": [
                {
                    "artifactRef": "artifact://input/objective-image",
                    "filename": "objective.png",
                    "contentType": "image/png",
                    "sizeBytes": 12345,
                }
            ],
            "steps": [
                {
                    "id": "inspect",
                    "title": "Inspect screenshot",
                    "attachmentRefs": [
                        {
                            "artifactRef": "artifact://input/step-image",
                            "filename": "step.png",
                            "contentType": "image/png",
                        }
                    ],
                }
            ],
        },
        "targetDiagnostics": {
            "targets": [
                {
                    "targetKind": "objective",
                    "refs": [
                        {
                            "refKind": "attachment_manifest",
                            "artifactRef": "artifact://diagnostics/input-manifest",
                        }
                    ],
                },
                {
                    "targetKind": "step",
                    "stepId": "inspect",
                    "failures": [
                        {
                            "phase": "materialization",
                            "message": "Attachment download failed before step execution.",
                            "evidenceRef": "artifact://diagnostics/prepare",
                        },
                        {
                            "phase": "unknown-provider-phase",
                            "message": "Provider returned an unrecognized phase.",
                        }
                    ],
                },
            ],
            "degradedReason": "step_attachment_missing",
        },
        "recoverySource": {
            "sourceWorkflowId": "mm:source",
            "sourceRunId": "run-source",
            "preservedSteps": [
                {
                    "logicalStepId": "prepare",
                    "title": "Prepare context",
                    "sourceExecutionOrdinal": 1,
                    "sourceWorkflowId": "mm:source",
                    "sourceRunId": "run-source",
                }
            ],
        },
    }
    record.memo = {
        **record.memo,
        "recovery_checkpoint_ref": "artifact://resume/checkpoint",
        "resume_failed_step_id": "inspect",
        "recovery_workspace_checkpoint_ref": "artifact://workspace/checkpoint",
        "resume_plan_digest": "sha256:plan",
        "resume_completed_step_refs": ["artifact://completed/prepare"],
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    diagnostics = response.json()["targetDiagnostics"]
    assert diagnostics["degradedReason"] == "step_attachment_missing"
    objective = diagnostics["targets"][0]
    assert objective["targetKind"] == "objective"
    assert objective["label"] == "Task objective"
    assert objective["attachments"][0]["artifactRef"] == "artifact://input/objective-image"
    assert objective["refs"][0]["artifactRef"] == "artifact://diagnostics/input-manifest"
    step = diagnostics["targets"][1]
    assert step["targetKind"] == "step"
    assert step["stepId"] == "inspect"
    assert step["label"] == "Inspect screenshot"
    assert step["attachments"][0]["filename"] == "step.png"
    assert step["failures"][0]["phase"] == "materialization"
    assert step["failures"][1]["phase"] == "degraded"
    assert diagnostics["recovery"]["resumed"] is True
    assert diagnostics["recovery"]["sourceWorkflowId"] == "mm:source"
    assert diagnostics["recovery"]["sourceRunId"] == "run-source"
    assert diagnostics["recovery"]["checkpointRef"] == "artifact://resume/checkpoint"
    assert diagnostics["recovery"]["preservedSteps"][0]["logicalStepId"] == "prepare"

def test_describe_execution_distinguishes_empty_step_attachment_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.parameters = {
        "workflow": {
            "instructions": "Review the screenshot.",
            "inputAttachments": [
                {
                    "artifactId": "art-objective",
                    "filename": "objective.png",
                    "contentType": "image/png",
                }
            ],
            "steps": [
                {
                    "id": "inspect",
                    "title": "Inspect screenshot",
                    "instructions": "Inspect without a step attachment.",
                }
            ],
        }
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    targets = response.json()["targetDiagnostics"]["targets"]
    objective = next(target for target in targets if target["targetKind"] == "objective")
    step = next(target for target in targets if target["targetKind"] == "step")
    assert objective["attachments"][0]["artifactRef"] == "art-objective"
    assert step["stepId"] == "inspect"
    assert step["attachments"] == []


def test_describe_execution_preserves_generated_context_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.parameters = {
        "workflow": {"instructions": "Review prepared context."},
        "targetDiagnostics": {
            "targets": [
                {
                    "targetKind": "objective",
                    "refs": [
                        {
                            "refKind": "generated_context",
                            "artifactRef": "artifact://context/objective",
                        }
                    ],
                }
            ]
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    refs = response.json()["targetDiagnostics"]["targets"][0]["refs"]
    assert refs == [
        {
            "refKind": "generated_context",
            "artifactRef": "artifact://context/objective",
            "path": None,
        }
    ]


def test_describe_execution_preserves_target_semantics_for_alias_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.parameters = {
        "workflow": {
            "instructions": "Review aliased attachments.",
            "input_attachments": [
                {
                    "artifact_ref": "artifact://input/objective",
                    "filename": "objective.png",
                    "content_type": "image/png",
                }
            ],
            "steps": [
                {
                    "step_id": "inspect",
                    "title": "Inspect screenshot",
                    "input_attachments": [
                        {
                            "artifact_ref": "artifact://input/step",
                            "filename": "step.png",
                            "content_type": "image/png",
                        }
                    ],
                }
            ],
        },
        "target_diagnostics": {
            "targets": [
                {
                    "target_kind": "objective",
                    "refs": [
                        {
                            "ref_kind": "attachment_manifest",
                            "artifact_ref": "artifact://manifest/objective",
                        }
                    ],
                },
                {
                    "target_kind": "step",
                    "step_id": "inspect",
                    "refs": [
                        {
                            "ref_kind": "generated_context",
                            "artifact_ref": "artifact://context/step",
                        }
                    ],
                },
            ]
        },
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    targets = response.json()["targetDiagnostics"]["targets"]
    objective = next(target for target in targets if target["targetKind"] == "objective")
    step = next(target for target in targets if target["targetKind"] == "step")
    assert objective["attachments"][0]["artifactRef"] == "artifact://input/objective"
    assert objective["refs"][0]["artifactRef"] == "artifact://manifest/objective"
    assert step["stepId"] == "inspect"
    assert step["attachments"][0]["artifactRef"] == "artifact://input/step"
    assert step["refs"][0]["artifactRef"] == "artifact://context/step"


def test_describe_execution_surfaces_failed_step_execution_recovery_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.parameters = {
        "workflow": {"instructions": "Resume failed while executing step."},
        "targetDiagnostics": {
            "recovery": {
                "resumed": True,
                "sourceWorkflowId": "mm:source",
                "sourceRunId": "run-source",
                "failedRecoveryPhase": "failed_step_execution",
            }
        },
    }
    record.memo = {
        **record.memo,
        "recovery_checkpoint_ref": "artifact://resume/checkpoint",
        "resume_failed_step_id": "inspect",
        "recovery_workspace_checkpoint_ref": "artifact://workspace/checkpoint",
        "resume_plan_digest": "sha256:plan",
        "resume_completed_step_refs": ["artifact://completed/prepare"],
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    recovery = response.json()["targetDiagnostics"]["recovery"]
    assert recovery["sourceWorkflowId"] == "mm:source"
    assert recovery["sourceRunId"] == "run-source"
    assert recovery["failedRecoveryPhase"] == "failed_step_execution"


def test_describe_execution_prefers_diagnostics_failed_phase_over_disabled_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.parameters = {
        "workflow": {"instructions": "Resume failed while executing step."},
        "targetDiagnostics": {
            "recovery": {
                "resumed": True,
                "sourceWorkflowId": "mm:source",
                "sourceRunId": "run-source",
                "failedRecoveryPhase": "failed_step_execution",
            }
        },
    }
    record.memo = {
        **record.memo,
        "resume_failed_step_id": "inspect",
        "resume_completed_step_refs": ["artifact://completed/prepare"],
        "recovery_workspace_checkpoint_ref": "artifact://workspace/checkpoint",
        "resume_plan_digest": "sha256:plan",
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard, "temporal_workflow_editing_enabled", True
    )

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["resume"]["disabledReason"] == "recovery_checkpoint_missing"
    assert (
        body["targetDiagnostics"]["recovery"]["failedRecoveryPhase"]
        == "failed_step_execution"
    )


def test_describe_execution_omits_recovery_for_routine_recovery_action_gating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)
    record.parameters = {
        "workflow": {
            "instructions": "Review the screenshot.",
            "attachmentRefs": [
                {
                    "artifactRef": "artifact://input/objective-image",
                    "filename": "objective.png",
                    "contentType": "image/png",
                }
            ],
        }
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["resume"]["disabledReason"] == "state_not_eligible"
    assert body["targetDiagnostics"]["targets"][0]["targetKind"] == "objective"
    assert body["targetDiagnostics"]["recovery"] is None


@pytest.mark.parametrize(
    ("memo_updates", "expected_reason"),
    [
        (
            {
                "resume_failed_step_id": "implement",
                "resume_completed_step_refs": ["artifact://completed/plan"],
                "recovery_workspace_checkpoint_ref": "artifact://workspace/before-implement",
                "resume_plan_digest": "sha256:resume-plan",
            },
            "recovery_checkpoint_missing",
        ),
        (
            {
                "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
                "resume_completed_step_refs": ["artifact://completed/plan"],
                "recovery_workspace_checkpoint_ref": "artifact://workspace/before-implement",
                "resume_plan_digest": "sha256:resume-plan",
            },
            "failed_step_identity_missing",
        ),
        (
            {
                "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
                "resume_failed_step_id": "implement",
                "recovery_workspace_checkpoint_ref": "artifact://workspace/before-implement",
                "resume_plan_digest": "sha256:resume-plan",
            },
            "completed_step_refs_missing",
        ),
        (
            {
                "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
                "resume_failed_step_id": "implement",
                "resume_completed_step_refs": ["artifact://completed/plan"],
                "resume_plan_digest": "sha256:resume-plan",
            },
            "workspace_checkpoint_missing",
        ),
        (
            {
                "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
                "resume_failed_step_id": "implement",
                "resume_completed_step_refs": ["artifact://completed/plan"],
                "recovery_workspace_checkpoint_ref": "artifact://workspace/before-implement",
            },
            "plan_identity_missing",
        ),
    ],
)
def test_describe_execution_requires_complete_recovery_evidence(
    monkeypatch: pytest.MonkeyPatch,
    memo_updates: dict[str, object],
    expected_reason: str,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.memo = {**record.memo, **memo_updates}
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["canResumeFromFailedStep"] is False
    assert body["resume"]["available"] is False
    assert body["resume"]["disabledReason"] == expected_reason


def test_describe_execution_rejects_stale_recovery_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.memo = {
        **record.memo,
        "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
        "resume_failed_step_id": "implement",
        "resume_completed_step_refs": ["artifact://completed/plan"],
        "recovery_workspace_checkpoint_ref": "artifact://workspace/before-implement",
        "resume_plan_digest": "sha256:resume-plan",
        "resume_evidence_stale": True,
    }
    mock_service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["canResumeFromFailedStep"] is False
    assert body["actions"]["disabledReasons"]["canResumeFromFailedStep"] == "stale_recovery_evidence"
    assert "canRecoverFromFailedStep" not in body["actions"]
    assert "canRecoverFromFailedStep" not in body["actions"]["disabledReasons"]
    assert body["resume"]["available"] is False
    assert body["resume"]["disabledReason"] == "stale_recovery_evidence"


def test_failed_step_recovery_submission_rejects_stale_recovery_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    canonical = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    canonical.memo = {
        **canonical.memo,
        "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
        "resume_evidence_stale": True,
    }
    mock_service.describe_execution.return_value = canonical

    class Session:
        async def get(self, model, key):
            return canonical

        async def commit(self):
            return None

    artifact_service = SimpleNamespace(read=AsyncMock())
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: Session()
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda _session: artifact_service,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/recover-from-failed-step",
            json={"idempotencyKey": "resume-1"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "stale_recovery_evidence"
    artifact_service.read.assert_not_awaited()
    mock_service.create_failed_step_recovery_execution.assert_not_awaited()


@pytest.mark.parametrize(
    ("payload_fields", "expected_fields"),
    [
        (
            {
                "workflow": {"instructions": "change the task"},
                "runtime": {"model": "gpt-5.4"},
            },
            ["runtime"],
        ),
        (
            {
                "instructions": "changed",
                "steps": [{"id": "new-step"}],
                "attachments": ["artifact://new"],
                "inputAttachments": [{"artifactRef": "artifact://new"}],
            },
            ["attachments", "inputAttachments", "instructions", "steps"],
        ),
        (
            {
                "publishMode": "draft-pr",
                "branch": "feature/new",
                "startingBranch": "main",
                "targetBranch": "main",
                "presets": ["runtime"],
                "dependencies": ["mm:upstream"],
            },
            [
                "branch",
                "dependencies",
                "presets",
                "publishMode",
                "startingBranch",
                "targetBranch",
            ],
        ),
        (
            {
                "model": "gpt-5.4",
                "requestedModel": "gpt-5.4",
                "effort": "high",
                "parametersPatch": {"workflow": {"instructions": "changed"}},
                "inputArtifactRef": "artifact://input/new",
                "planArtifactRef": "artifact://plan/new",
                "manifestArtifactRef": "artifact://manifest/new",
            },
            [
                "effort",
                "inputArtifactRef",
                "manifestArtifactRef",
                "model",
                "parametersPatch",
                "planArtifactRef",
                "requestedModel",
            ],
        ),
    ],
)
def test_failed_step_recovery_request_rejects_edited_task_payload_fields(
    payload_fields: dict[str, object],
    expected_fields: list[str],
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record(
        state=MoonMindWorkflowState.FAILED
    )
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = _empty_session_override
    _override_user_dependencies(app, is_superuser=True)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/recover-from-failed-step",
            json={
                "idempotencyKey": "resume-1",
                **payload_fields,
            },
        )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "recovery_payload_not_allowed"
    assert detail["fields"] == expected_fields


def test_checkpoint_failed_step_execution_rejects_boolean_ordinals() -> None:
    assert (
        _checkpoint_failed_step_execution(
            {"failedStep": {"logicalStepId": "implement", "executionOrdinal": True}}
        )
        is None
    )
    assert (
        _checkpoint_failed_step_execution(
            {"failedStep": {"logicalStepId": "implement", "attempt": False}}
        )
        is None
    )
    assert (
        _checkpoint_failed_step_execution(
            {"failedStep": {"logicalStepId": "implement", "executionOrdinal": "2"}}
        )
        == 2
    )


def test_mm773_serialize_execution_surfaces_comparison_source_related_run() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.COMPLETED)
    record.parameters = {
        "targetRuntime": "codex_cli",
        "model": "gpt-5.4",
        "workflow": {
            "runtime": {"mode": "codex_cli", "model": "gpt-5.4"},
            "comparison": {
                "kind": "model_runtime_comparison",
                "sourceWorkflowId": "mm:source-run",
                "sourceRunId": "run-source",
            },
        },
    }

    payload = _serialize_execution(record).model_dump(by_alias=True)

    assert payload["relatedRuns"] == [
        {
            "workflowId": "mm:source-run",
            "runId": "run-source",
            "relationship": "Comparison source",
            "status": None,
            "targetRuntime": None,
            "model": None,
            "requestedModel": None,
            "resolvedModel": None,
            "effort": None,
            "createdAt": None,
            "href": "/workflows/mm%3Asource-run?source=temporal",
        }
    ]


@pytest.mark.asyncio
async def test_mm773_hydrates_related_run_runtime_model_metadata() -> None:
    record = _build_execution_record(state=MoonMindWorkflowState.COMPLETED)
    record.parameters = {
        "workflow": {
            "comparison": {
                "kind": "model_runtime_comparison",
                "sourceWorkflowId": "mm:source-run",
                "sourceRunId": "run-source",
            },
        },
    }
    execution = _serialize_execution(record)
    source = _build_execution_record(state=MoonMindWorkflowState.COMPLETED)
    source.workflow_id = "mm:source-run"
    source.run_id = "run-source"
    source.close_status = TemporalExecutionCloseStatus.COMPLETED
    source.parameters = {
        "targetRuntime": "claude_code",
        "model": "gemini-2.5-pro",
        "requestedModel": "gemini-2.5-pro",
        "effort": "medium",
        "workflow": {"runtime": {"mode": "claude_code", "effort": "medium"}},
    }
    session = SimpleNamespace(get=AsyncMock(return_value=source))

    hydrated = await _hydrate_related_run_metadata(execution, session=session)
    related = hydrated.model_dump(by_alias=True)["relatedRuns"][0]

    assert related["workflowId"] == "mm:source-run"
    assert related["runId"] == "run-source"
    assert related["status"] == "completed"
    assert related["targetRuntime"] == "claude_code"
    assert related["model"] == "gemini-2.5-pro"
    assert related["requestedModel"] == "gemini-2.5-pro"
    assert related["effort"] == "medium"


@pytest.mark.asyncio
async def test_mm773_hydrates_related_run_metadata_for_same_owner() -> None:
    user = SimpleNamespace(id=uuid4(), is_superuser=False)
    record = _build_execution_record(owner_id=str(user.id))
    record.parameters = {
        "workflow": {
            "comparison": {
                "kind": "model_runtime_comparison",
                "sourceWorkflowId": "mm:source-run",
            },
        },
    }
    execution = _serialize_execution(record, user=user)
    source = _build_execution_record(
        state=MoonMindWorkflowState.COMPLETED,
        owner_id=str(user.id),
    )
    source.workflow_id = "mm:source-run"
    source.close_status = TemporalExecutionCloseStatus.COMPLETED
    source.parameters = {"targetRuntime": "codex_cli", "model": "gpt-5.4"}
    session = SimpleNamespace(get=AsyncMock(return_value=source))

    hydrated = await _hydrate_related_run_metadata(
        execution,
        session=session,
        user=user,
    )
    related = hydrated.model_dump(by_alias=True)["relatedRuns"][0]

    assert related["status"] == "completed"
    assert related["targetRuntime"] == "codex_cli"
    assert related["model"] == "gpt-5.4"


@pytest.mark.asyncio
async def test_mm773_skips_related_run_metadata_for_foreign_owner() -> None:
    user = SimpleNamespace(id=uuid4(), is_superuser=False)
    record = _build_execution_record(owner_id=str(user.id))
    record.parameters = {
        "workflow": {
            "comparison": {
                "kind": "model_runtime_comparison",
                "sourceWorkflowId": "mm:source-run",
            },
        },
    }
    execution = _serialize_execution(record, user=user)
    source = _build_execution_record(
        state=MoonMindWorkflowState.COMPLETED,
        owner_id=str(uuid4()),
    )
    source.workflow_id = "mm:source-run"
    source.close_status = TemporalExecutionCloseStatus.COMPLETED
    source.parameters = {"targetRuntime": "codex_cli", "model": "gpt-5.4"}
    session = SimpleNamespace(get=AsyncMock(return_value=source))

    hydrated = await _hydrate_related_run_metadata(
        execution,
        session=session,
        user=user,
    )
    related = hydrated.model_dump(by_alias=True)["relatedRuns"][0]

    assert related["workflowId"] == "mm:source-run"
    assert related["status"] is None
    assert related["targetRuntime"] is None
    assert related["model"] is None


def _valid_failed_run_recovery_manifest_payload(
    canonical,
    *,
    checkpoint_ref: str,
    failed_step_id: str = "implement",
) -> dict[str, Any]:
    return {
        "schemaVersion": "v1",
        "contentType": FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE,
        "workflowId": canonical.workflow_id,
        "runId": canonical.run_id,
        "failedLogicalStepId": failed_step_id,
        "failedExecutionOrdinal": 1,
        "checkpointRefs": [
            {
                "category": "checkpoint",
                "status": "available",
                "artifactRef": checkpoint_ref,
                "boundary": "before_recovery_restoration",
            }
        ],
        "validation": {
            "result": "valid",
            "checkpointRef": checkpoint_ref,
            "boundary": "before_recovery_restoration",
        },
        "sideEffectDispositions": [],
        "resumeAllowed": True,
        "recoveryEligibility": {
            "eligible": True,
            "defaultAction": "resume_from_checkpoint",
            "requiredBoundary": "before_recovery_restoration",
            "checkpointRef": checkpoint_ref,
            "sourceWorkflowId": canonical.workflow_id,
            "sourceRunId": canonical.run_id,
            "operatorGuidance": "resume",
            "evidence": [
                {
                    "category": "checkpoint",
                    "status": "available",
                    "artifactRef": checkpoint_ref,
                    "boundary": "before_recovery_restoration",
                }
            ],
        },
        "createdAt": "2026-06-13T12:00:00+00:00",
    }


def test_failed_step_recovery_hydrates_checkpoint_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.executions._checkpoint_resume_admission_for_request",
        lambda **_kwargs: SimpleNamespace(admitted=True),
    )
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    canonical = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    canonical.memo = {
        **canonical.memo,
        "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
        "failed_run_recovery_manifest_ref": "artifact://recovery/manifest",
        "task_input_snapshot_ref": "artifact://snapshot/source",
    }
    mock_service.describe_execution.return_value = canonical
    mock_service.create_failed_step_recovery_execution.return_value = {
        "accepted": True,
        "applied": "created_resumed_execution",
        "source": {"workflowId": canonical.workflow_id, "runId": canonical.run_id},
        "execution": {
            "workflowId": "mm:resumed",
            "runId": "run-resumed",
            "detailHref": "/workflows/mm:resumed",
        },
        "relationship": "Recovered from failed step",
        "recoveryCheckpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
    }

    checkpoint_payload = {
        "schemaVersion": "v1",
        "source": {"workflowId": canonical.workflow_id, "runId": canonical.run_id},
        "taskInputSnapshotRef": "artifact://snapshot/source",
        "planRef": "artifact://plan/source",
        "planDigest": "sha256:resume-plan",
        "failedStep": {
            "logicalStepId": "implement",
            "order": 2,
            "attempt": 1,
        },
        "preservedSteps": [
            {
                "logicalStepId": "plan",
                "order": 1,
                "status": "completed",
                "sourceExecutionOrdinal": 1,
                "artifacts": {"summary": "artifact://completed/plan"},
                "stateCheckpointRef": "artifact://workspace/before-implement",
            }
        ],
        "recoveryWorkspace": {
            "branch": "feature/resume",
            "commit": "abc123",
            "checkpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
            "archiveBytes": 100,
        },
    }
    manifest_payload = _valid_failed_run_recovery_manifest_payload(
        canonical,
        checkpoint_ref="artifact://resume-checkpoints/source/checkpoint-v1",
    )
    artifact_service = SimpleNamespace(
        read=AsyncMock(
            side_effect=[
                (SimpleNamespace(), json.dumps(manifest_payload).encode()),
                (SimpleNamespace(), json.dumps(checkpoint_payload).encode()),
            ]
        )
    )

    class Session:
        async def get(self, model, key):
            return canonical

        async def commit(self):
            return None

    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: Session()
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda _session: artifact_service,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/recover-from-failed-step",
            json={"idempotencyKey": "resume-1"},
        )

    assert response.status_code == 201
    assert artifact_service.read.await_count == 2
    call_kwargs = mock_service.create_failed_step_recovery_execution.await_args.kwargs
    assert call_kwargs["checkpoint_payload"] == checkpoint_payload
    assert call_kwargs["failed_run_recovery_manifest"] == manifest_payload
    assert (
        call_kwargs["failed_run_recovery_manifest_ref"]
        == "artifact://recovery/manifest"
    )
    assert call_kwargs["recovery_checkpoint_ref"] is None


def test_failed_step_recovery_hydrates_checkpoint_from_manifest_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.executions._checkpoint_resume_admission_for_request",
        lambda **_kwargs: SimpleNamespace(admitted=True),
    )
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    canonical = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    canonical.memo = {
        **canonical.memo,
        "task_input_snapshot_ref": "artifact://snapshot/source",
    }
    canonical.finish_summary_json = {
        "recoveryManifest": {
            "resumeAllowed": True,
            "failedLogicalStepId": "implement",
            "failedExecutionOrdinal": 1,
            "validationResult": "valid",
            "checkpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
            "manifestRef": "artifact://recovery/manifest",
        }
    }
    mock_service.describe_execution.return_value = canonical
    mock_service.create_failed_step_recovery_execution.return_value = {
        "accepted": True,
        "applied": "created_resumed_execution",
        "source": {"workflowId": canonical.workflow_id, "runId": canonical.run_id},
        "execution": {
            "workflowId": "mm:resumed",
            "runId": "run-resumed",
            "detailHref": "/workflows/mm:resumed",
        },
        "relationship": "Recovered from failed step",
        "recoveryCheckpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
    }
    checkpoint_payload = {
        "schemaVersion": "v1",
        "source": {"workflowId": canonical.workflow_id, "runId": canonical.run_id},
        "taskInputSnapshotRef": "artifact://snapshot/source",
        "planDigest": "sha256:resume-plan",
        "failedStep": {
            "logicalStepId": "implement",
            "order": 2,
            "attempt": 1,
        },
        "preservedSteps": [
            {
                "logicalStepId": "plan",
                "order": 1,
                "status": "completed",
                "sourceExecutionOrdinal": 1,
                "artifacts": {"summary": "artifact://completed/plan"},
                "stateCheckpointRef": "artifact://workspace/before-implement",
            }
        ],
        "recoveryWorkspace": {
            "checkpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
            "archiveBytes": 100,
        },
    }
    manifest_payload = _valid_failed_run_recovery_manifest_payload(
        canonical,
        checkpoint_ref="artifact://resume-checkpoints/source/checkpoint-v1",
    )
    artifact_service = SimpleNamespace(
        read=AsyncMock(
            side_effect=[
                (SimpleNamespace(), json.dumps(manifest_payload).encode()),
                (SimpleNamespace(), json.dumps(checkpoint_payload).encode()),
            ]
        )
    )

    class Session:
        async def get(self, model, key):
            return canonical

        async def commit(self):
            return None

    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: Session()
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda _session: artifact_service,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/recover-from-failed-step",
            json={"idempotencyKey": "resume-1"},
        )

    assert response.status_code == 201
    assert artifact_service.read.await_count == 2
    call_kwargs = mock_service.create_failed_step_recovery_execution.await_args.kwargs
    assert call_kwargs["checkpoint_payload"] == checkpoint_payload
    assert call_kwargs["failed_run_recovery_manifest"] == manifest_payload
    assert (
        call_kwargs["failed_run_recovery_manifest_ref"]
        == "artifact://recovery/manifest"
    )


def test_selected_step_recovery_pins_source_and_selected_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api_service.api.routers.executions._checkpoint_resume_admission_for_request",
        lambda **_kwargs: SimpleNamespace(admitted=True),
    )
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    canonical = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    canonical.memo = {
        **canonical.memo,
        "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
        "failed_run_recovery_manifest_ref": "artifact://recovery/manifest",
        "task_input_snapshot_ref": "artifact://snapshot/source",
    }
    mock_service.describe_execution.return_value = canonical
    mock_service.create_failed_step_recovery_execution.return_value = {
        "accepted": True,
        "applied": "created_resumed_execution",
        "source": {"workflowId": canonical.workflow_id, "runId": canonical.run_id},
        "execution": {
            "workflowId": "mm:selected",
            "runId": "run-selected",
            "detailHref": "/workflows/mm:selected",
        },
        "relationship": "Recovered from selected step",
        "recoveryCheckpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
    }
    checkpoint_payload = {
        "schemaVersion": "v1",
        "source": {"workflowId": canonical.workflow_id, "runId": canonical.run_id},
        "taskInputSnapshotRef": "artifact://snapshot/source",
        "planDigest": "sha256:resume-plan",
        "failedStep": {
            "logicalStepId": "implement",
            "order": 2,
            "attempt": 1,
        },
        "preservedSteps": [
            {
                "logicalStepId": "plan",
                "order": 1,
                "status": "completed",
                "sourceExecutionOrdinal": 1,
                "artifacts": {"outputSummary": "artifact://completed/plan"},
                "stateCheckpointRef": "artifact://workspace/before-plan",
            }
        ],
        "recoveryWorkspace": {
            "checkpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
            "archiveBytes": 100,
        },
    }
    manifest_payload = _valid_failed_run_recovery_manifest_payload(
        canonical,
        checkpoint_ref="artifact://resume-checkpoints/source/checkpoint-v1",
    )
    artifact_service = SimpleNamespace(
        read=AsyncMock(
            side_effect=[
                (SimpleNamespace(), json.dumps(manifest_payload).encode()),
                (SimpleNamespace(), json.dumps(checkpoint_payload).encode()),
            ]
        )
    )

    class Session:
        async def get(self, model, key):
            return canonical

        async def commit(self):
            return None

    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: Session()
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda _session: artifact_service,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/recover-from-selected-step",
            json={
                "idempotencyKey": "recover-selected-1",
                "sourceWorkflowId": canonical.workflow_id,
                "sourceRunId": canonical.run_id,
                "selectedStartStepId": "plan",
            },
        )

    assert response.status_code == 201
    call_kwargs = mock_service.create_failed_step_recovery_execution.await_args.kwargs
    assert call_kwargs["checkpoint_payload"] == checkpoint_payload
    assert (
        call_kwargs["recovery_checkpoint_ref"]
        == "artifact://resume-checkpoints/source/checkpoint-v1"
    )
    assert call_kwargs["selected_start_step_id"] == "plan"


def test_selected_step_recovery_requires_checkpoint_ref_before_hydration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    canonical = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    canonical.memo = {
        key: value
        for key, value in dict(canonical.memo).items()
        if key not in {"recovery_checkpoint_ref", "recoveryCheckpointRef"}
    }
    mock_service.describe_execution.return_value = canonical

    artifact_service = SimpleNamespace(read=AsyncMock())

    def session_override() -> SimpleNamespace:
        return SimpleNamespace()

    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = session_override
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda _session: artifact_service,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/recover-from-selected-step",
            json={
                "idempotencyKey": "recover-selected-1",
                "sourceWorkflowId": canonical.workflow_id,
                "sourceRunId": canonical.run_id,
                "selectedStartStepId": "plan",
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "checkpoint_missing"
    artifact_service.read.assert_not_awaited()
    mock_service.create_failed_step_recovery_execution.assert_not_awaited()


def test_selected_step_recovery_rejects_source_run_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    canonical = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    canonical.memo = {
        **canonical.memo,
        "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
    }
    mock_service.describe_execution.return_value = canonical

    class Session:
        async def get(self, model, key):
            return canonical

    artifact_service = SimpleNamespace(read=AsyncMock())
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: Session()
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda _session: artifact_service,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/recover-from-selected-step",
            json={
                "idempotencyKey": "recover-selected-1",
                "sourceWorkflowId": canonical.workflow_id,
                "sourceRunId": "stale-run",
                "selectedStartStepId": "plan",
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "checkpoint_inconsistent"
    artifact_service.read.assert_not_awaited()
    mock_service.create_failed_step_recovery_execution.assert_not_awaited()


def test_failed_step_recovery_reports_checkpoint_authorization_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    canonical = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    canonical.memo = {
        **canonical.memo,
        "recovery_checkpoint_ref": "artifact://resume-checkpoints/source/checkpoint-v1",
        "failed_run_recovery_manifest_ref": "artifact://recovery/manifest",
        "task_input_snapshot_ref": "artifact://snapshot/source",
    }
    mock_service.describe_execution.return_value = canonical
    manifest_payload = _valid_failed_run_recovery_manifest_payload(
        canonical,
        checkpoint_ref="artifact://resume-checkpoints/source/checkpoint-v1",
    )
    artifact_service = SimpleNamespace(
        read=AsyncMock(
            side_effect=[
                (SimpleNamespace(), json.dumps(manifest_payload).encode()),
                TemporalArtifactAuthorizationError("denied"),
            ]
        )
    )

    class Session:
        async def get(self, model, key):
            return canonical

        async def commit(self):
            return None

    app.dependency_overrides[_get_service] = lambda: mock_service
    app.dependency_overrides[get_async_session] = lambda: Session()
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(
        "api_service.api.routers.executions.get_temporal_artifact_service",
        lambda _session: artifact_service,
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/executions/mm:wf-1/recover-from-failed-step",
            json={"idempotencyKey": "resume-1"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "checkpoint_unauthorized"
    mock_service.create_failed_step_recovery_execution.assert_not_awaited()


def test_recovery_not_available_reason_prioritizes_mismatch_over_missing_plan() -> None:
    reason = _recovery_not_available_reason(
        ValueError("Recovery checkpoint plan identity does not match source execution.")
    )

    assert reason == "checkpoint_inconsistent"

def test_temporal_workflow_editing_actions_require_run_workflow_and_feature_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", False)
    disabled_record = _build_execution_record(state=MoonMindWorkflowState.EXECUTING)

    disabled_actions = _serialize_execution(disabled_record).actions
    assert disabled_actions.can_update_inputs is False
    assert disabled_actions.disabled_reasons["canUpdateInputs"] == "temporal_workflow_editing_disabled"

    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)
    manifest_record = _build_execution_record(
        workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
        state=MoonMindWorkflowState.COMPLETED,
    )

    manifest_actions = _serialize_execution(manifest_record).actions
    assert manifest_actions.can_edit_for_rerun is False
    assert (
        manifest_actions.disabled_reasons["canEditForRerun"]
        == "unsupported_workflow_type"
    )
    assert manifest_actions.can_rerun is False
    assert manifest_actions.disabled_reasons["canRerun"] == "unsupported_workflow_type"

    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", False)
    disabled_manifest_actions = _serialize_execution(manifest_record).actions
    assert disabled_manifest_actions.can_rerun is False
    assert (
        disabled_manifest_actions.disabled_reasons["canRerun"]
        == "unsupported_workflow_type"
    )

def test_temporal_workflow_editing_actions_require_original_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)
    record = _build_execution_record(
        state=MoonMindWorkflowState.COMPLETED,
        has_workflow_input_snapshot=False,
    )

    actions = _serialize_execution(record).actions

    assert actions.can_edit_for_rerun is False
    assert (
        actions.disabled_reasons["canEditForRerun"]
        == "original_task_input_snapshot_missing"
    )
    assert actions.can_rerun is False
    assert (
        actions.disabled_reasons["canRerun"]
        == "original_task_input_snapshot_missing"
    )


def test_mm644_failed_task_edit_for_rerun_requires_authoritative_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)
    eligible = _build_execution_record(state=MoonMindWorkflowState.FAILED)

    eligible_body = _serialize_execution(eligible).model_dump(by_alias=True)

    assert eligible_body["taskInputSnapshot"]["available"] is True
    assert eligible_body["taskInputSnapshot"]["artifactRef"] == "art_snapshot_1"
    assert eligible_body["taskInputSnapshot"]["reconstructionMode"] == "authoritative"
    assert eligible_body["actions"]["canEditForRerun"] is True

    missing_snapshot = _build_execution_record(
        state=MoonMindWorkflowState.FAILED,
        has_workflow_input_snapshot=False,
    )
    missing_body = _serialize_execution(missing_snapshot).model_dump(by_alias=True)

    assert missing_body["taskInputSnapshot"]["available"] is False
    assert missing_body["actions"]["canEditForRerun"] is False
    assert (
        missing_body["actions"]["disabledReasons"]["canEditForRerun"]
        == "original_task_input_snapshot_missing"
    )


def test_workflow_gate_actions_expose_distinct_capabilities_and_consumed_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard, "temporal_workflow_editing_enabled", True
    )
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.finish_summary_json = {
        "controlStop": {
            "kind": "workflow_gate",
            "remainingWorkRef": "artifact://remaining/final",
            "workspaceHeadRef": "artifact://workspace/final",
            "metrics": {"remediationAdmitted": True},
            "auxiliaryOutcomes": {
                "gitPublication": {
                    "status": "failed",
                    "recoveryContract": {
                        "sourceWorkflowId": record.workflow_id,
                        "sourceRunId": record.run_id,
                        "sourceSemanticOutcome": "failed",
                        "target": {
                            "kind": "publication",
                            "publicationKind": "pull_request",
                            "sourcePublicationOperationId": "publish-1",
                            "semanticContext": "incomplete_draft_handoff",
                        },
                        "continuation": {
                            "phase": "resume_publication",
                            "publicationIdempotencyKey": (
                                publication_operation_key(
                                    source_workflow_id=record.workflow_id,
                                    source_run_id=record.run_id,
                                    publication_kind="pull_request",
                                    repository="MoonLadderStudios/MoonMind",
                                    head_ref="issue-3481",
                                    base_ref="main",
                                )
                            ),
                            "candidateRef": "artifact://workspace/final",
                            "beforePublicationCheckpointRef": (
                                "artifact://checkpoint/before-publication"
                            ),
                            "expectedHeadSha": "a" * 40,
                            "expectedTreeDigest": "sha256:" + "b" * 64,
                            "expectedDiffDigest": "sha256:" + "c" * 64,
                            "priorObservationsRef": "artifact://github/observations",
                            "secretScanRef": "artifact://scan/clean",
                            "diagnosticsRef": "artifact://diagnostics/publication",
                            "remainingWorkRef": "artifact://remaining/final",
                        },
                        "intent": {
                            "repository": "MoonLadderStudios/MoonMind",
                            "baseRef": "main",
                            "headRef": "issue-3481",
                            "mode": "draft_pr",
                            "branchPolicy": "reuse_exact_head",
                            "githubAuthorityRef": "managed-secret://github/source",
                        },
                        "candidateAccepted": False,
                        "hasPublishableChange": True,
                        "publicationAuthorityCurrent": True,
                        "incompleteDraftAuthorized": True,
                    },
                }
            },
        }
    }

    actions = _serialize_execution(record).actions.model_dump(by_alias=True)

    assert actions["canFullRetry"] is True
    assert actions["canContinueRemediation"] is True
    assert actions["canRetryPublication"] is True
    assert actions["canResumeFromFailedStep"] is False
    assert actions["actionEvidence"]["continueRemediation"] == {
        "candidateRef": "artifact://workspace/final",
        "remainingWorkRef": "artifact://remaining/final",
    }


def _publication_recovery_record() -> SimpleNamespace:
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    operation_key = publication_operation_key(
        source_workflow_id=record.workflow_id,
        source_run_id=record.run_id,
        publication_kind="pull_request",
        repository="MoonLadderStudios/MoonMind",
        head_ref="issue-3481",
        base_ref="main",
    )
    record.finish_summary_json = {
        "controlStop": {
            "auxiliaryOutcomes": {
                "gitPublication": {
                    "status": "failed",
                    "recoveryContract": {
                        "sourceWorkflowId": record.workflow_id,
                        "sourceRunId": record.run_id,
                        "sourceSemanticOutcome": "accepted",
                        "target": {
                            "kind": "publication",
                            "publicationKind": "pull_request",
                            "sourcePublicationOperationId": "publish-1",
                            "semanticContext": "accepted",
                        },
                        "continuation": {
                            "phase": "resume_publication",
                            "publicationIdempotencyKey": operation_key,
                            "candidateRef": "artifact://candidate/accepted",
                            "verifiedRemoteCandidateRef": "artifact://remote/head",
                            "expectedHeadSha": "a" * 40,
                            "expectedTreeDigest": "sha256:" + "b" * 64,
                            "expectedDiffDigest": "sha256:" + "c" * 64,
                            "priorObservationsRef": "artifact://github/observations",
                            "secretScanRef": "artifact://scan/clean",
                            "diagnosticsRef": "artifact://diagnostics/publication",
                        },
                        "intent": {
                            "repository": "MoonLadderStudios/MoonMind",
                            "baseRef": "main",
                            "headRef": "issue-3481",
                            "mode": "pr",
                            "branchPolicy": "reuse_exact_head",
                            "githubAuthorityRef": "managed-secret://github/source",
                        },
                        "candidateAccepted": True,
                        "hasPublishableChange": True,
                        "publicationAuthorityCurrent": True,
                    },
                }
            }
        }
    }
    return record


def test_retry_publication_starts_stable_linked_workflow_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    record = _publication_recovery_record()
    service.describe_execution.return_value = record
    adapter = AsyncMock()
    contract_payload = record.finish_summary_json["controlStop"]["auxiliaryOutcomes"][
        "gitPublication"
    ]["recoveryContract"]
    from moonmind.workflows.temporal.publication_recovery import (
        PublicationRecoveryContract,
    )

    destination_id = publication_recovery_workflow_id(
        PublicationRecoveryContract.model_validate(contract_payload)
    )
    adapter.start_workflow.return_value = WorkflowStartResult(
        workflow_id=destination_id,
        run_id="publication-run-1",
    )
    app.dependency_overrides[_get_service] = lambda: service
    app.dependency_overrides[get_temporal_client_adapter] = lambda: adapter
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.feature_flags, "publication_recovery_enabled", True)
    monkeypatch.setattr(
        settings.feature_flags, "publication_recovery_generation", "canary-1"
    )

    with TestClient(app) as test_client:
        first = test_client.post("/api/executions/mm:wf-1/retry-publication")
        duplicate = test_client.post("/api/executions/mm:wf-1/retry-publication")

    assert first.status_code == 201
    assert duplicate.status_code == 201
    assert first.json() == duplicate.json()
    assert first.json()["workflowId"] == destination_id
    assert first.json()["rolloutGeneration"] == "canary-1"
    assert adapter.start_workflow.await_count == 2
    for call_args in adapter.start_workflow.await_args_list:
        assert call_args.kwargs["workflow_id"] == destination_id
        assert call_args.kwargs["workflow_type"] == "MoonMind.PublicationRecoveryV1"
        assert call_args.kwargs["input_args"] == (
            PublicationRecoveryContract.model_validate(contract_payload).model_dump(
                by_alias=True, mode="json"
            )
        )
        assert call_args.kwargs["memo"]["source_workflow_id"] == "mm:wf-1"
        assert call_args.kwargs["memo"]["publication_semantic_context"] == "accepted"
        assert call_args.kwargs["memo"][
            "publication_no_implementation_rerun"
        ] is True


def test_retry_publication_stops_before_temporal_when_rollout_disables_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    service.describe_execution.return_value = _publication_recovery_record()
    adapter = AsyncMock()
    app.dependency_overrides[_get_service] = lambda: service
    app.dependency_overrides[get_temporal_client_adapter] = lambda: adapter
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.feature_flags, "publication_recovery_enabled", False)

    with TestClient(app) as test_client:
        response = test_client.post("/api/executions/mm:wf-1/retry-publication")

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "publication_recovery_disabled"
    adapter.start_workflow.assert_not_awaited()


@pytest.mark.parametrize("malformed_field", ["metrics", "auxiliaryOutcomes"])
def test_workflow_gate_actions_ignore_malformed_optional_control_stop_fields(
    monkeypatch: pytest.MonkeyPatch,
    malformed_field: str,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.finish_summary_json = {
        "controlStop": {
            "kind": "workflow_gate",
            malformed_field: "not-an-object",
        }
    }

    actions = _serialize_execution(record).actions.model_dump(by_alias=True)

    assert actions["canFullRetry"] is True
    assert actions["canContinueRemediation"] is False
    assert actions["canRetryPublication"] is False


def test_workflow_gate_actions_expose_action_specific_disabled_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard, "temporal_workflow_editing_enabled", True
    )
    record = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    record.memo["finishSummary"] = {
        "controlStop": {
            "kind": "workflow_gate",
            "remainingWorkRef": "artifact://remaining/final",
            "workspaceHeadRef": "artifact://workspace/final",
            "metrics": {"remediationAdmitted": False},
            "auxiliaryOutcomes": {"gitPublication": {"status": "not_attempted"}},
        }
    }

    actions = _serialize_execution(record).actions.model_dump(by_alias=True)

    assert actions["canContinueRemediation"] is False
    assert (
        actions["disabledReasons"]["canContinueRemediation"]
        == "remediation_not_admitted"
    )
    assert actions["canRetryPublication"] is False
    assert (
        actions["disabledReasons"]["canRetryPublication"]
        == "publication_not_failed"
    )
    assert actions["actionEvidence"]["continueRemediation"] == {
        "candidateRef": "artifact://workspace/final",
        "remainingWorkRef": "artifact://remaining/final",
    }


def test_mm644_rerun_snapshot_payload_records_source_lineage() -> None:
    payload = _build_original_workflow_input_snapshot_payload(
        source_kind="rerun",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "workflow": {
                "instructions": "MM-644 edited retry instructions.",
                "recovery": {
                    "kind": "edited_full_retry",
                    "sourceWorkflowId": "mm:failed-source",
                    "sourceRunId": "run-source",
                },
            },
        },
        task_payload={
            "instructions": "MM-644 edited retry instructions.",
            "recovery": {
                "kind": "edited_full_retry",
                "sourceWorkflowId": "mm:failed-source",
                "sourceRunId": "run-source",
            },
        },
        source_workflow_id="mm:failed-source",
        source_run_id="run-source",
    )

    assert payload["source"] == {
        "kind": "rerun",
        "sourceWorkflowId": "mm:failed-source",
        "sourceRunId": "run-source",
    }
    assert payload["draft"]["workflow"]["recovery"]["kind"] == "edited_full_retry"


@pytest.mark.asyncio
async def test_exact_rerun_reuses_source_task_input_snapshot_lineage() -> None:
    source = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    source.memo = {
        **source.memo,
        "task_input_snapshot_ref": "artifact://snapshot/source",
        "task_input_snapshot_version": 1,
        "task_input_snapshot_source_kind": "create",
    }
    target = TemporalExecutionRecord(
        workflow_id="mm:rerun",
        run_id="run-rerun",
        namespace="moonmind",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        memo={},
        artifact_refs=[],
    )
    canonical = TemporalExecutionCanonicalRecord(
        workflow_id="mm:rerun",
        run_id="run-rerun",
        namespace="moonmind",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        memo={},
        artifact_refs=[],
    )
    session = _SnapshotReuseSession(canonical=canonical)

    snapshot_ref = await _reuse_original_task_input_snapshot_from_source(
        session=session,
        source_record=source,
        target_record=target,
    )

    assert snapshot_ref == "artifact://snapshot/source"
    for record in (target, canonical):
        assert record.memo["task_input_snapshot_ref"] == "artifact://snapshot/source"
        assert record.memo["task_input_snapshot_version"] == 1
        assert record.memo["task_input_snapshot_source_kind"] == "rerun"
        assert record.artifact_refs == ["artifact://snapshot/source"]
    session.get.assert_awaited_once_with(
        TemporalExecutionCanonicalRecord,
        "mm:rerun",
    )
    assert len(session.added) == 1
    link = session.added[0]
    assert isinstance(link, TemporalArtifactLink)
    assert link.artifact_id == "artifact://snapshot/source"
    assert link.namespace == "moonmind"
    assert link.workflow_id == "mm:rerun"
    assert link.run_id == "run-rerun"
    assert link.link_type == "input.original_snapshot"


@pytest.mark.asyncio
async def test_exact_rerun_reuses_snapshot_defaults_invalid_version() -> None:
    source = _build_execution_record(state=MoonMindWorkflowState.FAILED)
    source.memo = {
        **source.memo,
        "task_input_snapshot_ref": "artifact://snapshot/source",
        "task_input_snapshot_version": "2026-05-13T00:00:00Z",
        "task_input_snapshot_source_kind": "create",
    }
    target = TemporalExecutionCanonicalRecord(
        workflow_id="mm:rerun",
        run_id="run-rerun",
        namespace="moonmind",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        memo={},
        artifact_refs=[],
    )
    session = _SnapshotReuseSession()

    snapshot_ref = await _reuse_original_task_input_snapshot_from_source(
        session=session,
        source_record=source,
        target_record=target,
    )

    assert snapshot_ref == "artifact://snapshot/source"
    assert target.memo["task_input_snapshot_version"] == 1
    assert len(session.added) == 1


def test_terminal_task_editing_actions_reject_parameter_fallback_without_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)
    record = _build_execution_record(
        state=MoonMindWorkflowState.FAILED,
        has_workflow_input_snapshot=False,
    )
    record.parameters = {
        "requestType": "task",
        "repository": "Moon/Mind",
        "targetRuntime": "codex_cli",
        "workflow": {
            "instructions": "Run Jira Orchestrate for MM-501.",
            "steps": [
                {
                    "id": "step-1",
                    "title": "First step",
                    "instructions": "Do the first step.",
                }
            ],
        },
    }

    actions = _serialize_execution(record).actions

    assert actions.can_update_inputs is False
    assert (
        actions.disabled_reasons["canUpdateInputs"]
        == "original_task_input_snapshot_missing"
    )
    assert actions.can_edit_for_rerun is False
    assert actions.can_rerun is False
    assert (
        actions.disabled_reasons["canEditForRerun"]
        == "original_task_input_snapshot_missing"
    )
    assert (
        actions.disabled_reasons["canRerun"]
        == "original_task_input_snapshot_missing"
    )


def test_terminal_task_editing_actions_reject_title_only_parameter_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)
    record = _build_execution_record(
        state=MoonMindWorkflowState.FAILED,
        has_workflow_input_snapshot=False,
    )
    record.parameters = {
        "requestType": "task",
        "repository": "Moon/Mind",
        "targetRuntime": "codex_cli",
        "workflow": {
            "steps": [
                {
                    "id": "step-1",
                    "title": "Title without reconstructable instructions",
                }
            ],
        },
    }

    actions = _serialize_execution(record).actions

    assert actions.can_edit_for_rerun is False
    assert actions.can_rerun is False
    assert (
        actions.disabled_reasons["canEditForRerun"]
        == "original_task_input_snapshot_missing"
    )
    assert (
        actions.disabled_reasons["canRerun"]
        == "original_task_input_snapshot_missing"
    )


def test_describe_execution_disables_actions_when_feature_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", False)
    monkeypatch.setattr(settings.temporal_dashboard, "debug_fields_enabled", False)

    with TestClient(app) as test_client:
        response = test_client.get("/api/executions/mm:wf-1")

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["canPause"] is False
    assert body["actions"]["disabledReasons"]["pause"] == "actions_disabled"
    assert body["debugFields"] is None

def test_action_endpoints_reject_requests_when_actions_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    mock_service = AsyncMock()
    mock_service.describe_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: mock_service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", False)

    with TestClient(app) as test_client:
        # Test update endpoint
        update_response = test_client.post(
            "/api/executions/mm:wf-1/update", json={"updateName": "RequestRerun"}
        )
        assert update_response.status_code == 403
        assert update_response.json()["detail"]["code"] == "actions_disabled"

        # Test signal endpoint
        signal_response = test_client.post(
            "/api/executions/mm:wf-1/signal", json={"signalName": "pause"}
        )
        assert signal_response.status_code == 403
        assert signal_response.json()["detail"]["code"] == "actions_disabled"

        # Test cancel endpoint
        cancel_response = test_client.post("/api/executions/mm:wf-1/cancel", json={})
        assert cancel_response.status_code == 403
        assert cancel_response.json()["detail"]["code"] == "actions_disabled"

def test_action_endpoints_reject_non_owner_operator(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    service.describe_execution.return_value = _build_execution_record(
        owner_id="other-user"
    )
    service.describe_cancel_target_execution.return_value = _build_execution_record(
        owner_id="other-user"
    )

    update_response = test_client.post(
        "/api/executions/mm:wf-1/update", json={"updateName": "RequestRerun"}
    )
    signal_response = test_client.post(
        "/api/executions/mm:wf-1/signal", json={"signalName": "pause"}
    )
    cancel_response = test_client.post("/api/executions/mm:wf-1/cancel", json={})

    for response in (update_response, signal_response, cancel_response):
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "execution_not_found"

    service.update_execution.assert_not_awaited()
    service.signal_execution.assert_not_awaited()
    service.cancel_execution.assert_not_awaited()


def test_continue_remediation_returns_same_destination_for_duplicate_requests(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, user = client
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    service.describe_execution.return_value = _build_execution_record(
        state=MoonMindWorkflowState.FAILED,
        owner_id=str(user.id),
    )
    reservations = [
        ControlStopContinuationReservation(
            destination_workflow_id="control-stop-continuation-digest",
            source_workflow_id="mm:wf-1",
            source_run_id="run-2",
            control_stop_id="verify:control-stop:6",
            workspace_head_ref="artifact://checkpoint/head-6",
            remaining_work_ref="artifact://verify/remaining-6",
            created=created,
        )
        for created in (True, False)
    ]
    admit = AsyncMock(side_effect=reservations)
    monkeypatch.setattr(
        "api_service.api.routers.executions.admit_control_stop_continuation",
        admit,
    )
    authorized_budget = ContinuationBudgetGrant(
        grantId="grant-1",
        maxAttempts=2,
        maxConsecutiveNoProgressAttempts=1,
    )
    repository = SimpleNamespace(
        load_source_identity=AsyncMock(
            return_value=(
                "verify:control-stop:6",
                SimpleNamespace(contract_payload={"authoritative": True}),
            )
        )
    )
    monkeypatch.setattr(
        "api_service.api.routers.executions.SqlControlStopContinuationRepository",
        lambda _session: repository,
    )
    monkeypatch.setattr(
        "api_service.api.routers.executions.ControlStopContinuationContract.model_validate",
        lambda _payload: SimpleNamespace(continuation_budget=authorized_budget),
    )

    first = test_client.post(
        "/api/executions/mm:wf-1/actions/continue-remediation",
        json={},
    )
    duplicate = test_client.post(
        "/api/executions/mm:wf-1/actions/continue-remediation",
        json={},
    )

    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert first.json()["destinationWorkflowId"] == duplicate.json()[
        "destinationWorkflowId"
    ]
    assert first.json()["created"] is True
    assert duplicate.json()["created"] is False
    assert admit.await_count == 2
    assert repository.load_source_identity.await_count == 2
    for invocation in admit.await_args_list:
        assert invocation.kwargs["source_workflow_id"] == "mm:wf-1"
        assert invocation.kwargs["source_run_id"] == "run-2"
        assert invocation.kwargs["control_stop_id"] == "verify:control-stop:6"
        assert invocation.kwargs["continuation_budget"].grant_id == "grant-1"
        assert invocation.kwargs["instruction_changes_ref"] is None


def test_continue_remediation_rejects_non_owner_before_admission(
    client: tuple[TestClient, AsyncMock, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, service, _user = client
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    service.describe_execution.return_value = _build_execution_record(
        state=MoonMindWorkflowState.FAILED,
        owner_id="other-user",
    )
    admit = AsyncMock()
    monkeypatch.setattr(
        "api_service.api.routers.executions.admit_control_stop_continuation",
        admit,
    )

    response = test_client.post(
        "/api/executions/mm:wf-1/actions/continue-remediation",
        json={},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "execution_not_found"
    admit.assert_not_awaited()


def test_serialize_execution_canceled_state_uses_correct_spelling() -> None:
    """Regression: 'cancelled' (British) must not leak into the Literal('canceled') field."""
    from api_service.db.models import TemporalExecutionCloseStatus

    record = SimpleNamespace(
        close_status=TemporalExecutionCloseStatus.CANCELED,
        search_attributes={"mm_entry": "run"},
        memo={},
        owner_id="user-1",
        entry="run",
        workflow_type=SimpleNamespace(value="MoonMind.UserWorkflow"),
        state=MoonMindWorkflowState.CANCELED,
        workflow_id="mm:canceled-1",
        namespace="moonmind",
        run_id="run-1",
        artifact_refs=[],
        created_at="2026-03-24T00:00:00Z",
        started_at="2026-03-24T00:00:00Z",
        updated_at="2026-03-24T00:00:00Z",
        closed_at="2026-03-24T00:00:00Z",
        integration_state=None,
    )

    payload = _serialize_execution(record)

    assert payload.status == "canceled"
    assert payload.dashboard_status == "canceled"
    assert payload.temporal_status == "canceled"
