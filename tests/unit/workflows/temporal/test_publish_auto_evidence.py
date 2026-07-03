from __future__ import annotations

import json

import pytest

from moonmind.workflows.temporal.publish_auto_evidence import (
    AutoPublishEvidenceError,
    parse_auto_publish_evidence,
)


def _evidence(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": "moonmind.publish.auto.v1",
        "mode": "auto",
        "owner": "agent",
        "skillId": "fix-ci",
        "status": "verified",
        "action": "push",
        "repository": "MoonLadderStudios/MoonMind",
        "branch": "feature/example",
        "localHead": "abc123",
        "remoteBranchHead": "abc123",
        "remoteVerified": True,
        "pushed": True,
        "merged": False,
        "prUrl": None,
        "blockedReason": None,
        "verificationCommands": [
            "git rev-parse HEAD",
            "git ls-remote origin refs/heads/feature/example",
        ],
    }
    payload.update(overrides)
    return payload


def test_parse_auto_publish_evidence_accepts_verified_push() -> None:
    """Auto evidence contract source: docs/Workflows/WorkflowPublishing.md."""

    evidence = parse_auto_publish_evidence(json.dumps(_evidence()).encode())

    assert evidence.status == "verified"
    assert evidence.action == "push"
    assert evidence.finish_code == "PUBLISHED_BRANCH"
    assert evidence.remote_verified is True


def test_parse_auto_publish_evidence_accepts_verified_merge() -> None:
    evidence = parse_auto_publish_evidence(
        json.dumps(
            _evidence(
                action="merge",
                pushed=False,
                merged=True,
                remoteVerified=False,
                remoteBranchHead=None,
                prUrl="https://github.com/MoonLadderStudios/MoonMind/pull/123",
            )
        ).encode()
    )

    assert evidence.finish_code == "PUBLISHED_PR"


def test_parse_auto_publish_evidence_accepts_verified_no_op() -> None:
    evidence = parse_auto_publish_evidence(
        json.dumps(
            _evidence(
                status="no_op_verified",
                action="none",
                pushed=False,
            )
        ).encode()
    )

    assert evidence.finish_code == "NO_COMMIT"


def test_parse_auto_publish_evidence_accepts_blocked_publish_unavailable() -> None:
    evidence = parse_auto_publish_evidence(
        json.dumps(
            _evidence(
                status="blocked",
                action="none",
                pushed=False,
                remoteVerified=False,
                blockedReason="publish_unavailable",
            )
        ).encode()
    )

    assert evidence.status == "blocked"
    assert evidence.blocked_reason == "publish_unavailable"


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"status": "published"}, "unsupported auto publish status"),
        ({"action": "force_push"}, "unsupported auto publish action"),
        ({"remoteBranchHead": "def456"}, "localHead must match remoteBranchHead"),
        ({"pushed": False, "merged": False}, "verified evidence must prove"),
        ({"status": "blocked", "blockedReason": None}, "blockedReason"),
    ],
)
def test_parse_auto_publish_evidence_rejects_invalid_or_unproven_payloads(
    overrides: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(AutoPublishEvidenceError, match=match):
        parse_auto_publish_evidence(json.dumps(_evidence(**overrides)).encode())


def test_parse_auto_publish_evidence_rejects_malformed_json() -> None:
    with pytest.raises(AutoPublishEvidenceError, match="valid JSON object"):
        parse_auto_publish_evidence(b"{")
