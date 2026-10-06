"""Reliability duration hints for pytest-split sharding (MoonLadderStudios/MoonMind#4366).

Covers the tested compatible ``pytest-split`` declaration, the committed
node-level duration hint file, and the small local refresh/validate helper
(``tools/ci/refresh_reliability_durations.py``). Duration history is advisory
load balancing only: missing, stale, or corrupt entries must never drop,
duplicate, or skip tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomllib

REPO_ROOT = Path(__file__).resolve().parents[3]
DURATIONS_PATH = REPO_ROOT / "tests" / ".reliability-test-durations.json"
RELIABILITY_DIR = REPO_ROOT / "tests" / "integration" / "reliability"


def test_pytest_split_is_a_test_extra_dependency() -> None:
    """MoonLadderStudios/MoonMind#4366 R2: pytest-split is declared as an
    optional test dependency and part of the ``tests`` extra that CI installs
    (``uv pip install --system -e .[tests]``). The committed ``poetry.lock``
    owns the image install path (``api_service/Dockerfile`` runs
    ``poetry export --extras tests`` from the lock), so the lock must stay
    regenerated alongside this pin; a second dependency manager must
    not appear."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["tool"]["poetry"]["dependencies"]
    assert "pytest-split" in dependencies, "pytest-split missing from test dependencies"
    entry = dependencies["pytest-split"]
    assert isinstance(entry, dict) and entry.get("optional") is True
    extras = pyproject["tool"]["poetry"]["extras"]
    assert "pytest-split" in extras["tests"]
    # The tests extra stays exactly the documented test plugin set plus
    # pytest-split: no second manager, no unrelated pins touched here.
    assert set(extras["tests"]) == {
        "pytest",
        "pytest-mock",
        "pytest-asyncio",
        "pytest-xdist",
        "pytest-timeout",
        "aiosqlite",
        "pytest-split",
    }


def test_duration_hints_are_a_pure_node_mapping() -> None:
    """MoonLadderStudios/MoonMind#4366 R3: the committed hint file maps test
    node IDs to advisory durations in seconds. It carries no envelope keys:
    unknown keys would be looked up as node IDs, so the file stays a pure
    mapping and provenance lives in docs, not in the payload."""
    payload = json.loads(DURATIONS_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict) and payload, "duration hints must be a nonempty mapping"
    for nodeid, duration in payload.items():
        assert isinstance(nodeid, str) and "::" in nodeid, nodeid
        assert isinstance(duration, (int, float)) and duration > 0, (nodeid, duration)
        filename = nodeid.split("::", 1)[0]
        assert (REPO_ROOT / filename).exists(), f"hint references missing file: {nodeid}"


def test_duration_hints_cover_the_expensive_parameterizations() -> None:
    """MoonLadderStudios/MoonMind#4366 R3: the expensive parameterized cases
    (not whole files) carry per-node hints so least_duration can spread them
    across shards instead of pinning one file to one shard."""
    payload = json.loads(DURATIONS_PATH.read_text(encoding="utf-8"))
    routing = sorted(
        nodeid
        for nodeid in payload
        if nodeid.startswith(
            "tests/integration/reliability/test_release_routing_journey.py::"
            "test_candidate_canary_compare_and_set_and_inflight_upgrade["
        )
    )
    # The pinned/unpinned in-flight upgrade parameterizations from run #14404.
    assert len(routing) >= 2, routing
    assert any("[True]" in nodeid for nodeid in routing)
    assert any("[False]" in nodeid for nodeid in routing)


_SHARD_IDENTITY = {"revision": "abc123", "run_id": "37545840801", "attempt": "1"}


def _snapshot(tmp_path: Path, suite: str, cases: dict[str, float], **overrides) -> Path:
    """Write a backend-matrix durations snapshot as the evidence hook does."""
    payload = {
        "suite": suite,
        **_SHARD_IDENTITY,
        "tests": len(cases),
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time": sum(cases.values()),
        "cases": [
            {"nodeid": key, "classname": key.split("::", 1)[0], "duration": value}
            for key, value in cases.items()
        ],
    }
    payload.update(overrides)
    path = tmp_path / f"pytest-backend-{suite}-durations.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


