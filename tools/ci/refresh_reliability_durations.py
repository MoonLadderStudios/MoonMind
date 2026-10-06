#!/usr/bin/env python3
"""Refresh and validate reliability duration hints (MoonLadderStudios/MoonMind#4366, #4629).

``tests/.reliability-test-durations.json`` maps reliability test node IDs to
advisory durations in seconds for pytest-split ``--splitting-algorithm
least_duration``. Duration history is advisory load balancing only: missing,
stale, corrupt, or incomplete entries must never drop, duplicate, or skip
tests -- unknown nodes fall back to the plugin estimate and still run exactly
once.

Subcommands (this file is the single local helper; keep it small):

- ``--from-snapshots SNAPSHOT...``: import measured per-node costs from the
  per-shard durations snapshots the backend-matrix evidence hook
  (``tools/ci/write_backend_matrix_summary.py``) uploads with each
  reliability row (``pytest-backend-reliability-shard-N-durations.json``).
  Each case is pytest's JUnit ``time``, which by default
  (``junit_duration_report = total``) sums the setup, call and teardown
  phases, keyed by its actual parameter ID. All snapshots must come from one
  revision, run and attempt, from shards without failures or errors, with
  every case measured once; otherwise nothing is written.
- ``refresh`` (default, no snapshots): re-collect the eligible reliability
  universe and keep the existing measured hints for nodes that are still
  collected. Deleted tests stop being hints; values are never invented or
  averaged.
- ``--validate-only``: exit 0 when the hints file is usable, 2 when it is
  present but unusable (warn and fall back to the deterministic no-history
  partition on every shard), 3 when it is missing or empty (missing timing
  history is not a coverage failure).

Retained run artifacts supply the snapshots, for example::

    gh run download <run-id> -p 'pytest-reliability-shard-*-attempt-<n>' -D /tmp/hints
    python3 tools/ci/refresh_reliability_durations.py \\
      --from-snapshots /tmp/hints/*/pytest-backend-reliability-shard-*-durations.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RELIABILITY_DIR = REPO_ROOT / "tests" / "integration" / "reliability"
DURATIONS_PATH = REPO_ROOT / "tests" / ".reliability-test-durations.json"

# Mirrors the eligible reliability universe in
# tools/verify_test_shard_ownership.py: reliability journeys without provider
# or credential requirements.
RELIABILITY_MARKER_EXPR = (
    "reliability_journey and not provider_verification and not requires_credentials"
)

# Exit statuses for --validate-only (consumed by the CI reliability rows).
VALID, UNUSABLE, MISSING = 0, 2, 3

# Snapshot fields that identify one coherent measurement.
SNAPSHOT_IDENTITY = ("revision", "run_id", "attempt")
# The hints file requires positive durations; near-zero cases keep a floor.
MIN_HINT_SECONDS = 0.01


class SnapshotError(ValueError):
    """The supplied snapshots are not one coherent, complete measurement."""


def junit_case_key(nodeid: str) -> str:
    """Return the ``classname::name`` identity pytest's JUnit report uses.

    ``tests/a/test_x.py::TestC::test_y[p]`` becomes
    ``tests.a.test_x.TestC::test_y[p]``; parameter IDs are kept verbatim.
    """
    base, bracket, params = nodeid.partition("[")
    path, *names = base.split("::")
    module = path[:-3] if path.endswith(".py") else path
    classname = ".".join([module.replace("/", "."), *names[:-1]])
    return f"{classname}::{names[-1]}{bracket}{params}"


def load_measured_costs(snapshot_paths: list[Path]) -> dict[str, float]:
    """Return measured seconds by JUnit case key from one run attempt.

    Raises SnapshotError for unreadable snapshots, missing or mixed
    revision/run/attempt identity, failed or errored shards (a partial
    result must not replace a complete baseline), or a case measured twice.
    """
    if not snapshot_paths:
        raise SnapshotError("no durations snapshots were supplied")
    identity: dict[str, str] | None = None
    suites: set[str] = set()
    measured: dict[str, float] = {}
    for path in snapshot_paths:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SnapshotError(f"{path}: unreadable snapshot ({exc})") from exc
        if not isinstance(payload, dict):
            raise SnapshotError(f"{path}: snapshot is not an object")
        current = {field: str(payload.get(field) or "") for field in SNAPSHOT_IDENTITY}
        for field, value in current.items():
            if not value:
                raise SnapshotError(f"{path}: snapshot has no {field}")
        if identity is None:
            identity = current
        for field in SNAPSHOT_IDENTITY:
            if current[field] != identity[field]:
                raise SnapshotError(
                    f"{path}: {field} {current[field]!r} differs from "
                    f"{identity[field]!r}; import one revision/attempt at a time"
                )
        suite = str(payload.get("suite") or path)
        if suite in suites:
            raise SnapshotError(f"{path}: suite {suite!r} supplied more than once")
        suites.add(suite)
        if payload.get("failures") or payload.get("errors"):
            raise SnapshotError(
                f"{path}: suite {suite!r} failed; a partial shard cannot replace measured hints"
            )
        for case in payload.get("cases") or []:
            key = str(case.get("nodeid") or "")
            try:
                seconds = float(case.get("duration"))
            except (TypeError, ValueError):
                raise SnapshotError(f"{path}: case {key!r} has no numeric duration") from None
            if not key or seconds < 0:
                raise SnapshotError(f"{path}: invalid case {case!r}")
            if key in measured:
                raise SnapshotError(f"{path}: case {key!r} was measured more than once")
            measured[key] = seconds
    return measured


def assemble_measured_hints(
    collected_nodeids: list[str], measured: dict[str, float]
) -> dict[str, float]:
    """Map each collected node to its measured cost.

    Collected nodes without a measurement (new tests) stay absent so the
    plugin fallback still selects them; measurements for nodes that are no
    longer collected are dropped.
    """
    hints: dict[str, float] = {}
    for nodeid in sorted(collected_nodeids):
        seconds = measured.get(junit_case_key(nodeid))
        if seconds is not None:
            hints[nodeid] = max(round(seconds, 2), MIN_HINT_SECONDS)
    return hints


def validate_hints_file(path: Path = DURATIONS_PATH) -> int:
    """Return VALID (0), UNUSABLE (2), or MISSING (3) for a hints file."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"warning: duration hints file is missing: {path}", file=sys.stderr)
        return MISSING
    except (OSError, ValueError) as exc:
        print(f"warning: duration hints file is unusable ({exc}); "
              "falling back to the deterministic no-history partition", file=sys.stderr)
        return UNUSABLE
    if not isinstance(payload, dict) or not payload:
        print("warning: duration hints file holds no usable entries; "
              "falling back to the deterministic no-history partition", file=sys.stderr)
        return UNUSABLE
    for nodeid, duration in payload.items():
        if not isinstance(nodeid, str) or "::" not in nodeid:
            print(f"warning: duration hints file has a non-node entry {nodeid!r}; "
                  "falling back to the deterministic no-history partition", file=sys.stderr)
            return UNUSABLE
        if not isinstance(duration, (int, float)) or not duration > 0:
            print(f"warning: duration hints file has a non-positive duration for {nodeid!r}; "
                  "falling back to the deterministic no-history partition", file=sys.stderr)
            return UNUSABLE
    return VALID


