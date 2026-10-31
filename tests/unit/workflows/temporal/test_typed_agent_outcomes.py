"""Issue #4984: preserve result facts through the actual parent and API owners."""

from copy import deepcopy
from contextlib import asynccontextmanager

import pytest

from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.statuses.workflow import MoonMindWorkflowState
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.api.routers.test_executions import (
    _serialize_execution,
    _serialize_execution_list_item,
)


@asynccontextmanager
async def _persisted_execution(tmp_path):
    from api_service.db.models import (
        TemporalExecutionRecord,
        TemporalExecutionOwnerType,
        TemporalWorkflowType,
    )
    from tests.unit.workflows.temporal.test_remediation_context import temporal_db

    async with temporal_db(tmp_path) as session:
        record = TemporalExecutionRecord(
            workflow_id="mm:typed",
            run_id="run-typed",
            namespace="moonmind",
            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_type=TemporalExecutionOwnerType.SYSTEM,
            owner_id="system",
            entry="run",
            state=MoonMindWorkflowState.COMPLETED,
            memo={"objectiveOutcome": "active"},
        )
        session.add(record)
        await session.flush()
        yield session, record, TemporalExecutionService(session)


CASES = [
    dict(executionState="completed", objectiveOutcome="succeeded"),
    dict(executionState="completed", objectiveOutcome="failed"),
    dict(
        executionState="completed",
        objectiveOutcome="succeeded",
        publicationState="published",
        secondaryErrors=[dict(code="reporting_failed", diagnosticsRef="art_report")],
    ),
    dict(
        executionState="unknown", objectiveOutcome="unknown", observation="unavailable"
    ),
    dict(
        executionState="delivery_unknown",
        publicationState="unknown",
        observation="unavailable",
    ),
    dict(
        executionState="failed",
        failureClass="user_error",
        providerErrorCode="401",
        retryDisposition="no_retry",
    ),
    dict(
        executionState="failed",
        failureClass="execution_error",
        providerErrorCode="429",
        retryDisposition="wait",
    ),
]


@pytest.mark.parametrize("facts", CASES)
@pytest.mark.asyncio
async def test_parent_persistence_and_list_detail_preserve_facts(
    monkeypatch, facts, tmp_path
):
    from moonmind.omnigent.bridge_artifacts import build_omnigent_result
    from tests.unit.omnigent.test_execute import _bundle, _request

    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    parent = run_module.MoonMindRunWorkflow()
    produced = build_omnigent_result(
        request=_request(),
        terminal_status=facts["executionState"],
        session_id="session",
        agent_id="agent",
        final_snapshot={
            key: value
            for key, value in facts.items()
            if key in {"objectiveOutcome", "publicationState", "secondaryErrors"}
        },
        event_count=1,
        capture_bundle=_bundle(diagnostics_ref="art_original"),
        provider_error_code=facts.get("providerErrorCode"),
    )
    # Confirmed auth/quota evidence comes from the provider contract, not text.
    if facts.get("providerErrorCode"):
        produced = AgentRunResult.model_validate(
            {**produced.model_dump(by_alias=True), **facts}
        )
    returned = AgentRunResult.model_validate_json(
        produced.model_dump_json(by_alias=True)
    )
    mapped = parent._map_agent_run_result(returned)
    canonical = mapped["outputs"]["agentResult"]
    for key, value in facts.items():
        assert canonical[key] == value
    assert canonical["diagnosticsRef"] == "art_original"
    async with _persisted_execution(tmp_path) as (session, record, service):
        service._record_finish_summary(
            record, finish_outcome_code=None, finish_summary={"agentResult": canonical}
        )
        await session.commit()
        await session.refresh(record)
        for serializer in (_serialize_execution, _serialize_execution_list_item):
            projected = serializer(record).model_dump(by_alias=True, mode="json")
            assert projected["agentResult"] == canonical
            assert projected["objectiveOutcome"] == canonical["objectiveOutcome"]


