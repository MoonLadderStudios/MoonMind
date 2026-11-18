#!/usr/bin/env python3
"""Admit the complete two-shard hermetic execution, never partial successes."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci.refresh_reliability_durations import durations_pytest_args  # noqa: E402
from tools.ci.write_backend_matrix_summary import validate_receipt  # noqa: E402

DURATIONS_PATH = Path("tools/ci/hermetic-durations.json")
ARTIFACT = re.compile(r"integration-ci-hermetic-shard-([12])-attempt-([1-9][0-9]*)")
STORAGE = {
    "storage-seed.receipt.json": "tests/integration/temporal/test_storage_restart.py::test_seed_migrated_saved_inputs",
    "storage-verify.receipt.json": "tests/integration/temporal/test_storage_restart.py::test_retrieve_migrated_saved_inputs_after_restart",
}


def durations_args() -> list[str]:
    """Share advisory duration flags between execution and collection proof."""
    return durations_pytest_args(DURATIONS_PATH)


def _attempt(value: str, current: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]*", value) or int(value) > int(current):
        raise ValueError("Evidence attempt was not admitted by the current run")
    return value


def aggregate(
    root: Path,
    ownership_manifest: Path,
    *,
    revision: str,
    run_id: str,
    attempt: str,
    ownership_attempt: str,
    output: Path,
) -> dict:
    """Validate actual owners and retained evidence before publishing a summary."""
    if not revision or not run_id or not re.fullmatch(r"[1-9][0-9]*", attempt):
        raise ValueError("Missing current CI identity")
    manifest = json.loads(ownership_manifest.read_text())
    identity = {
        "revision": revision,
        "runId": run_id,
        "attempt": _attempt(ownership_attempt, attempt),
    }
    if manifest.get("schema") != "moonmind.test-ownership/v1" or any(
        manifest.get(key) != value for key, value in identity.items()
    ):
        raise ValueError("Ownership manifest belongs to another source/run/attempt")
    nodes = manifest["nodes"]
    if len({node["nodeid"] for node in nodes}) != len(nodes):
        raise ValueError("Duplicate ownership node")
    universe = {
        node["nodeid"]
        for node in nodes
        if node["eligible"] and "integration-ci" in node["owners"]
    }
    if not universe or not set(STORAGE.values()) <= universe:
        raise ValueError("Missing hermetic or storage ownership")

    selected = {}
    candidates = [root, *root.rglob("*")]
    for directory in candidates:
        match = ARTIFACT.fullmatch(directory.name)
        if not directory.is_dir() or not match:
            continue
        shard, admitted = match.groups()
        _attempt(admitted, attempt)
        previous = selected.get(shard)
        if previous and previous[0] == admitted:
            raise ValueError(f"Duplicate shard {shard} artifact for attempt {admitted}")
        if not previous or int(admitted) > int(previous[0]):
            selected[shard] = (admitted, directory)
    if set(selected) != {"1", "2"}:
        raise ValueError("Both hermetic shard artifacts are required")

    covered: set[str] = set()
    from tests.integration.security.quality import MANIFEST, summarize

    known_cases = {case["id"] for case in json.loads(MANIFEST.read_text())["cases"]}
    quality_cases: dict[str, Path] = {}
    repetitions: list[tuple[str, str, Path]] = []
    shard_quality = []
    execution_identities = set()
    receipts = []
    for shard, (admitted, directory) in sorted(selected.items()):
        suite = f"integration-hermetic-shard-{shard}"
        expected_identity = {
            "revision": revision,
            "runId": run_id,
            "attempt": admitted,
            "suite": suite,
        }
        paths = list(directory.rglob("*.receipt.json"))
        expected_names = {"main.receipt.json", *(STORAGE if shard == "1" else [])}
        if {path.name for path in paths} != expected_names or len(paths) != len(
            expected_names
        ):
            raise ValueError(
                f"Shard {shard} has missing, duplicate or unexpected phase receipts"
            )
        for path in paths:
            receipt = validate_receipt(
                path, revision=revision, run_id=run_id, attempt=admitted
            )
            if receipt["suite"] != suite:
                raise ValueError("Receipt suite differs from artifact owner")
            owned = set(receipt["expectedNodeIds"])
            if path.name in STORAGE and owned != {STORAGE[path.name]}:
                raise ValueError("Storage phase does not own its exact test")
            if path.name == "main.receipt.json" and owned & set(STORAGE.values()):
                raise ValueError("Main receipt duplicates storage phase ownership")
            if covered & owned:
                raise ValueError("Duplicate hermetic test node across receipts")
            covered.update(owned)
            receipts.append(str(path))
        if shard == "1":
            qualifications = list(directory.rglob("qualification.json"))
            if len(qualifications) != 1:
                raise ValueError("Missing or duplicate proxy/egress qualification")
            qualification = json.loads(qualifications[0].read_text())
            if (
                any(qualification.get(k) != v for k, v in expected_identity.items())
                or qualification.get("status") != "passed"
                or qualification.get("phases") != ["docker-proxy", "scoped-egress"]
            ):
                raise ValueError(
                    "Proxy/egress qualification failed or has another identity"
                )
        elif list(directory.rglob("qualification.json")):
            raise ValueError("Shard 2 cannot own proxy/egress qualification")
        run_ids = set()
        shard_cases: set[str] = set()
        shard_identities = set()
        quality_roots: set[Path] = set()
        for path in directory.rglob("security-quality/*/result.json"):
            result = json.loads(path.read_text())
            if result.get("executionIdentity") != expected_identity:
                raise ValueError("Security result belongs to another shard execution")
            run_identity = result.get("runIdentity")
            if not isinstance(run_identity, str) or not run_identity:
                raise ValueError("Missing security execution identity")
            run_ids.add(run_identity)
            case_id = result["case"]["id"]
            if (
                not isinstance(case_id, str)
                or Path(case_id).name != case_id
                or case_id in {".", ".."}
            ):
                raise ValueError("Unsafe security case id")
            if case_id not in known_cases:
                raise ValueError("Unknown security case")
            if case_id in shard_cases:
                raise ValueError("Duplicate security case within shard")
            shard_cases.add(case_id)
            quality_roots.add(path.parent.parent)
            if case_id in quality_cases:
                repetitions.append((shard, case_id, path.parent))
            else:
                quality_cases[case_id] = path.parent
            execution_identity = (revision, run_id, admitted, suite, run_identity)
            execution_identities.add(execution_identity)
            shard_identities.add(execution_identity)
        if len(run_ids) > 1:
            raise ValueError("Mixed security pytest executions within one shard")
        if len(quality_roots) > 1:
            raise ValueError("Multiple security evidence roots within shard")
        if shard_cases:
            shard_quality.append(
                (next(iter(quality_roots)), shard_cases, shard_identities)
            )
    if covered != universe:
        raise ValueError(
            f"Hermetic corpus mismatch: missing={sorted(universe-covered)}, unexpected={sorted(covered-universe)}"
        )

    # Parametrized tests may exercise the same oracle case on different shards.
    # Every occurrence must pass independently before selecting a representative.
    for directory, case_ids, identities in shard_quality:
        result = summarize(
            directory, execution_identities=identities, case_ids=case_ids
        )
        if not result["passed"]:
            raise ValueError(f"Security quality aggregate failed in shard: {result}")

    output.parent.mkdir(parents=True, exist_ok=True)
    # Build separately: an incomplete invocation cannot reuse stale successful cases.
    with tempfile.TemporaryDirectory(
        prefix="hermetic-quality-", dir=output.parent
    ) as tmp:
        staged = Path(tmp)
        for case_id, source in quality_cases.items():
            shutil.copytree(source, staged / case_id)
        summary = summarize(staged, execution_identities=execution_identities)
        if not summary["passed"]:
            raise ValueError(f"Security quality aggregate failed: {summary}")
        for shard, case_id, source in repetitions:
            target = staged / "repetitions" / f"shard-{shard}" / case_id
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
        summary["receipts"] = receipts
        (staged / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(staged, output)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("durations-args")
    command = sub.add_parser("aggregate")
    command.add_argument("--root", type=Path, required=True)
    command.add_argument("--ownership-manifest", type=Path, required=True)
    command.add_argument("--ownership-attempt", required=True)
    command.add_argument(
        "--output", type=Path, default=Path("artifacts/integration-ci/security-quality")
    )
    args = parser.parse_args()
    if args.command == "durations-args":
        print(shlex.join(durations_args()))
        return 0
    try:
        result = aggregate(
            args.root,
            args.ownership_manifest,
            revision=os.environ.get("MOONMIND_CI_REVISION", ""),
            run_id=os.environ.get("GITHUB_RUN_ID", ""),
            attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            ownership_attempt=args.ownership_attempt,
            output=args.output,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Hermetic aggregation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