ROUTING = "tests/integration/reliability/test_release_routing_journey.py"
ROUTING_CLASS = "tests.integration.reliability.test_release_routing_journey"


def test_junit_identity_maps_collected_nodes_with_classes_and_parameter_ids() -> None:
    from tools.ci.refresh_reliability_durations import junit_case_key

    assert junit_case_key(f"{ROUTING}::test_upgrade[True]") == f"{ROUTING_CLASS}::test_upgrade[True]"
    assert (
        junit_case_key("tests/integration/reliability/test_a.py::TestX::test_y[a::b-1]")
        == "tests.integration.reliability.test_a.TestX::test_y[a::b-1]"
    )


def test_import_keeps_measured_per_node_differences(tmp_path: Path) -> None:
    """MoonLadderStudios/MoonMind#4629: hints carry each node's observed
    JUnit total (setup + call + teardown) under its actual parameter ID, not
    the file average."""
    from tools.ci.refresh_reliability_durations import (
        assemble_measured_hints,
        load_measured_costs,
    )

    snapshots = [
        _snapshot(
            tmp_path,
            "reliability-shard-1",
            {f"{ROUTING_CLASS}::test_upgrade[True]": 118.4, f"{ROUTING_CLASS}::test_fast": 0.003},
        ),
        _snapshot(tmp_path, "reliability-shard-2", {f"{ROUTING_CLASS}::test_upgrade[False]": 9.25}),
    ]
    measured = load_measured_costs(snapshots)
    collected = [
        f"{ROUTING}::test_fast",
        f"{ROUTING}::test_upgrade[False]",
        f"{ROUTING}::test_upgrade[True]",
        f"{ROUTING}::test_brand_new",
    ]
    hints = assemble_measured_hints(collected, measured)
    assert hints == {
        f"{ROUTING}::test_upgrade[True]": 118.4,
        f"{ROUTING}::test_upgrade[False]": 9.25,
        # Positive floor keeps the hint file valid for near-zero cases.
        f"{ROUTING}::test_fast": 0.01,
    }
    # A new test without history has no hint and keeps the plugin fallback;
    # a measured node that is no longer collected is not carried forward.
    assert f"{ROUTING}::test_brand_new" not in hints


@pytest.mark.parametrize("field", ["revision", "run_id", "attempt"])
def test_import_rejects_mixed_revisions_or_attempts(tmp_path: Path, field: str) -> None:
    from tools.ci.refresh_reliability_durations import SnapshotError, load_measured_costs

    first = _snapshot(tmp_path, "reliability-shard-1", {f"{ROUTING_CLASS}::test_a": 1.0})
    second = _snapshot(
        tmp_path, "reliability-shard-2", {f"{ROUTING_CLASS}::test_b": 2.0}, **{field: "other"}
    )
    with pytest.raises(SnapshotError, match=field):
        load_measured_costs([first, second])


@pytest.mark.parametrize("field", ["revision", "run_id", "attempt"])
def test_import_rejects_snapshots_without_provenance(tmp_path: Path, field: str) -> None:
    from tools.ci.refresh_reliability_durations import SnapshotError, load_measured_costs

    path = _snapshot(tmp_path, "reliability-shard-1", {f"{ROUTING_CLASS}::test_a": 1.0}, **{field: ""})
    with pytest.raises(SnapshotError, match=field):
        load_measured_costs([path])


@pytest.mark.parametrize("counts", [{"failures": 1}, {"errors": 1}])
def test_import_rejects_partial_failed_shards(tmp_path: Path, counts: dict) -> None:
    from tools.ci.refresh_reliability_durations import SnapshotError, load_measured_costs

    path = _snapshot(tmp_path, "reliability-shard-1", {f"{ROUTING_CLASS}::test_a": 1.0}, **counts)
    with pytest.raises(SnapshotError, match="failed"):
        load_measured_costs([path])


def test_import_rejects_overlapping_shard_measurements(tmp_path: Path) -> None:
    from tools.ci.refresh_reliability_durations import SnapshotError, load_measured_costs

    case = {f"{ROUTING_CLASS}::test_a": 1.0}
    first = _snapshot(tmp_path, "reliability-shard-1", case)
    second = _snapshot(tmp_path, "reliability-shard-2", case)
    with pytest.raises(SnapshotError, match="more than once"):
        load_measured_costs([first, second])