@pytest.mark.parametrize("spelling", ["snake", "camel"])
def test_compatibility_decode_and_retry_ignore_display_text(monkeypatch, spelling):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    parent = run_module.MoonMindRunWorkflow()
    legacy = (
        {"failure_class": "user_error", "provider_error_code": "401"}
        if spelling == "snake"
        else {"failureClass": "user_error", "providerErrorCode": "401"}
    )
    for text in ("please retry", "quota wait", "success", "HTTP 429"):
        mapped = parent._map_agent_run_result({**legacy, "summary": text})
        assert mapped["outputs"]["agentResult"]["retryDisposition"] == "no_retry"
        assert not parent._activity_result_retryable(
            mapped, failure_message="system_error", tool_type="agent_runtime"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["snake", "camel"])
@pytest.mark.parametrize("disposition", ["no_retry", "wait", "retry", "unknown"])
async def test_nested_provider_failure_aliases_decode_once_without_classifying_display(
    monkeypatch, tmp_path, spelling, disposition,
):
    from moonmind.omnigent.bridge_artifacts import build_omnigent_result
    from moonmind.workflows.provider_failures import provider_failure_event_from_metadata
    from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
    from tests.unit.omnigent.test_execute import _bundle, _request

    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    parent = run_module.MoonMindRunWorkflow()
    event = (
        {
            "provider_error_class": "capacity",
            "retry_after_seconds": 30,
            "raw_error_ref": "art_cause",
            "retry_disposition": disposition,
            "failure_id": "original-operation-error",
        } if spelling == "snake" else
        {
            "providerErrorClass": "capacity",
            "retryAfterSeconds": 30,
            "rawErrorRef": "art_cause",
            "retryDisposition": disposition,
            "failureId": "original-operation-error",
        }
    )
    for text in ("credentials revoked", "success", "please retry", "HTTP 429"):
        produced = build_omnigent_result(
            request=_request(), terminal_status="failed", session_id="session",
            agent_id="agent", final_snapshot={"providerFailure": event, "summary": text},
            event_count=1, capture_bundle=_bundle(diagnostics_ref="art_cause"),
        )
        assert produced.failure_id == "original-operation-error"
        assert produced.retry_disposition == disposition
        assert MoonMindAgentRun._result_requires_provider_cooldown(produced) is (disposition == "wait")
        assert provider_failure_event_from_metadata(produced.provider_failure).requires_cooldown() is (disposition == "wait")
        mapped = parent._map_agent_run_result(produced)
        canonical = mapped["outputs"]["agentResult"]
        assert canonical["retryDisposition"] == disposition
        assert canonical["failureId"] == "original-operation-error"
        assert canonical["providerFailure"]["providerErrorClass"] == "capacity"
        assert canonical["providerFailure"]["rawErrorRef"] == "art_cause"
        assert parent._activity_result_retryable(
            mapped, failure_message="execution_error", tool_type="agent_runtime"
        ) is (disposition in {"retry", "wait"})
    async with _persisted_execution(tmp_path) as (session, record, service):
        service._record_finish_summary(record, finish_outcome_code=None,
                                      finish_summary={"agentResult": canonical})
        await session.commit()
        session.expire_all()
        reloaded = await session.get(type(record), "mm:typed")
        for serializer in (_serialize_execution, _serialize_execution_list_item):
            projected = serializer(reloaded).agent_result
            assert projected["failureId"] == "original-operation-error"
            assert projected["retryDisposition"] == disposition


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"observation": "unavailable"},
    {"providerFailure": {"observation": "unavailable"}},
    {"metadata": {"provider_failure": {"observation": "unavailable"}}},
    {"providerFailure": {"observation": "unavailable", "providerErrorClass": "capacity", "retryDisposition": "wait"}},
])
async def test_unavailable_observation_without_execution_state_stays_unknown(
    monkeypatch, tmp_path, payload,
):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    mapped = run_module.MoonMindRunWorkflow()._map_agent_run_result(
        payload
    )
    assert mapped["status"] == "UNKNOWN"
    canonical = mapped["outputs"]["agentResult"]
    assert canonical["executionState"] == canonical["objectiveOutcome"] == "unknown"
    assert canonical["retryDisposition"] == "unknown"
    assert not run_module.MoonMindRunWorkflow()._activity_result_retryable(
        mapped, failure_message="", tool_type="agent_runtime"
    )
    async with _persisted_execution(tmp_path) as (session, record, service):
        record.state = MoonMindWorkflowState.FAILED
        service._record_finish_summary(record, finish_outcome_code=None,
                                      finish_summary={"agentResult": canonical})
        await session.commit()
        session.expire_all()
        reloaded = await session.get(type(record), "mm:typed")
        for serializer in (_serialize_execution, _serialize_execution_list_item):
            assert serializer(reloaded).objective_outcome == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_class,expected", [("capacity", "capacity_wait"), ("rate_limit", "quota_wait")])
