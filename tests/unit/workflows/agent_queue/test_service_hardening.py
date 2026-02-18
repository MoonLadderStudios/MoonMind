"""Unit tests for queue hardening service behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.config.settings import settings
from moonmind.workflows.agent_queue import models
from moonmind.workflows.agent_queue.repositories import AgentQueueRepository
from moonmind.workflows.agent_queue.service import (
    AgentQueueAuthenticationError,
    AgentQueueService,
    AgentQueueValidationError,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


@asynccontextmanager
async def queue_db(tmp_path: Path):
    """Provide isolated async sqlite storage for service tests."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/agent_queue_service_hardening.db"
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


async def test_issue_and_resolve_worker_token_policy(tmp_path: Path) -> None:
    """Issued worker tokens should resolve to stored policy metadata."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)

            issued = await service.issue_worker_token(
                worker_id="executor-01",
                description="primary",
                allowed_repositories=["Moon/Mind"],
                allowed_job_types=["codex_exec"],
                capabilities=["codex", "git"],
            )
            policy = await service.resolve_worker_token(issued.raw_token)

    assert policy.worker_id == "executor-01"
    assert policy.auth_source == "worker_token"
    assert policy.allowed_repositories == ("Moon/Mind",)
    assert policy.allowed_job_types == ("codex_exec",)
    assert policy.capabilities == ("codex", "git")


async def test_resolve_worker_token_rejects_inactive_token(tmp_path: Path) -> None:
    """Inactive tokens should not authenticate worker actions."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)

            issued = await service.issue_worker_token(worker_id="executor-01")
            await service.revoke_worker_token(token_id=issued.token_record.id)

            with pytest.raises(AgentQueueAuthenticationError):
                await service.resolve_worker_token(issued.raw_token)


async def test_fail_job_retry_backoff_and_dead_letter(tmp_path: Path) -> None:
    """Retryable failures should back off then dead-letter after exhaustion."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(
                repo,
                retry_backoff_base_seconds=5,
                retry_backoff_max_seconds=60,
            )
            job = await service.create_job(
                job_type="codex_exec",
                payload={"repository": "Moon/Mind", "instruction": "run"},
                max_attempts=2,
            )
            await service.claim_job(
                worker_id="executor-01",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )

            first = await service.fail_job(
                job_id=job.id,
                worker_id="executor-01",
                error_message="transient",
                retryable=True,
            )
            assert first.status is models.AgentJobStatus.QUEUED
            assert first.next_attempt_at is not None
            assert first.next_attempt_at > datetime.now(UTC)

            first.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            await repo.commit()

            await service.claim_job(
                worker_id="executor-01",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )
            second = await service.fail_job(
                job_id=job.id,
                worker_id="executor-01",
                error_message="still broken",
                retryable=True,
            )

    assert second.status is models.AgentJobStatus.DEAD_LETTER
    assert second.next_attempt_at is None


async def test_append_and_list_events_with_after_cursor(tmp_path: Path) -> None:
    """Event reads should support incremental polling with `after` cursor."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await service.create_job(
                job_type="codex_exec",
                payload={"repository": "Moon/Mind", "instruction": "run"},
            )
            first = await service.append_event(
                job_id=job.id,
                level=models.AgentJobEventLevel.INFO,
                message="first",
            )
            second = await service.append_event(
                job_id=job.id,
                level=models.AgentJobEventLevel.WARN,
                message="second",
            )

            events = await service.list_events(job_id=job.id, after=first.created_at)

    assert len(events) == 1
    assert events[0].id == second.id


