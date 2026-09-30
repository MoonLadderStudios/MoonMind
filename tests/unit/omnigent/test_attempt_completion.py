from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import OmnigentRuntimeBindingRecord
from moonmind.omnigent.attempt_completion import complete_skill_turns
from moonmind.omnigent.runtime_bindings import (
    DbRuntimeBindingStore,
    RuntimeBindingSessionAuthoritySink,
    RuntimeBindingState,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.terminal_evidence import evaluate_terminal_evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_phase", ["compute", "turn:0"])
async def test_cleaned_attempt_finishes_publication_without_recreating_provider(
    tmp_path,
    receipt_phase,
):
    from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore
    from tests.unit.workflows.test_terminal_evidence import (
        _auto_publish_contract,
        _write_auto_publish_result,
    )

    _write_auto_publish_result(tmp_path)

    store = InMemoryStableRuntimeBindingStore()
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": "attempt-finalization",
            "workspaceSpec": {"repository": "owner/repo"},
            "terminalContract": {
                key: value
                for key, value in _auto_publish_contract().items()
                if key != "skillId"
            },
        }
    )
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "a" * 64,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    await sink.record_phase(
        "workspace",
        {
            "workspaceSpec": {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": "recorded",
                    "relativePath": "repo",
                }
            }
        },
    )
    await sink.record_phase(
        receipt_phase,
        AgentRunResult(summary="verified compute").model_dump(
            by_alias=True, mode="json"
        ),
    )
    await sink.record_phase("saved", {"archiveRef": "artifact://verified-candidate"})
    binding = sink.binding
    for state in (RuntimeBindingState.cleanup_pending, RuntimeBindingState.cleaned):
        binding = await store.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=state,
        )
    # Construct only the finalization boundary: a provider/host implementation
    # is deliberately absent and cannot be used to finish this operation.
    realizer = object.__new__(GenericOmnigentHostRealizer)
    realizer._runtime_bindings = store
    published = []
    inspected = []

    async def inspect(bound):
        inspected.append(1)
        return evaluate_terminal_evidence(
            bound.terminal_contract.model_dump(by_alias=True),
            workspace_path=str(tmp_path),
        )

    realizer._inspect_terminal = inspect

    async def publish(bound, result):
        assert bound.workspace_spec["workspaceLocator"]["workspaceId"] == "recorded"
        published.append(1)
        return result.model_copy(update={"summary": "remote publication verified"})

    realizer._publish_repository = publish
    first = await realizer._reconcile_finalization(request, binding)
    second = await realizer._reconcile_finalization(
        request, await store.get(binding.bindingId)
    )
    assert first == second
    assert first.summary == "remote publication verified"
    assert published == [1]
    assert inspected == ([1] if receipt_phase == "turn:0" else [])


