#!/usr/bin/env python3
"""Execute deterministic Omnigent conformance layers and publish runner evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.conformance import (  # noqa: E402
    ConformanceContractError,
    assert_secret_free,
    redact_secrets,
)
from tools.ci.write_backend_matrix_summary import validate_receipt  # noqa: E402

# Activation contract for the bounded fake/stock-compatible provider path.  A
# caller engages it explicitly via ``--fake-provider`` or this env var; the
# selection is recorded in the published evidence (an observable runtime mode
# switch), never a hidden fallback.  The Tier-1 exact-artifact gate does not use
# it: that gate probes image capabilities and runs no provider execution.
FAKE_PROVIDER_ENV = "MOONMIND_OMNIGENT_FAKE_PROVIDER"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def resolve_fake_provider_selection(
    *, cli_flag: bool, env: Mapping[str, str], auth_mode: str
) -> dict[str, object]:
    """Resolve the bounded fake-provider activation for this conformance run.

    A requested fake provider requires the deterministic-fake auth mode, so a
    mismatched credentialed auth mode fails fast (never silently runs a
    different provider path). Returns a compact, secret-free record for the
    published evidence so the exact-artifact gate can prove the bounded
    execution engaged through the exact image rather than being assumed.
    """

    env_requested = str(env.get(FAKE_PROVIDER_ENV, "")).strip().lower() in _TRUTHY
    requested = bool(cli_flag) or env_requested
    if not requested:
        return {"requested": False, "engaged": False, "authMode": auth_mode}
    if auth_mode != "deterministic-fake":
        raise ConformanceContractError(
            "fake-provider execution requires --auth-mode 'deterministic-fake'; "
            f"got {auth_mode!r}"
        )
    return {
        "requested": True,
        "engaged": True,
        "authMode": auth_mode,
        "source": "cli" if cli_flag else "env",
    }


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
# This case stays critical in the profile, but it must remain skipped in the
# partial deterministic report until one production-shaped journey proves the
# persisted Temporal, profile-bound host/session, publication, cleanup, and
# Workflow Detail boundaries plus the complete fault/restart matrix.  A green
# exit from the narrower checkpoint test is useful evidence, but is not enough
# to classify the product case as passed.
PENDING_PRODUCTION_SHAPED_CASES = {"product.cumulative-remediation"}
ISSUE_LINKS = (
    "MoonLadderStudios/MoonMind#3480",
    "MoonLadderStudios/MoonMind#3471",
    "MoonLadderStudios/MoonMind#3456",
)
EVIDENCE_GROUPS = {
    "cumulativeJourney": (
        "tests/integration/reliability_journey/"
        "test_omnigent_cumulative_remediation_journey.py",
    ),
    "failureAndRestartMatrix": (
        "tests/integration/reliability_journey/"
        "test_omnigent_cumulative_remediation_journey.py",
        "tests/integration/omnigent/test_embedded_recovery.py",
    ),
    "rolloutAndReplay": (
        "tests/unit/workflows/adapters/test_external_adapter_registry.py",
        "tests/unit/workflows/temporal/test_temporal_workers.py",
        "tests/unit/workflows/temporal/workflows/test_run_bounded_story_loop.py",
        "frontend/src/entrypoints/workflow-detail.test.tsx",
    ),
}
PENDING_EVIDENCE_GROUPS = {"cumulativeJourney"}
# The deterministic layers are ordinary hermetic pytest modules that already run
# under xdist in the unit shards; spreading them across the runner's cores keeps
# the evidence bundle identical while the job finishes in a fraction of the time.
_PYTEST_PARALLEL_ARGS = ("-n", "auto", "--dist", "loadfile")
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
        # Actual Docker recovery belongs to the separate required installed-
        # image owner. Source receipts cannot qualify that runtime boundary.
        "--ignore=tests/integration/omnigent/test_exact_docker_n_way_concurrency.py",
        # The second layer runs this module explicitly and owns its recovery
        # evidence. Exclude it here to avoid executing the same nodes twice
        # with the same interpreter, environment, and pytest options.
        "--ignore=tests/integration/omnigent/test_embedded_recovery.py",
        "-q",
        "--tb=short",
        *_PYTEST_PARALLEL_ARGS,
    ),
    (
        sys.executable,
        "-m",
        "pytest",
        "tests/integration/reliability/test_checkpoint_cold_resume.py",
        "tests/integration/reliability_journey/"
        "test_omnigent_cumulative_remediation_journey.py",
        "tests/unit/workflows/temporal/test_remediation_workspace_head.py",
        "tests/unit/workflows/temporal/workflows/test_run_integration.py",
        "tests/integration/omnigent/test_embedded_recovery.py",
        "tests/unit/workflows/adapters/test_external_adapter_registry.py",
        "tests/unit/workflows/temporal/test_temporal_workers.py",
        "tests/unit/workflows/temporal/workflows/test_run_bounded_story_loop.py",
        "-q",
        "--tb=short",
        *_PYTEST_PARALLEL_ARGS,
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


def owner_receipt_commands(
    directory: Path,
    manifest_path: Path,
    *,
    revision: str,
    run_id: str,
    attempt: str,
    manifest_attempt: str | None = None,
    frontend_attempt: str | None = None,
    require_full_source: bool = False,
) -> list[dict]:
    """Prove the same collected corpus through its existing exclusive owners."""
    manifest = json.loads(manifest_path.read_text())

    def admitted(value):
        if (
            not value.isdecimal()
            or not attempt.isdecimal()
            or not 1 <= int(value) <= int(attempt)
        ):
            raise ValueError("Evidence attempt was not admitted by the current run")
        return value

    identity = {
        "revision": revision,
        "runId": run_id,
        "attempt": admitted(manifest_attempt or attempt),
    }
    if (
        not revision
        or not run_id
        or not attempt
        or manifest.get("schema") != "moonmind.test-ownership/v1"
        or any(manifest.get(key) != value for key, value in identity.items())
    ):
        raise ValueError("Ownership manifest belongs to another source/run/attempt")
    covered: dict[str, str] = {}
    receipt_refs: list[str] = []
    artifact_roots: set[Path] = set()
    inventory = {node["nodeid"]: node for node in manifest["nodes"]}
    selected = {}
    for path in sorted(directory.rglob("*.receipt.json")):
        if path.name == "frontend.receipt.json":
            continue
        expected_attempt, expected_suite, root = attempt, None, path.parent
        for parent in path.parents:
            match = re.fullmatch(
                r"pytest-([a-z0-9-]+)-attempt-([1-9][0-9]*)", parent.name
            )
            hermetic = re.fullmatch(
                r"integration-ci-hermetic-attempt-([1-9][0-9]*)", parent.name
            )
            if match or hermetic:
                expected_attempt = admitted(match[2] if match else hermetic[1])
                expected_suite = match[1] if match else "integration-hermetic"
                root = parent
                break
        key = (expected_suite, path.name)
        if key not in selected or int(expected_attempt) > int(selected[key][1]):
            selected[key] = (path, expected_attempt, expected_suite, root)
    for path, expected_attempt, expected_suite, root in selected.values():
        receipt = validate_receipt(
            path, revision=revision, run_id=run_id, attempt=expected_attempt
        )
        if expected_suite and receipt["suite"] != expected_suite:
            raise ValueError("Receipt suite differs from its immutable owner artifact")
        owner = receipt["suite"]
        if owner.startswith("reliability-shard-"):
            owner = "reliability-matrix"
        if owner == "integration-hermetic":
            owner = "integration-ci"
        for case in receipt["cases"]:
            node = inventory.get(case["nodeid"])
            if not node or not node["eligible"]:
                continue
            if owner not in node["owners"]:
                raise ValueError(f"Wrong receipt owner for {case['nodeid']}")
            covered[case["nodeid"]] = case["outcome"]
        receipt_refs.append(_artifact_ref(path))
        artifact_roots.add(root)
    if require_full_source:
        source_owners = {
            "unit-fast",
            "unit-slow",
            "api-component",
            "temporal-boundary",
            "reliability-matrix",
            "integration-ci",
        }
        expected = {
            node["nodeid"]
            for node in inventory.values()
            if node["eligible"] and set(node["owners"]) & source_owners
        }
        if not expected or expected - covered.keys():
            raise ValueError(
                f"Incomplete full source verification: {sorted(expected - covered.keys())[:20]}"
            )
    results = []
    for index, command in enumerate(COMMANDS, start=1):
        paths = [argument for argument in command if argument.startswith("tests/")]
        ignored = [
            argument.removeprefix("--ignore=")
            for argument in command
            if argument.startswith("--ignore=")
        ]
        if paths:
            expected = {
                node["nodeid"]
                for node in inventory.values()
                if node["eligible"]
                and any(
                    node["path"] == path or node["path"].startswith(path + "/")
                    for path in paths
                )
                and not any(
                    node["path"] == path or node["path"].startswith(path + "/")
                    for path in ignored
                )
            }
            if not expected or expected - covered.keys():
                raise ValueError(
                    f"Missing owner verification for conformance layer {index}: {sorted(expected - covered.keys())[:20]}"
                )
            for path in paths:
                cases = [
                    nodeid
                    for nodeid in expected
                    if nodeid.split("::")[0] == path or nodeid.startswith(path + "/")
                ]
                if not cases or not any(
                    covered[nodeid] == "passed" for nodeid in cases
                ):
                    raise ValueError(f"No executed passing witness for {path}")
            nodeids = sorted(expected)
        else:
            frontend = [
                json.loads(path.read_text())
                for path in directory.rglob("frontend.receipt.json")
            ]
            if (
                len(frontend) != 1
                or frontend[0].get("schema") != "moonmind.vitest-verification/v1"
                or any(
                    frontend[0].get(key) != value
                    for key, value in {
                        **identity,
                        "attempt": admitted(frontend_attempt or attempt),
                    }.items()
                )
            ):
                raise ValueError("Missing current frontend owner receipt")
            report = frontend[0]["report"]
            assertions = [
                case
                for suite in report["testResults"]
                for case in suite["assertionResults"]
            ]
            if (
                report.get("success") is not True
                or len(assertions) != report["numTotalTests"]
                or any(
                    case["status"] not in {"passed", "pending", "todo", "skipped"}
                    for case in assertions
                )
            ):
                raise ValueError("Incomplete frontend owner collection")
            selected = [
                suite
                for suite in report["testResults"]
                if suite["name"].replace("\\", "/").endswith(command[-1])
            ]
            if (
                len(selected) != 1
                or not selected[0]["assertionResults"]
                or not any(
                    case["status"] == "passed"
                    for case in selected[0]["assertionResults"]
                )
            ):
                raise ValueError("Missing frontend conformance assertions")
            nodeids = [case["fullName"] for case in selected[0]["assertionResults"]]
            artifact_roots.update(
                path.parent for path in directory.rglob("frontend.receipt.json")
            )
        results.append(
            {
                "arguments": list(command),
                "nodeIds": nodeids,
                "ownerReceiptRefs": receipt_refs,
                "ownerArtifactRoots": [str(path) for path in sorted(artifact_roots)],
            }
        )
    return results


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
    parser.add_argument("--owner-receipts", type=Path)
    parser.add_argument("--ownership-manifest", type=Path)
    parser.add_argument("--ownership-attempt")
    parser.add_argument("--frontend-attempt")
    parser.add_argument("--require-full-source", action="store_true")
    parser.add_argument(
        "--fake-provider",
        action="store_true",
        help=(
            "Engage the bounded fake/stock-compatible provider path for the "
            "exact-artifact Tier-1 gate (also activated by "
            f"{FAKE_PROVIDER_ENV}=1). Requires --auth-mode deterministic-fake."
        ),
    )
    args = parser.parse_args()
    if not args.output_dir.is_absolute():
        args.output_dir = REPO_ROOT / args.output_dir
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Resolve the fake-provider activation before running any layer so a
    # requested-but-incompatible auth mode fails fast rather than mid-run.
    try:
        fake_provider = resolve_fake_provider_selection(
            cli_flag=args.fake_provider, env=os.environ, auth_mode=args.auth_mode
        )
    except ConformanceContractError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    failed = False
    log_paths: list[Path] = []
    command_results: list[dict[str, object]] = []
    receipts = None
    if args.owner_receipts:
        try:
            receipts = owner_receipt_commands(
                args.owner_receipts,
                args.ownership_manifest,
                revision=os.environ.get("MOONMIND_CI_REVISION", ""),
                run_id=os.environ.get("GITHUB_RUN_ID", ""),
                attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
                manifest_attempt=args.ownership_attempt,
                frontend_attempt=args.frontend_attempt,
                require_full_source=args.require_full_source,
            )
            # Retain the secret scan over the actual owners' streamed logs.
            log_paths.extend(
                sorted(
                    {
                        path
                        for result in receipts
                        for root in result["ownerArtifactRoots"]
                        for path in Path(root).rglob("*.log")
                    }
                )
            )
            if not log_paths:
                raise ValueError("Missing owner execution logs")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"::error::Invalid owner evidence: {exc}", file=sys.stderr)
            return 1
    for index, command in enumerate(COMMANDS, start=1):
        log_path = args.output_dir / f"deterministic-{index}.log"
        with log_path.open("w", encoding="utf-8") as log:
            if receipts is not None:
                json.dump(receipts[index - 1], log, indent=2)
                completed = subprocess.CompletedProcess(command, 0)
            else:
                completed = subprocess.run(
                    command,
                    cwd=REPO_ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                    text=True,
                )
        log_paths.append(log_path)
        if completed.returncode:
            print(
                f"Conformance layer {index} failed with exit code "
                f"{completed.returncode}; log: {_artifact_ref(log_path)}"
            )
            redacted_log = redact_secrets(log_path.read_text(encoding="utf-8"))
            print("\n".join(redacted_log.splitlines()[-200:]))
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
        if completed.returncode != 0:
            print(
                f"::error::Conformance layer {index} failed "
                f"(exit code {completed.returncode}); log: {_artifact_ref(log_path)}",
                file=sys.stderr,
            )
            print(
                redact_secrets(log_path.read_text(encoding="utf-8")),
                file=sys.stderr,
                end="",
            )

    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    profile_case_ids = {case["id"] for case in profile["cases"]}
    missing_deterministic = DETERMINISTIC_CASES - profile_case_ids
    missing_pending = PENDING_PRODUCTION_SHAPED_CASES - profile_case_ids
    if missing_deterministic:
        failed = True
    if missing_pending:
        failed = True
    evidence_group_results: dict[str, dict[str, object]] = {}
    for name, paths in EVIDENCE_GROUPS.items():
        path_results = []
        for path in paths:
            matching_commands = [
                result for result in command_results if path in result["arguments"]
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
        paths_passed = all(result["status"] == "passed" for result in path_results)
        evidence_group_results[name] = {
            "status": (
                "pending"
                if name in PENDING_EVIDENCE_GROUPS and paths_passed
                else "passed" if paths_passed else "failed"
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
                "evidenceRefs": [_artifact_ref(path) for path in log_paths],
            }
        )
    scan_path = args.output_dir / "secret-scan.json"
    try:
        for log_path in log_paths:
            assert_secret_free(log_path.read_text(encoding="utf-8"))
        scan_status = "passed"
    except ConformanceContractError as exc:
        scan_status = "failed"
        failed = True
        print(f"::error::{exc}", file=sys.stderr)
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
        "fakeProvider": fake_provider,
        "protocolVersion": "omnigent/v1",
        "capabilities": ["deterministic-fake", "bridge", "workflow-detail"],
        "evidenceScans": scans,
        "commandResults": command_results,
        "cases": cases,
        "deterministicCoverage": {
            "requiredCaseIds": sorted(DETERMINISTIC_CASES),
            "missingCaseIds": sorted(missing_deterministic),
            "pendingProductionShapedCaseIds": sorted(PENDING_PRODUCTION_SHAPED_CASES),
            "missingPendingCaseIds": sorted(missing_pending),
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