async def test_list_events_rejects_after_event_id_without_after(tmp_path: Path) -> None:
    """Composite event cursors require a timestamp component."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await service.create_job(
                job_type="codex_exec",
                payload={"repository": "Moon/Mind", "instruction": "run"},
            )
            first = await service.append_event(
                job_id=job.id,
                level=models.AgentJobEventLevel.INFO,
                message="first",
            )

            with pytest.raises(
                AgentQueueValidationError,
                match="afterEventId requires after timestamp",
            ):
                await service.list_events(
                    job_id=job.id,
                    after_event_id=first.id,
                )


async def test_create_task_job_derives_runtime_capabilities(tmp_path: Path) -> None:
    """Canonical task jobs should derive required capabilities automatically."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await service.create_job(
                job_type="task",
                payload={
                    "repository": "Moon/Mind",
                    "task": {
                        "instructions": "Run task",
                        "skill": {"id": "auto", "args": {}},
                        "runtime": {"mode": "codex", "model": None, "effort": None},
                        "git": {"startingBranch": None, "newBranch": None},
                        "publish": {"mode": "pr", "prBaseBranch": "main"},
                    },
                },
            )

    assert job.payload["targetRuntime"] == "codex"
    assert job.payload["task"]["runtime"]["mode"] == "codex"
    assert job.payload["requiredCapabilities"] == ["codex", "git", "gh"]


async def test_create_task_job_applies_settings_defaults_for_missing_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task creation should resolve missing repository/model/effort from settings."""

    monkeypatch.setattr(
        settings.spec_workflow,
        "github_repository",
        "MoonLadderStudios/MoonMind",
    )
    monkeypatch.setattr(settings.spec_workflow, "codex_model", "gpt-5.3-codex")
    monkeypatch.setattr(settings.spec_workflow, "codex_effort", "high")

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await service.create_job(
                job_type="task",
                payload={
                    "task": {
                        "instructions": "Run task",
                        "runtime": {"mode": "codex"},
                        "git": {"startingBranch": None, "newBranch": None},
                        "publish": {"mode": "branch"},
                    },
                },
            )

    assert job.payload["repository"] == "MoonLadderStudios/MoonMind"
    assert job.payload["targetRuntime"] == "codex"
    assert job.payload["task"]["runtime"]["model"] == "gpt-5.3-codex"
    assert job.payload["task"]["runtime"]["effort"] == "high"


async def test_create_task_job_defaults_publish_mode_to_pr_when_omitted(
    tmp_path: Path,
) -> None:
    """Canonical task jobs should default to PR publishing when mode is omitted."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await service.create_job(
                job_type="task",
                payload={
                    "repository": "Moon/Mind",
                    "task": {
                        "instructions": "Run task",
                        "runtime": {"mode": "codex"},
                        "git": {"startingBranch": None, "newBranch": None},
                    },
                },
            )

    assert job.payload["task"]["publish"]["mode"] == "pr"
    assert job.payload["requiredCapabilities"] == ["codex", "git", "gh"]


async def test_create_task_job_rejects_invalid_repository_format(
    tmp_path: Path,
) -> None:
    """Task creation should fail fast on malformed repository values."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            with pytest.raises(
                AgentQueueValidationError,
                match="repository must be",
            ):
                await service.create_job(
                    job_type="task",
                    payload={
                        "repository": "invalid-repo-format",
                        "task": {
                            "instructions": "Run task",
                            "runtime": {"mode": "codex"},
                            "git": {"startingBranch": None, "newBranch": None},
                            "publish": {"mode": "none"},
                        },
                    },
                )


@pytest.mark.parametrize(
    ("repository",),
    [
        ("https://github.com/MoonLadderStudios/MoonMind.git",),
        ("git@github.com:MoonLadderStudios/MoonMind.git",),
    ],
)
async def test_create_task_job_accepts_supported_repository_url_formats(
    tmp_path: Path,
    repository: str,
) -> None:
    """Task creation should accept slug, HTTPS, and SSH token-free repository values."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await service.create_job(
                job_type="task",
                payload={
                    "repository": repository,
                    "task": {
                        "instructions": "Run task",
                        "runtime": {"mode": "codex"},
                        "git": {"startingBranch": None, "newBranch": None},
                        "publish": {"mode": "branch"},
                    },
                },
            )

    assert job.payload["repository"] == repository


