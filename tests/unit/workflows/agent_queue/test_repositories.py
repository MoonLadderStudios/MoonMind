"""Unit tests for Agent Queue repository lifecycle behavior."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.workflows.agent_queue import models
from moonmind.workflows.agent_queue.repositories import (
    AgentJobOwnershipError,
    AgentJobStateError,
    AgentQueueRepository,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


@asynccontextmanager
async def queue_db(tmp_path):
    """Provide an isolated async sqlite database for repository tests."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/agent_queue.db"
    engine = create_async_engine(db_url, future=True)
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield async_session_maker
    finally:
        await engine.dispose()


async def _create_job(
    repo: AgentQueueRepository,
    *,
    job_type: str = "codex_exec",
    priority: int = 0,
    payload: dict | None = None,
    max_attempts: int = 3,
):
    payload = payload or {
        "repository": "moon/default",
        "requiredCapabilities": ["codex", "git"],
        "instruction": "run",
    }
    return await repo.create_job(
        job_type=job_type,
        payload=payload,
        priority=priority,
        max_attempts=max_attempts,
    )


async def test_create_and_list_jobs(tmp_path):
    """Jobs should persist with queued defaults and list filters."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            await _create_job(repo, job_type="codex_exec", priority=1)
            await _create_job(repo, job_type="codex_skill", priority=5)
            await repo.commit()

        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            all_jobs = await repo.list_jobs(limit=10)
            codex_skill_jobs = await repo.list_jobs(job_type="codex_skill", limit=10)

    assert len(all_jobs) == 2
    assert all(job.status is models.AgentJobStatus.QUEUED for job in all_jobs)
    assert codex_skill_jobs[0].type == "codex_skill"
    assert codex_skill_jobs[0].attempt == 1


async def test_claim_prioritizes_highest_priority_then_oldest(tmp_path):
    """Claim should pick highest priority first, then oldest creation time."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            low = await _create_job(repo, priority=1)
            high = await _create_job(repo, priority=9)
            await repo.commit()

            first = await repo.claim_job(
                worker_id="worker-1",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )
            await repo.commit()
            second = await repo.claim_job(
                worker_id="worker-2",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )
            await repo.commit()

    assert first is not None
    assert second is not None
    assert first.id == high.id
    assert second.id == low.id
    assert first.status is models.AgentJobStatus.RUNNING
    assert first.claimed_by == "worker-1"
    assert first.lease_expires_at is not None


async def test_heartbeat_requires_owner_and_running_state(tmp_path):
    """Heartbeat should reject ownership mismatch and non-running states."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job = await _create_job(repo)
            await repo.commit()

            await repo.claim_job(
                worker_id="owner",
                lease_seconds=60,
                worker_capabilities=["codex", "git"],
            )
            await repo.commit()

            with pytest.raises(AgentJobOwnershipError):
                await repo.heartbeat(
                    job_id=job.id,
                    worker_id="other-worker",
                    lease_seconds=60,
                )

            completed = await repo.complete_job(
                job_id=job.id,
                worker_id="owner",
                result_summary="ok",
            )
            await repo.commit()

            with pytest.raises(AgentJobStateError):
                await repo.heartbeat(
                    job_id=job.id,
                    worker_id="owner",
                    lease_seconds=60,
                )

    assert completed.status is models.AgentJobStatus.SUCCEEDED


async def test_fail_retryable_requeues_until_attempt_limit(tmp_path):
    """Retryable failures should requeue until max attempts, then fail terminally."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job = await _create_job(repo, max_attempts=2)
            await repo.commit()

            await repo.claim_job(
                worker_id="worker-1",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )
            await repo.commit()
            requeued = await repo.fail_job(
                job_id=job.id,
                worker_id="worker-1",
                error_message="transient",
                retryable=True,
            )
            await repo.commit()
            assert requeued.status is models.AgentJobStatus.QUEUED
            assert requeued.attempt == 2
            assert requeued.next_attempt_at is not None
            requeued.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            await repo.commit()

            await repo.claim_job(
                worker_id="worker-2",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )
            await repo.commit()
            failed = await repo.fail_job(
                job_id=job.id,
                worker_id="worker-2",
                error_message="still failing",
                retryable=True,
            )
            await repo.commit()

    assert failed.status is models.AgentJobStatus.DEAD_LETTER


