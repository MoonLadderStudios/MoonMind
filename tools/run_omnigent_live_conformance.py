#!/usr/bin/env python3
"""Run credentialed Omnigent conformance and protected product journeys.

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
import re
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.conformance import (  # noqa: E402
    REPORT_VERSION,
    CaseResult,
    ConformanceContractError,
    PROFILE_SHA256,
    PROFILE_VERSION,
    assert_secret_free,
    build_report,
    load_profile,
    require_pinned_images,
)
from moonmind.omnigent.remediation_matrix import (  # noqa: E402
    PROHIBITED_UI_JOURNEY_MARKERS,
    REMEDIATION_ARTIFACT_SCHEMA_VERSION,
    REMEDIATION_MATRIX_VERSION,
    REMEDIATION_ROW_CATALOG,
    REMEDIATION_SOURCE_RECORD_SCHEMAS,
    RemediationMatrixError,
    REQUIRED_REMEDIATION_LINEAGE_FIELDS,
    REQUIRED_REMEDIATION_EVIDENCE_KINDS,
    REQUIRED_REMEDIATION_RETAINED_CHANNELS,
    REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES,
    REQUIRED_UI_JOURNEY_ASSERTIONS,
    derive_remediation_observation_from_source_records,
    validate_remediation_evidence_artifact,
)
from moonmind.omnigent.workflow_chat_acceptance import (  # noqa: E402
    REQUIRED_BUNDLE_DIGESTS,
    REQUIRED_WORKFLOW_CHAT_ROWS,
    REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS,
    WORKFLOW_CHAT_ACCEPTANCE_ISSUE,
    WORKFLOW_CHAT_CASE_EVIDENCE_VERSION,
    WORKFLOW_CHAT_COMBINATION_VERSION,
    WORKFLOW_CHAT_COMPATIBILITY_PROFILE,
    WORKFLOW_CHAT_PARENT_ISSUE,
    WorkflowChatCombination,
    build_workflow_chat_acceptance_manifest,
    validate_workflow_chat_acceptance_manifest,
    validate_workflow_chat_source_records,
    workflow_chat_combinations,
)

PROFILE = REPO_ROOT / "tests/fixtures/omnigent/conformance-v4.json"
PROJECT = "moonmind-test-omnigent-live"
PROVIDER_TEST = "tests/provider/omnigent/test_omnigent_smoke.py"
LIVE_CASES = {
    "browser": {"product.normal-create-api"},
    "product": {"product.normal-create-api"},
    "cumulative": {"product.cumulative-remediation"},
    "remediation": {"product.operator-remediation-release-matrix"},
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
    "workflow_chat": {"workflow-chat.native-release-matrix"},
}
BROWSER_ROWS = (
    "static_profile_bound",
    "static_restart_replay",
    "on_demand_policy_selected",
    "repository_read_analysis",
    "repository_mutation_publication",
    "failed_credential_readiness_admission",
    "failed_host_registration_readiness",
    "active_cancellation_interruption",
    "partial_start_cleanup_janitor",
)
BROWSER_AUTHORITY_FIELDS = (
    "authoredWorkflowRef",
    "taskInputSnapshotRef",
    "compiledRuntimeRequestRef",
    "executionProfileRef",
    "launchPolicyRef",
    "effectiveLaunchSnapshotRef",
    "providerProfileRef",
    "providerLeaseId",
    "hostBindingRef",
    "hostLeaseId",
    "hostId",
    "hostCapability",
    "bridgeSessionId",
    "omnigentSessionId",
    "firstMessageId",
    "eventCursor",
    "workspaceLocator",
    "sourceCommit",
    "resourceRefs",
    "artifactRefs",
    "terminalState",
    "cleanupState",
    "janitorState",
    "providerProfileRelease",
    "networkPolicyRef",
    "runtime",
    "hostMode",
)
BROWSER_RECORD_ORDER = (
    "browserTrace",
    "createRequest",
    "authoredWorkflow",
    "taskInputSnapshot",
    "compiledExecutionRequest",
    "executionProfile",
    "launchPolicy",
    "effectiveLaunchSnapshot",
    "profileLease",
    "hostBinding",
    "hostLease",
    "hostRegistration",
    "bridgeSession",
    "omnigentSession",
    "bridgeEvents",
    "workspace",
    "artifactInventory",
    "terminalProjection",
    "cleanupResult",
    "janitorState",
    "sideEffectAudit",
)
BROWSER_RECORD_TYPES = set(BROWSER_RECORD_ORDER)
CATALOG_BOOTSTRAP_EVIDENCE_VERSION = (
    "moonmind.omnigent.catalog-bootstrap-evidence/v1"
)
CATALOG_BOOTSTRAP_EVIDENCE_ENV = (
    "MOONMIND_OMNIGENT_CATALOG_BOOTSTRAP_EVIDENCE"
)
_IMMUTABLE_IMAGE_REF = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_SAFE_BUILD_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,254}$")
REMEDIATION_RECORD_TYPES = REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES
PROHIBITED_RETAINED_AUTHORITY = re.compile(
    r'(?i)"(?:rawHostShell|dockerSocket|sqlConnection|storageKey|credentialValue|'
    r'secretRead|redactionBypass)"\s*:\s*(?!false\b|null\b|""|\[\]|\{\})'
)
ADMISSION_RECORD_TYPES = {"browserTrace", "sideEffectAudit"}
BROWSER_FIELD_RECORD_TYPE = {
    "authoredWorkflowRef": "authoredWorkflow",
    "taskInputSnapshotRef": "taskInputSnapshot",
    "compiledRuntimeRequestRef": "compiledExecutionRequest",
    "executionProfileRef": "executionProfile",
    "launchPolicyRef": "launchPolicy",
    "effectiveLaunchSnapshotRef": "effectiveLaunchSnapshot",
    "providerProfileRef": "profileLease",
    "providerLeaseId": "profileLease",
    "hostBindingRef": "hostBinding",
    "hostLeaseId": "hostLease",
    "hostId": "hostRegistration",
    "hostCapability": "hostRegistration",
    "bridgeSessionId": "bridgeSession",
    "omnigentSessionId": "omnigentSession",
    "firstMessageId": "bridgeEvents",
    "eventCursor": "bridgeEvents",
    "workspaceLocator": "workspace",
    "sourceCommit": "workspace",
    "resourceRefs": "artifactInventory",
    "artifactRefs": "artifactInventory",
    "terminalState": "terminalProjection",
    "cleanupState": "cleanupResult",
    "janitorState": "janitorState",
    "providerProfileRelease": "profileLease",
    "networkPolicyRef": "effectiveLaunchSnapshot",
    "runtime": "compiledExecutionRequest",
    "hostMode": "launchPolicy",
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
WORKFLOW_CHAT_ACTIONS = tuple(REQUIRED_WORKFLOW_CHAT_ROWS)
SCENARIOS = {
    "browser": f"{PROVIDER_TEST}::test_live_browser_release_matrix",
    "product": f"{PROVIDER_TEST}::test_live_product_create_api_journey",
    "cumulative": f"{PROVIDER_TEST}::test_live_cumulative_remediation_journey",
    "remediation": f"{PROVIDER_TEST}::test_live_operator_remediation_release_matrix",
    "stock": f"{PROVIDER_TEST}::test_live_stock_proxy_compatibility_profile",
    "static": f"{PROVIDER_TEST}::test_live_static_workflow_detail_restart_replay",
    "ondemand": f"{PROVIDER_TEST}::test_live_ondemand_oauth_lifecycle_and_cleanup",
    "failures": f"{PROVIDER_TEST}::test_live_failure_matrix_and_durable_evidence",
    "workflow_chat": (
        f"{PROVIDER_TEST}::test_live_native_workflow_chat_release_matrix"
    ),
}
EVIDENCE_ENV = {
    "logs": "MOONMIND_OMNIGENT_LOG_EVIDENCE",
    "temporalHistory": "MOONMIND_OMNIGENT_TEMPORAL_HISTORY_EVIDENCE",
    "screenshots": "MOONMIND_OMNIGENT_SCREENSHOT_EVIDENCE",
    "archives": "MOONMIND_OMNIGENT_ARCHIVE_EVIDENCE",
}
REMEDIATION_EVIDENCE_ENV = {
    "logs": "MOONMIND_OMNIGENT_LOG_EVIDENCE",
    "events": "MOONMIND_OMNIGENT_EVENT_EVIDENCE",
    "screenshotsCaptures": "MOONMIND_OMNIGENT_SCREENSHOT_EVIDENCE",
    "diagnostics": "MOONMIND_OMNIGENT_DIAGNOSTIC_EVIDENCE",
    "artifacts": "MOONMIND_OMNIGENT_ARTIFACT_EVIDENCE",
    "histories": "MOONMIND_OMNIGENT_TEMPORAL_HISTORY_EVIDENCE",
    "archives": "MOONMIND_OMNIGENT_ARCHIVE_EVIDENCE",
}
if set(REMEDIATION_EVIDENCE_ENV) != set(REQUIRED_REMEDIATION_RETAINED_CHANNELS):
    raise RuntimeError("operator-remediation retained evidence channels drifted")
SCENARIO_EVIDENCE_ENV = {
    "browser": "MOONMIND_OMNIGENT_BROWSER_EVIDENCE",
    "product": "MOONMIND_OMNIGENT_PRODUCT_EVIDENCE",
    "cumulative": "MOONMIND_OMNIGENT_CUMULATIVE_EVIDENCE",
    "remediation": "MOONMIND_OMNIGENT_REMEDIATION_MATRIX_EVIDENCE",
    "stock": "MOONMIND_OMNIGENT_STOCK_EVIDENCE",
    "static": "MOONMIND_OMNIGENT_STATIC_EVIDENCE",
    "ondemand": "MOONMIND_OMNIGENT_ONDEMAND_EVIDENCE",
    "failures": "MOONMIND_OMNIGENT_FAILURE_EVIDENCE",
    "workflow_chat": "MOONMIND_OMNIGENT_WORKFLOW_CHAT_EVIDENCE",
}


def _validated_catalog_bootstrap_evidence(
    value: object,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Validate the live adapter's bounded first-admission observations."""

    if not isinstance(value, dict):
        raise ConformanceContractError(
            "browser setup lacks catalog bootstrap observations"
        )
    required = {
        "schemaVersion",
        "observedAt",
        "providerSnapshotObserved",
        "eventTransportObserved",
        "serverImageRefObserved",
        "hostImageRefObserved",
        "uiBuildRefObserved",
    }
    if set(value) != required:
        raise ConformanceContractError(
            "browser setup catalog bootstrap observations have an invalid shape"
        )
    if (
        value.get("schemaVersion") != CATALOG_BOOTSTRAP_EVIDENCE_VERSION
        or value.get("providerSnapshotObserved") is not True
        or value.get("eventTransportObserved") is not True
    ):
        raise ConformanceContractError(
            "browser setup did not directly observe required provider capabilities"
        )
    server_ref = str(value.get("serverImageRefObserved") or "").strip()
    host_ref = str(value.get("hostImageRefObserved") or "").strip()
    ui_ref = str(value.get("uiBuildRefObserved") or "").strip()
    if (
        not _IMMUTABLE_IMAGE_REF.fullmatch(server_ref)
        or not _IMMUTABLE_IMAGE_REF.fullmatch(host_ref)
        or server_ref.endswith("@sha256:" + "0" * 64)
        or host_ref.endswith("@sha256:" + "0" * 64)
        or not _SAFE_BUILD_REF.fullmatch(ui_ref)
    ):
        raise ConformanceContractError(
            "browser setup lacks immutable observed deployment builds"
        )
    try:
        observed_at = datetime.fromisoformat(
            str(value.get("observedAt") or "").replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ConformanceContractError(
            "browser setup catalog bootstrap timestamp is invalid"
        ) from exc
    if observed_at.tzinfo is None:
        raise ConformanceContractError(
            "browser setup catalog bootstrap timestamp lacks a timezone"
        )
    age = (now or datetime.now(timezone.utc)) - observed_at.astimezone(timezone.utc)
    if age < timedelta(0) or age > timedelta(minutes=5):
        raise ConformanceContractError(
            "browser setup catalog bootstrap observations are not fresh"
        )
    return {
        "schemaVersion": CATALOG_BOOTSTRAP_EVIDENCE_VERSION,
        "observedAt": observed_at.astimezone(timezone.utc).isoformat(),
        "providerSnapshotObserved": True,
        "eventTransportObserved": True,
        "serverImageRefObserved": server_ref,
        "hostImageRefObserved": host_ref,
        "uiBuildRefObserved": ui_ref,
    }


def _validate_remediation_browser_lineage(
    *,
    row_id: str,
    browser_observation: dict[str, object],
    lineage: object,
    target_workflow_id: str,
    target_run_id: str,
) -> None:
    """Bind provider observations to the exact browser-created workflow pair."""

    browser_workflow_id = str(browser_observation.get("workflowId") or "").strip()
    if (
        browser_observation.get("targetWorkflowId") != target_workflow_id
        or browser_observation.get("targetRunId") != target_run_id
        or not isinstance(lineage, dict)
        or lineage.get("targetWorkflowId") != target_workflow_id
        or lineage.get("targetRunId") != target_run_id
    ):
        raise ConformanceContractError(
            f"remediation/{row_id} evidence is not bound to the pinned target"
        )
    if row_id == "remediation.autonomous.rollout-gate-closed":
        if (
            browser_workflow_id
            or browser_observation.get("normalCreateRequest") is not False
        ):
            raise ConformanceContractError(
                "remediation/autonomous gate evidence unexpectedly created a workflow"
            )
        return
    if (
        not browser_workflow_id
        or lineage.get("remediationWorkflowId") != browser_workflow_id
    ):
        raise ConformanceContractError(
            f"remediation/{row_id} evidence is not bound to the "
            "browser-created remediation workflow"
        )


class LiveRunner:
    def __init__(self, *, output_dir: Path, env: dict[str, str]) -> None:
        self.output_dir = output_dir
        self.env = env
        self.logs: list[Path] = []
        self.evidence_refs: list[str] = []
        self._scan_generation = 0
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
        if scenario in {
            "browser",
            "product",
            "cumulative",
            "remediation",
            "failures",
            "workflow_chat",
        }:
            records = [
                record
                for item in observations
                for record in item.get("sourceRecords", [])
                if isinstance(record, dict)
            ]
            required_types = (
                (
                    ADMISSION_RECORD_TYPES
                    if action in {
                        "failed_credential_readiness_admission",
                        "failed_host_registration_readiness",
                    }
                    else BROWSER_RECORD_TYPES
                )
                if scenario == "browser"
                else PRODUCT_RECORD_TYPES[action]
                if scenario == "product"
                else {"cumulativeRunState", "sideEffectAudit"}
                if scenario == "cumulative"
                else REMEDIATION_RECORD_TYPES
                if scenario == "remediation"
                else {"injectionControl", "terminalProjection", "sideEffectAudit"}
                if scenario == "failures"
                else REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS[action]
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
                if (
                    scenario == "remediation"
                    and urllib.parse.urlparse(record["ref"]).scheme != "file"
                ):
                    raise ConformanceContractError(
                        f"{scenario}/{action} source record is not durable local "
                        f"evidence: {record['type']}"
                    )
                raw = self._resolve_ref_bytes(record["ref"])
                if hashlib.sha256(raw).hexdigest() != record["sha256"].lower():
                    raise ConformanceContractError(
                        f"{scenario}/{action} source record digest does not match: {record['type']}"
                    )
                try:
                    record["_resolved"] = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ConformanceContractError(
                        f"{scenario}/{action} source record is not JSON: {record['type']}"
                    ) from exc
                if scenario == "remediation":
                    resolved_record = record["_resolved"]
                    if (
                        not isinstance(resolved_record, dict)
                        or resolved_record.get("schemaVersion")
                        != REMEDIATION_SOURCE_RECORD_SCHEMAS.get(record["type"])
                        or not isinstance(resolved_record.get("generatedAt"), str)
                        or not resolved_record["generatedAt"].strip()
                    ):
                        raise ConformanceContractError(
                            f"{scenario}/{action} source record lacks schema/freshness: "
                            f"{record['type']}"
                        )
                    record["_sizeBytes"] = len(raw)
            payload["_sourceRecordTypes"] = sorted(observed_types)
            payload["_sourceRecords"] = records
        returned_ids = {
            key: value for key, value in payload.items()
            if key in {
                "leaseId", "hostId", "workflowId", "agentRunId", "sessionId",
                "runId", "stepId", "bridgeId", "sourceWorkflowId",
                "destinationWorkflowId", "continuationId", "profileRef",
                "targetWorkflowId", "targetRunId", "remediationWorkflowId",
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
                    "targetWorkflowId", "targetRunId", "remediationWorkflowId",
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

    def browser_observation(
        self,
        row: str,
        *,
        target_workflow_id: str | None = None,
        target_run_id: str | None = None,
        authority_mode: str | None = None,
    ) -> dict[str, object]:
        """Run the fixed repository-owned Playwright journey for one matrix row."""
        browser_dir = self.output_dir / "browser"
        browser_dir.mkdir(parents=True, exist_ok=True)
        env = dict(self.env)
        env.update({
            "MOONMIND_OMNIGENT_BROWSER_ROW": row,
            "MOONMIND_OMNIGENT_BROWSER_OUTPUT_DIR": str(browser_dir),
        })
        if target_workflow_id is not None:
            env.update({
                "MOONMIND_OMNIGENT_REMEDIATION_TARGET_WORKFLOW_ID": target_workflow_id,
                "MOONMIND_OMNIGENT_REMEDIATION_TARGET_RUN_ID": str(target_run_id or ""),
                "MOONMIND_OMNIGENT_REMEDIATION_AUTHORITY_MODE": str(
                    authority_mode or "approval_gated"
                ),
            })
        result = subprocess.run(
            ["node", "tools/run_omnigent_browser_journey.mjs"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        log_path = browser_dir / f"{row}.log"
        log_path.write_text(
            f"--- STDOUT ---\n{result.stdout}\n--- STDERR ---\n{result.stderr}",
            encoding="utf-8",
        )
        self.logs.append(log_path)
        if result.returncode:
            raise RuntimeError(f"browser/{row} failed; see {log_path}")
        try:
            observation = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ConformanceContractError(
                f"browser/{row} controller returned invalid JSON"
            ) from exc
        expected_schema = (
            "moonmind.operator-remediation-browser-observation/v1"
            if target_workflow_id is not None
            else "moonmind.omnigent.browser-observation/v1"
        )
        if (
            not isinstance(observation, dict)
            or observation.get("schemaVersion") != expected_schema
            or observation.get("row") != row
        ):
            raise ConformanceContractError(
                f"browser/{row} controller returned a mismatched observation"
            )
        return observation

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

    def _portable_ref(self, ref: str) -> str:
        """Keep run-local file evidence resolvable after artifact publication."""

        parsed = urllib.parse.urlparse(ref)
        if parsed.scheme != "file":
            return ref
        path = Path(urllib.request.url2pathname(parsed.path)).resolve()
        root = self.output_dir.resolve()
        if path != root and root not in path.parents:
            raise ConformanceContractError(
                "workflow Chat evidence is outside the run output directory"
            )
        return str(path.relative_to(root))

    def _write_workflow_chat_report(
        self,
        *,
        name: str,
        images: dict[str, str],
        cases: list[dict[str, object]],
        scans: dict[str, dict[str, str]],
    ) -> Path:
        report = {
            "schemaVersion": REPORT_VERSION,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "images": images,
            "hostArchitecture": platform.machine(),
            "authMode": "codex-oauth",
            "protocolVersion": "omnigent/v1",
            "capabilities": ["workflow_chat"],
            "evidenceScans": scans,
            "cases": cases,
            "summary": {
                "passed": sum(case["status"] == "passed" for case in cases),
                "failed": sum(case["status"] == "failed" for case in cases),
                "skipped": sum(case["status"] == "skipped" for case in cases),
            },
        }
        assert_secret_free(report)
        path = self.output_dir / name
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return path

    def _scan_publication_tree(self) -> Path:
        """Fail closed unless every file about to be published is secret-free."""

        files: list[dict[str, str]] = []
        root = self.output_dir.resolve()
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if path.name == "publication-secret-scan.json":
                continue
            raw = path.read_bytes()
            assert_secret_free(raw.decode("utf-8", errors="replace"))
            files.append(
                {
                    "ref": str(path.relative_to(root)),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        if not files:
            raise ConformanceContractError(
                "workflow Chat publication tree contains no evidence"
            )
        payload = {"status": "passed", "files": files}
        assert_secret_free(payload)
        path = self.output_dir / "publication-secret-scan.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def _workflow_chat_combination(
        self,
        combination: WorkflowChatCombination,
        *,
        index: int,
        images: dict[str, str],
        source_commit: str,
    ) -> dict[str, object]:
        """Run one claimed combination's full protected journey."""

        rows: dict[str, dict[str, object]] = {}
        cases: list[dict[str, object]] = []
        case_refs: list[str] = []
        correlation: dict[str, str] | None = None
        state: dict[str, object] = {"combination": combination.combination_id}
        binding_identity: object | None = None
        bundle_digests: object | None = None
        timeline_ref: str | None = None
        cleanup_outcome: dict[str, object] | None = None
        for row_index, row_name in enumerate(WORKFLOW_CHAT_ACTIONS):
            started = time.monotonic()
            result = self.action("workflow_chat", row_name, **state)
            duration_ms = max(1, int((time.monotonic() - started) * 1000))
            if result.get("row") != row_name or result.get("combination") != (
                combination.combination_id
            ):
                raise ConformanceContractError(
                    "workflow Chat action returned the wrong row or combination: "
                    f"{combination.combination_id}/{row_name}"
                )
            records = result.get("_sourceRecords")
            if not isinstance(records, list):
                raise ConformanceContractError(
                    "workflow Chat action lacks resolved source records: "
                    f"{combination.combination_id}/{row_name}"
                )
            sources = {
                str(record["type"]): record["_resolved"]
                for record in records
                if isinstance(record, dict)
                and isinstance(record.get("_resolved"), dict)
            }
            assertions, observed_correlation = validate_workflow_chat_source_records(
                sources,
                row_name=row_name,
                source_commit=source_commit,
                images=images,
                generated_at=datetime.now(timezone.utc),
                combination=combination,
                expected_correlation=correlation,
            )
            if correlation is None:
                correlation = observed_correlation
            state = {
                "combination": combination.combination_id,
                **{
                    field: observed_correlation[field]
                    for field in (
                        "workflowId",
                        "chatBindingId",
                        "bridgeSessionId",
                        "providerSessionId",
                    )
                },
            }
            if row_name == "native-live-conversation":
                binding_identity = result.get("bindingIdentity")
                bundle_digests = result.get("bundleDigests")
            if row_name == "terminal-evidence-and-continuation":
                timeline_ref = self._portable_ref(str(result.get("timelineRef") or ""))
                cleanup_data = sources["cleanupReceipt"]["data"]
                cleanup_outcome = {
                    "liveResourcesRemoved": cleanup_data["liveResourcesRemoved"],
                    "providerProfileReleasedLast": cleanup_data[
                        "providerProfileReleasedLast"
                    ],
                    "cleanupState": cleanup_data["cleanupState"],
                }
            case_ref = f"workflow-chat-case-{index}-{row_index}.json"
            case_payload = {
                "schemaVersion": WORKFLOW_CHAT_CASE_EVIDENCE_VERSION,
                "issue": WORKFLOW_CHAT_ACCEPTANCE_ISSUE,
                "parentIssue": WORKFLOW_CHAT_PARENT_ISSUE,
                "combination": combination.combination_id,
                "row": row_name,
                "status": "passed",
                "sourceCommit": source_commit,
                "images": images,
                "stockHostUnmodified": True,
                "browserOriginated": True,
                "moonmindScopedOnly": True,
                "assertions": assertions,
                "sourceRecords": [
                    {
                        "type": record["type"],
                        "ref": self._portable_ref(str(record["ref"])),
                        "sha256": record["sha256"],
                    }
                    for record in records
                ],
                "observations": [
                    "typed production records resolved by the protected controller"
                ],
            }
            (self.output_dir / case_ref).write_text(
                json.dumps(case_payload, indent=2) + "\n", encoding="utf-8"
            )
            rows[row_name] = {
                "status": "passed",
                "assertions": assertions,
                "evidenceRefs": [case_ref],
            }
            case_refs.append(case_ref)
            cases.append(
                {
                    "caseId": f"workflow-chat-{combination.combination_id}-{row_name}",
                    "status": "passed",
                    "durationMs": duration_ms,
                    "evidenceRefs": [case_ref],
                    "diagnostics": [],
                }
            )
        if (
            binding_identity is None
            or timeline_ref is None
            or cleanup_outcome is None
            or not isinstance(bundle_digests, dict)
            or set(bundle_digests) != set(REQUIRED_BUNDLE_DIGESTS)
        ):
            raise ConformanceContractError(
                "workflow Chat combination did not report binding identity, "
                "bundle digests, operator timeline, and cleanup outcome: "
                f"{combination.combination_id}"
            )
        return {
            "rows": rows,
            "cases": cases,
            "caseRefs": case_refs,
            "bindingIdentity": binding_identity,
            "bundleDigests": {
                str(key): str(value) for key, value in bundle_digests.items()
            },
            "timelineRef": timeline_ref,
            "cleanupOutcome": cleanup_outcome,
        }

    def workflow_chat(self, images: dict[str, str], source_commit: str) -> None:
        """Own the protected #3642 native Workflow Chat combination matrix."""

        if not source_commit.strip():
            raise ConformanceContractError(
                "workflow Chat mode requires the tested source commit"
            )
        self.env["MOONMIND_OMNIGENT_SOURCE_COMMIT"] = source_commit
        self.env["MOONMIND_OMNIGENT_WORKFLOW_CHAT_EVIDENCE_DIR"] = str(
            self.output_dir
        )
        inventory = workflow_chat_combinations()
        combinations: dict[str, dict[str, object]] = {}
        aggregate_cases: list[dict[str, object]] = []
        bundle_digests: dict[str, str] | None = None
        for index, (combination_id, combination) in enumerate(inventory.items()):
            declared: dict[str, object] = {
                "schemaVersion": WORKFLOW_CHAT_COMBINATION_VERSION,
                "combinationId": combination_id,
                "harnessId": combination.harness_id,
                "hostClassRef": combination.host_class_ref,
                "launchPolicyRef": combination.launch_policy_ref,
                "executionRealizerRef": combination.execution_realizer_ref,
                "hostMode": combination.host_mode,
                "advertisedCapabilities": sorted(
                    combination.advertised_capabilities
                ),
            }
            if not combination.native_chat_claimed:
                # An unclaimed combination is reported with its stable reason
                # instead of being silently dropped from the matrix.
                combinations[combination_id] = {
                    **declared,
                    "status": "unsupported",
                    "unsupportedReason": combination.unsupported_reason,
                }
                continue
            self.env["MOONMIND_OMNIGENT_WORKFLOW_CHAT_COMBINATION"] = combination_id
            self.env["MOONMIND_OMNIGENT_WORKFLOW_CHAT_HARNESS_ID"] = (
                combination.harness_id
            )
            self.env["MOONMIND_OMNIGENT_WORKFLOW_CHAT_HOST_CLASS_REF"] = (
                combination.host_class_ref
            )
            self.env["MOONMIND_OMNIGENT_WORKFLOW_CHAT_LAUNCH_POLICY_REF"] = (
                combination.launch_policy_ref
            )
            self.env["MOONMIND_OMNIGENT_WORKFLOW_CHAT_REALIZER_REF"] = (
                combination.execution_realizer_ref
            )
            self.run(
                f"workflow-chat-up-{combination_id}",
                self.compose_profile(
                    combination.compose_profile,
                    "up",
                    "-d",
                    "--wait",
                    *combination.compose_services,
                ),
            )
            observed = self._workflow_chat_combination(
                combination,
                index=index,
                images=images,
                source_commit=source_commit,
            )
            aggregate_cases.extend(observed["cases"])
            if bundle_digests is None:
                bundle_digests = observed["bundleDigests"]
            elif bundle_digests != observed["bundleDigests"]:
                raise ConformanceContractError(
                    "workflow Chat combinations loaded different deployed bundles"
                )
            combinations[combination_id] = {
                **declared,
                "status": "passed",
                "unsupportedReason": None,
                "bindingIdentity": observed["bindingIdentity"],
                "rows": observed["rows"],
                "reports": [f"workflow-chat-report-{index}.json"],
                "timelineRef": observed["timelineRef"],
                "cleanupOutcome": observed["cleanupOutcome"],
                "_cases": observed["cases"],
            }
        if bundle_digests is None:
            raise ConformanceContractError(
                "workflow Chat matrix claims no supported combination"
            )
        scans = self.scan()
        for entry in combinations.values():
            cases = entry.pop("_cases", None)
            if cases is None:
                continue
            self._write_workflow_chat_report(
                name=str(entry["reports"][0]),
                images=images,
                cases=cases,
                scans=scans,
            )
        self._write_workflow_chat_report(
            name="workflow-chat-report.json",
            images=images,
            cases=aggregate_cases,
            scans=scans,
        )
        generated_at = datetime.now(timezone.utc)
        matrix = {
            "generatedAt": generated_at.isoformat(),
            "expiresAt": (generated_at + timedelta(days=7)).isoformat(),
            "sourceCommit": source_commit,
            "compatibilityProfile": WORKFLOW_CHAT_COMPATIBILITY_PROFILE,
            "images": images,
            "bundleDigests": bundle_digests,
            "supersededReportRef": (
                self.env.get(
                    "MOONMIND_OMNIGENT_WORKFLOW_CHAT_SUPERSEDED_REPORT", ""
                ).strip()
                or None
            ),
            "combinations": combinations,
            "evidenceScans": scans,
        }
        matrix_path = self.output_dir / "workflow-chat-matrix.json"
        matrix_path.write_text(
            json.dumps(matrix, indent=2) + "\n", encoding="utf-8"
        )
        manifest = build_workflow_chat_acceptance_manifest(
            matrix, evidence_root=self.output_dir
        )
        validate_workflow_chat_acceptance_manifest(
            manifest,
            evidence_root=self.output_dir,
            expected_commit=source_commit,
            now=generated_at,
        )
        manifest_path = self.output_dir / "workflow-chat-acceptance.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.env[SCENARIO_EVIDENCE_ENV["workflow_chat"]] = str(manifest_path)
        self.scenario("workflow_chat")

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

    def browser(self) -> None:
        """Control /workflows/new and prove every #3508 release row."""
        rows: dict[str, dict[str, object]] = {}
        evidence_refs: list[str] = []
        for row in BROWSER_ROWS:
            setup = self.action("browser-setup", row)
            bootstrap_evidence = _validated_catalog_bootstrap_evidence(
                setup.get("catalogBootstrapEvidence")
            )
            # Only the protected canary receives this bounded live-observation
            # manifest. The catalog authenticates the separate canary bearer
            # before it parses or trusts these values.
            self.env[CATALOG_BOOTSTRAP_EVIDENCE_ENV] = json.dumps(
                bootstrap_evidence,
                separators=(",", ":"),
            )
            observation = self.browser_observation(row)
            result = self.action("browser", row, browserObservation=observation)
            if result.get("row") != row:
                raise ConformanceContractError(
                    f"browser row identity mismatch: expected {row!r}"
                )
            admission_failure = row in {
                "failed_credential_readiness_admission",
                "failed_host_registration_readiness",
            }
            if admission_failure:
                expected_reason = (
                    "credential_readiness"
                    if row == "failed_credential_readiness_admission"
                    else "host_registration_readiness"
                )
                selected = observation.get("selected")
                admission_authority = result.get("admissionAuthority")
                if (
                    observation.get("admissionRejected") is not True
                    or observation.get("createRequestCount") != 0
                    or observation.get("admissionReason") != expected_reason
                    or not isinstance(selected, dict)
                    or not isinstance(admission_authority, dict)
                    or selected.get("profileId")
                    != admission_authority.get("providerProfileRef")
                ):
                    raise ConformanceContractError(
                        f"browser/{row} did not prove its distinct fail-closed admission"
                    )
                rows[row] = {
                    "status": "passed",
                    "assertions": {
                        "browser_originated": True,
                        "normal_create_request_rejected": True,
                        "distinct_admission_reason": True,
                        "no_fallback": bool(result.get("noFallback")),
                    },
                    "authorityChain": admission_authority,
                    "browserObservation": observation,
                    "evidenceRefs": result["evidenceRefs"],
                }
                if not all(rows[row]["assertions"].values()):
                    raise ConformanceContractError(
                        f"browser/{row} did not prove no fallback"
                    )
                evidence_refs.extend(setup["evidenceRefs"])
                evidence_refs.extend(result["evidenceRefs"])
                continue
            authority = result.get("authorityChain")
            if not isinstance(authority, dict):
                raise ConformanceContractError(
                    f"browser/{row} lacks the complete authority chain"
                )
            missing = [field for field in BROWSER_AUTHORITY_FIELDS if not authority.get(field)]
            if missing:
                raise ConformanceContractError(
                    f"browser/{row} lacks the complete authority chain: {missing}"
                )
            if authority.get("hostCapability") != "codex-native":
                raise ConformanceContractError(
                    f"browser/{row} did not reach the codex-native capability"
                )
            if authority.get("runtime") != "external/omnigent":
                raise ConformanceContractError(
                    f"browser/{row} used a non-Omnigent runtime"
                )
            selected = observation.get("selected")
            if not isinstance(selected, dict):
                raise ConformanceContractError(
                    f"browser/{row} lacks the selected browser authorities"
                )
            expected = {
                "providerProfileRef": selected.get("profileId"),
                "executionProfileRef": selected.get("executionTargetRef"),
                "launchPolicyRef": selected.get("launchPolicyRef"),
                "runtime": "external/omnigent",
                "hostCapability": "codex-native",
            }
            if selected.get("hostMode"):
                expected["hostMode"] = selected["hostMode"]
            mismatches = {
                key: (value, authority.get(key))
                for key, value in expected.items()
                if authority.get(key) != value
            }
            if mismatches:
                raise ConformanceContractError(
                    f"browser/{row} selected authorities do not match observed records: {mismatches}"
                )
            records = result.get("_sourceRecords")
            if not isinstance(records, list):
                raise ConformanceContractError(f"browser/{row} lacks resolved records")
            positions = {
                record.get("type"): index
                for index, record in enumerate(records)
                if isinstance(record, dict)
            }
            if any(
                positions[BROWSER_RECORD_ORDER[index]]
                >= positions[BROWSER_RECORD_ORDER[index + 1]]
                for index in range(len(BROWSER_RECORD_ORDER) - 1)
            ):
                raise ConformanceContractError(
                    f"browser/{row} authority records are not lifecycle ordered"
                )
            resolved_by_type = {
                record["type"]: json.dumps(record.get("_resolved"), sort_keys=True)
                for record in records
            }
            unbound = [
                field
                for field, value in authority.items()
                if field in BROWSER_AUTHORITY_FIELDS
                and json.dumps(value, sort_keys=True)
                not in resolved_by_type[BROWSER_FIELD_RECORD_TYPE[field]]
            ]
            if unbound:
                raise ConformanceContractError(
                    f"browser/{row} authority values are not bound to resolved records: {unbound}"
                )
            assertions = {
                "browser_originated": observation.get("workflowId") == result.get("workflowId"),
                "normal_create_request": bool(observation.get("createRequest")),
                "workflow_detail_terminal_replay": (
                    observation.get("terminalUrl") == observation.get("replayUrl")
                    and observation.get("replayComplete") is True
                    and observation.get("hostRemovedBeforeReplay") is True
                ),
                "no_fallback": not mismatches,
            }
            if row == "active_cancellation_interruption":
                assertions["active_control"] = (
                    observation.get("controlAction") == "cancel_or_interrupt"
                    and authority.get("terminalState") in {"cancelled", "interrupted"}
                )
            if row == "partial_start_cleanup_janitor":
                assertions["janitor_reconciliation"] = (
                    observation.get("janitorReconciled") is True
                    and authority.get("janitorState") in {"reconciled", "completed"}
                )
            if row in {"repository_read_analysis", "repository_mutation_publication"}:
                assertions["repository_outcome"] = (
                    observation.get("repositoryOutcome") == "read_analysis"
                    if row == "repository_read_analysis"
                    else result.get("repositoryMutationPublished") is True
                    and bool(result.get("repositoryCommitSha"))
                    and bool(result.get("publicationRef"))
                )
            if row == "static_restart_replay":
                assertions["static_restart"] = (
                    result.get("staticHostRestarted") is True
                    and bool(result.get("hostIdentityBeforeRestart"))
                    and bool(result.get("hostIdentityAfterRestart"))
                    and result.get("hostIdentityBeforeRestart")
                    != result.get("hostIdentityAfterRestart")
                )
            if not all(assertions.values()):
                raise ConformanceContractError(
                    f"browser/{row} did not prove browser control or no fallback"
                )
            rows[row] = {
                "status": "passed",
                "assertions": assertions,
                "authorityChain": authority,
                "browserObservation": observation,
                "evidenceRefs": result["evidenceRefs"],
            }
            evidence_refs.extend(setup["evidenceRefs"])
            evidence_refs.extend(result["evidenceRefs"])
        self.write_evidence("browser", {
            "issue": "MoonLadderStudios/MoonMind#3508",
            "parentIssue": "MoonLadderStudios/MoonMind#3448",
            "entrypoint": "/workflows/new",
            "rows": rows,
            "evidenceRefs": evidence_refs,
        })
        self.scenario("browser")

    def remediation(self, images: dict[str, str]) -> None:
        """Drive every #3626 row through Detail -> Remediate -> normal Create.

        The deployment adapter supplies source-record refs and raw observations.
        This repository-owned controller resolves their digests and derives row
        qualification from the catalog; it never accepts caller-owned rows or a
        caller-supplied ``passed`` field.
        """

        policy_version = self.env.get(
            "MOONMIND_OMNIGENT_LAUNCH_POLICY_VERSION", ""
        ).strip()
        agent_profile_version = self.env.get(
            "MOONMIND_OMNIGENT_AGENT_PROFILE_VERSION", ""
        ).strip()
        remediation_policy_version = self.env.get(
            "MOONMIND_OMNIGENT_REMEDIATION_POLICY_VERSION", ""
        ).strip()
        if not all(
            (policy_version, agent_profile_version, remediation_policy_version)
        ):
            raise ConformanceContractError(
                "remediation matrix requires launch, Agent Profile, and remediation policy versions"
            )

        captured: list[
            tuple[
                object,
                dict[str, object],
                dict[str, object],
                list[dict[str, object]],
            ]
        ] = []
        for row in REMEDIATION_ROW_CATALOG:
            setup = self.action("browser-setup", row.row_id)
            target_workflow_id = str(setup.get("targetWorkflowId") or "").strip()
            target_run_id = str(setup.get("targetRunId") or "").strip()
            if not target_workflow_id or not target_run_id:
                raise ConformanceContractError(
                    f"remediation setup lacks pinned target identity: {row.row_id}"
                )
            browser_observation = self.browser_observation(
                row.row_id,
                target_workflow_id=target_workflow_id,
                target_run_id=target_run_id,
                authority_mode=row.authority_mode,
            )
            result = self.action(
                "remediation",
                row.row_id,
                browserObservation=browser_observation,
            )
            records = result.get("_sourceRecords")
            if not isinstance(records, list):
                raise ConformanceContractError(
                    f"remediation/{row.row_id} lacks resolved source records"
                )
            scenario_records = [
                record
                for record in records
                if record.get("type") == "scenarioObservation"
            ]
            if len(scenario_records) != 1:
                raise ConformanceContractError(
                    f"remediation/{row.row_id} requires one scenario observation"
                )
            try:
                facts = derive_remediation_observation_from_source_records(
                    row=row,
                    manifest_by_type={str(record["type"]): record for record in records},
                    sources={
                        str(record["type"]): record["_resolved"]
                        for record in records
                    },
                )
            except RemediationMatrixError as exc:
                raise ConformanceContractError(
                    f"remediation/{row.row_id} source records are semantically invalid"
                ) from exc
            if facts["observedDisposition"] != row.expected_outcome:
                raise ConformanceContractError(
                    f"remediation/{row.row_id} did not observe its required disposition"
                )
            if facts["hostMode"] not in row.host_modes:
                raise ConformanceContractError(
                    f"remediation/{row.row_id} used an unsupported host mode"
                )
            selected = browser_observation.get("selected")
            selected_host_mode = (
                "static"
                if isinstance(selected, dict)
                and selected.get("hostMode") == "static_compose"
                else "on_demand"
            )
            if selected_host_mode != facts["hostMode"]:
                raise ConformanceContractError(
                    f"remediation/{row.row_id} browser and host evidence disagree"
                )
            lineage = facts["lineage"]
            _validate_remediation_browser_lineage(
                row_id=row.row_id,
                browser_observation=browser_observation,
                lineage=lineage,
                target_workflow_id=target_workflow_id,
                target_run_id=target_run_id,
            )
            if (
                facts["architecture"] not in row.architectures
                or facts["targetProvenance"] not in row.target_provenance
                or facts["remediationProvenance"]
                not in row.remediation_provenance
            ):
                raise ConformanceContractError(
                    f"remediation/{row.row_id} runtime provenance is unsupported"
                )
            if facts["telemetryFacts"]["remainingLiveResources"] != 0:
                raise ConformanceContractError(
                    f"remediation/{row.row_id} retained live resources after cleanup"
                )

            threshold_samples = facts["thresholds"]
            if not isinstance(threshold_samples, dict):
                raise ConformanceContractError(
                    f"remediation/{row.row_id} lacks threshold samples"
                )
            for threshold in row.thresholds:
                sample = threshold_samples.get(threshold)
                if (
                    not isinstance(sample, dict)
                    or sample.get("within") is not True
                    or not isinstance(sample.get("passed"), int)
                    or not isinstance(sample.get("total"), int)
                    or sample["total"] < 1
                    or sample["passed"] != sample["total"]
                ):
                    raise ConformanceContractError(
                        f"remediation/{row.row_id} exceeded threshold {threshold}"
                    )
            required_observations = facts["observations"]
            if not isinstance(required_observations, dict) or any(
                required_observations.get(name) is not True
                for name in row.required_observations
            ):
                raise ConformanceContractError(
                    f"remediation/{row.row_id} lacks required sub-scenario evidence"
                )
            if not isinstance(lineage, dict) or any(
                not lineage.get(key)
                for key in REQUIRED_REMEDIATION_LINEAGE_FIELDS
            ):
                raise ConformanceContractError(
                    f"remediation/{row.row_id} lacks complete durable lineage"
                )
            captured.append((row, browser_observation, facts, records))

        scans = self.scan(remediation=True)
        architectures = sorted({str(facts["architecture"]) for _, _, facts, _ in captured})
        artifacts: dict[str, dict[str, object]] = {
            kind: {
                "schemaVersion": REMEDIATION_ARTIFACT_SCHEMA_VERSION,
                "matrixVersion": REMEDIATION_MATRIX_VERSION,
                "kind": kind,
                "producerVersion": "moonmind.operator-remediation-live-observer/v1",
                "rows": [],
            }
            for kind in REQUIRED_REMEDIATION_EVIDENCE_KINDS
        }
        browser_rows: dict[str, object] = {}
        for row, browser_observation, facts, records in captured:
            ui_journey = {
                "journey": row.ui_journey,
                **{
                    assertion: browser_observation.get(assertion)
                    for assertion in REQUIRED_UI_JOURNEY_ASSERTIONS
                },
                **{
                    marker: browser_observation.get(marker)
                    for marker in PROHIBITED_UI_JOURNEY_MARKERS
                },
            }
            if row.row_id == "remediation.autonomous.rollout-gate-closed":
                ui_journey["autonomousAdmissionDenied"] = (
                    browser_observation.get("admissionRejected") is True
                    and browser_observation.get("admissionReason")
                    == "autonomous_rollout_gate"
                )
            entry = {
                "row": row.row_id,
                "gate": row.gate,
                "observedDisposition": facts["observedDisposition"],
                "hostMode": facts["hostMode"],
                "architecture": facts["architecture"],
                "images": dict(images),
                "targetProvenance": facts["targetProvenance"],
                "remediationProvenance": facts["remediationProvenance"],
                "authorityMode": facts["authorityMode"],
                "egress": facts["egress"],
                "actionCapability": facts["actionCapability"],
                "verificationCapability": facts["verificationCapability"],
                "uiJourney": ui_journey,
                "actionDelivery": facts["actionDelivery"],
                "repairVerification": facts["repairVerification"],
                "timings": facts["timings"],
                "observations": {
                    name: facts["observations"][name]
                    for name in row.required_observations
                },
                "lineage": dict(facts["lineage"]),
                "evidenceManifest": [
                    {
                        "type": record["type"],
                        "ref": record["ref"],
                        "sha256": record["sha256"],
                        "schemaVersion": record["_resolved"]["schemaVersion"],
                        "contentType": "application/json",
                        "sizeBytes": record["_sizeBytes"],
                        "generatedAt": record["_resolved"]["generatedAt"],
                    }
                    for record in records
                ],
                "thresholds": {
                    threshold: {
                        "within": True,
                        "passed": facts["thresholds"][threshold]["passed"],
                        "total": facts["thresholds"][threshold]["total"],
                    }
                    for threshold in row.thresholds
                },
                "secretScan": scans,
                "profileVersion": PROFILE_VERSION,
                "profileSha256": PROFILE_SHA256,
                "launchPolicyVersion": policy_version,
                "agentProfileVersion": agent_profile_version,
                "remediationPolicyVersion": remediation_policy_version,
            }
            artifacts[row.evidence_kind]["rows"].append(entry)
            browser_rows[row.row_id] = {
                "browserObservation": browser_observation,
                "sourceRecordTypes": sorted(
                    str(record["type"]) for record in records
                ),
            }

        artifact_refs = []
        for kind, artifact in artifacts.items():
            path = self.output_dir / f"operator-remediation-{kind}.json"
            validate_remediation_evidence_artifact(
                artifact,
                expected_kind=kind,
                images=images,
                architectures=architectures,
                profile_version=PROFILE_VERSION,
                profile_sha256=PROFILE_SHA256,
                policy_version=policy_version,
                agent_profile_version=agent_profile_version,
                remediation_policy_version=remediation_policy_version,
                evidence_document_path=path,
            )
            path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
            artifact_refs.append(path.resolve().as_uri())

        self.write_evidence("remediation", {
            "issue": "MoonLadderStudios/MoonMind#3626",
            "matrixVersion": REMEDIATION_MATRIX_VERSION,
            "entrypoint": "workflow-detail.remediate.normal-create",
            "rows": browser_rows,
            "artifactRefs": artifact_refs,
            "architectures": architectures,
        })
        self.scenario("remediation")

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
        return LiveRunner.compose_profile("omnigent-host-codex", *args)

    @staticmethod
    def compose_profile(profile: str, *args: str) -> list[str]:
        return [
            "docker", "compose", "--project-name", PROJECT,
            "--profile", profile, *args,
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

    def scan(self, *, remediation: bool = False) -> dict[str, dict[str, object]]:
        self._scan_generation += 1
        scans: dict[str, dict[str, object]] = {}
        evidence_channels = REMEDIATION_EVIDENCE_ENV if remediation else EVIDENCE_ENV
        for channel, env_name in evidence_channels.items():
            raw = self.env.get(env_name, "")
            paths = [Path(item) for item in raw.split(os.pathsep) if item]
            if channel == "logs":
                paths.extend(self.logs)
            if channel in {"screenshots", "screenshotsCaptures"}:
                paths.extend(sorted(self.output_dir.rglob("*-terminal.png")))
                paths.extend(sorted(self.output_dir.rglob("*-replay.png")))
                paths.extend(sorted(self.output_dir.rglob("*-target.png")))
            if not paths or any(not path.is_file() for path in paths):
                raise ConformanceContractError(f"{channel} evidence was not collected")
            unique_paths = list(dict.fromkeys(path.resolve() for path in paths))
            files: list[dict[str, str]] = []
            for index, evidence in enumerate(unique_paths):
                raw_bytes = evidence.read_bytes()
                assert_secret_free(raw_bytes.decode("utf-8", errors="replace"))
                retained_text = raw_bytes.decode("utf-8", errors="replace")
                if PROHIBITED_RETAINED_AUTHORITY.search(retained_text):
                    raise ConformanceContractError(
                        f"{channel} evidence retained prohibited raw authority"
                    )
                root = self.output_dir.resolve()
                if evidence != root and root not in evidence.parents:
                    staged = self.output_dir / "raw-evidence" / (
                        f"{self._scan_generation:02d}-{channel}-{index}-{evidence.name}"
                    )
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    staged.write_bytes(raw_bytes)
                else:
                    staged = evidence
                files.append(
                    {
                        "ref": str(staged.resolve().relative_to(root)),
                        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                    }
                )
            scan_path = self.output_dir / (
                f"secret-scan-{self._scan_generation:02d}-{channel}.json"
            )
            generated_at = datetime.now(timezone.utc).isoformat()
            scan_content = (
                json.dumps(
                    {
                        "schemaVersion": "moonmind.retained-evidence-secret-scan/v1",
                        "generatedAt": generated_at,
                        "channel": channel,
                        "status": "passed",
                        "secretFindings": 0,
                        "prohibitedAuthorityFindings": 0,
                        "files": files,
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode()
            scan_path.write_bytes(scan_content)
            scans[channel] = {
                "status": "passed",
                "evidenceRef": scan_path.name,
                "sha256": hashlib.sha256(scan_content).hexdigest(),
                "schemaVersion": "moonmind.retained-evidence-secret-scan/v1",
                "contentType": "application/json",
                "sizeBytes": len(scan_content),
                "generatedAt": generated_at,
            }
        return scans


def main() -> int:
    parser = argparse.ArgumentParser(description="Run protected live Omnigent conformance")
    parser.add_argument("--mode", choices=(*LIVE_CASES, "all"), default="all")
    parser.add_argument("--server-image", required=True)
    parser.add_argument("--host-image", required=True)
    parser.add_argument(
        "--source-commit",
        default=os.environ.get("GITHUB_SHA", ""),
        help="tested repository commit (required for workflow_chat mode)",
    )
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
    if "workflow_chat" in selected and not args.source_commit.strip():
        print(
            "live conformance failed: workflow Chat mode requires --source-commit",
            file=sys.stderr,
        )
        return 2
    passed: set[str] = set()
    failure: str | None = None
    try:
        for mode in selected:
            try:
                if mode == "browser":
                    runner.browser()
                elif mode == "product":
                    runner.product()
                elif mode == "cumulative":
                    runner.cumulative()
                elif mode == "remediation":
                    runner.remediation(images)
                elif mode == "stock":
                    runner.stock(images)
                elif mode == "static":
                    runner.static()
                elif mode == "ondemand":
                    runner.ondemand()
                elif mode == "workflow_chat":
                    runner.workflow_chat(images, args.source_commit)
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
        report = None
        if selected == ("workflow_chat",):
            if failure is None:
                report = json.loads(
                    (output_dir / "workflow-chat-report.json").read_text(
                        encoding="utf-8"
                    )
                )
        else:
            report = build_report(
                profile=profile,
                images=images,
                host_architecture=platform.machine(),
                auth_mode="codex-oauth",
                capabilities=selected,
                cases=results,
                protocol_version="omnigent/v1",
                evidence_scans=scans,
            )
        if report is not None:
            (output_dir / "report.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
        if "workflow_chat" in selected and failure is None:
            runner._scan_publication_tree()
    except (ConformanceContractError, OSError, json.JSONDecodeError) as exc:
        failure = failure or str(exc)
    if failure:
        print(f"live conformance failed: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
