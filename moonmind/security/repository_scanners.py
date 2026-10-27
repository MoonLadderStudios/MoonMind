"""Small repository-scanner adapters on the existing job and assessment contracts."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field

from moonmind.schemas.repository_scanner_models import RepositoryScan
from moonmind.security.assessments import Contract, Finding, Items, Text, content_key
from moonmind.utils.logging import redact_sensitive_text

Scanner = Literal["gitleaks", "opengrep", "trivy"]
WITHHELD_LOG = "Scanner console output withheld; see sanitized native evidence."
MAX_REPORT_BYTES = 8 * 1024 * 1024


def build_scanner_job(
    scan: RepositoryScan, *, image_source_ref="repository-security"
) -> dict:
    """Workload only; existing transports stamp workspace and session authority."""
    return {
        "imageSourceRef": image_source_ref,
        "repositoryScan": scan.model_dump(mode="json", by_alias=True),
        "workspaceReadOnly": True,
        "entrypoint": ["python"],
        "command": [
            "-I",
            "-m",
            "moonmind.security.repository_scanner_runner",
            scan.model_dump_json(by_alias=True),
        ],
        "networkMode": "none",
        "workdir": "/workspace",
        "resources": {
            "cpuMillis": 2000,
            # tmpfs is charged to the container's memory cgroup too. Keep the
            # scanner's 2 GiB allowance in addition to its staged copies.
            "memoryMiB": 2048 + (scan.scratch_bytes + 1024**2 - 1) // 1024**2,
            "pids": 256,
        },
        "timeoutSeconds": scan.timeout_seconds + 15,
        "outputs": scan.outputs(),
        "caches": (
            [
                {
                    "cacheRef": scan.database_cache_ref,
                    "target": "/opt/moonmind-scanner-db",
                    "readOnly": True,
                }
            ]
            if scan.database_cache_ref
            else []
        ),
    }


def validate_scanner_job(spec) -> None:
    """Rechecked by admission and the worker, including model-copy callers."""
    scan = spec.repository_scan
    if scan is None:
        return
    canonical = build_scanner_job(scan)
    for field, expected in (
        ("entrypoint", canonical["entrypoint"]),
        ("command", canonical["command"]),
        ("workspace_read_only", True),
        ("network_mode", "none"),
        ("workdir", "/workspace"),
        ("environment", []),
    ):
        if getattr(spec, field) != expected:
            raise ValueError("scanner workload differs from admitted invocation")
    if spec.timeout_seconds != canonical["timeoutSeconds"]:
        raise ValueError("scanner timeout differs from admitted bound")
    if [v.model_dump(by_alias=True) for v in spec.outputs] != scan.outputs():
        raise ValueError("scanner outputs differ from admitted locations")
    if [v.model_dump(by_alias=True) for v in spec.caches] != canonical["caches"]:
        raise ValueError("scanner cache mounts differ from admitted inputs")


def _document(scanner: str, body: bytes, limit: int):
    if len(body) > limit:
        raise ValueError("scanner report exceeds bound")
    result = json.loads(body)
    if scanner == "gitleaks":
        valid = isinstance(result, list)
    elif scanner == "opengrep":
        valid = (
            isinstance(result, dict)
            and isinstance(result.get("results"), list)
            and isinstance(result.get("errors"), list)
            and isinstance(result.get("paths"), dict)
            and isinstance(result["paths"].get("scanned"), list)
        )
    else:
        valid = (
            isinstance(result, dict)
            and result.get("SchemaVersion") == 2
            and isinstance(result.get("Results"), list)
        )
    if not valid:
        raise ValueError("unsupported scanner report schema")
    return result


# Source snippets and arbitrary diagnostics are never ordinary evidence. Retain
# the native structure, numbers, identifiers and locations; remove free text.
_UNSAFE = {
    "secret",
    "match",
    "line",
    "lines",
    "content",
    "abstract_content",
    "message",
    "description",
    "title",
    "author",
    "email",
    "code",
    "snippet",
    "text",
    "summary",
    "resolution",
}
_IDENTIFIERS = {
    "ruleid",
    "file",
    "commit",
    "fingerprint",
    "check_id",
    "path",
    "version",
    "type",
    "severity",
    "target",
    "vulnerabilityid",
    "pkgname",
    "installedversion",
    "fixedversion",
    "id",
    "avdid",
    "status",
    "category",
    "scanned",
    "skipped",
    "class",
    "rule_id",
}


class ScannerOutputSanitizer:
    """One job's native evidence and metadata share the same redaction owner."""

    def __init__(self, redacted_metadata_hashes=()):
        self.secrets: set[str] = set()
        # The runner cannot send secret values to ingestion. Hashes of metadata
        # strings it redacted let ingestion also protect the original admission.
        self.redacted_metadata_hashes = set(redacted_metadata_hashes)

    def observe_native(self, scanner: str, body: bytes, limit=MAX_REPORT_BYTES):
        native = _document(scanner, body, limit)
        self._collect(native)
        return native

    def _collect(self, value, key=""):
        if isinstance(value, dict):
            for k, v in value.items():
                self._collect(v, k.lower())
        elif isinstance(value, list):
            for v in value:
                self._collect(v, key)
        elif (
            isinstance(value, str)
            and value
            and value != "[REDACTED]"
            and key
            in {
                "secret",
                "match",
                "abstract_content",
                "lines",
                "content",
                "snippet",
            }
        ):
            self.secrets.add(value)

    def _identifier(self, value: str) -> str:
        digest = content_key(value)
        if (
            digest in self.redacted_metadata_hashes
            or any(secret in value for secret in self.secrets)
            or redact_sensitive_text(value) != value
        ):
            self.redacted_metadata_hashes.add(digest)
            return "redacted-" + digest
        return value

    def _clean(self, value, key="", *, native=False):
        if isinstance(value, dict):
            # JSON keys are untrusted too; native schema keys remain stable.
            return {
                self._identifier(k): self._clean(v, k.lower(), native=native)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self._clean(v, key, native=native) for v in value]
        if isinstance(value, str):
            return (
                self._identifier(value)
                if not native or (key in _IDENTIFIERS and key not in _UNSAFE)
                else "[REDACTED]"
            )
        return value

    def metadata(self, value):
        return self._clean(value)