@pytest.mark.asyncio
async def test_restart_reuses_turn_receipts_and_cumulative_continuation(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'attempt.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(OmnigentRuntimeBindingRecord.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DbRuntimeBindingStore(sessions)
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "a" * 64,
        idempotency_key="attempt-1",
        provider_leases={},
    )
    for state in (
        RuntimeBindingState.credentials_materialized,
        RuntimeBindingState.host_allocating,
        RuntimeBindingState.host_ready,
        RuntimeBindingState.session_creating,
    ):
        binding = await store.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=state,
        )
    contract = {
        "contractId": "pr_resolver_terminal.v1",
        "relativePath": "result.json",
        "expectedSchemaVersion": "moonmind.pr-resolver-result.v1",
        "executionRef": "step:1",
    }
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": "attempt-1",
            "terminalContract": contract,
        }
    )
    calls = []
    result_path = tmp_path / "result.json"
    fail_inspection = True

    async def driver(request, *, session_authority_sink, **kwargs):
        calls.append((request.idempotency_key, kwargs))
        await session_authority_sink.session_created("same-session")
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": "moonmind.pr-resolver-result.v1",
                    "executionRef": "step:1",
                    "phase": "intermediate",
                    "mergeAutomationDisposition": "manual_review",
                    "skillContinuation": {
                        "schemaVersion": "skill-continuation/v1",
                        "executionRef": "step:1",
                        "action": "resume_skill",
                        "progressKey": "same-head:ci",
                        "instructions": "Continue the resolved Skill's CI remediation.",
                    },
                }
            )
        )
        return AgentRunResult(
            summary="diagnostic", metadata={"omnigentSessionId": "same-session"}
        )

    async def inspect(request):
        nonlocal fail_inspection
        if len(calls) == 2 and fail_inspection:
            fail_inspection = False
            raise RuntimeError("worker process lost after the continuation")
        return evaluate_terminal_evidence(contract, workspace_path=str(tmp_path))

    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    with pytest.raises(RuntimeError, match="worker process lost"):
        await complete_skill_turns(
            request=request, sink=sink, driver=driver, inspect_terminal=inspect
        )
    # Reconstruct both the repository and sink, as a replacement worker does.
    restarted = DbRuntimeBindingStore(sessions)
    sink = RuntimeBindingSessionAuthoritySink(
        restarted, await restarted.get(binding.bindingId)
    )
    result = await complete_skill_turns(
        request=request, sink=sink, driver=driver, inspect_terminal=inspect
    )
    assert len(calls) == 2
    assert calls[1][0] == "attempt-1:terminal-contract:1"
    assert calls[1][1]["resume_session_id"] == "same-session"
    assert result.metadata["terminalContractRecoveryOwner"] == "runtime_binding"
    assert result.metadata["terminalContractContinuationCount"] == 1
    assert result.metadata["terminalContractRecoveryOutcome"] == "exhausted"
    with pytest.raises(Exception, match="phase|receipt"):
        await sink.record_phase("turn:0", {"summary": "substituted"})
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
async def test_evidence_read_retries_do_not_repeat_provider_turn(tmp_path, recover):
    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore
    from moonmind.workflows.terminal_evidence import TerminalEvidenceEvaluation

    store = InMemoryStableRuntimeBindingStore()
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "a" * 64,
        idempotency_key="evidence-read",
        provider_leases={},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": "evidence-read",
            "terminalContract": {
                "contractId": "pr_resolver_terminal.v1",
                "relativePath": "result.json",
                "expectedSchemaVersion": "moonmind.pr-resolver-result.v1",
                "executionRef": "step:1",
            },
        }
    )
    calls = []
    reads = []

    async def driver(*args, **kwargs):
        calls.append(1)
        return AgentRunResult(summary="completed work")

    async def inspect(request):
        reads.append(1)
        if recover and len(reads) == 2:
            return TerminalEvidenceEvaluation(True)
        raise ConnectionError("artifact service unavailable")

    result = await complete_skill_turns(
        request=request, sink=sink, driver=driver, inspect_terminal=inspect
    )
    if recover:
        assert result.failure_class is None
        assert len(reads) == 2
    else:
        assert result.metadata["unfinishedPhase"] == "verification"
        assert result.provider_error_code == "VERIFICATION_EVIDENCE_UNAVAILABLE"
        restarted = RuntimeBindingSessionAuthoritySink(
            store, await store.get(binding.bindingId)
        )
        await complete_skill_turns(
            request=request, sink=restarted, driver=driver, inspect_terminal=inspect
        )
        assert len(reads) == 3
    assert calls == [1]


def test_cleaned_binding_reconciles_publication_receipt_without_compute():
    from types import SimpleNamespace

    from moonmind.omnigent.attempt_completion import recorded_attempt_result

    completed = AgentRunResult(
        summary="remote publication verified", metadata={"remoteVerified": True}
    )
    binding = SimpleNamespace(
        terminalResult=None,
        phaseResults={
            "turn:0": {"summary": "compute finished"},
            "publication": completed.model_dump(mode="json", by_alias=True),
        },
    )
    assert recorded_attempt_result(binding) == completed
    binding.phaseResults.pop("publication")
    binding.phaseResults["saved"] = {"archiveRef": "artifact://saved"}
    pending = recorded_attempt_result(binding)
    assert pending.failure_class == "integration_error"
    assert pending.metadata["workPreserved"]
    assert pending.metadata["unfinishedPhase"] == "finalization"