async def test_manifest_claim_requires_capabilities(tmp_path):
    """Manifest jobs should only be claimed by workers advertising every capability."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            manifest_payload = {
                "manifest": {
                    "name": "demo",
                    "source": {"kind": "registry", "name": "demo"},
                },
                "manifestHash": "sha256:abc",
                "manifestVersion": "v0",
                "requiredCapabilities": ["manifest", "qdrant"],
            }
            await _create_job(
                repo,
                job_type="manifest",
                payload=manifest_payload,
                priority=5,
            )
            await repo.commit()

            denied = await repo.claim_job(
                worker_id="worker-1",
                lease_seconds=30,
                worker_capabilities=["manifest"],
            )
            assert denied is None

            granted = await repo.claim_job(
                worker_id="worker-1",
                lease_seconds=30,
                worker_capabilities=["manifest", "qdrant"],
            )
            await repo.commit()
            assert granted is not None
            assert granted.claimed_by == "worker-1"
            assert granted.status is models.AgentJobStatus.RUNNING
    assert failed.finished_at is not None
    assert failed.next_attempt_at is None


async def test_claim_requeues_expired_lease_before_selecting_job(tmp_path):
    """Expired running jobs should be normalized before claim selection."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            expired = await _create_job(repo, priority=10)
            ready = await _create_job(repo, priority=1)
            await repo.commit()

            await repo.claim_job(
                worker_id="old-worker",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )
            expired.lease_expires_at = datetime.now(UTC) - timedelta(seconds=5)
            expired.updated_at = datetime.now(UTC) - timedelta(seconds=5)
            await repo.commit()

            claimed = await repo.claim_job(
                worker_id="new-worker",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )
            await repo.commit()

    assert claimed is not None
    assert claimed.id == ready.id
    assert claimed.claimed_by == "new-worker"
    assert claimed.status is models.AgentJobStatus.RUNNING
    assert expired.next_attempt_at is not None


async def test_claim_skips_jobs_waiting_for_retry_window(tmp_path):
    """Claim should ignore queued jobs whose next_attempt_at is in the future."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            delayed = await _create_job(repo, priority=10)
            ready = await _create_job(repo, priority=1)
            delayed.next_attempt_at = datetime.now(UTC) + timedelta(minutes=5)
            await repo.commit()

            claimed = await repo.claim_job(
                worker_id="worker-1",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )
            await repo.commit()

    assert claimed is not None
    assert claimed.id == ready.id


async def test_claim_applies_repository_and_capability_filters(tmp_path):
    """Claim should respect repository allowlists and required capabilities."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            await _create_job(
                repo,
                priority=10,
                payload={
                    "repository": "moon/blocked",
                    "requiredCapabilities": ["codex"],
                    "instruction": "skip",
                },
            )
            allowed = await _create_job(
                repo,
                priority=5,
                payload={
                    "repository": "moon/allowed",
                    "requiredCapabilities": ["codex"],
                    "instruction": "run",
                },
            )
            await repo.commit()

            claimed = await repo.claim_job(
                worker_id="worker-1",
                lease_seconds=30,
                allowed_repositories=["moon/allowed"],
                worker_capabilities=["codex", "git"],
            )
            await repo.commit()

    assert claimed is not None
    assert claimed.id == allowed.id


async def test_claim_denies_jobs_without_required_capabilities(tmp_path):
    """Deny-by-default claim path should skip jobs missing capability requirements."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            await _create_job(
                repo,
                payload={
                    "repository": "moon/allowed",
                    "instruction": "run",
                },
            )
            await repo.commit()

            claimed = await repo.claim_job(
                worker_id="worker-1",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )
            await repo.commit()

    assert claimed is None


async def test_claim_scans_past_first_batch_for_eligible_filtered_job(tmp_path):
    """Claim should continue scanning when early batches are all ineligible."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            for index in range(205):
                await _create_job(
                    repo,
                    priority=1000 - index,
                    payload={
                        "repository": "moon/blocked",
                        "requiredCapabilities": ["codex"],
                        "instruction": f"skip-{index}",
                    },
                )
            eligible = await _create_job(
                repo,
                priority=1,
                payload={
                    "repository": "moon/allowed",
                    "requiredCapabilities": ["codex"],
                    "instruction": "run",
                },
            )
            await repo.commit()

            claimed = await repo.claim_job(
                worker_id="worker-1",
                lease_seconds=30,
                allowed_repositories=["moon/allowed"],
                worker_capabilities=["codex"],
            )
            await repo.commit()

    assert claimed is not None
    assert claimed.id == eligible.id


