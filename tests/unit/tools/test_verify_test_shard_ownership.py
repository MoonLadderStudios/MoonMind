from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools.verify_test_shard_ownership import (
    RELIABILITY_SHARD_NAMES,
    CollectedNode,
    owners,
    reliability_partition,
    reliability_shard_for_path,
    verify,
)


def _node(path: str, *markers: str) -> CollectedNode:
    return CollectedNode(path, path, frozenset(markers))


def test_each_backend_shard_has_one_owner() -> None:
    nodes = [
        _node("tests/unit/services/test_a.py", "unit_fast"),
        _node("tests/unit/api/test_a.py", "component"),
        _node("tests/unit/workflows/temporal/test_a.py", "temporal_boundary"),
        _node("tests/unit/api/routers/test_agent_runs.py", "component", "slow"),
        _node("tests/integration/reliability/test_a.py", "reliability_journey"),
        _node("tests/integration/api/test_a.py", "integration", "integration_ci"),
    ]

    assert verify(nodes) == []
    assert owners(nodes[3]) == {"unit-slow"}
    # Each reliability file owns exactly one deterministic shard.
    reliability_owner = owners(nodes[4])
    assert len(reliability_owner) == 1
    assert next(iter(reliability_owner)) in set(RELIABILITY_SHARD_NAMES)
    assert next(iter(reliability_owner)) == reliability_shard_for_path(
        "tests/integration/reliability/test_a.py"
    )


def test_reliability_shards_are_deterministic_and_cover_all_files() -> None:
    from pathlib import Path

    from tools.ci.reliability_shard_partition import load_weights, partition

    repo_root = Path(__file__).resolve().parents[3]
    files = sorted(
        p.name
        for p in (repo_root / "tests" / "integration" / "reliability").glob(
            "test_*.py"
        )
    )
    assert len(files) > 4
    assignment = reliability_partition()
    assert sorted(assignment) == files
    shards = set(assignment.values())
    assert shards == set(RELIABILITY_SHARD_NAMES)
    # Duration balance (MoonLadderStudios/MoonMind#4367): estimated shard
    # loads stay within a factor of two instead of merely balancing counts.
    weights, default = load_weights()
    loads = {name: 0.0 for name in RELIABILITY_SHARD_NAMES}
    for filename, shard in assignment.items():
        loads[shard] += weights.get(filename, default)
    assert max(loads.values()) <= 2 * min(loads.values())


def test_reliability_partition_hints_are_optional() -> None:
    from tools.ci.reliability_shard_partition import partition

    # New, renamed, or stale timing entries never change selection: unknown
    # files still land on exactly one shard via the default weight.
    assignment = partition(
        candidates=["test_brand_new.py", "test_a.py"],
        weights={"test_a.py": 300.0},
        default_weight=15.0,
    )
    assert set(assignment) == {"test_brand_new.py", "test_a.py"}
    assert assignment["test_brand_new.py"] in set(RELIABILITY_SHARD_NAMES)
    assert reliability_shard_for_path(
        "tests/integration/reliability/test_brand_new.py"
    ) in set(RELIABILITY_SHARD_NAMES)


def test_partition_cli_matches_verifier_exactly_once() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    seen: dict[str, str] = {}
    for shard in range(len(RELIABILITY_SHARD_NAMES)):
        proc = subprocess.run(
            [
                sys.executable,
                "tools/ci/partition_reliability_shards.py",
                "--shard",
                str(shard),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_root,
        )
        assert proc.returncode == 0, proc.stderr
        for line in proc.stdout.split():
            assert line not in seen, f"{line} assigned twice"
            seen[line] = RELIABILITY_SHARD_NAMES[shard]
    discovered = sorted(
        f"tests/integration/reliability/{p.name}"
        for p in (repo_root / "tests" / "integration" / "reliability").glob(
            "test_*.py"
        )
    )
    assert sorted(seen) == discovered
    for path, shard in seen.items():
        assert reliability_shard_for_path(path) == shard


def test_reliability_shards_share_no_mutable_state() -> None:
    """MoonLadderStudios/MoonMind#4376: isolated shards, reusable layers."""
    import yaml

    repo_root = Path(__file__).resolve().parents[3]
    compose_path = repo_root / "tests" / "integration" / "reliability" / "compose.yaml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    # No externally shared volumes or networks: per-shard Compose project
    # names give each shard its own network/volumes while image layers are
    # reused natively from the runner cache.
    for volume in (compose.get("volumes") or {}).values():
        assert not (volume or {}).get("external", False)
    for network in (compose.get("networks") or {}).values():
        assert not (network or {}).get("external", False)
    workflow = (
        repo_root / ".github" / "workflows" / "pytest-unit-tests.yml"
    ).read_text(encoding="utf-8")
    assert "moonmind-reliability-${{ matrix.suite }}" in workflow


def test_verifier_reports_missing_duplicate_and_marker_conflicts() -> None:
    errors = verify(
        [
            _node("tests/unit/test_missing.py"),
            _node("tests/unit/test_conflict.py", "unit_fast", "slow"),
            _node(
                "tests/integration/reliability/test_overlap.py",
                "integration_ci",
                "reliability_journey",
            ),
        ]
    )

    assert any("no CI owner" in error for error in errors)
    assert any("unit_fast conflicts" in error for error in errors)
    assert any("multiple CI owners" in error for error in errors)
    assert any("integration_ci conflicts" in error for error in errors)