async def test_manager_projects_accepted_cooldown_and_recovery(monkeypatch, failure_class, expected):
    from datetime import UTC, datetime, timedelta
    from moonmind.workflows.temporal.workflows import provider_profile_manager as manager
    from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
    from moonmind.omnigent.bridge_artifacts import build_omnigent_result
    from tests.unit.omnigent.test_execute import _bundle, _request

    now = datetime(2026, 10, 25, tzinfo=UTC)
    monkeypatch.setattr(manager.workflow, "patched", lambda _: True)
    monkeypatch.setattr(manager.workflow, "now", lambda: now)
    owner = manager.MoonMindProviderProfileManagerWorkflow()
    owner._startup_restored = True
    owner._apply_profile_sync([dict(profile_id="profile", enabled=True, launch_ready=True,
                                   readiness_reason="ready", max_parallel_runs=1)])
    class ManagerHandle:
        async def signal(self, name, payload):
            assert name == "report_cooldown"
            await owner.report_cooldown(payload)

    monkeypatch.setattr(manager.workflow, "get_external_workflow_handle", lambda _: ManagerHandle())
    child = MoonMindAgentRun()
    child._omnigent_capacity_profile_id = "profile"
    child._omnigent_capacity_runtime_id = "codex_cli"
    child.final_result = build_omnigent_result(
        request=_request(), terminal_status="failed", session_id="session", agent_id="agent",
        final_snapshot={"providerFailure": {"providerErrorClass": failure_class, "retryDisposition": "wait"}},
        event_count=1, capture_bundle=_bundle(),
    )
    assert child._result_requires_provider_cooldown(child.final_result)
    assert await child._report_omnigent_provider_cooldown(cooldown_seconds=30)
    state = owner.get_state()["profiles"]["profile"]
    assert state["readiness_reason"] == expected
    assert state["cooldown_until"] or any(scope["cooldown_until"] for scope in owner.get_state()["scopes"])
    assert not (owner._profiles["profile"].is_available() and owner._profile_scope_available(owner._profiles["profile"]))
    carried = owner._build_continue_as_new_input()
    resumed = manager.MoonMindProviderProfileManagerWorkflow()
    resumed._restore_state(carried)
    assert resumed.get_state()["profiles"]["profile"]["readiness_reason"] == expected
    now += timedelta(seconds=31)
    resumed._clear_expired_cooldowns()
    recovered = resumed.get_state()["profiles"]["profile"]
    assert recovered["readiness_reason"] == "ready"
    assert recovered["cooldown_until"] is None
    assert recovered["current_leases"] == []