def durations_pytest_args(path: Path = DURATIONS_PATH) -> list[str]:
    """Return the --durations-path argv when hints are usable, else [].

    Centralizes the warn-and-fallback contract so CI rows and the ownership
    verifier share one behavior: missing history is not a failure, an
    unusable file warns and partitions without history.
    """
    status = validate_hints_file(path)
    if status == VALID:
        return ["--durations-path", str(path)]
    if status == MISSING:
        print("warning: running without duration hints; using the deterministic "
              "no-history partition", file=sys.stderr)
    return []


def collect_reliability_nodeids() -> list[str]:
    """Collect the eligible reliability universe. Raises on failure: a
    collection error must never silently shrink the corpus."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/reliability",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "-m",
            RELIABILITY_MARKER_EXPR,
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=600,
    )
    if proc.returncode not in (0, 5):
        raise RuntimeError(
            "reliability collection failed with exit "
            f"{proc.returncode}:\n{(proc.stdout + proc.stderr)[-2000:]}"
        )
    return sorted(
        {line.strip() for line in proc.stdout.splitlines() if "::" in line}
    )


def _existing_hints(path: Path) -> dict[str, float]:
    if validate_hints_file(path) != VALID:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def refresh(
    durations_path: Path = DURATIONS_PATH, snapshots: list[Path] | None = None
) -> int:
    """Rewrite the hints file for fresh collection. Returns node count.

    With snapshots, hints are the measured costs of that run attempt;
    without, existing measured hints are kept for still-collected nodes.
    """
    nodeids = collect_reliability_nodeids()
    if snapshots:
        hints = assemble_measured_hints(nodeids, load_measured_costs(snapshots))
    else:
        existing = _existing_hints(durations_path)
        hints = {nodeid: existing[nodeid] for nodeid in nodeids if nodeid in existing}
    durations_path.write_text(
        json.dumps(hints, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {len(hints)} duration hints for {len(nodeids)} collected nodes; "
        f"{len(nodeids) - len(hints)} without history use the plugin fallback"
    )
    return len(nodeids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate the hints file and exit 0 (usable), 2 (unusable), or 3 (missing)",
    )
    parser.add_argument(
        "--from-snapshots",
        nargs="+",
        type=Path,
        metavar="SNAPSHOT",
        help="import measured per-node costs from one run attempt's shard snapshots",
    )
    args = parser.parse_args(argv)
    if args.validate_only:
        return validate_hints_file(DURATIONS_PATH)
    try:
        refresh(DURATIONS_PATH, snapshots=args.from_snapshots)
    except SnapshotError as exc:
        print(f"error: {exc}; the hints file was not changed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
