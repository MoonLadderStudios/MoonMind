"""Bounded repository-security tool pack, first passive slice (#3970).

Declarative contract owner: ``docs/Security/RepositorySecurityToolPack.md``.
This module is the hermetic reference implementation of that contract: one
portable tool pack for an authorized immutable repository or artifact
snapshot, initially supporting dependency/inventory and secret-exposure
triage with explicit coverage.

Scope rules enforced here, not merely documented:

- passive level 1 only: no assessment-target network access, no Docker
  socket, read-only execution, bounded CPU/memory/time/output;
- no repository hooks, install scripts, arbitrary plugins, or package
  builds merely to inventory dependencies;
- job completion, analysis completeness, and finding disposition are
  separate; anything short of declared successful coverage is never clean;
- findings and scanner output are untrusted and confidential; raw secret
  matches never appear in ordinary reports;
- scan failure preserves evidence and never authorizes mutation.

The module performs no I/O, no network access, and no mutation. It builds
real :class:`ContainerJobSpec` values through the existing generic Container
Job substrate so the selected tool runs through MoonMind-owned durable
execution, evidence, and cleanup rather than a parallel scanner engine.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.schemas.container_job_models import (
    ContainerJobFailureClass,
    ContainerJobSpec,
    ContainerJobState,
    OutputDeclaration,
    ResourceLimits,
    ensure_temporal_safe,
)
from moonmind.schemas.workspace_locator_models import WorkspaceLocator
from moonmind.utils.logging import redact_sensitive_text

TOOL_PACK_NAME = "repo-security-scan"
TOOL_PACK_CONTRACT_VERSION = "v1"
TOOL_PACK_OUTPUT_CONTRACT = "repo-security-scan-output/v1"

# Roadmap graduated capability level owned by this slice: repository and
# artifact analysis with no target network access. Network-active levels are
# explicitly out of scope and gated by their own target authority.
PASSIVE_SLICE_CAPABILITY_LEVEL = 1
NETWORK_ACTIVE_REQUIRES_EXPLICIT_AUTHORITY = True

FIRST_SLICE_COVERAGE = (
    "dependency_inventory",
    "secret_exposure_triage",
)
DECLARED_SUPPORTED_LOCKFILES = (
    "requirements.txt",
    "requirements.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "go.mod",
)
DECLARED_SUPPORTED_LANGUAGES = (
    "python",
    "javascript",
    "typescript",
)

SCAN_CPU_MILLIS = 2000
SCAN_MEMORY_MIB = 2048
SCAN_TIMEOUT_SECONDS = 1200
SCAN_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
SCAN_MAX_FINDINGS = 500
SCAN_MAX_NATIVE_BYTES = 2 * 1024 * 1024

# Feed older than this is reported stale; staleness never becomes clean.
FEED_MAX_AGE_SECONDS = 7 * 24 * 3600


class RepoSecurityToolPackError(ValueError):
    """Raised when tool-pack, scope, feed, or scanner output is invalid."""


class AnalysisCompleteness(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class ScanVerdict(StrEnum):
    """Terminal reading of a scan result. Only one value means clean."""

    CLEAN_WITH_DECLARED_COVERAGE = "clean_with_declared_coverage"
    FINDINGS_OPEN = "findings_open"
    NOT_CLEAN = "not_clean"


class FeedFreshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    MISSING = "missing"


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class FeedInfo(ContractModel):
    """One rule/vulnerability-feed input with honest freshness."""

    name: str = Field(min_length=1, max_length=128)
    version: str | None = Field(None, max_length=128)
    fetched_at: datetime | None = Field(None, alias="fetchedAt")
    max_age_seconds: int = Field(FEED_MAX_AGE_SECONDS, alias="maxAgeSeconds", ge=1)

    def freshness(self, *, now: datetime | None = None) -> FeedFreshness:
        if self.version is None or self.fetched_at is None:
            return FeedFreshness.MISSING
        assert self.fetched_at is not None
        if now is None:
            from datetime import timezone

            now = (
                datetime.now(timezone.utc)
                if self.fetched_at.tzinfo is not None
                else datetime.now()
            )
        try:
            age = (now - self.fetched_at).total_seconds()
        except TypeError:
            return FeedFreshness.MISSING
        if age < 0 or age > self.max_age_seconds:
            return FeedFreshness.STALE
        return FeedFreshness.CURRENT


class ScanScope(ContractModel):
    """Admitted snapshot scope. Relative paths only; nothing escapes."""

    include_paths: tuple[str, ...] = Field(
        default=(".",), alias="includePaths", max_length=128
    )
    exclude_paths: tuple[str, ...] = Field(
        default=(), alias="excludePaths", max_length=128
    )
    max_files: int = Field(5000, alias="maxFiles", ge=1, le=100000)
    max_bytes: int = Field(256 * 1024 * 1024, alias="maxBytes", ge=1024)
    coverage: tuple[str, ...] = Field(default=FIRST_SLICE_COVERAGE, max_length=16)

    @field_validator("include_paths", "exclude_paths", mode="before")
    @classmethod
    def _coerce_path_lists(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value]
        return value

    @model_validator(mode="after")
    def _validate_scope(self) -> "ScanScope":
        normalized_includes = tuple(
            normalize_scope_path(p) for p in self.include_paths
        )
        normalized_excludes = tuple(
            normalize_scope_path(p) for p in self.exclude_paths
        )
        if not normalized_includes:
            raise ValueError("includePaths must contain at least one path")
        unknown = [c for c in self.coverage if c not in FIRST_SLICE_COVERAGE]
        if unknown:
            raise ValueError(f"unsupported coverage entries: {sorted(unknown)}")
        object.__setattr__(self, "include_paths", normalized_includes)
        object.__setattr__(self, "exclude_paths", normalized_excludes)
        return self


def normalize_scope_path(raw: str) -> str:
    """Normalize one admitted scope path, rejecting escapes (MoonMind#3970)."""

    candidate = str(raw or "").strip().replace("\\", "/")
    if not candidate:
        raise RepoSecurityToolPackError("scope paths must not be empty")
    if candidate == ".":
        return "."
    decoded = candidate
    for _ in range(3):
        nxt = unquote(decoded)
        if nxt == decoded:
            break
        decoded = nxt
    if decoded != candidate:
        raise RepoSecurityToolPackError(
            f"scope path must not contain percent-encoding: {raw!r}"
        )
    path = PurePosixPath(candidate)
    if path.is_absolute():
        raise RepoSecurityToolPackError(
            f"scope path must be relative: {raw!r}"
        )
    if any(part in {"", ".", ".."} for part in path.parts):
        raise RepoSecurityToolPackError(
            f"scope path must be normalized without traversal: {raw!r}"
        )
    return str(path)


def normalize_finding_path(raw: str) -> str:
    """Validate one untrusted scanner-reported path before rendering."""

    candidate = str(raw or "").strip().replace("\\", "/")
    if not candidate:
        raise RepoSecurityToolPackError("finding paths must not be empty")
    decoded = candidate
    for _ in range(3):
        nxt = unquote(decoded)
        if nxt == decoded:
            break
        decoded = nxt
    if decoded != candidate:
        raise RepoSecurityToolPackError("finding path must not be encoded")
    path = PurePosixPath(candidate)
    if path.is_absolute():
        raise RepoSecurityToolPackError("finding path must be relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise RepoSecurityToolPackError("finding path must not traverse")
    if len(candidate) > 1000:
        raise RepoSecurityToolPackError("finding path is too long")
    return str(path)


class RepoSecurityToolPack(ContractModel):
    """The single portable tool pack owned by the first slice."""

    name: Literal["repo-security-scan"] = "repo-security-scan"
    contract_version: Literal["v1"] = Field("v1", alias="contractVersion")
    tool_version: str = Field(alias="toolVersion", min_length=1, max_length=64)
    # Deployment-owned image-source allowlist key. The exact resolved image
    # digest is bound per result at execution time (ImageObservation), never
    # assumed from the tool version alone.
    image_source_ref: str = Field(alias="imageSourceRef", min_length=1, max_length=128)
    entrypoint: tuple[str, ...] = Field(
        default=("/scan", "--read-only", "--no-network"),
        max_length=16,
    )
    network_mode: Literal["none"] = Field("none", alias="networkMode")
    cpu_millis: int = Field(SCAN_CPU_MILLIS, alias="cpuMillis", ge=1, le=8000)
    memory_mib: int = Field(SCAN_MEMORY_MIB, alias="memoryMiB", ge=256, le=8192)
    timeout_seconds: int = Field(
        SCAN_TIMEOUT_SECONDS, alias="timeoutSeconds", ge=60, le=3600
    )
    max_output_bytes: int = Field(
        SCAN_MAX_OUTPUT_BYTES, alias="maxOutputBytes", ge=1024
    )
    # The pack creates no service, worker, or container beyond the per-run
    # Container Job; these flags make that structural invariant testable.
    requires_new_service: Literal[False] = Field(False, alias="requiresNewService")
    requires_new_worker: Literal[False] = Field(False, alias="requiresNewWorker")
    requires_docker_socket: Literal[False] = Field(
        False, alias="requiresDockerSocket"
    )

    @field_validator("entrypoint", mode="before")
    @classmethod
    def _coerce_entrypoint(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value]
        return value

    @model_validator(mode="after")
    def _forbid_execution_vectors(self) -> "RepoSecurityToolPack":
        forbidden = (
            "hook", "hooks", "install", "pip install", "npm install",
            "build", "plugin", "plugins", "--exec", "--write", "--apply",
            "--fix", "--publish",
        )
        joined = " ".join(self.entrypoint).lower()
        for token in forbidden:
            if token in joined:
                raise ValueError(
                    f"tool-pack entrypoint must not contain {token!r}"
                )
        return self


def default_first_slice_pack(
    *, tool_version: str = "0.1.0", image_source_ref: str = "repo-security-scan"
) -> RepoSecurityToolPack:
    """Return the bounded first-slice tool pack with default limits."""

    return RepoSecurityToolPack(
        toolVersion=tool_version,
        imageSourceRef=image_source_ref,
    )


class ScanProvenance(ContractModel):
    """Immutable evidence bound to every scan result."""

    input_revision: str = Field(alias="inputRevision", min_length=1, max_length=256)
    content_digest: str = Field(alias="contentDigest", min_length=1, max_length=256)
    tool_name: str = Field(alias="toolName", min_length=1, max_length=128)
    tool_version: str = Field(alias="toolVersion", min_length=1, max_length=64)
    resolved_image_ref: str | None = Field(
        None, alias="resolvedImageRef", max_length=1024
    )
    resolved_image_digest: str | None = Field(
        None, alias="resolvedImageDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    config_rules_digest: str = Field(
        alias="configRulesDigest", min_length=1, max_length=256
    )
    feeds: tuple[FeedInfo, ...] = Field(default=(), max_length=16)
    admitted_scope: ScanScope = Field(alias="admittedScope")
    output_contract: str = Field(
        TOOL_PACK_OUTPUT_CONTRACT, alias="outputContract", max_length=128
    )


class InventoryEntry(ContractModel):
    component: str = Field(min_length=1, max_length=512)
    version: str | None = Field(None, max_length=128)
    lockfile: str = Field(min_length=1, max_length=256)
    language: str = Field(min_length=1, max_length=64)


class NativeFinding(ContractModel):
    """One finding as reported by native scanner output (untrusted)."""

    identifier: str = Field(alias="id", min_length=1, max_length=256)
    category: str = Field(min_length=1, max_length=128)
    severity: Literal["critical", "high", "medium", "low", "info"] = "medium"
    confidence: Literal["high", "medium", "low"] = "medium"
    path: str = Field(min_length=1, max_length=1000)
    line: int | None = Field(None, ge=1)
    summary: str = Field(min_length=1, max_length=2048)
    secret_match: str | None = Field(None, alias="secretMatch", max_length=4096)

    @field_validator("path", mode="before")
    @classmethod
    def _validate_path(cls, value: Any) -> Any:
        return normalize_finding_path(str(value or ""))


class ParsedScannerOutput(ContractModel):
    findings: tuple[NativeFinding, ...] = Field(default=(), max_length=SCAN_MAX_FINDINGS)
    inventory: tuple[InventoryEntry, ...] = Field(default=(), max_length=5000)
    skipped_paths: tuple[str, ...] = Field(default=(), alias="skippedPaths", max_length=1024)
    unsupported: tuple[str, ...] = Field(default=(), max_length=256)
    truncated: bool = False
    quarantined_paths: int = Field(0, alias="quarantinedPaths", ge=0)


class PublicFinding(ContractModel):
    """Finding safe for ordinary reports: no raw secret material."""

    identifier: str = Field(alias="id", min_length=1, max_length=256)
    category: str = Field(min_length=1, max_length=128)
    severity: str = Field(min_length=1, max_length=32)
    confidence: str = Field(min_length=1, max_length=32)
    path: str = Field(min_length=1, max_length=1000)
    line: int | None = None
    summary: str = Field(min_length=1, max_length=2048)
    redacted_preview: str = Field(alias="redactedPreview", max_length=512)
    disposition: Literal["open"] = "open"


class ScanResult(ContractModel):
    """Authoritative scan envelope: provenance survives every outcome."""

    provenance: ScanProvenance
    job_state: ContainerJobState = Field(alias="jobState")
    exit_code: int | None = Field(None, alias="exitCode")
    failure_class: ContainerJobFailureClass | None = Field(
        None, alias="failureClass"
    )
    completeness: AnalysisCompleteness
    verdict: ScanVerdict
    coverage: tuple[str, ...] = Field(default=(), max_length=16)
    feed_freshness: dict[str, str] = Field(default_factory=dict, alias="feedFreshness")
    findings: tuple[PublicFinding, ...] = Field(default=(), max_length=SCAN_MAX_FINDINGS)
    inventory: tuple[InventoryEntry, ...] = Field(default=(), max_length=5000)
    skipped_paths: tuple[str, ...] = Field(default=(), alias="skippedPaths", max_length=1024)
    unsupported: tuple[str, ...] = Field(default=(), max_length=256)
    diagnostics: tuple[str, ...] = Field(default=(), max_length=64)
    summary_markdown: str = Field("", alias="summaryMarkdown", max_length=16384)
    logs_ref: str | None = Field(None, alias="logsRef", max_length=1024)
    artifacts_ref: str | None = Field(None, alias="artifactsRef", max_length=1024)
    raw_evidence_ref: str | None = Field(
        None, alias="rawEvidenceRef", max_length=1024
    )
    artifact_visibility: Literal["confidential"] = Field(
        "confidential", alias="artifactVisibility"
    )
    # Fail-closed invariant: a scan never authorizes source mutation,
    # publication, or suppression, and never discards evidence to do so.
    mutation_authorized: Literal[False] = Field(False, alias="mutationAuthorized")
    evidence_discarded: Literal[False] = Field(False, alias="evidenceDiscarded")

    @model_validator(mode="after")
    def _verdict_matches_completeness(self) -> "ScanResult":
        if self.verdict == ScanVerdict.CLEAN_WITH_DECLARED_COVERAGE:
            if self.completeness != AnalysisCompleteness.COMPLETE:
                raise ValueError("clean requires complete analysis")
            if self.findings:
                raise ValueError("clean requires zero findings")
        if self.completeness != AnalysisCompleteness.COMPLETE and self.verdict != (
            ScanVerdict.NOT_CLEAN
        ):
            raise ValueError("incomplete analysis must not read clean or findings-open")
        return self


def canonical_digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def validate_tool_pack(pack: RepoSecurityToolPack) -> RepoSecurityToolPack:
    """Fail fast on any pack that widens first-slice authority."""

    if pack.network_mode != "none":
        raise RepoSecurityToolPackError("first-slice scans require networkMode none")
    if pack.requires_docker_socket or pack.requires_new_service or pack.requires_new_worker:
        raise RepoSecurityToolPackError(
            "first-slice scans add no socket, service, or worker"
        )
    return pack


def build_scan_job_spec(
    *,
    pack: RepoSecurityToolPack,
    workspace_ref: WorkspaceLocator,
    scope: ScanScope,
    snapshot_path: str = "snapshot",
) -> ContainerJobSpec:
    """Build the real Container Job spec that executes one bounded scan.

    The spec is the wiring proof: the selected tool runs through the generic
    container-job service with read-only, network-none, bounded execution.
    """

    validate_tool_pack(pack)
    normalized_snapshot = normalize_scope_path(snapshot_path)
    scope_args = [f"--snapshot={normalized_snapshot}"]
    for include in scope.include_paths:
        scope_args.append(f"--include={include}")
    for exclude in scope.exclude_paths:
        scope_args.append(f"--exclude={exclude}")
    scope_args.append(f"--max-files={scope.max_files}")

    spec = ContainerJobSpec(
        imageSourceRef=pack.image_source_ref,
        workspaceRef=workspace_ref,
        command=[*pack.entrypoint, *scope_args],
        workdir="/workspace",
        networkMode="none",
        resources=ResourceLimits(
            cpuMillis=pack.cpu_millis,
            memoryMiB=pack.memory_mib,
        ),
        timeoutSeconds=pack.timeout_seconds,
        outputs=[
            OutputDeclaration(
                name="scan-native-json",
                relativePath="scan/native.json",
            ),
            OutputDeclaration(
                name="scan-summary-md",
                relativePath="scan/summary.md",
            ),
        ],
    )
    ensure_temporal_safe(spec)
    return spec


def parse_native_scanner_output(raw: str | bytes | dict[str, Any]) -> ParsedScannerOutput:
    """Parse untrusted native scanner output; malformed input fails loudly."""

    if isinstance(raw, bytes):
        if len(raw) > SCAN_MAX_NATIVE_BYTES:
            raise RepoSecurityToolPackError("scanner output exceeds size bound")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RepoSecurityToolPackError("scanner output is not utf-8") from exc
    elif isinstance(raw, str):
        if len(raw.encode("utf-8")) > SCAN_MAX_NATIVE_BYTES:
            raise RepoSecurityToolPackError("scanner output exceeds size bound")
        text = raw
    elif isinstance(raw, dict):
        payload: Any = raw
        text = ""
    else:
        raise RepoSecurityToolPackError("scanner output has an unsupported shape")

    if isinstance(raw, dict):
        payload = raw
    else:
        stripped = text.strip()
        if not stripped:
            raise RepoSecurityToolPackError("scanner output is empty")
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise RepoSecurityToolPackError(
                f"scanner output is not valid JSON: {exc}"
            ) from exc
    if not isinstance(payload, dict):
        raise RepoSecurityToolPackError("scanner output must be a JSON object")

    raw_findings = payload.get("findings", [])
    raw_inventory = payload.get("inventory", [])
    if not isinstance(raw_findings, list) or not isinstance(raw_inventory, list):
        raise RepoSecurityToolPackError("scanner output findings/inventory must be arrays")
    if len(raw_findings) > SCAN_MAX_FINDINGS:
        raise RepoSecurityToolPackError("scanner output reports too many findings")

    findings: list[NativeFinding] = []
    quarantined = 0
    for entry in raw_findings:
        if not isinstance(entry, dict):
            raise RepoSecurityToolPackError("scanner findings must be objects")
        try:
            findings.append(NativeFinding.model_validate(entry))
        except ValueError as exc:
            message = str(exc)
            if "finding path" in message or "paths must not" in message:
                # Hostile or escaping path: quarantine the finding and keep
                # the analysis incomplete rather than dropping it silently.
                quarantined += 1
                continue
            raise RepoSecurityToolPackError(
                f"invalid scanner finding: {exc}"
            ) from exc

    inventory: list[InventoryEntry] = []
    for entry in raw_inventory:
        if not isinstance(entry, dict):
            raise RepoSecurityToolPackError("scanner inventory must be objects")
        try:
            inventory.append(InventoryEntry.model_validate(entry))
        except ValueError as exc:
            raise RepoSecurityToolPackError(
                f"invalid scanner inventory entry: {exc}"
            ) from exc

    skipped = payload.get("skippedPaths", payload.get("skipped_paths", []))
    unsupported = payload.get("unsupported", [])
    if not isinstance(skipped, list) or not isinstance(unsupported, list):
        raise RepoSecurityToolPackError("skipped/unsupported must be arrays")
    truncated = bool(payload.get("truncated", False))
    normalized_skipped = tuple(str(p) for p in skipped)
    normalized_unsupported = tuple(str(u) for u in unsupported)

    return ParsedScannerOutput(
        findings=tuple(findings),
        inventory=tuple(inventory),
        skippedPaths=normalized_skipped,
        unsupported=normalized_unsupported,
        truncated=truncated,
        quarantinedPaths=quarantined,
    )


def classify_job_outcome(
    *,
    state: ContainerJobState,
    exit_code: int | None = None,
    failure_class: ContainerJobFailureClass | None = None,
) -> tuple[AnalysisCompleteness, tuple[str, ...]]:
    """Map the durable job outcome to analysis completeness (never clean)."""

    if state == ContainerJobState.SUCCEEDED and (exit_code in (None, 0)):
        return AnalysisCompleteness.COMPLETE, ()
    if state == ContainerJobState.CANCELED or failure_class == ContainerJobFailureClass.CANCELED:
        return AnalysisCompleteness.FAILED, ("scan was canceled; partial output is preserved")
    if state == ContainerJobState.TIMED_OUT or failure_class == ContainerJobFailureClass.TIMEOUT:
        return AnalysisCompleteness.FAILED, ("scan timed out; partial output is preserved",)
    if state in {
        ContainerJobState.FAILED,
        ContainerJobState.REJECTED,
        ContainerJobState.WORKSPACE_NOT_VISIBLE,
    }:
        detail = failure_class.value if failure_class is not None else state.value
        return AnalysisCompleteness.FAILED, (f"scan job ended as {detail}; evidence preserved",)
    return AnalysisCompleteness.FAILED, (f"scan job ended as {state.value}; evidence preserved",)


def public_finding(native: NativeFinding) -> PublicFinding:
    """Project one native finding into its ordinary-report form."""

    preview_source = native.secret_match or native.summary
    redacted = redact_sensitive_text(preview_source)
    if len(redacted) > 512:
        redacted = redacted[:509] + "..."
    return PublicFinding(
        id=native.identifier,
        category=native.category,
        severity=native.severity,
        confidence=native.confidence,
        path=native.path,
        line=native.line,
        summary=redact_sensitive_text(native.summary),
        redactedPreview=redacted,
    )


def evaluate_scan(
    *,
    provenance: ScanProvenance,
    state: ContainerJobState,
    exit_code: int | None = None,
    failure_class: ContainerJobFailureClass | None = None,
    parsed: ParsedScannerOutput | None = None,
    parser_error: str | None = None,
    now: datetime | None = None,
    logs_ref: str | None = None,
    artifacts_ref: str | None = None,
    raw_evidence_ref: str | None = None,
) -> ScanResult:
    """Bind provenance, job outcome, feeds, and parsed output into one result.

    The central no-false-clean rule lives here: only a succeeded job with
    valid parsed output, zero skipped or quarantined paths, zero unsupported
    entries, no truncation, and current feeds with zero open findings may
    read as clean with declared coverage.
    """

    diagnostics: list[str] = []
    completeness, job_notes = classify_job_outcome(
        state=state, exit_code=exit_code, failure_class=failure_class
    )
    diagnostics.extend(job_notes)

    if parser_error is not None:
        diagnostics.append(f"scanner output could not be parsed: {parser_error}")
        result = ScanResult(
            provenance=provenance,
            jobState=state,
            exitCode=exit_code,
            failureClass=failure_class,
            completeness=AnalysisCompleteness.FAILED,
            verdict=ScanVerdict.NOT_CLEAN,
            coverage=tuple(provenance.admitted_scope.coverage),
            feedFreshness=_feed_freshness_map(provenance, now=now),
            diagnostics=tuple(diagnostics),
            logsRef=logs_ref,
            artifactsRef=artifacts_ref,
            rawEvidenceRef=raw_evidence_ref,
        )
        return _with_summary(result)

    if parsed is None:
        raise RepoSecurityToolPackError(
            "evaluate_scan requires parsed output or a parser_error"
        )
    if completeness == AnalysisCompleteness.FAILED:
        result = ScanResult(
            provenance=provenance,
            jobState=state,
            exitCode=exit_code,
            failureClass=failure_class,
            completeness=AnalysisCompleteness.FAILED,
            verdict=ScanVerdict.NOT_CLEAN,
            coverage=tuple(provenance.admitted_scope.coverage),
            feedFreshness=_feed_freshness_map(provenance, now=now),
            inventory=parsed.inventory,
            skippedPaths=parsed.skipped_paths,
            unsupported=parsed.unsupported,
            diagnostics=tuple(diagnostics),
            logsRef=logs_ref,
            artifactsRef=artifacts_ref,
            rawEvidenceRef=raw_evidence_ref,
        )
        return _with_summary(result)

    feed_map = _feed_freshness_map(provenance, now=now)
    problems: list[str] = []
    if parsed.skipped_paths:
        problems.append(f"{len(parsed.skipped_paths)} skipped paths")
    if parsed.quarantined_paths:
        problems.append(f"{parsed.quarantined_paths} quarantined paths")
    if parsed.unsupported:
        problems.append(f"{len(parsed.unsupported)} unsupported inputs")
    if parsed.truncated:
        problems.append("truncated output")
    stale = sorted(name for name, value in feed_map.items() if value != "current")
    if stale:
        problems.append(f"non-current feeds: {', '.join(stale)}")
    diagnostics.extend(f"coverage gap: {problem}" for problem in problems)

    unsupported_only = (
        not parsed.findings
        and not parsed.inventory
        and not parsed.skipped_paths
        and parsed.unsupported
        and parsed.quarantined_paths == 0
        and not parsed.truncated
    )
    if unsupported_only:
        result = ScanResult(
            provenance=provenance,
            jobState=state,
            exitCode=exit_code,
            failureClass=failure_class,
            completeness=AnalysisCompleteness.UNSUPPORTED,
            verdict=ScanVerdict.NOT_CLEAN,
            coverage=tuple(provenance.admitted_scope.coverage),
            feedFreshness=feed_map,
            inventory=parsed.inventory,
            skippedPaths=parsed.skipped_paths,
            unsupported=parsed.unsupported,
            diagnostics=tuple(diagnostics or ("no supported inputs matched the admitted scope",)),
            logsRef=logs_ref,
            artifactsRef=artifacts_ref,
            rawEvidenceRef=raw_evidence_ref,
        )
        return _with_summary(result)

    if problems:
        result = ScanResult(
            provenance=provenance,
            jobState=state,
            exitCode=exit_code,
            failureClass=failure_class,
            completeness=AnalysisCompleteness.INCOMPLETE,
            verdict=ScanVerdict.NOT_CLEAN,
            coverage=tuple(provenance.admitted_scope.coverage),
            feedFreshness=feed_map,
            inventory=parsed.inventory,
            skippedPaths=parsed.skipped_paths,
            unsupported=parsed.unsupported,
            diagnostics=tuple(diagnostics),
            logsRef=logs_ref,
            artifactsRef=artifacts_ref,
            rawEvidenceRef=raw_evidence_ref,
        )
        return _with_summary(result)

    public = tuple(public_finding(f) for f in parsed.findings)
    verdict = (
        ScanVerdict.FINDINGS_OPEN if public else ScanVerdict.CLEAN_WITH_DECLARED_COVERAGE
    )
    if public:
        diagnostics.append(f"{len(public)} open findings require triage")
    else:
        diagnostics.append("zero findings for the declared successful coverage")
    result = ScanResult(
        provenance=provenance,
        jobState=state,
        exitCode=exit_code,
        failureClass=failure_class,
        completeness=AnalysisCompleteness.COMPLETE,
        verdict=verdict,
        coverage=tuple(provenance.admitted_scope.coverage),
        feedFreshness=feed_map,
        findings=public,
        inventory=parsed.inventory,
        diagnostics=tuple(diagnostics),
        logsRef=logs_ref,
        artifactsRef=artifacts_ref,
        rawEvidenceRef=raw_evidence_ref,
    )
    return _with_summary(result)


def _feed_freshness_map(
    provenance: ScanProvenance, *, now: datetime | None
) -> dict[str, str]:
    return {
        feed.name: feed.freshness(now=now).value for feed in provenance.feeds
    }


def _with_summary(result: ScanResult) -> ScanResult:
    """Attach the human-readable summary; a summary never changes disposition."""

    lines = [
        "# Repository security scan (bounded first slice)",
        "",
        f"Tool: {result.provenance.tool_name} {result.provenance.tool_version}",
        f"Input revision: {result.provenance.input_revision}",
        f"Content digest: {result.provenance.content_digest}",
        f"Completeness: {result.completeness.value}",
        f"Verdict: {result.verdict.value}",
        f"Coverage: {', '.join(result.coverage) or 'none declared'}",
    ]
    if result.feed_freshness:
        freshness = ", ".join(
            f"{name}={value}" for name, value in sorted(result.feed_freshness.items())
        )
        lines.append(f"Feeds: {freshness}")
    if result.findings:
        lines.append("")
        lines.append(f"Open findings: {len(result.findings)}")
        for finding in result.findings[:20]:
            lines.append(
                f"- [{finding.severity}] {finding.category} at "
                f"{finding.path}: {finding.redacted_preview}"
            )
    if result.diagnostics:
        lines.append("")
        lines.append("Diagnostics:")
        for diagnostic in result.diagnostics:
            lines.append(f"- {redact_sensitive_text(diagnostic)}")
    lines.append("")
    lines.append(
        "Findings remain open: this summary does not verify or resolve them. "
        "A fix is a separate authorized workflow with before-and-after evidence."
    )
    summary = "\n".join(lines)
    return result.model_copy(update={"summary_markdown": summary})


__all__ = [
    "DECLARED_SUPPORTED_LANGUAGES",
    "DECLARED_SUPPORTED_LOCKFILES",
    "FEED_MAX_AGE_SECONDS",
    "FIRST_SLICE_COVERAGE",
    "NETWORK_ACTIVE_REQUIRES_EXPLICIT_AUTHORITY",
    "PASSIVE_SLICE_CAPABILITY_LEVEL",
    "SCAN_MAX_FINDINGS",
    "SCAN_MAX_NATIVE_BYTES",
    "SCAN_MAX_OUTPUT_BYTES",
    "SCAN_MEMORY_MIB",
    "SCAN_CPU_MILLIS",
    "SCAN_TIMEOUT_SECONDS",
    "TOOL_PACK_CONTRACT_VERSION",
    "TOOL_PACK_NAME",
    "TOOL_PACK_OUTPUT_CONTRACT",
    "AnalysisCompleteness",
    "ContractModel",
    "FeedFreshness",
    "FeedInfo",
    "InventoryEntry",
    "NativeFinding",
    "ParsedScannerOutput",
    "PublicFinding",
    "RepoSecurityToolPack",
    "RepoSecurityToolPackError",
    "ScanProvenance",
    "ScanResult",
    "ScanScope",
    "ScanVerdict",
    "build_scan_job_spec",
    "canonical_digest",
    "classify_job_outcome",
    "default_first_slice_pack",
    "evaluate_scan",
    "normalize_finding_path",
    "normalize_scope_path",
    "parse_native_scanner_output",
    "public_finding",
    "validate_tool_pack",
]
