from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from moonmind.codex_conformance.canary import (
    CANARY_DUPLICATE_EXECUTION,
    CANARY_SCENARIO_VERSION,
    CANARY_TIMEOUT,
    CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
    DEFAULT_MARKER_PATH,
    validate_canary_evidence,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNNER_PATH = _REPO_ROOT / "tools" / "run_codex_conformance_canary.py"
_SPEC = importlib.util.spec_from_file_location("run_codex_conformance_canary", _RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


class _Response:
    def __init__(self, status_code: int, payload: Any | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://moonmind.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("request failed", request=request, response=response)


class _FakeClient:
    def __init__(self, routes: dict[str, _Response]) -> None:
        self.routes = routes
        self.requested: list[str] = []

    def get(self, url: str, **_kwargs: Any) -> _Response:
        self.requested.append(url)
        return self.routes.get(url, _Response(404, {"detail": "not found"}))


def _ts(offset: int) -> str:
    base = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
    return (base + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


def _marker(*, nonce: str = "nonce-123456") -> dict[str, Any]:
    return {
        "schemaVersion": "v1",
        "scenarioVersion": CANARY_SCENARIO_VERSION,
        "nonce": nonce,
        "command": "sleep 4 && printf ok",
        "processExitCode": 0,
        "startedAt": _ts(0),
        "completedAt": _ts(4),
        "durationSeconds": 4.0,
        "outputSha256": "a" * 64,
    }


def _observation(**overrides: Any) -> dict[str, Any]:
    observation = {
        "sessionId": "session-1",
        "testedImageDigest": "sha256:" + "b" * 64,
        "sessionIdsObserved": ["session-1"],
        "turnId": "turn-1",
        "markerArtifactRef": "marker-artifact",
        "markerPath": DEFAULT_MARKER_PATH,
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
        "cleanupObserved": True,
        "cleanupSessionId": "session-1",
        "githubMutationCount": 0,
        "processInvocationCount": 1,
        "markerArtifactCreateCount": 1,
    }
    observation.update(overrides)
    return observation


def _client_with_observation(
    observation: dict[str, Any],
    marker: dict[str, Any] | None = None,
) -> _FakeClient:
    api_url = "https://moonmind.test"
    return _FakeClient(
        {
            f"{api_url}/api/executions/wf-1/steps": _Response(
                200,
                {
                    "steps": [
                        {
                            "id": "codex-long-command-canary",
                            "refs": {"agentRunId": "agent-run-1"},
                            "codexConformanceCanary": observation,
                        }
                    ]
                },
            ),
            f"{api_url}/api/executions/moonmind/wf-1/run-1/artifacts": _Response(200, {"artifacts": []}),
            f"{api_url}/api/artifacts/marker-artifact/download": _Response(200, marker or _marker()),
        }
    )


def _assemble(client: _FakeClient, *, nonce: str = "nonce-123456") -> dict[str, Any]:
    return runner._assemble_success_evidence(
        client=client,
        api_url="https://moonmind.test",
        latest={"workflowId": "wf-1", "runId": "run-1", "status": "completed"},
        candidate_digest="sha256:" + "b" * 64,
        candidate_ref="ghcr.io/moonladderstudios/moonmind:canary",
        nonce=nonce,
    )


def test_runner_assembles_success_from_api_observation_and_marker_artifact() -> None:
    client = _client_with_observation(_observation())

    evidence = _assemble(client)

    assert evidence["marker"] == _marker()
    assert evidence["sessionId"] == "session-1"
    assert evidence["processInvocationCount"] == 1
    assert evidence["markerArtifactCreateCount"] == 1
    assert f"https://moonmind.test/api/artifacts/marker-artifact/download" in client.requested
    result = validate_canary_evidence(
        evidence,
        expected_candidate_digest="sha256:" + "b" * 64,
        now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC),
    )
    assert result.passed is True


def test_runner_submits_configured_profile_under_provider_profile_ref() -> None:
    payload = runner._workflow_payload(nonce="nonce-123456", profile_ref="codex-prod")

    runtime = payload["payload"]["workflow"]["runtime"]
    assert runtime["providerProfileRef"] == "codex-prod"
    assert "profileRef" not in runtime


def test_runner_reads_local_managed_session_marker_from_context() -> None:
    marker = _marker()
    observation = _observation(
        markerArtifactRef="session-1/codex_conformance_canary.marker.json",
        marker=marker,
    )
    client = _client_with_observation(observation)

    evidence = _assemble(client)

    assert evidence["marker"] == marker
    assert (
        "https://moonmind.test/api/artifacts/"
        "session-1%2Fcodex_conformance_canary.marker.json/download"
        not in client.requested
    )


def test_runner_requires_observed_candidate_digest() -> None:
    client = _client_with_observation(_observation(testedImageDigest=""))

    try:
        _assemble(client)
    except RuntimeError as exc:
        assert "tested runtime image digest" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected missing tested digest to fail")


def test_runner_rejects_observed_candidate_digest_mismatch() -> None:
    client = _client_with_observation(_observation(testedImageDigest="sha256:" + "c" * 64))

    try:
        _assemble(client)
    except RuntimeError as exc:
        assert "does not match requested candidate digest" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected digest mismatch to fail")


def test_runner_writes_timeout_failure_with_timeout_reason(tmp_path: Path) -> None:
    output = tmp_path / "canary-result.json"

    rc = runner._canary_failure_evidence(
        candidate_digest="sha256:" + "b" * 64,
        candidate_ref="ghcr.io/moonladderstudios/moonmind:canary",
        output_path=output,
        nonce="nonce-123456",
        reason_code=CANARY_TIMEOUT,
        message="TimeoutError: workflow did not complete",
    )

    assert rc == 1
    result = validate_canary_evidence(
        json.loads(output.read_text(encoding="utf-8")),
        expected_candidate_digest="sha256:" + "b" * 64,
        now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC),
    )
    assert result.passed is False
    assert result.reason_code == CANARY_TIMEOUT


def test_runner_requires_authoritative_canary_observation() -> None:
    client = _FakeClient({"https://moonmind.test/api/executions/wf-1/steps": _Response(200, {"steps": []})})

    try:
        _assemble(client)
    except RuntimeError as exc:
        assert "authoritative canary observations" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected missing observation to fail")


def test_runner_requires_readable_marker_artifact() -> None:
    observation = _observation(markerArtifactRef="missing-marker")
    client = _client_with_observation(observation)

    try:
        _assemble(client)
    except RuntimeError as exc:
        assert "marker artifact" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected missing marker download to fail")


def test_runner_rejects_marker_nonce_mismatch() -> None:
    client = _client_with_observation(_observation(), marker=_marker(nonce="nonce-other"))

    try:
        _assemble(client)
    except RuntimeError as exc:
        assert "nonce" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected nonce mismatch to fail")


def test_runner_sources_duplicate_process_count_from_observation() -> None:
    client = _client_with_observation(_observation(processInvocationCount=2))

    evidence = _assemble(client)
    result = validate_canary_evidence(
        evidence,
        now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC),
    )

    assert result.passed is False
    assert result.reason_code == CANARY_DUPLICATE_EXECUTION


def test_runner_sources_missing_resumable_handle_from_observation() -> None:
    client = _client_with_observation(_observation(protocolEvents=["poll_after_yield"]))

    evidence = _assemble(client)
    result = validate_canary_evidence(
        evidence,
        now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC),
    )

    assert result.passed is False
    assert result.reason_code == CANARY_TOOL_PROTOCOL_INCOMPATIBLE
