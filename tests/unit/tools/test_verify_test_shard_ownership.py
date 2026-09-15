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
    # Round-robin stability: the first four sorted files own distinct shards.
    first_four = {
        reliability_shard_for_path(f"tests/integration/reliability/{name}")
        for name in files[:4]
    }
    assert len(first_four) == 4


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
