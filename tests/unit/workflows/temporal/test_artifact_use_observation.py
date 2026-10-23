"""Best-effort consumer observations must not damage the caller transaction.

Failures come from SQLite triggers executing real INSERT/DELETE statements;
the repository, session, authorization, and filesystem reads remain real.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from api_service.db.models import (
    TemporalArtifactLink,
    TemporalArtifactRedactionLevel,
    TemporalArtifactUseClaim,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactStateError,
    TemporalArtifactValidationError,
)
from tests.unit.workflows.temporal.test_saved_work_retention_4017 import (
    _complete_artifact,
    _service,
    temporal_db,
)

pytestmark = [pytest.mark.asyncio]

_OWNER = "saved-work-owner"
_READER = "service:artifact-consumer"
_PAYLOAD = b"recoverable saved work"
_INSERT_FAILURE = "injected consumer claim INSERT failure"
_DELETE_FAILURE = "injected consumer claim DELETE failure"
_LOGGER = "moonmind.workflows.temporal.artifacts"
_READ_SURFACES = ("read", "read_path", "read_chunks", "presign_download")


async def _fail_claim_statement(sessions, statement: str) -> str:
    """Install a committed DB failure before the caller opens its transaction."""
    assert statement in {"INSERT", "DELETE"}
    cause = _INSERT_FAILURE if statement == "INSERT" else _DELETE_FAILURE
    async with sessions.kw["bind"].begin() as connection:
        await connection.execute(
            text(
                f"CREATE TRIGGER fail_consumer_claim_{statement.lower()} "
                f"BEFORE {statement} ON temporal_artifact_use_claims "
                f"BEGIN SELECT RAISE(ABORT, '{cause}'); END"
            )
        )
    return cause


def _caller_link(artifact_id: str, label: str) -> TemporalArtifactLink:
    return TemporalArtifactLink(
        id=uuid4(),
        artifact_id=artifact_id,
        namespace="caller-work",
        workflow_id="unrelated-caller-workflow",
        run_id="unrelated-caller-run",
        link_type="input.test",
        label=label,
    )


async def _add_uncommitted_caller_work(session, artifact_id: str):
    flushed = _caller_link(artifact_id, "already flushed")
    session.add(flushed)
    await session.flush()
    pending = _caller_link(artifact_id, "still pending")
    session.add(pending)
    assert pending in session.new
    return flushed, pending


async def _assert_caller_work_preserved(session, sessions, rows) -> None:
    """Neither commit nor rollback belongs to a failed observation."""
    row_ids = {row.id for row in rows}
    statement = select(TemporalArtifactLink.id).where(
        TemporalArtifactLink.id.in_(row_ids)
    )
    assert session.is_active
    # A fresh connection cannot see the caller's uncommitted work.
    async with sessions() as observer:
        assert (await observer.scalars(statement)).all() == []
    # This also proves the caller can keep issuing SQL after the failure.
    assert set((await session.scalars(statement)).all()) == row_ids
    await session.commit()
    async with sessions() as observer:
        assert set((await observer.scalars(statement)).all()) == row_ids


async def _claims(sessions, artifact_id: str):
    async with sessions() as observer:
        return await TemporalArtifactRepository(observer).list_use_claims(artifact_id)


async def _serve(service, surface: str, artifact_id: str, **overrides):
    arguments = {
        "artifact_id": artifact_id,
        "principal": _READER,
        "admitted_principal": _OWNER,
        **overrides,
    }
    result = await getattr(service, surface)(**arguments)
    if surface == "read":
        artifact, payload = result
        assert payload == _PAYLOAD
    elif surface == "read_path":
        artifact, path = result
        assert path.read_bytes() == _PAYLOAD
    elif surface == "read_chunks":
        artifact, chunks = result
        assert b"".join(chunks) == _PAYLOAD
    else:
        artifact, expires_at, url = result
        assert expires_at > datetime.now(UTC)
        assert url == f"/api/artifacts/{artifact_id}/download"
    assert artifact.artifact_id == artifact_id
    return result


@pytest.mark.parametrize("surface", _READ_SURFACES)
async def test_failed_acquisition_preserves_authorized_read_and_caller_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog, surface: str
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id
        await _fail_claim_statement(sessions, "INSERT")
        rows = await _add_uncommitted_caller_work(session, artifact_id)

        await _serve(service, surface, artifact_id)

        await _assert_caller_work_preserved(session, sessions, rows)
        assert await _claims(sessions, artifact_id) == []
        assert "observation" in caplog.text
        assert _INSERT_FAILURE in caplog.text


async def test_failed_release_preserves_committed_claim_and_caller_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id
        request_id = "restore-original-operation"
        expiry = datetime.now(UTC) + timedelta(minutes=5)
        await service._repository.acquire_use_claim(
            artifact_id=artifact_id,
            owner_principal=_OWNER,
            request_id=request_id,
            operation_kind="restore",
            expires_at=expiry,
        )
        await session.commit()
        original_claim = (await _claims(sessions, artifact_id))[0]
        await _fail_claim_statement(sessions, "DELETE")
        rows = await _add_uncommitted_caller_work(session, artifact_id)

        await service._release_observed_use(
            artifact_id=artifact_id,
            principal=_READER,
            admitted_principal=_OWNER,
            request_id=request_id,
        )

        await _assert_caller_work_preserved(session, sessions, rows)
        claims = await _claims(sessions, artifact_id)
        assert len(claims) == 1
        assert claims[0].id == original_claim.id
        assert claims[0].expires_at == original_claim.expires_at
        assert claims[0].request_id == request_id
        assert claims[0].owner_principal == _OWNER
        assert "observation" in caplog.text
        assert _DELETE_FAILURE in caplog.text


@pytest.mark.parametrize("surface", ("read", "read_path"))
async def test_failed_release_still_returns_bounded_read_and_ttl_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog, surface: str
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id
        await _fail_claim_statement(sessions, "DELETE")
        started_at = datetime.now(UTC)

        await _serve(service, surface, artifact_id)

        assert await session.scalar(select(TemporalArtifactUseClaim.id))
        claims = await _claims(sessions, artifact_id)
        assert len(claims) == 1
        assert claims[0].owner_principal == _OWNER
        assert claims[0].operation_kind == "restore"
        expiry = claims[0].expires_at.replace(tzinfo=UTC)
        assert started_at + timedelta(seconds=300) <= expiry
        assert expiry <= datetime.now(UTC) + timedelta(seconds=300)
        assert _DELETE_FAILURE in caplog.text


async def test_failed_acquisition_does_not_break_safe_preview_derivation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(
            service,
            principal=_OWNER,
            payload=b"token=secret-value safe summary",
            content_type="text/plain",
            redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
        )
        artifact_id = artifact.artifact_id
        # Completion derived a preview already; require a new real derivation.
        metadata = dict(artifact.metadata_json)
        metadata.pop("preview_artifact_id")
        artifact.metadata_json = metadata
        await session.commit()
        await _fail_claim_statement(sessions, "INSERT")

        preview_ref = await service.compute_preview(
            artifact_id=artifact_id,
            principal=_READER,
            admitted_principal=_OWNER,
        )

        preview = await service._repository.get_artifact(preview_ref.artifact_id)
        payload = service._store.read_bytes(preview.storage_key)
        assert payload == b"token=[REDACTED] safe summary"
        assert preview.redaction_level is TemporalArtifactRedactionLevel.PREVIEW_ONLY
        assert await _claims(sessions, artifact_id) == []
        assert _INSERT_FAILURE in caplog.text


async def test_caller_preflush_failure_is_not_swallowed_as_observation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller's failed write is a real error, outside best-effort accounting."""
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id
        async with sessions.kw["bind"].begin() as connection:
            await connection.execute(
                text(
                    "CREATE TRIGGER fail_caller_preflush "
                    "BEFORE INSERT ON temporal_artifact_links "
                    "WHEN NEW.label = 'still pending' "
                    "BEGIN SELECT RAISE(ABORT, 'caller preflush failed'); END"
                )
            )
        await _add_uncommitted_caller_work(session, artifact_id)

        with pytest.raises(IntegrityError, match="caller preflush failed"):
            await _serve(service, "read", artifact_id)

        assert await _claims(sessions, artifact_id) == []