async def test_concurrent_claims_do_not_duplicate_jobs(tmp_path):
    """Concurrent claim attempts should not return the same queued job twice."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            await _create_job(repo, priority=3)
            await _create_job(repo, priority=2)
            await repo.commit()

        async def claim(worker: str):
            async with session_maker() as claim_session:
                claim_repo = AgentQueueRepository(claim_session)
                job = await claim_repo.claim_job(
                    worker_id=worker,
                    lease_seconds=45,
                    worker_capabilities=["codex", "git"],
                )
                await claim_repo.commit()
                return job.id if job else None

        claimed_ids = await asyncio.gather(claim("worker-a"), claim("worker-b"))

    assert claimed_ids[0] is not None
    assert claimed_ids[1] is not None
    assert claimed_ids[0] != claimed_ids[1]


async def test_list_jobs_for_telemetry_and_events_for_jobs(tmp_path):
    """Telemetry helpers should return bounded recent jobs and related events."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            older = await _create_job(repo, job_type="task")
            await repo.append_event(
                job_id=older.id,
                level=models.AgentJobEventLevel.INFO,
                message="older",
            )
            await repo.commit()

            newer = await _create_job(repo, job_type="codex_exec")
            await repo.append_event(
                job_id=newer.id,
                level=models.AgentJobEventLevel.ERROR,
                message="newer",
            )
            await repo.commit()

            since = datetime.now(UTC) - timedelta(minutes=5)
            jobs = await repo.list_jobs_for_telemetry(since=since, limit=10)
            events = await repo.list_events_for_jobs(
                job_ids=[job.id for job in jobs],
                since=since,
                limit=100,
            )

    assert len(jobs) == 2
    assert {job.id for job in jobs} == {older.id, newer.id}
    assert len(events) == 2
    assert {event.message for event in events} == {"older", "newer"}


