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
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from moonmind.codex_conformance.canary import (
    CANARY_PROVIDER_UNAVAILABLE,
    CANARY_SCENARIO_VERSION,
    CANARY_TERMINAL_MARKER_MISSING,
    CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
    DEFAULT_MARKER_PATH,
    canary_prompt,
    validate_canary_evidence,
)

_TERMINAL_STATUSES = {"completed", "failed", "canceled"}
_CANARY_OBSERVATION_KEYS = (
    "codexConformanceCanary",
    "codex_conformance_canary",
    "canaryEvidence",
    "canary_evidence",
)


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


def _walk_mappings(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for item in value.values():
            found.extend(_walk_mappings(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_mappings(item))
    return found


def _first_mapping_field(
    payloads: list[dict[str, Any]],
    keys: tuple[str, ...],
) -> dict[str, Any] | None:
    for payload in payloads:
        for mapping in _walk_mappings(payload):
            for key in keys:
                value = mapping.get(key)
                if isinstance(value, dict):
                    return value
    return None


def _artifact_id_from_ref(artifact_ref: str) -> str:
    ref = artifact_ref.strip()
    for prefix in ("artifact://", "art:", "ref:"):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref


def _get_json_or_none(client: httpx.Client, url: str) -> dict[str, Any] | None:
    response = client.get(url)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def _execution_artifacts(
    client: httpx.Client,
    *,
    api_url: str,
    workflow_id: str,
    run_id: str,
    namespace: str,
) -> dict[str, Any] | None:
    if not workflow_id or not run_id:
        return None
    return _get_json_or_none(
        client,
        f"{api_url}/api/executions/{quote(namespace, safe='')}/"
        f"{quote(workflow_id, safe='')}/{quote(run_id, safe='')}/artifacts",
    )


def _agent_run_context(
    client: httpx.Client,
    *,
    api_url: str,
    agent_run_id: str | None,
) -> list[dict[str, Any]]:
    if not agent_run_id:
        return []
    encoded = quote(agent_run_id, safe="")
    contexts: list[dict[str, Any]] = []
    for suffix in ("observability-summary", "observability/events"):
        payload = _get_json_or_none(
            client,
            f"{api_url}/api/agent-runs/{encoded}/{suffix}",
        )
        if payload is not None:
            contexts.append(payload)
    return contexts


def _fetch_marker(
    client: httpx.Client,
    *,
    api_url: str,
    marker_ref: str,
    nonce: str,
) -> dict[str, Any]:
    artifact_id = _artifact_id_from_ref(marker_ref)
    if not artifact_id or artifact_id == DEFAULT_MARKER_PATH:
        raise RuntimeError("Canary marker artifact ref is missing.")
    response = client.get(
        f"{api_url}/api/artifacts/{quote(artifact_id, safe='')}/download"
    )
    try:
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError("Canary marker artifact could not be read.") from exc
    marker = response.json()
    if not isinstance(marker, dict):
        raise RuntimeError("Canary marker artifact is not a JSON object.")
    if marker.get("nonce") != nonce:
        raise RuntimeError("Canary marker nonce does not match this run.")
    if marker.get("scenarioVersion") != CANARY_SCENARIO_VERSION:
        raise RuntimeError("Canary marker scenario version is invalid.")
    return marker


def _required_str(source: dict[str, Any], key: str) -> str:
    value = str(source.get(key) or "").strip()
    if not value:
        raise RuntimeError(f"Canary observation is missing {key}.")
    return value


def _required_list(source: dict[str, Any], key: str) -> list[str]:
    value = source.get(key)
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"Canary observation is missing {key}.")
    return [str(item) for item in value]


def _required_int(source: dict[str, Any], key: str) -> int:
    value = source.get(key)
    if not isinstance(value, int):
        raise RuntimeError(f"Canary observation is missing {key}.")
    return value


def _required_bool(source: dict[str, Any], key: str) -> bool:
    value = source.get(key)
    if not isinstance(value, bool):
        raise RuntimeError(f"Canary observation is missing {key}.")
    return value


def _required_timestamps(
    observation: dict[str, Any],
    marker: dict[str, Any],
) -> dict[str, str]:
    raw = observation.get("timestamps")
    if not isinstance(raw, dict):
        raise RuntimeError("Canary observation is missing timestamps.")
    timestamps = {key: str(value) for key, value in raw.items() if value}
    timestamps.setdefault("processStart", str(marker.get("startedAt") or ""))
    timestamps.setdefault("processComplete", str(marker.get("completedAt") or ""))
    for key in (
        "processStart",
        "firstToolYield",
        "subsequentPoll",
        "processComplete",
        "markerCreation",
        "turnComplete",
        "cleanup",
    ):
        if not timestamps.get(key):
            raise RuntimeError(f"Canary observation is missing timestamps.{key}.")
    return timestamps


def _assemble_success_evidence(
    *,
    client: httpx.Client,
    api_url: str,
    latest: dict[str, Any],
    candidate_digest: str,
    candidate_ref: str | None,
    nonce: str,
) -> dict[str, Any]:
    workflow_id = _required_str(latest, "workflowId")
    run_id = str(latest.get("runId") or latest.get("agentRunId") or workflow_id)
    namespace = str(latest.get("namespace") or "moonmind")
    agent_run_id = (
        str(latest.get("agentRunId") or latest.get("agent_run_id") or "").strip()
        or None
    )
    contexts: list[dict[str, Any]] = [latest]
    steps = _get_json_or_none(
        client,
        f"{api_url}/api/executions/{quote(workflow_id, safe='')}/steps",
    )
    if steps is not None:
        contexts.append(steps)
        for mapping in _walk_mappings(steps):
            if agent_run_id is None:
                candidate = str(
                    mapping.get("agentRunId") or mapping.get("agent_run_id") or ""
                ).strip()
                if candidate:
                    agent_run_id = candidate
    artifacts = _execution_artifacts(
        client,
        api_url=api_url,
        workflow_id=workflow_id,
        run_id=run_id,
        namespace=namespace,
    )
    if artifacts is not None:
        contexts.append(artifacts)
    contexts.extend(_agent_run_context(client, api_url=api_url, agent_run_id=agent_run_id))

    observation = _first_mapping_field(contexts, _CANARY_OBSERVATION_KEYS)
    if observation is None:
        raise RuntimeError("Canary execution did not publish authoritative canary observations.")
    marker_ref = _required_str(observation, "markerArtifactRef")
    marker = _fetch_marker(client, api_url=api_url, marker_ref=marker_ref, nonce=nonce)
    timestamps = _required_timestamps(observation, marker)
    protocol_events = _required_list(observation, "protocolEvents")

    return {
        "schemaVersion": "v1",
        "issueRef": "MoonLadderStudios/MoonMind#3150",
        "scenarioVersion": CANARY_SCENARIO_VERSION,
        "candidateImageDigest": candidate_digest,
        "candidateImageRef": candidate_ref,
        "codexCliVersion": os.environ.get("CODEX_CLI_VERSION", "unknown"),
        "codexAppServerVersion": os.environ.get("CODEX_APP_SERVER_VERSION"),
        "moonmindBuildSha": os.environ.get("GITHUB_SHA", "unknown"),
        "runId": run_id,
        "workflowId": workflow_id,
        "sessionId": _required_str(observation, "sessionId"),
        "sessionIdsObserved": _required_list(observation, "sessionIdsObserved"),
        "turnId": _required_str(observation, "turnId"),
        "markerArtifactRef": marker_ref,
        "markerPath": _required_str(observation, "markerPath"),
        "marker": marker,
        "timestamps": timestamps,
        "protocolEvents": protocol_events,
        "finalAgentStatus": "completed" if latest.get("status") == "completed" else "failed",
        "agentRunResultSuccessful": latest.get("status") == "completed",
        "cleanupObserved": _required_bool(observation, "cleanupObserved"),
        "cleanupSessionId": observation.get("cleanupSessionId"),
        "githubMutationCount": _required_int(observation, "githubMutationCount"),
        "processInvocationCount": _required_int(observation, "processInvocationCount"),
        "markerArtifactCreateCount": _required_int(observation, "markerArtifactCreateCount"),
        "providerAvailable": True,
        "failureCode": None if latest.get("status") == "completed" else CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
    }


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


def _canary_failure_evidence(
    *,
    candidate_digest: str,
    candidate_ref: str | None,
    output_path: Path,
    nonce: str,
    reason_code: str,
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
        "runId": "unverified",
        "workflowId": "unverified",
        "sessionId": "unverified",
        "sessionIdsObserved": ["unverified"],
        "turnId": "unverified",
        "markerArtifactRef": DEFAULT_MARKER_PATH,
        "markerPath": DEFAULT_MARKER_PATH,
        "marker": {
            "schemaVersion": "v1",
            "scenarioVersion": CANARY_SCENARIO_VERSION,
            "nonce": nonce,
            "command": "unverified",
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
        "providerAvailable": True,
        "failureCode": reason_code,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = validate_canary_evidence(evidence, expected_candidate_digest=candidate_digest)
    print(result.model_dump_json(by_alias=True))
    return 1


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run live Codex conformance canary.")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("MOONMIND_API_URL", "http://localhost:7000"),
    )
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
    subsequent_poll_at = process_started_at
    try:
        with httpx.Client(timeout=30.0, headers=headers) as client:
            created = client.post(
                f"{api_url}/api/executions",
                json=_workflow_payload(nonce=nonce, profile_ref=args.profile_ref),
            )
            created.raise_for_status()
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

            try:
                evidence = _assemble_success_evidence(
                    client=client,
                    api_url=api_url,
                    latest=latest,
                    candidate_digest=args.candidate_digest,
                    candidate_ref=args.candidate_ref,
                    nonce=nonce,
                )
            except (httpx.HTTPError, RuntimeError) as exc:
                return _canary_failure_evidence(
                    candidate_digest=args.candidate_digest,
                    candidate_ref=args.candidate_ref,
                    output_path=args.output,
                    nonce=nonce,
                    reason_code=CANARY_TERMINAL_MARKER_MISSING
                    if "marker" in str(exc).lower()
                    else CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
                    message=f"{exc.__class__.__name__}: {exc}",
                )
    except (httpx.HTTPError, RuntimeError, TimeoutError) as exc:
        return _provider_failure_evidence(
            candidate_digest=args.candidate_digest,
            candidate_ref=args.candidate_ref,
            output_path=args.output,
            message=f"{exc.__class__.__name__}: {exc}",
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = validate_canary_evidence(evidence, expected_candidate_digest=args.candidate_digest)
    print(result.model_dump_json(by_alias=True))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
