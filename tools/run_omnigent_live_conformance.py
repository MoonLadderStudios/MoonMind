#!/usr/bin/env python3
"""Run credentialed Omnigent conformance and product journeys for #3456.

The runner owns only an isolated Compose project.  In particular, it never
removes volumes: the enrolled Codex OAuth volume is operator-owned evidence,
not disposable test state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import shlex
import xml.etree.ElementTree as ET
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.conformance import (  # noqa: E402
    CaseResult,
    ConformanceContractError,
    assert_secret_free,
    build_report,
    load_profile,
    require_pinned_images,
)

PROFILE = REPO_ROOT / "tests/fixtures/omnigent/conformance-v4.json"
PROJECT = "moonmind-test-omnigent-live"
PROVIDER_TEST = "tests/provider/omnigent/test_omnigent_smoke.py"
LIVE_CASES = {
    "product": {"product.normal-create-api"},
    "cumulative": {"product.cumulative-remediation"},
    "stock": {
        "stock-images.proxy",
        "proxy.routes",
        "failures.transport-status-timeout",
        "events.replay-overlap-schema-drift",
        "resources.bounds-and-secret-scan",
    },
    "static": {"compose.static-codex-oauth"},
    "ondemand": {"ondemand.codex-oauth", "cleanup.lease-owned-only"},
    "failures": {"failures.lifecycle-and-redaction"},
}
STOCK_ROUTES = (
    "agents", "hosts", "session.create", "session.get", "event.post",
    "events.stream", "elicitation.resolve", "interrupt", "stop",
    "changed-files", "workspace.files", "workspace.content", "workspace.diff",
    "session.files", "session.content", "terminal.snapshot",
)
FAILURE_CASES = (
    "create_invalid_selector", "create_duplicate_submit", "create_persistence_failure",
    "compile_runtime_shape_mismatch", "capability_authority_mismatch",
    "workspace_stale_version", "workspace_wrong_parent", "workspace_concurrent_advance",
    "checkpoint_capture_failure", "restore_missing", "restore_corrupt",
    "restore_unauthorized", "restore_digest_mismatch",
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
)
CUMULATIVE_ACTIONS = (
    "workflow_created", "authored_state_persisted", "request_compiled",
    "initial_implementation_completed", "initial_verification_incomplete",
    "attempt_1_started", "attempt_1_checkpoint_captured",
    "attempt_1_source_destroyed", "attempt_2_started", "checkpoint_c1_restored",
    "attempt_2_checkpoint_captured", "final_verification_passed",
    "candidate_published", "workflow_detail_reloaded",
    "control_stop_created", "continuation_submitted", "continuation_replayed",
    "continuation_head_restored", "continuation_remediation_completed",
    "run_resources_removed", "profile_released",
)
PRODUCT_ACTIONS = (
    "runtime_catalog_loaded", "workflow_created", "authored_intent_persisted",
    "request_compiled", "temporal_routed", "workflow_detail_streamed",
    "artifacts_harvested", "host_removed", "workflow_detail_replayed",
    "profile_released",
)
PRODUCT_RECORD_TYPES = {
    "runtime_catalog_loaded": {"runtimeCatalog"},
    "workflow_created": {"createRequest", "authoredWorkflow"},
    "authored_intent_persisted": {"authoredWorkflow", "taskInputSnapshot"},
    "request_compiled": {"compiledExecutionRequest"},
    "temporal_routed": {"temporalHistory", "hostBinding", "profileLease"},
    "workflow_detail_streamed": {"workflowDetail", "bridgeEvents"},
    "artifacts_harvested": {"artifactInventory"},
    "host_removed": {"cleanupResult"},
    "workflow_detail_replayed": {"workflowDetail", "bridgeEvents"},
    "profile_released": {"profileLease", "cleanupResult"},
}
PRODUCT_ACCEPTANCE_FIELDS = (
    "credentialGeneration", "executionProfileRef", "policyVersion",
    "effectiveLaunchSnapshotDigest", "serverImageDigest", "hostImageDigest",
    "caseOutcomes", "secretScan", "evidence", "cleanupAndRelease",
)
ONDEMAND_ACTIONS = (
    "lease_acquired", "host_launched", "preflight_ready", "session_bound",
    "executed", "resources_harvested", "partial_start_retry", "janitor_recovery",
    "host_removed", "workflow_detail_reloaded", "lease_released",
)
SCENARIOS = {
    "product": f"{PROVIDER_TEST}::test_live_product_create_api_journey",
    "cumulative": f"{PROVIDER_TEST}::test_live_cumulative_remediation_journey",
    "stock": f"{PROVIDER_TEST}::test_live_stock_proxy_compatibility_profile",
    "static": f"{PROVIDER_TEST}::test_live_static_workflow_detail_restart_replay",
    "ondemand": f"{PROVIDER_TEST}::test_live_ondemand_oauth_lifecycle_and_cleanup",
    "failures": f"{PROVIDER_TEST}::test_live_failure_matrix_and_durable_evidence",
}
EVIDENCE_ENV = {
    "logs": "MOONMIND_OMNIGENT_LOG_EVIDENCE",
    "temporalHistory": "MOONMIND_OMNIGENT_TEMPORAL_HISTORY_EVIDENCE",
    "screenshots": "MOONMIND_OMNIGENT_SCREENSHOT_EVIDENCE",
    "archives": "MOONMIND_OMNIGENT_ARCHIVE_EVIDENCE",
}
SCENARIO_EVIDENCE_ENV = {
    "product": "MOONMIND_OMNIGENT_PRODUCT_EVIDENCE",
    "cumulative": "MOONMIND_OMNIGENT_CUMULATIVE_EVIDENCE",
    "stock": "MOONMIND_OMNIGENT_STOCK_EVIDENCE",
    "static": "MOONMIND_OMNIGENT_STATIC_EVIDENCE",
    "ondemand": "MOONMIND_OMNIGENT_ONDEMAND_EVIDENCE",
    "failures": "MOONMIND_OMNIGENT_FAILURE_EVIDENCE",
}


class LiveRunner:
    def __init__(self, *, output_dir: Path, env: dict[str, str]) -> None:
        self.output_dir = output_dir
        self.env = env
        self.logs: list[Path] = []
        self.evidence_refs: list[str] = []
        self.env.setdefault("MOONMIND_OMNIGENT_BACKEND_STATE", str(output_dir / "backend-state.json"))
        self.env.setdefault("MOONMIND_OMNIGENT_BACKEND_EVIDENCE_DIR", str(output_dir))

    def run(self, name: str, command: Sequence[str]) -> Path:
        log_path = self.output_dir / f"{name}.log"
        with log_path.open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                command,
                cwd=REPO_ROOT,
                env=self.env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        self.logs.append(log_path)
        if result.returncode:
            raise RuntimeError(f"{name} failed; see {log_path}")
        return log_path

    def action(self, scenario: str, action: str, **inputs: object) -> dict[str, object]:
        """Execute an operator-supplied live adapter and parse its observed result.

        The adapter is a portable executable boundary, not an evidence file: each
        invocation must perform the named action and return one JSON object on
        stdout. This keeps credentials and deployment-specific API mechanics out
        of this repository while making the runner own ordering and conclusions.
        """
        configured = self.env.get("MOONMIND_OMNIGENT_ACTION_COMMAND", "").strip()
        if not configured:
            raise ConformanceContractError(
                "MOONMIND_OMNIGENT_ACTION_COMMAND must name a real live action adapter"
            )
        command = shlex.split(configured)
        command.extend([scenario, action, json.dumps(inputs, separators=(",", ":"))])
        result = subprocess.run(
            command, cwd=REPO_ROOT, env=self.env, capture_output=True,
            text=True, encoding="utf-8", check=False,
        )
        stdout, stderr = result.stdout, result.stderr
        log_path = self.output_dir / f"{scenario}-{action.replace('.', '-')}.log"
        log_path.write_text(
            f"--- STDOUT ---\n{stdout}\n--- STDERR ---\n{stderr}",
            encoding="utf-8",
        )
        self.logs.append(log_path)
        if result.returncode:
            raise RuntimeError(f"{scenario}/{action} failed; see {log_path}")
        try:
            payload = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ConformanceContractError(f"{scenario}/{action} returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise ConformanceContractError(f"{scenario}/{action} did not report observed success")
        evidence = payload.get("evidenceRefs")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(ref, str) and ref.strip() for ref in evidence
        ):
            raise ConformanceContractError(
                f"{scenario}/{action} did not return durable evidence refs"
            )
        observations = [self._resolve_evidence_ref(ref) for ref in evidence]
        self.evidence_refs.extend(evidence)
        if not any(
            item.get("scenario") == scenario
            and item.get("action") == action
            and item.get("observed") is True
            for item in observations
        ):
            raise ConformanceContractError(
                f"{scenario}/{action} evidence did not describe the observed action"
            )
        if scenario in {"product", "cumulative", "failures"}:
            records = [
                record
                for item in observations
                for record in item.get("sourceRecords", [])
                if isinstance(record, dict)
            ]
            required_types = (
                PRODUCT_RECORD_TYPES[action]
                if scenario == "product"
                else {"cumulativeRunState", "sideEffectAudit"}
                if scenario == "cumulative"
                else {"injectionControl", "terminalProjection", "sideEffectAudit"}
            )
            observed_types = {record.get("type") for record in records}
            missing = sorted(required_types - observed_types)
            if missing:
                raise ConformanceContractError(
                    f"{scenario}/{action} lacks independently resolved source records: {missing}"
                )
            for record in records:
                if not all(
                    isinstance(record.get(key), str) and record[key].strip()
                    for key in ("type", "ref", "sha256")
                ) or len(record["sha256"]) != 64:
                    raise ConformanceContractError(
                        f"{scenario}/{action} contains an invalid source record"
                    )
                raw = self._resolve_ref_bytes(record["ref"])
                if hashlib.sha256(raw).hexdigest() != record["sha256"].lower():
                    raise ConformanceContractError(
                        f"{scenario}/{action} source record digest does not match: {record['type']}"
                    )
            payload["_sourceRecordTypes"] = sorted(observed_types)
        returned_ids = {
            key: value for key, value in payload.items()
            if key in {
                "leaseId", "hostId", "workflowId", "agentRunId", "sessionId",
                "runId", "stepId", "bridgeId", "sourceWorkflowId",
                "destinationWorkflowId", "continuationId", "profileRef",
                "c0Ref", "c1Ref", "c2Ref",
            }
            and value
        }
        state = payload.get("state")
        if isinstance(state, dict):
            returned_ids.update({
                key: value for key, value in state.items()
                if key in {
                    "leaseId", "hostId", "workflowId", "agentRunId", "sessionId",
                    "runId", "stepId", "bridgeId", "sourceWorkflowId",
                    "destinationWorkflowId", "continuationId", "profileRef",
                    "c0Ref", "c1Ref", "c2Ref",
                }
                and value
            })
        for item in observations:
            evidence_ids = item.get("identifiers", {})
            if evidence_ids and (
                not isinstance(evidence_ids, dict)
                or any(evidence_ids.get(key) != value for key, value in returned_ids.items())
            ):
                raise ConformanceContractError(
                    f"{scenario}/{action} evidence identifiers do not match the response"
                )
        durable = payload.get("durableEvidence")
        if durable is not None and not any(
            item.get("durableEvidence") == durable for item in observations
        ):
            raise ConformanceContractError(
                f"{scenario}/{action} durable failure claims are not bound to evidence"
            )
        return payload

    def _resolve_evidence_ref(self, ref: str) -> dict[str, object]:
        """Resolve durable evidence and reject opaque or unreachable attestations."""
        raw_bytes = self._resolve_ref_bytes(ref)
        try:
            raw = raw_bytes.decode("utf-8")
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConformanceContractError(f"unreachable or malformed evidence ref: {ref}") from exc
        if not isinstance(payload, dict) or payload.get("schemaVersion") != "moonmind.omnigent.action-evidence/v1":
            raise ConformanceContractError(f"invalid action evidence document: {ref}")
        assert_secret_free(raw)
        return payload

    def _resolve_ref_bytes(self, ref: str) -> bytes:
        """Resolve a run-owned or HTTPS evidence reference to its authoritative bytes."""
        parsed = urllib.parse.urlparse(ref)
        try:
            if parsed.scheme == "file":
                path = Path(urllib.request.url2pathname(parsed.path)).resolve()
                allowed = self.output_dir.resolve()
                if path != allowed and allowed not in path.parents:
                    raise ConformanceContractError("file evidence is outside the run output directory")
                raw = path.read_bytes()
            elif parsed.scheme == "https":
                with urllib.request.urlopen(ref, timeout=30) as response:
                    raw = response.read()
            else:
                raise ConformanceContractError(f"unsupported evidence ref scheme: {parsed.scheme or 'none'}")
        except (OSError, urllib.error.URLError) as exc:
            raise ConformanceContractError(f"unreachable or malformed evidence ref: {ref}") from exc
        return raw

    def write_evidence(self, mode: str, payload: dict[str, object]) -> Path:
        path = self.output_dir / f"{mode}-evidence.json"
        payload = {"schemaVersion": "moonmind.omnigent.live-evidence/v1", **payload}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self.env[SCENARIO_EVIDENCE_ENV[mode]] = str(path)
        return path

    def stock(self, images: dict[str, str]) -> None:
        self.run(
            "stock-up",
            self.compose("up", "-d", "--wait", "omnigent", "omnigent-host-codex"),
        )
        observed = {route: self.action("stock", route) for route in STOCK_ROUTES}
        inventory = self.action("stock", "inventory")
        self.write_evidence("stock", {
            "images": images, "hostSource": "published-stock-image",
            "moonmindHostPatch": False,
            "protocolVersion": inventory.get("protocolVersion"),
            "hostArchitecture": inventory.get("hostArchitecture"),
            "advertisedAgents": inventory.get("agents"),
            "advertisedCapabilities": inventory.get("capabilities"),
            "assertions": {name: result["ok"] is True for name, result in observed.items()},
        })
        self.scenario("stock")

    def product(self) -> None:
        """Drive the normal product create contract, never a raw execution request."""
        state: dict[str, object] = {}
        results: dict[str, dict[str, object]] = {}
        for action in PRODUCT_ACTIONS:
            result = self.action("product", action, **state)
            next_state = result.get("state")
            if not isinstance(next_state, dict):
                raise ConformanceContractError(f"product/{action} did not return lifecycle state")
            state.update(next_state)
            results[action] = result
        required = ("workflowId", "runId", "stepId", "bridgeId", "hostId", "sessionId")
        if not all(state.get(key) for key in required):
            raise ConformanceContractError("product journey lacks durable product identifiers")
        missing_acceptance = [key for key in PRODUCT_ACCEPTANCE_FIELDS if not state.get(key)]
        if missing_acceptance:
            raise ConformanceContractError(
                f"product journey lacks acceptance fields: {missing_acceptance}"
            )
        assertions = {
            "normal_create_api": bool(results["workflow_created"].get("normalCreateApi")),
            "authored_intent_and_snapshot": bool(results["authored_intent_persisted"].get("authoredIntentAndSnapshot")),
            "external_omnigent_compilation": bool(results["request_compiled"].get("externalOmnigentCompilation")),
            "selected_profile_policy_workspace": bool(results["request_compiled"].get("selectedAuthoritiesPreserved")),
            "real_temporal_activity_route": bool(results["temporal_routed"].get("temporalActivityRoute")),
            "workflow_detail_sse": bool(results["workflow_detail_streamed"].get("workflowDetailSse")),
            "release_last": bool(results["profile_released"].get("releaseLast")),
            "replay_after_host_removal": bool(results["workflow_detail_replayed"].get("replayAfterRemoval")),
            "no_fallback": all(bool(result.get("noFallback")) for result in results.values()),
        }
        if not all(assertions.values()):
            raise ConformanceContractError("product journey did not prove every controlling assertion")
        self.write_evidence("product", {
            "issue": "MoonLadderStudios/MoonMind#3456", "actions": list(PRODUCT_ACTIONS),
            "identifiers": {key: state[key] for key in required}, "assertions": assertions,
            "selection": state.get("selection"), "schemaVersions": state.get("schemaVersions"),
            "acceptance": {key: state[key] for key in PRODUCT_ACCEPTANCE_FIELDS},
            "evidenceRefs": [ref for result in results.values() for ref in result["evidenceRefs"]],
            "sourceRecordTypes": sorted({
                record_type
                for result in results.values()
                for record_type in result.get("_sourceRecordTypes", [])
            }),
        })
        self.scenario("product")

    def cumulative(self) -> None:
        """Prove cumulative remediation through the production action boundary."""
        state: dict[str, object] = {}
        results: dict[str, dict[str, object]] = {}
        for action in CUMULATIVE_ACTIONS:
            result = self.action("cumulative", action, **state)
            next_state = result.get("state")
            if not isinstance(next_state, dict):
                raise ConformanceContractError(
                    f"cumulative/{action} did not return lifecycle state"
                )
            state.update(next_state)
            results[action] = result

        required = (
            "sourceWorkflowId", "destinationWorkflowId", "continuationId",
            "profileRef", "c0Ref", "c1Ref", "c2Ref",
        )
        if not all(state.get(key) for key in required):
            raise ConformanceContractError(
                "cumulative journey lacks durable lineage or checkpoint identifiers"
            )
        attempts = state.get("attempts")
        if not isinstance(attempts, list) or len(attempts) < 2:
            raise ConformanceContractError("cumulative journey lacks two attempt records")
        first, second = attempts[0], attempts[1]
        if not isinstance(first, dict) or not isinstance(second, dict):
            raise ConformanceContractError("cumulative attempt records are malformed")
        distinct_keys = ("workspaceId", "leaseId", "hostId", "sessionId", "firstMessageId")
        if any(first.get(key) == second.get(key) or not first.get(key) or not second.get(key)
               for key in distinct_keys):
            raise ConformanceContractError(
                "cumulative attempts did not use distinct destination identities"
            )
        if first.get("baseCheckpointRef") != state["c0Ref"]:
            raise ConformanceContractError("attempt 1 did not start from C0")
        if second.get("baseCheckpointRef") != state["c1Ref"]:
            raise ConformanceContractError("attempt 2 did not restore C1")
        failure_matrix = state.get("failureMatrix")
        if not isinstance(failure_matrix, dict) or set(failure_matrix) != set(FAILURE_CASES):
            raise ConformanceContractError(
                "cumulative journey lacks the complete integrated failure matrix"
            )
        if any(outcome != "passed" for outcome in failure_matrix.values()):
            raise ConformanceContractError(
                "cumulative journey contains a non-passing failure-matrix outcome"
            )
        rollout = state.get("rollout")
        rollout_keys = (
            "canary", "disableNewSelection", "rollback", "historicalReads",
            "workerVersionReplay",
        )
        if not isinstance(rollout, dict) or any(
            rollout.get(key) is not True for key in rollout_keys
        ):
            raise ConformanceContractError(
                "cumulative journey lacks rollout and replay evidence"
            )

        assertions = {
            "normal_create_api": bool(results["workflow_created"].get("normalCreateApi")),
            "authored_state_persisted": bool(results["authored_state_persisted"].get("complete")),
            "external_omnigent_compilation": bool(results["request_compiled"].get("exactSelection")),
            "c0_c1_c2_head_transitions": bool(results["attempt_2_checkpoint_captured"].get("cumulative")),
            "source_destroyed_before_restore": bool(results["attempt_1_source_destroyed"].get("destroyed")),
            "marker_a_restored_before_marker_b": bool(results["checkpoint_c1_restored"].get("markerA")),
            "verification_read_only": bool(results["final_verification_passed"].get("readOnly")),
            "continuation_idempotent": bool(results["continuation_replayed"].get("sameDestination")),
            "prior_side_effects_not_replayed": bool(results["continuation_head_restored"].get("noSideEffectReplay")),
            "workflow_detail_after_removal": bool(results["workflow_detail_reloaded"].get("available")),
            "profile_released_last": bool(results["profile_released"].get("releaseLast")),
            "no_fallback": all(bool(result.get("noFallback")) for result in results.values()),
        }
        if not all(assertions.values()):
            raise ConformanceContractError(
                "cumulative journey did not prove every controlling assertion"
            )
        self.write_evidence("cumulative", {
            "issue": "MoonLadderStudios/MoonMind#3480",
            "parentIssue": "MoonLadderStudios/MoonMind#3471",
            "acceptanceIssue": "MoonLadderStudios/MoonMind#3456",
            "actions": list(CUMULATIVE_ACTIONS),
            "identifiers": {key: state[key] for key in required},
            "attempts": attempts,
            "assertions": assertions,
            "failureMatrix": failure_matrix,
            "rollout": rollout,
            "evidenceRefs": [
                ref for result in results.values() for ref in result["evidenceRefs"]
            ],
        })
        self.scenario("cumulative")

    @staticmethod
    def compose(*args: str) -> list[str]:
        return [
            "docker", "compose", "--project-name", PROJECT,
            "--profile", "omnigent-host-codex", *args,
        ]

    def scenario(self, mode: str, *, phase: str | None = None) -> None:
        """Run exactly one strict provider scenario and reject skips/no collection."""
        self.env["MOONMIND_OMNIGENT_LIVE_MODE"] = mode
        self.env["MOONMIND_OMNIGENT_STRICT_LIVE"] = "1"
        if phase is not None:
            self.env["MOONMIND_OMNIGENT_STATIC_PHASE"] = phase
        evidence_name = f"{mode}-{phase}" if phase else mode
        junit = self.output_dir / f"{evidence_name}-junit.xml"
        self.run(
            f"{evidence_name}-journey",
            [sys.executable, "-m", "pytest", SCENARIOS[mode], "-q", "-s", f"--junitxml={junit}"],
        )
        if not junit.is_file():
            raise RuntimeError(f"{mode} did not produce pytest outcome evidence")
        try:
            root = ET.parse(junit).getroot()
        except ET.ParseError as exc:
            raise RuntimeError(f"failed to parse pytest JUnit XML: {exc}") from exc
        suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        totals = {key: sum(int(s.get(key, "0")) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
        if totals["tests"] != 1 or any(totals[key] for key in ("failures", "errors", "skipped")):
            raise RuntimeError(f"{mode} scenario was not one unskipped passing test: {totals}")

    def static(self) -> None:
        self.run(
            "static-up",
            self.compose("up", "-d", "--wait", "omnigent", "omnigent-host-codex"),
        )
        executed = self.action("static", "execute")
        identifiers = {key: executed.get(key) for key in ("workflowId", "agentRunId", "sessionId")}
        if not all(identifiers.values()):
            raise ConformanceContractError("static execute did not return durable identifiers")
        self.write_evidence("static", {**identifiers, "assertions": {
            **{name: bool(executed.get(name)) for name in (
                "one_first_message", "live_events", "final_snapshot", "resources",
                "workflow_detail", "secret_free")},
            "workflow_created_through_static_profile": True,
        }})
        self.scenario("static", phase="execute")
        self.run("static-restart", self.compose("restart", "omnigent", "omnigent-host-codex"))
        # Reload the persisted identifiers and assert the same workflow after
        # restart.  This is deliberately a real second provider invocation,
        # never collection-only evidence.
        replayed = self.action("static", "replay", **identifiers)
        if any(replayed.get(key) != value for key, value in identifiers.items()):
            raise ConformanceContractError("static replay returned different durable identifiers")
        self.write_evidence("static", {**identifiers, "assertions": {
            **{name: bool(replayed.get(name)) for name in (
                "one_first_message", "live_events", "final_snapshot", "resources",
                "workflow_detail", "secret_free", "durable_replay")},
            "services_restarted": True, "same_identifiers_reloaded": True,
        }})
        self.scenario("static", phase="replay")

    def ondemand(self) -> None:
        events: list[str] = []
        results: dict[str, dict[str, object]] = {}
        state: dict[str, object] = {}
        for action in ONDEMAND_ACTIONS:
            results[action] = self.action("ondemand", action, **state)
            returned_state = results[action].get("state")
            if not isinstance(returned_state, dict):
                raise ConformanceContractError(
                    f"ondemand/{action} did not return lifecycle state"
                )
            state.update(returned_state)
            events.append(action)
        required = ("leaseId", "hostId", "workflowId", "agentRunId", "sessionId")
        if not all(state.get(name) for name in required):
            raise ConformanceContractError("on-demand lifecycle did not propagate durable identifiers")
        self.write_evidence("ondemand", {"events": events, "assertions": {
            "exact_profile_host": bool(results["host_launched"].get("exactProfileHost")),
            "partial_start_retry": bool(results["partial_start_retry"].get("retryRecovered")),
            "janitor_recovery": bool(results["janitor_recovery"].get("orphanRecovered")),
            "state_removed_per_policy": bool(results["host_removed"].get("stateRemoved")),
            "unrelated_resources_survived": bool(results["host_removed"].get("unrelatedResourcesSurvived")),
            "credential_volume_preserved": bool(results["host_removed"].get("credentialVolumePreserved")),
            "workflow_detail_available_after_removal": bool(results["workflow_detail_reloaded"].get("available")),
        }, "identifiers": {name: state[name] for name in required},
            "evidenceRefs": [ref for result in results.values() for ref in result["evidenceRefs"]]})
        self.scenario("ondemand")

    def failures(self) -> None:
        cases = {}
        for case in FAILURE_CASES:
            result = self.action("failures", case)
            durable = result.get("durableEvidence")
            if not isinstance(durable, dict):
                raise ConformanceContractError(f"failure {case} lacks durable evidence")
            cases[case] = {key: bool(durable.get(key)) for key in (
                "injected", "lifecycleProjected", "terminalProjected", "redacted",
                "noFallback")}
            cases[case]["evidenceRefs"] = result["evidenceRefs"]
        self.write_evidence("failures", {"failureCases": cases})
        self.scenario("failures")

    def cleanup(self, mode: str) -> None:
        # No --volumes: OAuth and unrelated state must survive this runner.
        self.run(f"{mode}-cleanup", self.compose("down", "--remove-orphans"))

    def scan(self) -> dict[str, dict[str, str]]:
        scans: dict[str, dict[str, str]] = {}
        for channel, env_name in EVIDENCE_ENV.items():
            raw = self.env.get(env_name, "")
            paths = [Path(item) for item in raw.split(os.pathsep) if item]
            if channel == "logs":
                paths.extend(self.logs)
            if not paths or any(not path.is_file() for path in paths):
                raise ConformanceContractError(f"{channel} evidence was not collected")
            for evidence in paths:
                assert_secret_free(evidence.read_text(encoding="utf-8", errors="replace"))
            scan_path = self.output_dir / f"secret-scan-{channel}.json"
            scan_path.write_text(json.dumps({"status": "passed", "files": [str(p) for p in paths]}) + "\n")
            scans[channel] = {"status": "passed", "evidenceRef": str(scan_path)}
        return scans


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live Omnigent conformance for MoonLadderStudios/MoonMind#3368")
    parser.add_argument("--mode", choices=(*LIVE_CASES, "all"), default="all")
    parser.add_argument("--server-image", required=True)
    parser.add_argument("--host-image", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/omnigent-conformance/live"))
    args = parser.parse_args()
    images = {"server": args.server_image, "host": args.host_image}
    require_pinned_images(images)
    output_dir = args.output_dir if args.output_dir.is_absolute() else REPO_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "OMNIGENT_IMAGE_REF": args.server_image,
        "OMNIGENT_HOST_IMAGE_REF": args.host_image,
        # The server uses these values when it launches an on-demand host.
        "OMNIGENT_HOST_IMAGE": args.host_image,
        "OMNIGENT_HOST_IMAGE_TAG": "",
    })
    runner = LiveRunner(output_dir=output_dir, env=env)
    selected = tuple(LIVE_CASES) if args.mode == "all" else (args.mode,)
    passed: set[str] = set()
    failure: str | None = None
    try:
        for mode in selected:
            try:
                if mode == "product":
                    runner.product()
                elif mode == "cumulative":
                    runner.cumulative()
                elif mode == "stock":
                    runner.stock(images)
                elif mode == "static":
                    runner.static()
                elif mode == "ondemand":
                    runner.ondemand()
                else:
                    runner.failures()
            finally:
                runner.cleanup(mode)
            passed.update(LIVE_CASES[mode])
    except (RuntimeError, ConformanceContractError) as exc:
        failure = str(exc)
    finally:
        try:
            scans = runner.scan()
        except (RuntimeError, ConformanceContractError) as exc:
            failure = failure or str(exc)
            scans = {}

    profile = load_profile(PROFILE)
    requested = set().union(*(LIVE_CASES[item] for item in selected))
    results = []
    def report_ref(path: Path) -> str:
        try:
            return str(path.relative_to(REPO_ROOT))
        except ValueError:
            return path.resolve().as_uri()

    refs = tuple(dict.fromkeys(
        [report_ref(path) for path in runner.logs]
        + runner.evidence_refs
        + [report_ref(Path(value)) for env_name in SCENARIO_EVIDENCE_ENV.values()
           if (value := runner.env.get(env_name))]
    )) or (report_ref(output_dir),)
    for item in profile["cases"]:
        case_id = item["id"]
        status = "passed" if case_id in passed else "failed" if case_id in requested else "skipped"
        results.append(CaseResult(case_id, status, refs))
    try:
        report = build_report(profile=profile, images=images, host_architecture=platform.machine(), auth_mode="codex-oauth", capabilities=selected, cases=results, protocol_version="omnigent/v1", evidence_scans=scans)
        (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    except ConformanceContractError as exc:
        failure = failure or str(exc)
    if failure:
        print(f"live conformance failed: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
