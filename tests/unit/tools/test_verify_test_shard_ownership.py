from __future__ import annotations

import subprocess

import pytest

from tools.verify_test_shard_ownership import (
    RELIABILITY_MATRIX_OWNER,
    RELIABILITY_SHARD_NAMES,
    CollectedNode,
    PluginUnavailable,
    collect_group_node_ids,
    owners,
    verify,
    verify_physical_partitions,
    verify_physical_reliability_partitions,
)
from tools.verify_test_shard_ownership import (
    _run_collection_command as _real_run_collection_command,
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
    # Reliability nodes share one logical owner (the matrix job). Physical
    # exact-once across groups 1-4 is proven through the actual
    # pytest-split CLI, not through a static per-file mapping.
    assert owners(nodes[4]) == {RELIABILITY_MATRIX_OWNER}
    assert len(RELIABILITY_SHARD_NAMES) == 4


def test_parameterized_and_new_reliability_cases_keep_the_matrix_owner() -> None:
    """Parameterized expansions of one file and newly added files stay on
    the matrix owner. Duration history only balances load: it never moves a
    case off the corpus or skips it."""
    first = CollectedNode(
        nodeid="tests/integration/reliability/test_a.py::test_x[param-1]",
        path="tests/integration/reliability/test_a.py",
        markers=frozenset({"reliability_journey"}),
    )
    second = CollectedNode(
        nodeid="tests/integration/reliability/test_a.py::test_x[param-2]",
        path="tests/integration/reliability/test_a.py",
        markers=frozenset({"reliability_journey"}),
    )
    assert owners(first) == owners(second) == {RELIABILITY_MATRIX_OWNER}
    assert verify([first, second]) == []

    fresh = _node(
        "tests/integration/reliability/test_brand_new_4377.py",
        "reliability_journey",
    )
    assert owners(fresh) == {RELIABILITY_MATRIX_OWNER}
    assert verify([fresh]) == []


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


def _reliability_nodes() -> list[CollectedNode]:
    return [
        CollectedNode(
            nodeid=f"tests/integration/reliability/test_a.py::test_{name}",
            path="tests/integration/reliability/test_a.py",
            markers=frozenset({"reliability_journey"}),
        )
        for name in ("one", "two", "three", "four")
    ]


def _groups_for(nodes: list[CollectedNode]) -> dict[int, set[str]]:
    universe = [node.nodeid for node in nodes]
    return {
        1: set(universe[:2]),
        2: set(universe[2:3]),
        3: set(universe[3:]),
        4: set(),
    }


def test_physical_groups_must_cover_the_universe_exactly_once() -> None:
    nodes = _reliability_nodes()
    universe = {node.nodeid for node in nodes}
    assert verify_physical_partitions(universe, _groups_for(nodes)) == []


def test_physical_groups_report_a_dropped_node() -> None:
    """Negative control: a node missing from every group fails the owning
    coverage check instead of silently dropping a test."""
    nodes = _reliability_nodes()
    universe = {node.nodeid for node in nodes}
    groups = _groups_for(nodes)
    dropped = next(iter(groups[1]))
    groups[1].discard(dropped)
    errors = verify_physical_partitions(universe, groups)
    assert any(dropped in error and "missing" in error for error in errors)


def test_physical_groups_report_a_duplicated_node() -> None:
    """Negative control: a node selected by two groups fails instead of
    running (and mutating hermetic state) twice."""
    nodes = _reliability_nodes()
    universe = {node.nodeid for node in nodes}
    groups = _groups_for(nodes)
    duplicated = next(iter(groups[1]))
    groups[2].add(duplicated)
    errors = verify_physical_partitions(universe, groups)
    assert any(duplicated in error for error in errors)


def test_physical_check_uses_the_plugin_cli_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verifier exercises the actual pytest-split CLI collection rather
    than copying its algorithm into a second scheduler."""
    seen: list[list[str]] = []

    def fake_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        group = argv[argv.index("--group") + 1]
        node = f"tests/integration/reliability/test_a.py::test_{group}"
        return subprocess.CompletedProcess(argv, 0, stdout=node + "\n", stderr="")

    monkeypatch.setattr(
        "tools.verify_test_shard_ownership._run_collection_command", fake_run
    )
    nodes = [
        CollectedNode(
            nodeid=f"tests/integration/reliability/test_a.py::test_{group}",
            path="tests/integration/reliability/test_a.py",
            markers=frozenset({"reliability_journey"}),
        )
        for group in ("1", "2", "3", "4")
    ]
    errors, skipped = verify_physical_reliability_partitions(nodes)
    assert errors == []
    assert skipped is False
    assert len(seen) == 4
    for argv in seen:
        assert "--splits" in argv and "4" in argv
        assert "--splitting-algorithm" in argv and "least_duration" in argv


def test_physical_check_skips_when_the_plugin_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing plugin is a skipped physical check with a warning, never a
    silently green exact-once proof."""

    def fake_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 4, stdout="", stderr="unrecognized arguments: --splits"
        )

    monkeypatch.setattr(
        "tools.verify_test_shard_ownership._run_collection_command", fake_run
    )
    errors, skipped = verify_physical_reliability_partitions(_reliability_nodes())
    assert errors == []
    assert skipped is True


