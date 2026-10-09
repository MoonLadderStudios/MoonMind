"""Automatic Codex completion evidence at the portable Skill boundary."""

from __future__ import annotations

import copy
import json
import runpy
from datetime import datetime
from pathlib import Path

import pytest

from pr_resolver_core import ResolverAction, classify_snapshot, normalize_portable_snapshot

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests/fixtures/pr_resolver/codex_automatic_review_4773.json"


@pytest.fixture
def captured():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def snapshot_module():
    return runpy.run_path(
        str(REPO_ROOT / ".agents/skills/pr-resolver/bin/pr_resolve_snapshot.py")
    )


def _evidence(snapshot_module, monkeypatch, captured, **overrides):
    build = snapshot_module["build_automated_review_evidence"]
    # Isolate the evidence decision from GitHub IO; this is the full SHA
    # returned by the repository's commit endpoint for the captured abbreviation.
    monkeypatch.setitem(
        build.__globals__,
        "_resolve_automatic_review_commit",
        lambda **kwargs: captured["head_sha"],
    )
    params = {
        "provider": "codex",
        "require_fresh_review": True,
        "pr_repo": captured["repository"],
        "pr_number": captured["pr_number"],
        "head_sha": captured["head_sha"],
        "head_committed_at": datetime.fromisoformat(
            captured["head_committed_at"].replace("Z", "+00:00")
        ),
        "comments": copy.deepcopy(captured["comments"]),
        "reviews": [],
        "reactions_for_request": [],
        "reactions_for_pr": copy.deepcopy(captured["reactions"]),
    }
    params.update(overrides)
    return build(**params)


def test_captured_pr4773_automatic_review_opens_the_current_head_gate(
    snapshot_module, monkeypatch, captured
):
    evidence = _evidence(snapshot_module, monkeypatch, captured)

    assert evidence["freshReviewForHead"] is True
    assert evidence["requestPending"] is False
    assert evidence["requestCommentId"] is None
    assert evidence["completionKind"] == "automatic_summary"
    assert evidence["completionId"] == 6082098611
    assert evidence["completedAt"] == "2026-10-09T13:45:42.886890+00:00"
    payload = {
        "repository": captured["repository"],
        "pr": {
            "number": captured["pr_number"],
            "state": "OPEN",
            "headRefOid": captured["head_sha"],
            "mergeStateStatus": "CLEAN",
            "mergeable": True,
        },
        "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
        "commentsFetch": {"succeeded": True},
        "commentsSummary": {
            "includeBotReviewComments": True,
            "hasActionableComments": False,
        },
        "automatedReview": evidence,
    }
    assert (
        classify_snapshot(normalize_portable_snapshot(payload)).action
        is ResolverAction.ATTEMPT_MERGE
    )