async def test_outer_commit_failure_is_not_reported_as_successful_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown commit outcomes cannot be treated as a skipped observation."""
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id
        rows = await _add_uncommitted_caller_work(session, artifact_id)

        def fail_outer_commit(sync_session):
            if not sync_session.in_nested_transaction():
                raise RuntimeError("caller outer commit failed")

        event.listen(session.sync_session, "before_commit", fail_outer_commit)
        try:
            with pytest.raises(RuntimeError, match="caller outer commit failed"):
                await _serve(service, "read", artifact_id)
        finally:
            event.remove(session.sync_session, "before_commit", fail_outer_commit)
        await _assert_caller_work_preserved(session, sessions, rows)


async def test_failed_release_does_not_hide_original_storage_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id
        service._store.read_path(artifact.storage_key).unlink()
        await _fail_claim_statement(sessions, "DELETE")

        with pytest.raises(
            TemporalArtifactStateError, match="bytes are missing"
        ) as failure:
            await _serve(service, "read", artifact_id)

        assert isinstance(failure.value.__cause__, FileNotFoundError)
        assert await session.scalar(select(TemporalArtifactUseClaim.id))
        claims = await _claims(sessions, artifact_id)
        assert len(claims) == 1
        assert claims[0].owner_principal == _OWNER
        assert _DELETE_FAILURE in caplog.text


@pytest.mark.parametrize("surface", _READ_SURFACES)
async def test_successful_observation_retains_existing_visibility_and_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id

        await _serve(service, surface, artifact_id)

        claims = await _claims(sessions, artifact_id)
        if surface in {"read", "read_path"}:
            assert claims == []
        else:
            assert len(claims) == 1
            assert claims[0].owner_principal == _OWNER
            assert claims[0].expires_at.replace(tzinfo=UTC) > datetime.now(UTC)


@pytest.mark.parametrize("surface", _READ_SURFACES)
@pytest.mark.parametrize("denial", ("wrong_scope", "restricted_raw"))
async def test_observation_failure_never_weakens_read_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
    surface: str,
    denial: str,
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(
            service,
            principal=_OWNER,
            payload=_PAYLOAD,
            redaction_level=(
                TemporalArtifactRedactionLevel.RESTRICTED
                if denial == "restricted_raw"
                else TemporalArtifactRedactionLevel.NONE
            ),
        )
        artifact_id = artifact.artifact_id
        await _fail_claim_statement(sessions, "INSERT")

        with pytest.raises(TemporalArtifactAuthorizationError):
            await _serve(
                service,
                surface,
                artifact_id,
                # The instance operator has metadata visibility, but no
                # grant for another owner's restricted raw bytes.
                admitted_principal=(
                    "operator" if denial == "restricted_raw" else "wrong-scope"
                ),
            )

        assert await _claims(sessions, artifact_id) == []
        assert "observation" not in caplog.text


async def test_claim_trigger_aborting_caller_transaction_is_never_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """RAISE(ROLLBACK) loses the outer transaction, even if ORM flags stay active."""
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id
        async with sessions.kw["bind"].begin() as connection:
            await connection.execute(
                text(
                    "CREATE TRIGGER abort_caller_transaction "
                    "BEFORE INSERT ON temporal_artifact_use_claims "
                    "BEGIN SELECT RAISE(ROLLBACK, "
                    "'claim trigger aborted caller transaction'); END"
                )
            )
        await _add_uncommitted_caller_work(session, artifact_id)

        with pytest.raises(
            Exception, match="aborted caller transaction|no such savepoint"
        ):
            await _serve(service, "read", artifact_id)

        assert "observation skipped" not in caplog.text
        assert await _claims(sessions, artifact_id) == []


@pytest.mark.parametrize("failure_kind", ("non_database", "cancelled", "invalidated"))
async def test_unrecoverable_claim_execution_error_propagates_without_losing_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog, failure_kind: str
) -> None:
    """Real SQL-execution events must not convert cancellation/control loss to success."""
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id
        rows = await _add_uncommitted_caller_work(session, artifact_id)
        if failure_kind == "cancelled":
            failure = asyncio.CancelledError("claim execution cancelled")
        elif failure_kind == "invalidated":
            # Inject the SQLAlchemy driver's invalidation signal at the real
            # execution boundary; this does not claim to simulate a network loss.
            failure = DBAPIError(
                "INSERT INTO temporal_artifact_use_claims",
                {},
                RuntimeError("claim connection invalidated"),
                connection_invalidated=True,
            )
        else:
            failure = RuntimeError("claim execution programming error")

        def fail_claim_execution(
            connection, cursor, statement, parameters, context, many
        ):
            if (
                statement.lstrip()
                .upper()
                .startswith("INSERT INTO TEMPORAL_ARTIFACT_USE_CLAIMS")
            ):
                raise failure

        engine = sessions.kw["bind"].sync_engine
        event.listen(engine, "before_cursor_execute", fail_claim_execution)
        try:
            with pytest.raises(type(failure)) as caught:
                await _serve(service, "read", artifact_id)
        finally:
            event.remove(engine, "before_cursor_execute", fail_claim_execution)

        assert caught.value is failure
        assert "observation skipped" not in caplog.text
        await _assert_caller_work_preserved(session, sessions, rows)
        assert await _claims(sessions, artifact_id) == []


@pytest.mark.parametrize(
    ("phase", "statement_prefix"),
    (
        ("setup", "SAVEPOINT "),
        ("release", "RELEASE SAVEPOINT "),
        ("rollback", "ROLLBACK TO SAVEPOINT "),
    ),
)
async def test_physical_savepoint_control_errors_never_report_read_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
    phase: str,
    statement_prefix: str,
) -> None:
    """A real SQLite error at a control statement is not a skipped claim write."""
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id
        if phase == "rollback":
            await _fail_claim_statement(sessions, "INSERT")
        await _add_uncommitted_caller_work(session, artifact_id)
        missing_function = f"missing_observation_{phase}_control"

        def fail_control(connection, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith(statement_prefix):
                # Execute an actual failing driver operation, rather than
                # replacing a repository or transaction helper with a mock.
                cursor.execute(f"SELECT {missing_function}()")

        engine = sessions.kw["bind"].sync_engine
        event.listen(engine, "before_cursor_execute", fail_control)
        try:
            with pytest.raises(Exception, match=missing_function):
                await _serve(service, "read", artifact_id)
        finally:
            event.remove(engine, "before_cursor_execute", fail_control)

        assert "observation skipped" not in caplog.text
        assert await _claims(sessions, artifact_id) == []


async def test_successful_claim_is_visible_to_independent_reader_before_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serving starts after durable admission; bounded-read completion releases it."""
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id
        loop = asyncio.get_running_loop()
        seen = []
        rows = await _add_uncommitted_caller_work(session, artifact_id)
        row_ids = {row.id for row in rows}

        async def independent_snapshot():
            async with sessions() as observer:
                claims = await TemporalArtifactRepository(observer).list_use_claims(
                    artifact_id
                )
                caller_ids = set(
                    (
                        await observer.scalars(
                            select(TemporalArtifactLink.id).where(
                                TemporalArtifactLink.id.in_(row_ids)
                            )
                        )
                    ).all()
                )
                return claims, caller_ids

        class ObservedStore(LocalTemporalArtifactStore):
            def read_bytes(self, storage_key: str) -> bytes:
                # read() runs this filesystem callback in its executor thread,
                # while the original event loop services a separate DB session.
                claims, committed_caller_ids = asyncio.run_coroutine_threadsafe(
                    independent_snapshot(), loop
                ).result(timeout=10)
                seen.extend(claims)
                assert len(claims) == 1
                assert claims[0].owner_principal == _OWNER
                assert committed_caller_ids == row_ids
                return super().read_bytes(storage_key)

        service._store = ObservedStore(tmp_path / "artifacts")

        await _serve(service, "read", artifact_id)

        assert len(seen) == 1
        assert await _claims(sessions, artifact_id) == []


