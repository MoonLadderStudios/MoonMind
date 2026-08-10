#!/usr/bin/env python3
"""Produce the complete #3642 deterministic + protected-live evidence matrix.

The repository owns the action ordering and all pass/fail conclusions.  A
deployment adapter named by ``MOONMIND_NATIVE_CHAT_ACTION_COMMAND`` performs
each protected action through the normal MoonMind product API and returns one
observed JSON object.  It cannot submit a pre-computed acceptance matrix.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.conformance import (  # noqa: E402
    ConformanceContractError,
    assert_secret_free,
    require_pinned_images,
)
from moonmind.omnigent.native_chat_acceptance import (  # noqa: E402
    CASE_EVIDENCE_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    REQUIRED_SCENARIOS,
    SCENARIO_LANES,
)
from moonmind.omnigent.native_chat_telemetry import (  # noqa: E402
    NATIVE_CHAT_TELEMETRY_VERSION as TELEMETRY_VERSION,
)
from moonmind.omnigent.native_outbound_scan import (  # noqa: E402
    NATIVE_OUTBOUND_SCAN_CONTRACT_VERSION as SCAN_VERSION,
)
from moonmind.omnigent.native_ui import (  # noqa: E402
    NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION,
    NATIVE_UI_ROUTE_FEATURE_VERSION,
)

_DIGEST = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_PROTECTED_ACTIONS = (
    "create_workflow",
    "open_workflow_detail_chat",
    "send_native_message",
    "exercise_native_surfaces",
    "resolve_approval",
    "transition_terminal_and_harvest",
    "cleanup_and_release_leases",
    "reload_terminal_replay",
    "create_linked_continuation",
)
_REQUIRED_RESULT_KEYS = {
    "create_workflow": ("workflowRef", "runRef", "stepRef", "agentRunRef"),
    "open_workflow_detail_chat": ("bindingRef", "profileRef", "providerProfileRef"),
    "send_native_message": ("mutationAccepted",),
    "exercise_native_surfaces": ("transcript", "tools", "resources"),
    "resolve_approval": ("approvalResolved",),
    "transition_terminal_and_harvest": ("terminalReadOnly", "mutationEvidenceRef"),
    "cleanup_and_release_leases": ("leasesReleased", "liveResourcesRemoved"),
    "reload_terminal_replay": ("historicalEvidencePreserved", "evidenceRefsResolved"),
    "create_linked_continuation": ("continuationCreated", "sourceUnmodified"),
}


def _run(command: list[str], *, env: dict[str, str], label: str) -> dict[str, Any]:
    result = subprocess.run(
        command, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise ConformanceContractError(f"{label} failed: {result.stderr[-800:]}")
    assert_secret_free(result.stdout)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ConformanceContractError(f"{label} returned invalid JSON") from exc
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise ConformanceContractError(f"{label} did not report observed success")
    return value


def _run_protected_actions(env: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    configured = env.get("MOONMIND_NATIVE_CHAT_ACTION_COMMAND", "").strip()
    if not configured:
        raise ConformanceContractError(
            "MOONMIND_NATIVE_CHAT_ACTION_COMMAND must name the protected product adapter"
        )
    state: dict[str, Any] = {}
    refs: list[str] = []
    for action in _PROTECTED_ACTIONS:
        result = _run(
            [*shlex.split(configured), action, json.dumps(state, separators=(",", ":"))],
            env=env,
            label=f"protected-live/{action}",
        )
        observation = result.get("observation")
        if not isinstance(observation, dict) or any(
            observation.get(key) in (None, False, "") for key in _REQUIRED_RESULT_KEYS[action]
        ):
            raise ConformanceContractError(
                f"protected-live/{action} lacks its required observed result"
            )
        evidence = result.get("evidenceRefs")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(ref, str) and ref.startswith("artifact://") for ref in evidence
        ):
            raise ConformanceContractError(
                f"protected-live/{action} lacks durable artifact evidence"
            )
        state.update(observation)
        refs.extend(evidence)
    return state, list(dict.fromkeys(refs))


def _run_deterministic_commands(env: dict[str, str]) -> list[str]:
    commands = (
        [
            "moonmind", "container", "python-tests",
            "tests/integration/reliability_journey/test_native_chat_acceptance_journey.py",
        ],
        [
            "npm", "run", "ui:test", "--",
            "--config", "frontend/vitest.browser.config.ts",
            "frontend/src/browser/workflowNativeChat.browser.test.tsx",
        ],
    )
    refs: list[str] = []
    for index, command in enumerate(commands, 1):
        result = subprocess.run(command, cwd=REPO_ROOT, env=env, check=False)
        if result.returncode:
            raise ConformanceContractError(
                f"deterministic acceptance command {index} failed"
            )
        refs.append(f"artifact://channels/deterministic-command-{index}")
    return refs


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    assert_secret_free(encoded)
    path.write_text(encoded, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-image", required=True)
    parser.add_argument("--ui-image", required=True)
    parser.add_argument("--host-image", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    images = {
        "server": args.server_image,
        "ui": args.ui_image,
        "host": args.host_image,
    }
    require_pinned_images(images)
    if any(not _DIGEST.fullmatch(value) for value in images.values()):
        raise ConformanceContractError("all native-chat images must be immutable digests")

    env = dict(os.environ)
    env.update({
        "OMNIGENT_IMAGE_REF": args.server_image,
        "OMNIGENT_NATIVE_UI_IMAGE_REF": args.ui_image,
        "OMNIGENT_HOST_IMAGE_REF": args.host_image,
        "OMNIGENT_HOST_IMAGE": args.host_image,
        "OMNIGENT_HOST_IMAGE_TAG": "",
    })
    deterministic_refs = _run_deterministic_commands(env)
    state, protected_refs = _run_protected_actions(env)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=7)
    manifest = state.get("compatibilityManifest")
    if not isinstance(manifest, dict):
        raise ConformanceContractError("protected journey lacks a compatibility manifest")
    manifest_digest = "sha256:" + hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    identities = {
        "moonmindCommit": args.commit,
        "moonmindBuild": args.build,
        "hostArchitecture": platform.machine(),
        "contractVersions": {
            "nativeUiBootstrap": NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION,
            "nativeUiRouteFeature": NATIVE_UI_ROUTE_FEATURE_VERSION,
            "outboundScan": SCAN_VERSION,
            "telemetry": TELEMETRY_VERSION,
        },
        "images": images,
        "compatibilityManifestDigest": manifest_digest,
    }
    safe = {key: state.get(key) for key in (
        "workflowRef", "runRef", "stepRef", "agentRunRef", "bindingRef"
    )}
    profiles = {key: state.get(key) for key in (
        "profileRef", "launchPolicyRef", "effectiveLaunchSnapshotRef", "providerProfileRef"
    )}
    if any(not isinstance(value, str) or not value for value in (*safe.values(), *profiles.values())):
        raise ConformanceContractError("protected journey lacks safe identity/profile refs")

    evidence_root = args.output_root / "evidence"
    scenarios: dict[str, Any] = {}
    common = {
        "status": "passed", "identities": identities,
        "generatedAt": now.isoformat(), "expiresAt": expires.isoformat(),
        "revokedAt": None, "supersededBy": None,
        "producer": "github:omnigent-native-chat-acceptance",
        "secretScan": "passed", "cleanup": "passed",
    }
    for name in REQUIRED_SCENARIOS:
        lane = SCENARIO_LANES[name]
        ref = f"artifact://scenarios/{name}"
        case_ref = f"artifact://cases/{name}"
        channels = deterministic_refs if lane == "deterministic" else protected_refs
        _write_json(evidence_root / "scenarios" / f"{name}.json", {
            "schemaVersion": EVIDENCE_SCHEMA_VERSION,
            "claim": f"scenario:{name}", **common,
            "evidenceRefs": channels,
            "cases": {"complete-product-journey": {
                "status": "passed", "evidenceRefs": [case_ref]
            }},
        })
        _write_json(evidence_root / "cases" / f"{name}.json", {
            "schemaVersion": CASE_EVIDENCE_SCHEMA_VERSION,
            "claim": f"scenario:{name}", "case": "complete-product-journey",
            **common, "evidenceRefs": channels,
        })
        scenarios[name] = {"status": "passed", "lane": lane, "evidenceRefs": [ref]}

    cleanup_ref = "artifact://cleanup/final"
    _write_json(evidence_root / "cleanup" / "final.json", {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION, "claim": "cleanup", **common,
        "evidenceRefs": protected_refs,
        "cases": {"post-cleanup-replay": {
            "status": "passed", "evidenceRefs": ["artifact://cases/cleanup"]
        }},
    })
    _write_json(evidence_root / "cases" / "cleanup.json", {
        "schemaVersion": CASE_EVIDENCE_SCHEMA_VERSION, "claim": "cleanup",
        "case": "post-cleanup-replay", **common, "evidenceRefs": protected_refs,
    })
    _write_json(args.output_root / "native-chat-acceptance-input.json", {
        "producer": "github:omnigent-native-chat-acceptance",
        "expiresAt": expires.isoformat(), "supersedes": None,
        "identities": identities, "safeIdentities": safe,
        "profilePolicyRefs": profiles, "scenarios": scenarios,
        "cleanup": {"status": "passed", "evidenceRefs": [cleanup_ref],
                    "historicalEvidencePreserved": True, "leasesReleased": True},
        "secretScan": {"status": "passed"},
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