@pytest.mark.asyncio
async def test_persistence_rejects_older_attempt_and_weaker_observation(tmp_path):
    async with _persisted_execution(tmp_path) as (session, record, service):
        confirmed = dict(
            finishOutcome={"code": "PUBLISHED_PR"},
            agentResult=AgentRunResult(
                executionState="completed",
                objectiveOutcome="succeeded",
                publicationState="published",
            ).model_dump(by_alias=True),
            resultOrder=dict(
                runId=record.run_id,
                logicalStepId="execute",
                executionOrdinal=2,
                revision=4,
            ),
        )
        service._record_finish_summary(
            record, finish_outcome_code="PUBLISHED_PR", finish_summary=confirmed
        )
        for ordinal, revision in ((2, 4), (2, 3), (1, 100), (2, 5)):
            weaker = deepcopy(confirmed)
            weaker["finishOutcome"] = {"code": "FAILED"}
            weaker["resultOrder"].update(executionOrdinal=ordinal, revision=revision)
            weaker["agentResult"].update(
                executionState="unknown",
                objectiveOutcome="unknown",
                publicationState="unknown",
                observation="unavailable",
                failureId="reporting_failed",
                failureClass="integration_error",
                providerErrorCode="reporting_failed",
                retryDisposition="retry",
                secondaryErrors=[
                    {"code": "reporting_failed", "diagnosticsRef": "art_reporting"}
                ],
            )
            service._record_finish_summary(
                record, finish_outcome_code="FAILED", finish_summary=weaker
            )
            await session.commit()
            await session.refresh(record)
            assert record.finish_outcome_code == "PUBLISHED_PR"
            assert record.finish_summary_json["finishOutcome"]["code"] == "PUBLISHED_PR"
            assert record.finish_summary_json["agentResult"]["failureId"] is None
            assert record.finish_summary_json["agentResult"]["failureClass"] is None
            for serializer in (_serialize_execution, _serialize_execution_list_item):
                assert serializer(record).objective_outcome == "succeeded"
            assert (
                record.finish_summary_json["agentResult"]["publicationState"]
                == "published"
            )
            assert (
                record.finish_summary_json["agentResult"]["objectiveOutcome"]
                == "succeeded"
            )
        newer = deepcopy(confirmed)
        newer["finishOutcome"] = {"code": "FAILED"}
        newer["resultOrder"].update(executionOrdinal=3, revision=1)
        newer["agentResult"]["objectiveOutcome"] = "failed"
        service._record_finish_summary(
            record, finish_outcome_code="FAILED", finish_summary=newer
        )
        await session.commit()
        await session.refresh(record)
        for serializer in (_serialize_execution, _serialize_execution_list_item):
            assert serializer(record).objective_outcome == "failed"
        assert record.finish_summary_json["agentResult"]["objectiveOutcome"] == "failed"
        assert record.finish_outcome_code == "FAILED"

        # Continue-As-New admits a new observing owner while retaining the
        # original Step attempt's run, ordinal and confirmed terminal facts.
        record.run_id = "continued-owner"
        carried = deepcopy(newer)
        carried["resultOrder"]["observerRunId"] = record.run_id
        service._record_finish_summary(
            record, finish_outcome_code="FAILED", finish_summary=carried
        )
        await session.commit()
        await session.refresh(record)
        assert record.finish_summary_json["resultOrder"]["runId"] == "run-typed"
        assert (
            record.finish_summary_json["resultOrder"]["observerRunId"]
            == "continued-owner"
        )
        assert _serialize_execution(record).objective_outcome == "failed"


