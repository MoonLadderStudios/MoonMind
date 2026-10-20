"""Consumer claim statement failures on an isolated PostgreSQL schema.

Ordinary trigger exceptions exercise savepoint recovery, not backend loss,
whole-transaction rollback, or uncertain commit outcomes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from api_service.db import models
from moonmind.config.settings import settings
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.support.isolated_postgres import isolated_postgres

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

_OWNER = "saved-work-owner"
_READER = "service:artifact-consumer"
_PAYLOAD = b"recoverable saved work"
_LOGGER = "moonmind.workflows.temporal.artifacts"


@pytest_asyncio.fixture
async def artifact_context(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "accounts")
    tables = [
        models.TemporalArtifact.__table__,
        models.TemporalArtifactLink.__table__,
        models.TemporalArtifactPin.__table__,
        models.TemporalArtifactUseClaim.__table__,
        models.TemporalArtifactDeletionIntent.__table__,
        # Denied reads also check for an owned linked execution.
        models.TemporalExecutionCanonicalRecord.__table__,
        models.TemporalExecutionRecord.__table__,
    ]
    async with isolated_postgres(tables) as sessions, sessions() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        yield sessions, session, service


async def _complete_artifact(service, **kwargs):
    artifact, _ = await service.create(
        principal=_OWNER, content_type="application/octet-stream", **kwargs
    )
    return await service.write_complete(
        artifact_id=artifact.artifact_id,
        principal=_OWNER,
        payload=_PAYLOAD,
        content_type="application/octet-stream",
    )


async def _fail_claim_statement(sessions, statement):
    assert statement in {"INSERT", "DELETE"}
    cause = f"injected consumer claim {statement} failure"
    # The harness search_path confines both objects to its disposable schema;
    # teardown drops that schema, including this function and trigger.
    async with sessions.kw["bind"].begin() as connection:
        await connection.execute(
            text(
                "CREATE FUNCTION fail_consumer_claim() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                f"RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = '{cause}'; "
                "END; $$"
            )
        )
        await connection.execute(
            text(
                f"CREATE TRIGGER fail_consumer_claim BEFORE {statement} "
                "ON temporal_artifact_use_claims FOR EACH ROW "
                "EXECUTE FUNCTION fail_consumer_claim()"
            )
        )
    return cause


async def _add_caller_work(session, artifact_id):
    rows = tuple(
        models.TemporalArtifactLink(
            id=uuid4(),
            artifact_id=artifact_id,
            namespace="caller-work",
            workflow_id="unrelated-workflow",
            run_id="unrelated-run",
            link_type="input.test",
            label=label,
        )
        for label in ("already flushed", "still pending")
    )
    session.add(rows[0])
    await session.flush()
    session.add(rows[1])
    assert rows[1] in session.new
    return rows, session.get_transaction()


async def _assert_caller_work_preserved(sessions, session, rows, outer):
    assert session.is_active
    assert session.get_transaction() is outer
    assert outer.is_active
    assert [row.label for row in rows] == ["already flushed", "still pending"]
    row_ids = {row.id for row in rows}
    statement = select(models.TemporalArtifactLink.id).where(
        models.TemporalArtifactLink.id.in_(row_ids)
    )
    # No outer commit occurred: a separate connection cannot see either row.
    async with sessions() as observer:
        assert (await observer.scalars(statement)).all() == []
    # No outer rollback occurred, and the caller can still issue SQL and commit.
    assert set((await session.scalars(statement)).all()) == row_ids
    await session.commit()
    async with sessions() as observer:
        assert set((await observer.scalars(statement)).all()) == row_ids


async def _claims(sessions, artifact_id):
    async with sessions() as observer:
        return await TemporalArtifactRepository(observer).list_use_claims(artifact_id)


async def _read(service, artifact, **kwargs):
    loaded, payload = await service.read(
        artifact_id=artifact.artifact_id,
        principal=_READER,
        admitted_principal=_OWNER,
        **kwargs,
    )
    assert loaded is artifact
    assert payload == _PAYLOAD
    assert loaded.status is models.TemporalArtifactStatus.COMPLETE
    assert loaded.created_by_principal == _OWNER
    assert loaded.operator_recovery_granted is False
    assert loaded.size_bytes == len(_PAYLOAD)
    assert service._store.read_path(loaded.storage_key).read_bytes() == _PAYLOAD


class _ReadBoundaryStore(LocalTemporalArtifactStore):
    def __init__(self, root, before_read):
        super().__init__(root)
        self._loop = asyncio.get_running_loop()
        self._before_read = before_read

    def read_bytes(self, storage_key):
        # The actual service read runs in its filesystem executor thread.
        # The event loop can inspect PostgreSQL before any bytes are returned.
        asyncio.run_coroutine_threadsafe(self._before_read(), self._loop).result(
            timeout=10
        )
        return super().read_bytes(storage_key)


async def test_postgres_failed_acquisition_preserves_read_and_caller_work(
    artifact_context, caplog
):
    sessions, session, service = artifact_context
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    artifact = await _complete_artifact(service)
    cause = await _fail_claim_statement(sessions, "INSERT")
    rows, outer = await _add_caller_work(session, artifact.artifact_id)

    await _read(service, artifact)

    await _assert_caller_work_preserved(sessions, session, rows, outer)
    assert await _claims(sessions, artifact.artifact_id) == []
    assert "observation skipped" in caplog.text
    assert cause in caplog.text


async def test_postgres_failed_release_preserves_read_caller_work_and_ttl_claim(
    artifact_context, tmp_path, caplog
):
    sessions, session, service = artifact_context
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    artifact = await _complete_artifact(service)
    cause = await _fail_claim_statement(sessions, "DELETE")
    original_claims = []
    caller_work = []

    async def before_read():
        original_claims.extend(await _claims(sessions, artifact.artifact_id))
        assert len(original_claims) == 1
        # Acquisition has committed. Add caller work immediately before the
        # real bounded read releases its claim, so release must preserve it.
        caller_work.append(await _add_caller_work(session, artifact.artifact_id))

    service._store = _ReadBoundaryStore(tmp_path / "artifacts", before_read)
    started_at = datetime.now(UTC)

    await _read(service, artifact)

    assert len(caller_work) == 1
    rows, outer = caller_work[0]
    await _assert_caller_work_preserved(sessions, session, rows, outer)
    claims = await _claims(sessions, artifact.artifact_id)
    assert len(claims) == 1
    assert claims[0].id == original_claims[0].id
    assert claims[0].expires_at == original_claims[0].expires_at
    assert claims[0].request_id == original_claims[0].request_id
    assert claims[0].owner_principal == _OWNER
    assert claims[0].operation_kind == "restore"
    assert started_at + timedelta(seconds=300) <= claims[0].expires_at
    assert claims[0].expires_at <= datetime.now(UTC) + timedelta(seconds=300)
    assert "observation release skipped" in caplog.text
    assert cause in caplog.text


async def test_postgres_successful_claim_is_visible_before_bytes_and_released(
    artifact_context, tmp_path
):
    sessions, _session, service = artifact_context
    artifact = await _complete_artifact(service)
    observed = []

    async def before_read():
        observed.extend(await _claims(sessions, artifact.artifact_id))
        assert len(observed) == 1
        assert observed[0].owner_principal == _OWNER
        assert observed[0].operation_kind == "restore"
        assert observed[0].expires_at > datetime.now(UTC)

    service._store = _ReadBoundaryStore(tmp_path / "artifacts", before_read)

    await _read(service, artifact)

    assert len(observed) == 1
    assert await _claims(sessions, artifact.artifact_id) == []


@pytest.mark.parametrize("denial", ["wrong_scope", "restricted_raw"])
async def test_postgres_claim_failure_does_not_grant_read_access(
    artifact_context, caplog, denial
):
    sessions, _session, service = artifact_context
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    artifact = await _complete_artifact(
        service,
        redaction_level=(
            models.TemporalArtifactRedactionLevel.RESTRICTED
            if denial == "restricted_raw"
            else models.TemporalArtifactRedactionLevel.NONE
        ),
    )
    cause = await _fail_claim_statement(sessions, "INSERT")

    with pytest.raises(
        TemporalArtifactAuthorizationError,
        match="restricted raw" if denial == "restricted_raw" else "cannot read",
    ):
        await service.read(
            artifact_id=artifact.artifact_id,
            principal=_READER,
            # Operator metadata visibility is insufficient for another
            # owner's restricted raw bytes without a protected recovery grant.
            admitted_principal=(
                "operator" if denial == "restricted_raw" else "wrong-scope"
            ),
            allow_restricted_raw=True,
        )

    assert await _claims(sessions, artifact.artifact_id) == []
    assert artifact.operator_recovery_granted is False
    assert artifact.created_by_principal == _OWNER
    assert "observation" not in caplog.text
    assert cause not in caplog.text