async def test_explicit_claim_retry_extends_but_never_shortens_loaded_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id
        arguments = {
            "artifact_id": artifact_id,
            "principal": _OWNER,
            "request_id": "explicit-restoration",
            "operation_kind": "restore",
        }
        original = await service.acquire_saved_work_use(**arguments, ttl_seconds=600)
        original_id = original.id
        original_expiry = original.expires_at.replace(tzinfo=UTC)

        retry = await service.acquire_saved_work_use(**arguments, ttl_seconds=60)
        assert retry.id == original_id
        assert retry.expires_at.replace(tzinfo=UTC) == original_expiry

        extended = await service.acquire_saved_work_use(**arguments, ttl_seconds=1200)
        extended_expiry = extended.expires_at.replace(tzinfo=UTC)
        assert extended.id == original_id
        assert extended_expiry > original_expiry
        # An already loaded claim must reflect the successful UPDATE result.
        assert original.expires_at.replace(tzinfo=UTC) == extended_expiry

        shorter_retry = await service.acquire_saved_work_use(
            **arguments, ttl_seconds=60
        )
        assert shorter_retry.id == original_id
        assert shorter_retry.expires_at.replace(tzinfo=UTC) == extended_expiry
        claims = await _claims(sessions, artifact_id)
        assert len(claims) == 1
        assert claims[0].id == original_id
        assert claims[0].expires_at.replace(tzinfo=UTC) == extended_expiry


