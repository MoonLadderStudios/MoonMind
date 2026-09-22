"""Passive repository secret-exposure scan through existing job/artifact path.

MoonLadderStudios/MoonMind#3970: one useful read-only security analysis on an
authorized snapshot, reusing the existing outbound-scan regex contract, the
container-job workload contract, and the artifact store. No external feed, no
new scanner runtime, no universal findings model.
"""

from __future__ import annotations

import json
from pathlib import Path

from moonmind.schemas.container_job_models import ContainerJobSpec
from moonmind.security.passive_repo_scan import (
    PASSIVE_SCAN_TOOL_REF,
    PassiveScanConfig,
    build_scan_job_workload,
    parse_scan_report_json,
    retain_scan_report,
    run_passive_scan,
)


def _load_in_memory_artifact_store():
    """Return the real InMemoryArtifactStore, even in dependency-thin envs.

    The canonical import executes the full ``moonmind.workflows`` package
    ``__init__`` (temporal/boto hatred); in environments without those
    optional heavy dependencies, load the real ``skill_plan_contracts`` and
    ``artifact_store`` source files under a stub parent package so the
    production artifact code itself is still what the test exercises.
    """

    try:
        from moonmind.workflows.skills.artifact_store import (  # noqa: PLC0415
            InMemoryArtifactStore,
        )

        return InMemoryArtifactStore
    except ImportError:
        pass
    import importlib
    import sys
    import types

    skills_dir = Path(__file__).resolve().parents[3] / "moonmind" / "workflows" / "skills"
    workflows_dir = skills_dir.parent
    for name, path in (
        ("moonmind.workflows", workflows_dir),
        ("moonmind.workflows.skills", skills_dir),
    ):
        stub = sys.modules.get(name)
        if stub is None or not hasattr(stub, "__path__"):
            stub = types.ModuleType(name)
            stub.__path__ = [str(path)]  # type: ignore[attr-defined]
            sys.modules[name] = stub
    module = importlib.import_module("moonmind.workflows.skills.artifact_store")
    return module.InMemoryArtifactStore


InMemoryArtifactStore = _load_in_memory_artifact_store()


def _write(path: Path, content: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def test_finding_present_fixture_reports_secret_exposure_without_raw_secret(
    tmp_path: Path,
) -> None:
    raw_secret = "ghp_" + "A" * 36
    _write(tmp_path / "app.py", f"token = '{raw_secret}'\n")
    report = run_passive_scan(tmp_path)

    assert report.verdict == "finding_present"
    assert report.tool_ref == PASSIVE_SCAN_TOOL_REF
    assert report.input_digest
    assert report.feed["name"] == "none"
    assert report.files_scanned >= 1
    assert len(report.findings) >= 1
    dumped = json.dumps(report.to_payload())
    assert raw_secret not in dumped
    assert raw_secret not in report.summary_markdown
    assert "whole repository is secure" in report.summary_markdown  # scope disclaimer
    assert report.summary_markdown  # readable summary retained


def test_clean_fixture_with_declared_coverage_is_not_called_secure(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "main.py", "print('hello world')\n")
    _write(tmp_path / "notes.md", "# hello\n")
    report = run_passive_scan(tmp_path)

    assert report.verdict == "clean_with_coverage"
    assert report.findings == []
    assert report.files_skipped == []
    assert len(report.covered_files) == 2
    assert "whole repository is secure" in report.summary_markdown  # scope disclaimer
    assert "no risk" not in report.summary_markdown.lower()
    assert "repository is clean" not in report.summary_markdown.lower()


def test_incomplete_fixture_never_reports_clean(tmp_path: Path) -> None:
    _write(tmp_path / "ok.py", "print('ok')\n")
    _write(tmp_path / "huge.py", "x = 1\n" + "y = 2\n" * 100000)
    _write(tmp_path / "blob.bin", b"\x00\x01\x02binary\xff\xfe" * 100)
    report = run_passive_scan(
        tmp_path,
        config=PassiveScanConfig(max_bytes_per_file=1024, max_total_bytes=1024 * 1024),
    )

    assert report.verdict == "incomplete"
    assert report.files_skipped, "oversized/binary input must be recorded as skipped"
    assert report.incomplete_reason
    dumped = json.dumps(report.to_payload())
    assert '"verdict": "clean' not in dumped


def test_unsafe_input_is_rejected_without_clean_result(tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    _write(outside, "print('outside')\n")
    link = tmp_path / "snapshot" / "escape.py"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except OSError:
        # Symlink creation unavailable; path traversal still must be rejected.
        pass
    report = run_passive_scan(tmp_path / "snapshot")
    assert report.verdict in {"clean_with_coverage", "incomplete"}
    if link.is_symlink():
        assert report.verdict == "incomplete"

    rejected = run_passive_scan(tmp_path / "does-not-exist")
    assert rejected.verdict == "incomplete"
    assert rejected.incomplete_reason


def test_malformed_and_truncated_output_never_become_clean() -> None:
    assert parse_scan_report_json(b"not json{{").verdict == "incomplete"
    assert parse_scan_report_json(b'{"verdict": "clean_with_coverage"').verdict == "incomplete"
    truncated = json.dumps(
        {
            "toolRef": PASSIVE_SCAN_TOOL_REF,
            "verdict": "clean_with_coverage",
            "findings": [{"category": "x"}],
        }
    ).encode()[:40]
    assert parse_scan_report_json(truncated).verdict == "incomplete"


def test_timeout_and_cancellation_are_bounded_incomplete(tmp_path: Path) -> None:
    for index in range(5):
        _write(tmp_path / f"file{index}.py", "print('ok')\n")
    calls = {"count": 0}

    def cancel_after_first() -> bool:
        calls["count"] += 1
        return calls["count"] > 1

    report = run_passive_scan(tmp_path, cancelled=cancel_after_first)
    assert report.verdict == "incomplete"
    assert report.incomplete_reason
    assert "cancell" in report.incomplete_reason.lower()


def test_report_survives_cleanup_via_artifact_store(tmp_path: Path) -> None:
    _write(tmp_path / "main.py", "print('hello')\n")
    report = run_passive_scan(tmp_path)
    store = InMemoryArtifactStore()
    refs = retain_scan_report(store, report)

    # Simulate job cleanup: only artifact refs survive.
    del report
    native = json.loads(store.get_bytes(refs["native_ref"]).decode("utf-8"))
    summary = store.get_bytes(refs["summary_ref"]).decode("utf-8")
    assert native["verdict"] == "clean_with_coverage"
    assert "passive repository scan" in summary.lower()


def test_scan_job_spec_reuses_bounded_read_only_production_path() -> None:
    workload = build_scan_job_workload()
    spec = ContainerJobSpec.model_validate(
        {
            **workload,
            "workspaceRef": {
                "kind": "sandbox",
                "workspaceId": "ws-scan-3970",
                "relativePath": "repo",
            },
        }
    )
    assert spec.network_mode == "none"
    assert spec.workspace_read_only is True
    assert spec.resources.cpu_millis == 2000
    assert spec.outputs[0].relative_path == "artifacts/passive-scan-report.json"

    with_path = build_scan_job_workload(snapshot_relative_path="snapshots/artifact-1")
    assert with_path["command"][with_path["command"].index("--snapshot") + 1] == (
        "snapshots/artifact-1"
    )
    try:
        build_scan_job_workload(snapshot_relative_path="../escape")
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe snapshot path must be rejected")
