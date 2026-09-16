from __future__ import annotations

from tools.verify_test_shard_ownership import (
    RELIABILITY_SHARD_NAMES,
    CollectedNode,
    owners,
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

    from tools.ci.reliability_shard_partition import (
        SHARD_NAMES,
        files_for_shard,
        partition,
    )

    repo_root = Path(__file__).resolve().parents[3]
    files = sorted(
        p.name
        for p in (repo_root / "tests" / "integration" / "reliability").glob(
            "test_*.py"
        )
    )
    assert len(files) > 4
    shards = {
        reliability_shard_for_path(f"tests/integration/reliability/{name}")
        for name in files
    }
    assert shards == set(RELIABILITY_SHARD_NAMES)
    # Single-authority exact-once assignment: the verifier agrees with the
    # partition module on every file, and every file is owned exactly once.
    assignment = partition()
    assert sorted(sum((sorted(m) for m in assignment.values()), [])) == files
    assert set(assignment) == set(SHARD_NAMES)
    for index in range(len(SHARD_NAMES)):
        for name in files_for_shard(index):
            assert (
                reliability_shard_for_path(f"tests/integration/reliability/{name}")
                == SHARD_NAMES[index]
            )
    # Determinism: repeated resolution is stable.
    for name in files:
        path = f"tests/integration/reliability/{name}"
        assert reliability_shard_for_path(path) == reliability_shard_for_path(path)
    # New files absent from the weights file are still selected (default
    # weight), never skipped.
    assert reliability_shard_for_path(
        "tests/integration/reliability/test_brand_new_unweighted_case.py"
    ) in set(RELIABILITY_SHARD_NAMES)


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
