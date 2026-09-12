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
async def test_cleaned_attempt_finishes_publication_without_recreating_provider(
    tmp_path,
):
    from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore

    store = InMemoryStableRuntimeBindingStore()
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": "attempt-finalization",
            "workspaceSpec": {"repository": "owner/repo"},
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
        "compute",
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
