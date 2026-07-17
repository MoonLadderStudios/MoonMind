#!/usr/bin/env python3
"""Run an Omnigent conformance layer and emit bounded JSON evidence.

Credentialed modes intentionally accept only service URLs and secret *presence*
through the environment. Secrets are never copied to subprocess arguments or
the report. Source: MoonLadderStudios/MoonMind#3368.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from moonmind.omnigent.conformance import build_report, load_profile

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "tests/fixtures/omnigent/conformance-profile-v1.json"
IMAGES = ROOT / "tests/fixtures/omnigent/stock-images-v1.json"

_MODES = {"deterministic", "stock-proxy", "static-compose", "on-demand"}

_DETERMINISTIC_CASES = {
    "configuration": ["tests/unit/omnigent/test_settings.py"],
    "proxy-routes": ["tests/unit/api/routers/test_omnigent_bridge.py"],
    "first-message-exactly-once": ["tests/integration/omnigent/test_bridge_conformance.py", "-k", "scenario_04 or scenario_05"],
    "event-replay-sse": ["tests/integration/omnigent/test_bridge_conformance.py", "-k", "scenario_01 or scenario_03"],
    "terminal-fallback": ["tests/integration/omnigent/test_bridge_conformance.py", "-k", "scenario_02 or scenario_03"],
    "resources": ["tests/integration/omnigent/test_bridge_conformance.py", "-k", "scenario_07"],
    "failure-lifecycle": ["tests/integration/omnigent/test_bridge_conformance.py", "-k", "scenario_02 or scenario_09"],
    "cleanup-and-lease-release": ["tests/unit/omnigent/test_oauth_profile_lifecycle.py"],
    "credential-redaction": ["tests/unit/omnigent/test_conformance_contract.py", "-k", "secret"],
}

_FRONTEND_TARGETS = (
    "frontend/src/lib/chatSession.test.ts",
    "frontend/src/entrypoints/workflow-detail.test.tsx",
)


def _preflight(mode: str) -> None:
    if mode == "deterministic":
        return
    required = {
        "OMNIGENT_ENABLED",
        "OMNIGENT_SERVER_URL",
        "OMNIGENT_API_TOKEN",
        "OMNIGENT_DEFAULT_AGENT_NAME",
    }
    if mode in {"static-compose", "on-demand"}:
        required.add("OMNIGENT_CODEX_OAUTH_PROFILE_REF")
    missing = sorted(name for name in required if not os.environ.get(name, "").strip())
    if missing:
        raise SystemExit("missing provisioned conformance inputs: " + ", ".join(missing))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=sorted(_MODES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _preflight(args.mode)

    profile = load_profile(PROFILE)
    images: list[dict[str, str]] = []
    if args.mode != "deterministic":
        images = json.loads(IMAGES.read_text(encoding="utf-8"))["images"]
    results: list[dict[str, Any]] = []
    returncode = 0
    for case in profile["cases"]:
        case_id = case["id"]
        if args.mode == "deterministic":
            if case_id in {"workflow-detail-chat", "direct-codex-parity"}:
                command = ["npm", "run", "ui:test", "--", *_FRONTEND_TARGETS]
            else:
                command = [sys.executable, "-m", "pytest", *_DETERMINISTIC_CASES[case_id], "-q", "--tb=short"]
        else:
            command = [sys.executable, "-m", "pytest", "tests/provider/omnigent/test_omnigent_smoke.py", "-m", "provider_verification", "-k", f"{args.mode.replace('-', '_')} and {case_id.replace('-', '_')}", "-q", "--tb=short"]
        completed = subprocess.run(command, cwd=ROOT, check=False)
        status = "passed" if completed.returncode == 0 else "failed"
        returncode = returncode or completed.returncode
        results.append({"caseId": case_id, "status": status, "evidence": "command:" + " ".join(command)})
    report = build_report(
        profile=profile,
        mode=args.mode,
        images=images,
        results=results,
        evidence_refs=[f"artifact://omnigent-conformance/{args.mode}.json"],
        runtime={
            "protocolProfile": profile["profile"] + "/" + profile["schemaVersion"],
            "architecture": os.environ.get("OMNIGENT_HOST_ARCHITECTURE", "local" if args.mode == "deterministic" else "unreported"),
            "authMode": "none" if args.mode == "deterministic" else os.environ.get("OMNIGENT_AUTH_MODE", "token"),
            "capabilitiesRef": os.environ.get("OMNIGENT_CAPABILITIES_REF", ""),
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
