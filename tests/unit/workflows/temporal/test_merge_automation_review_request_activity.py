"""Activity-boundary tests for the durable automated review request ledger."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import api_service.db.base as db_base
from api_service.db.models import Base, MergeAutomationReviewRequestRecord
from moonmind.workflows.merge_automation_review import build_review_request_key
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalIntegrationActivities,
)

pytestmark = [pytest.mark.asyncio]

_REPO = "MoonLadderStudios/MoonMind"
_HEAD = "abc1234abc1234abc1234abc1234abc1234abc12"
_PARENT = "merge-automation:wf-parent"


@pytest_asyncio.fixture
async def ledger_session(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/ledger.db", future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def _session_context():
        async with maker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    monkeypatch.setattr(db_base, "get_async_session_context", _session_context)
    try:
        yield maker
    finally:
        await engine.dispose()


class _FakeGitHubService:
    """Records calls and returns scripted request outcomes."""

    calls: list[dict[str, Any]] = []
    results: list[Any] = []

    async def request_automated_review(self, **kwargs: Any):
        type(self).calls.append(kwargs)
        outcome = type(self).results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _install_fake_github(monkeypatch, results: list[Any]) -> type[_FakeGitHubService]:
    from moonmind.workflows.adapters import github_service as github_service_module

    _FakeGitHubService.calls = []
    _FakeGitHubService.results = list(results)
    monkeypatch.setattr(
        github_service_module, "GitHubService", _FakeGitHubService, raising=True
    )
    return _FakeGitHubService


def _outcome(status: str, **overrides: Any):
    from moonmind.workflows.adapters.github_service import AutomatedReviewRequestResult

    payload = {
        "status": status,
        "provider": "codex",
        "command": "@codex review",
        "headSha": _HEAD,
        "requestCommentId": 98765,
        "requestCommentUrl": "https://github.com/x/y#issuecomment-98765",
        "requestedAt": "2026-08-24T22:15:00Z",
        "actor": "moonmind-bot",
        "reconciled": status == "reconciled",
        "retryable": False,
        "summary": "ok",
    }
    payload.update(overrides)
    return AutomatedReviewRequestResult.model_validate(payload)


def _payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "parentWorkflowId": _PARENT,
        "repository": _REPO,
        "prNumber": 350,
        "expectedHeadSha": _HEAD,
        "provider": "codex",
        "requestKey": build_review_request_key(
            parent_workflow_id=_PARENT,
            repository=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            provider="codex",
        ),
    }
    payload.update(overrides)
    return payload


async def test_one_request_per_head_sha(ledger_session, monkeypatch) -> None:
    fake = _install_fake_github(monkeypatch, [_outcome("requested")])
    activities = TemporalIntegrationActivities()

    first = await activities.merge_automation_request_automated_review(_payload())
    second = await activities.merge_automation_request_automated_review(_payload())

    assert first["status"] == "requested"
    assert second["status"] == "recorded"
    assert second["requestCommentId"] == 98765
    # GitHub was contacted exactly once for this head SHA.
    assert len(fake.calls) == 1

    async with ledger_session() as session:
        record = await session.get(
            MergeAutomationReviewRequestRecord, _payload()["requestKey"]
        )
        assert record.status == "requested"
        assert record.request_comment_id == 98765
        assert record.command == "@codex review"


async def test_retry_after_ambiguous_post_reconciles(ledger_session, monkeypatch) -> None:
    """A lost response leaves a pending claim that the retry reconciles."""

    fake = _install_fake_github(
        monkeypatch,
        [
            _outcome(
                "unavailable",
                retryable=True,
                requestCommentId=None,
                requestedAt=None,
                summary="delivery ambiguous",
            ),
            _outcome("reconciled"),
        ],
    )
    activities = TemporalIntegrationActivities()

    with pytest.raises(TemporalActivityRuntimeError):
        await activities.merge_automation_request_automated_review(_payload())

    async with ledger_session() as session:
        record = await session.get(
            MergeAutomationReviewRequestRecord, _payload()["requestKey"]
        )
        first_attempt_started_at = record.attempt_started_at
        assert record.status == "failed"

    result = await activities.merge_automation_request_automated_review(_payload())

    assert result["status"] == "reconciled"
    assert result["reconciled"] is True
    assert len(fake.calls) == 2
    # The retry reconciles against the *original* attempt window, so a comment
    # created by the ambiguous POST is adopted instead of duplicated.
    assert fake.calls[1]["recorded_comment_id"] is None
    assert fake.calls[1]["attempt_started_at"] <= first_attempt_started_at.isoformat()

    async with ledger_session() as session:
        record = await session.get(
            MergeAutomationReviewRequestRecord, _payload()["requestKey"]
        )
        assert record.status == "requested"
        assert record.attempt_started_at == first_attempt_started_at


async def test_settled_request_is_not_downgraded_by_a_later_failure(
    ledger_session, monkeypatch
) -> None:
    _install_fake_github(monkeypatch, [_outcome("requested")])
    activities = TemporalIntegrationActivities()
    await activities.merge_automation_request_automated_review(_payload())

    from api_service.services.merge_automation_review_requests import (
        MergeAutomationReviewRequestStore,
    )

    async with ledger_session() as session:
        entry = await MergeAutomationReviewRequestStore(session).settle(
            request_key=_payload()["requestKey"],
            status="failed",
            failure_reason="late failure",
        )
        await session.commit()

    assert entry.status == "requested"
    assert entry.request_comment_id == 98765


async def test_request_key_mismatch_is_rejected(ledger_session, monkeypatch) -> None:
    _install_fake_github(monkeypatch, [])
    activities = TemporalIntegrationActivities()

    with pytest.raises(TemporalActivityRuntimeError):
        await activities.merge_automation_request_automated_review(
            _payload(requestKey="not-the-right-identity")
        )


async def test_unsupported_provider_is_rejected(ledger_session, monkeypatch) -> None:
    _install_fake_github(monkeypatch, [])
    activities = TemporalIntegrationActivities()

    with pytest.raises(TemporalActivityRuntimeError):
        await activities.merge_automation_request_automated_review(
            _payload(provider="not-configured", requestKey="")
        )


async def test_stale_head_is_returned_without_raising(ledger_session, monkeypatch) -> None:
    _install_fake_github(
        monkeypatch,
        [
            _outcome(
                "stale_head",
                requestCommentId=None,
                requestedAt=None,
                observedHeadSha="f" * 40,
                summary="head advanced",
            )
        ],
    )
    activities = TemporalIntegrationActivities()

    result = await activities.merge_automation_request_automated_review(_payload())

    assert result["status"] == "stale_head"
    assert result["observedHeadSha"] == "f" * 40

    async with ledger_session() as session:
        record = await session.get(
            MergeAutomationReviewRequestRecord, _payload()["requestKey"]
        )
        assert record.status == "failed"


async def test_distinct_heads_get_distinct_request_identities() -> None:
    other_head = "9" * 40
    assert build_review_request_key(
        parent_workflow_id=_PARENT,
        repository=_REPO,
        pr_number=350,
        head_sha=_HEAD,
        provider="codex",
    ) != build_review_request_key(
        parent_workflow_id=_PARENT,
        repository=_REPO,
        pr_number=350,
        head_sha=other_head,
        provider="codex",
    )