async def test_create_task_job_rejects_repository_url_with_embedded_credentials(
    tmp_path: Path,
) -> None:
    """Task creation should reject repository URLs containing credentials."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            with pytest.raises(
                AgentQueueValidationError,
                match="must not include embedded credentials",
            ):
                await service.create_job(
                    job_type="task",
                    payload={
                        "repository": "https://ghp-secret@github.com/moon/repo.git",
                        "task": {
                            "instructions": "Run task",
                            "runtime": {"mode": "codex"},
                            "git": {"startingBranch": None, "newBranch": None},
                            "publish": {"mode": "none"},
                        },
                    },
                )


async def test_create_task_job_preserves_auth_secret_refs(tmp_path: Path) -> None:
    """Task creation should keep validated auth refs without raw tokens."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await service.create_job(
                job_type="task",
                payload={
                    "repository": "Moon/Mind",
                    "auth": {
                        "repoAuthRef": "vault://kv/moonmind/repos/Moon/Mind#github_token",
                        "publishAuthRef": "vault://kv/moonmind/repos/Moon/Mind#publish_token",
                    },
                    "task": {
                        "instructions": "Run task",
                        "runtime": {"mode": "codex"},
                        "git": {"startingBranch": None, "newBranch": None},
                        "publish": {"mode": "branch"},
                    },
                },
            )

    assert job.payload["auth"]["repoAuthRef"] == (
        "vault://kv/moonmind/repos/Moon/Mind#github_token"
    )
    assert job.payload["auth"]["publishAuthRef"] == (
        "vault://kv/moonmind/repos/Moon/Mind#publish_token"
    )


async def test_create_task_job_rejects_missing_instructions(tmp_path: Path) -> None:
    """Canonical task jobs should fail fast when instructions are missing."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            with pytest.raises(AgentQueueValidationError, match="task.instructions"):
                await service.create_job(
                    job_type="task",
                    payload={
                        "repository": "Moon/Mind",
                        "task": {
                            "skill": {"id": "auto", "args": {}},
                            "runtime": {"mode": "codex"},
                            "git": {"startingBranch": None, "newBranch": None},
                            "publish": {"mode": "branch"},
                        },
                    },
                )


async def test_create_job_rejects_unsupported_job_type(tmp_path: Path) -> None:
    """Queue API should reject job types that workers will never claim."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            with pytest.raises(
                AgentQueueValidationError,
                match="type must be one of",
            ):
                await service.create_job(
                    job_type="unsupported_type",
                    payload={
                        "repository": "Moon/Mind",
                        "task": {"instructions": "Run"},
                    },
                )


async def test_create_legacy_exec_job_requires_repository(tmp_path: Path) -> None:
    """Legacy codex_exec submissions should fail fast without repository."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            with pytest.raises(
                AgentQueueValidationError,
                match="repository is required",
            ):
                await service.create_job(
                    job_type="codex_exec",
                    payload={"instruction": "Run legacy path"},
                )


async def test_create_legacy_skill_job_is_enriched_with_task_contract(
    tmp_path: Path,
) -> None:
    """Legacy codex_skill jobs should be enriched with canonical task metadata."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await service.create_job(
                job_type="codex_skill",
                payload={
                    "skillId": "speckit",
                    "inputs": {"repo": "Moon/Mind", "instruction": "Run"},
                    "publishMode": "none",
                },
            )

    assert job.payload["repository"] == "Moon/Mind"
    assert job.payload["targetRuntime"] == "codex"
    assert job.payload["task"]["skill"]["id"] == "speckit"
    assert "codex" in job.payload["requiredCapabilities"]
    assert "git" in job.payload["requiredCapabilities"]


async def test_create_legacy_job_records_warning_event(tmp_path: Path) -> None:
    """Legacy submissions should emit migration warnings for rollout telemetry."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await service.create_job(
                job_type="codex_exec",
                payload={
                    "repository": "Moon/Mind",
                    "instruction": "Run legacy path",
                    "publish": {"mode": "none"},
                },
            )
            events = await service.list_events(job_id=job.id, limit=20)

    warning = next(
        (
            event
            for event in events
            if event.message == "Legacy job type submitted"
            and event.level is models.AgentJobEventLevel.WARN
        ),
        None,
    )
    assert warning is not None


async def test_migration_telemetry_reports_volume_and_legacy_counts(
    tmp_path: Path,
) -> None:
    """Migration telemetry should report job-type volumes and legacy submission totals."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            await service.create_job(
                job_type="task",
                payload={
                    "repository": "Moon/Mind",
                    "task": {
                        "instructions": "Run task",
                        "skill": {"id": "auto", "args": {}},
                        "runtime": {"mode": "codex", "model": None, "effort": None},
                        "git": {"startingBranch": None, "newBranch": None},
                        "publish": {"mode": "branch"},
                    },
                },
            )
            await service.create_job(
                job_type="codex_exec",
                payload={
                    "repository": "Moon/Mind",
                    "instruction": "Run legacy path",
                    "publish": {"mode": "none"},
                },
            )
            telemetry = await service.get_migration_telemetry(
                window_hours=24,
                limit=100,
            )

    assert telemetry.total_jobs >= 2
    assert telemetry.job_volume_by_type.get("task", 0) >= 1
    assert telemetry.job_volume_by_type.get("codex_exec", 0) >= 1
    assert telemetry.legacy_job_submissions >= 1
    assert telemetry.events_truncated is False
    assert "publishedRate" in telemetry.publish_outcomes


