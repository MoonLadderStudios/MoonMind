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

_PYTEST_TARGETS = {
    "deterministic": [
        "tests/unit/omnigent",
        "tests/unit/api/routers/test_omnigent_bridge.py",
        "tests/integration/omnigent/test_bridge_conformance.py",
    ],
    "stock-proxy": ["tests/provider/omnigent/test_omnigent_smoke.py"],
    "static-compose": ["tests/provider/omnigent/test_omnigent_smoke.py"],
    "on-demand": [
        "tests/provider/omnigent/test_omnigent_smoke.py",
        "tests/unit/omnigent/test_oauth_profile_lifecycle.py",
    ],
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
    parser.add_argument("--mode", choices=sorted(_PYTEST_TARGETS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _preflight(args.mode)

    profile = load_profile(PROFILE)
    images: list[dict[str, str]] = []
    if args.mode != "deterministic":
        images = json.loads(IMAGES.read_text(encoding="utf-8"))["images"]
    commands = [[
        sys.executable,
        "-m",
        "pytest",
        *_PYTEST_TARGETS[args.mode],
        "-q",
        "--tb=short",
    ]]
    if args.mode != "deterministic":
        commands[0].extend(["-m", "provider_verification"])
    else:
        commands.append(
            ["npm", "run", "ui:test", "--", *_FRONTEND_TARGETS]
        )
    returncode = 0
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode:
            returncode = completed.returncode
            break
    status = "passed" if returncode == 0 else "failed"
    results: list[dict[str, Any]] = [
        {
            "caseId": case["id"],
            "status": status,
            "evidence": f"pytest:{args.mode}",
        }
        for case in profile["cases"]
    ]
    report = build_report(
        profile=profile,
        mode=args.mode,
        images=images,
        results=results,
        evidence_refs=[f"artifact://omnigent-conformance/{args.mode}.json"],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
