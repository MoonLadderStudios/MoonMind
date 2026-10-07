#!/usr/bin/env python3
"""Validate and import reliability duration hints (MoonLadderStudios/MoonMind#4366, #4629).

``tests/.reliability-test-durations.json`` maps reliability test node IDs to
advisory durations in seconds for pytest-split ``--splitting-algorithm
least_duration``. Duration history is advisory load balancing only: missing,
stale, corrupt, or incomplete entries must never drop, duplicate, or skip
tests -- unknown nodes fall back to the plugin estimate and still run exactly
once.

Every committed entry is an observed cost: the exact pytest node ID and its
JUnit ``total`` duration (setup + call + teardown) recorded by one complete,
successful four-shard reliability matrix run. Nodes without an entry (new or
renamed tests) use pytest-split's fallback estimate, the average of the
observed entries for the collected nodes; stale entries for removed tests
are ignored by the plugin until the next import drops them.

Subcommands (this file is the single local helper; keep it small):

- ``--validate-only`` (also the no-argument default): exit 0 when the hints
  file is usable, 2 when it is present but unusable (warn and fall back to
  the deterministic no-history partition on every shard), 3 when it is
  missing (missing timing history is not a coverage failure).
- ``import SOURCE...``: read the per-shard durations snapshots written by
  ``tools/ci/write_backend_matrix_summary.py`` (files, or directories such
  as a ``gh run download`` target searched recursively) and replace the
  hints with their observed exact-node costs. The snapshots must come from
  one run, attempt, and revision, cover every reliability shard exactly
  once, report success, and carry valid exact observations; otherwise the
  committed baseline is left byte-for-byte unchanged and the reason is
  printed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.ci.write_backend_matrix_summary import (  # noqa: E402
    DURATION_SEMANTICS,
    SNAPSHOT_SCHEMA,
)

DURATIONS_PATH = REPO_ROOT / "tests" / ".reliability-test-durations.json"
RELIABILITY_PREFIX = "tests/integration/reliability/"

# The eligible reliability universe shared by the CI rows and
# tools/verify_test_shard_ownership.py: reliability journeys without
# provider or credential requirements.
RELIABILITY_MARKER_EXPR = (
    "reliability_journey and not provider_verification and not requires_credentials"
)
# pytest-split groups are 1-based; suite reliability-shard-N runs --group N.
RELIABILITY_SHARD_COUNT = 4
RELIABILITY_SUITES = tuple(
    f"reliability-shard-{index}" for index in range(1, RELIABILITY_SHARD_COUNT + 1)
)
SNAPSHOT_GLOB = "pytest-backend-reliability-shard-*-durations.json"
IDENTITY_FIELDS = ("revision", "runId", "attempt")
OBSERVED_OUTCOMES = ("passed", "skipped")

# Exit statuses for --validate-only (consumed by the CI reliability rows).
VALID, UNUSABLE, MISSING = 0, 2, 3


class ImportRejected(ValueError):
    """The observations cannot replace the committed baseline."""


@dataclass(frozen=True)
class ImportResult:
    revision: str
    run_id: str
    attempt: str
    node_count: int
    shard_seconds: dict[str, float]


def _is_duration(value: object) -> bool:
    """A finite, non-negative number of seconds (bool is not a number here)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


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
        if not _is_duration(duration):
            print(f"warning: duration hints file has an invalid duration {duration!r} "
                  f"for {nodeid!r}; falling back to the deterministic no-history "
                  "partition", file=sys.stderr)
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


def find_snapshots(sources: list[Path]) -> list[Path]:
    """Expand files and directories (searched recursively) into snapshots."""
    found: list[Path] = []
    for source in sources:
        if source.is_dir():
            found.extend(sorted(source.rglob(SNAPSHOT_GLOB)))
        else:
            found.append(source)
    return found


def _snapshot_errors(label: str, payload: object) -> list[str]:
    """Return why one shard snapshot is not a complete successful observation."""
    if not isinstance(payload, dict):
        return [f"{label}: not a durations snapshot object"]
    errors: list[str] = []
    if payload.get("schema") != SNAPSHOT_SCHEMA:
        errors.append(f"{label}: unsupported snapshot schema {payload.get('schema')!r}")
    suite = payload.get("suite")
    if suite not in RELIABILITY_SUITES:
        errors.append(f"{label}: unexpected suite {suite!r}")
    elif payload.get("shard") != suite.rsplit("-", 1)[-1]:
        errors.append(f"{label}: shard {payload.get('shard')!r} does not match suite {suite}")
    for field in IDENTITY_FIELDS:
        if not isinstance(payload.get(field), str) or not payload.get(field):
            errors.append(f"{label}: missing {field}")
    if payload.get("testOutcome") != "success":
        errors.append(f"{label}: shard outcome {payload.get('testOutcome')!r} is not success")
    for field in ("failures", "errors"):
        if payload.get(field) != 0:
            errors.append(f"{label}: {field}={payload.get(field)!r}")
    if payload.get("durationSemantics") != DURATION_SEMANTICS:
        errors.append(
            f"{label}: durationSemantics {payload.get('durationSemantics')!r} is not "
            f"{DURATION_SEMANTICS!r}"
        )
    cases = payload.get("cases")
    if not isinstance(cases, list):
        errors.append(f"{label}: no cases list")
    elif payload.get("tests") != len(cases):
        errors.append(
            f"{label}: incomplete observations ({len(cases)} cases for "
            f"tests={payload.get('tests')!r})"
        )
    return errors