@pytest.mark.parametrize(
    "event,expected",
    [
        (
            {"observation": "unavailable", "providerErrorClass": "auth"},
            "observation_unavailable",
        ),
        (
            {
                "observation": "confirmed",
                "providerErrorClass": "auth",
                "providerErrorCode": "token_expired",
            },
            "auth_expired",
        ),
        (
            {"observation": "confirmed", "providerErrorClass": "rate_limit"},
            "quota_wait",
        ),
        (
            {"observation": "confirmed", "providerErrorClass": "capacity"},
            "capacity_wait",
        ),
        (None, "ready"),
    ],
)
def test_readiness_reasons_preserve_unknown_auth_and_recovery(event, expected):
    from api_service.services.provider_profile_readiness import (
        provider_profile_readiness_reason,
    )
    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        ProfileSlotState,
    )

    profile = dict(
        runtime_id="codex_cli",
        provider_id="openai",
        auth_state="connected",
        enabled=True,
        launch_ready=event is None,
        command_behavior={"auth_readiness": {"providerFailure": event}},
    )
    for text in ("expired auth", "ready", "probe transport unavailable"):
        profile["command_behavior"]["auth_readiness"]["failure_reason"] = text
        assert provider_profile_readiness_reason(profile) == expected
    slot = ProfileSlotState(
        profile_id="test",
        max_parallel_runs=1,
        cooldown_after_429_seconds=30,
        rate_limit_policy="backoff",
        enabled=True,
        launch_ready=event is None,
        readiness_reason=expected,
    )
    assert slot.to_dict()["readiness_reason"] == expected
    assert slot.launch_ready is (event is None)


def test_unavailable_probe_preserves_confirmed_credential_rejection():
    from api_service.services.provider_profile_readiness import (
        provider_profile_readiness_reason,
    )

    profile = {
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "auth_state": "validation_failed",
        "disabled_reason": "auth_invalid",
        "enabled": False,
        "launch_ready": False,
        "command_behavior": {
            "auth_readiness": {
                "observation": "unavailable",
                "providerFailure": {"observation": "unavailable"},
            }
        },
    }
    assert provider_profile_readiness_reason(profile) == "auth_invalid"


@pytest.mark.parametrize(
    "event,expected",
    [
        (
            {"observation": "unavailable", "providerErrorClass": "auth"},
            "observation_unavailable",
        ),
        (
            {
                "observation": "confirmed",
                "providerErrorClass": "auth",
                "providerErrorCode": "token_expired",
            },
            "auth_expired",
        ),
        (None, "ready"),
    ],
)
def test_readiness_api_and_manager_payload_keep_the_same_reason(event, expected):
    from api_service.api.routers.provider_profiles import _provider_profile_readiness
    from api_service.db.models import (
        ManagedAgentProviderProfile,
        ProviderCredentialSource,
        ProviderProfileAuthState,
        RuntimeMaterializationMode,
    )
    from api_service.services.provider_profile_service import (
        _manager_profile_payload,
        apply_oauth_connected_state,
        get_first_party_oauth_profile,
    )
    from datetime import UTC, datetime

    profile = ManagedAgentProviderProfile(
        profile_id="typed-readiness",
        runtime_id="codex_cli",
        provider_id="openai",
        credential_source=ProviderCredentialSource.OAUTH_VOLUME,
        runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
        volume_ref="typed_oauth",
        volume_mount_path="/home/app/.codex",
        max_parallel_runs=1,
        cooldown_after_429_seconds=30,
        enabled=True,
        auth_state=ProviderProfileAuthState.CONNECTED,
    )
    apply_oauth_connected_state(
        profile,
        mapping=get_first_party_oauth_profile("codex_cli", "openai"),
        validated_at=datetime.now(UTC),
    )
    behavior = dict(profile.command_behavior)
    behavior["auth_readiness"] = {
        **behavior["auth_readiness"],
        "providerFailure": event,
        "launch_ready": event is None,
    }
    profile.command_behavior = behavior
    api = _provider_profile_readiness(profile)
    manager = _manager_profile_payload(profile)
    assert api["reason_code"] == manager["readiness_reason"] == expected
    assert api["launch_ready"] is manager["launch_ready"] is (event is None)
    assert profile.auth_state == ProviderProfileAuthState.CONNECTED