def _finish_request(*, idempotency_key: str):
    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": idempotency_key,
        }
    )


def _finish_realizer(store, *, publish):
    from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer

    realizer = object.__new__(GenericOmnigentHostRealizer)
    realizer._runtime_bindings = store
    realizer._publish_repository = publish
    return realizer


@pytest.mark.asyncio
async def test_failed_compute_with_valid_save_is_not_upgraded():
    """MoonLadderStudios/MoonMind#3825 REQ-04: a valid save never upgrades failed compute."""

    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore

    store = InMemoryStableRuntimeBindingStore()
    request = _finish_request(idempotency_key="failed-compute-saved")
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "b" * 64,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    saved = {"checkpointRef": "artifact://saved-checkpoint", "archiveRef": "artifact://saved"}
    await sink.record_phase("saved", saved)
    published = []

    async def publish(bound, result):
        published.append(1)
        return result

    realizer = _finish_realizer(store, publish=publish)
    failed = AgentRunResult(
        summary="provider turn failed",
        failure_class="execution_error",
        provider_error_code="PROVIDER_TURN_FAILED",
        retry_recommendation="do_not_retry",
    )
    finished = await realizer._finish_owned_execution(request, sink, failed)
    assert published == []
    assert finished.failure_class == "execution_error"
    assert finished.provider_error_code == "PROVIDER_TURN_FAILED"
    assert finished.metadata["workPreserved"] is True
    assert finished.metadata["savedWorkspaceCheckpoint"] == saved
    stored = (await store.get(binding.bindingId)).phaseResults or {}
    assert "saved" in stored
    assert stored["saved"] == saved
    assert AgentRunResult.model_validate(stored["publication"]) == finished