async def test_list_events_supports_composite_after_cursor(tmp_path):
    """Event paging should include same-timestamp events when id is newer than cursor."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job = await _create_job(repo, job_type="task")
            await repo.commit()

            created_at = datetime.now(UTC)
            event_ids = [
                UUID("00000000-0000-0000-0000-000000000001"),
                UUID("00000000-0000-0000-0000-000000000002"),
                UUID("00000000-0000-0000-0000-000000000003"),
            ]
            messages = ["first", "second", "third"]
            for event_id, message in zip(event_ids, messages):
                session.add(
                    models.AgentJobEvent(
                        id=event_id,
                        job_id=job.id,
                        level=models.AgentJobEventLevel.INFO,
                        message=message,
                        payload=None,
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )
            await repo.commit()

            events = await repo.list_events(
                job_id=job.id,
                limit=10,
                after=created_at,
                after_event_id=event_ids[0],
            )

    assert [event.id for event in events] == event_ids[1:]
    assert [event.message for event in events] == ["second", "third"]


async def test_list_events_supports_descending_before_cursor(tmp_path):
    """Reverse paging should include same-timestamp events older than cursor id."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job = await _create_job(repo, job_type="task")
            await repo.commit()

            created_at = datetime.now(UTC)
            event_ids = [
                UUID("00000000-0000-0000-0000-000000000001"),
                UUID("00000000-0000-0000-0000-000000000002"),
                UUID("00000000-0000-0000-0000-000000000003"),
            ]
            messages = ["first", "second", "third"]
            for event_id, message in zip(event_ids, messages):
                session.add(
                    models.AgentJobEvent(
                        id=event_id,
                        job_id=job.id,
                        level=models.AgentJobEventLevel.INFO,
                        message=message,
                        payload=None,
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )
            await repo.commit()

            events = await repo.list_events(
                job_id=job.id,
                limit=10,
                before=created_at,
                before_event_id=event_ids[2],
                sort="desc",
            )

    assert [event.id for event in events] == [event_ids[1], event_ids[0]]
    assert [event.message for event in events] == ["second", "first"]


async def test_request_cancel_queued_job_is_immediate_and_idempotent(tmp_path):
    """Queued cancellation should be immediate and repeated requests no-op."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job = await _create_job(repo)
            await repo.commit()

            cancelled, action = await repo.request_cancel(
                job_id=job.id,
                requested_by_user_id=uuid4(),
                reason="operator request",
            )
            await repo.commit()
            repeated, repeated_action = await repo.request_cancel(
                job_id=job.id,
                requested_by_user_id=uuid4(),
                reason="ignored",
            )
            await repo.commit()

    assert cancelled.status is models.AgentJobStatus.CANCELLED
    assert cancelled.finished_at is not None
    assert cancelled.cancel_requested_at is not None
    assert cancelled.cancel_reason == "operator request"
    assert action == "queued_cancelled"
    assert repeated.status is models.AgentJobStatus.CANCELLED
    assert repeated_action == "noop_cancelled"


async def test_running_cancel_request_requires_owner_ack(tmp_path):
    """Running cancellation should require owner ack for terminal transition."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job = await _create_job(repo)
            await repo.commit()

            await repo.claim_job(
                worker_id="owner",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )
            await repo.commit()

            requested, request_action = await repo.request_cancel(
                job_id=job.id,
                requested_by_user_id=uuid4(),
                reason="stop",
            )
            await repo.commit()
            assert requested.status is models.AgentJobStatus.RUNNING
            assert request_action == "running_requested"
            assert requested.cancel_requested_at is not None

            with pytest.raises(AgentJobOwnershipError):
                await repo.ack_cancel(job_id=job.id, worker_id="other-worker")

            acknowledged, ack_action = await repo.ack_cancel(
                job_id=job.id,
                worker_id="owner",
            )
            await repo.commit()

    assert acknowledged.status is models.AgentJobStatus.CANCELLED
    assert acknowledged.claimed_by is None
    assert acknowledged.finished_at is not None
    assert ack_action == "acknowledged"


async def test_retryable_fail_does_not_requeue_cancel_requested_job(tmp_path):
    """retryable fail should not requeue after cancellation request exists."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job = await _create_job(repo)
            await repo.commit()
            await repo.claim_job(
                worker_id="worker-1",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )
            await repo.commit()
            await repo.request_cancel(
                job_id=job.id,
                requested_by_user_id=uuid4(),
                reason="stop",
            )
            await repo.commit()

            failed = await repo.fail_job(
                job_id=job.id,
                worker_id="worker-1",
                error_message="transient after cancel",
                retryable=True,
            )
            await repo.commit()

    assert failed.status is models.AgentJobStatus.CANCELLED
    assert failed.finished_at is not None
    assert failed.next_attempt_at is None


async def test_expired_running_job_with_cancel_request_is_not_requeued(tmp_path):
    """Lease expiry normalization should cancel requested jobs instead of requeueing."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job = await _create_job(repo, priority=10)
            await _create_job(repo, priority=1)
            await repo.commit()

            await repo.claim_job(
                worker_id="owner",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )
            job.cancel_requested_at = datetime.now(UTC) - timedelta(seconds=2)
            job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=5)
            await repo.commit()

            await repo.claim_job(
                worker_id="next",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )
            await repo.commit()

    assert job.status is models.AgentJobStatus.CANCELLED
    assert job.finished_at is not None


async def test_upsert_live_session_sets_ended_at_only_once(tmp_path):
    """Terminal upserts should preserve first ended_at timestamp."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job = await _create_job(repo, job_type="task")
            await repo.commit()

            await repo.upsert_live_session(
                task_run_id=job.id,
                status=models.AgentJobLiveSessionStatus.STARTING,
            )
            await repo.commit()
            first_terminal = await repo.upsert_live_session(
                task_run_id=job.id,
                status=models.AgentJobLiveSessionStatus.ERROR,
            )
            first_ended_at = first_terminal.ended_at
            await repo.commit()
            assert first_ended_at is not None

            await asyncio.sleep(0.01)
            second_terminal = await repo.upsert_live_session(
                task_run_id=job.id,
                status=models.AgentJobLiveSessionStatus.ENDED,
            )
            await repo.commit()

    assert second_terminal.ended_at == first_ended_at
