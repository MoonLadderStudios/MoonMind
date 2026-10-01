"""The 5-minute GitHub issue reconcile must not re-read unchanged GitHub state.

2026-10-01: every run re-read the same no-op issues, re-read out-of-scope
issues one GET at a time, and scanned MoonMind twice under two spellings. The
shared account's 5,000/hour REST budget went to unchanged state, so agents and
pr-resolver runs failed on rate limits. An issue is re-read when its listing
``updated_at`` changes, when it carries a pending effect, or when its last
outcome was not a recorded no-op.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import httpx
import pytest

from moonmind.workflows.temporal import github_issue_reconciliation as recon
from moonmind.workflows.temporal.activities import (
    github_issue_reconciliation_activities as acts,
)
from tests.unit.workflows.temporal.test_github_issue_reconciliation_4182 import (
    FakeGitHubService,
    _handoff_body,
    _issue,
)

REPO = "o/r"
_RELEASED = {"pending_disposition": "", "activity": "released", "pr_url": "", "pr_head_sha": "", "pr_base": ""}


class CountingService(FakeGitHubService):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reads: Counter[tuple[str, int]] = Counter()

    async def get_issue(self, *, repo: str, issue_number: int) -> dict[str, Any]:
        self.reads["issue", issue_number] += 1
        return await super().get_issue(repo=repo, issue_number=issue_number)

    async def list_issue_comments(self, *, repo: str, issue_number: int, github_token=None) -> dict[str, Any]:
        self.reads["comments", issue_number] += 1
        return await super().list_issue_comments(repo=repo, issue_number=issue_number)


@pytest.fixture
def listing(monkeypatch):
    """Serve the open-issue listing the scan reads, from the same issue state."""

    served: dict[str, Any] = {"service": None, "updated": {}}
    original = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        if int(request.url.params.get("page", "1")) > 1:
            return httpx.Response(200, json=[])
        service = served["service"]
        return httpx.Response(
            200,
            json=[
                {
                    "number": number,
                    "state": issue.get("state", "open"),
                    "labels": [{"name": name} for name in issue.get("labels", [])],
                    "updated_at": served["updated"].get(number, "2026-09-30T00:00:00Z"),
                }
                for number, issue in sorted(service.issues.items())
            ],
        )

    class Client(original):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    return served


def _service() -> CountingService:
    return CountingService(
        issues={
            1: _issue(),  # available: owned by the normal admission path
            2: _issue("status: code-review"),
            3: _issue("status: needs-attention"),
        },
        comments={
            2: [_handoff_body(issue_number=2, **_RELEASED)],
            3: [_handoff_body(issue_number=3, **_RELEASED)],
        },
    )


async def _scan(service: CountingService, tmp_path) -> dict[str, Any]:
    return await acts.reconcile_github_issue_handoffs(
        repository=REPO, state_dir=str(tmp_path), service=service
    )


def _outcomes(result: dict[str, Any]) -> dict[int, tuple[str, str]]:
    return {item["issueNumber"]: (item["action"], item["reasonCode"]) for item in result["results"]}


@pytest.mark.asyncio
async def test_repeat_scan_does_not_re_read_unchanged_no_op_issues(listing, tmp_path) -> None:
    service = _service()
    listing["service"] = service

    first = await _scan(service, tmp_path)

    expected = {
        1: (recon.ACTION_NO_ACTION, "owned_by_normal_path"),
        2: (recon.ACTION_NO_ACTION, "no_interrupted_transition"),
        3: (recon.ACTION_NO_ACTION, "no_interrupted_transition"),
    }
    assert _outcomes(first) == expected
    # The listing already proves issue 1 is out of scope: no per-issue GET.
    assert service.reads == Counter({("issue", 2): 1, ("comments", 2): 1, ("issue", 3): 1, ("comments", 3): 1})

    service.reads.clear()
    second = await _scan(service, tmp_path)

    assert _outcomes(second) == expected
    assert service.reads == Counter()
    assert sum(item["apiRequests"] for item in second["results"]) == 0

    # Any label or comment change bumps updated_at; that issue is fully re-read.
    listing["updated"][2] = "2026-10-01T01:40:00Z"
    third = await _scan(service, tmp_path)

    assert _outcomes(third) == expected
    assert service.reads == Counter({("issue", 2): 1, ("comments", 2): 1})


@pytest.mark.asyncio
async def test_an_issue_with_a_pending_effect_is_always_re_read(listing, tmp_path) -> None:
    service = _service()
    listing["service"] = service
    await _scan(service, tmp_path)
    pending_path = tmp_path / "pending_sync.json"
    state = acts._load_json(pending_path)
    state.update(
        recon.record_pending_effect(
            state,
            repository=REPO,
            issue_number=2,
            intended_from_settled="code_review",
            intended_to_target="to_available",
            proposed_disposition="available",
            reason="fixture: interrupted repair",
        )
    )
    acts._save_json(pending_path, state)

    service.reads.clear()
    await _scan(service, tmp_path)

    assert service.reads["issue", 2] >= 1
    assert service.reads["issue", 3] == 0


@pytest.mark.asyncio
async def test_a_retained_settled_status_is_not_re_read_until_the_issue_changes(
    listing, tmp_path, monkeypatch
) -> None:
    """The observed steady state: expired claims on an issue settled elsewhere."""

    from moonmind.workflows.temporal import github_issue_claim_lease as leases

    service = CountingService(issues={5: _issue("status: code-review")}, comments={5: []})
    listing["service"] = service
    reassessed: list[int] = []

    async def retained(*, service, repository, issue_number, **_kwargs):
        reassessed.append(issue_number)
        return {"reclaimed": False, "reasonCode": "settled_status_retained"}

    monkeypatch.setattr(leases, "classified_attempts", lambda *_a, **_k: ([], [({}, object())], 1))
    monkeypatch.setattr(leases, "reconcile_expired_issue", retained)

    first = await _scan(service, tmp_path)
    second = await _scan(service, tmp_path)

    assert _outcomes(first) == _outcomes(second) == {5: (recon.ACTION_NO_ACTION, "settled_status_retained")}
    assert reassessed == [5]
    assert service.reads == Counter({("issue", 5): 1, ("comments", 5): 1})


@pytest.mark.asyncio
async def test_one_repository_is_swept_once_whatever_its_spelling(monkeypatch, tmp_path) -> None:
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal import github_issue_claim_recovery as recovery

    async def local_claims(*, state):
        return {"repositories": ["moonladderstudios/moonmind"], "results": []}

    swept: list[str] = []

    async def sweep(*, repository, **_kwargs):
        swept.append(repository)
        return {"ok": True, "repository": repository, "scan": {"status": recon.SCAN_COMPLETE}}

    monkeypatch.setattr(recovery, "reconcile_local_claims", local_claims)
    monkeypatch.setattr(acts, "reconcile_github_issue_handoffs", sweep)
    monkeypatch.setattr(settings.workflow, "github_repository", "MoonLadderStudios/MoonMind")

    await acts.reconcile_local_github_issue_claims(state_dir=str(tmp_path))
    await acts.reconcile_local_github_issue_claims(state_dir=str(tmp_path))

    # The configured spelling wins; the lowercased receipt adds no second scan.
    assert swept == ["MoonLadderStudios/MoonMind", "MoonLadderStudios/MoonMind"]
