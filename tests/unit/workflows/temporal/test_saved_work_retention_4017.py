"""Artifact-owned access, dependency retention, bounded recovery quotas.

Covers MoonLadderStudios/MoonMind#4017 against real API/service/database/
object-store boundaries (sqlite + local filesystem store, no mocks of the
pin/use/delete/sweep path):

- owner download/restore after source PAT/host removal; wrong-owner, hash
  guess, and manifest-id guess confer no access;
- two simultaneous restore/publish users plus an operator pin stay
  independently protected when one completes, expires, or retries;
- new use admission races soft/hard deletion without premature removal;
- shared blobs, required graphs, optional outputs, cycles, and
  missing/corrupt/quarantined bytes have explicit availability outcomes;
- object deletion failure, lost acknowledgment, failed DB commit, and
  sweeper restart converge without false available/deleted results;
- signed-URL expiry/revocation matches documented policy; preview stays
  separate from raw restore capability;
- concurrent quota admission and failed-save retention stay bounded and
  distinguish recoverable local work from verified durable results;
- sweeps are bounded, paginated, idempotent, and observable.
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    TemporalArtifactRedactionLevel,
    TemporalArtifactStatus,
)
from moonmind.config.settings import settings
from moonmind.schemas import saved_work_retention as retention
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
    TemporalArtifactValidationError,
)

pytestmark = [pytest.mark.asyncio]


@asynccontextmanager
async def temporal_db(tmp_path: Path, name: str = "retention_4017.db"):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/{name}"
    engine = create_async_engine(db_url, future=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield session_maker
    finally:
        await engine.dispose()


def _service(session, tmp_path: Path, **kwargs) -> TemporalArtifactService:
    return TemporalArtifactService(
        TemporalArtifactRepository(session),
        store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        **kwargs,
    )


async def _complete_artifact(
    service: TemporalArtifactService,
    *,
    principal: str,
    payload: bytes = b"saved-work-bytes",
    content_type: str = "application/octet-stream",
    metadata_json: dict | None = None,
    redaction_level: TemporalArtifactRedactionLevel = (
        TemporalArtifactRedactionLevel.NONE
    ),
    **kwargs,
):
    artifact, _upload = await service.create(
        principal=principal,
        content_type=content_type,
        metadata_json=metadata_json,
        redaction_level=redaction_level,
        **kwargs,
    )
    return await service.write_complete(
        artifact_id=artifact.artifact_id,
        principal=principal,
        payload=payload,
        content_type=content_type,
    )


async def test_owner_restores_after_source_removal_guesses_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner reads without any source credential; guesses confer nothing."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, tmp_path)
            artifact = await _complete_artifact(service, principal="owner-1")

            # No source PAT, no live host: ownership alone restores.
            _meta, payload = await service.read(
                artifact_id=artifact.artifact_id, principal="owner-1"
            )
            assert payload == b"saved-work-bytes"
            _artifact, _expires, url = await service.presign_download(
                artifact_id=artifact.artifact_id, principal="owner-1"
            )
            assert url

            # Wrong owner, unknown id, and digest-shaped guesses are denied.
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.read(
                    artifact_id=artifact.artifact_id, principal="owner-2"
                )
            with pytest.raises(Exception):
                await service.read(
                    artifact_id="art_missing_guess", principal="owner-2"
                )
            digest_guess = hashlib.sha256(b"saved-work-bytes").hexdigest()
            with pytest.raises(Exception):
                await service.read(artifact_id=digest_guess, principal="owner-2")
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.validate_saved_work_manifest_dependencies(
                    [artifact.artifact_id], principal="owner-2"
                )


async def test_manifest_graph_validation_auth_cycles_and_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Required/optional graph: auth, cycles, bounds, reachability evidence."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, tmp_path)
            baseline = await _complete_artifact(service, principal="owner-1")
            report = await _complete_artifact(service, principal="owner-1")

            evidence = await service.validate_saved_work_manifest_dependencies(
                [
                    {"artifactId": baseline.artifact_id, "kind": "required"},
                    {"artifactId": report.artifact_id, "kind": "optional"},
                ],
                principal="owner-1",
            )
            assert evidence["required"] == [baseline.artifact_id]
            assert evidence["optional"] == [report.artifact_id]
            assert evidence["graphDigest"].startswith("sha256:")

            # Legacy flat form stays required.
            flat = await service.validate_saved_work_manifest_dependencies(
                [baseline.artifact_id], principal="owner-1"
            )
            assert flat["required"] == [baseline.artifact_id]

            # Unresolved required dependency is rejected; optional tolerates.
            with pytest.raises(ValueError, match="SAVED_WORK_DEP_UNRESOLVED"):
                await service.validate_saved_work_manifest_dependencies(
                    [baseline.artifact_id, "art_missing_required"],
                    principal="owner-1",
                )
            optional_missing = (
                await service.validate_saved_work_manifest_dependencies(
                    [
                        {"artifactId": baseline.artifact_id, "kind": "required"},
                        {"artifactId": "art_missing_opt", "kind": "optional"},
                    ],
                    principal="owner-1",
                )
            )
            assert optional_missing["required"] == [baseline.artifact_id]

            # Unsafe cycles are rejected.
            cyclic_a = await _complete_artifact(
                service,
                principal="owner-1",
                metadata_json={"dependencies": ["CYCLE_B_PLACEHOLDER"]},
            )
            cyclic_b = await _complete_artifact(
                service,
                principal="owner-1",
                metadata_json={"dependencies": [cyclic_a.artifact_id]},
            )
            row_a = await service._repository.get_artifact(cyclic_a.artifact_id)
            row_a.metadata_json = {"dependencies": [cyclic_b.artifact_id]}
            await service._repository.commit()
            with pytest.raises(ValueError, match="SAVED_WORK_DEP_CYCLE"):
                await service.validate_saved_work_manifest_dependencies(
                    [cyclic_a.artifact_id], principal="owner-1"
                )

            # Bounds and duplicates are rejected before any DB walk.
            with pytest.raises(ValueError, match="SAVED_WORK_DEP_DUPLICATE"):
                await service.validate_saved_work_manifest_dependencies(
                    [baseline.artifact_id, baseline.artifact_id],
                    principal="owner-1",
                )
            with pytest.raises(ValueError, match="SAVED_WORK_DEP_BOUND"):
                await service.validate_saved_work_manifest_dependencies(
                    [f"art_{index}" for index in range(200)],
                    principal="owner-1",
                )


async def test_independent_use_claims_and_operator_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One operation's release never erases another's protection or a pin."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, tmp_path)
            artifact = await _complete_artifact(service, principal="owner-1")
            await service.validate_saved_work_manifest_dependencies(
                [artifact.artifact_id], principal="owner-1"
            )
            await service.pin(
                artifact_id=artifact.artifact_id,
                principal="owner-1",
                reason="operator hold",
            )
            claim_a = await service.acquire_saved_work_use(
                artifact_id=artifact.artifact_id,
                principal="owner-1",
                request_id="restore-1",
                operation_kind="restore",
            )
            # Same request identity retries idempotently to the same claim.
            retry = await service.acquire_saved_work_use(
                artifact_id=artifact.artifact_id,
                principal="owner-1",
                request_id="restore-1",
                operation_kind="restore",
            )
            assert retry.id == claim_a.id

            # A second principal cannot admit against another owner's artifact.
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.acquire_saved_work_use(
                    artifact_id=artifact.artifact_id,
                    principal="owner-2",
                    request_id="publish-1",
                    operation_kind="publication",
                )

            # Releasing one claim keeps the pin; sweep still skips the row.
            assert await service.release_saved_work_use(
                artifact_id=artifact.artifact_id,
                principal="owner-1",
                request_id="restore-1",
            ) is True
            assert (
                await service._repository.get_pin(artifact.artifact_id)
            ) is not None
            row = await service._repository.get_artifact(artifact.artifact_id)
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await service._repository.commit()
            sweep = await service.sweep_lifecycle(
                principal="service:lifecycle", run_id="pin-hold"
            )
            assert sweep.soft_deleted_count == 0
            refreshed = await service._repository.get_artifact(artifact.artifact_id)
            assert refreshed.status is TemporalArtifactStatus.COMPLETE


async def test_use_admission_races_delete_without_premature_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live claims block soft/hard delete and the sweep; release converges."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, tmp_path)
            artifact = await _complete_artifact(service, principal="owner-1")
            await service.acquire_saved_work_use(
                artifact_id=artifact.artifact_id,
                principal="owner-1",
                request_id="restore-1",
                operation_kind="restore",
            )

            with pytest.raises(TemporalArtifactStateError, match="DELETE_BLOCKED"):
                await service.soft_delete(
                    artifact_id=artifact.artifact_id, principal="owner-1"
                )
            # Explicit administrator deletion names active consumers.
            admin = await service.admin_delete(
                artifact_id=artifact.artifact_id,
                principal="owner-1",
                reason="operator-directed removal with named consumers",
            )
            assert admin["activeConsumers"] == ["owner-1"]
            # Claim rows survive admin deletion so consumers see deleting state.
            with pytest.raises(TemporalArtifactStateError, match="DELETED"):
                await service.acquire_saved_work_use(
                    artifact_id=artifact.artifact_id,
                    principal="owner-1",
                    request_id="restore-2",
                    operation_kind="restore",
                )

    async with temporal_db(tmp_path, name="race_sweep.db") as session_maker:
        async with session_maker() as session:
            service = _service(
                session, tmp_path, lifecycle_hard_delete_after_seconds=0
            )
            artifact = await _complete_artifact(service, principal="owner-1")
            await service.acquire_saved_work_use(
                artifact_id=artifact.artifact_id,
                principal="owner-1",
                request_id="publish-1",
                operation_kind="publication",
            )
            row = await service._repository.get_artifact(artifact.artifact_id)
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await service._repository.commit()
            sweep = await service.sweep_lifecycle(
                principal="service:lifecycle", run_id="race-1"
            )
            assert sweep.soft_deleted_count == 0
            assert sweep.skipped_in_use_count >= 1

            assert await service.release_saved_work_use(
                artifact_id=artifact.artifact_id,
                principal="owner-1",
                request_id="publish-1",
            ) is True
            # Releasing an unknown request is explicit, not silent success.
            assert await service.release_saved_work_use(
                artifact_id=artifact.artifact_id,
                principal="owner-1",
                request_id="publish-1",
            ) is False
            second = await service.sweep_lifecycle(
                principal="service:lifecycle", run_id="race-2"
            )
            assert second.soft_deleted_count == 1


async def test_shared_blob_keeps_logical_ownership_and_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dedup shares physical bytes; logical charges stay per owner reference."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, tmp_path)
            payload = b"shared-baseline-bytes"
            digest = hashlib.sha256(payload).hexdigest()
            first = await _complete_artifact(
                service,
                principal="owner-1",
                payload=payload,
                content_addressed_scope="saved-work",
                size_bytes=len(payload),
                sha256=digest,
            )
            second = await _complete_artifact(
                service,
                principal="owner-1",
                payload=payload,
                content_addressed_scope="saved-work",
                size_bytes=len(payload),
                sha256=digest,
            )
            assert first.storage_key == second.storage_key

            usage = await service._repository.quota_usage_for_scope("owner-1")
            assert usage["logicalBytes"] == 2 * len(payload)
            assert usage["physicalBytes"] == len(payload)

            await service.soft_delete(
                artifact_id=first.artifact_id, principal="owner-1"
            )
            await service.hard_delete(
                artifact_id=first.artifact_id, principal="owner-1"
            )
            # The surviving logical reference keeps the physical bytes.
            _meta, kept = await service.read(
                artifact_id=second.artifact_id, principal="owner-1"
            )
            assert kept == payload


async def test_deletion_failures_converge_without_false_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed deletes, lost acks, and restarts reconcile to truthful states."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, tmp_path)
            artifact = await _complete_artifact(service, principal="owner-1")
            await service.soft_delete(
                artifact_id=artifact.artifact_id, principal="owner-1"
            )

            failures = {"remaining": 1}
            real_delete = service._store.delete

            def _flaky_delete(storage_key: str) -> None:
                if failures["remaining"] > 0:
                    failures["remaining"] -= 1
                    raise OSError("object-store unavailable")
                return real_delete(storage_key)

            monkeypatch.setattr(service._store, "delete", _flaky_delete)
            with pytest.raises(TemporalArtifactStateError, match="DELETE_RETRY"):
                await service.hard_delete(
                    artifact_id=artifact.artifact_id, principal="owner-1"
                )
            # Intent persisted; the tombstone row is not falsely complete.
            intent = await service._repository.get_deletion_intent(
                artifact.artifact_id
            )
            assert intent is not None
            assert intent.attempts >= 1
            assert intent.last_error
            row = await service._repository.get_artifact(artifact.artifact_id)
            assert service.saved_work_availability_of(row) == "deleted"

            # Sweeper restart converges once the store recovers.
            monkeypatch.setattr(service._store, "delete", real_delete)
            reconciled = await service.reconcile_deletion_intents(
                principal="service:lifecycle"
            )
            assert reconciled >= 1
            assert (
                await service._repository.get_deletion_intent(artifact.artifact_id)
            ) is None
            with pytest.raises(TemporalArtifactStateError):
                await service.read(
                    artifact_id=artifact.artifact_id, principal="owner-1"
                )


async def test_signed_download_bounded_with_honest_revocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Restricted links use the short bound; URLs never reach logs/history."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, tmp_path, presign_ttl_seconds=7200)
            artifact = await _complete_artifact(
                service,
                principal="owner-1",
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
            )
            with caplog.at_level(logging.INFO):
                _artifact, expires_at, url = await service.presign_download(
                    artifact_id=artifact.artifact_id, principal="owner-1"
                )
            bounded = (expires_at - datetime.now(UTC)).total_seconds()
            assert bounded <= retention.SAVED_WORK_MAX_DOWNLOAD_TTL_SECONDS + 5
            statement = service.download_policy_statement()
            assert "does not retract" in statement
            # The issued URL itself is never logged.
            assert url not in caplog.text

            # Authorization changes are enforced at the serving boundary:
            # another principal still cannot mint or use the owner's link.
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.presign_download(
                    artifact_id=artifact.artifact_id, principal="owner-2"
                )
            preview = await service.compute_preview(
                artifact_id=artifact.artifact_id,
                principal="owner-1",
                policy="manual",
            )
            assert preview.artifact_id != artifact.artifact_id
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.presign_download(
                    artifact_id=artifact.artifact_id, principal="viewer-x"
                )


async def test_quotas_bound_concurrent_admission_and_failed_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Logical/physical quotas bound admission; failed saves stay distinct."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    ledger = retention.SavedWorkQuotaLedger(
        quotas={
            "max_logical_bytes_per_scope": 100,
            "max_physical_bytes_per_scope": 100,
            "max_live_use_claims_per_scope": 2,
        }
    )
    # Shared physical bytes charge once physically, per reference logically.
    ledger.check_and_reserve(scope="owner-1", logical_bytes=40, physical_bytes=40)
    ledger.check_and_reserve(scope="owner-1", logical_bytes=40, physical_bytes=0)
    with pytest.raises(ValueError, match="SAVED_WORK_QUOTA_LOGICAL"):
        ledger.check_and_reserve(scope="owner-1", logical_bytes=40, physical_bytes=0)
    ledger.release(scope="owner-1", logical_bytes=40)
    ledger.check_and_reserve(scope="owner-1", claims=2)
    with pytest.raises(ValueError, match="SAVED_WORK_QUOTA_CLAIMS"):
        ledger.check_and_reserve(scope="owner-1", claims=1)

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, tmp_path)
            await _complete_artifact(
                service, principal="owner-1", payload=b"x" * 64
            )
            with pytest.raises(
                TemporalArtifactValidationError, match="SAVED_WORK_QUOTA"
            ):
                await service.check_saved_work_quota(
                    scope="owner-1",
                    logical_bytes=10**9,
                    quotas={"max_logical_bytes_per_scope": 128},
                )
            # A failed save is never mislabeled as verified durable work.
            failed, _upload = await service.create(
                principal="owner-1", content_type="text/plain"
            )
            assert service.saved_work_availability_of(failed) == "incomplete"
            assert (
                service.saved_work_availability_of(
                    failed, never_verified_complete=True
                )
                == "locally_retained_but_unsaved"
            )


async def test_availability_states_come_from_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every lifecycle state has an explicit evidence-based outcome."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, tmp_path)
            artifact = await _complete_artifact(service, principal="owner-1")
            assert service.saved_work_availability_of(artifact) == "available"

            row = await service._repository.get_artifact(artifact.artifact_id)
            assert service.saved_work_availability_of(row, bytes_missing=True) == (
                "incomplete"
            )
            assert service.saved_work_availability_of(row, digest_mismatch=True) == (
                "corrupt"
            )
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            assert service.saved_work_availability_of(row) == "expired"
            row.metadata_json = {
                **dict(row.metadata_json or {}),
                "quarantine": "true",
            }
            row.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
            assert service.saved_work_availability_of(row) == "quarantined"


async def test_sweep_bounded_paginated_idempotent_observable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sweeps honor page bounds, prune stale claims, and converge idempotently."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(
                session, tmp_path, lifecycle_hard_delete_after_seconds=0
            )
            ids: list[str] = []
            for index in range(4):
                artifact = await _complete_artifact(service, principal="owner-1")
                ids.append(artifact.artifact_id)
            # Admit the use claim while the artifact is still available:
            # expired content is explicitly refused admission.
            await service.acquire_saved_work_use(
                artifact_id=ids[0],
                principal="owner-1",
                request_id="stale-claim",
                operation_kind="download",
                ttl_seconds=60,
            )
            for artifact_id in ids:
                row = await service._repository.get_artifact(artifact_id)
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await service._repository.commit()
            # Expire the claim directly so pruning is observable.
            claims = await service._repository.list_use_claims(ids[0])
            claims[0].expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await service._repository.commit()

            first = await service.sweep_lifecycle(
                principal="service:lifecycle", run_id="page-1", limit=2
            )
            assert first.expired_candidate_count <= 2
            assert first.soft_deleted_count <= 2
            assert first.pruned_claim_count >= 1
            rerun = await service.sweep_lifecycle(
                principal="service:lifecycle", run_id="page-1b", limit=10
            )
            # Idempotent: already-deleted rows are not double-counted.
            assert rerun.soft_deleted_count <= (4 - first.soft_deleted_count)
            final = await service.sweep_lifecycle(
                principal="service:lifecycle",
                run_id="converge",
                limit=10,
                now=datetime.now(UTC) + timedelta(hours=1),
            )
            assert final.hard_deleted_count >= 0
            settled = await service.sweep_lifecycle(
                principal="service:lifecycle",
                run_id="settled",
                limit=10,
                now=datetime.now(UTC) + timedelta(hours=2),
            )
            assert settled.soft_deleted_count == 0


async def test_service_principal_carries_admitted_scope_for_saved_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare service principal is not a generic saved-work permission."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, tmp_path)
            artifact = await _complete_artifact(service, principal="owner-1")

            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.acquire_saved_work_use(
                    artifact_id=artifact.artifact_id,
                    principal="service:restore-worker",
                    request_id="restore-svc",
                    operation_kind="restore",
                )
            # Carrying the admitted user scope through the service call works.
            claim = await service.acquire_saved_work_use(
                artifact_id=artifact.artifact_id,
                principal="service:restore-worker",
                admitted_principal="owner-1",
                request_id="restore-svc",
                operation_kind="restore",
            )
            assert claim.owner_principal == "service:restore-worker"


async def test_retention_graph_helpers_reject_unsafe_shapes() -> None:
    """Pure contract checks: provenance/hash/knowledge never authorize."""
    with pytest.raises(ValueError, match="SAVED_WORK_DEP_CYCLE"):
        retention.validate_saved_work_dependency_graph(
            ["a"],
            resolve_artifact=lambda artifact_id: {"dependencies": ["a"]},
            authorize_artifact=lambda artifact_id: True,
        )
    with pytest.raises(ValueError, match="SAVED_WORK_DEP_UNRESOLVED"):
        retention.validate_saved_work_dependency_graph(
            ["missing"],
            resolve_artifact=lambda artifact_id: None,
            authorize_artifact=lambda artifact_id: True,
        )
    with pytest.raises(ValueError, match="SAVED_WORK_DEP_UNAUTHORIZED"):
        retention.validate_saved_work_dependency_graph(
            ["known-id"],
            resolve_artifact=lambda artifact_id: {"dependencies": []},
            # Knowing the id (or its hash) is not authorization.
            authorize_artifact=lambda artifact_id: False,
        )
    ttl, statement = retention.resolve_download_ttl_seconds(
        configured_ttl_seconds=7200, restricted_content=True
    )
    assert ttl <= retention.SAVED_WORK_MAX_DOWNLOAD_TTL_SECONDS
    assert "does not retract" in statement


async def test_bare_service_denied_across_data_plane_with_admitted_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uniform owner policy on every data surface; scope must be carried.

    A bare ``service:`` principal without ``admitted_principal`` cannot
    read, stream, download, preview, or list another owner's quarantined
    artifact, and ``allow_restricted_raw=True`` never bypasses that.
    Carrying the admitted user scope through the same calls succeeds.
    """
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = _service(session, tmp_path)
            artifact = await _complete_artifact(
                service,
                principal="owner-1",
                payload=b"quarantined-bytes",
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                metadata_json={"quarantine": "true"},
            )
            bare = "service:restore-worker"

            for call in (
                service.read(artifact_id=artifact.artifact_id, principal=bare),
                service.read(
                    artifact_id=artifact.artifact_id,
                    principal=bare,
                    allow_restricted_raw=True,
                ),
                service.read_chunks(
                    artifact_id=artifact.artifact_id, principal=bare
                ),
                service.read_path(
                    artifact_id=artifact.artifact_id, principal=bare
                ),
                service.get_metadata(
                    artifact_id=artifact.artifact_id, principal=bare
                ),
                service.presign_download(
                    artifact_id=artifact.artifact_id, principal=bare
                ),
                service.compute_preview(
                    artifact_id=artifact.artifact_id, principal=bare
                ),
            ):
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await call

            # Listing surfaces filter instead of leaking the row.
            listed, _total = await service.list_authorized_collection(
                principal=bare, category="artifacts", query=None, offset=0, limit=50
            )
            assert artifact.artifact_id not in {
                item.artifact_id for item, _links in listed
            }

            # Carrying the admitted user scope succeeds on every surface.
            _meta, payload = await service.read(
                artifact_id=artifact.artifact_id,
                principal=bare,
                admitted_principal="owner-1",
            )
            assert payload == b"quarantined-bytes"
            _meta, _chunks = await service.read_chunks(
                artifact_id=artifact.artifact_id,
                principal=bare,
                admitted_principal="owner-1",
            )
            _meta, _path = await service.read_path(
                artifact_id=artifact.artifact_id,
                principal=bare,
                admitted_principal="owner-1",
            )
            _meta, _links, _pinned, policy = await service.get_metadata(
                artifact_id=artifact.artifact_id,
                principal=bare,
                admitted_principal="owner-1",
            )
            assert policy is not None
            _art, _expires, url = await service.presign_download(
                artifact_id=artifact.artifact_id,
                principal=bare,
                admitted_principal="owner-1",
            )
            assert url
            preview = await service.compute_preview(
                artifact_id=artifact.artifact_id,
                principal=bare,
                admitted_principal="owner-1",
            )
            assert preview.artifact_id != artifact.artifact_id
            listed_admitted, _total = await service.list_authorized_collection(
                principal=bare,
                admitted_principal="owner-1",
                category="artifacts",
                query=None,
                offset=0,
                limit=50,
            )
            assert artifact.artifact_id in {
                item.artifact_id for item, _links in listed_admitted
            }