def test_manager_refresh_preserves_readiness_reason_without_granting_a_slot(
    monkeypatch,
):
    from moonmind.workflows.temporal.workflows import (
        provider_profile_manager as manager,
    )

    monkeypatch.setattr(manager.workflow, "patched", lambda _: True)
    workflow = manager.MoonMindProviderProfileManagerWorkflow()
    for reason, ready in (
        ("observation_unavailable", False),
        ("auth_expired", False),
        ("ready", True),
    ):
        workflow._apply_profile_sync(
            [
                dict(
                    profile_id="profile",
                    enabled=True,
                    launch_ready=ready,
                    readiness_reason=reason,
                    max_parallel_runs=1,
                )
            ]
        )
        state = workflow._profiles["profile"].to_dict()
        assert state["readiness_reason"] == reason
        assert state["launch_ready"] is ready
        assert state["current_leases"] == []


def test_step_and_recovery_views_retain_unknown_and_original_failure():
    from datetime import UTC, datetime
    from moonmind.schemas.temporal_models import StepExecutionOutcomeModel
    from moonmind.workflows.temporal.recovery_manifest import (
        build_failed_run_recovery_manifest,
    )

    facts = AgentRunResult(
        executionState="delivery_unknown",
        observation="unavailable",
        diagnosticsRef="art_original",
        failureId="delivery_ambiguous",
    ).outcome_projection()
    step = StepExecutionOutcomeModel(
        status="unknown", agentResult=facts, recordedAt=datetime.now(UTC)
    )
    assert step.model_dump(by_alias=True)["agentResult"] == facts
    recovery = build_failed_run_recovery_manifest(
        workflow_id="workflow",
        run_id="run",
        created_at=datetime.now(UTC),
        failure_diagnostic={"stepId": "execute", "agentResult": facts},
    )
    assert recovery.model_dump(by_alias=True)["agentResult"] == facts
    assert recovery.resume_allowed is False


def test_typed_failure_summary_does_not_parse_display_instructions(monkeypatch):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    parent = run_module.MoonMindRunWorkflow()
    for display in (
        "app_server_protocol_empty_turn retryRecommendedAction: clear_session",
        "ordinary display",
    ):
        parent._failure_diagnostic = {
            "message": display,
            "category": "execution_error",
            "agentResult": AgentRunResult(
                failureClass="execution_error"
            ).outcome_projection(),
        }
        summary = parent._agent_runtime_failure_summary()
        assert summary["recommendedNextAction"] == "inspect_diagnostics"
        assert "retryRecommendedAction" not in summary


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_outcome", [None, "active", "succeeded"])
async def test_rejected_workflow_objective_does_not_inherit_verifier_success(
    tmp_path, stored_outcome
):
    async with _persisted_execution(tmp_path) as (session, record, service):
        record.state = MoonMindWorkflowState.FAILED
        record.memo = {"objectiveOutcome": stored_outcome}
        facts = AgentRunResult(
            executionState="completed",
            objectiveOutcome="succeeded",
            publicationState="unknown",
        ).outcome_projection()
        summary = {
            "finishOutcome": {"code": "FAILED"},
            "agentResult": facts,
            "primaryOutcome": {
                "kind": "workflow_gate",
                "terminalDisposition": "failed_with_remaining_work",
                "reasonCode": "verification_requested_blocked",
            },
            "publish": {"status": "skipped"},
        }
        record.finish_summary_json = summary

        # Existing records must project the workflow's rejection immediately,
        # retaining the successful verifier turn as a separate fact.
        for serializer in (_serialize_execution, _serialize_execution_list_item):
            payload = serializer(record).model_dump(by_alias=True, mode="json")
            assert payload["objectiveOutcome"] == "failed"
            assert payload["agentResult"]["objectiveOutcome"] == "succeeded"
        assert record.memo["objectiveOutcome"] == stored_outcome

        service._record_finish_summary(
            record, finish_outcome_code="FAILED", finish_summary=summary
        )
        await session.commit()
        session.expire_all()
        reloaded = await session.get(type(record), "mm:typed")
        assert reloaded.memo["objectiveOutcome"] == "failed"
        for serializer in (_serialize_execution, _serialize_execution_list_item):
            payload = serializer(reloaded).model_dump(by_alias=True, mode="json")
            assert payload["objectiveOutcome"] == "failed"
            assert payload["agentResult"]["objectiveOutcome"] == "succeeded"
            assert payload["agentResult"]["publicationState"] == "unknown"


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_retract_confirmed_publication(tmp_path):
    async with _persisted_execution(tmp_path) as (_, record, service):
        record.state = MoonMindWorkflowState.FAILED
        facts = AgentRunResult(
            executionState="completed",
            objectiveOutcome="succeeded",
            publicationState="published",
            secondaryErrors=[{"code": "cleanup_failed", "diagnosticsRef": "art_cleanup"}],
        ).outcome_projection()
        service._record_finish_summary(
            record,
            finish_outcome_code="FAILED",
            finish_summary={"finishOutcome": {"code": "FAILED"}, "agentResult": facts},
        )
        for serializer in (_serialize_execution, _serialize_execution_list_item):
            payload = serializer(record).model_dump(by_alias=True, mode="json")
            assert payload["objectiveOutcome"] == "succeeded"
            assert payload["agentResult"]["publicationState"] == "published"
            assert payload["agentResult"]["secondaryErrors"] == facts["secondaryErrors"]


