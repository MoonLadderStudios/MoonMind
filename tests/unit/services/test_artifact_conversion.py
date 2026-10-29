"""Real guarded artifact conversion and retained recovery boundaries."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    TemporalArtifact,
    TemporalArtifactDeletionIntent,
    TemporalArtifactLink,
    TemporalArtifactPin,
    TemporalArtifactRedactionLevel,
    TemporalArtifactUseClaim,
    WorkflowArtifact,
    WorkflowArtifactType,
    WorkflowRun,
)
from api_service.services import single_user_conversion as conversion
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from tests.artifact_access import access_for_test
from tests.helpers.account_source import User, add_source_user, create_source_schema

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/conversion.db")
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(create_source_schema)
    try:
        yield maker
    finally:
        await engine.dispose()


def service(session, tmp_path):
    return TemporalArtifactService(
        TemporalArtifactRepository(session),
        store=LocalTemporalArtifactStore(tmp_path / "bytes"),
    )


async def seed(session, tmp_path, *, quarantine=False):
    owner = str(uuid4())
    session.add(User(id=UUID(owner), email="retained@example.invalid"))
    await session.commit()
    artifacts = service(session, tmp_path)
    artifact, _ = await artifacts.create(
        principal=owner, content_type="application/octet-stream",
        redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
        metadata_json={"quarantine": True} if quarantine else {},
        link={"namespace": "default", "workflow_id": "retained-workflow",
              "run_id": "retained-run", "link_type": "input"},
    )
    artifact = await artifacts.write_complete(
        artifact_id=artifact.artifact_id, principal=owner, payload=b"retained bytes",
        content_type="application/octet-stream",
    )
    session.add(TemporalArtifactPin(artifact_id=artifact.artifact_id,
                                  pinned_by_principal=owner, reason="retain"))
    session.add(TemporalArtifactUseClaim(
        artifact_id=artifact.artifact_id, owner_principal=owner,
        request_id="ongoing-recovery", operation_kind="restore",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    ))
    session.add(TemporalArtifactDeletionIntent(
        artifact_id=artifact.artifact_id, initiated_by_principal=owner,
    ))
    await session.commit()
    return artifact.artifact_id, owner


async def test_guarded_conversion_preserves_recovery_and_restarts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session, tmp_path)
            raw = await session.get(TemporalArtifact, artifact_id)
            preview_id = raw.metadata_json["preview_artifact_id"]
            assert preview_id != artifact_id
            retained = (await session.execute(select(TemporalArtifact))).scalars().all()
            assert {row.artifact_id for row in retained} == {artifact_id, preview_id}
            assert all(row.created_by_principal == owner and not row.operator_recovery_granted
                       for row in retained)
            preview = await session.get(TemporalArtifact, preview_id)
            assert preview.redaction_level is TemporalArtifactRedactionLevel.PREVIEW_ONLY
            assert preview.metadata_json == {"preview_of": artifact_id, "policy": "auto-generated"}
            artifacts = service(session, tmp_path)
            with pytest.raises(TemporalArtifactAuthorizationError):
                await artifacts.read(
                    artifact_id=artifact_id, access=access_for_test("operator")
                )
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert result.published, result.to_sanitized_dict()
            assert result.transforms["artifacts"]["recovery_grants"] == 2
        # A new service/session represents restart, without an account lookup.
        async with maker() as session:
            retained = (await session.execute(select(TemporalArtifact))).scalars().all()
            assert {row.artifact_id for row in retained if row.operator_recovery_granted} == {
                artifact_id, preview_id}
            assert {row.artifact_id for row in retained} == {artifact_id, preview_id}
            assert all(row.created_by_principal == owner for row in retained)
            artifacts = service(session, tmp_path)
            with pytest.raises(TemporalArtifactAuthorizationError):
                await artifacts.read(
                    artifact_id=artifact_id, access=access_for_test("operator")
                )
            for principal, admitted in [("operator", None), (owner, None),
                                        ("service:restore", owner),
                                        ("service:restore", "operator")]:
                row, payload = await artifacts.read(
                    artifact_id=artifact_id,
                    access=access_for_test(
                        principal,
                        admitted_principal=admitted,
                        recovery_principal="operator",
                    ),
                )
                assert payload == b"retained bytes"
                assert row.created_by_principal == owner
            _row, path = await artifacts.read_path(
                artifact_id=artifact_id,
                access=access_for_test("operator", recovery_principal="operator"),
            )
            assert path.read_bytes() == b"retained bytes"
            _row, chunks = await artifacts.read_chunks(
                artifact_id=artifact_id,
                access=access_for_test("operator", recovery_principal="operator"),
            )
            assert b"".join(chunks) == b"retained bytes"
            _row, expires_at, url = await artifacts.presign_download(
                artifact_id=artifact_id,
                access=access_for_test("operator", recovery_principal="operator"),
            )
            assert expires_at > datetime.now(UTC)
            assert url == f"/api/artifacts/{artifact_id}/download"
            _row, _links, _pin, policy = await artifacts.get_metadata(
                artifact_id=artifact_id,
                access=access_for_test("operator", recovery_principal="operator"),
            )
            assert policy.raw_access_allowed
            for principal in ["service:restore", "workflow:unrelated", str(uuid4())]:
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await artifacts.read(
                        artifact_id=artifact_id, access=access_for_test(principal)
                    )
            pin = (await session.execute(select(TemporalArtifactPin))).scalar_one()
            claim = (await session.execute(select(TemporalArtifactUseClaim).where(
                TemporalArtifactUseClaim.request_id == "ongoing-recovery"))).scalar_one()
            intent = await session.get(TemporalArtifactDeletionIntent, artifact_id)
            link = (await session.execute(select(TemporalArtifactLink))).scalar_one()
            assert pin.pinned_by_principal == claim.owner_principal == owner
            assert intent.initiated_by_principal == owner
            assert (link.workflow_id, link.run_id) == ("retained-workflow", "retained-run")
            again = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert again.published


async def test_conversion_never_releases_quarantine(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session, tmp_path, quarantine=True)
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert result.published, result.to_sanitized_dict()
            artifacts = service(session, tmp_path)
            for principal in ["operator", owner]:
                for method in [artifacts.read, artifacts.read_path, artifacts.read_chunks,
                               artifacts.presign_download]:
                    with pytest.raises((TemporalArtifactStateError,
                                        TemporalArtifactAuthorizationError)):
                        await method(
                            artifact_id=artifact_id,
                            access=access_for_test(
                                principal, recovery_principal="operator"
                            ),
                        )
            _row, _links, _pin, policy = await artifacts.get_metadata(
                artifact_id=artifact_id, access=access_for_test("operator")
            )
            assert policy.raw_access_allowed is False


async def test_unknown_workflow_parent_refuses_without_artifact_mutation(tmp_path):
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session, tmp_path)
            run = WorkflowRun(feature_key="retained", requested_by_user_id=uuid4())
            session.add(run)
            await session.flush()
            session.add(WorkflowArtifact(workflow_run_id=run.id,
                artifact_type=WorkflowArtifactType.CODEX_LOGS, path="retained.txt"))
            await session.commit()
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert result.published is False
            assert result.decision.reason_code == "unknown_principal"
            assert not getattr(await session.get(TemporalArtifact, artifact_id),
                               "operator_recovery_granted", False)
            assert not (await session.execute(select(conversion.SingleUserConversionRun))).scalars().all()


@pytest.mark.parametrize("owner_prefix", ["", "user:"])
async def test_unknown_active_claim_authority_refuses(tmp_path, owner_prefix):
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            claim = (await session.execute(select(TemporalArtifactUseClaim))).scalar_one()
            claim.owner_principal = f"{owner_prefix}{uuid4()}"
            await session.commit()
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert not result.published
            assert result.decision.reason_code == "unknown_principal"
            assert not (await session.get(TemporalArtifact, artifact_id)).operator_recovery_granted


async def test_same_count_artifact_authority_change_rejects_stale_preflight(tmp_path):
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            bound = await conversion.preflight(session)
        async with maker() as writer:
            row = await writer.get(TemporalArtifact, artifact_id)
            row.metadata_json = {"quarantine": True}
            await writer.commit()
        async with maker() as session:
            with pytest.raises(conversion.StaleAttributionError):
                await conversion.apply_conversion(session, bound, operator_authorized=True,
                    transforms=conversion.DEFAULT_SUBSYSTEM_TRANSFORMS)


async def test_old_published_ledger_preserves_admission_without_recovery_grant(tmp_path):
    from api_service.services.single_user_conversion import completed_guarded_upgrade
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            bound = await conversion.preflight(session)
            # Historical admission is not proof of the new artifact recovery.
            session.add(conversion.SingleUserConversionRun(
                preflight_digest=conversion.compute_digest(bound.fingerprint, bound.options,
                    schema_revision="386_single_user_conversion_4346"),
                status="complete", result_json={"published": True,
                    "decision": {"disposition": "eligible_conversion"}},
            ))
            await session.commit()
            assert await completed_guarded_upgrade(session) is None
            assert not (await session.get(TemporalArtifact, artifact_id)).operator_recovery_granted
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service(session, tmp_path).read(
                    artifact_id=artifact_id, access=access_for_test("operator")
                )
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert result.published, result.to_sanitized_dict()
            assert await completed_guarded_upgrade(session) is not None
            assert (await session.get(TemporalArtifact, artifact_id)).operator_recovery_granted


async def test_interrupted_real_conversion_rolls_back_grant_and_retries(tmp_path):
    from sqlalchemy import event
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            engine = session.get_bind()

            def fail_completion(_conn, _cursor, statement, *_args):
                if statement.startswith("INSERT INTO single_user_conversion_runs"):
                    raise RuntimeError("interrupted completion")

            event.listen(engine, "before_cursor_execute", fail_completion)
            try:
                with pytest.raises(RuntimeError, match="interrupted completion"):
                    await conversion.run_guarded_upgrade(session, operator_authorized=True)
            finally:
                event.remove(engine, "before_cursor_execute", fail_completion)
        async with maker() as session:
            row = await session.get(TemporalArtifact, artifact_id)
            assert row.operator_recovery_granted is False
            assert not (await session.execute(select(conversion.SingleUserConversionRun))).scalars().all()
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert result.published
        async with maker() as session:
            assert (await session.get(TemporalArtifact, artifact_id)).operator_recovery_granted


async def test_alias_recovery_preserves_claim_and_quota_scopes(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session, tmp_path)
            alias = str(uuid4())
            session.add(User(id=UUID(alias), email="alias@example.invalid"))
            row = await session.get(TemporalArtifact, artifact_id)
            row.created_by_principal = alias
            await session.commit()
            repo = TemporalArtifactRepository(session)
            before = await repo.quota_usage_for_scope(alias)
            result = await conversion.run_guarded_upgrade(
                session, operator_authorized=True, alias_groups=[{owner, alias}],
            )
            assert result.published
            assert await repo.quota_usage_for_scope(alias) == before
            assert (await repo.quota_usage_for_scope("operator"))["logicalBytes"] == 0
            artifacts = service(session, tmp_path)
            assert (
                await artifacts.read(
                    artifact_id=artifact_id,
                    access=access_for_test("operator", recovery_principal="operator"),
                )
            )[1] == b"retained bytes"
            assert not await artifacts.release_saved_work_use(
                artifact_id=artifact_id, principal="operator", request_id="ongoing-recovery",
            )
            claim = (await session.execute(select(TemporalArtifactUseClaim).where(
                TemporalArtifactUseClaim.request_id == "ongoing-recovery"))).scalar_one()
            assert claim.owner_principal == owner
            assert (await session.get(TemporalArtifact, artifact_id)).created_by_principal == alias


@pytest.mark.parametrize("owner", [None, "someone@example.invalid", "service:"])
async def test_unsupported_creator_refuses_even_with_deployment_attestation(tmp_path, owner):
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            row = await session.get(TemporalArtifact, artifact_id)
            row.created_by_principal = owner
            await session.commit()
            result = await conversion.run_guarded_upgrade(
                session, operator_authorized=True, deployment_owned_tables={"temporal_artifacts"},
            )
            assert not result.published
            row = await session.get(TemporalArtifact, artifact_id)
            assert not row.operator_recovery_granted


@pytest.mark.parametrize(
    "machine", ["workflow:retained", "system:agent_runtime", "agent_workflow"]
)
async def test_machine_creator_and_caller_metadata_cannot_grant_recovery(
    tmp_path, monkeypatch, machine
):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            row = await session.get(TemporalArtifact, artifact_id)
            row.created_by_principal = machine
            row.metadata_json = {"operator_recovery_granted": True}
            await session.commit()
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert result.published
            row = await session.get(TemporalArtifact, artifact_id)
            assert not row.operator_recovery_granted
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service(session, tmp_path).read(
                    artifact_id=artifact_id, access=access_for_test("operator")
                )


async def test_old_fresh_admission_is_not_artifact_recovery_proof(tmp_path):
    from api_service.services.single_user_conversion import completed_guarded_upgrade
    async with database(tmp_path) as maker:
        async with maker() as session:
            session.add(conversion.SingleUserConversionRun(
                preflight_digest="old-fresh", status="complete",
                result_json={"published": True, "decision": {"disposition": "fresh_init"}},
            ))
            await session.commit()
            artifact_id, _owner = await seed(session, tmp_path)
            assert await completed_guarded_upgrade(session) is None
            assert not (await session.get(TemporalArtifact, artifact_id)).operator_recovery_granted
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service(session, tmp_path).read(
                    artifact_id=artifact_id, access=access_for_test("operator")
                )
            assert (await conversion.run_guarded_upgrade(session, operator_authorized=True)).published
        async with maker() as restarted:
            assert await completed_guarded_upgrade(restarted) is not None
            assert (await restarted.get(TemporalArtifact, artifact_id)).operator_recovery_granted


async def test_quarantine_short_circuits_linked_execution_raw_fallback(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session, tmp_path, quarantine=True)
            assert (await conversion.run_guarded_upgrade(session, operator_authorized=True)).published
            artifacts = service(session, tmp_path)
            linked = AsyncMock(return_value=True)
            monkeypatch.setattr(artifacts._repository, "principal_owns_linked_execution", linked)
            row = await session.get(TemporalArtifact, artifact_id)
            assert not await artifacts._artifact_raw_access_allowed(
                row, access=access_for_test(owner)
            )
            with pytest.raises(TemporalArtifactStateError):
                await artifacts._assert_artifact_raw_access(
                    row, access=access_for_test(owner)
                )
            linked.assert_not_awaited()


async def test_live_machine_temporal_parent_is_preserved_by_conversion(tmp_path):
    # Execution owners treat historical owner fields as provenance, so accepted
    # work needs no rewrite; legacy workflow parents still refuse (see
    # test_old_admission_survives_unsupported_mixed_history_without_raw_grant).
    from api_service.db.models import (
        TemporalExecutionOwnerType,
        TemporalExecutionRecord,
        TemporalWorkflowType,
    )
    from moonmind.statuses.workflow import MoonMindWorkflowState
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            session.add(TemporalExecutionRecord(
                workflow_id="retained-workflow", run_id="retained-run", entry="e",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id=None, owner_type=TemporalExecutionOwnerType.SYSTEM,
                state=MoonMindWorkflowState.EXECUTING,
            ))
            await session.commit()
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert result.published
            assert (await session.get(TemporalArtifact, artifact_id)).operator_recovery_granted
            retained = await session.get(TemporalExecutionRecord, "retained-workflow")
            assert retained.run_id == "retained-run"
            assert retained.owner_id is None
            assert retained.owner_type == TemporalExecutionOwnerType.SYSTEM
            assert retained.state == MoonMindWorkflowState.EXECUTING


async def test_null_owner_projection_does_not_grant_linked_restricted_artifact_access(
    tmp_path,
    monkeypatch,
):
    from api_service.db.models import (
        TemporalExecutionOwnerType,
        TemporalExecutionRecord,
        TemporalWorkflowType,
    )
    from moonmind.statuses.workflow import MoonMindWorkflowState

    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    async with database(tmp_path) as maker:
        async with maker() as session:
            human_artifact_id, owner = await seed(session, tmp_path)
            session.add(
                TemporalExecutionRecord(
                    workflow_id="retained-workflow",
                    run_id="retained-run",
                    namespace="default",
                    entry="temporal",
                    workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                    owner_id=None,
                    owner_type=TemporalExecutionOwnerType.USER,
                    state=MoonMindWorkflowState.EXECUTING,
                )
            )
            artifacts = service(session, tmp_path)
            machine_principal = "workflow:retained-workflow"
            machine_artifact, _ = await artifacts.create(
                principal=machine_principal,
                content_type="application/octet-stream",
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                link={
                    "namespace": "default",
                    "workflow_id": "retained-workflow",
                    "run_id": "retained-run",
                    "link_type": "output",
                },
            )
            await artifacts.write_complete(
                artifact_id=machine_artifact.artifact_id,
                principal=machine_principal,
                payload=b"machine-only evidence",
                content_type="application/octet-stream",
            )
            machine_artifact_id = machine_artifact.artifact_id
            await session.commit()
            for principal in (owner, "operator"):
                assert not await artifacts._repository.principal_owns_linked_execution(
                    artifact_id=machine_artifact_id,
                    principal=principal,
                )
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await artifacts.read(
                        artifact_id=machine_artifact_id,
                        access=access_for_test(principal),
                    )

            converted = await conversion.run_guarded_upgrade(
                session, operator_authorized=True
            )
            assert converted.published, converted.to_sanitized_dict()
            assert (
                await session.get(TemporalArtifact, human_artifact_id)
            ).operator_recovery_granted

        async with maker() as restarted:
            assert (
                await conversion.run_guarded_upgrade(
                    restarted,
                    operator_authorized=True,
                )
            ).published
            projection = await restarted.get(
                TemporalExecutionRecord, "retained-workflow"
            )
            assert projection.owner_id is None
            assert projection.owner_type == TemporalExecutionOwnerType.USER
            machine_artifact = await restarted.get(
                TemporalArtifact, machine_artifact_id
            )
            assert machine_artifact.created_by_principal == machine_principal
            assert machine_artifact.operator_recovery_granted is False
            artifacts = service(restarted, tmp_path)
            assert (
                await artifacts.read(
                    artifact_id=human_artifact_id,
                    access=access_for_test("operator", recovery_principal="operator"),
                )
            )[1] == b"retained bytes"
            for principal in (owner, "operator"):
                assert not await artifacts._repository.principal_owns_linked_execution(
                    artifact_id=machine_artifact_id,
                    principal=principal,
                )
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await artifacts.read(
                        artifact_id=machine_artifact_id,
                        access=access_for_test(principal),
                    )


@pytest.mark.parametrize("projection_owner", ["unknown", "another_user"])
async def test_populated_projection_owner_still_requires_single_person_attribution(
    tmp_path,
    projection_owner,
):
    from api_service.db.models import (
        TemporalExecutionOwnerType,
        TemporalExecutionRecord,
        TemporalWorkflowType,
    )

    async with database(tmp_path) as maker, maker() as session:
        artifact_id, _owner = await seed(session, tmp_path)
        other_id = uuid4()
        if projection_owner == "another_user":
            await add_source_user(session, User(id=other_id, email="other@example.invalid"))
        session.add(
            TemporalExecutionRecord(
                workflow_id="retained-workflow",
                run_id="retained-run",
                namespace="default",
                entry="temporal",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id=str(other_id),
                owner_type=TemporalExecutionOwnerType.USER,
            )
        )
        await session.commit()
        refused = await conversion.run_guarded_upgrade(
            session, operator_authorized=True
        )
        assert refused.published is False
        assert refused.decision.reason_code == (
            "unknown_principal" if projection_owner == "unknown" else "multi_person"
        )
        assert (
            await session.get(TemporalArtifact, artifact_id)
        ).operator_recovery_granted is False
        projection = await session.get(TemporalExecutionRecord, "retained-workflow")
        assert projection.owner_id == str(other_id)


@pytest.mark.parametrize(
    "table_name",
    [
        "temporal_executions",
        "temporal_execution_sources",
        "workflow_execution_source_mappings",
    ],
)
@pytest.mark.parametrize(
    "configuration", ["default", "explicit_zero", "oidc", "nonzero"]
)
async def test_reserved_local_execution_owner_preserves_projection_without_recovery_grant(
    tmp_path,
    monkeypatch,
    table_name,
    configuration,
):
    from api_service.db.models import (
        Base,
        TemporalExecutionOwnerType,
        TemporalWorkflowType,
    )

    monkeypatch.setenv(
        "AUTH_PROVIDER", "oidc" if configuration == "oidc" else "disabled"
    )
    monkeypatch.delenv("DEFAULT_USER_ID", raising=False)
    if configuration != "default":
        monkeypatch.setenv(
            "DEFAULT_USER_ID",
            (
                str(uuid4())
                if configuration == "nonzero"
                else conversion.DEPLOYMENT_SUBJECT_ID
            ),
        )
    table = Base.metadata.tables[table_name]
    values = {
        "workflow_id": "retained-workflow",
        "entry": "temporal",
        "owner_type": TemporalExecutionOwnerType.USER,
        "owner_id": conversion.DEPLOYMENT_SUBJECT_ID,
    }
    if table_name == "workflow_execution_source_mappings":
        values.update(source="temporal", source_record_id="retained-workflow")
    else:
        values.update(
            run_id="retained-run", workflow_type=TemporalWorkflowType.USER_WORKFLOW
        )
    async with database(tmp_path) as maker:
        async with maker() as session:
            await session.execute(table.insert().values(**values))
            session.add(
                TemporalArtifact(
                    artifact_id="machine-evidence",
                    storage_key="machine-key",
                    created_by_principal="workflow:retained-workflow",
                    redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                )
            )
            await session.commit()
            before = dict((await session.execute(select(table))).mappings().one())
            result = await conversion.run_guarded_upgrade(
                session, operator_authorized=True
            )
            eligible = configuration in {"default", "explicit_zero"}
            assert result.published is eligible, result.to_sanitized_dict()
            if eligible:
                from api_service.migrations.account_source import source_user_ids

                assert await source_user_ids(session) == []
                assert result.decision.principal_count == 0
                assert result.transforms["artifacts"]["recovery_grants"] == 0
            else:
                assert result.decision.reason_code == "unknown_principal"
            assert (
                dict((await session.execute(select(table))).mappings().one()) == before
            )
            assert not (
                await session.get(TemporalArtifact, "machine-evidence")
            ).operator_recovery_granted
        if eligible:
            async with maker() as restarted:
                assert (
                    await conversion.run_guarded_upgrade(
                        restarted,
                        operator_authorized=True,
                    )
                ).published


async def test_recovery_schema_downgrade_preserves_published_grants(tmp_path):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from api_service.services.single_user_conversion import completed_guarded_upgrade

    migration = importlib.import_module(
        "api_service.migrations.versions.396_artifact_operator_recovery"
    )
    lookup_migration = importlib.import_module(
        "api_service.migrations.versions.401_artifact_recovery_lookup_indexes"
    )

    def migrate(connection, operation):
        with Operations.context(MigrationContext.configure(connection)):
            operation()

    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            connection = await session.connection()
            # No conversion has run, so an ordinary schema rollback is safe.
            await connection.run_sync(migrate, lookup_migration.downgrade)
            await connection.run_sync(migrate, migration.downgrade)
            await connection.run_sync(migrate, migration.upgrade)
            await connection.run_sync(migrate, lookup_migration.upgrade)
            await session.commit()
            assert (await conversion.run_guarded_upgrade(session, operator_authorized=True)).published
        async with maker() as session:
            connection = await session.connection()
            with pytest.raises(RuntimeError, match="recovery grants"):
                await connection.run_sync(migrate, migration.downgrade)
            await session.rollback()
        async with maker() as restarted:
            assert (await restarted.get(TemporalArtifact, artifact_id)).operator_recovery_granted
            assert await completed_guarded_upgrade(restarted) is not None


async def test_admission_observes_restored_artifacts_and_revoked_recovery(
    tmp_path, monkeypatch
):
    from fastapi import HTTPException, Request
    from sqlalchemy import update

    from api_service.auth_providers import get_instance_access

    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "")
    incoming = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/schedules",
            "query_string": b"",
            "headers": [(b"host", b"localhost:7000")],
            "client": ("127.0.0.1", 43210),
            "server": ("api", 8000),
        }
    )
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session, tmp_path)
            assert (
                await conversion.run_guarded_upgrade(session, operator_authorized=True)
            ).published
            assert await get_instance_access(incoming, session) is not None
            session.add(
                TemporalArtifact(
                    artifact_id="restored-later",
                    storage_key="restored-later",
                    created_by_principal=owner,
                )
            )
            await session.commit()
            with pytest.raises(HTTPException) as refused:
                await get_instance_access(incoming, session)
            assert refused.value.status_code == 503
            assert refused.value.detail == "conversion_required"
            assert not (
                await session.get(TemporalArtifact, "restored-later")
            ).operator_recovery_granted
            assert (
                await conversion.run_guarded_upgrade(session, operator_authorized=True)
            ).published
            assert await get_instance_access(incoming, session) is not None
            assert (
                await session.get(TemporalArtifact, "restored-later")
            ).operator_recovery_granted
            await session.execute(
                update(TemporalArtifact)
                .where(TemporalArtifact.artifact_id == artifact_id)
                .values(operator_recovery_granted=False)
            )
            await session.commit()
        async with maker() as restarted:
            with pytest.raises(HTTPException) as refused:
                await get_instance_access(incoming, restarted)
            assert refused.value.detail == "conversion_required"
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service(restarted, tmp_path).read(
                    artifact_id=artifact_id, access=access_for_test("operator")
                )


@pytest.mark.parametrize("authority", ["expired_claim", "pin", "deletion_intent"])
@pytest.mark.parametrize("historical_actor", ["unknown_uuid", "legacy-actor-label"])
async def test_historical_child_provenance_does_not_block_recovery(
    tmp_path, authority, historical_actor,
):
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            actor = str(uuid4()) if historical_actor == "unknown_uuid" else historical_actor
            if authority == "pin":
                model, field = TemporalArtifactPin, "pinned_by_principal"
            elif authority == "deletion_intent":
                model, field = TemporalArtifactDeletionIntent, "initiated_by_principal"
            else:
                model, field = TemporalArtifactUseClaim, "owner_principal"
            row = (await session.execute(select(model))).scalar_one()
            setattr(row, field, actor)
            if authority == "expired_claim":
                row.expires_at = datetime.now(UTC) - timedelta(days=1)
            await session.commit()
            before = dict((await session.execute(select(model.__table__))).mappings().one())
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert result.published, result.to_sanitized_dict()
            after = dict((await session.execute(select(model.__table__))).mappings().one())
            assert after == before
            assert (
                await service(session, tmp_path).read(
                    artifact_id=artifact_id,
                    access=access_for_test("operator", recovery_principal="operator"),
                )
            )[1] == b"retained bytes"
