"""First-slice repository-security tool-pack tests (MoonMind#3970).

Hermetic by construction: inline fixtures and controlled feed clocks only.
No network, no Docker, no credentials, no live scanner binary. Exact-image
and feed-update qualification are recorded separately at deployment time;
these tests prove the contract fails closed with fixture digests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from moonmind.schemas.container_job_models import (
    ContainerJobFailureClass,
    ContainerJobState,
)
from moonmind.schemas.workspace_locator_models import ExternalStateLocator
from moonmind.security.repo_security_toolpack import (
    AnalysisCompleteness,
    FeedInfo,
    NETWORK_ACTIVE_REQUIRES_EXPLICIT_AUTHORITY,
    PASSIVE_SLICE_CAPABILITY_LEVEL,
    ParsedScannerOutput,
    RepoSecurityToolPack,
    RepoSecurityToolPackError,
    ScanProvenance,
    ScanScope,
    ScanVerdict,
    build_scan_job_spec,
    canonical_digest,
    classify_job_outcome,
    default_first_slice_pack,
    evaluate_scan,
    normalize_finding_path,
    normalize_scope_path,
    parse_native_scanner_output,
    validate_tool_pack,
)

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


def _provenance(**overrides) -> ScanProvenance:
    scope = overrides.pop("scope", ScanScope(includePaths=["src"]))
    feeds = overrides.pop(
        "feeds",
        (
            FeedInfo(
                name="vuln-feed",
                version="2026.09.04",
                fetchedAt=NOW - timedelta(hours=6),
            ),
            FeedInfo(
                name="secret-rules",
                version="2026.09.01",
                fetchedAt=NOW - timedelta(days=1),
            ),
        ),
    )
    return ScanProvenance(
        inputRevision="abc123",
        contentDigest="sha256:" + "a" * 64,
        toolName="repo-security-scan",
        toolVersion="0.1.0",
        resolvedImageRef="example-registry/repo-security-scan:0.1.0",
        resolvedImageDigest="sha256:" + "b" * 64,
        configRulesDigest="sha256:" + "c" * 64,
        feeds=feeds,
        admittedScope=scope,
        **overrides,
    )


def _vulnerable_payload() -> dict:
    return {
        "findings": [
            {
                "id": "dep-openssl-1",
                "category": "dependency_vulnerability",
                "severity": "high",
                "confidence": "high",
                "path": "src/requirements.txt",
                "line": 3,
                "summary": "openssl 1.1.1 has a known critical flaw in this fixture",
            },
            {
                "id": "secret-1",
                "category": "secret_exposure",
                "severity": "high",
                "confidence": "medium",
                "path": "src/settings.py",
                "line": 12,
                "summary": "candidate credential assignment in settings",
                "secretMatch": "api_key = 'fixture-only-not-a-real-secret-0000'",
            },
        ],
        "inventory": [
            {
                "component": "openssl",
                "version": "1.1.1",
                "lockfile": "requirements.txt",
                "language": "python",
            }
        ],
    }


def test_first_slice_is_passive_level_with_gated_network_active() -> None:
    assert PASSIVE_SLICE_CAPABILITY_LEVEL == 1
    assert NETWORK_ACTIVE_REQUIRES_EXPLICIT_AUTHORITY is True
    pack = default_first_slice_pack()
    assert pack.network_mode == "none"
    assert pack.requires_new_service is False
    assert pack.requires_new_worker is False
    assert pack.requires_docker_socket is False


def test_tool_pack_runs_through_real_container_job_wiring() -> None:
    pack = default_first_slice_pack()
    scope = ScanScope(includePaths=["src"], excludePaths=["src/fixtures"])
    spec = build_scan_job_spec(
        pack=pack,
        workspace_ref=ExternalStateLocator(artifactRef="art_snapshot_1"),
        scope=scope,
    )
    assert spec.network_mode == "none"
    assert spec.image_source_ref == "repo-security-scan"
    assert spec.image is None  # deployment-owned authority, not a raw image string
    assert spec.timeout_seconds == pack.timeout_seconds
    assert spec.resources.cpu_millis == pack.cpu_millis
    assert spec.resources.memory_mib == pack.memory_mib
    assert spec.command[0] == "/scan"
    assert "--read-only" in spec.command
    assert "--no-network" in spec.command
    assert any(arg.startswith("--snapshot=") for arg in spec.command)
    assert {output.name for output in spec.outputs} == {
        "scan-native-json",
        "scan-summary-md",
    }
    # No secret-shaped environment is injected for the scan.
    assert spec.environment == []


def test_tool_pack_rejects_execution_vectors_and_widened_authority() -> None:
    with pytest.raises(ValueError, match="entrypoint"):
        RepoSecurityToolPack(
            toolVersion="0.1.0",
            imageSourceRef="repo-security-scan",
            entrypoint=("/scan", "pip install -r requirements.txt"),
        )
    with pytest.raises(ValueError, match="entrypoint"):
        RepoSecurityToolPack(
            toolVersion="0.1.0",
            imageSourceRef="repo-security-scan",
            entrypoint=("/scan", "--apply"),
        )
    with pytest.raises(ValueError, match="entrypoint"):
        RepoSecurityToolPack(
            toolVersion="0.1.0",
            imageSourceRef="repo-security-scan",
            entrypoint=("/scan", "--publish"),
        )
    hostile = default_first_slice_pack().model_copy(
        update={"network_mode": "bridge"}
    )
    with pytest.raises(RepoSecurityToolPackError, match="networkMode none"):
        validate_tool_pack(hostile)


def test_hostile_scope_is_rejected_before_execution() -> None:
    for bad in ("../escape", "/absolute", "a/../../b", "x%2e%2e/y", ""):
        with pytest.raises(ValueError, match="scope path|must not be empty"):
            normalize_scope_path(bad)
    # Model construction wraps scope errors in pydantic ValidationError, which
    # is a ValueError subclass; match on the inner diagnostic text.
    with pytest.raises(ValueError, match="scope path"):
        ScanScope(includePaths=["../escape"])
    with pytest.raises(ValueError, match="scope path"):
        ScanScope(includePaths=["src"], excludePaths=["/absolute"])
    with pytest.raises(ValueError, match="unsupported coverage"):
        ScanScope(includePaths=["src"], coverage=["network_active_assessment"])
    pack = default_first_slice_pack()
    with pytest.raises(RepoSecurityToolPackError):
        build_scan_job_spec(
            pack=pack,
            workspace_ref=ExternalStateLocator(artifactRef="art_snapshot_1"),
            scope=ScanScope(includePaths=["src"]),
            snapshot_path="../escape",
        )


def test_vulnerable_fixture_reports_open_findings() -> None:
    parsed = parse_native_scanner_output(_vulnerable_payload())
    result = evaluate_scan(
        provenance=_provenance(),
        state=ContainerJobState.SUCCEEDED,
        exit_code=0,
        parsed=parsed,
        now=NOW,
        logs_ref="logs-1",
        artifacts_ref="artifacts-1",
        raw_evidence_ref="artifacts-1/native.json",
    )
    assert result.completeness == AnalysisCompleteness.COMPLETE
    assert result.verdict == ScanVerdict.FINDINGS_OPEN
    assert len(result.findings) == 2
    assert all(finding.disposition == "open" for finding in result.findings)
    assert result.mutation_authorized is False
    assert result.evidence_discarded is False
    assert result.artifact_visibility == "confidential"
    assert result.logs_ref == "logs-1"
    assert result.artifacts_ref == "artifacts-1"
    # A generated summary cannot upgrade disposition.
    assert "remain open" in result.summary_markdown
    assert "verified" not in result.summary_markdown.lower()
    assert "resolved" not in result.summary_markdown.lower()


def test_clean_fixture_requires_declared_successful_coverage() -> None:
    parsed = parse_native_scanner_output({"findings": [], "inventory": []})
    result = evaluate_scan(
        provenance=_provenance(),
        state=ContainerJobState.SUCCEEDED,
        exit_code=0,
        parsed=parsed,
        now=NOW,
    )
    assert result.completeness == AnalysisCompleteness.COMPLETE
    assert result.verdict == ScanVerdict.CLEAN_WITH_DECLARED_COVERAGE
    assert "zero findings for the declared successful coverage" in result.diagnostics


def test_clean_is_impossible_with_skipped_truncated_or_stale_inputs() -> None:
    base = {"findings": [], "inventory": []}
    for payload in (
        {**base, "skippedPaths": ["src/vendor"]},
        {**base, "truncated": True},
    ):
        parsed = parse_native_scanner_output(payload)
        result = evaluate_scan(
            provenance=_provenance(),
            state=ContainerJobState.SUCCEEDED,
            exit_code=0,
            parsed=parsed,
            now=NOW,
        )
        assert result.verdict == ScanVerdict.NOT_CLEAN
        assert result.completeness == AnalysisCompleteness.INCOMPLETE

    parsed = parse_native_scanner_output(
        {**base, "unsupported": ["rust:Cargo.lock-not-declared"]}
    )
    result = evaluate_scan(
        provenance=_provenance(),
        state=ContainerJobState.SUCCEEDED,
        exit_code=0,
        parsed=parsed,
        now=NOW,
    )
    assert result.verdict == ScanVerdict.NOT_CLEAN
    assert result.completeness == AnalysisCompleteness.UNSUPPORTED

    stale_provenance = _provenance(
        feeds=(
            FeedInfo(
                name="vuln-feed",
                version="2026.01.01",
                fetchedAt=NOW - timedelta(days=60),
            ),
        )
    )
    parsed = parse_native_scanner_output(base)
    result = evaluate_scan(
        provenance=stale_provenance,
        state=ContainerJobState.SUCCEEDED,
        exit_code=0,
        parsed=parsed,
        now=NOW,
    )
    assert result.verdict == ScanVerdict.NOT_CLEAN
    assert result.completeness == AnalysisCompleteness.INCOMPLETE
    assert result.feed_freshness == {"vuln-feed": "stale"}

    missing_provenance = _provenance(feeds=(FeedInfo(name="vuln-feed"),))
    result = evaluate_scan(
        provenance=missing_provenance,
        state=ContainerJobState.SUCCEEDED,
        exit_code=0,
        parsed=parse_native_scanner_output(base),
        now=NOW,
    )
    assert result.verdict == ScanVerdict.NOT_CLEAN
    assert result.feed_freshness == {"vuln-feed": "missing"}


def test_unsupported_only_scope_reports_unsupported_never_clean() -> None:
    parsed = parse_native_scanner_output(
        {"findings": [], "inventory": [], "unsupported": ["cobol:legacy.lock"]}
    )
    result = evaluate_scan(
        provenance=_provenance(),
        state=ContainerJobState.SUCCEEDED,
        exit_code=0,
        parsed=parsed,
        now=NOW,
    )
    assert result.completeness == AnalysisCompleteness.UNSUPPORTED
    assert result.verdict == ScanVerdict.NOT_CLEAN


def test_malformed_output_fails_loudly_without_false_clean() -> None:
    for bad in ("", "not json {", "[1,2]", '"string"', "42"):
        with pytest.raises(RepoSecurityToolPackError):
            parse_native_scanner_output(bad)
    with pytest.raises(RepoSecurityToolPackError):
        parse_native_scanner_output({"findings": "nope", "inventory": []})
    with pytest.raises(RepoSecurityToolPackError):
        parse_native_scanner_output({"findings": [{"id": "x"}], "inventory": []})
    with pytest.raises(RepoSecurityToolPackError):
        parse_native_scanner_output(b"\xff\xfe invalid utf-8 \x00")
    with pytest.raises(RepoSecurityToolPackError, match="size bound"):
        parse_native_scanner_output("x" * (2 * 1024 * 1024 + 1))

    result = evaluate_scan(
        provenance=_provenance(),
        state=ContainerJobState.SUCCEEDED,
        exit_code=0,
        parsed=None,
        parser_error="scanner output is not valid JSON",
        now=NOW,
        logs_ref="logs-1",
        artifacts_ref="artifacts-1",
    )
    assert result.completeness == AnalysisCompleteness.FAILED
    assert result.verdict == ScanVerdict.NOT_CLEAN
    assert result.mutation_authorized is False
    assert result.logs_ref == "logs-1"
    assert result.artifacts_ref == "artifacts-1"


def test_escaping_finding_paths_are_quarantined_not_dropped_to_clean() -> None:
    for bad in ("../../etc/passwd", "/absolute/path", "a\\..\\b", "", "x%2e%2e/y"):
        with pytest.raises(RepoSecurityToolPackError):
            normalize_finding_path(bad)
    parsed = parse_native_scanner_output(
        {
            "findings": [
                {
                    "id": "evil-1",
                    "category": "secret_exposure",
                    "severity": "high",
                    "confidence": "high",
                    "path": "../../etc/passwd",
                    "summary": "escaped path finding",
                }
            ],
            "inventory": [],
        }
    )
    assert isinstance(parsed, ParsedScannerOutput)
    assert parsed.quarantined_paths == 1
    assert parsed.findings == ()
    result = evaluate_scan(
        provenance=_provenance(),
        state=ContainerJobState.SUCCEEDED,
        exit_code=0,
        parsed=parsed,
        now=NOW,
    )
    assert result.completeness == AnalysisCompleteness.INCOMPLETE
    assert result.verdict == ScanVerdict.NOT_CLEAN


def test_secret_bearing_findings_suppress_raw_matches() -> None:
    raw_secret = "fixture-only-not-a-real-secret-0000"
    parsed = parse_native_scanner_output(_vulnerable_payload())
    result = evaluate_scan(
        provenance=_provenance(),
        state=ContainerJobState.SUCCEEDED,
        exit_code=0,
        parsed=parsed,
        now=NOW,
        raw_evidence_ref="artifacts-1/native.json",
    )
    secret_findings = [
        finding for finding in result.findings if finding.category == "secret_exposure"
    ]
    assert len(secret_findings) == 1
    ordinary_report = result.model_dump(mode="json")
    assert raw_secret not in str(ordinary_report)
    assert secret_findings[0].redacted_preview != raw_secret
    assert "[REDACTED" in secret_findings[0].redacted_preview or "REDACTED" in (
        secret_findings[0].redacted_preview
    )
    # Restricted raw evidence travels by artifact ref only, never inline.
    assert result.raw_evidence_ref == "artifacts-1/native.json"
    assert raw_secret not in result.summary_markdown


def test_timeout_and_cancellation_preserve_evidence_without_clean() -> None:
    parsed = parse_native_scanner_output({"findings": [], "inventory": []})
    for state, failure in (
        (ContainerJobState.TIMED_OUT, ContainerJobFailureClass.TIMEOUT),
        (ContainerJobState.CANCELED, ContainerJobFailureClass.CANCELED),
        (ContainerJobState.FAILED, ContainerJobFailureClass.EXECUTION),
    ):
        completeness, _ = classify_job_outcome(
            state=state, exit_code=None, failure_class=failure
        )
        assert completeness == AnalysisCompleteness.FAILED
        result = evaluate_scan(
            provenance=_provenance(),
            state=state,
            exit_code=None,
            failure_class=failure,
            parsed=parsed,
            now=NOW,
            logs_ref="logs-1",
            artifacts_ref="artifacts-1",
        )
        assert result.verdict == ScanVerdict.NOT_CLEAN
        assert result.mutation_authorized is False
        assert result.evidence_discarded is False
        assert result.provenance.input_revision == "abc123"
        assert result.logs_ref == "logs-1"
        assert result.artifacts_ref == "artifacts-1"

    nonzero = evaluate_scan(
        provenance=_provenance(),
        state=ContainerJobState.SUCCEEDED,
        exit_code=3,
        parsed=parsed,
        now=NOW,
    )
    assert nonzero.completeness == AnalysisCompleteness.FAILED
    assert nonzero.verdict == ScanVerdict.NOT_CLEAN


def test_provenance_binding_is_stable_and_complete() -> None:
    scope = ScanScope(includePaths=["src"])
    provenance = _provenance(scope=scope)
    first = canonical_digest(provenance.model_dump(mode="json", by_alias=True))
    second = canonical_digest(_provenance(scope=scope).model_dump(mode="json", by_alias=True))
    assert first == second
    assert first.startswith("sha256:")
    changed = _provenance(scope=ScanScope(includePaths=["other"]))
    assert canonical_digest(changed.model_dump(mode="json", by_alias=True)) != first
    dumped = provenance.model_dump(by_alias=True)
    for key in (
        "inputRevision",
        "contentDigest",
        "toolName",
        "toolVersion",
        "resolvedImageDigest",
        "configRulesDigest",
        "feeds",
        "admittedScope",
        "outputContract",
    ):
        assert key in dumped, key
    assert dumped["outputContract"] == "repo-security-scan-output/v1"
