#!/usr/bin/env python3
"""Run Tactics build/tests through MoonMind's shared container-job backend."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument("--phase", choices=("all", "build", "test"), default="all")
    parser.add_argument("--target", default="TacticsEditor")
    parser.add_argument("--build-platform", default="Linux")
    parser.add_argument("--configuration", default="Development")
    parser.add_argument("--uproject", default="Tactics.uproject")
    parser.add_argument(
        "--test-filter", default="Tactics.Unit.PlayerReadyNotification"
    )
    parser.add_argument(
        "--results-subdir", default=".artifacts/moonmind-unreal-tactics"
    )
    parser.add_argument("--gate-file")
    parser.add_argument("--dry-run", action="store_true")
    # Direct-Docker-only options remain accepted so one command works in both
    # substrates; they are intentionally ignored inside MoonMind.
    parser.add_argument("--image")
    parser.add_argument("--platform")
    parser.add_argument("--ccache-dir")
    parser.add_argument("--ubt-dir")
    parser.add_argument("--workspace-volume")
    parser.add_argument("--workspace-target")
    parser.add_argument("--ccache-volume")
    parser.add_argument("--ubt-volume")
    parser.add_argument("--pull")
    return parser


def _relative_path(value: str, *, field: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must be a normalized repository-relative path")
    return path


def _environment(values: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": key, "value": value} for key, value in values.items()]


def _base_spec(*, timeout_seconds: int) -> dict[str, Any]:
    return {
        "imageSourceRef": "tactics-unreal",
        "workdir": "/workspace",
        "networkMode": "none",
        "caches": [
            {
                "cacheRef": "unreal-ccache",
                "target": "/home/ue4/.ccache",
                "readOnly": False,
            },
            {
                "cacheRef": "unreal-ubt",
                "target": "/home/ue4/.config/Epic/UnrealBuildTool",
                "readOnly": False,
            },
        ],
        "resources": {"cpuMillis": 8000, "memoryMiB": 16384, "pids": 2048},
        "timeoutSeconds": timeout_seconds,
    }


def _run_job(
    spec: dict[str, Any], *, request_id: str, log_path: Path, dry_run: bool
) -> int:
    with tempfile.TemporaryDirectory(prefix="moonmind-tactics-") as temp_dir:
        spec_path = Path(temp_dir) / "job.json"
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        command = [
            "moonmind",
            "container",
            "run",
            "--spec",
            str(spec_path),
            "--request-id",
            request_id,
        ]
        if dry_run:
            print(json.dumps({"command": command, "spec": spec}, indent=2))
            return 0
        with log_path.open("w", encoding="utf-8") as handle:
            process = subprocess.run(
                command,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            handle.write(process.stdout)
            print(process.stdout, end="")
            return process.returncode


def main() -> int:
    args = _parser().parse_args()
    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        raise ValueError(f"repository does not exist: {repo}")
    uproject = _relative_path(args.uproject, field="--uproject")
    if not (repo / uproject).is_file():
        raise ValueError(f"uproject does not exist: {repo / uproject}")
    results_subdir = _relative_path(args.results_subdir, field="--results-subdir")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results_dir = repo / results_subdir / timestamp
    results_dir.mkdir(parents=True, exist_ok=True)
    gate_path = (
        repo / _relative_path(args.gate_file, field="--gate-file")
        if args.gate_file
        else repo / results_subdir / "latest" / "gate.json"
    )
    gate_path.parent.mkdir(parents=True, exist_ok=True)

    build_status = "not_run"
    test_status = "not_run"
    reason = "Requested phases completed successfully"
    exit_code = 0

    if args.phase in {"all", "build"}:
        build_spec = _base_spec(timeout_seconds=14400)
        build_spec.update(
            {
                "command": [
                    "bash",
                    "-lc",
                    (
                        "set -o pipefail; "
                        "if test -w /home/ue4/.ccache; then "
                        "export UE_CCACHE=1 CCACHE_DIR=/home/ue4/.ccache; "
                        "else echo '[WARN] ccache volume is not writable; "
                        "continuing without ccache'; fi; "
                        "/home/ue4/UnrealEngine/Engine/Build/BatchFiles/Linux/Build.sh "
                        '"$MM_TARGET" "$MM_PLATFORM" "$MM_CONFIGURATION" '
                        '"-project=/workspace/$MM_UPROJECT" -NoHotReload'
                    ),
                ],
                "environment": _environment(
                    {
                        "MM_TARGET": args.target,
                        "MM_PLATFORM": args.build_platform,
                        "MM_CONFIGURATION": args.configuration,
                        "MM_UPROJECT": uproject.as_posix(),
                    }
                ),
            }
        )
        build_log = results_dir / "build.log"
        exit_code = _run_job(
            build_spec,
            request_id=f"tactics-build-{timestamp}",
            log_path=build_log,
            dry_run=args.dry_run,
        )
        build_status = "pass" if exit_code == 0 else "fail"
        if exit_code:
            reason = "Build phase failed"

    if exit_code == 0 and args.phase in {"all", "test"}:
        test_spec = _base_spec(timeout_seconds=7200)
        test_spec.update(
            {
                "command": [
                    "/home/ue4/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd",
                    f"/workspace/{uproject.as_posix()}",
                    "-nullrhi",
                    "-unattended",
                    "-nop4",
                    "-nosplash",
                    "-NoSound",
                    "-nohmd",
                    "-nopause",
                    "-log",
                    f"-ExecCmds=Automation RunTests {args.test_filter}; Quit",
                ]
            }
        )
        test_log = results_dir / "test.log"
        exit_code = _run_job(
            test_spec,
            request_id=f"tactics-test-{timestamp}",
            log_path=test_log,
            dry_run=args.dry_run,
        )
        test_status = "pass" if exit_code == 0 else "fail"
        if exit_code:
            reason = "Test phase failed"

    gate = {
        "status": "SKIPPED" if args.dry_run else ("PASS" if exit_code == 0 else "FAIL"),
        "reason": "Dry-run mode; jobs not submitted" if args.dry_run else reason,
        "timestamp": datetime.now(UTC).isoformat(),
        "source": "moonmind-container-job",
        "repo": str(repo),
        "phase": args.phase,
        "buildStatus": "skipped" if args.dry_run else build_status,
        "testStatus": "skipped" if args.dry_run else test_status,
        "resultsDir": str(results_dir),
    }
    gate_path.write_text(json.dumps(gate, indent=2) + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