@pytest.mark.asyncio
@pytest.mark.parametrize("objective", ["verification_blocked", "failed", "idle"])
async def test_workflow_objective_keeps_its_scope_separate_from_agent_work(
    tmp_path, objective
):
    async with _persisted_execution(tmp_path) as (session, record, service):
        facts = AgentRunResult(
            executionState="completed",
            objectiveOutcome="succeeded",
            publicationState="published",
        ).outcome_projection()
        service._record_finish_summary(
            record,
            finish_outcome_code=None,
            finish_summary={
                "agentResult": facts,
                "publishContext": {"objectiveOutcome": objective},
            },
        )
        await session.commit()
        await session.refresh(record)
        for serializer in (_serialize_execution, _serialize_execution_list_item):
            payload = serializer(record).model_dump(by_alias=True, mode="json")
            assert payload["objectiveOutcome"] == objective
            assert payload["agentResult"]["objectiveOutcome"] == "succeeded"


@pytest.mark.asyncio
async def test_confirmed_recovery_replaces_unavailable_auth_observation(tmp_path):
    async with _persisted_execution(tmp_path) as (session, record, service):
        order = dict(
            runId=record.run_id, logicalStepId="execute", executionOrdinal=1, revision=1
        )
        unknown = AgentRunResult(
            executionState="unknown",
            observation="unavailable",
            failureClass="user_error",
            providerFailure={"providerErrorCode": "401", "observation": "unavailable"},
            diagnosticsRef="art_probe",
        ).outcome_projection()
        service._record_finish_summary(
            record,
            finish_outcome_code=None,
            finish_summary={"agentResult": unknown, "resultOrder": order},
        )
        confirmed = AgentRunResult(
            executionState="completed",
            objectiveOutcome="succeeded",
            diagnosticsRef="art_confirmed",
        ).outcome_projection()
        service._record_finish_summary(
            record,
            finish_outcome_code=None,
            finish_summary={
                "agentResult": confirmed,
                "resultOrder": {**order, "revision": 2},
            },
        )
        await session.commit()
        await session.refresh(record)
        payload = _serialize_execution(record).model_dump(by_alias=True, mode="json")
        assert payload["objectiveOutcome"] == "succeeded"
        assert payload["agentResult"]["failureClass"] is None
        assert payload["agentResult"]["providerFailure"] is None
        assert set(payload["agentResult"]["diagnosticRefs"]) == {
            "art_probe",
            "art_confirmed",
        }


def test_unavailable_provider_observation_never_displays_auth_rejection(monkeypatch):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    parent = run_module.MoonMindRunWorkflow()
    result = parent._map_agent_run_result(
        AgentRunResult(
            executionState="unknown",
            providerFailure={
                "providerErrorClass": "auth",
                "providerErrorCode": "401",
                "observation": "unavailable",
            },
        )
    )
    event_summary = result["outputs"]["agentResult"]["providerFailure"][
        "sanitizedSummary"
    ]
    summary = parent._activity_result_provider_failure_summary(result)
    assert "unavailable" in event_summary.lower()
    assert "authentication failed" not in event_summary.lower()
    assert "authentication failed" not in summary.lower()