def build_observed_hints(
    snapshots: list[tuple[str, object]],
) -> tuple[dict[str, float], tuple[str, str, str]]:
    """Validate labeled shard snapshots and return (hints, identity).

    Raises ImportRejected listing every problem found; never fabricates,
    averages, or carries forward an observation.
    """
    errors: list[str] = []
    by_suite: dict[str, list[str]] = {}
    for label, payload in snapshots:
        errors.extend(_snapshot_errors(label, payload))
        if isinstance(payload, dict) and payload.get("suite") in RELIABILITY_SUITES:
            by_suite.setdefault(payload["suite"], []).append(label)
    for suite in RELIABILITY_SUITES:
        labels = by_suite.get(suite, [])
        if not labels:
            errors.append(f"missing report for {suite}")
        elif len(labels) > 1:
            errors.append(f"more than one report for {suite}: {', '.join(labels)}")
    payloads = [payload for _, payload in snapshots if isinstance(payload, dict)]
    for field in IDENTITY_FIELDS:
        values = sorted({str(payload.get(field)) for payload in payloads})
        if len(values) > 1:
            errors.append(f"mixed {field} across shards: {', '.join(values)}")

    hints: dict[str, float] = {}
    origin: dict[str, str] = {}
    for label, payload in snapshots:
        cases = payload.get("cases") if isinstance(payload, dict) else None
        for case in cases if isinstance(cases, list) else []:
            case = case if isinstance(case, dict) else {}
            nodeid = case.get("nodeid")
            name = nodeid or case.get("junitName")
            if not (
                isinstance(nodeid, str)
                and nodeid.startswith(RELIABILITY_PREFIX)
                and "::" in nodeid
            ):
                errors.append(f"{label}: case {name!r} lacks an exact pytest node ID")
                continue
            if case.get("outcome") not in OBSERVED_OUTCOMES:
                errors.append(f"{label}: {nodeid} {case.get('outcome')!r}")
            duration = case.get("duration")
            if not _is_duration(duration):
                errors.append(f"{label}: invalid duration {duration!r} for {nodeid}")
                continue
            if nodeid in origin:
                errors.append(
                    f"{nodeid} observed more than once ({origin[nodeid]}, {label})"
                )
                continue
            origin[nodeid] = label
            hints[nodeid] = float(duration)
    if not errors and not hints:
        errors.append("the observations contain no reliability cases")
    if errors:
        shown = errors[:20]
        if len(errors) > len(shown):
            shown.append(f"... and {len(errors) - len(shown)} more")
        raise ImportRejected("\n".join(shown))
    first = payloads[0]
    return hints, (first["revision"], first["runId"], first["attempt"])


def _write_atomically(path: Path, hints: dict[str, float]) -> None:
    text = json.dumps(hints, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def import_observations(
    sources: list[Path], durations_path: Path = DURATIONS_PATH
) -> ImportResult:
    """Replace the hints with one coherent run's observations, or raise."""
    files = find_snapshots(sources)
    if not files:
        raise ImportRejected(
            f"no reliability durations snapshots ({SNAPSHOT_GLOB}) found in "
            + ", ".join(str(source) for source in sources)
        )
    snapshots: list[tuple[str, object]] = []
    for path in files:
        try:
            snapshots.append((str(path), json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError) as exc:
            raise ImportRejected(f"{path}: unreadable snapshot ({exc})") from exc
    hints, (revision, run_id, attempt) = build_observed_hints(snapshots)
    _write_atomically(durations_path, hints)
    shard_seconds = {
        payload["suite"]: sum(float(case["duration"]) for case in payload["cases"])
        for _, payload in snapshots
        if isinstance(payload, dict)
    }
    return ImportResult(revision, run_id, attempt, len(hints), shard_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate the hints file and exit 0 (usable), 2 (unusable), or 3 (missing); "
        "the default when no subcommand is given",
    )
    subcommands = parser.add_subparsers(dest="command")
    importer = subcommands.add_parser(
        "import",
        help="replace the hints with the observed costs of one complete reliability run",
    )
    importer.add_argument(
        "sources",
        nargs="+",
        type=Path,
        help=f"{SNAPSHOT_GLOB} files or directories containing them",
    )
    importer.add_argument("--durations-path", type=Path, default=DURATIONS_PATH)
    args = parser.parse_args(argv)
    if args.command != "import":
        return validate_hints_file()
    try:
        result = import_observations(args.sources, durations_path=args.durations_path)
    except ImportRejected as exc:
        print(
            f"error: observations rejected; {args.durations_path} is unchanged:\n{exc}",
            file=sys.stderr,
        )
        return 1
    print(
        f"wrote {result.node_count} observed duration hints to {args.durations_path} "
        f"from run {result.run_id} attempt {result.attempt} at revision {result.revision}"
    )
    for suite, seconds in sorted(result.shard_seconds.items()):
        print(f"  {suite}: {seconds:.1f}s observed test cost under the previous hints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