@pytest.mark.asyncio
async def test_publication_exhaustion_preserves_valid_save_without_republishing(monkeypatch):
    """MoonLadderStudios/MoonMind#3825 REQ-04/REQ-09: publication failure keeps the
    valid save and a resume reuses recorded failures instead of republishing."""

    import asyncio

    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore

    async def no_sleep(delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    store = InMemoryStableRuntimeBindingStore()
    request = _finish_request(idempotency_key="publication-exhaustion")
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "c" * 64,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    saved = {"checkpointRef": "artifact://saved-checkpoint", "archiveRef": "artifact://saved"}
    await sink.record_phase("saved", saved)
    published = []

    async def publish(bound, result):
        published.append(1)
        raise HarnessPlatformError(
            "remote publication unavailable",
            code="OMNIGENT_REPOSITORY_PUBLICATION_FAILED",
        )

    realizer = _finish_realizer(store, publish=publish)
    finished = await realizer._finish_owned_execution(
        request, sink, AgentRunResult(summary="verified compute")
    )
    assert published == [1, 1, 1]
    assert finished.failure_class == "integration_error"
    assert finished.provider_error_code == "OMNIGENT_REPOSITORY_PUBLICATION_FAILED"
    assert finished.retry_recommendation == "do_not_retry"
    assert finished.summary.startswith("Agent work is saved;")
    assert finished.metadata["unfinishedPhase"] == "publication"
    assert finished.metadata["workPreserved"] is True
    assert finished.metadata["savedWorkspaceCheckpoint"] == saved
    stored = (await store.get(binding.bindingId)).phaseResults or {}
    assert stored["saved"] == saved
    assert {key for key in stored if key.startswith("publication_failure:")} == {
        "publication_failure:0",
        "publication_failure:1",
        "publication_failure:2",
    }
    assert AgentRunResult.model_validate(stored["publication"]) == finished
    resumed = await realizer._finish_owned_execution(
        request,
        RuntimeBindingSessionAuthoritySink(store, await store.get(binding.bindingId)),
        AgentRunResult(summary="verified compute"),
    )
    assert resumed == finished
    assert published == [1, 1, 1]


@pytest.mark.asyncio
async def test_save_only_candidate_survives_source_host_removal_without_github(
    tmp_path, monkeypatch
):
    """MoonLadderStudios/MoonMind#4016: verified artifact storage is the durability.

    Save-only work saves through the real capture and artifact gateway, then
    loses its whole source worker root (workspace, owner records, host). A
    replacement finalization owner restores usable content from the saved
    checkpoint alone and completes without a provider turn or GitHub lookup.
    """

    import hashlib
    import shutil
    import subprocess

    from api_service.db import models
    from moonmind.omnigent import workspace_publication
    from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
    from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore
    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )
    from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
    from moonmind.workflows.temporal.artifacts import (
        LocalTemporalArtifactStore,
        TemporalArtifactService,
    )
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecord,
        SandboxWorkspaceRecordStore,
    )

    def no_github(*args, **kwargs):
        raise AssertionError("save-only finalization must not look up GitHub")

    monkeypatch.setattr(workspace_publication, "resolve_github_credential", no_github)
    monkeypatch.setattr(workspace_publication, "GitHubService", no_github)

    source_root = tmp_path / "source-worker"
    workflow_id = "save-only-workflow"
    step_id = f"{workflow_id}:source-run:implement:execution:1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = source_root / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)

    def git(path, *args):
        return subprocess.check_output(
            ["git", "-C", str(path), *args], text=True
        ).strip()

    git(workspace, "init", "-q")
    git(workspace, "config", "user.name", "Qualification")
    git(workspace, "config", "user.email", "qualification@example.invalid")
    (workspace / "file.txt").write_text("original\n")
    git(workspace, "add", ".")
    git(workspace, "commit", "-qm", "base")
    # This commit never reaches any remote repository.
    (workspace / "committed.txt").write_text("unpublished work\n")
    git(workspace, "add", ".")
    git(workspace, "commit", "-qm", "candidate")
    head = git(workspace, "rev-parse", "HEAD")
    (workspace / "file.txt").write_text("uncommitted candidate\n")
    SandboxWorkspaceRecordStore(source_root).ensure(
        SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'saved.sqlite'}")
    async with engine.begin() as connection:
        for table in (
            models.TemporalArtifact.__table__,
            models.TemporalArtifactLink.__table__,
            models.TemporalArtifactPin.__table__,
            models.TemporalArtifactUseClaim.__table__,
            models.TemporalArtifactDeletionIntent.__table__,
        ):
            await connection.run_sync(table.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    blob_root = tmp_path / "durable-blob-volume"
    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(lambda: LocalTemporalArtifactStore(blob_root)),
    )

    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": workflow_id,
            "idempotencyKey": "save-only-attempt",
            "parameters": {"publishMode": "none"},
            "workspaceSpec": {
                "repository": "MoonLadderStudios/MoonMind",
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
            },
            "stepExecution": {
                "workflowId": workflow_id,
                "runId": "source-run",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
                "stepExecutionId": step_id,
                "runtimeContextPolicy": "fresh_agent_run",
            },
        }
    )
    gateway = TemporalOmnigentArtifactGateway(session_factory=sessions)
    input_payload = b'{"objective":"save only"}'
    plan_payload = b'{"steps":["implement"]}'
    input_ref = await gateway.write_bytes(
        request=request, name="input", payload=input_payload,
        content_type="application/json", link_type="input",
    )
    plan_ref = await gateway.write_bytes(
        request=request, name="plan", payload=plan_payload,
        content_type="application/json", link_type="input",
    )
    plan_digest = hashlib.sha256(plan_payload).hexdigest()
    request = request.model_copy(
        update={
            "step_execution": request.step_execution.model_copy(
                update={
                    "omnigent_execution_plan": OmnigentExecutionPlanBinding(
                        planRef="omnigent-execution-plan:sha256:" + plan_digest,
                        planDigest="sha256:" + plan_digest,
                        planArtifactRef=plan_ref,
                        taskInputSnapshotRef=input_ref,
                        taskInputSnapshotDigest="sha256:"
                        + hashlib.sha256(input_payload).hexdigest(),
                    ),
                }
            )
        }
    )

    store = InMemoryStableRuntimeBindingStore()
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + plan_digest,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    await sink.record_phase(
        "workspace",
        request.model_dump(
            by_alias=True, mode="json", exclude_none=True,
            include={
                "agent_kind", "agent_id", "correlation_id", "idempotency_key",
                "step_execution", "omnigent_execution_plan",
            },
        ) | {"workspaceSpec": dict(request.workspace_spec)},
    )
    await sink.record_phase(
        "compute",
        AgentRunResult(summary="verified compute").model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
    )

    def realizer_for(root):
        realizer = object.__new__(GenericOmnigentHostRealizer)
        realizer._runtime_bindings = store
        realizer._workspace_publisher = OmnigentWorkspacePublicationService(
            root, artifact_gateway=TemporalOmnigentArtifactGateway(session_factory=sessions)
        )
        return realizer

    # Cleanup saves the drained attempt before releasing its host.
    saved_binding = await realizer_for(source_root)._ensure_saved(
        request, sink.binding
    )
    saved = saved_binding.phaseResults["saved"]
    assert saved["checkpointRef"].startswith("artifact://")
    cleaned = saved_binding
    for state in (RuntimeBindingState.cleanup_pending, RuntimeBindingState.cleaned):
        cleaned = await store.update(
            cleaned.bindingId,
            expected_revision=cleaned.revision,
            expected_fencing_generation=cleaned.fencingGeneration,
            state=state,
        )
    # The source host and everything on it are gone.
    shutil.rmtree(source_root)

    replacement_root = tmp_path / "replacement-worker"
    result = await realizer_for(replacement_root)._reconcile_finalization(
        request, cleaned
    )

    assert result.failure_class is None
    assert result.metadata["workPreserved"] is True
    assert result.metadata["savedWorkspaceCheckpoint"]["checkpointRef"] == (
        saved["checkpointRef"]
    )
    restored = replacement_root / "temporal_sandbox" / workspace_id / "repo"
    assert git(restored, "rev-parse", "HEAD") == head
    assert (restored / "committed.txt").read_text() == "unpublished work\n"
    assert (restored / "file.txt").read_text() == "uncommitted candidate\n"
    phases = (await store.get(binding.bindingId)).phaseResults
    assert phases["saved"] == saved
    assert "restoration" in phases
    assert "publication" in phases
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault", "expected_saves"),
    [
        ("before_object_commit", 2),
        ("partial_upload", 2),
        ("after_object_commit_before_receipt", 2),
        ("publication_interrupted", 1),
    ],
)
async def test_finalization_fault_resumes_only_the_unfinished_phase(
    monkeypatch, fault, expected_saves
):
    """MoonLadderStudios/MoonMind#4016: each fault resumes its own phase.

    A cleaned attempt holds only receipts, never a provider or host. Whichever
    finalization boundary the fault interrupts, the next delivery reruns only
    that boundary through its owner, with the same capture identity, and the
    recorded compute is never recreated.
    """

    import asyncio

    from types import SimpleNamespace

    from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore

    async def no_sleep(delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    class LosesOneReceipt(InMemoryStableRuntimeBindingStore):
        """Loses the write that would first commit one phase receipt."""

        lose: str | None = None

        async def update(self, binding_id, **kwargs):
            phases = (kwargs.get("updates") or {}).get("phaseResults") or {}
            current = await self.get(binding_id)
            if self.lose in phases and self.lose not in (current.phaseResults or {}):
                self.lose = None
                raise ConnectionError("binding store lost the receipt write")
            return await super().update(binding_id, **kwargs)

    store = LosesOneReceipt()
    request = _finish_request(idempotency_key=f"fault-{fault}")
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "7" * 64,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    await sink.record_phase(
        "workspace",
        {"workspaceSpec": {"workspaceLocator": {
            "kind": "sandbox", "workspaceId": "ws-fault", "relativePath": "repo",
        }}},
    )
    await sink.record_phase(
        "compute",
        AgentRunResult(summary="verified compute").model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
    )
    binding = sink.binding
    for state in (RuntimeBindingState.cleanup_pending, RuntimeBindingState.cleaned):
        binding = await store.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=state,
        )

    objects: list[str] = []
    save_keys: list[str] = []
    publications: list[str] = []
    restores: list[str] = []
    saved = {
        "kind": "worktree_archive",
        "archiveRef": "artifact://saved-archive",
        "checkpointRef": "artifact://saved-checkpoint",
    }

    async def save(bound):
        save_keys.append(f"{bound.idempotency_key}:saved-work")
        if fault == "before_object_commit" and len(save_keys) == 1:
            raise ConnectionError("artifact storage unavailable")
        objects.append("archive")
        if fault == "partial_upload" and len(save_keys) == 1:
            raise ConnectionError("upload interrupted after the first object")
        objects.append("checkpoint")
        return saved

    async def restore(bound, recorded):
        restores.append(recorded["checkpointRef"])
        return {"restorationEvidenceRef": "artifact://restored"}

    async def publish(bound, result):
        publications.append("attempt")
        if fault == "publication_interrupted" and len(publications) == 1:
            raise ConnectionError("worker lost during publication")
        return result.model_copy(update={"summary": "remote publication verified"})

    if fault == "after_object_commit_before_receipt":
        store.lose = "saved"
    realizer = object.__new__(GenericOmnigentHostRealizer)
    realizer._runtime_bindings = store
    realizer._workspace_publisher = SimpleNamespace(
        save_request_workspace=save, restore_saved_request_workspace=restore
    )
    realizer._publish_repository = publish

    with pytest.raises(ConnectionError):
        await realizer._reconcile_finalization(request, binding)
    first_phases = (await store.get(binding.bindingId)).phaseResults
    assert "publication" not in first_phases
    assert ("saved" in first_phases) is (fault == "publication_interrupted")

    result = await realizer._reconcile_finalization(
        request, await store.get(binding.bindingId)
    )
    repeated = await realizer._reconcile_finalization(
        request, await store.get(binding.bindingId)
    )

    assert result.failure_class is None
    assert result.summary == "remote publication verified"
    assert result.metadata["savedWorkspaceCheckpoint"] == saved
    assert repeated == result
    assert len(save_keys) == expected_saves
    # A retried capture reuses the same idempotent capture identity.
    assert set(save_keys) == {f"{request.idempotency_key}:saved-work"}
    assert publications[-1] == "attempt"
    assert len(publications) == (2 if fault == "publication_interrupted" else 1)
    assert restores == (
        ["artifact://saved-checkpoint"] if fault == "publication_interrupted" else []
    )
    phases = (await store.get(binding.bindingId)).phaseResults
    assert phases["saved"] == saved
    assert AgentRunResult.model_validate(phases["publication"]) == result
    assert AgentRunResult.model_validate(phases["compute"]).summary == (
        "verified compute"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("publish_mode", ["auto", "none", None, "omitted"])
async def test_parent_never_republishes_skill_owned_or_unpublished_work(publish_mode):
    """Only an explicit branch/pr grant publishes; Skill-owned Auto stays the Skill's."""

    from types import SimpleNamespace

    from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer

    async def publish_request_workspace(**kwargs):
        raise AssertionError("the parent must not publish this workspace")

    realizer = object.__new__(GenericOmnigentHostRealizer)
    realizer._workspace_publisher = SimpleNamespace(
        publish_request_workspace=publish_request_workspace
    )
    parameters = {} if publish_mode == "omitted" else {"publishMode": publish_mode}
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": f"no-parent-publish-{publish_mode}",
            "parameters": parameters,
        }
    )
    computed = AgentRunResult(summary="skill published its own pull request")

    assert await realizer._publish_repository(request, computed) == computed
