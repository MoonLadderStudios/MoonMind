"""Reliability duration hints for pytest-split sharding (MoonLadderStudios/MoonMind#4366, #4629).

Covers the tested compatible ``pytest-split`` declaration, the committed
node-level duration hint file, and the small local validate/import helper
(``tools/ci/refresh_reliability_durations.py``). Duration history is advisory
load balancing only: missing, stale, or corrupt entries must never drop,
duplicate, or skip tests. The hints come only from observed exact-node
costs imported from one complete reliability matrix run (#4629).
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tools.ci.refresh_reliability_durations import (
    RELIABILITY_SHARD_COUNT,
    ImportRejected,
    build_observed_hints,
    import_observations,
    main,
    validate_hints_file,
)
from tools.ci.write_backend_matrix_summary import DURATION_SEMANTICS, SNAPSHOT_SCHEMA

REPO_ROOT = Path(__file__).resolve().parents[3]
DURATIONS_PATH = REPO_ROOT / "tests" / ".reliability-test-durations.json"
PREFIX = "tests/integration/reliability/"


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


def test_committed_hints_are_a_usable_pure_node_mapping() -> None:
    """The committed hint file maps test node IDs to advisory durations in
    seconds and passes the same validator every CI row runs. It carries no
    envelope keys: unknown keys would be looked up as node IDs, so the file
    stays a pure mapping and provenance lives in docs/PR evidence."""
    payload = json.loads(DURATIONS_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict) and payload, "duration hints must be a nonempty mapping"
    for nodeid in payload:
        assert isinstance(nodeid, str) and nodeid.startswith(PREFIX) and "::" in nodeid, nodeid
    assert validate_hints_file(DURATIONS_PATH) == 0


def test_file_weight_generator_and_seed_are_retired() -> None:
    """#4629 R4: one hint source. The file-average generator and its seed
    are gone so a later refresh cannot erase measured differences."""
    from tools.ci import refresh_reliability_durations as helper

    assert not (REPO_ROOT / "tools" / "ci" / "reliability_shard_weights.json").exists()
    for retired in ("distribute_weights", "assemble_hints", "_load_file_weights", "refresh"):
        assert not hasattr(helper, retired), retired


# --- validator -----------------------------------------------------------


def _validate(tmp_path: Path, content: str) -> int:
    path = tmp_path / "durations.json"
    path.write_text(content, encoding="utf-8")
    return validate_hints_file(path)


def test_validate_accepts_measured_and_genuine_zero_durations(tmp_path: Path) -> None:
    """A sub-millisecond test is genuinely ~0s in JUnit's 3-decimal time; a
    zero observation is a valid hint, not a reason to discard history."""
    assert _validate(tmp_path, json.dumps({"a.py::test_1": 12.5, "a.py::test_2": 0.0})) == 0


def test_validate_reports_missing_history_distinctly(tmp_path: Path) -> None:
    """Missing timing history is not a coverage failure: a missing file
    reports a distinct status so CI falls back to the deterministic
    no-history partition instead of failing."""
    assert validate_hints_file(tmp_path / "absent.json") == 3


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[1, 2]",
        "{}",
        json.dumps({"a.py::test_1": "slow"}),
        json.dumps({"a.py::test_1": -1.0}),
        json.dumps({"a.py::test_1": True}),
        '{"a.py::test_1": NaN}',
        '{"a.py::test_1": Infinity}',
        json.dumps({"not-a-node": 1.0}),
    ],
)
def test_validate_rejects_an_unusable_hints_file(tmp_path: Path, content: str) -> None:
    """An unusable hint file warns and falls back to the same deterministic
    no-history partition on every shard: it must never skip tests or require
    a remote timing service. Booleans and non-finite values are rejected
    deliberately (json accepts NaN/Infinity and bool is an int subtype)."""
    assert _validate(tmp_path, content) == 2


# --- observed import -----------------------------------------------------


def _case(nodeid: str | None, duration: object, outcome: str = "passed") -> dict:
    return {"nodeid": nodeid, "junitName": "legacy::name", "duration": duration, "outcome": outcome}


def _snapshot(shard: int, cases: list[dict], **overrides: object) -> dict:
    payload = {
        "schema": SNAPSHOT_SCHEMA,
        "suite": f"reliability-shard-{shard}",
        "shard": str(shard),
        "revision": "abc123",
        "runId": "777",
        "attempt": "1",
        "testOutcome": "success",
        "durationSemantics": DURATION_SEMANTICS,
        "tests": len(cases),
        "failures": 0,
        "errors": 0,
        "skipped": sum(case["outcome"] == "skipped" for case in cases),
        "time": 1.0,
        "cases": cases,
    }
    payload.update(overrides)
    return payload


def _coherent_snapshots() -> dict[int, dict]:
    return {
        1: _snapshot(
            1,
            [
                _case(f"{PREFIX}test_a.py::TestJourney::test_upgrade[pinned-v1.2]", 125.4),
                _case(f"{PREFIX}test_a.py::test_cheap", 0.0),
            ],
        ),
        2: _snapshot(2, [_case(f"{PREFIX}test_a.py::TestJourney::test_upgrade[unpinned]", 52.0)]),
        3: _snapshot(3, [_case(f"{PREFIX}test_b.py::test_b[1]", 3.25)]),
        4: _snapshot(4, [_case(f"{PREFIX}test_b.py::test_b[2]", 0.004, outcome="skipped")]),
    }


def _write_snapshots(directory: Path, snapshots: dict[int, dict]) -> Path:
    for shard, payload in snapshots.items():
        artifact = directory / f"pytest-reliability-shard-{shard}-attempt-1"
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / f"pytest-backend-reliability-shard-{shard}-durations.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    return directory


@pytest.fixture
def baseline(tmp_path: Path) -> Path:
    path = tmp_path / "hints.json"
    path.write_text(json.dumps({f"{PREFIX}test_old.py::test_x": 7.0}, indent=2) + "\n", encoding="utf-8")
    return path


def test_coherent_import_preserves_unequal_observed_costs_within_a_file(
    tmp_path: Path, baseline: Path
) -> None:
    """#4629 AC1: one explicit import replaces the baseline with observed
    exact-node costs; cheap and expensive cases of one file (and one
    parameterization) keep their own measurements instead of a file average.
    Only observed entries are written; nothing is inherited."""
    source = _write_snapshots(tmp_path / "download", _coherent_snapshots())

    result = import_observations([source], durations_path=baseline)

    hints = json.loads(baseline.read_text(encoding="utf-8"))
    assert hints == {
        f"{PREFIX}test_a.py::TestJourney::test_upgrade[pinned-v1.2]": 125.4,
        f"{PREFIX}test_a.py::TestJourney::test_upgrade[unpinned]": 52.0,
        f"{PREFIX}test_a.py::test_cheap": 0.0,
        f"{PREFIX}test_b.py::test_b[1]": 3.25,
        f"{PREFIX}test_b.py::test_b[2]": 0.004,
    }
    assert result.revision == "abc123" and result.run_id == "777" and result.attempt == "1"
    assert result.node_count == 5
    assert validate_hints_file(baseline) == 0


def _rejects(tmp_path: Path, baseline: Path, snapshots: dict, fragment: str) -> None:
    before = baseline.read_bytes()
    source = _write_snapshots(tmp_path / "download", snapshots)
    with pytest.raises(ImportRejected) as excinfo:
        import_observations([source], durations_path=baseline)
    assert fragment in str(excinfo.value)
    assert baseline.read_bytes() == before, "a rejected import must leave the baseline unchanged"
    assert sorted(p.name for p in baseline.parent.iterdir() if p.name.startswith(".")) == []


def test_missing_shard_report_cannot_replace_the_baseline(tmp_path: Path, baseline: Path) -> None:
    snapshots = _coherent_snapshots()
    del snapshots[3]
    _rejects(tmp_path, baseline, snapshots, "reliability-shard-3")


def test_duplicate_shard_reports_are_rejected(tmp_path: Path, baseline: Path) -> None:
    snapshots = _coherent_snapshots()
    _write_snapshots(tmp_path / "download" / "extra", {2: snapshots[2]})
    _rejects(tmp_path, baseline, snapshots, "more than one report")


def test_a_node_observed_twice_is_rejected(tmp_path: Path, baseline: Path) -> None:
    snapshots = _coherent_snapshots()
    snapshots[4]["cases"].append(_case(f"{PREFIX}test_b.py::test_b[1]", 1.0))
    snapshots[4]["tests"] += 1
    _rejects(tmp_path, baseline, snapshots, "observed more than once")


@pytest.mark.parametrize(
    ("field", "value"),
    [("attempt", "2"), ("revision", "def456"), ("runId", "778")],
)
def test_mixed_run_identity_is_rejected(
    tmp_path: Path, baseline: Path, field: str, value: str
) -> None:
    """Rerun attempts, revisions or runs are never mixed into one baseline."""
    snapshots = _coherent_snapshots()
    snapshots[2][field] = value
    _rejects(tmp_path, baseline, snapshots, field)


@pytest.mark.parametrize(
    "overrides",
    [
        {"testOutcome": "failure"},
        {"testOutcome": "cancelled"},
        {"failures": 1},
        {"errors": 1},
    ],
)
def test_unsuccessful_shard_cannot_replace_the_baseline(
    tmp_path: Path, baseline: Path, overrides: dict
) -> None:
    snapshots = _coherent_snapshots()
    snapshots[1].update(overrides)
    _rejects(tmp_path, baseline, snapshots, "reliability-shard-1")


def test_incomplete_case_list_is_rejected(tmp_path: Path, baseline: Path) -> None:
    """A snapshot whose cases do not match its JUnit test count is partial."""
    snapshots = _coherent_snapshots()
    snapshots[1]["tests"] = 5
    _rejects(tmp_path, baseline, snapshots, "incomplete")


def test_failed_case_is_rejected(tmp_path: Path, baseline: Path) -> None:
    snapshots = _coherent_snapshots()
    snapshots[3]["cases"][0]["outcome"] = "failed"
    _rejects(tmp_path, baseline, snapshots, "failed")


@pytest.mark.parametrize(
    "overrides",
    [
        {"durationSemantics": None},
        {"durationSemantics": "call-only"},
        {"schema": "something-else/v0"},
    ],
)
def test_incompatible_identity_or_phase_semantics_are_rejected(
    tmp_path: Path, baseline: Path, overrides: dict
) -> None:
    snapshots = _coherent_snapshots()
    snapshots[4].update(overrides)
    _rejects(tmp_path, baseline, snapshots, "reliability-shard-4")


@pytest.mark.parametrize(
    "nodeid",
    [None, "tests.integration.reliability.test_a.TestJourney::test_upgrade[x]", "test_a.py::test_x"],
)
def test_reconstructed_or_missing_node_ids_are_rejected(
    tmp_path: Path, baseline: Path, nodeid: str | None
) -> None:
    """Legacy snapshots that only carry a JUnit classname::name guess (or no
    exact node ID at all) are not a measured corpus."""
    snapshots = _coherent_snapshots()
    snapshots[2]["cases"][0]["nodeid"] = nodeid
    _rejects(tmp_path, baseline, snapshots, "exact pytest node ID")


@pytest.mark.parametrize("duration", [None, "1.5", True, False, -0.5, math.nan, math.inf])
def test_invalid_observed_durations_are_rejected(
    tmp_path: Path, baseline: Path, duration: object
) -> None:
    """Malformed, boolean, negative and non-finite values are rejected
    deliberately; they never silently become a measured zero."""
    snapshots = _coherent_snapshots()
    snapshots[3]["cases"][0]["duration"] = duration
    _rejects(tmp_path, baseline, snapshots, "duration")


def test_no_observations_found_is_rejected(tmp_path: Path, baseline: Path) -> None:
    (tmp_path / "empty").mkdir()
    before = baseline.read_bytes()
    with pytest.raises(ImportRejected, match="no reliability durations snapshots"):
        import_observations([tmp_path / "empty"], durations_path=baseline)
    assert baseline.read_bytes() == before


def test_build_observed_hints_requires_every_configured_shard() -> None:
    snapshots = _coherent_snapshots()
    assert RELIABILITY_SHARD_COUNT == 4
    hints, identity = build_observed_hints(
        [(f"shard-{n}.json", payload) for n, payload in snapshots.items()]
    )
    assert len(hints) == 5
    assert identity == ("abc123", "777", "1")


def test_import_cli_reports_rejection_without_touching_the_baseline(
    tmp_path: Path, baseline: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshots = _coherent_snapshots()
    del snapshots[1]
    source = _write_snapshots(tmp_path / "download", snapshots)
    before = baseline.read_bytes()

    status = main(["import", str(source), "--durations-path", str(baseline)])

    assert status == 1
    assert "reliability-shard-1" in capsys.readouterr().err
    assert baseline.read_bytes() == before


def test_import_cli_records_the_source_identity(
    tmp_path: Path, baseline: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _write_snapshots(tmp_path / "download", _coherent_snapshots())

    status = main(["import", str(source), "--durations-path", str(baseline)])

    assert status == 0
    out = capsys.readouterr().out
    assert "run 777" in out and "attempt 1" in out and "abc123" in out


def test_no_argument_invocation_validates_the_committed_hints() -> None:
    """The helper succeeds with no arguments: it validates the committed
    hints instead of rewriting them."""
    before = DURATIONS_PATH.read_bytes()
    proc = subprocess.run(
        [sys.executable, "tools/ci/refresh_reliability_durations.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert DURATIONS_PATH.read_bytes() == before


# --- producer-to-import round trip through real pytest ---------------------


_ROUND_TRIP_FIXTURE = '''
import time

import pytest


@pytest.fixture
def staged_dependency():
    time.sleep(0.15)
    yield
    time.sleep(0.15)


class TestJourney:
    @pytest.mark.parametrize(
        "cost",
        [pytest.param(0.4, id="expensive-v1.2"), pytest.param(0.0, id="cheap")],
    )
    def test_upgrade(self, cost):
        time.sleep(cost)


def test_setup_and_teardown_are_charged(staged_dependency):
    pass


def test_plain():
    pass
'''


def test_producer_to_import_round_trip_keeps_exact_ids_and_total_cost(
    tmp_path: Path,
) -> None:
    """#4629 AC2: real pytest-split rows (same flags as CI) feed the real
    producer; the import yields exact path/class/parameter node IDs and the
    documented setup+call+teardown cost, with unequal costs preserved."""
    corpus = tmp_path / "corpus"
    reliability = corpus / "tests" / "integration" / "reliability"
    reliability.mkdir(parents=True)
    (reliability / "test_round_trip.py").write_text(_ROUND_TRIP_FIXTURE, encoding="utf-8")
    env_path = str(REPO_ROOT)
    probe = subprocess.run(
        [sys.executable, "-m", "pytest", "--help"], capture_output=True, text=True, timeout=60
    )
    if "--splits" not in probe.stdout:
        pytest.skip("pytest-split is not installed")
    download = tmp_path / "download"
    for shard in range(1, RELIABILITY_SHARD_COUNT + 1):
        junit = tmp_path / f"junit-{shard}.xml"
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/reliability",
                "-p",
                "no:cacheprovider",
                "-p",
                "tools.ci.write_backend_matrix_summary",
                "--splits",
                str(RELIABILITY_SHARD_COUNT),
                "--group",
                str(shard),
                "--splitting-algorithm",
                "least_duration",
                f"--junitxml={junit}",
            ],
            cwd=corpus,
            env={**_env(), "PYTHONPATH": env_path},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
        artifact = download / f"pytest-reliability-shard-{shard}-attempt-3"
        produce = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "ci" / "write_backend_matrix_summary.py"),
                "--suite", f"reliability-shard-{shard}",
                "--shard", str(shard),
                "--revision", "feedface",
                "--run-id", "4629",
                "--attempt", "3",
                "--test-outcome", "success",
                "--junit", str(junit),
                "--log", str(tmp_path / "absent.log"),
                "--slowest", str(artifact / f"pytest-backend-reliability-shard-{shard}-slowest.txt"),
                "--durations-snapshot",
                str(artifact / f"pytest-backend-reliability-shard-{shard}-durations.json"),
                "--summary", str(tmp_path / f"summary-{shard}.md"),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert produce.returncode == 0, produce.stderr

    baseline = tmp_path / "hints.json"
    result = import_observations([download], durations_path=baseline)

    hints = json.loads(baseline.read_text(encoding="utf-8"))
    base = "tests/integration/reliability/test_round_trip.py::"
    assert set(hints) == {
        f"{base}TestJourney::test_upgrade[expensive-v1.2]",
        f"{base}TestJourney::test_upgrade[cheap]",
        f"{base}test_setup_and_teardown_are_charged",
        f"{base}test_plain",
    }
    assert hints[f"{base}TestJourney::test_upgrade[expensive-v1.2]"] >= 0.4
    assert hints[f"{base}TestJourney::test_upgrade[cheap]"] < 0.2
    # Fixture setup and teardown are part of the documented cost meaning.
    assert hints[f"{base}test_setup_and_teardown_are_charged"] >= 0.3
    assert (result.revision, result.run_id, result.attempt) == ("feedface", "4629", "3")


def _env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    return env
