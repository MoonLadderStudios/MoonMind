"""Supervisor continuations use existing execution grants, with no human rows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from api_service.db import base, models
from api_service.services import omnigent_execution_plan_service as writer
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.schemas.omnigent_session_models import OmnigentSessionActivityRequest
from moonmind.workflows.temporal.activities import (
    omnigent_session_activities as activities,
)
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _OnDemandTemporalArtifactService,
)
from tests.unit.omnigent.test_admitted_input_authority import (  # noqa: F401
    artifacts,
    auth_mode,
)
from tests.unit.services.test_omnigent_execution_plan_service import (  # noqa: F401
    _compile_opencode_plan,
    _protected_support_evidence,
    _ready_opencode_image_pair,
)

WORKFLOW = "run-wf"
STEP = f"{WORKFLOW}:run-1:step:execution:1"


@pytest_asyncio.fixture
async def supervisor(artifacts, monkeypatch, request):  # noqa: F811
    strict = getattr(request, "param", "ordinary") == "strict"
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_EVIDENCE_POLICY", "protected" if strict else "either"
    )
    monkeypatch.setattr(base, "async_session_maker", artifacts)
    monkeypatch.setattr(
        writer, "_try_load_real_harness_config", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        writer,
        "resolve_execution_evidence",
        lambda plan, **_kw: (
            (_protected_support_evidence(plan), "supported")
            if strict
            else (None, "uncertified")
        ),
    )
    monkeypatch.setattr(
        activities, "_plan_capacity_authority", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        "moonmind.omnigent.realizers.registry.get_default_registry",
        lambda: SimpleNamespace(
            require=lambda _ref: SimpleNamespace(authority_kinds=("model",))
        ),
    )
    artifact_service = _OnDemandTemporalArtifactService(artifacts)
    input_ref, input_digest = await writer.persist_json_artifact(
        artifact_service=artifact_service,
        principal="user-1",
        artifact_class="workflow.task_input_snapshot",
        payload={
            "draft": {"workflow": {"instructions": "Preserve the admitted request."}}
        },
    )
    compiled = await _compile_opencode_plan(
        monkeypatch,
        artifacts=artifact_service,
        plan_store=DbExecutionPlanStore(artifacts),
        session_factory=artifacts,
        launch_policy_ref="omnigent-on-demand@1",
        workflow_id=WORKFLOW,
        task_input_snapshot_ref=input_ref,
        task_input_snapshot_digest=input_digest,
    )
    admission = {
        "agentRunId": "agent-run-1",
        "workflowId": WORKFLOW,
        "stepExecutionId": STEP,
        "executionProfileRef": "provider-opencode-native",
        "omnigentExecutionPlan": compiled.binding.model_dump(by_alias=True),
    }
    await activities.omnigent_evaluate_session_admission_activity(admission)
    return SimpleNamespace(sessions=artifacts, compiled=compiled, admission=admission)


async def _links(sessions):
    async with sessions() as session:
        links = (await session.execute(select(models.TemporalArtifactLink))).scalars()
        return {(row.id, row.artifact_id, row.workflow_id, row.run_id) for row in links}


async def _resolve(context):
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore

    resolved = await activities.omnigent_resolve_intent_activity(
        {
            key: value
            for key, value in context.admission.items()
            if key != "executionProfileRef"
        }
    )
    async with OmnigentControlPlaneStore(context.sessions).transaction() as repos:
        session = await repos.sessions.get(resolved["sessionId"])
    return {
        **resolved,
        "expectedRevision": session.revision,
        "fencingGeneration": session.fencing_generation,
    }


def _activity(resolved, **updates):
    return OmnigentSessionActivityRequest(
        sessionId=resolved["sessionId"],
        compiledExecutionIntentRef=resolved["compiledExecutionIntentRef"],
        compiledExecutionIntentDigest=resolved["compiledExecutionIntentDigest"],
        omnigentExecutionPlan=resolved["omnigentExecutionPlan"],
        expectedRevision=resolved["expectedRevision"],
        fencingGeneration=resolved["fencingGeneration"],
        **updates,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("supervisor", ["ordinary", "strict"], indirect=True)
@pytest.mark.parametrize("auth_mode", ["accounts", "oidc", "header", "disabled"])
async def test_resolve_and_reconcile_read_admitted_artifacts_without_new_grants(
    supervisor,
):
    before = await _links(supervisor.sessions)
    resolved = await _resolve(supervisor)
    loaded = await activities.omnigent_load_reconciliation_inputs_activity(resolved)
    assert loaded["durable"]["sessionId"] == resolved["sessionId"]
    request = await activities._load_intent_request(_activity(resolved))
    assert request.instruction_ref == "Preserve the admitted request."
    assert await _links(supervisor.sessions) == before
    async with supervisor.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(models.User)) == 0


async def _command(context, resolved, *, command_id="command-1", session_id=None):
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore

    async with OmnigentControlPlaneStore(context.sessions).transaction() as repos:
        await repos.commands.record(
            command_id=command_id,
            session_id=session_id or resolved["sessionId"],
            command_type="ensure_host",
            idempotency_key=command_id,
            payload_digest="a" * 64,
            expected_session_revision=resolved["expectedRevision"],
            fencing_generation=resolved["fencingGeneration"],
            owner_class="omnigent_session_workflow",
        )
    return _activity(resolved, commandId=command_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_mode", ["accounts", "oidc", "header"])
async def test_resolve_cannot_admit_another_workflow(supervisor):
    from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError

    before = await _links(supervisor.sessions)
    payload = {
        **supervisor.admission,
        "workflowId": "other-wf",
        "stepExecutionId": "other-wf:run-1:step:execution:1",
    }
    payload.pop("executionProfileRef")
    with pytest.raises(TemporalArtifactAuthorizationError):
        await activities.omnigent_resolve_intent_activity(payload)
    assert await _links(supervisor.sessions) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["session", "intent", "plan", "fence", "revision"])
async def test_command_validates_canonical_authority_before_reading_plan(
    supervisor, monkeypatch, tamper
):
    from moonmind.omnigent.control_plane import (
        FencingConflictError,
        RevisionConflictError,
    )

    resolved = await _resolve(supervisor)
    request = await _command(supervisor, resolved)
    updates = {
        "session": {"session_id": "unknown-session"},
        "intent": {"compiled_execution_intent_digest": "sha256:" + "b" * 64},
        "plan": {
            "omnigent_execution_plan": supervisor.compiled.binding.model_copy(
                update={"plan_artifact_ref": "art_unrelated"}
            )
        },
        "fence": {"fencing_generation": request.fencing_generation + 1},
        "revision": {"expected_revision": request.expected_revision + 1},
    }
    reader = AsyncMock(wraps=activities._read_json_artifact)
    monkeypatch.setattr(activities, "_read_json_artifact", reader)
    before = await _links(supervisor.sessions)
    with pytest.raises(
        (ValueError, KeyError, FencingConflictError, RevisionConflictError)
    ):
        await activities._claim_command(request.model_copy(update=updates[tamper]))
    reader.assert_not_awaited()
    assert await _links(supervisor.sessions) == before


@pytest.mark.asyncio
async def test_intent_snapshot_cannot_supply_another_sessions_scope(supervisor):
    resolved = await _resolve(supervisor)
    with pytest.raises(ValueError, match="session"):
        await activities._load_intent_request(
            _activity(resolved).model_copy(update={"session_id": "other-session"})
        )


@pytest.mark.asyncio
async def test_signal_continuation_and_retry_reuse_existing_grants(supervisor):
    resolved = await _resolve(supervisor)
    before = await _links(supervisor.sessions)
    payload = {
        **_activity(resolved).model_dump(by_alias=True),
        "signals": [{"kind": "timeout_requested"}],
    }
    for _ in range(2):
        result = await activities.omnigent_persist_signal_intents_activity(payload)
        assert result["appliedIntentCount"] == 1
    assert await _links(supervisor.sessions) == before


@pytest.mark.asyncio
async def test_failure_and_cleanup_read_admitted_plan_without_new_grants(supervisor):
    resolved = await _resolve(supervisor)
    before = await _links(supervisor.sessions)
    payload = {
        **_activity(resolved).model_dump(by_alias=True),
        "status": "execution_failed",
        "failedActivity": "omnigent.ensure_host",
        "reasonCode": "session_deadline_elapsed",
    }
    result = await activities.omnigent_persist_failure_activity(payload)
    assert result["terminalResult"]["status"] == "execution_failed"
    assert await activities.omnigent_persist_failure_activity(payload) == result
    assert await _links(supervisor.sessions) == before


async def _replace_support(context, *, ref, digest):
    from moonmind.omnigent.harness_platform.execution_plan import (
        create_execution_plan_envelope,
    )

    authority = context.compiled.envelope.payload.admissionAuthority.model_copy(
        update={
            "supportEvidenceRef": ref,
            "supportEvidenceDigest": digest,
            "supportTier": "supported",
        }
    )
    plan = create_execution_plan_envelope(
        context.compiled.envelope.payload.model_copy(
            update={"admissionAuthority": authority}
        )
    )
    await DbExecutionPlanStore(context.sessions).persist(plan)
    plan_ref, _ = await writer.persist_json_artifact(
        artifact_service=_OnDemandTemporalArtifactService(context.sessions),
        principal="user-1",
        artifact_class="omnigent.execution_plan",
        payload=plan.model_dump(mode="json", by_alias=True),
    )
    return context.compiled.binding.model_copy(
        update={
            "plan_ref": plan.planRef,
            "plan_digest": "sha256:" + plan.planRef.rsplit(":", 1)[-1],
            "plan_artifact_ref": plan_ref,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("supervisor", ["strict"], indirect=True)
@pytest.mark.parametrize("wrong", ["class", "digest"])
async def test_wrong_strict_certificate_pointer_grants_nothing(supervisor, wrong):
    from moonmind.omnigent.bridge_artifacts import OmnigentArtifactError

    ref, digest = await writer.persist_json_artifact(
        artifact_service=_OnDemandTemporalArtifactService(supervisor.sessions),
        principal="user-1",
        artifact_class="unrelated.private"
        if wrong == "class"
        else "omnigent.execution_support_evidence",
        payload={"private": "unrelated bytes"},
    )
    binding = await _replace_support(
        supervisor,
        ref=f"artifact:{ref}",
        digest=digest if wrong == "class" else "sha256:" + "0" * 64,
    )
    before = await _links(supervisor.sessions)
    with pytest.raises(OmnigentArtifactError, match="support evidence"):
        await activities._load_verified_execution_plan(
            binding, workflow_id="new-wf", run_id="new-run"
        )
    assert await _links(supervisor.sessions) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("supervisor", ["strict"], indirect=True)
@pytest.mark.parametrize(
    "ref", ["artifact:art_missing", "artifact:", "artifact:invalid"]
)
async def test_pre_upgrade_missing_certificate_keeps_ordinary_reissue(
    supervisor, monkeypatch, ref
):
    binding = await _replace_support(supervisor, ref=ref, digest="sha256:" + "0" * 64)
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    migrated = await activities._load_verified_execution_plan(
        binding, workflow_id="new-wf", run_id="new-run"
    )
    assert migrated.payload.admissionAuthority.admissionMode == "ordinary"
    assert migrated.payload.admissionAuthority.supportTier == "uncertified"


@pytest.mark.asyncio
async def test_ordinary_optional_certificate_is_not_required(supervisor):
    binding = await _replace_support(
        supervisor, ref="artifact:art_missing", digest="sha256:" + "0" * 64
    )
    plan = await activities._load_verified_execution_plan(
        binding, workflow_id="new-wf", run_id="new-run"
    )
    assert plan.payload.admissionAuthority.admissionMode == "ordinary"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["fence", "revision"])
async def test_delayed_command_cannot_read_after_canonical_authority_changes(
    supervisor, monkeypatch, change
):
    from moonmind.omnigent.control_plane import (
        FencingConflictError,
        FencingScope,
        OmnigentControlPlaneStore,
        RevisionConflictError,
    )

    resolved = await _resolve(supervisor)
    request = await _command(supervisor, resolved)
    async with OmnigentControlPlaneStore(supervisor.sessions).transaction() as repos:
        if change == "fence":
            await repos.sessions.acquire_fencing_generation(
                request.session_id,
                FencingScope.SESSION_SUPERVISOR,
                expected_revision=request.expected_revision,
            )
        else:
            await repos.sessions.update_lifecycle(
                request.session_id,
                expected_revision=request.expected_revision,
                expected_fencing_generation=request.fencing_generation,
                desired_state="cancel",
            )
    reader = AsyncMock(wraps=activities._read_json_artifact)
    monkeypatch.setattr(activities, "_read_json_artifact", reader)
    with pytest.raises((FencingConflictError, RevisionConflictError)):
        await activities._claim_command(request)
    reader.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_reads_plan_and_preserves_realizer_ownership(supervisor):
    resolved = await _resolve(supervisor)
    request = await _command(supervisor, resolved)
    before = await _links(supervisor.sessions)
    with pytest.raises(
        ValueError, match="generic host cleanup belongs to the AgentRun realizer"
    ):
        await activities.omnigent_stop_host_activity(request.model_dump(by_alias=True))
    assert await _links(supervisor.sessions) == before


@pytest.mark.asyncio
async def test_signal_retry_still_rejects_foreign_intent(supervisor):
    resolved = await _resolve(supervisor)
    payload = {
        **_activity(resolved).model_dump(by_alias=True),
        "signals": [{"kind": "timeout_requested"}],
    }
    await activities.omnigent_persist_signal_intents_activity(payload)
    payload["compiledExecutionIntentDigest"] = "sha256:" + "d" * 64
    with pytest.raises(ValueError, match="intent authority"):
        await activities.omnigent_persist_signal_intents_activity(payload)


@pytest.mark.asyncio
async def test_legacy_request_snapshot_continues_under_its_original_scope(supervisor):
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore

    binding = supervisor.compiled.binding
    plan = await activities._load_verified_execution_plan(
        binding, admitted_principal=f"workflow:{WORKFLOW}"
    )
    request = await activities._reconstruct_plan_bound_request(
        binding=binding,
        plan=plan,
        workflow_id=WORKFLOW,
        step_execution_id=STEP,
        agent_run_id="legacy-agent-run",
    )
    before = await _links(supervisor.sessions)
    resolved = await activities.omnigent_resolve_intent_activity(
        {
            "request": request.model_dump(mode="json", by_alias=True),
            "workflowId": WORKFLOW,
            "stepExecutionId": STEP,
            "agentRunId": "legacy-agent-run",
        }
    )
    async with OmnigentControlPlaneStore(supervisor.sessions).transaction() as repos:
        session = await repos.sessions.get(resolved["sessionId"])
    resolved.update(
        expectedRevision=session.revision, fencingGeneration=session.fencing_generation
    )
    snapshot = await activities._read_json_artifact(
        resolved["compiledExecutionIntentRef"]
    )
    assert snapshot["schemaVersion"] == "omnigent-compiled-execution-intent/v1"
    restored = await activities._load_intent_request(_activity(resolved))
    assert restored == request
    assert await _links(supervisor.sessions) == before


@pytest.mark.asyncio
async def test_claimed_command_retry_can_read_after_its_own_revision_advance(
    supervisor,
):
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore

    resolved = await _resolve(supervisor)
    request = await _command(supervisor, resolved)
    before = await _links(supervisor.sessions)
    await activities._claim_command(request)
    async with OmnigentControlPlaneStore(supervisor.sessions).transaction() as repos:
        await repos.sessions.update_lifecycle(
            request.session_id,
            expected_revision=request.expected_revision,
            expected_fencing_generation=request.fencing_generation,
            desired_state="cancel",
        )
    command, _execute = await activities._claim_command(request)
    assert command.session_id == request.session_id
    loaded = await activities.omnigent_load_reconciliation_inputs_activity(resolved)
    assert loaded["durable"]["revision"] > request.expected_revision
    assert await _links(supervisor.sessions) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("consumer", "error"),
    [
        (
            activities.omnigent_ensure_provider_profile_lease_activity,
            "Provider Profile does not exist",
        ),
        (
            activities.omnigent_ensure_host_activity,
            "Provider Profile lease authority is missing",
        ),
    ],
)
async def test_launch_consumers_read_inputs_before_enforcing_profile_prerequisites(
    supervisor, consumer, error
):
    resolved = await _resolve(supervisor)
    request = await _command(supervisor, resolved)
    before = await _links(supervisor.sessions)
    with pytest.raises(ValueError, match=error):
        await consumer(request.model_dump(by_alias=True))
    assert await _links(supervisor.sessions) == before
