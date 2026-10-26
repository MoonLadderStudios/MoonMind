"""Repository scanner contract and native semantics for GitHub issue #5040."""

import json

import pytest

from moonmind.schemas.container_job_models import ContainerJobSpec


def scanner_api():
    assert (
        "repository_scan" in ContainerJobSpec.model_fields
    ), "scanner admission is missing"
    from moonmind.security import repository_scanners

    return repository_scanners


def test_scanners_are_separate_bounded_read_only_jobs():
    api = scanner_api()
    for scanner in ("gitleaks", "opengrep", "trivy"):
        extra = (
            {
                "rules": [
                    {
                        "id": "source",
                        "path": "/opt/moonmind-scanner-rules/test.yaml",
                        "sha256": "a" * 64,
                        "license": "MIT",
                    }
                ]
            }
            if scanner == "opengrep"
            else {}
        )
        if scanner == "trivy":
            extra = {"trivyScanners": ["misconfig"]}
        scan = api.RepositoryScan(
            scanner=scanner, outputDirectory=f"artifacts/{scanner}", **extra
        )
        spec = ContainerJobSpec.model_validate(
            {
                **api.build_scanner_job(scan),
                "workspaceRef": {"kind": "sandbox", "workspaceId": "test"},
            }
        )
        assert spec.workspace_read_only is True
        assert spec.network_mode == "none"
        assert spec.repository_scan.scanner == scanner
        assert spec.timeout_seconds > 0
        assert all(
            row.relative_path.startswith(f"artifacts/{scanner}/")
            for row in spec.outputs
        )


@pytest.mark.parametrize(
    "scanner,native,code",
    [
        (
            "gitleaks",
            [
                {
                    "RuleID": "credential",
                    "File": "tracked.txt",
                    "StartLine": 1,
                    "Secret": "synthetic-private-value",
                    "Match": "synthetic-private-value",
                    "Commit": "a" * 40,
                }
            ],
            1,
        ),
        (
            "opengrep",
            {
                "version": "1.0",
                "results": [
                    {
                        "check_id": "source",
                        "path": "tracked.txt",
                        "start": {"line": 1},
                        "extra": {"lines": "synthetic-private-value"},
                    }
                ],
                "errors": [
                    {"type": "ParseError", "message": "synthetic-private-value"}
                ],
                "paths": {"scanned": ["tracked.txt"]},
            },
            0,
        ),
        (
            "trivy",
            {
                "SchemaVersion": 2,
                "Results": [
                    {
                        "Target": "tracked.txt",
                        "Vulnerabilities": [
                            {
                                "VulnerabilityID": "CVE-2026-0001",
                                "PkgName": "dependency",
                                "InstalledVersion": "1",
                            }
                        ],
                        "Secrets": [
                            {"RuleID": "token", "Match": "synthetic-private-value"}
                        ],
                    }
                ],
            },
            0,
        ),
    ],
)
def test_native_findings_and_incomplete_coverage_survive(scanner, native, code):
    api = scanner_api()
    safe = api.sanitize_native(scanner, json.dumps(native).encode(), 100000)
    assert b"synthetic-private-value" not in safe
    result = api.normalize_native(
        scanner,
        safe,
        exit_code=code,
        terminal_state="timed_out",
        native_ref="art_sanitized",
    )
    assert result.findings
    assert result.coverage == "partial"
    assert all(f.evidence_refs == ["art_sanitized"] for f in result.findings)
    assert result.verification == "not_performed"


@pytest.mark.parametrize("scanner", ["gitleaks", "opengrep", "trivy"])
@pytest.mark.parametrize("body", [b"", b"{}", b"[", b"null"])
def test_missing_or_malformed_report_is_never_clean(scanner, body):
    api = scanner_api()
    result = api.normalize_native(
        scanner,
        body,
        exit_code=0,
        terminal_state="succeeded",
        native_ref="art_sanitized",
    )
    assert result.coverage == "partial"
    assert result.limitations


def test_normalized_identity_does_not_depend_on_timestamp_or_native_bytes():
    api = scanner_api()
    base = [
        {
            "RuleID": "credential",
            "File": "tracked.txt",
            "StartLine": 1,
            "Commit": "a" * 40,
            "Date": "yesterday",
            "Secret": "redacted",
        }
    ]
    a = api.normalize_native(
        "gitleaks",
        json.dumps(base).encode(),
        exit_code=1,
        terminal_state="succeeded",
        native_ref="art_one",
    )
    base[0]["Date"] = "today"
    b = api.normalize_native(
        "gitleaks",
        json.dumps(base).encode(),
        exit_code=1,
        terminal_state="succeeded",
        native_ref="art_two",
    )
    assert [f.finding_id for f in a.findings] == [f.finding_id for f in b.findings]


def test_history_scope_and_exclusions_reach_git_pathspecs(tmp_path):
    api = scanner_api()
    from moonmind.security.repository_scanner_runner import scanner_commands

    scan = api.RepositoryScan(
        scanner="gitleaks",
        inputPath="src",
        exclusions=["src/vendor"],
        outputDirectory="artifacts/scan",
    )
    commands = scanner_commands(
        scan, report_root=tmp_path, config=tmp_path / "config", cache=tmp_path
    )
    history = commands["history"]
    options = history[history.index("--log-opts") + 1]
    assert "-- src" in options
    assert ":(exclude)src/vendor" in options
    assert "--no-ext-diff" in options and "--no-textconv" in options


def test_explicit_inline_suppression_choice_is_recorded(tmp_path):
    api = scanner_api()
    from moonmind.security.repository_scanner_runner import scanner_commands

    scan = api.RepositoryScan(
        scanner="gitleaks",
        outputDirectory="artifacts/scan",
        allowInlineSuppressions=True,
    )
    commands = scanner_commands(
        scan, report_root=tmp_path, config=tmp_path / "config", cache=tmp_path
    )
    assert "--ignore-gitleaks-allow" not in commands["worktree"]
    assert "--redact=100" in commands["worktree"]
    assert scan.model_dump(by_alias=True)["allowInlineSuppressions"] is True


def test_bounded_snapshot_does_not_follow_links_or_modify_source(tmp_path):
    api = scanner_api()
    from moonmind.security.repository_scanner_runner import _copy_inputs

    source = tmp_path / "repo"
    source.mkdir()
    (source / "a.txt").write_text("first")
    (source / "b.txt").write_text("second")
    (source / "link").symlink_to(tmp_path / "unrelated")
    (tmp_path / "unrelated").write_text("private")
    stage = tmp_path / "stage"
    stage.mkdir()
    scan = api.RepositoryScan(
        scanner="gitleaks", outputDirectory="artifacts/scan", maxFiles=1
    )
    assets, digest, limitations = _copy_inputs(scan, source, stage)
    assert assets == ["a.txt"]
    assert "input_bound_exceeded" in limitations
    assert not (stage / "link").exists()
    assert (source / "a.txt").read_text() == "first"
    assert len(digest) == 64