def sanitize_native(
    scanner: str,
    body: bytes,
    limit: int = MAX_REPORT_BYTES,
    *,
    sanitizer: ScannerOutputSanitizer | None = None,
) -> bytes:
    sanitizer = sanitizer or ScannerOutputSanitizer()
    native = sanitizer.observe_native(scanner, body, limit)
    safe = json.dumps(
        sanitizer._clean(native, native=True), sort_keys=True, separators=(",", ":")
    ).encode()
    if len(safe) > limit:
        raise ValueError("sanitized scanner report exceeds bound")
    return safe


class NativeResult(Contract):
    execution: Literal["succeeded", "failed", "timed_out", "canceled"]
    coverage: Literal["complete", "partial"]
    findings: list[Finding] = Field(default_factory=list, max_length=4096)
    examined_assets: Items = Field(default_factory=list)
    unexamined_assets: Items = Field(default_factory=list)
    limitations: Items = Field(default_factory=list)
    verification: Literal["not_performed"] = "not_performed"


def _asset(path: str) -> str:
    for prefix in ("/workspace/", "/scan-input/"):
        path = path.removeprefix(prefix)
    value = PurePosixPath(path)
    if value.is_absolute() or ".." in value.parts or not value.parts:
        raise ValueError("finding outside admitted input")
    return value.as_posix()


