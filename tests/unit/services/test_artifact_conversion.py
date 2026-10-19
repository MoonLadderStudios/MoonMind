"""Real guarded artifact conversion and retained recovery boundaries."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base, TemporalArtifact, TemporalArtifactDeletionIntent, TemporalArtifactLink,
    TemporalArtifactPin, TemporalArtifactRedactionLevel, TemporalArtifactUseClaim,
    User, WorkflowArtifact, WorkflowArtifactType, WorkflowRun,
)
from api_service.services import single_user_conversion as conversion
from moonmind.security import auth_modes_4120
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore, TemporalArtifactAuthorizationError,
    TemporalArtifactRepository, TemporalArtifactService, TemporalArtifactStateError,
)

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/conversion.db")
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
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


async def test_guarded_conversion_preserves_recovery_and_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "oidc")
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session, tmp_path)
            artifacts = service(session, tmp_path)
            with pytest.raises(TemporalArtifactAuthorizationError):
                await artifacts.read(artifact_id=artifact_id, principal="operator")
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert result.published, result.to_sanitized_dict()
        # A new service/session represents restart, without an account lookup.
        async with maker() as session:
            artifacts = service(session, tmp_path)
            for principal, admitted in [("operator", None), (owner, None),
                                        ("service:restore", owner),
                                        ("service:restore", "operator")]:
                row, payload = await artifacts.read(
                    artifact_id=artifact_id, principal=principal,
                    admitted_principal=admitted,
                )
                assert payload == b"retained bytes"
                assert row.created_by_principal == owner
            _row, path = await artifacts.read_path(artifact_id=artifact_id, principal="operator")
            assert path.read_bytes() == b"retained bytes"
            _row, chunks = await artifacts.read_chunks(artifact_id=artifact_id, principal="operator")
            assert b"".join(chunks) == b"retained bytes"
            _row, expires_at, url = await artifacts.presign_download(
                artifact_id=artifact_id, principal="operator")
            assert expires_at > datetime.now(UTC)
            assert url == f"/api/artifacts/{artifact_id}/download"
            _row, _links, _pin, policy = await artifacts.get_metadata(
                artifact_id=artifact_id, principal="operator")
            assert policy.raw_access_allowed
            for principal in ["service:restore", "workflow:unrelated", str(uuid4())]:
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await artifacts.read(artifact_id=artifact_id, principal=principal)
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
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "oidc")
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
                        await method(artifact_id=artifact_id, principal=principal)
            _row, _links, _pin, policy = await artifacts.get_metadata(
                artifact_id=artifact_id, principal="operator")
            assert policy.raw_access_allowed is False


async def test_unsupported_workflow_parent_refuses_without_artifact_mutation(tmp_path):
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session, tmp_path)
            run = WorkflowRun(feature_key="retained", requested_by_user_id=UUID(owner))
            session.add(run)
            await session.flush()
            session.add(WorkflowArtifact(workflow_run_id=run.id,
                artifact_type=WorkflowArtifactType.CODEX_LOGS, path="retained.txt"))
            await session.commit()
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert result.published is False
            assert result.decision.reason_code == "missing_transform_coverage"
            assert "workflows" in result.decision.detail
            assert not getattr(await session.get(TemporalArtifact, artifact_id),
                               "operator_recovery_granted", False)
            assert not (await session.execute(select(conversion.SingleUserConversionRun))).scalars().all()


async def test_unknown_active_claim_authority_refuses(tmp_path):
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            claim = (await session.execute(select(TemporalArtifactUseClaim))).scalar_one()
            claim.owner_principal = str(uuid4())
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


async def test_old_published_ledger_does_not_admit_until_real_conversion(tmp_path):
    from api_service.auth_providers import _disabled_account_free_allowed
    async with database(tmp_path) as maker:
        async with maker() as session:
            await seed(session, tmp_path)
            bound = await conversion.preflight(session)
            # Historical completion is a negative fixture, never proof of success.
            session.add(conversion.SingleUserConversionRun(
                preflight_digest=conversion.compute_digest(bound.fingerprint, bound.options,
                    schema_revision="386_single_user_conversion_4346"),
                status="complete", result_json={"published": True,
                    "decision": {"disposition": "eligible_conversion"}},
            ))
            await session.commit()
            assert await _disabled_account_free_allowed(session) is False
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert result.published, result.to_sanitized_dict()
            assert await _disabled_account_free_allowed(session) is True


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
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "oidc")
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
            assert (await artifacts.read(artifact_id=artifact_id, principal="operator"))[1] == b"retained bytes"
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


@pytest.mark.parametrize("machine", ["workflow:retained", "system:agent_runtime", "agent_workflow"])
async def test_machine_creator_and_caller_metadata_cannot_grant_recovery(tmp_path, monkeypatch, machine):
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "oidc")
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
                await service(session, tmp_path).read(artifact_id=artifact_id, principal="operator")


async def test_old_fresh_completion_requires_current_real_guarded_upgrade(tmp_path):
    from api_service.auth_providers import _disabled_account_free_allowed
    async with database(tmp_path) as maker:
        async with maker() as session:
            session.add(conversion.SingleUserConversionRun(
                preflight_digest="old-fresh", status="complete",
                result_json={"published": True, "decision": {"disposition": "fresh_init"}},
            ))
            await session.commit()
            assert not await _disabled_account_free_allowed(session)
            assert (await conversion.run_guarded_upgrade(session, operator_authorized=True)).published
        async with maker() as restarted:
            assert await _disabled_account_free_allowed(restarted)


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
            assert not await artifacts._artifact_raw_access_allowed(row, principal=owner)
            with pytest.raises(TemporalArtifactStateError):
                await artifacts._assert_artifact_raw_access(row, principal=owner)
            linked.assert_not_awaited()


async def test_live_machine_temporal_parent_requires_owned_transform(tmp_path):
    from api_service.db.models import TemporalExecutionRecord, TemporalExecutionOwnerType, TemporalWorkflowType
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
            assert not result.published
            assert result.decision.reason_code == "missing_transform_coverage"
            assert "temporal" in result.decision.detail
            assert not (await session.get(TemporalArtifact, artifact_id)).operator_recovery_granted


async def test_recovery_schema_downgrade_preserves_published_grants(tmp_path):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from api_service.auth_providers import _disabled_account_free_allowed
    migration = importlib.import_module("api_service.migrations.versions.396_artifact_operator_recovery")

    def migrate(connection, operation):
        with Operations.context(MigrationContext.configure(connection)):
            operation()

    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            connection = await session.connection()
            # No conversion has run, so an ordinary schema rollback is safe.
            await connection.run_sync(migrate, migration.downgrade)
            await connection.run_sync(migrate, migration.upgrade)
            await session.commit()
            assert (await conversion.run_guarded_upgrade(session, operator_authorized=True)).published
        async with maker() as session:
            connection = await session.connection()
            with pytest.raises(RuntimeError, match="recovery grants"):
                await connection.run_sync(migrate, migration.downgrade)
            await session.rollback()
        async with maker() as restarted:
            assert (await restarted.get(TemporalArtifact, artifact_id)).operator_recovery_granted
            assert await _disabled_account_free_allowed(restarted)


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
            assert (await service(session, tmp_path).read(
                artifact_id=artifact_id, principal="operator"))[1] == b"retained bytes"