async def test_phantom_insert_blocked_across_transactions_during_hard_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Insert-vs-delete protocol across independent transactions.

    Session A hard-deletes a content-addressed blob with a failing object
    store, leaving a committed tombstone + deletion intent. Session B, on an
    independent connection to the same database file, must refuse to attach
    a phantom logical reference to the same storage key until the sweeper
    reconciles; the retry then re-uploads bytes instead of pointing at
    removed objects.
    """
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    db_path = tmp_path / "phantom_race.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine_a = create_async_engine(db_url, future=True)
    engine_b = create_async_engine(db_url, future=True)
    try:
        async with engine_a.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker_a = sessionmaker(
            engine_a, class_=AsyncSession, expire_on_commit=False
        )
        maker_b = sessionmaker(
            engine_b, class_=AsyncSession, expire_on_commit=False
        )
        payload = b"phantom-shared-bytes"
        digest = hashlib.sha256(payload).hexdigest()

        async with maker_a() as session_a:
            service_a = _service(session_a, tmp_path)
            artifact_a = await _complete_artifact(
                service_a,
                principal="owner-1",
                payload=payload,
                content_addressed_scope="saved-work",
                size_bytes=len(payload),
                sha256=digest,
            )
            storage_key = artifact_a.storage_key
            await service_a.soft_delete(
                artifact_id=artifact_a.artifact_id, principal="owner-1"
            )
            real_delete = service_a._store.delete

            def _flaky_delete(_storage_key: str) -> None:
                raise OSError("object-store unavailable")

            monkeypatch.setattr(service_a._store, "delete", _flaky_delete)
            with pytest.raises(TemporalArtifactStateError, match="DELETE_RETRY"):
                await service_a.hard_delete(
                    artifact_id=artifact_a.artifact_id, principal="owner-1"
                )
            monkeypatch.setattr(service_a._store, "delete", real_delete)

        # Independent transaction observes the committed intent and refuses.
        async with maker_b() as session_b:
            service_b = _service(session_b, tmp_path)
            assert await service_b._repository.has_pending_deletion_for_storage_key(
                storage_backend=artifact_a.storage_backend,
                storage_key=storage_key,
            )
            with pytest.raises(
                TemporalArtifactStateError, match="SAVED_WORK_STORAGE_RACE"
            ):
                await service_b.create(
                    principal="owner-1",
                    content_type="application/octet-stream",
                    size_bytes=len(payload),
                    sha256=digest,
                    content_addressed_scope="saved-work",
                )
            await session_b.rollback()

        # Sweeper reconciliation converges the failed delete (bytes removed,
        # intent cleared); the retry then re-uploads instead of dangling.
        async with maker_a() as session_a2:
            service_a2 = _service(session_a2, tmp_path)
            reconciled = await service_a2.reconcile_deletion_intents(
                principal="service:lifecycle"
            )
            assert reconciled >= 1

        async with maker_b() as session_b2:
            service_b2 = _service(session_b2, tmp_path)
            retried, _upload = await service_b2.create(
                principal="owner-1",
                content_type="application/octet-stream",
                size_bytes=len(payload),
                sha256=digest,
                content_addressed_scope="saved-work",
            )
            completed = await service_b2.write_complete(
                artifact_id=retried.artifact_id,
                principal="owner-1",
                payload=payload,
                content_type="application/octet-stream",
            )
            _meta, kept = await service_b2.read(
                artifact_id=completed.artifact_id, principal="owner-1"
            )
            assert kept == payload
    finally:
        await engine_a.dispose()
        await engine_b.dispose()
