"""Durable owner for one server-authored Checkpoint Branch turn."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping
from urllib.parse import urlsplit

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError
from temporalio.workflow import ChildWorkflowCancellationType

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import select
    from api_service.db.base import async_session_maker
    from api_service.db.models import (
        OmnigentBridgeSessionEvent,
        TemporalArtifact,
        TemporalArtifactRetentionClass,
    )
    from api_service.services.checkpoint_branch_service import CheckpointBranchService
    from api_service.services.checkpoint_branch_turn_execution import (
        get_checkpoint_branch_artifact_service,
    )
    from moonmind.schemas.agent_runtime_models import (
        AgentExecutionRequest,
        AgentRunResult,
    )
    from moonmind.schemas.temporal_models import StepExecutionCheckpointModel
    from moonmind.security.outbound_scan import scan_outbound_text
    from moonmind.workflows.temporal.activity_catalog import (
        ARTIFACTS_TASK_QUEUE,
        SANDBOX_TASK_QUEUE,
    )

WORKFLOW_NAME = "MoonMind.CheckpointBranchTurn"
SCHEMA_VERSION = "checkpoint-branch-turn-execution/v1"

_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)

_LOCAL_PATH = re.compile(
    r"(?:^|[\s\"'=])(?:/[^/\s]|\\\\|\.\.?[/\\]|[A-Za-z]:[/\\])"
)
_FORBIDDEN_AUTHORITY_KEYS = frozenset(
    {
        "accesstoken",
        "refreshtoken",
        "authorization",
        "authorizationheader",
        "apikey",
        "password",
        "privatekey",
        "cookie",
        "oauthgrant",
        "providergrant",
        "credentialvalue",
        "rawcredential",
        "dockersocket",
        "hostpath",
    }
)


class CheckpointBranchRetainedEvidenceError(ValueError):
    """Terminal evidence cannot cross the durable branch boundary safely."""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _validate_secret_and_path_safety(value: Any, *, path: str) -> None:
    """Reject raw grants, credentials, and host-local paths without echoing them."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _normalized_key(key) in _FORBIDDEN_AUTHORITY_KEYS:
                raise CheckpointBranchRetainedEvidenceError(
                    f"unsafe retained authority field at {path}.{key}"
                )
            _validate_secret_and_path_safety(nested, path=f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, nested in enumerate(value):
            _validate_secret_and_path_safety(nested, path=f"{path}[{index}]")
        return
    if not isinstance(value, str):
        return
    candidate = value.strip()
    if _LOCAL_PATH.search(candidate) or candidate.lower().startswith("file://"):
        raise CheckpointBranchRetainedEvidenceError(
            f"local-only path is forbidden at {path}"
        )


def _require_durable_artifact_ref(value: Any, *, path: str) -> str:
    ref = str(value or "").strip()
    if re.fullmatch(r"art_[0-9A-HJKMNP-TV-Z]{26}", ref):
        return f"artifact://{ref}"
    parsed = urlsplit(ref)
    if parsed.scheme != "artifact" or not parsed.netloc:
        raise CheckpointBranchRetainedEvidenceError(
            f"{path} must be a durable artifact ref"
        )
    return ref


def _safe_ref_list(value: Any, *, path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        raise CheckpointBranchRetainedEvidenceError(f"{path} must be a ref list")
    result: list[str] = []
    for index, item in enumerate(value):
        ref = _require_durable_artifact_ref(item, path=f"{path}[{index}]")
        if ref not in result:
            result.append(ref)
    return result


def _safe_authority_evidence(authority_chain: Mapping[str, Any]) -> dict[str, Any]:
    """Project proof, never live host/provider authority, into branch evidence."""

    chain = _mapping(authority_chain)
    workspace = _mapping(chain.get("workspace"))
    runtime = _mapping(chain.get("runtime"))
    egress = _mapping(runtime.get("egress"))
    publication = _mapping(chain.get("publication"))
    terminal = _mapping(chain.get("terminal"))
    evidence_refs = _mapping(publication.get("evidenceRefs"))
    safe_publication_refs: dict[str, str] = {}
    for key, value in evidence_refs.items():
        if key == "pullRequestUrl":
            parsed = urlsplit(str(value or "").strip())
            if parsed.scheme != "https" or not parsed.netloc or parsed.username:
                raise CheckpointBranchRetainedEvidenceError(
                    "publication pullRequestUrl must be a credential-free HTTPS URL"
                )
            safe_publication_refs[str(key)] = str(value).strip()
        else:
            safe_publication_refs[str(key)] = _require_durable_artifact_ref(
                value, path=f"authority.publication.evidenceRefs.{key}"
            )
    reasons: list[dict[str, Any]] = []
    for item in chain.get("reasons") or []:
        if not isinstance(item, Mapping):
            continue
        reasons.append(
            {
                key: item.get(key)
                for key in (
                    "stage",
                    "code",
                    "failureClass",
                    "remediationAction",
                )
                if item.get(key) is not None
            }
        )
    projected = {
        "schemaVersion": str(chain.get("schemaVersion") or ""),
        "workspace": {
            key: workspace.get(key)
            for key in (
                "locatorKind",
                "identityVerified",
                "repository",
                "sourceBranch",
                "sourceCommit",
                "candidateHead",
                "materializationAction",
                "sourceKind",
            )
            if workspace.get(key) is not None
        },
        "runtime": {
            key: runtime.get(key)
            for key in (
                "hostMode",
                "executionProfileRef",
                "launchPolicyRef",
                "policyRef",
                "providerProfileId",
                "credentialGeneration",
                "mountClasses",
                "capabilityClasses",
                "controlCapabilities",
            )
            if runtime.get(key) is not None
        },
        "publication": {
            key: publication.get(key)
            for key in (
                "publishMode",
                "outputBranch",
                "repositoryMutationAuthorized",
                "githubMutationRequired",
                "publicationState",
            )
            if publication.get(key) is not None
        },
        "terminal": {
            key: terminal.get(key)
            for key in (
                "harvestState",
                "cleanupMode",
                "cleanupCompleted",
                "leaseReleased",
                "janitorRequired",
                "releaseOrdering",
            )
            if terminal.get(key) is not None
        },
        "reasons": reasons,
    }
    projected["workspace"]["restoreInputRefs"] = _safe_ref_list(
        workspace.get("restoreInputRefs") or [],
        path="authority.workspace.restoreInputRefs",
    )
    projected["runtime"]["egress"] = {
        key: egress.get(key)
        for key in (
            "profileDigest",
            "appliedRuleDigest",
            "validationState",
            "validatedAt",
        )
        if egress.get(key) is not None
    }
    projected["publication"]["declaredOutputRefs"] = _safe_ref_list(
        publication.get("declaredOutputRefs") or [],
        path="authority.publication.declaredOutputRefs",
    )
    projected["publication"]["evidenceRefs"] = safe_publication_refs
    _validate_secret_and_path_safety(projected, path="authorityEvidence")
    scan = scan_outbound_text(
        json.dumps(projected, sort_keys=True),
        location="checkpointBranch.retainedAuthority",
        high_security_mode=True,
    )
    if not scan.allowed:
        raise CheckpointBranchRetainedEvidenceError(
            "retained authority evidence contains credential-like content"
        )
    return projected


def _safe_capture_evidence(capture: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in (
        "externalStateRef",
        "terminalRef",
        "diagnosticsRef",
        "resourceManifestRef",
        "captureManifestRef",
        "headRef",
        "diffRef",
        "workspaceCheckpointRef",
    ):
        if capture.get(key):
            safe[key] = _require_durable_artifact_ref(
                capture[key], path=f"capture.{key}"
            )
    return safe


def _artifact_refs_in(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, Mapping):
        for nested in value.values():
            refs.extend(_artifact_refs_in(nested))
    elif isinstance(value, list | tuple):
        for nested in value:
            refs.extend(_artifact_refs_in(nested))
    elif isinstance(value, str) and (
        value.strip().startswith("artifact://")
        or re.fullmatch(r"art_[0-9A-HJKMNP-TV-Z]{26}", value.strip())
    ):
        refs.append(_require_durable_artifact_ref(value, path="checkpoint.ref"))
    return list(dict.fromkeys(refs))


def checkpoint_branch_turn_terminal_disposition(
    *,
    result: AgentRunResult,
    checkpoint_ref: str | None,
    authority_chain: Mapping[str, Any] | None,
) -> str:
    """Classify terminal delivery without confusing it with repair success."""

    chain = _mapping(authority_chain)
    terminal = _mapping(chain.get("terminal"))
    reason_codes = {
        str(item.get("code") or "").strip().lower()
        for item in chain.get("reasons", [])
        if isinstance(item, Mapping)
    }
    provider_code = str(result.provider_error_code or "").strip().lower()
    codes = {provider_code, *reason_codes}
    if result.failure_class == "canceled":
        return "canceled"
    if any(
        "delivery_unknown" in code or "ambiguous_terminal" in code
        for code in codes
    ):
        return "delivery_unknown"
    if any("resume_unavailable" in code for code in codes):
        return "resume_unavailable"
    if chain and (
        terminal.get("cleanupCompleted") is False
        or terminal.get("leaseReleased") is False
        or terminal.get("janitorRequired") is True
    ):
        return "cleanup_failure"
    if result.failure_class or result.provider_error_code:
        return "provider_failure"
    if not checkpoint_ref:
        return "terminal_checkpoint_missing"
    return "verification_pending"


async def _load_authority_chain(bridge_session_id: str | None) -> dict[str, Any]:
    if not bridge_session_id:
        return {}
    async with async_session_maker() as session:
        event = (
            await session.execute(
                select(OmnigentBridgeSessionEvent)
                .where(
                    OmnigentBridgeSessionEvent.bridge_session_id
                    == bridge_session_id,
                    OmnigentBridgeSessionEvent.event_type
                    == "lifecycle.authority_chain",
                )
                .order_by(OmnigentBridgeSessionEvent.sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if event is None:
        return {}
    metadata = _mapping(event.metadata_)
    inner = _mapping(metadata.get("metadata"))
    return _mapping(inner.get("authorityChain"))


async def _write_result_artifact(
    *,
    principal: str,
    payload: Mapping[str, Any],
    kind: str,
    branch_turn_id: str,
    source_namespace: str,
    source_workflow_id: str,
    source_run_id: str,
) -> str:
    data = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":")
    ).encode()
    digest = _sha256(data).removeprefix("sha256:")
    async with async_session_maker() as session:
        artifacts = get_checkpoint_branch_artifact_service(session)
        artifact = (
            await session.execute(
                select(TemporalArtifact)
                .where(
                    TemporalArtifact.created_by_principal == principal,
                    TemporalArtifact.metadata_json["kind"].as_string() == kind,
                    TemporalArtifact.metadata_json["branchTurnId"].as_string()
                    == branch_turn_id,
                )
                .order_by(TemporalArtifact.created_at, TemporalArtifact.artifact_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if artifact is not None:
            if (
                artifact.sha256 not in {None, digest}
                or artifact.size_bytes not in {None, len(data)}
                or artifact.content_type not in {None, "application/json"}
            ):
                raise CheckpointBranchRetainedEvidenceError(
                    f"owned terminal artifact {kind} changed across retry"
                )
            if getattr(artifact.status, "value", artifact.status) == "complete":
                _stored, existing_payload = await artifacts.read(
                    artifact_id=artifact.artifact_id,
                    principal=principal,
                    allow_restricted_raw=True,
                )
                if existing_payload != data:
                    raise CheckpointBranchRetainedEvidenceError(
                        f"owned terminal artifact {kind} changed across retry"
                    )
                return f"artifact://{artifact.artifact_id}"
        else:
            artifact, _upload = await artifacts.create(
                principal=principal,
                content_type="application/json",
                size_bytes=len(data),
                sha256=digest,
                retention_class=TemporalArtifactRetentionClass.LONG,
                link={
                    "namespace": source_namespace,
                    "workflow_id": source_workflow_id,
                    "run_id": source_run_id,
                    "link_type": "checkpoint_branch.turn",
                    "label": f"Checkpoint Branch turn {branch_turn_id}",
                },
                metadata_json={
                    "kind": kind,
                    "branchTurnId": branch_turn_id,
                    "issue": "MoonLadderStudios/MoonMind#3621",
                },
            )
        await artifacts.write_complete(
            artifact_id=artifact.artifact_id,
            principal=principal,
            payload=data,
            content_type="application/json",
        )
        return f"artifact://{artifact.artifact_id}"


async def _read_retained_artifact(*, ref: str, path: str) -> bytes:
    durable_ref = _require_durable_artifact_ref(ref, path=path)
    if durable_ref.startswith("artifact://omnigent/"):
        from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway

        try:
            return await LocalOmnigentArtifactGateway().read_bytes(durable_ref)
        except Exception as exc:
            raise CheckpointBranchRetainedEvidenceError(
                f"{path} is not resolvable"
            ) from exc
    artifact_id = durable_ref.removeprefix("artifact://")
    try:
        async with async_session_maker() as session:
            _artifact, data = await get_checkpoint_branch_artifact_service(
                session
            ).read(
                artifact_id=artifact_id,
                principal="service:checkpoint-branch-turn",
                allow_restricted_raw=True,
            )
            return data
    except Exception as exc:
        raise CheckpointBranchRetainedEvidenceError(
            f"{path} is not resolvable"
        ) from exc


@activity.defn(name="checkpoint_branch.turn.mark_running")
async def mark_checkpoint_branch_turn_running(payload: Mapping[str, Any]) -> None:
    """Persist dispatch state immediately before the AgentRun child starts."""

    async with async_session_maker() as session:
        await CheckpointBranchService(session).mark_turn_running(
            workflow_id=str(payload["workflowId"]),
            branch_id=str(payload["branchId"]),
            branch_turn_id=str(payload["branchTurnId"]),
            runtime_agent_run_id=str(payload["agentRunWorkflowId"]),
        )
        await session.commit()


@activity.defn(name="checkpoint_branch.turn.persist_terminal")
async def persist_checkpoint_branch_turn_terminal(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist terminal delivery evidence and hand off to verification."""

    workflow_id = str(payload["workflowId"])
    branch_id = str(payload["branchId"])
    branch_turn_id = str(payload["branchTurnId"])
    principal = str(payload["principal"])
    raw_result = _mapping(payload.get("agentResult"))
    result = AgentRunResult.model_validate(raw_result)
    async with async_session_maker() as session:
        service = CheckpointBranchService(session)
        _branch, locked_turn = await service.lock_turn_execution(
            workflow_id=workflow_id,
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
        )
        _validate_secret_and_path_safety(raw_result, path="agentResult")
        scan = scan_outbound_text(
            json.dumps(raw_result, sort_keys=True),
            location="checkpointBranch.agentResult",
            high_security_mode=True,
        )
        if not scan.allowed:
            raise CheckpointBranchRetainedEvidenceError(
                "agent result contains credential-like retained evidence"
            )

        checkpoint = _mapping(payload.get("checkpoint"))
        checkpoint_ref = (
            _require_durable_artifact_ref(
                checkpoint.get("checkpointRef"), path="checkpointRef"
            )
            if checkpoint.get("checkpointRef")
            else None
        )
        checkpoint_digest: str | None = None
        checkpoint_model: StepExecutionCheckpointModel | None = None
        if checkpoint_ref:
            checkpoint_bytes = await _read_retained_artifact(
                ref=checkpoint_ref,
                path="checkpointRef",
            )
            try:
                checkpoint_model = StepExecutionCheckpointModel.model_validate_json(
                    checkpoint_bytes
                )
            except Exception as exc:
                raise CheckpointBranchRetainedEvidenceError(
                    "checkpointRef does not resolve to a valid Step Execution "
                    "checkpoint"
                ) from exc
            if (
                checkpoint_model.omnigent is None
                or checkpoint_model.omnigent.step_execution_id
                != locked_turn.created_step_execution_id
            ):
                raise CheckpointBranchRetainedEvidenceError(
                    "terminal checkpoint Step Execution authority does not match "
                    "the turn"
                )
            checkpoint_digest = _sha256(checkpoint_bytes)

        capture = _mapping(result.metadata.get("omnigentCheckpointCapture"))
        safe_capture = _safe_capture_evidence(capture)
        terminal_ref = safe_capture.get("terminalRef")
        provider_session_id = (
            str(capture.get("omnigentSessionId") or "").strip() or None
        )
        if provider_session_id:
            if len(provider_session_id) > 255:
                raise CheckpointBranchRetainedEvidenceError(
                    "provider session identity exceeds the retained evidence bound"
                )
            _validate_secret_and_path_safety(
                provider_session_id, path="capture.omnigentSessionId"
            )
        authority_chain = _mapping(result.metadata.get("authorityChain"))
        if not authority_chain:
            authority_chain = await _load_authority_chain(
                str(capture.get("bridgeSessionId") or "").strip() or None
            )
        safe_authority = _safe_authority_evidence(authority_chain)

        safe_output_refs = _safe_ref_list(
            result.output_refs, path="agentResult.outputRefs"
        )
        runtime_diagnostics_ref = (
            _require_durable_artifact_ref(
                result.diagnostics_ref, path="agentResult.diagnosticsRef"
            )
            if result.diagnostics_ref
            else None
        )
        refs_to_resolve = [
            *safe_output_refs,
            *([runtime_diagnostics_ref] if runtime_diagnostics_ref else []),
            *_artifact_refs_in(safe_capture),
            *_artifact_refs_in(safe_authority),
        ]
        if checkpoint_model is not None:
            refs_to_resolve.extend(
                _artifact_refs_in(
                    checkpoint_model.model_dump(
                        by_alias=True, mode="json", exclude_none=True
                    )
                )
            )
        for index, ref in enumerate(dict.fromkeys(refs_to_resolve)):
            await _read_retained_artifact(
                ref=ref,
                path=f"retainedRefs[{index}]",
            )

        disposition = checkpoint_branch_turn_terminal_disposition(
            result=result,
            checkpoint_ref=checkpoint_ref,
            authority_chain=authority_chain,
        )
        outcome = str(payload.get("outcome") or "failed").strip().lower()
        if disposition in {
            "delivery_unknown",
            "resume_unavailable",
            "cleanup_failure",
        }:
            outcome = "blocked"
        elif result.failure_class or result.provider_error_code or not checkpoint_ref:
            outcome = "canceled" if result.failure_class == "canceled" else "failed"

        result_payload = {
            "outputRefs": safe_output_refs,
            "summary": result.summary,
            "metrics": result.metrics,
            "diagnosticsRef": runtime_diagnostics_ref,
            "failureClass": result.failure_class,
            "providerErrorCode": result.provider_error_code,
            "retryRecommendation": result.retry_recommendation,
            "metadata": {
                "omnigentCheckpointCapture": safe_capture,
                "authorityEvidence": safe_authority,
            },
        }
        result_payload = {
            key: value for key, value in result_payload.items() if value is not None
        }
        agent_result_ref = await _write_result_artifact(
            principal=principal,
            payload=result_payload,
            kind="runtime.branch_turn.agent_result.json",
            branch_turn_id=branch_turn_id,
            source_namespace=str(payload["sourceNamespace"]),
            source_workflow_id=workflow_id,
            source_run_id=str(payload["sourceRunId"]),
        )
        diagnostics_payload = {
            "schemaVersion": "checkpoint-branch-terminal-diagnostics/v1",
            "deliveryOutcome": outcome,
            "terminalDisposition": disposition,
            "checkpointCaptured": checkpoint_ref is not None,
            "authorityEvidence": safe_authority,
            "verificationPending": outcome == "succeeded",
            "failureClass": result.failure_class,
            "providerErrorCode": result.provider_error_code,
            "runtimeDiagnosticsRef": runtime_diagnostics_ref,
        }
        diagnostics_ref = await _write_result_artifact(
            principal=principal,
            payload=diagnostics_payload,
            kind="output.branch_turn.diagnostics.json",
            branch_turn_id=branch_turn_id,
            source_namespace=str(payload["sourceNamespace"]),
            source_workflow_id=workflow_id,
            source_run_id=str(payload["sourceRunId"]),
        )
        turn = await service.finalize_turn_execution(
            workflow_id=workflow_id,
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
            outcome=outcome,
            agent_result_ref=agent_result_ref,
            diagnostics_ref=diagnostics_ref,
            checkpoint_ref=checkpoint_ref,
            checkpoint_digest=checkpoint_digest,
            provider_session_id=provider_session_id,
            terminal_ref=terminal_ref,
            output_refs=safe_output_refs,
            terminal_disposition=disposition,
        )
        await session.commit()
        return {
            "branchId": branch_id,
            "branchTurnId": branch_turn_id,
            "status": turn.status,
            "deliveryOutcome": outcome,
            "terminalDisposition": disposition,
            "verificationPending": outcome == "succeeded",
            "agentResultRef": agent_result_ref,
            "diagnosticsRef": diagnostics_ref,
            "checkpointRef": checkpoint_ref,
            "terminalRef": terminal_ref,
        }


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindCheckpointBranchTurnWorkflow:
    """Execute, checkpoint, and retain one fresh Omnigent branch turn."""

    def __init__(self) -> None:
        self._phase = "created"
        self._result: dict[str, Any] | None = None

    @workflow.query(name="checkpoint_branch.turn.state")
    def state(self) -> dict[str, Any]:
        return {"phase": self._phase, "result": self._result}

    async def _persist_terminal(
        self,
        payload: Mapping[str, Any],
        *,
        result: AgentRunResult,
        outcome: str,
        checkpoint: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _mapping(
            await workflow.execute_activity(
                "checkpoint_branch.turn.persist_terminal",
                {
                    "workflowId": payload["workflowId"],
                    "branchId": payload["branchId"],
                    "branchTurnId": payload["branchTurnId"],
                    "principal": payload["principal"],
                    "sourceNamespace": payload["sourceNamespace"],
                    "sourceRunId": payload["sourceRunId"],
                    "outcome": outcome,
                    "agentResult": result.model_dump(
                        by_alias=True, mode="json", exclude_none=True
                    ),
                    "checkpoint": dict(checkpoint or {}),
                },
                start_to_close_timeout=timedelta(minutes=2),
                schedule_to_close_timeout=timedelta(minutes=5),
                retry_policy=_RETRY,
            )
        )

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError("unsupported Checkpoint Branch turn schema")
        agent_request = AgentExecutionRequest.model_validate(payload["agentRequest"])
        self._phase = "dispatching"
        await workflow.execute_activity(
            "checkpoint_branch.turn.mark_running",
            {
                "workflowId": payload["workflowId"],
                "branchId": payload["branchId"],
                "branchTurnId": payload["branchTurnId"],
                "agentRunWorkflowId": payload["agentRunWorkflowId"],
            },
            start_to_close_timeout=timedelta(minutes=1),
            schedule_to_close_timeout=timedelta(minutes=3),
            retry_policy=_RETRY,
        )
        terminal_handoff_started = False
        try:
            self._phase = "running"
            raw_result = await workflow.execute_child_workflow(
                "MoonMind.AgentRun",
                agent_request,
                id=str(payload["agentRunWorkflowId"]),
                task_queue=workflow.info().task_queue,
                cancellation_type=ChildWorkflowCancellationType.TRY_CANCEL,
            )
            result = (
                raw_result
                if isinstance(raw_result, AgentRunResult)
                else AgentRunResult.model_validate(raw_result)
            )
            if result.failure_class or result.provider_error_code:
                self._phase = "failed"
                terminal_handoff_started = True
                self._result = await self._persist_terminal(
                    payload, result=result, outcome="failed"
                )
                terminal_handoff_started = False
                return self._result

            self._phase = "capturing_workspace"
            step = agent_request.step_execution
            assert step is not None
            identity = {
                "workflowId": step.workflow_id,
                "runId": step.run_id,
                "logicalStepId": step.logical_step_id,
                "executionOrdinal": step.execution_ordinal,
            }
            capture = _mapping(
                await workflow.execute_activity(
                    "workspace.capture_checkpoint",
                    {
                        "identity": identity,
                        "boundary": "after_execution",
                        "kind": "worktree_archive",
                        "workspaceLocator": payload["workspaceLocator"],
                        "artifactNamespace": (
                            f"checkpoint-branches/{payload['branchId']}/"
                            f"{payload['branchTurnId']}"
                        ),
                        "idempotencyKey": (
                            f"{step.step_execution_id}:capture:after_execution"
                        ),
                        "baseCommit": payload.get("baseCommit"),
                        "includeUntracked": True,
                        "includeIgnoredFiles": False,
                    },
                    task_queue=SANDBOX_TASK_QUEUE,
                    start_to_close_timeout=timedelta(minutes=5),
                    schedule_to_close_timeout=timedelta(minutes=10),
                    retry_policy=_RETRY,
                )
            )
            if capture.get("status") != "captured" or not isinstance(
                capture.get("workspace"), Mapping
            ):
                raise ValueError("branch workspace checkpoint capture failed")
            self._phase = "persisting_checkpoint"
            omnigent_capture = _mapping(
                result.metadata.get("omnigentCheckpointCapture")
            )
            omnigent_capture["workspaceLocator"] = payload["workspaceLocator"]
            omnigent_capture["instructionRefs"] = [payload["instructionRef"]]
            checkpoint = _mapping(
                await workflow.execute_activity(
                    "step_checkpoint.create_v2",
                    {
                        "identity": identity,
                        "boundary": "after_execution",
                        "taskInputSnapshotRef": payload["instructionRef"],
                        "workspace": capture["workspace"],
                        "omnigentCheckpointCapture": omnigent_capture,
                        "createdAt": workflow.now().astimezone(UTC).isoformat(),
                        "planDigest": _sha256(
                            str(
                                agent_request.step_execution.context_bundle_digest
                            ).encode()
                        ),
                        "preparedInputRefs": agent_request.input_refs,
                        "stepOutputs": {
                            "outputRefs": result.output_refs,
                            "terminalRef": omnigent_capture.get("terminalRef"),
                        },
                        "diagnosticRefs": [
                            ref
                            for ref in [
                                result.diagnostics_ref,
                                *capture.get("diagnosticRefs", []),
                            ]
                            if ref
                        ],
                        "idempotencyKey": (
                            f"{step.step_execution_id}:checkpoint:after_execution"
                        ),
                    },
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    start_to_close_timeout=timedelta(minutes=2),
                    schedule_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_RETRY,
                )
            )
            self._phase = "verification_handoff"
            terminal_handoff_started = True
            self._result = await self._persist_terminal(
                payload,
                result=result,
                outcome="succeeded",
                checkpoint=checkpoint,
            )
            terminal_handoff_started = False
            self._phase = "completed"
            return self._result
        except CancelledError:
            self._phase = "canceled"
            self._result = await self._persist_terminal(
                payload,
                result=AgentRunResult(
                    summary="Checkpoint Branch turn was canceled.",
                    failureClass="canceled",
                ),
                outcome="canceled",
            )
            raise
        except Exception as exc:
            # A terminal Activity retry must always receive byte-identical
            # evidence. If that Activity exhausts retries after persisting only
            # part of its immutable artifact set, do not replace the original
            # payload with a different synthetic result under the same owned
            # artifact keys. Temporal will retain the failed Activity evidence
            # and an operator can retry the same handoff safely.
            if terminal_handoff_started:
                raise
            self._phase = "failed"
            self._result = await self._persist_terminal(
                payload,
                result=AgentRunResult(
                    summary=(
                        "Checkpoint Branch turn failed before terminal delivery."
                    ),
                    failureClass="system_error",
                    providerErrorCode=type(exc).__name__,
                ),
                outcome="failed",
            )
            return self._result


__all__ = [
    "MoonMindCheckpointBranchTurnWorkflow",
    "WORKFLOW_NAME",
    "mark_checkpoint_branch_turn_running",
    "persist_checkpoint_branch_turn_terminal",
]
