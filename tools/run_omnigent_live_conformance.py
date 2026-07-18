#!/usr/bin/env python3
"""Run credentialed Omnigent conformance journeys for #3368.

The runner owns only an isolated Compose project.  In particular, it never
removes volumes: the enrolled Codex OAuth volume is operator-owned evidence,
not disposable test state.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
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

PROFILE = REPO_ROOT / "tests/fixtures/omnigent/conformance-v1.json"
PROJECT = "moonmind-test-omnigent-live"
PROVIDER_TEST = "tests/provider/omnigent/test_omnigent_smoke.py"
LIVE_CASES = {
    "stock": {"stock-images.proxy"},
    "static": {"compose.static-codex-oauth"},
    "ondemand": {"ondemand.codex-oauth", "cleanup.lease-owned-only"},
    "failures": {"failures.lifecycle-and-redaction"},
}
SCENARIOS = {
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
        root = ET.parse(junit).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        totals = {key: sum(int(s.get(key, "0")) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
        if totals["tests"] != 1 or any(totals[key] for key in ("failures", "errors", "skipped")):
            raise RuntimeError(f"{mode} scenario was not one unskipped passing test: {totals}")

    def static(self) -> None:
        self.run(
            "static-up",
            self.compose("up", "-d", "--wait", "omnigent", "omnigent-host-codex"),
        )
        self.scenario("static", phase="execute")
        self.run("static-restart", self.compose("restart", "omnigent", "omnigent-host-codex"))
        # Reload the persisted identifiers and assert the same workflow after
        # restart.  This is deliberately a real second provider invocation,
        # never collection-only evidence.
        self.scenario("static", phase="replay")

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
    env.update({"OMNIGENT_IMAGE_REF": args.server_image, "OMNIGENT_HOST_IMAGE_REF": args.host_image})
    runner = LiveRunner(output_dir=output_dir, env=env)
    selected = tuple(LIVE_CASES) if args.mode == "all" else (args.mode,)
    passed: set[str] = set()
    failure: str | None = None
    try:
        for mode in selected:
            try:
                runner.static() if mode == "static" else runner.scenario(mode)
                passed.update(LIVE_CASES[mode])
            finally:
                runner.cleanup(mode)
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
    refs = tuple(str(path.relative_to(REPO_ROOT)) for path in runner.logs) or (str(output_dir.relative_to(REPO_ROOT)),)
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