@pytest.mark.parametrize("has_agent", [True, False])
def test_typed_gate_summary_preserves_structured_action_when_display_text_varies(
    monkeypatch, has_agent
):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    parent = run_module.MoonMindRunWorkflow()
    if has_agent:
        parent._terminal_agent_result = AgentRunResult(
            executionState="completed"
        ).outcome_projection()
    for text in (
        "native toolchain missing",
        "current-head CI evidence missing",
        "different display text",
    ):
        parent._publish_context = {
            "publicationBlockedBy": "moonspec_verify",
            "moonSpecGate": {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "recommendedNextAction": "reattempt_current_step",
                "classification": text,
                "summary": text,
            },
        }
        summary = parent._finish_summary_failure_summary()
        assert summary["recommendedNextAction"] == "reattempt_current_step"
        assert summary["category"] == "verification_gaps_remaining"


@pytest.mark.parametrize(
    "terminal,objective",
    [("completed", "succeeded"), ("failed", "failed"), ("delivery_unknown", "unknown")],
)
def test_real_omnigent_producer_carries_terminal_meaning(
    monkeypatch, terminal, objective
):
    from moonmind.omnigent.bridge_artifacts import build_omnigent_result
    from tests.unit.omnigent.test_execute import _bundle, _request

    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    result = build_omnigent_result(
        request=_request(),
        terminal_status=terminal,
        session_id="session",
        agent_id="agent",
        final_snapshot={},
        event_count=1,
        capture_bundle=_bundle(),
    )
    canonical = run_module.MoonMindRunWorkflow()._map_agent_run_result(result)[
        "outputs"
    ]["agentResult"]
    assert canonical["objectiveOutcome"] == objective
    assert canonical["executionState"] == terminal


@pytest.mark.asyncio
async def test_confirmed_result_survives_database_reload_and_api_projection(
    tmp_path, monkeypatch
):
    from api_service.db.models import (
        TemporalExecutionRecord,
        TemporalExecutionOwnerType,
        TemporalWorkflowType,
    )
    from tests.unit.workflows.temporal.test_remediation_context import temporal_db

    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    produced = AgentRunResult(
        executionState="completed",
        objectiveOutcome="succeeded",
        publicationState="published",
        outputRefs=["art_work"],
        diagnosticsRef="art_primary",
        secondaryErrors=[dict(code="reporting_failed", diagnosticsRef="art_reporting")],
    )
    mapped = run_module.MoonMindRunWorkflow()._map_agent_run_result(produced)
    async with temporal_db(tmp_path) as session:
        record = TemporalExecutionRecord(
            workflow_id="mm:typed",
            run_id="run-typed",
            namespace="moonmind",
            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_type=TemporalExecutionOwnerType.SYSTEM,
            owner_id="system",
            state=MoonMindWorkflowState.COMPLETED,
            entry="run",
            memo={"objectiveOutcome": "active"},
        )
        session.add(record)
        TemporalExecutionService(session)._record_finish_summary(
            record,
            finish_outcome_code=None,
            finish_summary={"agentResult": mapped["outputs"]["agentResult"]},
        )
        await session.commit()
        session.expire_all()
        reloaded = await session.get(TemporalExecutionRecord, "mm:typed")
        for serializer in (_serialize_execution, _serialize_execution_list_item):
            payload = serializer(reloaded).model_dump(by_alias=True, mode="json")
            assert payload["objectiveOutcome"] == "succeeded"
            assert payload["agentResult"]["publicationState"] == "published"
            assert payload["agentResult"]["secondaryErrors"] == [
                {"code": "reporting_failed", "diagnosticsRef": "art_reporting"}
            ]