async def test_extract_publish_mode_defaults_to_none_when_absent() -> None:
    """Telemetry parser should not count missing publish metadata as requested."""

    assert AgentQueueService._extract_publish_mode({}) == "none"
    assert (
        AgentQueueService._extract_publish_mode(
            {"task": {"publish": {"mode": "branch"}}}
        )
        == "branch"
    )


async def test_load_events_by_job_sets_truncation_flag(tmp_path: Path) -> None:
    """Event-loader should flag telemetry truncation when limit is exceeded."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await service.create_job(
                job_type="task",
                payload={
                    "repository": "Moon/Mind",
                    "task": {
                        "instructions": "Run task",
                        "runtime": {"mode": "codex"},
                        "git": {"startingBranch": None, "newBranch": None},
                        "publish": {"mode": "none"},
                    },
                },
            )
            await service.append_event(
                job_id=job.id,
                level=models.AgentJobEventLevel.INFO,
                message="first",
            )
            await service.append_event(
                job_id=job.id,
                level=models.AgentJobEventLevel.INFO,
                message="second",
            )

            events_by_job, events_truncated = await service._load_events_by_job(
                jobs=[job],
                since=datetime.now(UTC) - timedelta(hours=1),
                event_limit=1,
            )

    assert events_truncated is True
    assert len(events_by_job.get(job.id, [])) == 1


async def test_request_cancel_queued_job_adds_terminal_event(tmp_path: Path) -> None:
    """Queued cancellation should terminalize job and append cancellation event."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await service.create_job(
                job_type="task",
                payload={
                    "repository": "Moon/Mind",
                    "task": {
                        "instructions": "Run task",
                        "runtime": {"mode": "codex"},
                        "git": {"startingBranch": None, "newBranch": None},
                        "publish": {"mode": "none"},
                    },
                },
            )

            cancelled = await service.request_cancel(
                job_id=job.id,
                requested_by_user_id=uuid4(),
                reason="operator request",
            )
            events = await service.list_events(job_id=job.id, limit=20)

    assert cancelled.status is models.AgentJobStatus.CANCELLED
    assert any(event.message == "Job cancelled" for event in events)


async def test_running_cancellation_is_requested_then_acknowledged(
    tmp_path: Path,
) -> None:
    """Running cancellation should be cooperative via request then worker ack."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            job = await service.create_job(
                job_type="task",
                payload={
                    "repository": "Moon/Mind",
                    "task": {
                        "instructions": "Run task",
                        "runtime": {"mode": "codex"},
                        "git": {"startingBranch": None, "newBranch": None},
                        "publish": {"mode": "none"},
                    },
                },
            )
            await service.claim_job(
                worker_id="worker-1",
                lease_seconds=30,
                worker_capabilities=["codex", "git"],
            )

            running = await service.request_cancel(
                job_id=job.id,
                requested_by_user_id=uuid4(),
                reason="stop",
            )
            running_status = running.status
            acknowledged = await service.ack_cancel(
                job_id=job.id,
                worker_id="worker-1",
                message="stopped by cancellation",
            )
            events = await service.list_events(job_id=job.id, limit=50)

    assert running_status is models.AgentJobStatus.RUNNING
    assert running.cancel_requested_at is not None
    assert acknowledged.status is models.AgentJobStatus.CANCELLED
    assert any(event.message == "Cancellation requested" for event in events)
    assert any(event.message == "Job cancelled" for event in events)