async def test_failed_claim_extension_preserves_loaded_expiry_and_caller_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id
        request_id = "existing-operation"
        original = await service.acquire_saved_work_use(
            artifact_id=artifact_id,
            principal=_OWNER,
            request_id=request_id,
            operation_kind="restore",
            ttl_seconds=300,
        )
        original_id = original.id
        original_expiry = original.expires_at.replace(tzinfo=UTC)
        async with sessions.kw["bind"].begin() as connection:
            await connection.execute(
                text(
                    "CREATE TRIGGER fail_consumer_claim_extension "
                    "BEFORE UPDATE ON temporal_artifact_use_claims "
                    "BEGIN SELECT RAISE(ABORT, 'claim extension UPDATE failed'); END"
                )
            )
        rows = await _add_uncommitted_caller_work(session, artifact_id)
        outer, savepoint = await service._repository.begin_use_claim_savepoint()

        with pytest.raises(IntegrityError, match="claim extension UPDATE failed"):
            await service._repository.acquire_use_claim(
                artifact_id=artifact_id,
                owner_principal=_OWNER,
                request_id=request_id,
                operation_kind="restore",
                expires_at=original_expiry + timedelta(minutes=10),
            )
        await savepoint.rollback()

        assert outer.is_active
        assert original.id == original_id
        assert original.expires_at.replace(tzinfo=UTC) == original_expiry
        await _assert_caller_work_preserved(session, sessions, rows)
        claims = await _claims(sessions, artifact_id)
        assert len(claims) == 1
        assert claims[0].id == original_id
        assert claims[0].expires_at.replace(tzinfo=UTC) == original_expiry


