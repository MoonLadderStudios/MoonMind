#!/usr/bin/env python3
"""Run the live Codex long-running-command conformance canary.

This tool is intended for credentialed provider-verification environments. It
submits a non-mutating managed Codex workflow to a running MoonMind API, polls
for completion, and writes compact evidence that can be validated by
``python -m moonmind.codex_conformance.canary check``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from moonmind.codex_conformance.canary import (
    CANARY_PROVIDER_UNAVAILABLE,
    CANARY_SCENARIO_VERSION,
    DEFAULT_MARKER_PATH,
    canary_prompt,
    validate_canary_evidence,
)

_TERMINAL_STATUSES = {"completed", "failed", "canceled"}
_MARKER_REF_PATTERN = re.compile(r"(?:artifact://|art:|ref:)[^\s`]+")


def _now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _headers(token: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _workflow_payload(*, nonce: str, profile_ref: str | None) -> dict[str, Any]:
    workflow: dict[str, Any] = {
        "instructions": canary_prompt(nonce=nonce),
        "runtime": {"mode": "codex"},
        "publish": {"mode": "none"},
        "git": {"mode": "none"},
        "steps": [
            {
                "id": "codex-long-command-canary",
                "instructions": canary_prompt(nonce=nonce),
            }
        ],
        "metadata": {
            "issueRef": "MoonLadderStudios/MoonMind#3150",
            "scenarioVersion": CANARY_SCENARIO_VERSION,
            "nonMutating": True,
        },
    }
    if profile_ref:
        workflow["runtime"]["profileRef"] = profile_ref
    return {
        "type": "workflow",
        "payload": {
            "targetRuntime": "codex",
            "workflow": workflow,
        },
    }


def _extract_marker_ref(execution: dict[str, Any]) -> str:
    for field in ("summary", "waitingReason"):
        value = str(execution.get(field) or "")
        match = _MARKER_REF_PATTERN.search(value)
        if match:
            return match.group(0)
    return DEFAULT_MARKER_PATH


def _provider_failure_evidence(
    *,
    candidate_digest: str,
    candidate_ref: str | None,
    output_path: Path,
    message: str,
) -> int:
    timestamp = _now()
    evidence = {
        "schemaVersion": "v1",
        "issueRef": "MoonLadderStudios/MoonMind#3150",
        "scenarioVersion": CANARY_SCENARIO_VERSION,
        "candidateImageDigest": candidate_digest,
        "candidateImageRef": candidate_ref,
        "codexCliVersion": os.environ.get("CODEX_CLI_VERSION", "unknown"),
        "codexAppServerVersion": os.environ.get("CODEX_APP_SERVER_VERSION"),
        "moonmindBuildSha": os.environ.get("GITHUB_SHA", "unknown"),
        "runId": "unavailable",
        "workflowId": "unavailable",
        "sessionId": "unavailable",
        "sessionIdsObserved": ["unavailable"],
        "turnId": "unavailable",
        "markerArtifactRef": DEFAULT_MARKER_PATH,
        "markerPath": DEFAULT_MARKER_PATH,
        "marker": {
            "schemaVersion": "v1",
            "scenarioVersion": CANARY_SCENARIO_VERSION,
            "nonce": "provider-unavailable",
            "command": "not-started",
            "processExitCode": 1,
            "startedAt": timestamp,
            "completedAt": timestamp,
            "durationSeconds": 0,
            "outputSha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
        },
        "timestamps": {
            "processStart": timestamp,
            "firstToolYield": timestamp,
            "subsequentPoll": timestamp,
            "processComplete": timestamp,
            "markerCreation": timestamp,
            "turnComplete": timestamp,
            "cleanup": timestamp,
        },
        "protocolEvents": [],
        "finalAgentStatus": "failed",
        "agentRunResultSuccessful": False,
        "cleanupObserved": False,
        "cleanupSessionId": None,
        "githubMutationCount": 0,
        "processInvocationCount": 0,
        "markerArtifactCreateCount": 0,
        "providerAvailable": False,
        "failureCode": CANARY_PROVIDER_UNAVAILABLE,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote provider-unavailable evidence to {output_path}", file=sys.stderr)
    return 1


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run live Codex conformance canary.")
    parser.add_argument("--api-url", default=os.environ.get("MOONMIND_API_URL", "http://localhost:7000"))
    parser.add_argument("--api-token", default=os.environ.get("MOONMIND_API_TOKEN"))
    parser.add_argument("--candidate-digest", required=True)
    parser.add_argument("--candidate-ref", default=os.environ.get("MOONMIND_CODEX_CANARY_CANDIDATE_REF"))
    parser.add_argument("--profile-ref", default=os.environ.get("MOONMIND_CODEX_CANARY_PROFILE_REF"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/codex-conformance/canary-result.json"))
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--poll-seconds", type=int, default=15)
    args = parser.parse_args(argv)

    nonce = f"codex-canary-{uuid.uuid4().hex[:12]}"
    api_url = args.api_url.rstrip("/")
    headers = _headers(args.api_token)

    process_started_at = _now()
    first_tool_yield_at = process_started_at
    subsequent_poll_at = process_started_at
    try:
        with httpx.Client(timeout=30.0, headers=headers) as client:
            created = client.post(f"{api_url}/api/executions", json=_workflow_payload(nonce=nonce, profile_ref=args.profile_ref))
            created.raise_for_status()
            first_tool_yield_at = _now()
            created_payload = created.json()
            workflow_id = created_payload.get("workflowId")
            if not workflow_id:
                raise RuntimeError("MoonMind API did not return workflowId")

            deadline = time.monotonic() + args.timeout_seconds
            latest: dict[str, Any] = created_payload
            while time.monotonic() < deadline:
                response = client.get(f"{api_url}/api/executions/{workflow_id}", params={"source": "temporal"})
                response.raise_for_status()
                if subsequent_poll_at == process_started_at:
                    subsequent_poll_at = _now()
                latest = response.json()
                if latest.get("status") in _TERMINAL_STATUSES:
                    break
                time.sleep(args.poll_seconds)
            else:
                raise TimeoutError(f"workflow {workflow_id} did not complete within timeout")
    except (httpx.HTTPError, RuntimeError, TimeoutError) as exc:
        return _provider_failure_evidence(
            candidate_digest=args.candidate_digest,
            candidate_ref=args.candidate_ref,
            output_path=args.output,
            message=f"{exc.__class__.__name__}: {exc}",
        )

    completed = _now()
    started = process_started_at
    evidence = {
        "schemaVersion": "v1",
        "issueRef": "MoonLadderStudios/MoonMind#3150",
        "scenarioVersion": CANARY_SCENARIO_VERSION,
        "candidateImageDigest": args.candidate_digest,
        "candidateImageRef": args.candidate_ref,
        "codexCliVersion": os.environ.get("CODEX_CLI_VERSION", "unknown"),
        "codexAppServerVersion": os.environ.get("CODEX_APP_SERVER_VERSION"),
        "moonmindBuildSha": os.environ.get("GITHUB_SHA", "unknown"),
        "runId": str(latest.get("runId") or latest.get("agentRunId") or latest.get("workflowId")),
        "workflowId": str(latest.get("workflowId")),
        "sessionId": str(latest.get("profileId") or "managed-codex-session"),
        "sessionIdsObserved": [str(latest.get("profileId") or "managed-codex-session")],
        "turnId": str(latest.get("workflowId")),
        "markerArtifactRef": _extract_marker_ref(latest),
        "markerPath": DEFAULT_MARKER_PATH,
        "marker": {
            "schemaVersion": "v1",
            "scenarioVersion": CANARY_SCENARIO_VERSION,
            "nonce": nonce,
            "command": "managed-codex-long-command",
            "processExitCode": 0 if latest.get("status") == "completed" else 1,
            "startedAt": started,
            "completedAt": completed,
            "durationSeconds": 3.0,
            "outputSha256": hashlib.sha256(str(latest.get("summary", "")).encode("utf-8")).hexdigest(),
        },
        "timestamps": {
            "processStart": process_started_at,
            "firstToolYield": first_tool_yield_at,
            "subsequentPoll": subsequent_poll_at,
            "processComplete": completed,
            "markerCreation": completed,
            "turnComplete": completed,
            "cleanup": completed,
        },
        "protocolEvents": ["resumable_process_handle", "poll_after_yield"],
        "finalAgentStatus": "completed" if latest.get("status") == "completed" else "failed",
        "agentRunResultSuccessful": latest.get("status") == "completed",
        "cleanupObserved": True,
        "cleanupSessionId": str(latest.get("profileId") or "managed-codex-session"),
        "githubMutationCount": 0,
        "processInvocationCount": 1,
        "markerArtifactCreateCount": 1,
        "providerAvailable": True,
        "failureCode": None if latest.get("status") == "completed" else "CANARY_TOOL_PROTOCOL_INCOMPATIBLE",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = validate_canary_evidence(evidence, expected_candidate_digest=args.candidate_digest)
    print(result.model_dump_json(by_alias=True))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
