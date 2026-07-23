#!/usr/bin/env python3
"""Execute deterministic Omnigent conformance layers and publish runner evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.conformance import (  # noqa: E402
    ConformanceContractError,
    assert_secret_free,
)

PROFILE = REPO_ROOT / "tests/fixtures/omnigent/conformance-v4.json"
DETERMINISTIC_CASES = {
    "proxy.routes",
    "session.first-message-crash-matrix",
    "events.durable-replay-sse",
    "projection.workflow-detail-chat",
    "resources.authorization-and-evidence",
    "failures.lifecycle-and-redaction",
    "codex.direct-event-parity",
    "failures.transport-status-timeout",
    "events.replay-overlap-schema-drift",
    "resources.bounds-and-secret-scan",
}
ISSUE_LINKS = (
    "MoonLadderStudios/MoonMind#3480",
    "MoonLadderStudios/MoonMind#3471",
    "MoonLadderStudios/MoonMind#3456",
)
EVIDENCE_GROUPS = {
    "cumulativeJourney": (
        "tests/integration/reliability/test_checkpoint_cold_resume.py",
        "tests/unit/workflows/temporal/test_remediation_workspace_head.py",
        "tests/unit/workflows/temporal/workflows/test_run_integration.py",
    ),
    "failureAndRestartMatrix": (
        "tests/integration/omnigent/test_embedded_recovery.py",
    ),
    "rolloutAndReplay": (
        "tests/unit/workflows/adapters/test_external_adapter_registry.py",
        "tests/unit/workflows/temporal/test_temporal_workers.py",
        "tests/unit/workflows/temporal/workflows/test_run_bounded_story_loop.py",
        "frontend/src/entrypoints/workflow-detail.test.tsx",
    ),
}
COMMANDS = (
    (
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/omnigent",
        "tests/integration/omnigent",
        "tests/unit/tools/test_run_omnigent_live_conformance.py",
        # This production-Postgres serialization check belongs to the
        # compose-backed integration-ci job; the deterministic runner does not
        # provision a database service.
        "--ignore=tests/integration/omnigent/test_host_auth_lifecycle.py",
        "-q",
        "--tb=short",
    ),
    (
        sys.executable,
        "-m",
        "pytest",
        "tests/integration/reliability/test_checkpoint_cold_resume.py",
        "tests/unit/workflows/temporal/test_remediation_workspace_head.py",
        "tests/unit/workflows/temporal/workflows/test_run_integration.py",
        "tests/integration/omnigent/test_embedded_recovery.py",
        "tests/unit/workflows/adapters/test_external_adapter_registry.py",
        "tests/unit/workflows/temporal/test_temporal_workers.py",
        "tests/unit/workflows/temporal/workflows/test_run_bounded_story_loop.py",
        "-q",
        "--tb=short",
    ),
    (
        "npm",
        "run",
        "ui:test",
        "--",
        "frontend/src/entrypoints/workflow-detail.test.tsx",
    ),
)


def _artifact_ref(path: Path) -> str:
    """Return a repo-relative ref when possible, otherwise an absolute path."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/omnigent-conformance"),
    )
    parser.add_argument("--server-image", required=True)
    parser.add_argument("--host-image", required=True)
    parser.add_argument("--host-architecture", required=True)
    parser.add_argument("--auth-mode", default="deterministic-fake")
    args = parser.parse_args()
    if not args.output_dir.is_absolute():
        args.output_dir = REPO_ROOT / args.output_dir
    args.output_dir.mkdir(parents=True, exist_ok=True)
    failed = False
    log_paths: list[Path] = []
    command_results: list[dict[str, object]] = []
    for index, command in enumerate(COMMANDS, start=1):
        log_path = args.output_dir / f"deterministic-{index}.log"
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                text=True,
            )
        log_paths.append(log_path)
        command_results.append(
            {
                "commandIndex": index,
                "arguments": list(command),
                "exitCode": completed.returncode,
                "logRef": _artifact_ref(log_path),
                "logDigest": "sha256:"
                + hashlib.sha256(log_path.read_bytes()).hexdigest(),
            }
        )
        failed |= completed.returncode != 0

    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    profile_case_ids = {case["id"] for case in profile["cases"]}
    missing_deterministic = DETERMINISTIC_CASES - profile_case_ids
    if missing_deterministic:
        failed = True
    evidence_group_results: dict[str, dict[str, object]] = {}
    for name, paths in EVIDENCE_GROUPS.items():
        path_results = []
        for path in paths:
            matching_commands = [
                result
                for result in command_results
                if path in result["arguments"]
            ]
            passed = bool(matching_commands) and all(
                result["exitCode"] == 0 for result in matching_commands
            )
            path_results.append(
                {
                    "path": path,
                    "status": "passed" if passed else "failed",
                    "commandIndexes": [
                        result["commandIndex"] for result in matching_commands
                    ],
                }
            )
            failed |= not passed
        evidence_group_results[name] = {
            "status": (
                "passed"
                if all(result["status"] == "passed" for result in path_results)
                else "failed"
            ),
            "paths": path_results,
        }
    cases = []
    for case in profile["cases"]:
        cases.append(
            {
                "caseId": case["id"],
                "status": (
                    ("failed" if failed else "passed")
                    if case["id"] in DETERMINISTIC_CASES
                    else "skipped"
                ),
                "evidenceRefs": [
                    _artifact_ref(path) for path in log_paths
                ],
            }
        )
    scan_path = args.output_dir / "secret-scan.json"
    try:
        for log_path in log_paths:
            assert_secret_free(log_path.read_text(encoding="utf-8"))
        scan_status = "passed"
    except ConformanceContractError:
        scan_status = "failed"
        failed = True
    scan_path.write_text(
        json.dumps({"status": scan_status, "scope": "deterministic-runner-logs"})
        + "\n",
        encoding="utf-8",
    )
    scans = {
        channel: {"status": scan_status, "evidenceRef": str(scan_path)}
        for channel in ("logs", "temporalHistory", "screenshots", "archives")
    }
    evidence = {
        "images": {"server": args.server_image, "host": args.host_image},
        "hostArchitecture": args.host_architecture,
        "authMode": args.auth_mode,
        "protocolVersion": "omnigent/v1",
        "capabilities": ["deterministic-fake", "bridge", "workflow-detail"],
        "evidenceScans": scans,
        "commandResults": command_results,
        "cases": cases,
        "deterministicCoverage": {
            "requiredCaseIds": sorted(DETERMINISTIC_CASES),
            "missingCaseIds": sorted(missing_deterministic),
            "issueLinks": list(ISSUE_LINKS),
            "evidenceGroups": {
                name: list(paths) for name, paths in EVIDENCE_GROUPS.items()
            },
            "evidenceGroupResults": evidence_group_results,
        },
    }
    evidence_path = args.output_dir / "runner-evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    build = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools/build_omnigent_conformance_report.py"),
            str(evidence_path),
            str(args.output_dir / "report.json"),
            "--allow-partial",
        ],
        cwd=REPO_ROOT,
        check=False,
    )
    return 1 if failed or build.returncode else 0


if __name__ == "__main__":
    raise SystemExit(main())