def normalize_native(
    scanner: Scanner,
    body: bytes,
    *,
    exit_code: int | None,
    terminal_state: str,
    native_ref: str,
) -> NativeResult:
    execution = (
        terminal_state
        if terminal_state in {"succeeded", "timed_out", "canceled"}
        else "failed"
    )
    limitations = []
    accepted = {0, 1} if scanner == "gitleaks" else {0}
    if exit_code not in accepted:
        execution = execution if execution != "succeeded" else "failed"
        limitations.append("scanner_exit_failure")
    if execution != "succeeded":
        limitations.append("execution_incomplete")
    findings, examined, unexamined = [], set(), set()
    try:
        native = _document(scanner, body, MAX_REPORT_BYTES)

        def finding(rule, asset, identity):
            if not isinstance(rule, str) or not rule:
                raise ValueError("missing native rule")
            findings.append(
                Finding(
                    source="repository",
                    tool=scanner,
                    rule_id=rule,
                    asset=_asset(asset),
                    native_id=content_key(identity),
                    evidence_refs=[native_ref],
                )
            )
            if len(findings) > 4096:
                raise ValueError("finding bound exceeded")

        if scanner == "gitleaks":
            for item in native:
                try:
                    finding(
                        item["RuleID"],
                        item["File"],
                        [
                            item["RuleID"],
                            item["File"],
                            item["StartLine"],
                            item.get("StartColumn"),
                            item.get("Commit"),
                        ],
                    )
                except (ValueError, KeyError, TypeError, AttributeError):
                    limitations.append("missing_malformed_or_truncated_report")
                if len(findings) > 4096:
                    break
            if exit_code == 1 and not native:
                limitations.append("findings_exit_without_findings")
        elif scanner == "opengrep":
            if native["errors"] or native["paths"].get("skipped"):
                limitations.append("unsupported_or_parser_failed_inputs")
            for path in native["paths"]["scanned"]:
                try:
                    examined.add(_asset(path))
                except (ValueError, TypeError, AttributeError):
                    limitations.append("missing_malformed_or_truncated_report")
            for skipped in native["paths"].get("skipped") or []:
                try:
                    unexamined.add(
                        _asset(
                            skipped["path"] if isinstance(skipped, dict) else skipped
                        )
                    )
                except (ValueError, KeyError, TypeError, AttributeError):
                    limitations.append("missing_malformed_or_truncated_report")
            for error in native["errors"]:
                try:
                    paths = ([error["path"]] if "path" in error else []) + [
                        span["file"] for span in error.get("spans", [])
                    ]
                    unexamined.update(_asset(path) for path in paths)
                except (ValueError, KeyError, TypeError, AttributeError):
                    limitations.append("missing_malformed_or_truncated_report")
            examined -= unexamined
            if not examined:
                limitations.append("no_inputs_examined")
            for item in native["results"]:
                try:
                    finding(
                        item["check_id"],
                        item["path"],
                        [
                            item["check_id"],
                            item["path"],
                            item["start"],
                            item.get("end"),
                        ],
                    )
                except (ValueError, KeyError, TypeError, AttributeError):
                    limitations.append("missing_malformed_or_truncated_report")
        else:
            for result in native["Results"]:
                try:
                    target = _asset(result["Target"])
                    families = ("Vulnerabilities", "Misconfigurations", "Secrets")
                    # Empty finding arrays are valid. Trivy may also omit them
                    # for clean targets while retaining analysis class/type.
                    if not any(f in result for f in families) and not (
                        result.get("Class")
                        in {"os-pkgs", "lang-pkgs", "config", "secret"}
                        and isinstance(result.get("Type"), str)
                        and result["Type"]
                    ):
                        raise ValueError("missing native analysis")
                    if result.get("Error"):
                        limitations.append("scanner_analysis_error")
                        unexamined.add(target)
                    else:
                        examined.add(target)
                except (ValueError, KeyError, TypeError, AttributeError):
                    limitations.append("missing_malformed_or_truncated_report")
                    continue
                for family, rule_key in (
                    ("Vulnerabilities", "VulnerabilityID"),
                    ("Misconfigurations", "ID"),
                    ("Secrets", "RuleID"),
                ):
                    items = result.get(family, [])
                    if not isinstance(items, list):
                        limitations.append("missing_malformed_or_truncated_report")
                        unexamined.add(target)
                        continue
                    for item in items:
                        try:
                            rule = item[rule_key]
                            if not isinstance(rule, str) or not rule:
                                raise ValueError("missing native rule")
                            if (
                                family == "Misconfigurations"
                                and item.get("Status") == "PASS"
                            ):
                                continue
                            finding(
                                rule,
                                target,
                                [
                                    family,
                                    rule,
                                    target,
                                    item.get("PkgName"),
                                    item.get("InstalledVersion"),
                                    item.get("StartLine"),
                                    item.get("CauseMetadata", {}).get("StartLine"),
                                ],
                            )
                        except (ValueError, KeyError, TypeError, AttributeError):
                            limitations.append("missing_malformed_or_truncated_report")
                            unexamined.add(target)
            if not examined:
                limitations.append("no_inputs_examined")
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        limitations.append("missing_malformed_or_truncated_report")
    findings = list({f.finding_id: f for f in findings[:4096]}.values())
    if len(examined) > 4096 or len(unexamined) > 4096:
        limitations.append("examined_input_bound_exceeded")
    return NativeResult(
        execution=execution,
        coverage="partial" if limitations else "complete",
        findings=findings,
        examined_assets=sorted(examined - unexamined)[:4096],
        unexamined_assets=sorted(unexamined)[:4096],
        limitations=sorted(set(limitations)),
    )


class ScannerExecution(Contract):
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    assets: Items
    limitations: list[Annotated[str, Field(pattern=r"^[a-z_]{1,80}$")]] = Field(
        default_factory=list, max_length=128
    )
    version: str = Field(
        pattern=r"^(?:[0-9]+\.){1,3}[0-9]+(?:[-+][A-Za-z0-9.-]+)?$|^unavailable$"
    )
    exits: dict[str, int | None]
    states: dict[str, Literal["succeeded", "failed", "timed_out", "canceled"]]
    git_refs: list[Annotated[str, Field(pattern=r"^[a-f0-9]{40,64}$")]] = Field(
        default_factory=list, max_length=1024
    )
    shallow: bool | None = None
    git_metadata: bool = False
    untracked_files: Items = Field(default_factory=list)
    redacted_metadata_hashes: list[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] = (
        Field(default_factory=list, max_length=16384)
    )
    database_digest: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")


class ScannerSummary(Contract):
    schema_version: Literal["repository-scanner/v1"] = "repository-scanner/v1"
    scanner: Scanner
    admission: RepositoryScan | None
    execution: ScannerExecution | None
    results: dict[str, NativeResult]
    native_refs: dict[str, Text]
    coverage: Literal["complete", "partial"]
    limitations: Items
    verification: Literal["not_performed"] = "not_performed"
