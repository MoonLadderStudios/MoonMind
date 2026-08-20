"""Tests for Omnigent live-verification health and safe-failure evidence.

Source issue: MoonLadderStudios/MoonMind#3710.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.live_verification_health import (
    DEFAULT_MAX_LOG_LINES,
    REQUIRED_LIVE_MATRIX_MODES,
    LiveVerificationHealthError,
    build_safe_failure_diagnostics,
    evaluate_live_verification_health,
)

NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
COMMIT = "0123456789abcdef0123456789abcdef01234567"
SERVER_DIGEST = "sha256:" + "a" * 64
HOST_DIGEST = "sha256:" + "b" * 64


def _healthy_status(**overrides):
    status = {
        "runner": {"status": "online", "busy": False},
        "queue": {"oldestQueuedAgeSeconds": 60},
        "latest_run": {
            "status": "success",
            "sourceCommit": COMMIT,
            "startedAt": (NOW - timedelta(minutes=30)).isoformat(),
            "completedAt": (NOW - timedelta(minutes=5)).isoformat(),
            "modes": list(REQUIRED_LIVE_MATRIX_MODES),
        },
        "manifest": {
            "generatedAt": (NOW - timedelta(days=1)).isoformat(),
            "expiresAt": (NOW + timedelta(days=29)).isoformat(),
            "sourceCommit": COMMIT,
            "images": {"serverDigest": SERVER_DIGEST, "hostDigest": HOST_DIGEST},
        },
        "deployed_commit": COMMIT,
        "required_digests": {"server": SERVER_DIGEST, "host": HOST_DIGEST},
        "now": NOW,
    }
    status.update(overrides)
    return status


def _evaluate(**overrides):
    return evaluate_live_verification_health(**_healthy_status(**overrides))


def test_healthy_protected_tier_is_ready() -> None:
    projection = _evaluate()

    assert projection["tier1Ready"] is True
    assert projection["protectedTierReady"] is True
    assert projection["rolloutReady"] is True
    assert projection["notReadyReasons"] == []


def test_offline_runner_fails_closed_but_leaves_tier1_ready() -> None:
    projection = _evaluate(runner={"status": "offline", "busy": False})

    assert projection["tier1Ready"] is True
    assert projection["protectedTierReady"] is False
    assert projection["rolloutReady"] is False
    assert "runner_online" in projection["notReadyReasons"]


def test_queue_beyond_policy_fails_closed() -> None:
    projection = _evaluate(
        queue={"oldestQueuedAgeSeconds": 60 * 60 * 24},
        max_queue_age_seconds=6 * 60 * 60,
    )

    assert projection["rolloutReady"] is False
    assert "queue_within_policy" in projection["notReadyReasons"]


def test_expired_evidence_is_not_ready() -> None:
    projection = _evaluate(
        manifest={
            "generatedAt": (NOW - timedelta(days=40)).isoformat(),
            "expiresAt": (NOW - timedelta(days=10)).isoformat(),
            "sourceCommit": COMMIT,
            "images": {"serverDigest": SERVER_DIGEST, "hostDigest": HOST_DIGEST},
        }
    )

    assert projection["rolloutReady"] is False
    assert "evidence_fresh" in projection["notReadyReasons"]


def test_missing_successful_canary_for_commit_is_not_ready() -> None:
    projection = _evaluate(
        latest_run={
            "status": "success",
            "sourceCommit": "f" * 40,
            "startedAt": (NOW - timedelta(minutes=30)).isoformat(),
            "completedAt": (NOW - timedelta(minutes=5)).isoformat(),
            "modes": list(REQUIRED_LIVE_MATRIX_MODES),
        }
    )

    assert projection["rolloutReady"] is False
    assert "successful_canary" in projection["notReadyReasons"]


def test_silently_dropped_matrix_mode_is_incomplete() -> None:
    projection = _evaluate(
        latest_run={
            "status": "success",
            "sourceCommit": COMMIT,
            "startedAt": (NOW - timedelta(minutes=30)).isoformat(),
            "completedAt": (NOW - timedelta(minutes=5)).isoformat(),
            "modes": [m for m in REQUIRED_LIVE_MATRIX_MODES if m != "workflow_chat"],
        }
    )

    assert projection["rolloutReady"] is False
    assert "matrix_complete" in projection["notReadyReasons"]


def test_digest_mismatch_is_not_ready() -> None:
    projection = _evaluate(
        manifest={
            "generatedAt": (NOW - timedelta(days=1)).isoformat(),
            "expiresAt": (NOW + timedelta(days=29)).isoformat(),
            "sourceCommit": COMMIT,
            "images": {
                "serverDigest": "sha256:" + "c" * 64,
                "hostDigest": HOST_DIGEST,
            },
        }
    )

    assert projection["rolloutReady"] is False
    assert "evidence_digests_match" in projection["notReadyReasons"]


def test_missing_manifest_and_run_are_not_ready() -> None:
    projection = _evaluate(manifest=None, latest_run=None)

    assert projection["rolloutReady"] is False
    reasons = set(projection["notReadyReasons"])
    assert {"successful_canary", "evidence_fresh", "evidence_digests_match"} <= reasons


def test_tier4_outage_does_not_block_pr_tier_when_protected_not_required() -> None:
    projection = _evaluate(tier4_healthy=False, protected_tier_required=False)

    assert projection["tier1Ready"] is True
    assert projection["protectedTierReady"] is False
    # Tier 4 outage must not prevent Tier 1 from protecting PRs.
    assert projection["rolloutReady"] is True
    assert "tier4_soak_healthy" in projection["notReadyReasons"]


def test_tier4_outage_blocks_rollout_when_protected_required() -> None:
    projection = _evaluate(tier4_healthy=False, protected_tier_required=True)

    assert projection["rolloutReady"] is False
    assert "tier4_soak_healthy" in projection["notReadyReasons"]


def test_projection_is_secret_free() -> None:
    with pytest.raises(ConformanceContractError):
        _evaluate(deployed_commit="ghp_secretsecretsecretsecretsecretsecret")


def test_malformed_timestamp_is_rejected() -> None:
    with pytest.raises(LiveVerificationHealthError):
        _evaluate(
            manifest={
                "generatedAt": "not-a-timestamp",
                "expiresAt": (NOW + timedelta(days=29)).isoformat(),
                "sourceCommit": COMMIT,
                "images": {"serverDigest": SERVER_DIGEST, "hostDigest": HOST_DIGEST},
            }
        )


# --- Safe failure diagnostics --------------------------------------------------


def test_safe_failure_diagnostics_redacts_secrets() -> None:
    diagnostics = build_safe_failure_diagnostics(
        mode="product",
        outcome="failure",
        setup_stage="run-credentialed-live-matrix-case",
        runner_health={"status": "online", "token": "ghp_should_be_dropped"},
        failure_summary="auth failed with token=ghp_abcdefabcdefabcdefabcdef",
        log_tail=[
            "starting case",
            "Authorization: Bearer github_pat_11ABCDEFG",
            "case failed",
        ],
        duration_seconds=42.5,
        now=NOW,
    )

    assert diagnostics["status"] == "failed"
    assert diagnostics["mode"] == "product"
    assert diagnostics["durationSeconds"] == 42.5
    assert diagnostics["setupStage"] == "run-credentialed-live-matrix-case"
    assert "ghp_abcdef" not in diagnostics["failureSummary"]
    assert "[redacted]" in diagnostics["failureSummary"]
    # Secret-like runner-health keys are dropped entirely.
    assert "token" not in diagnostics["runnerHealth"]
    assert diagnostics["runnerHealth"]["status"] == "online"
    joined = "\n".join(diagnostics["logTail"])
    assert "github_pat_11ABCDEFG" not in joined
    assert diagnostics["secretScan"]["status"] == "passed"


def test_safe_failure_diagnostics_bounds_log_tail() -> None:
    many_lines = [f"line {i}" for i in range(DEFAULT_MAX_LOG_LINES + 50)]
    diagnostics = build_safe_failure_diagnostics(
        mode="failures",
        outcome="failure",
        setup_stage="setup",
        failure_summary="too many logs",
        log_tail=many_lines,
        now=NOW,
    )

    assert len(diagnostics["logTail"]) == DEFAULT_MAX_LOG_LINES
    assert diagnostics["logTailTruncated"] is True
    assert diagnostics["logTail"][-1] == many_lines[-1]


def test_safe_failure_diagnostics_truncates_long_lines() -> None:
    diagnostics = build_safe_failure_diagnostics(
        mode="static",
        outcome="failure",
        setup_stage="setup",
        failure_summary="x" * 2000,
        log_tail=["y" * 2000],
        max_line_length=100,
        now=NOW,
    )

    assert len(diagnostics["failureSummary"]) == 100
    assert len(diagnostics["logTail"][0]) == 100


def test_safe_failure_diagnostics_requires_core_fields() -> None:
    with pytest.raises(LiveVerificationHealthError):
        build_safe_failure_diagnostics(
            mode="",
            outcome="failure",
            setup_stage="setup",
            failure_summary="summary",
        )
    with pytest.raises(LiveVerificationHealthError):
        build_safe_failure_diagnostics(
            mode="browser",
            outcome="failure",
            setup_stage="setup",
            failure_summary="   ",
        )


# --- Complete credential redaction -------------------------------------------


def test_bearer_authorization_header_is_redacted_in_full() -> None:
    """Redaction must remove the credential, not just the header name.

    A pattern that stops at the first separator leaves the token after the
    whitespace verbatim in the published ``logTail`` while the document still
    claims ``secretScan.status == "passed"``, converting withheld failure output
    into a credential leak.
    """
    secret = "sk-live-9f8e7d6c5b4a39281706"
    diagnostics = build_safe_failure_diagnostics(
        mode="product",
        outcome="failure",
        setup_stage="host_registration",
        failure_summary="request rejected",
        log_tail=[f"GET /v1/hosts Authorization: Bearer {secret}"],
        now=NOW,
    )

    joined = "\n".join(diagnostics["logTail"])
    assert secret not in joined
    assert "Bearer" not in joined
    assert diagnostics["secretScan"]["status"] == "passed"
    # The published document must independently pass secret scanning.
    from moonmind.omnigent.conformance import assert_secret_free

    assert_secret_free(diagnostics)


@pytest.mark.parametrize(
    "line, secret",
    [
        (
            "set-cookie: session=8d2f4c9ab13e5f70; Path=/; HttpOnly",
            "8d2f4c9ab13e5f70",
        ),
        (
            "cookie: mm_session=41f0e9cbd7a2; theme=dark",
            "41f0e9cbd7a2",
        ),
        (
            "assertion eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jV",
            "dozjgNryP4J3jV",
        ),
        (
            "x-api-key: 7c1d9e0fa5b34826",
            "7c1d9e0fa5b34826",
        ),
        (
            "proxy-authorization: Basic bW9vbjptaW5kMTIzNA==",
            "bW9vbjptaW5kMTIzNA==",
        ),
    ],
)
def test_session_bearing_material_is_redacted(line: str, secret: str) -> None:
    diagnostics = build_safe_failure_diagnostics(
        mode="browser",
        outcome="failure",
        setup_stage="browser_capture",
        failure_summary="capture failed",
        log_tail=[line],
        now=NOW,
    )

    assert secret not in "\n".join(diagnostics["logTail"])


def test_line_truncation_cannot_reexpose_a_trimmed_credential() -> None:
    """Redaction happens before bounding, so a long line stays safe."""
    secret = "ghp_" + "0123456789abcdef" * 2
    diagnostics = build_safe_failure_diagnostics(
        mode="product",
        outcome="failure",
        setup_stage="host_registration",
        failure_summary="failed",
        log_tail=["x" * 480 + " " + secret],
        max_line_length=520,
        now=NOW,
    )

    assert secret not in "\n".join(diagnostics["logTail"])


def test_runner_health_drops_session_bearing_keys() -> None:
    diagnostics = build_safe_failure_diagnostics(
        mode="product",
        outcome="failure",
        setup_stage="host_registration",
        failure_summary="failed",
        runner_health={
            "status": "online",
            "Cookie": "session=abcd1234efgh",
            "authorization": "Bearer nope1234nope",
        },
        now=NOW,
    )

    assert diagnostics["runnerHealth"] == {"status": "online"}


# --- Projection freshness revalidation at consumption ------------------------


def _projection(**overrides):
    projection = _evaluate()
    projection.update(overrides)
    return projection


def test_assert_live_health_projection_accepts_a_current_ready_projection() -> None:
    from moonmind.omnigent.live_verification_health import (
        assert_live_health_projection,
    )

    assert_live_health_projection(_projection(), expected_commit=COMMIT, now=NOW)


def test_assert_live_health_projection_rejects_expired_acceptance_window() -> None:
    from moonmind.omnigent.live_verification_health import (
        assert_live_health_projection,
    )

    projection = _projection()
    with pytest.raises(LiveVerificationHealthError):
        assert_live_health_projection(
            projection, expected_commit=COMMIT, now=NOW + timedelta(days=30)
        )


def test_assert_live_health_projection_rejects_a_stalled_publisher() -> None:
    from moonmind.omnigent.live_verification_health import (
        assert_live_health_projection,
    )

    projection = _projection()
    with pytest.raises(LiveVerificationHealthError):
        assert_live_health_projection(
            projection, expected_commit=COMMIT, now=NOW + timedelta(hours=6)
        )


def test_assert_live_health_projection_rejects_foreign_schema_and_commit() -> None:
    from moonmind.omnigent.live_verification_health import (
        assert_live_health_projection,
    )

    with pytest.raises(LiveVerificationHealthError):
        assert_live_health_projection(
            _projection(schemaVersion="other/v1"), expected_commit=COMMIT, now=NOW
        )
    with pytest.raises(LiveVerificationHealthError):
        assert_live_health_projection(
            _projection(), expected_commit="different", now=NOW
        )
    with pytest.raises(LiveVerificationHealthError):
        assert_live_health_projection(
            _projection(rolloutReady=False), expected_commit=COMMIT, now=NOW
        )