@pytest.mark.parametrize("surface", ("read", "read_path", "compute_preview"))
@pytest.mark.parametrize("primary_kind", ("missing", "invalid", "cancelled", "none"))
@pytest.mark.parametrize(
    "release_kind", ("non_database", "invalidated", "control", "cancelled")
)
async def test_release_failure_preserves_primary_serving_failure(
    tmp_path, monkeypatch, caplog, surface, primary_kind, release_kind
):
    """Real release SQL errors retain the read error, including cancellation."""
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await _complete_artifact(service, principal=_OWNER, payload=_PAYLOAD)
        artifact_id = artifact.artifact_id
        primary = (
            TemporalArtifactValidationError("invalid artifact path")
            if primary_kind == "invalid" else asyncio.CancelledError("serving cancelled")
        )
        if primary_kind == "missing":
            service._store.read_path(artifact.storage_key).unlink()
        elif primary_kind in {"invalid", "cancelled"}:
            def fail_storage(_key):
                raise primary
            monkeypatch.setattr(
                service._store, "read_path" if surface == "read_path" else "read_bytes",
                fail_storage,
            )
        cleanup = (
            asyncio.CancelledError("release cancelled") if release_kind == "cancelled"
            else DBAPIError("DELETE", {}, RuntimeError("release invalidated"), connection_invalidated=True)
            if release_kind == "invalidated"
            else RuntimeError("release programming error")
        )
        releasing = False
        def fail_release(connection, cursor, statement, parameters, context, many):
            nonlocal releasing
            normalized = statement.lstrip().upper()
            if normalized.startswith("DELETE FROM TEMPORAL_ARTIFACT_USE_CLAIMS"):
                releasing = True
                if release_kind != "control":
                    raise cleanup
            if releasing and release_kind == "control" and normalized.startswith("RELEASE SAVEPOINT "):
                cursor.execute("SELECT missing_release_control()")
        engine = sessions.kw["bind"].sync_engine
        event.listen(engine, "before_cursor_execute", fail_release)
        try:
            expected = (
                TemporalArtifactValidationError if primary_kind == "invalid" and surface == "read_path"
                else TemporalArtifactStateError if primary_kind in {"missing", "invalid"}
                else asyncio.CancelledError if primary_kind == "cancelled"
                else sqlite3.OperationalError if release_kind == "control"
                else DBAPIError if release_kind == "invalidated"
                else type(cleanup)
            )
            with pytest.raises(expected) as caught:
                await getattr(service, surface)(
                    artifact_id=artifact_id, principal=_READER, admitted_principal=_OWNER,
                )
        finally:
            event.remove(engine, "before_cursor_execute", fail_release)
        if primary_kind == "missing":
            assert "bytes are missing" in str(caught.value)
            if surface == "read_path":
                assert caught.value.__cause__ is None
            else:
                assert isinstance(caught.value.__cause__, FileNotFoundError)
        elif primary_kind == "invalid":
            assert (caught.value if surface == "read_path" else caught.value.__cause__) is primary
        elif primary_kind == "cancelled":
            assert caught.value is primary
        elif release_kind != "control":
            assert caught.value is cleanup
        if primary_kind != "none":
            assert "consumer-use release also failed" in " ".join(caught.value.__notes__)
            assert "release failed while preserving serving failure" in caplog.text
        else:
            assert "preserving serving failure" not in caplog.text