def test_plugin_unavailable_propagates_from_group_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise PluginUnavailable("pytest-split is not installed")

    monkeypatch.setattr(
        "tools.verify_test_shard_ownership._run_collection_command", fake_run
    )
    with pytest.raises(PluginUnavailable):
        collect_group_node_ids(1, durations_args=[])


def test_plugin_cli_partitions_a_fixture_directory_exactly_once(
    tmp_path,
) -> None:
    """End-to-end guard through the real plugin CLI on a tiny fixture
    corpus: the union of --group partitions equals the unsharded universe
    and pairwise intersections are empty. Skipped where the plugin is not
    installed (e.g. hermetic local runs); CI installs it via .[tests]."""
    import sys

    (tmp_path / "test_small_a.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('p', ['one', 'two'])\n"
        "def test_alpha(p):\n    pass\n"
        "def test_beta():\n    pass\n"
    )
    base = [sys.executable, "-m", "pytest"]
    argv = base + [
        "--collect-only",
        "-q",
        "--splits",
        "2",
        "--group",
        "1",
        "--splitting-algorithm",
        "least_duration",
        "-p",
        "no:cacheprovider",
    ]
    probe = _real_run_collection_command(base + ["--help"])
    if "--splits" not in (probe.stdout + probe.stderr):
        pytest.skip("pytest-split is not installed")
    groups: dict[int, set[str]] = {}
    for group in (1, 2):
        argv[argv.index("--group") + 1] = str(group)
        proc = _real_run_collection_command(argv, cwd=tmp_path)
        assert proc.returncode in (0, 5), proc.stderr[-500:]
        groups[group] = {
            line.strip() for line in proc.stdout.splitlines() if "::" in line
        }
    universe = set().union(*groups.values())
    assert len(universe) == 3
    assert verify_physical_partitions(universe, groups) == []


def test_plugin_cli_runs_a_history_less_node_exactly_once(tmp_path) -> None:
    """MoonLadderStudios/MoonMind#4366 A2: a collected node absent from the
    durations hints still executes exactly once through the real --group
    collections (plugin fallback, not a skip or duplicate). Skipped where
    pytest-split is not installed; CI installs it via .[tests]."""
    import json
    import sys

    (tmp_path / "test_hist_a.py").write_text(
        "def test_one():\n    pass\ndef test_two():\n    pass\n"
    )
    (tmp_path / "test_brand_new.py").write_text(
        "def test_brand_new():\n    pass\n"
    )
    base = [sys.executable, "-m", "pytest"]
    probe = _real_run_collection_command(base + ["--help"])
    if "--splits" not in (probe.stdout + probe.stderr):
        pytest.skip("pytest-split is not installed")
    # Unsharded universe for this fixture directory.
    proc = _real_run_collection_command(
        base + ["--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
    )
    assert proc.returncode in (0, 5), proc.stderr[-500:]
    universe = {
        line.strip() for line in proc.stdout.splitlines() if "::" in line
    }
    assert len(universe) == 3
    new_node = next(node for node in universe if "test_brand_new" in node)
    # Hints cover only the pre-existing nodes; the new node has no history.
    hints = {
        node: 10.0 for node in sorted(universe) if node != new_node
    }
    durations_path = tmp_path / "durations.json"
    durations_path.write_text(json.dumps(hints), encoding="utf-8")
    groups: dict[int, set[str]] = {}
    for group in (1, 2, 3, 4):
        proc = _real_run_collection_command(
            base
            + [
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
                "--splits",
                "4",
                "--group",
                str(group),
                "--splitting-algorithm",
                "least_duration",
                "--durations-path",
                str(durations_path),
            ],
            cwd=tmp_path,
        )
        assert proc.returncode in (0, 5), proc.stderr[-500:]
        groups[group] = {
            line.strip() for line in proc.stdout.splitlines() if "::" in line
        }
    assert verify_physical_partitions(universe, groups) == []
    appearances = sum(new_node in nodes for nodes in groups.values())
    assert appearances == 1
