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


@pytest.mark.parametrize(
    "position", ["first", "middle", "last", "invalid-only", "clean"]
)
@pytest.mark.parametrize("malformed", [{}, None, {"RuleID": "credential"}])
def test_gitleaks_malformed_neighbors_preserve_valid_finding_identities(
    position, malformed
):
    api = scanner_api()
    valid = [
        {"RuleID": "credential", "File": path, "StartLine": 1}
        for path in ("tracked.txt", "untracked.txt")
    ]
    records = {
        "first": [malformed, *valid],
        "middle": [valid[0], malformed, valid[1]],
        "last": [*valid, malformed],
        "invalid-only": [malformed],
        "clean": [],
    }[position]

    def normalize(records):
        return api.normalize_native(
            "gitleaks",
            json.dumps(records).encode(),
            exit_code=0,
            terminal_state="succeeded",
            native_ref="art_sanitized",
        )

    expected = normalize(valid if position in {"first", "middle", "last"} else [])
    observed = normalize(records)
    assert [f.finding_id for f in observed.findings] == [
        f.finding_id for f in expected.findings
    ]
    assert observed.coverage == ("complete" if position == "clean" else "partial")
    assert bool(observed.limitations) == (position != "clean")


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


def test_opengrep_command_omits_removed_metrics_option(tmp_path):
    # Opengrep has no telemetry and rejects `--metrics` as an unknown option.
    api = scanner_api()
    from moonmind.security.repository_scanner_runner import scanner_commands

    scan = api.RepositoryScan(
        scanner="opengrep",
        outputDirectory="artifacts/scan",
        rules=[
            {
                "id": "source",
                "path": "/opt/moonmind-scanner-rules/test.yaml",
                "sha256": "a" * 64,
                "license": "MIT",
            }
        ],
    )
    commands = scanner_commands(
        scan, report_root=tmp_path, config=tmp_path / "config", cache=tmp_path
    )
    assert not [arg for arg in commands["scan"] if arg.startswith("--metrics")]


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


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"Target": "requirements.txt"},
        {"Vulnerabilities": []},
        {"Target": "requirements.txt", "Vulnerabilities": {}},
        {"Target": "requirements.txt", "Vulnerabilities": [None]},
        None,
    ],
)
@pytest.mark.parametrize("mixed", [False, True])
def test_invalid_trivy_results_keep_valid_findings_and_incomplete_coverage(
    invalid, mixed
):
    api = scanner_api()
    valid = {
        "Target": "requirements.txt",
        "Vulnerabilities": [{"VulnerabilityID": "CVE-EXAMPLE", "PkgName": "example"}],
    }
    native = {"SchemaVersion": 2, "Results": [invalid, valid] if mixed else [invalid]}
    safe = api.sanitize_native("trivy", json.dumps(native).encode())
    result = api.normalize_native(
        "trivy", safe, exit_code=0, terminal_state="succeeded", native_ref="art_native"
    )
    assert result.coverage == "partial"
    assert "missing_malformed_or_truncated_report" in result.limitations
    assert [f.rule_id for f in result.findings] == (["CVE-EXAMPLE"] if mixed else [])


@pytest.mark.parametrize(
    "analysis",
    [
        {"Vulnerabilities": []},
        {"Class": "lang-pkgs", "Type": "pip", "Packages": [{"Name": "example"}]},
        {
            "Class": "config",
            "Type": "terraform",
            "MisconfSummary": {"Successes": 1, "Failures": 0},
        },
    ],
)
def test_valid_trivy_clean_analysis_retains_examined_target(analysis):
    api = scanner_api()
    native = {"SchemaVersion": 2, "Results": [{"Target": "clean.txt", **analysis}]}
    safe = api.sanitize_native("trivy", json.dumps(native).encode())
    result = api.normalize_native(
        "trivy", safe, exit_code=0, terminal_state="succeeded", native_ref="art_native"
    )
    assert result.coverage == "complete"
    assert result.findings == []
    assert result.examined_assets == ["clean.txt"]


@pytest.mark.parametrize("incomplete", [False, True])
def test_opengrep_examined_assets_come_from_native_paths(incomplete):
    api = scanner_api()
    native = {
        "results": [],
        "errors": [{"type": "ParseError", "path": "broken.py"}] if incomplete else [],
        "paths": {
            "scanned": ["/scan-input/clean.py"],
            "skipped": [{"path": "unsupported.bin", "reason": "unsupported"}],
        },
    }
    result = api.normalize_native(
        "opengrep",
        api.sanitize_native("opengrep", json.dumps(native).encode()),
        exit_code=0,
        terminal_state="succeeded",
        native_ref="art_native",
    )
    assert result.examined_assets == ["clean.py"]
    assert result.unexamined_assets == (
        ["broken.py", "unsupported.bin"] if incomplete else ["unsupported.bin"]
    )
    assert result.coverage == "partial"
    assert "unsupported_or_parser_failed_inputs" in result.limitations
