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
    availability = sorted(
        nodeid
        for nodeid in payload
        if nodeid.startswith(
            "tests/integration/reliability/test_automatic_release_availability.py::"
        )
    )
    # The four record_source/versioning parameterizations from run #14404.
    assert len(availability) >= 4, availability
    assert any("[release-auto]" in nodeid for nodeid in availability)
    assert any("[serving-auto]" in nodeid for nodeid in availability)


def test_weight_distribution_spreads_files_across_nodes() -> None:
    """Advisory file weights divide evenly across that file's collected nodes
    (deterministic, sorted); files without collected nodes contribute nothing
    and unweighted files stay absent so the plugin fallback selects them."""
    from tools.ci.refresh_reliability_durations import distribute_weights

    result = distribute_weights(
        {"b.py": 100.0, "a.py": 60.0, "empty.py": 50.0},
        {"a.py": ["a.py::test_1", "a.py::test_2"], "b.py": ["b.py::test_1"]},
    )
    assert result == {
        "a.py::test_1": 30.0,
        "a.py::test_2": 30.0,
        "b.py::test_1": 100.0,
    }


def test_refresh_regenerates_from_collection_without_stale_entries() -> None:
    """Changed/deleted/stale hints cannot drop or duplicate tests: refresh
    output contains exactly the freshly collected nodes, so a deleted test
    simply stops being a hint and selection is unaffected (hints are
    advisory-only)."""
    from tools.ci.refresh_reliability_durations import assemble_hints

    collected = ["a.py::test_1", "a.py::test_2[param]"]
    hints = assemble_hints(collected, {"a.py": 60.0})
    assert sorted(hints) == sorted(collected)
    assert hints["a.py::test_2[param]"] == 30.0


def test_new_tests_without_history_use_no_hint_entry() -> None:
    """A newly added test has no duration history: it carries no hint entry
    and the plugin fallback still selects it exactly once (fallback coverage
    is asserted at the plugin boundary in the verifier tests)."""
    from tools.ci.refresh_reliability_durations import assemble_hints

    hints = assemble_hints(["new.py::test_brand_new"], {"other.py": 60.0})
    assert hints == {}


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
