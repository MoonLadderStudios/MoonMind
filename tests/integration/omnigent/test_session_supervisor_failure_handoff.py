"""Production persistence boundaries for Omnigent supervisor remediation."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.config.settings import settings
from moonmind.omnigent.control_plane import (
    ControlPlaneOutcome,
    FencingScope,
    OmnigentControlPlaneStore,
)
from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.schemas.omnigent_session_models import OmnigentSessionTerminalResult
from moonmind.workflows.temporal.activities.omnigent_session_activities import (
    _write_json_artifact,
    omnigent_evaluate_session_admission_activity,
    omnigent_load_failure_authority_activity,
    omnigent_persist_failure_activity,
    omnigent_stop_host_activity,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest_asyncio.fixture()
async def production_store(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/supervisor.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("api_service.db.base.async_session_maker", maker)
    monkeypatch.setattr(
        settings.workflow,
        "temporal_artifact_backend",
        "local_fs",
    )
    monkeypatch.setattr(
        settings.workflow,
        "temporal_artifact_root",
        str(tmp_path / "artifacts"),
    )
    try:
        yield OmnigentControlPlaneStore(maker)
    finally:
        await engine.dispose()


async def _seed_session(store: OmnigentControlPlaneStore, session_id: str):
    session, _turn = await store.establish_session(
        session_id=session_id,
        moonmind_workflow_id=f"workflow-{session_id}",
        provider="omnigent",
        chat_binding_id=f"chat-{session_id}",
        first_turn_attempt_id=f"turn-{session_id}",
        first_turn_idempotency_key=f"idem-{session_id}",
        step_execution_id=f"step-{session_id}",
        moonmind_agent_run_id=f"agent-{session_id}",
        compatibility_profile="v1",
        intent_ref=f"art_intent_{session_id}",
        intent_digest="sha256:" + "d" * 64,
        instruction_digest="sha256:instruction",
    )
    async with store.transaction() as repos:
        return await repos.sessions.acquire_fencing_generation(
            session_id,
            scope=FencingScope.SESSION_SUPERVISOR,
            expected_revision=session.revision,
        )


async def _attach_primary_success(store: OmnigentControlPlaneStore, session) -> tuple[object, str]:
    terminal = OmnigentSessionTerminalResult(
        status="completed",
        result=AgentRunResult(
            summary="Primary provider execution completed",
            metadata={
                "canonicalSessionId": session.session_id,
                "omnigentSessionStatus": "completed",
            },
        ),
    )
    terminal_ref = await _write_json_artifact(
        name="primary-terminal.json",
        artifact_type="omnigent.session_terminal",
        payload={
            "schemaVersion": "omnigent-session-terminal-evidence/v1",
            "sessionId": session.session_id,
            "terminalResult": terminal.model_dump(mode="json", by_alias=True),
        },
    )
    async with store.transaction() as repos:
        current = await repos.sessions.mark_terminal(
            session.session_id,
            "completed",
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
            terminal_evidence_ref=terminal_ref,
        )
        current = await repos.sessions.update_lifecycle(
            session.session_id,
            expected_revision=current.revision,
            expected_fencing_generation=current.fencing_generation,
            cleanup_state="provider_stopped",
        )
    return current, terminal_ref


async def test_production_admission_is_bounded_and_disable_only_blocks_new_selection(
    monkeypatch,
) -> None:
    flags = settings.feature_flags
    monkeypatch.setattr(flags, "omnigent_session_supervisor_admission_mode", "canary")
    monkeypatch.setattr(
        flags,
        "omnigent_session_supervisor_canary_owner_ids",
        "agent-canary",
    )
    monkeypatch.setattr(
        flags,
        "omnigent_session_supervisor_allowed_execution_profile_refs",
        "profile-canary",
    )
    payload = {
        "workflowId": "workflow-admission",
        "stepExecutionId": "step-admission",
        "agentRunId": "agent-canary",
        "executionProfileRef": "profile-canary",
    }

    admitted = await omnigent_evaluate_session_admission_activity(payload)
    assert admitted["admitted"] is True
    assert admitted["reasonCode"] == "canary_selected"

    denied = await omnigent_evaluate_session_admission_activity(
        {**payload, "agentRunId": "agent-outside-canary"}
    )
    assert denied["admitted"] is False
    assert denied["reasonCode"] == "canary_owner_not_allowlisted"

    profile_denied = await omnigent_evaluate_session_admission_activity(
        {**payload, "executionProfileRef": "profile-outside-canary"}
    )
    assert profile_denied["admitted"] is False
    assert profile_denied["reasonCode"] == "execution_profile_not_allowlisted"

    monkeypatch.setattr(
        flags,
        "omnigent_session_supervisor_generation",
        "superseded-generation",
    )
    generation_denied = await omnigent_evaluate_session_admission_activity(payload)
    assert generation_denied["admitted"] is False
    assert generation_denied["reasonCode"] == "feature_generation_mismatch"
    monkeypatch.setattr(
        flags,
        "omnigent_session_supervisor_generation",
        "omnigent-session-v1",
    )

    monkeypatch.setattr(flags, "omnigent_session_supervisor_admission_mode", "disabled")
    disabled = await omnigent_evaluate_session_admission_activity(payload)
    assert disabled["admitted"] is False
    assert disabled["reasonCode"] == "new_selection_disabled"


async def test_failure_authority_validates_immutable_child_ownership(
    production_store,
) -> None:
    session = await _seed_session(production_store, "failure-authority")
    payload = {
        "sessionId": session.session_id,
        "compiledExecutionIntentRef": session.intent_ref,
        "compiledExecutionIntentDigest": session.intent_digest,
        "workflowId": session.moonmind_workflow_id,
        "stepExecutionId": session.step_execution_id,
        "agentRunId": session.moonmind_agent_run_id,
    }

    authority = await omnigent_load_failure_authority_activity(payload)
    assert authority == {
        "sessionId": session.session_id,
        "revision": session.revision,
        "fencingGeneration": session.fencing_generation,
    }

    with pytest.raises(ValueError, match="conflicts with canonical session"):
        await omnigent_load_failure_authority_activity(
            {**payload, "agentRunId": "superseded-agent-run"}
        )


async def test_applied_command_response_loss_reconciles_without_false_failure(
    production_store,
) -> None:
    session = await _seed_session(production_store, "applied-command")
    command_id = "applied-command:command"
    claim_token = f"omnigent-session:{session.session_id}:{command_id}"
    async with production_store.transaction() as repos:
        await repos.commands.record(
            command_id=command_id,
            session_id=session.session_id,
            command_type="ensure_host",
            idempotency_key=command_id,
            payload_digest="sha256:ensure-host",
            expected_session_revision=session.revision,
            fencing_generation=session.fencing_generation,
            owner_class="omnigent_session_workflow",
        )
        await repos.commands.claim_command(
            command_id,
            owner_class="omnigent_session_activity",
            claim_token=claim_token,
        )
        await repos.commands.record_command_delivery(
            command_id,
            owner_class="omnigent_session_activity",
            claim_token=claim_token,
            outcome=ControlPlaneOutcome.APPLIED,
        )

    response = await omnigent_persist_failure_activity(
        {
            "sessionId": session.session_id,
            "compiledExecutionIntentRef": session.intent_ref,
            "compiledExecutionIntentDigest": session.intent_digest,
            "expectedRevision": session.revision,
            "fencingGeneration": session.fencing_generation,
            "decisionId": "applied-command:decision",
            "commandId": command_id,
            "status": "integration_unavailable",
            "failedActivity": "omnigent.ensure_host",
            "reasonCode": "bounded_activity_exhausted",
        }
    )

    assert response == {
        "failurePersisted": False,
        "reconcileRequired": True,
        "commandOutcome": "already_applied",
    }
    async with production_store.transaction() as repos:
        persisted = await repos.sessions.get(session.session_id)
    assert persisted is not None and persisted.terminal_state is None


async def test_failure_before_command_record_still_terminalizes_session(
    production_store,
) -> None:
    session = await _seed_session(production_store, "missing-command")
    response = await omnigent_persist_failure_activity(
        {
            "sessionId": session.session_id,
            "compiledExecutionIntentRef": session.intent_ref,
            "compiledExecutionIntentDigest": session.intent_digest,
            "expectedRevision": session.revision,
            "fencingGeneration": session.fencing_generation,
            "decisionId": "missing-command:decision",
            "commandId": "missing-command:not-recorded",
            "status": "integration_unavailable",
            "failedActivity": "omnigent.persist_decision",
            "reasonCode": "bounded_activity_exhausted",
        }
    )

    terminal = OmnigentSessionTerminalResult.model_validate(response["terminalResult"])
    assert terminal.status == "integration_unavailable"
    async with production_store.transaction() as repos:
        persisted = await repos.sessions.get(session.session_id)
    assert persisted is not None
    assert persisted.terminal_state == "integration_unavailable"
    assert persisted.terminal_evidence_ref == response["terminalResultRef"]


async def test_delivery_unknown_command_remains_parked_with_terminal_evidence(
    production_store,
) -> None:
    session = await _seed_session(production_store, "delivery-unknown")
    command_id = "delivery-unknown:command"
    claim_token = f"omnigent-session:{session.session_id}:{command_id}"
    async with production_store.transaction() as repos:
        await repos.commands.record(
            command_id=command_id,
            session_id=session.session_id,
            command_type="submit_turn",
            idempotency_key=command_id,
            payload_digest="sha256:submit-turn",
            expected_session_revision=session.revision,
            fencing_generation=session.fencing_generation,
            owner_class="omnigent_session_workflow",
        )
        await repos.commands.claim_command(
            command_id,
            owner_class="omnigent_session_activity",
            claim_token=claim_token,
        )
        await repos.commands.record_command_delivery(
            command_id,
            owner_class="omnigent_session_activity",
            claim_token=claim_token,
            outcome=ControlPlaneOutcome.DELIVERY_UNKNOWN,
        )

    response = await omnigent_persist_failure_activity(
        {
            "sessionId": session.session_id,
            "compiledExecutionIntentRef": session.intent_ref,
            "compiledExecutionIntentDigest": session.intent_digest,
            "expectedRevision": session.revision,
            "fencingGeneration": session.fencing_generation,
            "decisionId": "delivery-unknown:decision",
            "commandId": command_id,
            "status": "delivery_unknown",
            "failedActivity": "omnigent.submit_turn",
            "reasonCode": "bounded_activity_exhausted",
        }
    )

    terminal = OmnigentSessionTerminalResult.model_validate(response["terminalResult"])
    assert terminal.status == "delivery_unknown"
    assert terminal.result.metadata["omnigentSessionStatus"] == "delivery_unknown"
    async with production_store.transaction() as repos:
        persisted = await repos.sessions.get(session.session_id)
        command = await repos.commands.get(command_id)
    assert persisted is not None
    assert persisted.terminal_state == "delivery_unknown"
    assert command is not None
    assert command.status == "delivery_unknown"
    assert command.result_ref == response["terminalResultRef"]


async def test_post_terminal_integration_failure_preserves_primary_evidence(
    production_store,
) -> None:
    session = await _seed_session(production_store, "post-terminal-failure")
    session, primary_ref = await _attach_primary_success(production_store, session)
    response = await omnigent_persist_failure_activity(
        {
            "sessionId": session.session_id,
            "compiledExecutionIntentRef": session.intent_ref,
            "compiledExecutionIntentDigest": session.intent_digest,
            "expectedRevision": session.revision,
            "fencingGeneration": session.fencing_generation,
            "decisionId": "post-terminal-failure:decision",
            "status": "integration_unavailable",
            "failedActivity": "omnigent.publish_workspace",
            "reasonCode": "bounded_activity_exhausted",
        }
    )

    terminal = OmnigentSessionTerminalResult.model_validate(response["terminalResult"])
    assert terminal.status == "integration_unavailable"
    assert terminal.result.failure_class == "integration_error"
    assert terminal.result.metadata["primaryOmnigentSessionStatus"] == "completed"
    assert primary_ref in terminal.result.output_refs
    async with production_store.transaction() as repos:
        persisted = await repos.sessions.get(session.session_id)
    assert persisted is not None
    assert persisted.terminal_state == "completed"
    assert persisted.terminal_evidence_ref == primary_ref
    assert persisted.metadata["workflowFailureEvidenceRef"] == (
        response["terminalResultRef"]
    )


async def test_cleanup_exhaustion_preserves_primary_and_records_claimable_handoff(
    production_store,
) -> None:
    session = await _seed_session(production_store, "cleanup-handoff")
    session, primary_ref = await _attach_primary_success(production_store, session)
    command_id = "cleanup-handoff:command"
    async with production_store.transaction() as repos:
        await repos.commands.record(
            command_id=command_id,
            session_id=session.session_id,
            command_type="begin_cleanup",
            idempotency_key=command_id,
            payload_digest="sha256:cleanup",
            expected_session_revision=session.revision,
            fencing_generation=session.fencing_generation,
            owner_class="omnigent_session_workflow",
        )
        await repos.commands.claim_command(
            command_id,
            owner_class="omnigent_session_activity",
            claim_token=f"omnigent-session:{session.session_id}:{command_id}",
        )

    payload = {
        "sessionId": session.session_id,
        "compiledExecutionIntentRef": session.intent_ref,
        "compiledExecutionIntentDigest": session.intent_digest,
        "expectedRevision": session.revision,
        "fencingGeneration": session.fencing_generation,
        "decisionId": "cleanup-handoff:decision",
        "commandId": command_id,
        "status": "cleanup_incomplete",
        "failedActivity": "omnigent.stop_host",
        "reasonCode": "bounded_activity_exhausted",
    }
    response = await omnigent_persist_failure_activity(payload)
    retried = await omnigent_persist_failure_activity(payload)

    terminal = OmnigentSessionTerminalResult.model_validate(response["terminalResult"])
    retried_terminal = OmnigentSessionTerminalResult.model_validate(
        retried["terminalResult"]
    )
    assert retried["terminalResultRef"] == response["terminalResultRef"]
    assert retried_terminal.result.output_refs == terminal.result.output_refs
    assert terminal.status == "cleanup_incomplete"
    assert terminal.result.failure_class is None
    assert terminal.result.metadata["primaryOmnigentSessionStatus"] == "completed"
    assert terminal.result.metadata["janitorRequired"] is True
    assert primary_ref in terminal.result.output_refs

    async with production_store.transaction() as repos:
        persisted = await repos.sessions.get(session.session_id)
        command = await repos.commands.get(command_id)
        handoff = await repos.cleanup.get(session.session_id)
        claim = await repos.cleanup.claim_cleanup(
            session.session_id,
            owner_class="janitor",
            claim_token="janitor-recovery-1",
        )
    assert persisted is not None
    assert persisted.terminal_state == "completed"
    assert persisted.cleanup_state == "cleanup_incomplete"
    assert persisted.metadata["cleanupOwner"] == (
        "integration.omnigent.oauth_host_janitor"
    )
    assert command is not None and command.status == "failed"
    assert handoff is not None and handoff.state == "unclaimed"
    assert claim.outcome is ControlPlaneOutcome.APPLIED


async def test_cleanup_handoff_preserves_primary_integration_failure(
    production_store,
) -> None:
    session = await _seed_session(production_store, "failed-cleanup-handoff")
    failed = await omnigent_persist_failure_activity(
        {
            "sessionId": session.session_id,
            "compiledExecutionIntentRef": session.intent_ref,
            "compiledExecutionIntentDigest": session.intent_digest,
            "expectedRevision": session.revision,
            "fencingGeneration": session.fencing_generation,
            "status": "integration_unavailable",
            "failedActivity": "omnigent.load_reconciliation_inputs",
            "reasonCode": "bounded_activity_exhausted",
        }
    )
    async with production_store.transaction() as repos:
        terminalized = await repos.sessions.get(session.session_id)
    assert terminalized is not None

    cleanup = await omnigent_persist_failure_activity(
        {
            "sessionId": terminalized.session_id,
            "compiledExecutionIntentRef": terminalized.intent_ref,
            "compiledExecutionIntentDigest": terminalized.intent_digest,
            "expectedRevision": terminalized.revision,
            "fencingGeneration": terminalized.fencing_generation,
            "status": "cleanup_incomplete",
            "failedActivity": "omnigent.load_reconciliation_inputs",
            "reasonCode": "bounded_activity_exhausted",
        }
    )

    terminal = OmnigentSessionTerminalResult.model_validate(
        cleanup["terminalResult"]
    )
    assert terminal.status == "cleanup_incomplete"
    assert terminal.result.failure_class == "integration_error"
    assert terminal.result.metadata["primaryOmnigentSessionStatus"] == (
        "integration_unavailable"
    )
    assert terminal.result.metadata["janitorRequired"] is True
    assert failed["terminalResultRef"] in terminal.result.output_refs
    assert cleanup["terminalResultRef"] in terminal.result.output_refs

    async with production_store.transaction() as repos:
        persisted = await repos.sessions.get(session.session_id)
        handoff = await repos.cleanup.get(session.session_id)
    assert persisted is not None
    assert persisted.terminal_state == "integration_unavailable"
    assert persisted.terminal_evidence_ref == failed["terminalResultRef"]
    assert persisted.metadata["workflowFailureEvidenceRef"] == (
        failed["terminalResultRef"]
    )
    assert persisted.metadata["cleanupEvidenceRef"] == (
        cleanup["terminalResultRef"]
    )
    assert handoff is not None and handoff.state == "unclaimed"


async def test_production_host_cleanup_retry_is_idempotent(production_store) -> None:
    session = await _seed_session(production_store, "host-cleanup-retry")
    session, _primary_ref = await _attach_primary_success(production_store, session)
    command_id = "host-cleanup-retry:command"
    async with production_store.transaction() as repos:
        await repos.commands.record(
            command_id=command_id,
            session_id=session.session_id,
            command_type="begin_cleanup",
            idempotency_key=command_id,
            payload_digest="sha256:host-cleanup",
            expected_session_revision=session.revision,
            fencing_generation=session.fencing_generation,
            owner_class="omnigent_session_workflow",
        )
    payload = {
        "sessionId": session.session_id,
        "compiledExecutionIntentRef": session.intent_ref,
        "compiledExecutionIntentDigest": session.intent_digest,
        "expectedRevision": session.revision,
        "fencingGeneration": session.fencing_generation,
        "decisionId": "host-cleanup-retry:decision",
        "commandId": command_id,
    }

    first = await omnigent_stop_host_activity(payload)
    second = await omnigent_stop_host_activity(payload)

    assert first["outcome"] == "applied"
    assert second["outcome"] == "applied"
    async with production_store.transaction() as repos:
        persisted = await repos.sessions.get(session.session_id)
        command = await repos.commands.get(command_id)
    assert persisted is not None and persisted.cleanup_state == "host_stopped"
    assert command is not None and command.status == "applied"