def _refresh(monkeypatch, tmp_path: Path, collected: list[str], snapshots=None) -> dict:
    import tools.ci.refresh_reliability_durations as helper

    monkeypatch.setattr(helper, "collect_reliability_nodeids", lambda: sorted(collected))
    durations = tmp_path / "durations.json"
    if not durations.exists():
        durations.write_text(json.dumps({f"{ROUTING}::test_gone": 5.0, f"{ROUTING}::test_kept": 42.5}))
    helper.refresh(durations, snapshots=snapshots)
    return json.loads(durations.read_text())


def test_refresh_without_snapshots_preserves_measured_values(monkeypatch, tmp_path: Path) -> None:
    """Refresh cannot revert measured values to file averages: without new
    measurements it only prunes nodes that are no longer collected."""
    hints = _refresh(monkeypatch, tmp_path, [f"{ROUTING}::test_kept", f"{ROUTING}::test_new"])
    assert hints == {f"{ROUTING}::test_kept": 42.5}


def test_refresh_imports_snapshots_through_the_cli(monkeypatch, tmp_path: Path) -> None:
    import tools.ci.refresh_reliability_durations as helper

    snapshot = _snapshot(
        tmp_path, "reliability-shard-1", {f"{ROUTING_CLASS}::test_kept": 7.5}
    )
    durations = tmp_path / "durations.json"
    monkeypatch.setattr(helper, "DURATIONS_PATH", durations)
    monkeypatch.setattr(helper, "collect_reliability_nodeids", lambda: [f"{ROUTING}::test_kept"])
    assert helper.main(["--from-snapshots", str(snapshot)]) == 0
    assert json.loads(durations.read_text()) == {f"{ROUTING}::test_kept": 7.5}


def test_refresh_rejects_incoherent_snapshots_without_writing(monkeypatch, tmp_path: Path) -> None:
    import tools.ci.refresh_reliability_durations as helper

    first = _snapshot(tmp_path, "reliability-shard-1", {f"{ROUTING_CLASS}::test_kept": 1.0})
    second = _snapshot(
        tmp_path, "reliability-shard-2", {f"{ROUTING_CLASS}::test_new": 2.0}, attempt="2"
    )
    durations = tmp_path / "durations.json"
    durations.write_text(json.dumps({f"{ROUTING}::test_kept": 42.5}))
    monkeypatch.setattr(helper, "DURATIONS_PATH", durations)
    monkeypatch.setattr(helper, "collect_reliability_nodeids", lambda: [f"{ROUTING}::test_kept"])
    assert helper.main(["--from-snapshots", str(first), str(second)]) == 1
    assert json.loads(durations.read_text()) == {f"{ROUTING}::test_kept": 42.5}


def test_file_average_regeneration_is_retired() -> None:
    import tools.ci.refresh_reliability_durations as helper

    assert not hasattr(helper, "distribute_weights")
    assert not (REPO_ROOT / "tools" / "ci" / "reliability_shard_weights.json").exists()


def _validate(path: Path) -> int:
    from tools.ci.refresh_reliability_durations import validate_hints_file

    return validate_hints_file(path)


def test_validate_accepts_a_well_formed_hints_file(tmp_path: Path) -> None:
    path = tmp_path / "durations.json"
    path.write_text(json.dumps({"a.py::test_1": 12.5}), encoding="utf-8")
    assert _validate(path) == 0


def test_validate_reports_missing_history_distinctly(tmp_path: Path) -> None:
    """Missing timing history is not a coverage failure: a missing file
    reports a distinct status so CI falls back to the deterministic
    no-history partition instead of failing."""
    assert _validate(tmp_path / "absent.json") == 3


@pytest.mark.parametrize(
    "content",
    ["{not json", "[1, 2]", json.dumps({"a.py::test_1": "slow"}), json.dumps({"a.py::test_1": -1.0})],
)
def test_validate_rejects_an_unusable_hints_file(tmp_path: Path, content: str) -> None:
    """An unusable hint file warns and falls back to the same deterministic
    no-history partition on every shard: it must never skip tests or require
    a remote timing service."""
    path = tmp_path / "durations.json"
    path.write_text(content, encoding="utf-8")
    assert _validate(path) == 2
