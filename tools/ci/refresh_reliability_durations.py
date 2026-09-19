#!/usr/bin/env python3
"""Refresh and validate reliability duration hints (MoonLadderStudios/MoonMind#4366).

``tests/.reliability-test-durations.json`` maps reliability test node IDs to
advisory durations in seconds for pytest-split ``--splitting-algorithm
least_duration``. Duration history is advisory load balancing only: missing,
stale, corrupt, or incomplete entries must never drop, duplicate, or skip
tests -- unknown nodes fall back to the plugin estimate and still run exactly
once.

Subcommands (this file is the single local helper; keep it small):

- ``refresh`` (default): collect the eligible reliability universe and
  rewrite the hints file from the file-level seeds in
  ``tools/ci/reliability_shard_weights.json`` (seeded from CI run #14404),
  distributing each file's weight evenly across its collected nodes.
  Regenerating from collection means deleted tests simply stop being hints.
- ``--validate-only``: exit 0 when the hints file is usable, 2 when it is
  present but unusable (warn and fall back to the deterministic no-history
  partition on every shard), 3 when it is missing or empty (missing timing
  history is not a coverage failure).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RELIABILITY_DIR = REPO_ROOT / "tests" / "integration" / "reliability"
WEIGHTS_PATH = Path(__file__).resolve().parent / "reliability_shard_weights.json"
DURATIONS_PATH = REPO_ROOT / "tests" / ".reliability-test-durations.json"

# Mirrors the eligible reliability universe in
# tools/verify_test_shard_ownership.py: reliability journeys without provider
# or credential requirements.
RELIABILITY_MARKER_EXPR = (
    "reliability_journey and not provider_verification and not requires_credentials"
)

# Exit statuses for --validate-only (consumed by the CI reliability rows).
VALID, UNUSABLE, MISSING = 0, 2, 3


def _load_file_weights(weights_path: Path = WEIGHTS_PATH) -> dict[str, float]:
    """Return per-filename advisory weights. Never raises."""
    try:
        payload = json.loads(weights_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    weights: dict[str, float] = {}
    raw = payload.get("weights", {})
    if isinstance(raw, dict):
        for name, value in raw.items():
            try:
                weight = float(value)
            except (TypeError, ValueError):
                continue
            if weight > 0:
                weights[str(name)] = weight
    return weights


def distribute_weights(
    file_weights: dict[str, float], file_nodes: dict[str, list[str]]
) -> dict[str, float]:
    """Divide each file's advisory weight evenly across its collected nodes.

    Deterministic (sorted input order, rounded shares). Files without
    collected nodes contribute nothing; unweighted files stay absent so the
    plugin fallback selects them.
    """
    hints: dict[str, float] = {}
    for filename in sorted(file_nodes):
        weight = file_weights.get(filename, 0.0)
        nodes = file_nodes[filename]
        if weight <= 0 or not nodes:
            continue
        share = round(weight / len(nodes), 2)
        if share <= 0:
            continue
        for nodeid in sorted(nodes):
            hints[nodeid] = share
    return hints


def assemble_hints(
    collected_nodeids: list[str], file_weights: dict[str, float]
) -> dict[str, float]:
    """Build the hints mapping for exactly the freshly collected nodes."""
    file_nodes: dict[str, list[str]] = {}
    for nodeid in collected_nodeids:
        filename = nodeid.split("::", 1)[0].rsplit("/", 1)[-1]
        file_nodes.setdefault(filename, []).append(nodeid)
    return distribute_weights(file_weights, file_nodes)


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


def refresh(
    durations_path: Path = DURATIONS_PATH, weights_path: Path = WEIGHTS_PATH
) -> int:
    """Rewrite the hints file from fresh collection. Returns node count."""
    nodeids = collect_reliability_nodeids()
    hints = assemble_hints(nodeids, _load_file_weights(weights_path))
    durations_path.write_text(
        json.dumps(hints, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(hints)} duration hints for {len(nodeids)} collected nodes")
    return len(nodeids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate the hints file and exit 0 (usable), 2 (unusable), or 3 (missing)",
    )
    args = parser.parse_args(argv)
    if args.validate_only:
        return validate_hints_file()
    refresh()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
