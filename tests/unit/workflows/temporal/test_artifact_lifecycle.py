"""Lifecycle retention and cleanup tests for Temporal artifacts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, TemporalArtifactStatus
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

pytestmark = [pytest.mark.asyncio]

@asynccontextmanager
async def temporal_db(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_artifacts_lifecycle.db"
    engine = create_async_engine(db_url, future=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield session_maker
    finally:
        await engine.dispose()

async def test_lifecycle_sweep_is_idempotent_across_soft_and_hard_delete(
    tmp_path: Path,
) -> None:
    """Sweep should soft-delete then hard-delete once, and become idempotent."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                lifecycle_hard_delete_after_seconds=3600,
            )

            artifact, _upload = await service.create(
                principal="user-1", content_type="text/plain"
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=b"payload",
                content_type="text/plain",
            )

            artifact_row = await service._repository.get_artifact(artifact.artifact_id)
            artifact_row.expires_at = datetime.now(UTC) - timedelta(days=1)
            await service._repository.commit()

            first_now = datetime.now(UTC)
            first = await service.sweep_lifecycle(
                principal="service:lifecycle",
                run_id="run-1",
                now=first_now,
            )
            assert first.expired_candidate_count == 1
            assert first.soft_deleted_count == 1
            assert first.hard_deleted_count == 0

            second = await service.sweep_lifecycle(
                principal="service:lifecycle",
                run_id="run-2",
                now=first_now + timedelta(hours=2),
            )
            assert second.hard_deleted_count == 1

            third = await service.sweep_lifecycle(
                principal="service:lifecycle",
                run_id="run-3",
                now=first_now + timedelta(hours=3),
            )
            assert third.hard_deleted_count == 0

            refreshed = await service._repository.get_artifact(artifact.artifact_id)
            assert refreshed.status is TemporalArtifactStatus.DELETED
            assert refreshed.hard_deleted_at is not None
            assert refreshed.tombstoned_at is not None

async def _create_expired_artifacts(
    service: TemporalArtifactService, count: int
) -> list[str]:
    artifact_ids: list[str] = []
    for index in range(count):
        artifact, _upload = await service.create(
            principal="user-1", content_type="text/plain"
        )
        await service.write_complete(
            artifact_id=artifact.artifact_id,
            principal="user-1",
            payload=f"payload-{index}".encode(),
            content_type="text/plain",
        )
        row = await service._repository.get_artifact(artifact.artifact_id)
        row.expires_at = datetime.now(UTC) - timedelta(days=1)
        artifact_ids.append(artifact.artifact_id)
    await service._repository.commit()
    return artifact_ids


