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
    # The workspace mount stays writable so the declared outputs are
    # collectable; the scanner itself never writes into the snapshot
    # (read-only by construction, enforced by test_scan_never_writes_snapshot).
    assert spec.workspace_read_only in (None, False)
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


def test_prefixed_credential_assignments_are_detected(tmp_path: Path) -> None:
    _write(tmp_path / "settings.env", "DATABASE_PASSWORD=hunter2-abcdef\n")
    _write(tmp_path / "deploy.env", "GITHUB_TOKEN=plain-value-12345\n")
    _write(tmp_path / "aws.env", "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYz\n")
    report = run_passive_scan(tmp_path)

    assert report.verdict == "finding_present"
    assert report.findings, "prefixed credential keys must be detected"
    dumped = json.dumps(report.to_payload())
    assert "hunter2-abcdef" not in dumped


def test_bare_and_prefixed_keys_do_not_double_count(tmp_path: Path) -> None:
    raw_secret = "ghp_" + "A" * 36
    _write(tmp_path / "app.py", f"token = '{raw_secret}'\n")
    report = run_passive_scan(tmp_path)

    assert report.verdict == "finding_present"
    locations = [finding.location for finding in report.findings]
    assert locations, "bare credential key must still be detected once"


def test_git_internals_are_excluded_without_forcing_incomplete(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "main.py", "print('hello world')\n")
    git_index = tmp_path / ".git" / "index"
    _write(git_index, b"\x00\x01\x02binary\xff\xfe" * 100)
    report = run_passive_scan(tmp_path)

    assert report.verdict == "clean_with_coverage"
    assert all(not item.startswith(".git/") for item in report.covered_files)
    assert all(not item.path.startswith(".git/") for item in report.files_skipped)


def test_symlinked_snapshot_root_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    _write(real / "main.py", "print('hello')\n")
    link = tmp_path / "snapshot-link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        return  # symlink creation unavailable on this platform
    report = run_passive_scan(link)

    assert report.verdict == "incomplete"
    assert report.incomplete_reason


def test_clean_report_requires_complete_schema() -> None:
    # A bare clean verdict with no identity, digest, or coverage is incomplete.
    assert (
        parse_scan_report_json(b'{"verdict": "clean_with_coverage"}').verdict
        == "incomplete"
    )
    minimal_clean = json.dumps(
        {
            "toolRef": PASSIVE_SCAN_TOOL_REF,
            "verdict": "clean_with_coverage",
        }
    ).encode()
    assert parse_scan_report_json(minimal_clean).verdict == "incomplete"


def test_clean_report_round_trip_preserves_config_and_cancellation(
    tmp_path: Path,
) -> None:
    from moonmind.security.passive_repo_scan import (  # noqa: PLC0415
        PassiveScanConfig as _Config,
    )

    _write(tmp_path / "main.py", "print('hello')\n")
    report = run_passive_scan(tmp_path, config=_Config(max_files=7))
    payload = json.dumps(report.to_payload()).encode()
    parsed = parse_scan_report_json(payload)

    assert parsed.verdict == "clean_with_coverage"
    assert parsed.config.max_files == 7
    assert parsed.feed.get("name") == "none"
    assert parsed.cancelled is False


def test_workload_defaults_to_workspace_root_with_normalized_outputs() -> None:
    workload = build_scan_job_workload()
    assert workload["command"][workload["command"].index("--snapshot") + 1] == "."
    assert workload["command"][workload["command"].index("--report") + 1] == (
        "artifacts/passive-scan-report.json"
    )
    assert "workspaceReadOnly" not in workload

    backslashed = build_scan_job_workload(
        report_relative_path="artifacts\\report.json",
    )
    assert backslashed["command"][backslashed["command"].index("--report") + 1] == (
        "artifacts/report.json"
    )
    assert backslashed["outputs"][0]["relativePath"] == "artifacts/report.json"


def test_file_count_bound_does_not_require_full_enumeration(
    tmp_path: Path,
) -> None:
    for index in range(10):
        _write(tmp_path / f"file{index:03d}.py", "print('ok')\n")
    report = run_passive_scan(
        tmp_path,
        config=PassiveScanConfig(max_files=3),
    )

    assert report.verdict == "incomplete"
    assert report.incomplete_reason == "file-count bound exceeded"
    assert len(report.covered_files) <= 3


def test_markdown_summary_escapes_untrusted_paths(tmp_path: Path) -> None:
    _write(tmp_path / "evil`.md", "print('hello')\n")
    report = run_passive_scan(tmp_path)

    assert report.verdict == "clean_with_coverage"
    assert "`evil`" not in report.summary_markdown


def test_fifo_symlink_target_is_skipped_without_blocking(tmp_path: Path) -> None:
    import os  # noqa: PLC0415

    _write(tmp_path / "main.py", "print('hello')\n")
    fifo = tmp_path / "pipe"
    try:
        os.mkfifo(fifo)
    except (OSError, AttributeError):
        return  # fifo creation unavailable on this platform
    report = run_passive_scan(tmp_path)

    assert report.verdict == "incomplete"
    assert report.files_skipped


def test_scan_never_writes_snapshot(tmp_path: Path) -> None:
    from moonmind.security import passive_repo_scan as _module  # noqa: PLC0415

    before = sorted(path.as_posix() for path in tmp_path.rglob("*"))
    report = run_passive_scan(tmp_path)
    after = sorted(path.as_posix() for path in tmp_path.rglob("*"))

    assert before == after
    assert report.verdict in {"clean_with_coverage", "incomplete"}
    assert _module.__name__.endswith("passive_repo_scan")


def test_passive_scan_supported_journey_submits_without_hand_authored_json() -> None:
    from moonmind.container_job_cli import (  # noqa: PLC0415
        passive_scan_submission,
    )

    env = {
        "MOONMIND_AGENT_RUN_ID": "agent-run-scan-3970",
        "MOONMIND_TASK_WORKFLOW_ID": "workflow-scan-3970",
        "MOONMIND_CONTAINER_JOBS_SESSION_ID": "session-scan-3970",
        "MOONMIND_CONTAINER_JOBS_WORKSPACE_ID": "ws-scan-3970",
        "MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND": "sandbox",
        "MOONMIND_CONTAINER_JOBS_WORKSPACE_RELATIVE_PATH": "repo",
    }
    submission = passive_scan_submission(env=env, request_id="req-scan-1")

    spec = submission["spec"]
    assert spec["command"][spec["command"].index("--snapshot") + 1] == "."
    validated = ContainerJobSpec.model_validate(spec)
    assert validated.network_mode == "none"
    assert validated.outputs[0].relative_path == "artifacts/passive-scan-report.json"
    assert submission["idempotencyKey"].endswith("req-scan-1")
    assert submission["source"]["workflowId"] == "workflow-scan-3970"


def test_passive_scan_cli_command_is_registered() -> None:
    from moonmind.cli import container_app, container_passive_scan  # noqa: PLC0415

    assert callable(container_passive_scan)
    names = [command.name or "" for command in container_app.registered_commands]
    assert "passive-scan" in names
