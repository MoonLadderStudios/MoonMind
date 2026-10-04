"""Reliability duration hints for pytest-split sharding (MoonLadderStudios/MoonMind#4366).

Covers the tested compatible ``pytest-split`` declaration, the committed
node-level duration hint file, and the small local refresh/validate helper
(``tools/ci/refresh_reliability_durations.py``). Duration history is advisory
load balancing only: missing, stale, or corrupt entries must never drop,
duplicate, or skip tests.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tomllib
from pathlib import Path

import pytest

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
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
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
    assert (
        isinstance(payload, dict) and payload
    ), "duration hints must be a nonempty mapping"
    for nodeid, duration in payload.items():
        assert isinstance(nodeid, str) and "::" in nodeid, nodeid
        from tools.ci.refresh_reliability_durations import valid_duration

        assert valid_duration(duration), (nodeid, duration)


def _digest(nodes: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(nodes), separators=(",", ":")).encode()
    ).hexdigest()


@pytest.fixture
def measured_reports(tmp_path: Path) -> list[Path]:
    """#4629: coherent reports, with unequal classes/parameters in one file."""
    nodes = [
        f"tests/integration/reliability/test_cost.py::TestCost::test_run[{p}]"
        for p in ("cheap.a", "expensive/b", "zero", "tiny")
    ]
    paths = []
    for group, (nodeid, duration) in enumerate(zip(nodes, (0.01, 12.5, 0.0, 1e-9)), 1):
        recording = {
            "revision": "abc123",
            "run_id": "run-4629",
            "attempt": "1",
            "shard": group,
            "shard_count": 4,
            "collection_args": ["tests/integration/reliability"],
            "marker_expr": "reliability_journey and not provider_verification and not requires_credentials",
            "keyword_expr": "",
            "deselected_args": [],
            "algorithm": "least_duration",
            "universe_count": 4,
            "universe_digest": _digest(nodes),
            "selected_count": 1,
            "selected_digest": _digest([nodeid]),
            "hints_digest": "same-hints",
            "hints_digest_after": "same-hints",
            "baseline_digest": "same-baseline",
            "baseline_digest_after": "same-baseline",
            "exit_code": 0,
        }
        payload = {
            "schema_version": 1,
            "suite": f"reliability-shard-{group}",
            "identity": "pytest-nodeid",
            "cost_semantics": "pytest-report/setup+call+teardown",
            "source": {
                "revision": "abc123",
                "run_id": "run-4629",
                "attempt": "1",
                "shard": str(group),
                "selected": True,
                "test_outcome": "success",
            },
            "recording": recording,
            "tests": 1,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "cases": [
                {
                    "nodeid": nodeid,
                    "duration": duration,
                    "observed": True,
                    "phases": {"setup": 0.0, "call": duration, "teardown": 0.0},
                }
            ],
        }
        path = tmp_path / f"shard-{group}.json"
        path.write_text(json.dumps(payload))
        paths.append(path)
    return paths


def _refresh(reports: list[Path], baseline: Path) -> int:
    from tools.ci.refresh_reliability_durations import main

    return main(
        [
            "--import-reports",
            *map(str, reports),
            "--run-id",
            "run-4629",
            "--revision",
            "abc123",
            "--attempt",
            "1",
            "--output",
            str(baseline),
        ]
    )


