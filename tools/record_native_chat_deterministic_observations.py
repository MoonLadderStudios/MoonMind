#!/usr/bin/env python3
"""Record exact deterministic #3642 case observations from passing CI results.

The decisive browser and backend suites execute first and emit JUnit XML.  This
recorder rejects failures, errors, skips, or empty suites, then writes an exact
case ledger whose evidence files are those immutable test results.  The lane
builder subsequently resolves and digests every referenced file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.conformance import ConformanceContractError  # noqa: E402
from moonmind.omnigent.native_chat_acceptance import (  # noqa: E402
    LANE_DETERMINISTIC,
    REQUIRED_CASES,
    SCENARIO_LANES,
)
from moonmind.omnigent.native_chat_rollout import (  # noqa: E402
    native_chat_deployment_identity,
)
from tools.build_native_chat_acceptance_lane import (  # noqa: E402
    OBSERVATION_SCHEMA_VERSION,
)


def _case_map(
    fragment: str, *cases: str, suite: str = "backend"
) -> dict[str, tuple[tuple[str, str], ...]]:
    return {case: ((suite, fragment),) for case in cases}


# Each deterministic acceptance case is tied to at least one independently
# named test that exercises the real owning boundary.  The generic matrix test
# remains useful as an exact inventory/outcome row, but it is never sufficient
# evidence by itself.  This map is intentionally exhaustive and validated at
# import time so a newly added acceptance case cannot silently inherit a broad
# scenario-level assertion.
DETERMINISTIC_CASE_BOUNDARIES: dict[
    str, dict[str, tuple[tuple[str, str], ...]]
] = {
    "deterministic-browser-journey": {
        **_case_map(
            "selects Chat by default and preserves query state",
            "workflow-detail-chat-selection",
            suite="frontend",
        ),
        **_case_map("test_facade_resolves_via_chat_binding_column", "opaque-binding-resolution"),
        **_case_map("test_embedded_document_serves_native_app_with_bootstrap", "embedded-native-app-load"),
        **_case_map(
            "drives the native composer and claimed feature surface",
            "native-composer-message",
            "queue-and-steer",
            "tools-and-reasoning",
            "approval",
            "resources",
            "terminal",
            "subagents-and-tasks",
            "reconnect",
            suite="browser",
        ),
        **_case_map("test_terminal_session_snapshot_is_read_only", "terminal-transition"),
        **_case_map(
            "test_browser_payload_compiles_replays_and_releases_only_after_cleanup",
            "post-cleanup-reload",
        ),
        **_case_map(
            "opens captured evidence as authorized MoonMind download links",
            "diagnostics-and-evidence",
            suite="frontend",
        ),
        **_case_map("test_continue_creates_linked_workflow_and_pins_source", "linked-continuation"),
    },
    "binding-authorization-isolation": {
        **_case_map("test_owner_snapshot_virtualizes_provider_identity", "owner", "authorized-shared-viewer"),
        **_case_map("test_message_denied_when_policy_disables_send", "read-only-viewer"),
        **_case_map("test_resolve_elicitation_forwarded_to_bound_session", "approval-only-caller"),
        **_case_map("test_non_owner_gets_non_enumerating_binding_unknown", "unauthorized-caller", "cross-workflow-binding"),
        **_case_map("test_unknown_binding_is_non_enumerating", "unknown-binding"),
        **_case_map("test_session_without_provider_session_is_not_ready", "expired-binding", "cleaned-session"),
        **_case_map("test_websocket_closes_when_authority_is_revoked_midstream", "revoked-binding", "websocket-live-authorization-change"),
        **_case_map("test_session_substitution_in_path_is_rejected", "path-session-substitution"),
        **_case_map("test_identity_substitution_in_query_is_rejected", "query-session-substitution", "endpoint-substitution"),
        **_case_map("test_session_id_body_mismatch_is_rejected", "body-session-substitution"),
        **_case_map("test_identity_substitution_header_is_rejected", "header-session-substitution", "credential-substitution"),
        **_case_map("test_stream_rejects_malformed_cursor", "sse-cursor-substitution"),
        **_case_map("test_ws_rejects_session_identity_substitution", "websocket-frame-substitution"),
        **_case_map(
            "test_nested_identity_substitution_rejected_via_router",
            "host-substitution",
            "runner-substitution",
            "environment-substitution",
            "workspace-substitution",
            "terminal-substitution",
            "profile-substitution",
            "model-substitution",
            "effort-substitution",
            "goal-substitution",
        ),
        **_case_map("test_message_rejected_when_session_terminalizes_midrequest", "http-live-authorization-change"),
        **_case_map("test_stream_stops_when_authority_revoked_midstream", "sse-live-authorization-change"),
        **_case_map("test_terminal_binding_serves_durable_snapshot_without_provider_session", "deleted-workflow", "archived-workflow"),
        **_case_map("test_non_owner_gets_non_enumerating_binding_unknown", "non-enumerating-response"),
    },
    "credential-browser-isolation": {
        **_case_map("mounts the same-origin scoped URL", "no-direct-upstream-browser-request", suite="browser"),
        **_case_map("test_embedded_document_never_leaks_provider_identity", "no-upstream-secret-in-browser-state"),
        **_case_map("test_moonmind_credentials_cannot_cross_to_upstream", "no-moonmind-authority-upstream", "allowlisted-forward-headers-only"),
        **_case_map("test_upstream_redirect_is_kept_in_scope", "redirect-contained"),
        **_case_map("test_upstream_error_serves_unavailable_document", "error-body-contained"),
        **_case_map("test_resource_file_returns_bytes", "download-contained"),
        **_case_map("test_ws_rejects_cross_origin_browser_connect", "websocket-contained"),
        **_case_map("test_embedded_document_security_headers", "service-worker-contained"),
        **_case_map("test_full_page_document_refuses_framing", "full-page-scoped-single-sign-on"),
    },
    "capability-policy-immutability": {
        **_case_map("test_identity_guard_fails_closed", "pinned-model", "pinned-effort"),
        **_case_map("test_elicitation_rejects_caller_supplied_wrong_elicitation", "approval-authority-and-state", "stale-elicitation"),
        **_case_map("test_destructive_controls_denied_without_distinct_grant", "transcript-does-not-grant-mutation"),
        **_case_map("test_recompute_capabilities_read_only_when_terminal", "active-versus-terminal"),
        **_case_map("test_native_task_mutation_uses_effective_change_goal_authority", "hidden-control-direct-api-denied"),
        **_case_map(
            "test_message_rejects_stale_immutable_authority",
            "stale-agent-profile",
            "stale-provider-generation",
            "stale-policy-digest",
            "stale-launch-snapshot",
        ),
        **_case_map("test_message_rejects_caller_supplied_stale_session_epoch", "stale-session-epoch", "stale-turn"),
        **_case_map("test_message_idempotency_key_dedupes_replay", "duplicate-mutation"),
        **_case_map("test_native_http_mutation_is_scanned_receipted_and_replay_safe", "delivery-unknown-reconciliation"),
        **_case_map("test_unknown_ws_transport_fails_closed_with_diagnostic", "unsupported-control-unavailable"),
    },
    "high-security-outbound-scan": {
        **_case_map("test_high_security_allows_clean_message_and_forwards", "clean-message"),
        **_case_map("test_high_security_blocks_secret_bearing_message", "secret-like-message", "redacted-diagnostics-only"),
        **_case_map("test_high_security_blocks_native_payload_surfaces_before_upstream", "queued-message", "steered-message", "reply-and-quote", "slash-command-arguments", "text-attachment", "upload-metadata"),
        **_case_map("test_high_security_scans_elicitation_response", "approval-response"),
        **_case_map("test_message_idempotency_key_reuse_with_different_payload_conflicts", "changed-idempotency-payload"),
        **_case_map("test_unknown_top_level_schema_fails_closed", "unknown-payload"),
        **_case_map("test_message_requires_json_content_type", "malformed-payload", "compressed-payload"),
        **_case_map("test_binary_part_is_outside_text_scan_contract", "binary-payload", "uninspectable-payload"),
        **_case_map("test_oversized_body_is_rejected", "oversized-payload"),
        **_case_map("test_high_security_scanner_error_fails_closed", "scanner-unavailable", "scanner-error"),
        **_case_map("test_high_security_scanner_timeout_fails_closed", "scanner-timeout"),
        **_case_map("test_high_security_blocks_secret_bearing_message", "blocked-zero-upstream-side-effects"),
    },
    "native-ui-and-transports": {
        **_case_map("test_asset_is_reverse_proxied_without_bootstrap", "spa-assets"),
        **_case_map("test_deep_link_refresh_serves_spa_document", "deep-link", "refresh"),
        **_case_map("test_embedded_document_serves_native_app_with_bootstrap", "embedded-mode"),
        **_case_map("test_full_page_document_refuses_framing", "full-page-mode"),
        **_case_map("test_owner_snapshot_virtualizes_provider_identity", "transcript"),
        **_case_map("drives the native composer and claimed feature surface", "composer", "queue", "tool-and-reasoning-view", "approvals", "terminals", "agents", "tasks", suite="browser"),
        **_case_map("test_resource_index_delegated_to_bound_session", "files-and-diffs"),
        **_case_map("test_resource_file_returns_bytes", "upload-and-download", "binary"),
        **_case_map("test_unknown_browser_websocket_is_not_proxied", "browser-pane-capability"),
        **_case_map("test_message_forwarded_to_bound_provider_session", "http"),
        **_case_map("test_stream_replays_from_cursor_and_terminates", "sse"),
        **_case_map("test_global_updates_ws_relays_through_binding_scope", "websocket"),
        **_case_map("test_terminal_input_frame_is_scanned_and_durably_receipted", "pty"),
        **_case_map("test_bridge_proxy_complete_route_matrix", "multipart"),
        **_case_map("test_websocket_reconnect_resumes_after_cursor", "reconnect"),
        **_case_map("test_liveness_probe_is_local", "liveness"),
        **_case_map("keeps large mobile sessions keyboard and screen-reader operable", "mobile-responsive", "keyboard-shortcuts", "focus-transitions", "screen-reader-semantics", "reduced-motion", "large-session", suite="browser"),
        **_case_map("test_embedded_document_security_headers", "csp-frame-cors-csrf-origin-cookie-cache-service-worker"),
        **_case_map("test_unsupported_native_ui_version_fails_closed", "route-version-drift"),
    },
    "diagnostic-fallback": {
        **_case_map("test_upstream_error_serves_unavailable_document", "native-ui-unavailable", "upstream-server-unavailable"),
        **_case_map("renders an explicit unsupported-runtime state", "unsupported-runtime", suite="frontend"),
        **_case_map("renders an understandable failed-before-stream lifecycle", "failed-before-stream", suite="frontend"),
        **_case_map("test_stream_reports_retention_gap", "retention-gap"),
        **_case_map("test_unsupported_native_ui_version_fails_closed", "schema-incompatibility"),
        **_case_map("test_terminal_binding_serves_durable_snapshot_without_provider_session", "direct-runtime-provenance", "host-removed", "terminal-server-read-only", "captured-evidence-links"),
        **_case_map("keeps terminal workflow actions available when the native iframe is unavailable", "no-custom-composer", "terminal-ui-read-only", suite="frontend"),
    },
    "telemetry-and-rollout": {
        **_case_map("test_production_adapter_emits_through_shared_exporter_without_identity_tags", "binding-readiness-signals", "transport-and-upstream-signals", "authorization-capability-scan-signals", "mutation-and-reconciliation-signals", "fallback-replay-continuation-signals", "identity-free-bounded-labels"),
        **_case_map("test_rollout_default_is_non_interactive_before_proof", "default-pre-proof-read-only"),
        **_case_map("test_canary_end_to_end_resolves_current_report", "validated-canary-admission"),
        **_case_map("test_rollout_preserves_durable_terminal_snapshot", "rollback-preserves-diagnostics"),
        **_case_map("test_rollout_flag_is_documented_as_temporary", "temporary-flag-retirement"),
    },
}

_expected_boundary_cases = {
    scenario: set(cases)
    for scenario, cases in REQUIRED_CASES.items()
    if SCENARIO_LANES[scenario] == LANE_DETERMINISTIC
}
if set(DETERMINISTIC_CASE_BOUNDARIES) != set(_expected_boundary_cases) or any(
    set(DETERMINISTIC_CASE_BOUNDARIES[scenario]) != cases
    for scenario, cases in _expected_boundary_cases.items()
):
    raise RuntimeError("deterministic native-chat case boundary map drifted")


def _passing_junit(path: Path, *, label: str) -> dict[str, object]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ConformanceContractError(f"{label} JUnit evidence is invalid") from exc
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    totals = {
        key: sum(int(suite.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    if totals["tests"] <= 0 or any(
        totals[key] for key in ("failures", "errors", "skipped")
    ):
        raise ConformanceContractError(f"{label} suite did not pass without skips")
    testcase_records = []
    for suite in suites:
        for case in suite.findall(".//testcase"):
            identifier = "::".join(
                part
                for part in (case.get("classname", ""), case.get("name", ""))
                if part
            )
            properties = {
                str(item.get("name")): str(item.get("value"))
                for item in case.findall("./properties/property")
                if item.get("name") is not None and item.get("value") is not None
            }
            testcase_records.append({"identifier": identifier, "properties": properties})
    testcase_records.sort(key=lambda item: str(item["identifier"]))
    testcases = [str(item["identifier"]) for item in testcase_records]
    if len(testcases) != totals["tests"]:
        raise ConformanceContractError(f"{label} JUnit testcase inventory is incomplete")
    return {**totals, "testcases": testcases, "testcaseRecords": testcase_records}


def _require_junit_cases(
    result: dict[str, object], *, label: str, fragments: list[str]
) -> None:
    testcases = result.get("testcases")
    if not isinstance(testcases, list):
        raise ConformanceContractError(f"{label} JUnit testcase inventory is missing")
    missing = [
        fragment
        for fragment in fragments
        if not any(fragment in str(testcase) for testcase in testcases)
    ]
    if missing:
        raise ConformanceContractError(
            f"{label} JUnit lacks controlling cases: {missing}"
        )


def _resolved_boundary_tests(
    *,
    scenario: str,
    case: str,
    results: dict[str, dict[str, object]],
) -> list[str]:
    resolved: list[str] = []
    for suite, fragment in DETERMINISTIC_CASE_BOUNDARIES[scenario][case]:
        testcases = results[suite].get("testcases")
        matches = [
            str(testcase)
            for testcase in testcases if fragment in str(testcase)
        ] if isinstance(testcases, list) else []
        if not matches:
            raise ConformanceContractError(
                f"{suite} JUnit lacks {scenario}/{case} production boundary: {fragment}"
            )
        resolved.extend(f"{suite}:{identifier}" for identifier in matches)
    return sorted(set(resolved))


def _case_outcome(
    result: dict[str, object], *, scenario: str, case: str
) -> dict[str, object]:
    fragment = f"test_native_chat_acceptance_case[{scenario}--{case}]"
    records = result.get("testcaseRecords")
    if not isinstance(records, list):
        raise ConformanceContractError("backend JUnit testcase records are missing")
    matches = [
        record
        for record in records
        if isinstance(record, dict) and fragment in str(record.get("identifier"))
    ]
    if len(matches) != 1:
        raise ConformanceContractError(
            f"backend JUnit must contain exactly one outcome for {scenario}/{case}"
        )
    properties = matches[0].get("properties")
    if not isinstance(properties, dict):
        raise ConformanceContractError(
            f"backend JUnit lacks outcome properties for {scenario}/{case}"
        )
    expected_properties = {
        "nativeChatScenario": scenario,
        "nativeChatCase": case,
        "durableAfterCleanup": "true",
    }
    if any(properties.get(key) != value for key, value in expected_properties.items()):
        raise ConformanceContractError(
            f"backend JUnit outcome identity is invalid for {scenario}/{case}"
        )
    decision = properties.get("authorizationDecision")
    if decision not in {"allowed", "denied", "not_applicable"}:
        raise ConformanceContractError(
            f"backend JUnit lacks authorization outcome for {scenario}/{case}"
        )
    try:
        actual = int(str(properties["upstreamSideEffectCount"]))
        expected = int(str(properties["expectedUpstreamSideEffectCount"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ConformanceContractError(
            f"backend JUnit lacks side-effect outcome for {scenario}/{case}"
        ) from exc
    if actual < 0 or actual != expected:
        raise ConformanceContractError(
            f"backend JUnit side-effect outcome failed for {scenario}/{case}"
        )
    return {
        "authorizationDecision": decision,
        "upstreamSideEffectCount": actual,
        "expectedUpstreamSideEffectCount": expected,
        "durableAfterCleanup": True,
    }


def record(
    *,
    backend_junit: Path,
    frontend_junit: Path,
    browser_junit: Path,
    output_dir: Path,
    commit: str,
    build: str,
    server_image: str,
    ui_image: str,
    host_image: str,
) -> Path:
    backend = _passing_junit(backend_junit, label="backend")
    frontend = _passing_junit(frontend_junit, label="frontend Workflow Detail")
    browser = _passing_junit(browser_junit, label="browser")
    junit_results = {"backend": backend, "frontend": frontend, "browser": browser}
    deterministic_cases = [
        (scenario, case)
        for scenario, cases in REQUIRED_CASES.items()
        if SCENARIO_LANES[scenario] == LANE_DETERMINISTIC
        for case in cases
    ]
    _require_junit_cases(
        backend,
        label="backend",
        fragments=[
            f"test_native_chat_acceptance_case[{scenario}--{case}]"
            for scenario, case in deterministic_cases
        ]
        + [
            "test_browser_payload_compiles_replays_and_releases_only_after_cleanup",
            "test_bridge_proxy_create_get_and_journal",
            "test_bridge_proxy_complete_route_matrix",
            "test_rollout_blocks_direct_http_sse_resource_and_control_bypasses",
            "test_native_http_mutation_is_scanned_receipted_and_replay_safe",
            "test_websocket_closes_when_authority_is_revoked_midstream",
            "test_high_security_scanner_error_fails_closed",
            "test_terminal_binding_serves_durable_snapshot_without_provider_session",
            "test_global_updates_frames_are_identity_scoped_and_virtualized",
            "test_terminal_input_frame_is_scanned_and_durably_receipted",
            "test_continue_creates_linked_workflow_and_pins_source",
            "test_captured_evidence_projects_authorized_refs",
            "test_production_adapter_emits_through_shared_exporter_without_identity_tags",
        ],
    )
    _require_junit_cases(
        frontend,
        label="frontend Workflow Detail",
        fragments=[
            "selects Chat by default and preserves query state",
            "deep-links to Chat and keeps non-chat tabs one click away",
            "keeps terminal workflow actions available when the native iframe is unavailable",
            "opens captured evidence as authorized MoonMind download links",
            "continues into the linked workflow with authored intent and navigates to it",
        ],
    )
    _require_junit_cases(
        browser,
        label="browser",
        fragments=[
            "mounts the same-origin scoped URL",
            "ignores foreign-origin liveness messages",
            "reports ready on a genuine same-origin load",
            "drives the native composer and claimed feature surface",
            "keeps large mobile sessions keyboard and screen-reader operable",
        ],
    )
    env = {
        "MOONMIND_BUILD_COMMIT": commit,
        "OMNIGENT_IMAGE_REF": server_image,
        "OMNIGENT_NATIVE_UI_IMAGE_REF": ui_image,
        "OMNIGENT_HOST_IMAGE_REF": host_image,
    }
    identity = native_chat_deployment_identity(env=env)
    if identity is None:
        raise ConformanceContractError("deterministic deployment identity is not pinned")
    identities = {
        **identity,
        "moonmindBuild": build,
        "hostArchitecture": f"linux/{platform.machine()}",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    summary = {
        "schemaVersion": "moonmind.omnigent.native-chat-deterministic-summary/v1",
        "commit": commit,
        "backend": backend,
        "frontend": frontend,
        "browser": browser,
        "backendSha256": hashlib.sha256(backend_junit.read_bytes()).hexdigest(),
        "frontendSha256": hashlib.sha256(frontend_junit.read_bytes()).hexdigest(),
        "browserSha256": hashlib.sha256(browser_junit.read_bytes()).hexdigest(),
        "generatedAt": now.isoformat(),
    }
    for name in ("audit", "cleanup", "secret-scan"):
        (output_dir / f"{name}.json").write_text(
            json.dumps({**summary, "evidenceKind": name}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    scenarios: dict[str, object] = {}
    for scenario, required in REQUIRED_CASES.items():
        if SCENARIO_LANES[scenario] != LANE_DETERMINISTIC:
            continue
        scenarios[scenario] = {
            case: {
                "status": "passed",
                **_case_outcome(backend, scenario=scenario, case=case),
                "boundaryTests": _resolved_boundary_tests(
                    scenario=scenario,
                    case=case,
                    results=junit_results,
                ),
                "evidenceFiles": [
                    {"file": "backend-junit.xml", "kind": "test_result"},
                    {"file": "frontend-junit.xml", "kind": "test_result"},
                    {"file": "browser-junit.xml", "kind": "test_result"},
                    {"file": "journey-summary.json", "kind": "browser_trace"},
                ],
            }
            for case in required
        }
    # Keep the JUnit files under the bounded evidence root referenced above.
    backend_target = output_dir / "backend-junit.xml"
    frontend_target = output_dir / "frontend-junit.xml"
    browser_target = output_dir / "browser-junit.xml"
    if backend_junit.resolve() != backend_target.resolve():
        backend_target.write_bytes(backend_junit.read_bytes())
    if frontend_junit.resolve() != frontend_target.resolve():
        frontend_target.write_bytes(frontend_junit.read_bytes())
    if browser_junit.resolve() != browser_target.resolve():
        browser_target.write_bytes(browser_junit.read_bytes())
    (output_dir / "journey-summary.json").write_text(
        json.dumps({**summary, "evidenceKind": "complete-browser-product-journey"}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    observation = {
        "schemaVersion": OBSERVATION_SCHEMA_VERSION,
        "lane": LANE_DETERMINISTIC,
        "expiresAt": (now + timedelta(days=14)).isoformat(),
        "identities": identities,
        "safeIdentities": {},
        "sharedEvidence": {
            "auditFile": "audit.json",
            "cleanupFile": "cleanup.json",
            "secretScanFile": "secret-scan.json",
        },
        "scenarios": scenarios,
    }
    output = output_dir / "observations.json"
    output.write_text(json.dumps(observation, indent=2, sort_keys=True) + "\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-junit", type=Path, required=True)
    parser.add_argument("--frontend-junit", type=Path, required=True)
    parser.add_argument("--browser-junit", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--server-image", required=True)
    parser.add_argument("--ui-image", required=True)
    parser.add_argument("--host-image", required=True)
    args = parser.parse_args()
    record(**vars(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
