from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from moonmind.codex_conformance.canary import (
    CANARY_CANDIDATE_DIGEST_MISMATCH,
    CANARY_DUPLICATE_EXECUTION,
    CANARY_EVIDENCE_UNSAFE,
    CANARY_PROVIDER_UNAVAILABLE,
    CANARY_RESULT_STALE,
    CANARY_SCENARIO_VERSION,
    CANARY_SESSION_TERMINATED_EARLY,
    CANARY_TERMINAL_MARKER_MISSING,
    DEFAULT_MARKER_PATH,
    validate_canary_evidence,
)


def _ts(offset: int) -> str:
    base = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
    return (base + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


def _valid_evidence() -> dict:
    return {
        "schemaVersion": "v1",
        "issueRef": "MoonLadderStudios/MoonMind#3150",
        "scenarioVersion": CANARY_SCENARIO_VERSION,
        "candidateImageDigest": "sha256:" + "a" * 64,
        "candidateImageRef": "ghcr.io/moonladderstudios/moonmind:canary",
        "codexCliVersion": "codex-cli 0.104.0",
        "codexAppServerVersion": "app-server 1",
        "moonmindBuildSha": "b" * 40,
        "runId": "run-1",
        "workflowId": "wf-1",
        "sessionId": "session-1",
        "sessionIdsObserved": ["session-1"],
        "turnId": "turn-1",
        "markerArtifactRef": "artifact://codex-canary/marker",
        "markerPath": DEFAULT_MARKER_PATH,
        "marker": {
            "schemaVersion": "v1",
            "scenarioVersion": CANARY_SCENARIO_VERSION,
            "nonce": "nonce-123456",
            "command": "sleep 3 && printf ok",
            "processExitCode": 0,
            "startedAt": _ts(0),
            "completedAt": _ts(4),
            "durationSeconds": 4.0,
            "outputSha256": "c" * 64,
        },
        "timestamps": {
            "processStart": _ts(0),
            "firstToolYield": _ts(1),
            "subsequentPoll": _ts(2),
            "processComplete": _ts(4),
            "markerCreation": _ts(5),
            "turnComplete": _ts(6),
            "cleanup": _ts(7),
        },
        "protocolEvents": ["resumable_process_handle", "poll_after_yield"],
        "finalAgentStatus": "completed",
        "agentRunResultSuccessful": True,
        "cleanupObserved": True,
        "cleanupSessionId": "session-1",
        "githubMutationCount": 0,
        "processInvocationCount": 1,
        "markerArtifactCreateCount": 1,
        "providerAvailable": True,
        "failureCode": None,
        "evidenceArtifactRef": "artifact://codex-canary/evidence",
    }


def test_marker_produced_after_delayed_completion_passes() -> None:
    result = validate_canary_evidence(
        _valid_evidence(),
        expected_candidate_digest="sha256:" + "a" * 64,
        now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC),
    )

    assert result.passed is True
    assert result.reason_code is None
    assert result.marker_artifact_ref == "artifact://codex-canary/marker"


def test_marker_missing_fails_closed() -> None:
    evidence = _valid_evidence()
    evidence["markerPath"] = "var/conformance/other.json"

    result = validate_canary_evidence(evidence, now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC))

    assert result.passed is False
    assert result.reason_code == CANARY_TERMINAL_MARKER_MISSING


def test_session_cleanup_before_marker_fails_closed() -> None:
    evidence = _valid_evidence()
    evidence["timestamps"]["cleanup"] = _ts(3)

    result = validate_canary_evidence(evidence, now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC))

    assert result.passed is False
    assert result.reason_code == "CANARY_TOOL_PROTOCOL_INCOMPATIBLE"


def test_session_id_change_fails_as_early_termination() -> None:
    evidence = _valid_evidence()
    evidence["sessionIdsObserved"] = ["session-1", "session-2"]

    result = validate_canary_evidence(evidence, now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC))

    assert result.passed is False
    assert result.reason_code == CANARY_SESSION_TERMINATED_EARLY


def test_duplicate_process_invocation_fails_closed() -> None:
    evidence = _valid_evidence()
    evidence["processInvocationCount"] = 2

    result = validate_canary_evidence(evidence, now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC))

    assert result.passed is False
    assert result.reason_code == CANARY_DUPLICATE_EXECUTION


def test_candidate_digest_mismatch_blocks_promotion() -> None:
    result = validate_canary_evidence(
        _valid_evidence(),
        expected_candidate_digest="sha256:" + "d" * 64,
        now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC),
    )

    assert result.passed is False
    assert result.reason_code == CANARY_CANDIDATE_DIGEST_MISMATCH


def test_stale_conformance_result_rejected() -> None:
    result = validate_canary_evidence(
        _valid_evidence(),
        max_age_hours=1,
        now=datetime(2026, 7, 10, 14, 0, 0, tzinfo=UTC),
    )

    assert result.passed is False
    assert result.reason_code == CANARY_RESULT_STALE


def test_provider_unavailable_is_distinct_from_protocol_failure() -> None:
    evidence = _valid_evidence()
    evidence["providerAvailable"] = False

    result = validate_canary_evidence(evidence, now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC))

    assert result.passed is False
    assert result.reason_code == CANARY_PROVIDER_UNAVAILABLE


def test_credential_like_provider_data_is_rejected_before_publication() -> None:
    evidence = deepcopy(_valid_evidence())
    evidence["protocolEvents"].append("token=ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")

    result = validate_canary_evidence(evidence, now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC))

    assert result.passed is False
    assert result.reason_code == CANARY_EVIDENCE_UNSAFE