def test_refresh_preserves_observed_differences_and_source(
    measured_reports, tmp_path, capsys
):
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"stale.py::test_removed": 52.0}\n')
    assert _refresh(measured_reports, baseline) == 0
    hints = json.loads(baseline.read_text())
    assert list(hints.values()) == [0.01, 12.5, 1e-9, 0.0]
    assert "run-4629" in capsys.readouterr().out
    assert "stale.py::test_removed" not in hints


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "duplicate-report",
        "duplicate-node",
        "missing-case",
        "revision",
        "attempt",
        "run",
        "failed",
        "skipped",
        "identity",
        "semantics",
        "inherited",
        "count",
        "corpus",
        "selection",
        "marker",
        "keyword",
        "partial-path",
        "hints",
        "mutated-hints",
        "mutated-baseline",
        "missing-phase",
        "phase-total",
        "unknown-schema",
        "non-node",
        "source-mismatch",
    ],
)
def test_incoherent_import_preserves_baseline(
    measured_reports, tmp_path, defect, capsys
):
    reports = measured_reports.copy()
    path = reports[0]
    payload = json.loads(path.read_text())
    recording = payload["recording"]
    case = payload["cases"][0]
    if defect == "missing":
        reports.pop()
    elif defect == "duplicate-report":
        reports[-1] = reports[0]
    elif defect == "duplicate-node":
        case["nodeid"] = json.loads(reports[1].read_text())["cases"][0]["nodeid"]
    elif defect == "missing-case":
        payload["cases"] = []
    elif defect == "revision":
        recording["revision"] = "other"
    elif defect == "attempt":
        recording["attempt"] = "2"
    elif defect == "run":
        recording["run_id"] = "other"
    elif defect == "failed":
        recording["exit_code"] = 1
    elif defect == "skipped":
        payload["skipped"] = 1
    elif defect == "identity":
        payload["identity"] = "junit-classname"
    elif defect == "semantics":
        payload["cost_semantics"] = "call-only"
    elif defect == "inherited":
        case["observed"] = False
    elif defect == "count":
        payload["tests"] = 99
    elif defect == "corpus":
        recording["universe_digest"] = "other"
    elif defect == "selection":
        recording["selected_digest"] = "other"
    elif defect == "marker":
        recording["marker_expr"] = "reliability_journey"
    elif defect == "keyword":
        recording["keyword_expr"] = "cheap"
    elif defect == "partial-path":
        recording["collection_args"] = ["tests/integration/reliability/test_cost.py"]
    elif defect == "hints":
        recording["hints_digest"] = recording["hints_digest_after"] = "other"
    elif defect == "mutated-hints":
        recording["hints_digest_after"] = "other"
    elif defect == "mutated-baseline":
        recording["baseline_digest_after"] = "other"
    elif defect == "missing-phase":
        case["phases"].pop("setup")
    elif defect == "phase-total":
        case["phases"]["call"] = 1.0
    elif defect == "unknown-schema":
        payload["schema_version"] = 999
    elif defect == "non-node":
        case["nodeid"] = "mod.TestCost::test_run"
    elif defect == "source-mismatch":
        payload["source"]["revision"] = "other"
    path.write_text(json.dumps(payload))
    baseline = tmp_path / "baseline.json"
    original = b'{"old.py::test_old": 7}\n'
    baseline.write_bytes(original)
    assert _refresh(reports, baseline) == 1
    assert baseline.read_bytes() == original
    assert "unchanged" in capsys.readouterr().err


@pytest.mark.parametrize(
    "duration",
    [
        "slow",
        "0.2",
        None,
        True,
        False,
        -0.1,
        float("nan"),
        float("inf"),
        -float("inf"),
        10**400,
    ],
)
def test_invalid_import_values_preserve_baseline(measured_reports, tmp_path, duration):
    path = measured_reports[0]
    payload = json.loads(path.read_text())
    payload["cases"][0]["duration"] = duration
    payload["cases"][0]["phases"]["call"] = duration
    path.write_text(json.dumps(payload))
    baseline = tmp_path / "baseline.json"
    baseline.write_text("existing baseline\n")
    assert _refresh(measured_reports, baseline) == 1
    assert baseline.read_text() == "existing baseline\n"


def test_refresh_write_failure_preserves_baseline(
    measured_reports, tmp_path, monkeypatch
):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("existing baseline\n")

    def fail_replace(*args):
        raise OSError("replacement unavailable")

    monkeypatch.setattr("os.replace", fail_replace)
    assert _refresh(measured_reports, baseline) == 1
    assert baseline.read_text() == "existing baseline\n"


