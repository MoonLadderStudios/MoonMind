#!/usr/bin/env python3
"""Run Tactics build/tests through MoonMind's shared container-job backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo")
    parser.add_argument(
        "--resume", help="Resume evidence collection from a prior results directory."
    )
    parser.add_argument("--phase", choices=("all", "build", "test"), default="all")
    parser.add_argument("--target", default="TacticsEditor")
    parser.add_argument("--build-platform", default="Linux")
    parser.add_argument("--configuration", default="Development")
    parser.add_argument("--uproject", default="Tactics.uproject")
    parser.add_argument("--test-filter", default="Tactics.Unit.PlayerReadyNotification")
    parser.add_argument(
        "--results-subdir", default=".artifacts/moonmind-unreal-tactics"
    )
    parser.add_argument("--gate-file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--image")
    # Cache/volume authority remains deployment-owned in managed sessions.
    parser.add_argument("--platform")
    parser.add_argument("--ccache-dir")
    parser.add_argument("--ubt-dir")
    parser.add_argument("--workspace-volume")
    parser.add_argument("--workspace-target")
    parser.add_argument("--ccache-volume")
    parser.add_argument("--ubt-volume")
    parser.add_argument("--pull", choices=("if-missing", "always", "never"))
    return parser


# Classify the complete current Unreal log; a bounded CLI tail may omit an
# earlier failure and is never enough evidence for a test verdict.
_NO_TESTS_PATTERN = re.compile(
    r"no automation tests matched|found 0 automation tests", re.IGNORECASE
)
_FAILED_TEST_PATTERN = re.compile(r"Test Completed\. Result=\{Fail", re.IGNORECASE)


def _test_log_outcome(log_path: Path) -> tuple[str, str]:
    """Classify a zero-exit test phase from its log; exit 0 alone is not success."""

    text = log_path.read_text(encoding="utf-8", errors="replace")
    if _FAILED_TEST_PATTERN.search(text):
        return "fail", "Automation test reported a failed result"
    if _NO_TESTS_PATTERN.search(text):
        return "no_tests", "No automation tests matched the test filter"
    if re.search(r"Test Completed\. Result=\{Success\}", text, re.IGNORECASE):
        return "pass", ""
    return "incomplete", "No completed automation test evidence is available"


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
) -> dict[str, Any]:
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
            "--json",
        ]
        if dry_run:
            print(json.dumps({"command": command, "spec": spec}, indent=2))
            return {}
        process = subprocess.run(command, check=False, text=True, capture_output=True)
        # Keep every observation, including transport errors, without replacing
        # the full Unreal log or evidence from an earlier observation.
        diagnostic = log_path.with_name(f"{log_path.stem}-{uuid4().hex}.cli.log")
        diagnostic.write_text(process.stdout + process.stderr, encoding="utf-8")
        if process.stderr:
            print(process.stderr, end="", file=sys.stderr)
        try:
            result = json.loads(process.stdout)
        except ValueError:
            return {}
        if not isinstance(result, dict):
            return {}
        # Unreal writes its complete log into the shared workspace. A bounded
        # CLI tail can hide an earlier failed test and must never replace it.
        if not any(arg.startswith("-abslog=") for arg in spec["command"]):
            log_path.write_text(
                "\n".join(result.get("log_tail") or []) + "\n", encoding="utf-8"
            )
        return result


def _workspace_root() -> Path:
    root = os.environ.get("MOONMIND_REPO_DIR") or os.environ.get(
        "MOONMIND_SESSION_WORKSPACE_PATH"
    )
    if not root and os.environ.get("MOONMIND_CONTAINER_JOBS_SOURCE_KIND") == "omnigent":
        # The Omnigent workspace attachment and generic host use this mount.
        root = "/workspaces/run"
    if not root:
        raise ValueError("Managed workspace root is missing from the session handoff")
    return Path(root).resolve()


def _source_state(repo: Path, results_dir: Path, gate_path: Path) -> str | None:
    """A source snapshot for evidence reuse only, never runtime compatibility."""
    generated = [results_dir, gate_path]
    # Other runs' diagnostics are not source changes. Recognize only our own
    # archived run directories, never the user-selected parent (which may
    # contain real source), and never follow an artifact-directory symlink.
    for directory in results_dir.parent.glob("*"):
        if directory.is_symlink() or not re.fullmatch(
            r"\d{8}T\d{6}Z-[0-9a-f]{32}", directory.name
        ):
            continue
        archive = directory / "gate.json"
        if archive.is_symlink():
            continue
        try:
            saved = json.loads(archive.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            isinstance(saved, dict)
            and saved.get("source") == "moonmind-container-job"
            and saved.get("runId") == directory.name.rsplit("-", 1)[-1]
            and saved.get("resultsDir") == str(directory)
        ):
            generated.append(directory)
    paths = [
        ".",
        *(f":(exclude){path.relative_to(repo).as_posix()}" for path in generated),
    ]

    def git(*args: str) -> bytes:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL
        )

    try:
        digest = hashlib.sha256(git("rev-parse", "HEAD"))
        digest.update(git("diff", "--binary", "HEAD", "--", "."))
        for name in git(
            "ls-files", "--others", "--exclude-standard", "-z", "--", *paths
        ).split(b"\0"):
            if name:
                digest.update(name + b"\0")
                path = repo / os.fsdecode(name)
                digest.update(str(path.lstat().st_mode).encode() + b"\0")
                content = (
                    os.fsencode(os.readlink(path))
                    if path.is_symlink()
                    else path.read_bytes()
                )
                digest.update(hashlib.sha256(content).digest())
        return digest.hexdigest()
    except (OSError, subprocess.CalledProcessError):
        return None


def _outcome(result: dict[str, Any], phase: str, log_path: Path) -> tuple[str, str]:
    if not result.get("job_id"):
        return "incomplete", "No authoritative container-job receipt is available"
    state = result.get("state")
    if state in {"failed", "canceled", "timed_out", "rejected"} or (
        state == "succeeded" and result.get("exit_code") not in {None, 0}
    ):
        detail = result.get("message") or result.get("failure_class") or state
        return "fail", f"{phase.title()} phase failed: {detail}"
    if state != "succeeded" or result.get("exit_code") != 0:
        return "incomplete", f"{phase.title()} phase has no confirmed successful exit"
    if not log_path.is_file():
        return "incomplete", f"{phase.title()} log evidence is unavailable"
    if phase == "test":
        return _test_log_outcome(log_path)
    return "pass", ""


def _specs(
    args: argparse.Namespace, workdir: str, uproject: Path, test_log: str
) -> dict[str, dict[str, Any]]:
    specs = {}
    for phase, timeout in (("build", 14400), ("test", 7200)):
        spec = _base_spec(timeout_seconds=timeout)
        spec["workdir"] = workdir
        if args.image:
            spec.pop("imageSourceRef")
            spec["image"] = args.image
            spec["pullPolicy"] = args.pull or "if-missing"
        elif args.pull not in {None, "if-missing"}:
            raise ValueError(
                "--pull requires --image; the provisioned image source "
                "owns its pull policy"
            )
        specs[phase] = spec
    specs["build"].update(
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
                    '"-project=$MM_PROJECT" -NoHotReload'
                ),
            ],
            "environment": _environment(
                {
                    "MM_TARGET": args.target,
                    "MM_PLATFORM": args.build_platform,
                    "MM_CONFIGURATION": args.configuration,
                    "MM_PROJECT": f"{workdir}/{uproject.as_posix()}",
                }
            ),
        }
    )
    specs["test"]["command"] = [
        "/home/ue4/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd",
        f"{workdir}/{uproject.as_posix()}",
        "-nullrhi",
        "-unattended",
        "-nop4",
        "-nosplash",
        "-NoSound",
        "-nohmd",
        "-nopause",
        "-log",
        f"-abslog={test_log}",
        f"-ExecCmds=Automation RunTests {args.test_filter}; Quit",
    ]
    specs["test"]["outputs"] = [
        {"name": "test-log", "relativePath": test_log.removeprefix("/workspace/")}
    ]
    return {
        phase: spec for phase, spec in specs.items() if args.phase in {"all", phase}
    }


def main() -> int:
    args = _parser().parse_args()
    root = _workspace_root()
    repo = Path(args.repo).resolve() if args.repo else root
    if not repo.is_dir():
        raise ValueError(f"repository does not exist: {repo}")
    results_subdir = _relative_path(args.results_subdir, field="--results-subdir")
    gate_path = repo / (
        _relative_path(args.gate_file, field="--gate-file")
        if args.gate_file
        else results_subdir / "latest/gate.json"
    )
    run_id = uuid4().hex
    results_dir = (
        repo
        / results_subdir
        / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{run_id}"
    )
    previous: dict[str, Any] = {}
    if args.resume:
        results_dir = Path(args.resume).resolve()
        if not results_dir.is_relative_to((repo / results_subdir).resolve()):
            raise ValueError("--resume must name this repository's results directory")
        previous = json.loads((results_dir / "gate.json").read_text(encoding="utf-8"))
        run_id = previous["runId"]
    gate: dict[str, Any] = {
        "status": "INCOMPLETE",
        "reason": "Run started; no terminal result recorded",
        "timestamp": datetime.now(UTC).isoformat(),
        "source": "moonmind-container-job",
        "repo": str(repo),
        "phase": args.phase,
        "runId": run_id,
        "image": args.image,
        "imageSourceRef": None if args.image else "tactics-unreal",
        "resultsDir": str(results_dir),
        "buildStatus": "not_run",
        "testStatus": "not_run",
    }
    if args.resume and gate_path.resolve() == (results_dir / "gate.json").resolve():
        raise ValueError(
            "--gate-file must be separate from the archived gate when resuming"
        )
    if not args.dry_run:
        # Invalidate the prior pointer before validation; retain each run's own
        # gate and logs rather than modifying old timestamped evidence.
        gate_path.parent.mkdir(parents=True, exist_ok=True)
        _write_gate(gate_path, gate)
    if not repo.is_relative_to(root):
        raise ValueError(f"--repo is outside the admitted managed workspace: {root}")
    uproject = _relative_path(args.uproject, field="--uproject")
    if not (repo / uproject).is_file():
        raise ValueError(f"uproject does not exist: {repo / uproject}")
    workdir = "/workspace" + (
        "/" + repo.relative_to(root).as_posix() if repo != root else ""
    )
    test_log = results_dir / "test.log"
    specs = _specs(
        args, workdir, uproject, "/workspace/" + test_log.relative_to(root).as_posix()
    )
    candidate = {
        "repo": str(repo),
        "phase": args.phase,
        "specs": specs,
        "agentRunId": os.environ.get("MOONMIND_AGENT_RUN_ID"),
        "sourceState": _source_state(repo, results_dir, gate_path),
    }
    if args.resume and (
        not candidate["sourceState"] or candidate != previous.get("candidate")
    ):
        raise ValueError(
            "Resume candidate does not match current source, session, "
            "or requested phases/options; start a new run"
        )
    if args.resume:
        gate.update(previous)
        gate.update(status="INCOMPLETE", reason="Resuming current run evidence")
    gate["candidate"] = candidate
    for phase in ("build", "test"):
        if phase not in specs:
            gate[f"{phase}Status"] = "skipped"
    if args.dry_run:
        for phase, spec in specs.items():
            _run_job(
                spec,
                request_id=f"tactics-{phase}-{run_id}",
                log_path=results_dir / f"{phase}.log",
                dry_run=True,
            )
        print('Gate preview: status="SKIPPED" (dry-run; no gate artifact written)')
        return 0
    results_dir.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        _write_gate(results_dir / "gate.json", gate)
        gate_path.parent.mkdir(parents=True, exist_ok=True)
        _write_gate(gate_path, gate)

    save()
    for phase in ("build", "test"):
        if phase not in specs:
            gate[f"{phase}Status"] = "skipped"
            continue
        result: dict[str, Any] = {}
        old_log = previous.get(f"{phase}Log")
        old_result = previous.get(f"{phase}Result")
        if old_log and old_result:
            try:
                result = json.loads(Path(old_result).read_text(encoding="utf-8"))
                status, reason = _outcome(result, phase, Path(old_log))
            except (OSError, ValueError):
                status = "incomplete"
        else:
            status = "incomplete"
        if status == "incomplete":
            log_path = (
                test_log
                if phase == "test"
                else results_dir / f"build-{uuid4().hex}.log"
            )
            result_path = results_dir / f"{phase}-{uuid4().hex}.result.json"
            result = _run_job(
                specs[phase],
                request_id=f"tactics-{phase}-{run_id}",
                log_path=log_path,
                dry_run=False,
            )
            _write_gate(result_path, result)
            status, reason = _outcome(result, phase, log_path)
        else:
            log_path, result_path = Path(old_log), Path(old_result)
        gate[f"{phase}Status"] = status
        gate[f"{phase}Log"] = str(log_path)
        gate[f"{phase}Result"] = str(result_path)
        if status != "pass":
            gate["status"] = "INCOMPLETE" if status == "incomplete" else "FAIL"
            gate["reason"] = reason
            save()
            return 1
        save()
    gate["status"] = "PASS"
    gate["reason"] = "Requested phases completed successfully"
    save()
    return 0


def _write_gate(gate_path: Path, gate: dict[str, Any]) -> None:
    temporary = gate_path.with_name(f".{gate_path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(gate, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, gate_path)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
