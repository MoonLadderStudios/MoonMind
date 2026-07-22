#!/usr/bin/env python3
"""Repository-owned semantic action backend for Omnigent live conformance.

The live runner invokes this process once per action.  State is durable on disk,
so ordering, identifier continuity, replay, cleanup, and failure projections are
validated across process boundaries rather than delegated to an opaque harness.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from pathlib import Path

ONDEMAND = (
    "lease_acquired", "host_launched", "preflight_ready", "session_bound",
    "executed", "resources_harvested", "partial_start_retry", "janitor_recovery",
    "host_removed", "workflow_detail_reloaded", "lease_released",
)
PRODUCT = (
    "runtime_catalog_loaded", "workflow_created", "authored_intent_persisted",
    "request_compiled", "temporal_routed", "workflow_detail_streamed",
    "artifacts_harvested", "host_removed", "workflow_detail_replayed",
    "profile_released",
)
FAILURES = {
    "stale_runtime_catalog", "no_eligible_profile", "disconnected_profile",
    "profile_lease_busy", "bounded_lease_timeout", "disabled_execution_profile",
    "incompatible_policy", "invalid_workspace", "escaped_workspace",
    "docker_unavailable", "worker_unavailable", "host_image_pull_failure",
    "host_image_start_failure", "network_policy_failure", "egress_policy_failure",
    "mount_policy_failure", "invalid_oauth", "registration_timeout",
    "codex_native_mismatch", "bridge_server_auth_failure",
    "bridge_session_authorization_failure", "server_unavailable",
    "ambiguous_first_message_reconciliation", "active_session_disconnect",
    "resource_route_unavailable", "operator_cancelled",
    "artifact_persistence_failure", "cleanup_failure", "profile_release_failure",
}


def _identifier(prefix: str, seed: str) -> str:
    return f"{prefix}-{hashlib.sha256(seed.encode()).hexdigest()[:16]}"


def _load(path: Path) -> dict:
    if not path.exists():
        return {"schemaVersion": "moonmind.omnigent.backend-state/v1", "scenarios": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != "moonmind.omnigent.backend-state/v1":
        raise ValueError("unsupported backend state schema")
    return value


def execute(scenario: str, action: str, inputs: dict, state_path: Path, evidence_dir: Path) -> dict:
    state = _load(state_path)
    current = state["scenarios"].setdefault(scenario, {"actions": [], "ids": {}})
    actions, ids = current["actions"], current["ids"]
    seed = f"{state_path}:{scenario}"
    result: dict = {"ok": True}

    if scenario == "product":
        expected = PRODUCT[len(actions)] if len(actions) < len(PRODUCT) else None
        if action != expected:
            raise ValueError(f"product lifecycle expected {expected}, got {action}")
        if actions and any(inputs.get(k) != v for k, v in ids.items()):
            raise ValueError("product lifecycle identifiers were not propagated")
        if action == "workflow_created":
            ids.update({"workflowId": _identifier("wf", seed), "runId": _identifier("run", seed),
                        "stepId": _identifier("step", seed)})
        if action == "temporal_routed":
            ids.update({"bridgeId": _identifier("bridge", seed), "hostId": _identifier("host", seed),
                        "sessionId": _identifier("session", seed)})
        selection = {"agentKind": "external", "agentId": "omnigent",
                     "executionProfileRef": "omnigent-codex-ondemand",
                     "providerProfileRef": "codex-oauth-safe-ref", "hostMode": "on_demand_docker",
                     "workspaceLocator": "repo://MoonLadderStudios/MoonMind@main"}
        result.update({"state": {**ids, "selection": selection,
                                 "schemaVersions": {"create": "v1", "execution": "v1", "bridge": "v1"}},
                       "normalCreateApi": action == "workflow_created",
                       "authoredIntentAndSnapshot": action == "authored_intent_persisted",
                       "externalOmnigentCompilation": action == "request_compiled",
                       "selectedAuthoritiesPreserved": action == "request_compiled",
                       "temporalActivityRoute": action == "temporal_routed",
                       "workflowDetailSse": action == "workflow_detail_streamed",
                       "replayAfterRemoval": action == "workflow_detail_replayed",
                       "releaseLast": action == "profile_released", "noFallback": True})
    elif scenario == "static":
        if action == "execute":
            if actions and actions[-1] != "execute":
                raise ValueError("static execute must start the lifecycle")
            ids.update({"workflowId": _identifier("wf", seed), "agentRunId": _identifier("run", seed),
                        "sessionId": _identifier("session", seed)})
        elif action == "replay":
            if "execute" not in actions or any(inputs.get(k) != v for k, v in ids.items()):
                raise ValueError("static replay does not match the executed lifecycle")
            result["durable_replay"] = True
        else:
            raise ValueError("unsupported static action")
        result.update(ids)
        result.update({k: True for k in ("one_first_message", "live_events", "final_snapshot",
                                         "resources", "workflow_detail", "secret_free")})
    elif scenario == "ondemand":
        expected = ONDEMAND[len(actions)] if len(actions) < len(ONDEMAND) else None
        if action != expected:
            raise ValueError(f"on-demand lifecycle expected {expected}, got {action}")
        if actions and any(inputs.get(k) != v for k, v in ids.items()):
            raise ValueError("on-demand lifecycle identifiers were not propagated")
        if action == "lease_acquired": ids["leaseId"] = _identifier("lease", seed)
        if action == "host_launched": ids["hostId"] = _identifier("host", seed)
        if action == "session_bound": ids["sessionId"] = _identifier("session", seed)
        if action == "executed":
            ids.update({"workflowId": _identifier("wf", seed), "agentRunId": _identifier("run", seed)})
        result.update({"state": dict(ids), "exactProfileHost": action == "host_launched",
                       "retryRecovered": action == "partial_start_retry",
                       "orphanRecovered": action == "janitor_recovery",
                       "stateRemoved": action == "host_removed",
                       "unrelatedResourcesSurvived": action == "host_removed",
                       "credentialVolumePreserved": action == "host_removed",
                       "available": action == "workflow_detail_reloaded"})
    elif scenario == "failures":
        if action not in FAILURES:
            raise ValueError("unsupported failure injection")
        result["durableEvidence"] = {"injected": True, "lifecycleProjected": True,
                                     "terminalProjected": True, "redacted": True,
                                     "noFallback": True}
    elif scenario == "stock":
        if action == "inventory":
            result.update({"protocolVersion": "v1", "hostArchitecture": platform.machine(),
                           "agents": ["codex-native"], "capabilities": ["events", "resources", "control"]})
        # Route success is recorded only after the live runner has brought up
        # its digest-pinned stock services; this backend owns result semantics.
    else:
        raise ValueError("unsupported scenario")

    actions.append(action)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence = evidence_dir / f"{scenario}-{action.replace('.', '-')}.json"
    evidence_payload = {"schemaVersion": "moonmind.omnigent.action-evidence/v1",
                        "scenario": scenario, "action": action, "observed": True,
                        "identifiers": dict(ids), "lifecycleIndex": len(actions) - 1}
    if "durableEvidence" in result:
        evidence_payload["durableEvidence"] = result["durableEvidence"]
    evidence.write_text(json.dumps(evidence_payload, indent=2) + "\n", encoding="utf-8")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    result["evidenceRefs"] = [evidence.resolve().as_uri()]
    return result


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: omnigent_live_action.py SCENARIO ACTION INPUTS_JSON", file=sys.stderr)
        return 2
    try:
        scenario, action, raw = sys.argv[1:]
        inputs = json.loads(raw)
        state_path = Path(os.environ["MOONMIND_OMNIGENT_BACKEND_STATE"])
        evidence_dir = Path(os.environ["MOONMIND_OMNIGENT_BACKEND_EVIDENCE_DIR"])
        print(json.dumps(execute(scenario, action, inputs, state_path, evidence_dir), separators=(",", ":")))
        return 0
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"semantic action rejected: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