def test_refresh_success_does_not_depend_on_cleanup(
    measured_reports, tmp_path, monkeypatch
):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("existing baseline\n")

    def fail_cleanup(*args, **kwargs):
        raise OSError("cleanup unavailable")

    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    assert _refresh(measured_reports, baseline) == 0
    assert len(json.loads(baseline.read_text())) == 4


def test_equivalent_complete_suite_import(measured_reports, tmp_path):
    payloads = [json.loads(p.read_text()) for p in measured_reports]
    payload = copy.deepcopy(payloads[0])
    payload["cases"] = [p["cases"][0] for p in payloads]
    payload["tests"] = 4
    payload["suite"] = "reliability"
    payload["source"]["shard"] = ""
    payload["recording"].update(
        shard_count=1,
        selected_count=4,
        selected_digest=payload["recording"]["universe_digest"],
    )
    path = tmp_path / "full-suite.json"
    path.write_text(json.dumps(payload))
    baseline = tmp_path / "baseline.json"
    assert _refresh([path], baseline) == 0
    assert len(json.loads(baseline.read_text())) == 4


@pytest.mark.parametrize(
    "content", ["{broken", "[1, 2]", '{"schema_version": 1, "schema_version": 1}']
)
def test_malformed_report_does_not_replace_baseline(
    measured_reports, tmp_path, content
):
    measured_reports[0].write_text(content)
    baseline = tmp_path / "baseline.json"
    baseline.write_text("existing baseline\n")
    assert _refresh(measured_reports, baseline) == 1
    assert baseline.read_text() == "existing baseline\n"


def test_default_command_only_validates_existing_hints(tmp_path):
    from tools.ci.refresh_reliability_durations import main

    baseline = tmp_path / "hints.json"
    baseline.write_text('{"a.py::test_cost": 0.01}\n')
    original = baseline.read_bytes()
    assert main(["--output", str(baseline)]) == 0
    assert baseline.read_bytes() == original
    assert main(["--output", str(tmp_path / "missing.json")]) == 0


def test_cli_fallback_supplies_an_absent_path(tmp_path, capsys):
    from tools.ci.refresh_reliability_durations import main

    assert (
        main(["--pytest-duration-path", "--output", str(tmp_path / "missing.json")])
        == 0
    )
    path = Path(capsys.readouterr().out.strip())
    assert path.is_absolute() and not path.exists()


def _validate(path: Path) -> int:
    from tools.ci.refresh_reliability_durations import validate_hints_file

    return validate_hints_file(path)


def test_validate_accepts_a_well_formed_hints_file(tmp_path: Path) -> None:
    path = tmp_path / "durations.json"
    path.write_text(json.dumps({"a.py::test_1": 12.5}), encoding="utf-8")
    assert _validate(path) == 0


def test_validate_preserves_zero_and_tiny_durations(tmp_path):
    path = tmp_path / "durations.json"
    path.write_text(json.dumps({"a.py::test_zero": 0.0, "a.py::test_tiny": 1e-9}))
    assert _validate(path) == 0


def test_validate_reports_missing_history_distinctly(tmp_path: Path) -> None:
    """Missing timing history is not a coverage failure: a missing file
    reports a distinct status so CI falls back to the deterministic
    no-history partition instead of failing."""
    assert _validate(tmp_path / "absent.json") == 3


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[1, 2]",
        *[
            json.dumps({"a.py::test_1": value})
            for value in ("slow", -1.0, True, False, float("inf"), float("nan"))
        ],
    ],
)
def test_validate_rejects_an_unusable_hints_file(tmp_path: Path, content: str) -> None:
    """An unusable hint file warns and falls back to the same deterministic
    no-history partition on every shard: it must never skip tests or require
    a remote timing service."""
    path = tmp_path / "durations.json"
    path.write_text(content, encoding="utf-8")
    assert _validate(path) == 2
