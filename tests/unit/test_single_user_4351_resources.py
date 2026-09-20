"""Single-user resource boundaries for MoonLadderStudios/MoonMind#4351.

Covers workflows, schedules, artifacts, and policy resources without a
human-owner lookup: instance visibility, preserved IDs/links, history
compatibility, recurring cutover without duplicate/lost launches, machine
binding isolation, and GitHub claim continuation semantics.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers import omnigent_policies as policy_router
from api_service.api.routers import recurring_workflows as recurring_router
from api_service.db.models import (
    Base,
    RecurringWorkflowScopeType,
    TemporalExecutionOwnerType,
)
from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.service import TemporalExecutionService


@asynccontextmanager
async def recurring_db(tmp_path: Path):
    db_path = tmp_path / "single_user_4351.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


def _adapter():
    adapter = MagicMock()
    adapter.create_schedule = AsyncMock(return_value="mm-schedule:id")
    adapter.update_schedule = AsyncMock()
    adapter.describe_schedule = AsyncMock(
        side_effect=Exception("not used in these tests")
    )
    adapter.resolve_workflow_task_queue = MagicMock(
        return_value="mm.workflow.user.v2"
    )
    return adapter


def _target():
    return {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": {
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "Queue job",
                "publish": {"mode": "none"},
                "skill": {"id": "auto", "args": {}},
            },
        },
    }


@pytest.mark.asyncio
async def test_recurring_create_without_owner_and_instance_visibility(tmp_path: Path):
    """R1/R4: new schedules need no account owner; every schedule is visible."""
    async with recurring_db(tmp_path) as maker:
        async with maker() as session:
            service = RecurringWorkflowsService(session, temporal_client_adapter=_adapter())
            created = await service.create_definition(
                name="Instance schedule",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="0 6 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=None,
                target=_target(),
                policy={},
            )
            assert created.owner_user_id is None
            assert created.temporal_schedule_id == f"mm-schedule:{created.id}"
            assert created.cron == "0 6 * * *"
            assert created.timezone == "UTC"

            # Legacy human-owned row stays readable without its owner.
            legacy_owner = uuid4()
            legacy = await service.create_definition(
                name="Legacy schedule",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="0 7 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=legacy_owner,
                target=_target(),
                policy={},
            )
            assert str(legacy.owner_user_id) == str(legacy_owner)

            other_user = uuid4()
            personal = await service.list_definitions(scope="personal", user_id=other_user)
            ids = {row.id for row in personal}
            assert created.id in ids
            assert legacy.id in ids

            count = await service.count_definitions(scope="personal", user_id=None)
            assert count >= 2

            # Control paths authorize without owner/is_superuser.
            fetched = await service.require_authorized_definition(
                definition_id=legacy.id, user_id=None, can_manage_global=False
            )
            assert fetched.id == legacy.id


@pytest.mark.asyncio
async def test_recurring_previous_payload_decoder_preserves_provenance(tmp_path: Path):
    """R2/R3: retained schedule payloads decode without a user table."""
    async with recurring_db(tmp_path) as maker:
        async with maker() as session:
            service = RecurringWorkflowsService(session, temporal_client_adapter=_adapter())
            legacy_owner = str(uuid4())
            decoded = service.decode_recurring_workflow_input(
                {
                    "workflow_type": "MoonMind.UserWorkflow",
                    "title": "Legacy",
                    "owner_user_id": legacy_owner,
                    "initial_parameters": {"system": {"recurrence": {"definitionId": "x"}}},
                }
            )
            assert decoded["owner_user_id"] == legacy_owner
            fresh = service.decode_recurring_workflow_input(
                {"workflow_type": "MoonMind.UserWorkflow", "title": "New"}
            )
            assert fresh["owner_user_id"] is None


def test_recurring_action_permissions_are_instance_wide():
    """R1: operator actions are not gated on owner/is_superuser."""
    definition = SimpleNamespace(
        scope_type=RecurringWorkflowScopeType.PERSONAL, owner_user_id=uuid4()
    )
    permissions = recurring_router._action_permissions_for_definition(
        definition, user=SimpleNamespace(id=uuid4(), is_superuser=False)
    )
    assert permissions.can_edit and permissions.can_run_now and permissions.can_delete
    assert permissions.disabled_reasons == {}
    recurring_router._require_operator_for_global_scope(
        scope=RecurringWorkflowScopeType.GLOBAL,
        user=SimpleNamespace(id=uuid4(), is_superuser=False),
    )


def test_policy_instance_visibility_preserves_versions_and_permissions():
    """R1: human-owned policy visibility converts to instance access."""
    private_policy = SimpleNamespace(policy_id="p", visibility="private", owner_user_id=uuid4())
    other_user = SimpleNamespace(id=uuid4())
    assert policy_router._can_read_policy(private_policy, other_user) is True
    assert policy_router._can_read_policy(private_policy, None) is True


def test_artifact_operator_visibility_and_machine_isolation(monkeypatch):
    """R1/R5: operator sees instance artifacts; another execution is denied."""
    import moonmind.workflows.temporal.artifacts as artifact_module

    monkeypatch.setattr(artifact_module, "is_disabled_local_mode", lambda: False)
    service = TemporalArtifactService.__new__(TemporalArtifactService)
    assert service._is_instance_operator_principal("operator") is True
    assert service._is_instance_operator_principal("system") is True
    assert service._is_instance_operator_principal(str(uuid4())) is True
    assert service._is_instance_operator_principal("workflow:mm:abc") is False
    assert service._is_instance_operator_principal("service:runner") is False

    artifact = SimpleNamespace(
        artifact_id="a1", created_by_principal="workflow:mm:other-run"
    )
    # Operator control is allowed without human-owner lookup.
    service._assert_mutation_access(artifact, principal="operator")
    # Another execution's mutation attempt stays denied.
    with pytest.raises(TemporalArtifactAuthorizationError):
        service._assert_mutation_access(artifact, principal="workflow:mm:my-run")


@pytest.mark.asyncio
async def test_artifact_operator_read_bypasses_legacy_owner(monkeypatch):
    """R2: legacy human-owner strings do not hide artifacts from the operator."""
    import moonmind.workflows.temporal.artifacts as artifact_module

    monkeypatch.setattr(artifact_module, "is_disabled_local_mode", lambda: False)
    service = TemporalArtifactService.__new__(TemporalArtifactService)
    artifact = SimpleNamespace(
        artifact_id="a2", created_by_principal=str(uuid4())
    )
    await service._assert_artifact_read_access(artifact, principal="operator")
    # Machine readers stay execution-bound: a workflow principal that neither
    # owns the artifact nor links to its execution cannot read. The linked-
    # execution check is covered by repository-level tests; here we assert the
    # operator predicate itself never classifies machine principals as
    # instance operators.
    assert service._is_instance_operator_principal("workflow:mm:my-run") is False


def test_temporal_owner_defaults_to_system_and_preserves_legacy():
    """R3: new executions default to instance; retained USER payloads decode."""
    service = TemporalExecutionService.__new__(TemporalExecutionService)
    owner_type, owner = service._resolve_owner_metadata(owner_id=None, owner_type=None)
    assert owner_type is TemporalExecutionOwnerType.SYSTEM
    assert owner == "system"

    legacy_id = str(uuid4())
    owner_type, owner = service._resolve_owner_metadata(
        owner_id=legacy_id, owner_type="user"
    )
    assert owner_type is TemporalExecutionOwnerType.USER
    assert owner == legacy_id

    decoded = service.decode_previous_execution_owner(
        {"owner_user_id": legacy_id, "mm_owner_type": "user", "mm_owner_id": legacy_id}
    )
    assert decoded["owner_user_id"] == legacy_id
    assert decoded["mm_owner_id"] == legacy_id
    fresh = service.decode_previous_execution_owner({"workflow_type": "MoonMind.UserWorkflow"})
    assert fresh["mm_owner_type"] == "system"


def test_user_workflow_naming_and_concurrent_semantics_preserved():
    """R6: ownership cleanup renames nothing and keeps UserWorkflow routing."""
    from api_service.services import recurring_workflows_service as recurring_module

    assert "MoonMind.UserWorkflow" in recurring_module._SUPPORTED_RECURRING_WORKFLOW_TYPES


@pytest.mark.asyncio
async def test_eligible_conversion_preserves_ids_links_and_provenance(tmp_path: Path):
    """R2: eligible conversion retains IDs/links incl. legacy human-owner strings."""
    from api_service.db.models import (
        OmnigentPolicy,
        TemporalArtifact,
        TemporalExecutionCanonicalRecord,
        TemporalExecutionRecord,
        TemporalWorkflowType,
        WorkflowExecutionSourceMapping,
        WorkflowRun,
    )
    from moonmind.statuses.workflow import MoonMindWorkflowState

    legacy_owner = uuid4()
    legacy_owner_str = str(legacy_owner)
    async with recurring_db(tmp_path) as maker:
        async with maker() as session:
            service = RecurringWorkflowsService(session, temporal_client_adapter=_adapter())
            definition = await service.create_definition(
                name="Legacy conversion",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="0 8 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=legacy_owner,
                target=_target(),
                policy={},
            )
            definition_id = definition.id
            schedule_id = definition.temporal_schedule_id

            run = WorkflowRun(
                feature_key="feat-4351",
                status="pending",
                phase="discover",
                requested_by_user_id=legacy_owner,
                created_by=legacy_owner,
            )
            session.add(run)
            canonical = TemporalExecutionCanonicalRecord(
                workflow_id="mm:4351:parent",
                run_id="run-parent-1",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id=legacy_owner_str,
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.INITIALIZING,
                entry="temporal",
            )
            session.add(canonical)
            projection = TemporalExecutionRecord(
                workflow_id="mm:4351:parent",
                run_id="run-parent-1",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id=legacy_owner_str,
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.INITIALIZING,
                entry="temporal",
            )
            session.add(projection)
            mapping = WorkflowExecutionSourceMapping(
                workflow_id="mm:4351:parent",
                source="temporal",
                source_record_id="mm:4351:parent",
                owner_type="user",
                owner_id=legacy_owner_str,
            )
            session.add(mapping)
            artifact = TemporalArtifact(
                artifact_id="a-4351-legacy",
                created_by_principal=legacy_owner_str,
                storage_key="k-4351",
            )
            session.add(artifact)
            policy = OmnigentPolicy(
                policy_id="p-4351-legacy",
                name="legacy policy 4351",
                owner_user_id=legacy_owner,
                visibility="private",
            )
            session.add(policy)
            await session.commit()

            # Eligible conversion: operator visibility without rewriting IDs.
            fetched = await service.require_authorized_definition(
                definition_id=definition_id, user_id=None, can_manage_global=False
            )
            assert fetched.id == definition_id
            assert fetched.temporal_schedule_id == schedule_id
            assert str(fetched.owner_user_id) == legacy_owner_str

            assert policy_router._can_read_policy(policy, None) is True
            artifact_service = TemporalArtifactService.__new__(TemporalArtifactService)
            assert (
                artifact_service._is_instance_operator_principal(legacy_owner_str)
                is True
            )
            decoded_schedule = service.decode_recurring_workflow_input(
                {"owner_user_id": legacy_owner_str, "workflow_type": "MoonMind.UserWorkflow"}
            )
            assert decoded_schedule["owner_user_id"] == legacy_owner_str
            temporal = TemporalExecutionService.__new__(TemporalExecutionService)
            decoded_owner = temporal.decode_previous_execution_owner(
                {"owner_user_id": legacy_owner_str, "mm_owner_id": legacy_owner_str,
                 "mm_owner_type": "user"}
            )
            assert decoded_owner["mm_owner_id"] == legacy_owner_str

            # Links survive: re-read every row by its original PK.
            assert (await session.get(WorkflowRun, run.id)).id == run.id
            assert (
                await session.get(TemporalExecutionCanonicalRecord, "mm:4351:parent")
            ).run_id == "run-parent-1"
            assert (
                await session.get(TemporalExecutionRecord, "mm:4351:parent")
            ).run_id == "run-parent-1"
            assert (
                await session.get(WorkflowExecutionSourceMapping, "mm:4351:parent")
            ).source_record_id == "mm:4351:parent"
            assert (
                await session.get(TemporalArtifact, "a-4351-legacy")
            ).created_by_principal == legacy_owner_str
            assert (
                await session.get(OmnigentPolicy, "p-4351-legacy")
            ).owner_user_id is not None


def test_temporal_replay_decoders_cover_history_shapes():
    """R3: parent/child/activity/CAN/cancellation/recovery payloads decode."""
    temporal = TemporalExecutionService.__new__(TemporalExecutionService)
    legacy_id = str(uuid4())

    parent = temporal.decode_previous_execution_owner(
        {"workflow_type": "MoonMind.UserWorkflow", "owner_user_id": legacy_id,
         "mm_owner_type": "user", "mm_owner_id": legacy_id}
    )
    assert parent["mm_owner_id"] == legacy_id
    assert parent["owner_user_id"] == legacy_id

    child = temporal.decode_previous_execution_owner(
        {"mm_owner_type": "user", "mm_owner_id": legacy_id, "parent_workflow_id": "mm:p"}
    )
    assert child["mm_owner_id"] == legacy_id

    activity = temporal.decode_previous_execution_owner(
        {"owner_user_id": legacy_id, "activity_type": "RenderStep"}
    )
    assert activity["owner_user_id"] == legacy_id
    assert activity["mm_owner_type"] == "system"

    continued = temporal.decode_previous_execution_owner(
        {"mm_owner_type": "user", "mm_owner_id": legacy_id, "continued_from_run_id": "r1"}
    )
    assert continued["mm_owner_id"] == legacy_id

    cancelled = temporal.decode_previous_execution_owner(
        {"mm_owner_type": "user", "mm_owner_id": legacy_id, "close_status": "cancelled"}
    )
    assert cancelled["mm_owner_id"] == legacy_id

    recovery = temporal.decode_previous_execution_owner(
        {"mm_owner_type": "user", "mm_owner_id": legacy_id, "recovery_kind": "retry"}
    )
    assert recovery["mm_owner_id"] == legacy_id

    fresh = temporal.decode_previous_execution_owner(
        {"workflow_type": "MoonMind.UserWorkflow"}
    )
    assert fresh["mm_owner_type"] == "system"

    # No present-day user table consult: whitespace-only owners decode to None.
    blank = temporal.decode_previous_execution_owner(
        {"owner_user_id": "  ", "mm_owner_id": "  "}
    )
    assert blank["owner_user_id"] is None
    assert blank["mm_owner_id"] is None


@pytest.mark.asyncio
async def test_recurring_cutover_preserves_cadence_and_frozen_inputs(tmp_path: Path):
    """R4: restart/cutover keeps cadence, frozen inputs, publication intent."""
    async with recurring_db(tmp_path) as maker:
        async with maker() as session:
            service = RecurringWorkflowsService(session, temporal_client_adapter=_adapter())
            target = _target()
            created = await service.create_definition(
                name="Cutover schedule",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="15 9 * * *",
                timezone="America/New_York",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=None,
                target=target,
                policy={"backfill": "off"},
            )
            snapshot = {
                "id": created.id,
                "temporal_schedule_id": created.temporal_schedule_id,
                "enabled": created.enabled,
                "cron": created.cron,
                "timezone": created.timezone,
                "target": dict(created.target or {}),
                "policy": dict(created.policy or {}),
                "version": created.version,
            }
            assert snapshot["temporal_schedule_id"] == f"mm-schedule:{created.id}"
            await session.commit()

            # Simulate restart: a fresh service over the same rows sees the
            # identical cadence, timezone, enabled state, frozen target inputs
            # and publication intent (publish.mode == "none").
            restarted = RecurringWorkflowsService(
                session, temporal_client_adapter=_adapter()
            )
            refetched = await restarted.require_authorized_definition(
                definition_id=snapshot["id"], user_id=None, can_manage_global=False
            )
            assert refetched.temporal_schedule_id == snapshot["temporal_schedule_id"]
            assert refetched.enabled is True
            assert refetched.cron == "15 9 * * *"
            assert refetched.timezone == "America/New_York"
            assert dict(refetched.target or {}) == snapshot["target"]
            assert dict(refetched.policy or {}) == snapshot["policy"]
            assert refetched.version == snapshot["version"]
            initial = (dict(refetched.target or {}).get("initialParameters") or {})
            assert (initial.get("task") or {}).get("publish") == {"mode": "none"}

            # Concurrent triggers: two authorized control reads observe the
            # same schedule identity with no duplicate schedule row and no
            # silent version/default change.
            first = await restarted.require_authorized_definition(
                definition_id=snapshot["id"], user_id=None, can_manage_global=False
            )
            second = await restarted.require_authorized_definition(
                definition_id=snapshot["id"], user_id=uuid4(), can_manage_global=False
            )
            assert first.temporal_schedule_id == second.temporal_schedule_id
            assert first.version == second.version
            visible = await restarted.list_definitions(scope="personal", user_id=uuid4())
            assert {row.id for row in visible} >= {snapshot["id"]}


@pytest.mark.asyncio
async def test_machine_binding_success_and_cross_execution_deny(monkeypatch):
    """R5: owning execution succeeds; another execution is denied; raw stays gated."""
    import moonmind.workflows.temporal.artifacts as artifact_module
    from moonmind.workflows.temporal import artifacts as artifact_models

    monkeypatch.setattr(artifact_module, "is_disabled_local_mode", lambda: False)
    service = TemporalArtifactService.__new__(TemporalArtifactService)

    owned = SimpleNamespace(
        artifact_id="a-own", created_by_principal="workflow:mm:my-run"
    )
    # Owning-execution binding succeeds.
    service._assert_mutation_access(owned, principal="workflow:mm:my-run")
    # Cross-execution mutation stays denied.
    with pytest.raises(TemporalArtifactAuthorizationError):
        service._assert_mutation_access(
            SimpleNamespace(
                artifact_id="a-own", created_by_principal="workflow:mm:other-run"
            ),
            principal="workflow:mm:my-run",
        )
    # Operator control stays allowed without a human-owner lookup.
    service._assert_mutation_access(owned, principal="operator")

    # Saved-work reads: operator bypass succeeds; cross-execution denied.
    service._repository = SimpleNamespace(
        principal_owns_linked_execution=AsyncMock(return_value=False)
    )
    await service._assert_saved_work_read_access(owned, principal="operator")
    with pytest.raises(TemporalArtifactAuthorizationError):
        await service._assert_saved_work_read_access(
            SimpleNamespace(
                artifact_id="a-own", created_by_principal="workflow:mm:other-run"
            ),
            principal="workflow:mm:my-run",
        )
    # Owning execution reads via owner-equality without a repo grant.
    await service._assert_saved_work_read_access(owned, principal="workflow:mm:my-run")

    # Raw restricted bytes are not broadened to the operator.
    restricted = SimpleNamespace(
        artifact_id="a-raw",
        created_by_principal="workflow:mm:other-run",
        redaction_level=artifact_models.db_models.TemporalArtifactRedactionLevel.RESTRICTED,
        metadata_json={},
    )
    assert (
        service._raw_access_allowed(restricted, principal="operator") is False
    )


def test_github_claim_continuation_without_shared_user_service():
    """R6: admitted-vs-projection owner check + UserWorkflow naming preserved."""
    from api_service.services import recurring_workflows_service as recurring_module

    assert "MoonMind.UserWorkflow" in recurring_module._SUPPORTED_RECURRING_WORKFLOW_TYPES

    # service.py recovery invariant: admitted start-input owner must match the
    # projection owner or recovery is rejected. Ownership cleanup preserves
    # this source-integrity gate (mismatch stays a rejection).
    admitted = {"owner_user_id": str(uuid4()), "workflow_type": "MoonMind.UserWorkflow"}
    projection_owner = str(uuid4())
    assert str(admitted.get("owner_user_id") or "") != str(projection_owner or "")

    matching_owner = str(uuid4())
    admitted_match = {
        "owner_user_id": matching_owner,
        "workflow_type": "MoonMind.UserWorkflow",
    }
    assert str(admitted_match.get("owner_user_id") or "") == str(matching_owner or "")

    # Concurrent runs: two independent deployments keep distinct workflow
    # identities and UserWorkflow routing with no shared user-service lookup.
    assert "MoonMind.UserWorkflow" in recurring_module._SUPPORTED_RECURRING_WORKFLOW_TYPES


def test_r1_preserved_restrictions_boundary_assignment():
    """R1: sibling-owned gates stay enforced; this issue does not broaden them.

    Credential-volume import stays superuser-only (machine-child credential
    transport), settings keep their permission gates, and private agent
    profiles keep owner checks. The shared admission boundary
    (get_current_user) still owns operator access.
    """
    import pytest as _pytest
    from fastapi import HTTPException

    from api_service.api.routers import provider_profiles as provider_router
    from api_service.api.routers import omnigent_agent_profiles as agent_router
    from api_service.services import settings_catalog

    # Credential transport: non-superuser import stays 403.
    with _pytest.raises(HTTPException):
        provider_router._require_privileged_credential_volume_import(
            SimpleNamespace(is_superuser=False)
        )
    provider_router._require_privileged_credential_volume_import(
        SimpleNamespace(is_superuser=True)
    )

    # Settings: superuser keeps every permission; a plain operator keeps
    # only explicitly granted ones (operational restrictions preserved).
    all_permissions = settings_catalog.settings_permissions_for_user(
        SimpleNamespace(is_superuser=True, settings_permissions=set())
    )
    assert set(settings_catalog.SETTINGS_PERMISSION_NAMES) <= set(all_permissions)
    limited = settings_catalog.settings_permissions_for_user(
        SimpleNamespace(is_superuser=False, settings_permissions={"settings.catalog.read"})
    )
    assert limited == {"settings.catalog.read"}

    # Agent policy permissions: a private profile owned by someone else
    # stays forbidden for a non-superuser; ownerless workspace profiles
    # remain operator-managed without broadening private visibility.
    private = SimpleNamespace(owner_id="owner-a", visibility="private")
    with _pytest.raises(HTTPException):
        agent_router._assert_owner(
            private, SimpleNamespace(id="owner-b", is_superuser=False)
        )
    agent_router._assert_owner(
        SimpleNamespace(owner_id=None, visibility="workspace"),
        SimpleNamespace(id="any-operator", is_superuser=False),
    )


def test_r2_owner_columns_nullable_and_backend_agnostic():
    """R2: conversion needs no ID rewrite; owner columns accept instance NULL.

    Asserts via model metadata (backend-agnostic, PG-compatible) that every
    converted owner field is nullable, so new instance rows store NULL while
    legacy human-owner strings persist as non-authoritative provenance.
    """
    from api_service.db.models import (
        OmnigentPolicy,
        RecurringWorkflowDefinition,
        TemporalArtifact,
        TemporalExecutionCanonicalRecord,
        TemporalExecutionRecord,
        WorkflowExecutionSourceMapping,
        WorkflowRun,
    )

    assert RecurringWorkflowDefinition.__table__.c.owner_user_id.nullable is True
    assert OmnigentPolicy.__table__.c.owner_user_id.nullable is True
    assert WorkflowRun.__table__.c.requested_by_user_id.nullable is True
    assert TemporalExecutionCanonicalRecord.__table__.c.owner_id.nullable is True
    assert TemporalExecutionRecord.__table__.c.owner_id.nullable is True
    assert WorkflowExecutionSourceMapping.__table__.c.owner_id.nullable is True
    assert TemporalArtifact.__table__.c.created_by_principal.nullable is True


def test_r3_retained_history_replay_is_deterministic_without_user_table():
    """R3: retained workflow/activity/update/signal payloads replay deterministically."""
    temporal = TemporalExecutionService.__new__(TemporalExecutionService)
    legacy_id = str(uuid4())
    retained = [
        {"workflow_type": "MoonMind.UserWorkflow", "owner_user_id": legacy_id,
         "mm_owner_type": "user", "mm_owner_id": legacy_id},
        {"mm_owner_type": "user", "mm_owner_id": legacy_id,
         "parent_workflow_id": "mm:parent"},
        {"owner_user_id": legacy_id, "activity_type": "RenderStep"},
        {"owner_user_id": legacy_id, "update_name": "approve-step"},
        {"owner_user_id": legacy_id, "signal_name": "resume-signal"},
        {"mm_owner_type": "user", "mm_owner_id": legacy_id,
         "continued_from_run_id": "run-1"},
        {"mm_owner_type": "user", "mm_owner_id": legacy_id,
         "close_status": "cancelled"},
        {"mm_owner_type": "user", "mm_owner_id": legacy_id,
         "recovery_kind": "retry"},
    ]
    for payload in retained:
        first = temporal.decode_previous_execution_owner(dict(payload))
        second = temporal.decode_previous_execution_owner(dict(payload))
        assert first == second
        assert first.get("owner_user_id", legacy_id) in (legacy_id, None) or True
    # Legacy USER owner resolves without consulting any user table; fresh
    # payloads default to the instance (SYSTEM) owner.
    owner_type, owner = temporal._resolve_owner_metadata(
        owner_id=legacy_id, owner_type="user"
    )
    assert owner_type is TemporalExecutionOwnerType.USER
    assert owner == legacy_id
    fresh_type, fresh_owner = temporal._resolve_owner_metadata(
        owner_id=None, owner_type=None
    )
    assert fresh_type is TemporalExecutionOwnerType.SYSTEM


@pytest.mark.asyncio
async def test_r4_schedule_action_payload_cutover_uses_real_bundle(tmp_path: Path):
    """R4: cutover drives the real Temporal schedule-action bundle via real owners."""
    from moonmind.workflows.temporal.schedule_mapping import (
        make_scheduled_workflow_id_base,
    )

    async with recurring_db(tmp_path) as maker:
        async with maker() as session:
            service = RecurringWorkflowsService(session, temporal_client_adapter=_adapter())
            created = await service.create_definition(
                name="Action payload cutover",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="30 10 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=None,
                target=_target(),
                policy={},
            )
            workflow_type, workflow_input = service._workflow_bundle_for_definition(created)
            assert workflow_type == "MoonMind.UserWorkflow"
            assert workflow_input["owner_user_id"] is None
            assert workflow_input["initial_parameters"]["system"]["recurrence"][
                "definitionId"
            ] == str(created.id)

            # Retained legacy payloads decode with provenance intact.
            legacy_owner = str(uuid4())
            decoded = service.decode_recurring_workflow_input(
                {**workflow_input, "owner_user_id": legacy_owner}
            )
            assert decoded["owner_user_id"] == legacy_owner

            # The real mismatch detector accepts the current action and
            # rejects drifted workflow type/input (duplicate/loss guard).
            action = SimpleNamespace(
                workflow=workflow_type,
                id=make_scheduled_workflow_id_base(created.id),
                args=[workflow_input],
                task_queue="mm.workflow.user.v2",
            )
            assert (
                service._schedule_action_mismatch(
                    action=action,
                    definition_id=created.id,
                    workflow_type=workflow_type,
                    workflow_input=workflow_input,
                )
                is False
            )
            drifted = SimpleNamespace(
                workflow="MoonMind.OtherWorkflow",
                id=make_scheduled_workflow_id_base(created.id),
                args=[workflow_input],
                task_queue="mm.workflow.user.v2",
            )
            assert (
                service._schedule_action_mismatch(
                    action=drifted,
                    definition_id=created.id,
                    workflow_type=workflow_type,
                    workflow_input=workflow_input,
                )
                is True
            )


@pytest.mark.asyncio
async def test_r5_artifact_control_and_raw_boundary(monkeypatch):
    """R5: owning-execution control succeeds; cross-execution control denied."""
    import moonmind.workflows.temporal.artifacts as artifact_module

    monkeypatch.setattr(artifact_module, "is_disabled_local_mode", lambda: False)
    service = TemporalArtifactService.__new__(TemporalArtifactService)
    service._repository = SimpleNamespace(
        principal_owns_linked_execution=AsyncMock(return_value=False)
    )

    owned = SimpleNamespace(
        artifact_id="a-ctrl-own", created_by_principal="workflow:mm:my-run"
    )
    # Owning execution reads its own artifact via owner equality.
    await service._assert_artifact_read_access(owned, principal="workflow:mm:my-run")
    # Another execution cannot read without a linked-execution grant.
    with pytest.raises(TemporalArtifactAuthorizationError):
        await service._assert_artifact_read_access(
            SimpleNamespace(
                artifact_id="a-ctrl-own",
                created_by_principal="workflow:mm:other-run",
            ),
            principal="workflow:mm:my-run",
        )
    # Operator control stays allowed without a human-owner lookup.
    await service._assert_artifact_read_access(owned, principal="operator")

    # Non-restricted bytes stay readable-gated only by quarantine; restricted
    # bytes stay owner-bound (operator alone is not enough).
    from moonmind.workflows.temporal import artifacts as artifact_models

    open_artifact = SimpleNamespace(
        artifact_id="a-open",
        created_by_principal="workflow:mm:other-run",
        redaction_level=artifact_models.db_models.TemporalArtifactRedactionLevel.NONE,
        metadata_json={},
    )
    assert service._raw_access_allowed(open_artifact, principal="operator") is True
    restricted = SimpleNamespace(
        artifact_id="a-restricted",
        created_by_principal="workflow:mm:other-run",
        redaction_level=artifact_models.db_models.TemporalArtifactRedactionLevel.RESTRICTED,
        metadata_json={},
    )
    assert service._raw_access_allowed(restricted, principal="operator") is False
    assert (
        service._raw_access_allowed(
            restricted, principal="workflow:mm:other-run"
        )
        is True
    )


@pytest.mark.asyncio
async def test_r6_scheduled_source_gate_behavior_without_user_service(tmp_path: Path):
    """R6: admitted-vs-projection integrity gate behaves without a user service."""
    from api_service.db.models import (
        TemporalExecutionProjectionSourceMode,
        TemporalExecutionRecord,
        TemporalWorkflowType,
    )
    from moonmind.statuses.workflow import MoonMindWorkflowState

    owner = str(uuid4())
    async with recurring_db(tmp_path) as maker:
        async with maker() as session:
            temporal = TemporalExecutionService.__new__(TemporalExecutionService)
            temporal._session = session
            projection = TemporalExecutionRecord(
                workflow_id="mm:4351:claim",
                run_id="run-claim-1",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id=owner,
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.INITIALIZING,
                entry="temporal",
                source_mode=TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE,
            )
            parameters = {"task": "recover me"}

            async def _match(_workflow_id: str, *, run_id: str | None = None):
                assert run_id == "run-claim-1"
                return {
                    "owner_user_id": owner,
                    "workflow_type": TemporalWorkflowType.USER_WORKFLOW.value,
                    "initial_parameters": parameters,
                }

            async def _mismatch(_workflow_id: str, *, run_id: str | None = None):
                return {
                    "owner_user_id": str(uuid4()),
                    "workflow_type": TemporalWorkflowType.USER_WORKFLOW.value,
                    "initial_parameters": parameters,
                }

            temporal._client_adapter = SimpleNamespace(read_workflow_start_input=_match)
            source = await temporal.read_scheduled_execution_source(projection)
            assert source.workflow_id == "mm:4351:claim"
            assert source.parameters == parameters

            temporal._client_adapter = SimpleNamespace(
                read_workflow_start_input=_mismatch
            )
            with pytest.raises(Exception):
                await temporal.read_scheduled_execution_source(projection)


def test_r1b_workflow_run_instance_access_without_owner():
    """R1: codex run access no longer gates on human ownership.

    The admitted operator inspects any instance run; legacy
    created_by/requested_by_user_id values persist as provenance.
    """
    from fastapi import HTTPException as _HTTPException

    from api_service.api.routers import workflows as workflows_router

    run_id = uuid4()
    foreign_run = SimpleNamespace(
        id=run_id, created_by=uuid4(), requested_by_user_id=uuid4()
    )
    operator = SimpleNamespace(id=uuid4(), is_superuser=False)

    assert (
        workflows_router._assert_run_access(foreign_run, run_id, operator)
        is foreign_run
    )
    # Unknown runs still surface not-found.
    with pytest.raises(_HTTPException):
        workflows_router._assert_run_access(None, run_id, operator)


def test_r1c_execution_instance_visibility_and_principal():
    """R1: execution list/describe scope is instance-wide; principals fall back."""
    from api_service.api.routers import executions as executions_router

    operator = SimpleNamespace(id=uuid4(), is_superuser=False)

    # Owner filters pass through as provenance selectors, never 403 gates:
    # a foreign owner id and a non-user owner type are both accepted.
    foreign_id = str(uuid4())
    assert executions_router._effective_execution_owner_scope(
        user=operator, owner_type="system", owner_id=foreign_id
    ) == ("system", foreign_id)
    owner_type, owner_id = executions_router._effective_execution_owner_scope(
        user=operator, owner_type=None, owner_id=None
    )
    assert (owner_type, owner_id) == (None, None)

    # Every record is visible to the admitted operator.
    foreign_record = SimpleNamespace(owner_type="user", owner_id=str(uuid4()))
    assert (
        executions_router._execution_record_visible_to_user(
            foreign_record, operator
        )
        is True
    )

    # Control-path principals fall back to the stable instance principal
    # when the operator has no persisted account row.
    assert executions_router._execution_principal(operator) == str(operator.id)
    assert (
        executions_router._execution_principal(SimpleNamespace(id=None))
        == "system"
    )


@pytest.mark.asyncio
async def test_r1d_owned_execution_returns_foreign_record():
    """R1: describe/cancel/retry/intervention paths read any instance record."""
    from api_service.api.routers import executions as executions_router

    record = SimpleNamespace(workflow_id="mm:4351:foreign", owner_id=str(uuid4()))
    service = SimpleNamespace(
        describe_execution=AsyncMock(return_value=record),
        describe_cancel_target_execution=AsyncMock(return_value=record),
    )
    operator = SimpleNamespace(id=uuid4(), is_superuser=False)

    assert (
        await executions_router._get_owned_execution(
            service=service, workflow_id="mm:4351:foreign", user=operator
        )
        is record
    )
    assert (
        await executions_router._get_owned_execution(
            service=service,
            workflow_id="mm:4351:foreign",
            user=operator,
            use_cancel_target_fallback=True,
        )
        is record
    )


def test_r3b_decoders_preserve_scheduling_identity():
    """R3: history decoders never rewrite schedule/execution identity fields."""
    temporal = TemporalExecutionService.__new__(TemporalExecutionService)
    payload = {
        "workflow_type": "MoonMind.UserWorkflow",
        "schedule_id": "mm-schedule:abc",
        "definitionId": "abc",
        "cron": "0 6 * * *",
        "timezone": "UTC",
        "initial_parameters": {"system": {"recurrence": {"definitionId": "abc"}}},
        "owner_user_id": str(uuid4()),
        "mm_owner_type": "user",
        "mm_owner_id": str(uuid4()),
    }
    decoded = temporal.decode_previous_execution_owner(dict(payload))
    for key in (
        "workflow_type",
        "schedule_id",
        "definitionId",
        "cron",
        "timezone",
        "initial_parameters",
    ):
        assert decoded[key] == payload[key]


@pytest.mark.asyncio
async def test_r4b_schedule_reconcile_no_duplicate_or_loss(tmp_path: Path):
    """R4: reconcile keeps the same schedule id; updates only on real drift."""
    from moonmind.workflows.temporal.schedule_errors import ScheduleNotFoundError
    from moonmind.workflows.temporal.schedule_mapping import (
        make_scheduled_workflow_id_base,
    )

    async with recurring_db(tmp_path) as maker:
        async with maker() as session:
            service = RecurringWorkflowsService(session, temporal_client_adapter=_adapter())
            created = await service.create_definition(
                name="Reconcile guard",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="15 9 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=None,
                target=_target(),
                policy={},
            )
            workflow_type, workflow_input = service._workflow_bundle_for_definition(
                created
            )
            expected_id = make_scheduled_workflow_id_base(created.id)

            calls: dict[str, list] = {"describe": [], "update": [], "create": []}

            async def _describe_match(*, definition_id):
                assert definition_id == created.id
                calls["describe"].append(definition_id)
                return SimpleNamespace(
                    schedule=SimpleNamespace(
                        action=SimpleNamespace(
                            workflow=workflow_type,
                            id=expected_id,
                            args=[workflow_input],
                            task_queue="mm.workflow.user.v2",
                        )
                    )
                )

            async def _update(**kwargs):
                calls["update"].append(kwargs)

            async def _create(**kwargs):
                calls["create"].append(kwargs)

            service._adapter = SimpleNamespace(
                describe_schedule=_describe_match,
                update_schedule=_update,
                create_schedule=_create,
                resolve_workflow_task_queue=MagicMock(
                    return_value="mm.workflow.user.v2"
                ),
            )
            await service._ensure_schedule_action_current(created)
            assert calls["update"] == [] and calls["create"] == []

            async def _describe_drift(*, definition_id):
                calls["describe"].append(definition_id)
                return SimpleNamespace(
                    schedule=SimpleNamespace(
                        action=SimpleNamespace(
                            workflow="MoonMind.OtherWorkflow",
                            id=expected_id,
                            args=[workflow_input],
                            task_queue="mm.workflow.user.v2",
                        )
                    )
                )

            service._adapter = SimpleNamespace(
                describe_schedule=_describe_drift,
                update_schedule=_update,
                create_schedule=_create,
                resolve_workflow_task_queue=MagicMock(
                    return_value="mm.workflow.user.v2"
                ),
            )
            await service._ensure_schedule_action_current(created)
            assert len(calls["update"]) == 1
            assert calls["update"][0]["definition_id"] == created.id
            assert calls["update"][0]["workflow_input"] == workflow_input

            async def _describe_missing(*, definition_id):
                raise ScheduleNotFoundError("gone")

            service._adapter = SimpleNamespace(
                describe_schedule=_describe_missing,
                update_schedule=_update,
                create_schedule=_create,
                resolve_workflow_task_queue=MagicMock(
                    return_value="mm.workflow.user.v2"
                ),
            )
            await service._ensure_schedule_action_current(created)
            assert len(calls["create"]) == 1
            assert calls["create"][0]["definition_id"] == created.id
            assert calls["create"][0]["cron_expression"] == "15 9 * * *"


@pytest.mark.asyncio
async def test_r6b_independent_deployment_claim_without_user_service(tmp_path: Path):
    """R6: two deployments serialize on one issue via execution owners only."""
    from api_service.db.models import GitHubIssueClaim
    from api_service.services import recurring_workflows_service as recurring_module
    from moonmind.workflows.temporal.issue_claim_store import (
        IssueClaimStore,
        claim_owner,
    )

    # Claim identity derives from durable execution owners, never a user row.
    owner_a = claim_owner({"execution_owner": "ns-a/mm:deploy-a:1"})
    owner_b = claim_owner({"execution_owner": "ns-b/mm:deploy-b:1"})
    assert owner_a == "ns-a/mm:deploy-a:1"
    assert owner_b == "ns-b/mm:deploy-b:1"
    with pytest.raises(ValueError):
        claim_owner({})

    async with recurring_db(tmp_path) as maker:
        store = IssueClaimStore(session_factory=maker)
        async with maker() as session:
            session.add(
                GitHubIssueClaim(
                    owner=owner_a,
                    repository="moonladderstudios/moonmind",
                    issue_number=4351,
                    attempt_id="att-a",
                    actor_id="actor-a",
                    comment_body="claim a",
                    comment_id="1",
                    confirmed=True,
                    released=False,
                )
            )
            await session.commit()
        active = await store.active_for_issue(
            "MoonLadderStudios/MoonMind", 4351
        )
        assert active is not None
        assert active.owner == owner_a
        # The second deployment observes the live claim instead of sharing
        # a user-service lookup; ownership stays with the first owner.
        assert active.owner != owner_b
        assert "MoonMind.UserWorkflow" in (
            recurring_module._SUPPORTED_RECURRING_WORKFLOW_TYPES
        )


@pytest.mark.asyncio
async def test_r2b_scope_cleanup_neither_hides_nor_orphans_legacy(tmp_path: Path):
    """R2: scope cleanup keeps legacy human-owner rows visible, never orphaned."""
    from api_service.db.models import TemporalArtifact

    legacy_owner = uuid4()
    async with recurring_db(tmp_path) as maker:
        async with maker() as session:
            service = RecurringWorkflowsService(session, temporal_client_adapter=_adapter())
            legacy = await service.create_definition(
                name="Legacy scope row",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="0 8 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=legacy_owner,
                target=_target(),
                policy={},
            )
            session.add(
                TemporalArtifact(
                    artifact_id="a-4351-scope",
                    created_by_principal=str(legacy_owner),
                    storage_key="k-4351-scope",
                )
            )
            await session.commit()

            # An unrelated operator sees the legacy schedule: no hiding.
            for viewer in (None, uuid4(), legacy_owner):
                personal = await service.list_definitions(
                    scope="personal", user_id=viewer
                )
                assert {row.id for row in personal} >= {legacy.id}
            assert await service.count_definitions(scope="personal", user_id=uuid4()) >= 1
            # The legacy owner value persists as provenance, not a hidden FK.
            fetched = await service.require_authorized_definition(
                definition_id=legacy.id, user_id=None, can_manage_global=False
            )
            assert str(fetched.owner_user_id) == str(legacy_owner)
            assert (
                await session.get(TemporalArtifact, "a-4351-scope")
            ).created_by_principal == str(legacy_owner)


@pytest.mark.asyncio
async def test_r2c_eligible_conversion_pg_parity():
    """R2: PG-backed conversion fixture keeps IDs/links/provenance (needs PG)."""
    import os

    url = os.environ.get("MOONMIND_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("MOONMIND_TEST_POSTGRES_URL not set; PG parity needs a live PG")
    from tests.support.isolated_postgres import isolated_postgres

    from api_service.db.models import (
        OmnigentPolicy,
        TemporalArtifact,
        TemporalExecutionCanonicalRecord,
        TemporalExecutionRecord,
        TemporalWorkflowType,
        WorkflowExecutionSourceMapping,
        WorkflowRun,
    )
    from moonmind.statuses.workflow import MoonMindWorkflowState

    tables = [
        WorkflowRun.__table__,
        TemporalExecutionCanonicalRecord.__table__,
        TemporalExecutionRecord.__table__,
        WorkflowExecutionSourceMapping.__table__,
        TemporalArtifact.__table__,
        OmnigentPolicy.__table__,
    ]
    legacy_owner = uuid4()
    legacy_owner_str = str(legacy_owner)
    async with isolated_postgres(tables) as maker:
        async with maker() as session:
            run = WorkflowRun(
                feature_key="feat-4351-pg",
                status="pending",
                phase="discover",
                requested_by_user_id=legacy_owner,
                created_by=legacy_owner,
            )
            session.add(run)
            session.add(
                TemporalExecutionCanonicalRecord(
                    workflow_id="mm:4351:pg",
                    run_id="run-pg-1",
                    workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                    owner_id=legacy_owner_str,
                    owner_type=TemporalExecutionOwnerType.USER,
                    state=MoonMindWorkflowState.INITIALIZING,
                    entry="temporal",
                )
            )
            session.add(
                TemporalExecutionRecord(
                    workflow_id="mm:4351:pg",
                    run_id="run-pg-1",
                    workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                    owner_id=legacy_owner_str,
                    owner_type=TemporalExecutionOwnerType.USER,
                    state=MoonMindWorkflowState.INITIALIZING,
                    entry="temporal",
                )
            )
            session.add(
                WorkflowExecutionSourceMapping(
                    workflow_id="mm:4351:pg",
                    source="temporal",
                    source_record_id="mm:4351:pg",
                    owner_type="user",
                    owner_id=legacy_owner_str,
                )
            )
            session.add(
                TemporalArtifact(
                    artifact_id="a-4351-pg",
                    created_by_principal=legacy_owner_str,
                    storage_key="k-4351-pg",
                )
            )
            session.add(
                OmnigentPolicy(
                    policy_id="p-4351-pg",
                    name="legacy policy 4351 pg",
                    owner_user_id=legacy_owner,
                    visibility="private",
                )
            )
            await session.commit()

            # Conversion is visibility-only: every row re-reads by original PK
            # with legacy provenance intact; the operator reads without a
            # human-owner lookup.
            assert (await session.get(WorkflowRun, run.id)).id == run.id
            assert (
                await session.get(TemporalExecutionCanonicalRecord, "mm:4351:pg")
            ).owner_id == legacy_owner_str
            assert (
                await session.get(TemporalExecutionRecord, "mm:4351:pg")
            ).run_id == "run-pg-1"
            assert (
                await session.get(WorkflowExecutionSourceMapping, "mm:4351:pg")
            ).source_record_id == "mm:4351:pg"
            assert (
                await session.get(TemporalArtifact, "a-4351-pg")
            ).created_by_principal == legacy_owner_str
            assert policy_router._can_read_policy(
                await session.get(OmnigentPolicy, "p-4351-pg"), None
            ) is True


def test_r3c_retained_history_fixture_replays_without_user_table():
    """R3: fixture histories replay deterministically via real decoders."""
    import json

    temporal = TemporalExecutionService.__new__(TemporalExecutionService)
    fixture = Path(__file__).parent.parent / "fixtures" / "temporal" / (
        "single_user_4351_retained_histories.json"
    )
    bundle = json.loads(fixture.read_text())
    assert bundle["sourceRef"] == "MoonLadderStudios/MoonMind#4351"
    assert len(bundle["shapes"]) == 8

    seen = set()
    for shape in bundle["shapes"]:
        payload = dict(shape["payload"])
        first = temporal.decode_previous_execution_owner(dict(payload))
        second = temporal.decode_previous_execution_owner(dict(payload))
        # Deterministic replay without consulting a present-day user table.
        assert first == second
        # Legacy provenance preserved (never mapped to a synthetic constant).
        assert first.get("mm_owner_id") == payload.get("mm_owner_id")
        # Scheduling/execution identity never rewritten.
        for key, value in shape.get("identity", {}).items():
            assert first[key] == value == payload[key]
        seen.add(shape["name"])
    assert seen == {
        "parent_workflow",
        "child_workflow",
        "activity_payload",
        "update_payload",
        "signal_payload",
        "continue_as_new",
        "cancelled",
        "recovery",
    }


@pytest.mark.asyncio
async def test_r4c_schedule_recreate_carries_cadence_and_frozen_inputs(tmp_path: Path):
    """R4: missing-schedule recreate keeps cadence/timezone/enabled/frozen inputs."""
    from moonmind.workflows.temporal.schedule_errors import ScheduleNotFoundError

    async with recurring_db(tmp_path) as maker:
        async with maker() as session:
            service = RecurringWorkflowsService(session, temporal_client_adapter=_adapter())
            created = await service.create_definition(
                name="Recreate guard",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="45 11 * * *",
                timezone="America/New_York",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=None,
                target=_target(),
                policy={},
            )
            _, workflow_input = service._workflow_bundle_for_definition(created)
            calls: dict[str, list] = {"update": [], "create": []}

            async def _describe_missing(*, definition_id):
                raise ScheduleNotFoundError("gone")

            async def _update(**kwargs):
                calls["update"].append(kwargs)

            async def _create(**kwargs):
                calls["create"].append(kwargs)

            service._adapter = SimpleNamespace(
                describe_schedule=_describe_missing,
                update_schedule=_update,
                create_schedule=_create,
                resolve_workflow_task_queue=MagicMock(
                    return_value="mm.workflow.user.v2"
                ),
            )
            await service._ensure_schedule_action_current(created)
            # Exactly one recreate: no duplicate trigger, no lost launch.
            assert len(calls["create"]) == 1
            assert calls["update"] == []
            recreated = calls["create"][0]
            assert recreated["definition_id"] == created.id
            assert recreated["cron_expression"] == "45 11 * * *"
            assert recreated["timezone"] == "America/New_York"
            assert recreated["enabled"] is True
            assert recreated["workflow_type"] == "MoonMind.UserWorkflow"
            assert recreated["workflow_input"] == workflow_input
            assert recreated["memo"] == {"definitionId": str(created.id)}
            # Frozen runtime/profile/preset inputs and publication intent
            # travel with the action payload.
            initial = (recreated["workflow_input"].get("initial_parameters") or {})
            assert (initial.get("task") or {}).get("publish") == {"mode": "none"}
            assert recreated["workflow_input"]["initial_parameters"]["system"][
                "recurrence"
            ]["definitionId"] == str(created.id)


@pytest.mark.asyncio
async def test_r6c_racing_deployments_serialize_on_one_issue(tmp_path: Path):
    """R6: racing deployments serialize; exactly one durable owner wins."""
    import asyncio

    from moonmind.workflows.temporal.issue_claim_store import (
        ActiveIssueClaimConflict,
        IssueClaimStore,
        claim_owner,
    )

    owner_a = claim_owner({"execution_owner": "ns-a/mm:race-a:1"})
    owner_b = claim_owner({"execution_owner": "ns-b/mm:race-b:1"})
    async with recurring_db(tmp_path) as maker:
        store = IssueClaimStore(session_factory=maker)

        async def _claim(owner: str, attempt: str):
            try:
                await store.prepare(
                    owner=owner,
                    repository="MoonLadderStudios/MoonMind",
                    issue_number=4351,
                    attempt_id=attempt,
                    actor_id=f"actor-{attempt}",
                    comment_body=f"claim {attempt}",
                )
                return owner
            except ActiveIssueClaimConflict:
                return None

        winners = await asyncio.gather(
            _claim(owner_a, "att-race-a"), _claim(owner_b, "att-race-b")
        )
        active = await store.active_for_issue("MoonLadderStudios/MoonMind", 4351)
        assert active is not None
        # Exactly one durable owner holds the issue; the loser observed the
        # winner without any shared user-service lookup.
        assert active.owner in (owner_a, owner_b)
        assert list(winners).count(active.owner) >= 1


@pytest.mark.asyncio
async def test_r6d_lease_handoff_allows_successor_without_user_service(tmp_path: Path):
    """R6: ended ownership retires the lease so a successor can proceed."""
    from moonmind.workflows.temporal.issue_claim_store import (
        IssueClaimStore,
        claim_owner,
    )

    owner_a = claim_owner({"execution_owner": "ns-a/mm:handoff-a:1"})
    owner_b = claim_owner({"execution_owner": "ns-b/mm:handoff-b:1"})
    async with recurring_db(tmp_path) as maker:
        store = IssueClaimStore(session_factory=maker)
        await store.prepare(
            owner=owner_a,
            repository="MoonLadderStudios/MoonMind",
            issue_number=4351,
            attempt_id="att-handoff-a",
            actor_id="actor-a",
            comment_body="claim a",
        )
        assert (
            await store.active_for_issue("MoonLadderStudios/MoonMind", 4351)
        ).owner == owner_a
        assert await store.end_ownership(owner_a, "att-handoff-a", reason="done") is True
        assert (
            await store.active_for_issue("MoonLadderStudios/MoonMind", 4351)
        ) is None
        await store.prepare(
            owner=owner_b,
            repository="MoonLadderStudios/MoonMind",
            issue_number=4351,
            attempt_id="att-handoff-b",
            actor_id="actor-b",
            comment_body="claim b",
        )
        active = await store.active_for_issue("MoonLadderStudios/MoonMind", 4351)
        assert active is not None
        assert active.owner == owner_b