async def test_lifecycle_drain_clears_a_backlog_larger_than_one_page(
    tmp_path: Path,
) -> None:
    """One page per hourly pass let expired blobs outgrow the object store."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                lifecycle_hard_delete_after_seconds=0,
            )
            artifact_ids = await _create_expired_artifacts(service, 3)
            pages: list[int] = []

            summary = await service.drain_lifecycle(
                principal="service:lifecycle",
                run_id="drain-1",
                limit=1,
                time_budget=timedelta(minutes=5),
                on_page=lambda page: pages.append(page.hard_deleted_count),
            )

            assert summary.drained is True
            assert summary.soft_deleted_count == 3
            assert summary.hard_deleted_count == 3
            assert summary.pages == len(pages) >= 3
            for artifact_id in artifact_ids:
                row = await service._repository.get_artifact(artifact_id)
                assert row.hard_deleted_at is not None


async def test_lifecycle_drain_stops_at_its_budget_and_reports_backlog(
    tmp_path: Path,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                lifecycle_hard_delete_after_seconds=0,
            )
            await _create_expired_artifacts(service, 3)

            summary = await service.drain_lifecycle(
                principal="service:lifecycle",
                limit=1,
                time_budget=timedelta(0),
            )

            assert summary.pages == 1
            assert summary.hard_deleted_count == 1
            assert summary.drained is False


async def test_lifecycle_drain_stops_when_a_full_page_is_all_protected(
    tmp_path: Path,
) -> None:
    """Protected candidates fill a page without progress; never spin on them."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                lifecycle_hard_delete_after_seconds=0,
            )
            (artifact_id,) = await _create_expired_artifacts(service, 1)
            await service._repository.acquire_use_claim(
                artifact_id=artifact_id,
                owner_principal="service:restore",
                request_id="restore-1",
                operation_kind="restore",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            await service._repository.commit()

            summary = await service.drain_lifecycle(
                principal="service:lifecycle",
                limit=1,
                time_budget=timedelta(minutes=5),
            )

            assert summary.pages == 1
            assert summary.skipped_in_use_count == 1
            assert summary.drained is False
            row = await service._repository.get_artifact(artifact_id)
            assert row.status is TemporalArtifactStatus.COMPLETE


async def test_lifecycle_sweep_activity_drains_and_reports_completion(
    tmp_path: Path,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                lifecycle_hard_delete_after_seconds=0,
            )
            await _create_expired_artifacts(service, 2)

            activities = TemporalArtifactActivities(service)
            summary = await activities.artifact_lifecycle_sweep(
                principal="service:storage-maintenance"
            )

            assert summary.hard_deleted_count == 2
            assert summary.drained is True


async def test_lifecycle_drain_continues_through_a_deletion_intent_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full page of pending deletion intents is backlog, not a drained pass."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                lifecycle_hard_delete_after_seconds=0,
            )
            artifact_ids = await _create_expired_artifacts(service, 3)
            real_delete = service._store.delete

            def unavailable(_storage_key: str) -> None:
                raise OSError("object-store unavailable")

            monkeypatch.setattr(service._store, "delete", unavailable)
            await service.drain_lifecycle(
                principal="service:lifecycle",
                time_budget=timedelta(minutes=5),
            )
            for artifact_id in artifact_ids:
                assert await service._repository.get_deletion_intent(artifact_id)

            monkeypatch.setattr(service._store, "delete", real_delete)
            summary = await service.drain_lifecycle(
                principal="service:lifecycle",
                limit=1,
                time_budget=timedelta(minutes=5),
            )

            assert summary.reconciled_deletion_count == 3
            assert summary.drained is True
            for artifact_id in artifact_ids:
                assert (
                    await service._repository.get_deletion_intent(artifact_id)
                ) is None


async def test_lifecycle_sweep_activity_heartbeats_within_a_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow page must not outlive the heartbeat timeout between heartbeats."""

    from temporalio import activity

    heartbeats: list[object] = []
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        activity, "heartbeat", lambda *details: heartbeats.append(details)
    )

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                lifecycle_hard_delete_after_seconds=0,
            )
            await _create_expired_artifacts(service, 3)
            real_delete = service._store.delete
            heartbeats_before_delete: list[int] = []

            def observed_delete(storage_key: str) -> None:
                heartbeats_before_delete.append(len(heartbeats))
                return real_delete(storage_key)

            monkeypatch.setattr(service._store, "delete", observed_delete)

            activities = TemporalArtifactActivities(service)
            summary = await activities.artifact_lifecycle_sweep(
                principal="service:storage-maintenance"
            )

            assert summary.pages == 1
            assert summary.hard_deleted_count == 3
            # Every object deletion in the single page follows a fresh heartbeat.
            assert heartbeats_before_delete
            assert all(count > 0 for count in heartbeats_before_delete)
            assert heartbeats_before_delete == sorted(set(heartbeats_before_delete))


async def test_lifecycle_sweep_skips_pinned_artifacts(tmp_path: Path) -> None:
    """Pinned artifacts should remain undeleted during lifecycle sweeps."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            artifact, _upload = await service.create(
                principal="user-1", content_type="text/plain"
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=b"payload",
                content_type="text/plain",
            )
            await service.pin(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                reason="keep",
            )

            artifact_row = await service._repository.get_artifact(artifact.artifact_id)
            artifact_row.expires_at = datetime.now(UTC) - timedelta(days=2)
            await service._repository.commit()

            sweep = await service.sweep_lifecycle(
                principal="service:lifecycle",
                run_id="run-pinned",
            )
            assert sweep.soft_deleted_count == 0

            refreshed = await service._repository.get_artifact(artifact.artifact_id)
            assert refreshed.status is TemporalArtifactStatus.COMPLETE
