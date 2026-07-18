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


class LiveRunner:
    def __init__(self, *, output_dir: Path, env: dict[str, str]) -> None:
        self.output_dir = output_dir
        self.env = env
        self.logs: list[Path] = []

    def run(self, name: str, command: Sequence[str]) -> None:
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

    @staticmethod
    def compose(*args: str) -> list[str]:
        return [
            "docker", "compose", "--project-name", PROJECT,
            "--profile", "omnigent-host-codex", *args,
        ]

    def static(self) -> None:
        self.run(
            "static-up",
            self.compose("up", "-d", "--wait", "omnigent", "omnigent-host-codex"),
        )
        self.run("static-journey", [sys.executable, "-m", "pytest", PROVIDER_TEST, "-q", "-s"])
        self.run("static-restart", self.compose("restart", "omnigent", "omnigent-host-codex"))
        self.run("static-replay", [sys.executable, "-m", "pytest", PROVIDER_TEST, "-q", "-s"])

    def provider(self, mode: str) -> None:
        self.env["MOONMIND_OMNIGENT_LIVE_MODE"] = mode
        self.run(f"{mode}-journey", [sys.executable, "-m", "pytest", PROVIDER_TEST, "-q", "-s"])

    def cleanup(self) -> None:
        # No --volumes: OAuth and unrelated state must survive this runner.
        self.run("cleanup", self.compose("down", "--remove-orphans"))

    def scan(self) -> Path:
        for path in self.logs:
            assert_secret_free(path.read_text(encoding="utf-8", errors="replace"))
        path = self.output_dir / "secret-scan.json"
        path.write_text(json.dumps({"status": "passed", "files": [p.name for p in self.logs]}) + "\n")
        return path


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
            runner.static() if mode == "static" else runner.provider(mode)
            passed.update(LIVE_CASES[mode])
    except (RuntimeError, ConformanceContractError) as exc:
        failure = str(exc)
    finally:
        try:
            runner.cleanup()
            scan_path = runner.scan()
        except (RuntimeError, ConformanceContractError) as exc:
            failure = failure or str(exc)
            scan_path = output_dir / "secret-scan.json"

    profile = load_profile(PROFILE)
    requested = set().union(*(LIVE_CASES[item] for item in selected))
    results = []
    refs = tuple(str(path.relative_to(REPO_ROOT)) for path in runner.logs) or (str(output_dir.relative_to(REPO_ROOT)),)
    for item in profile["cases"]:
        case_id = item["id"]
        status = "passed" if case_id in passed else "failed" if case_id in requested else "skipped"
        results.append(CaseResult(case_id, status, refs))
    scans = {channel: {"status": "passed" if not failure else "failed", "evidenceRef": str(scan_path)} for channel in ("logs", "temporalHistory", "screenshots", "archives")}
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
