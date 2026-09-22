"""One passive repository secret-exposure scan (MoonLadderStudios/MoonMind#3970).

Bounded product extension, not a security platform: a single maintained
scanner (the in-repo outbound-scan regex contract, which needs no external
feed, image, or network) runs read-only over one declared input class (text
files in an authorized immutable snapshot directory) and retains its native
machine-readable output plus a small readable summary through the existing
artifact store.

Scanner selection (verified before adoption):

- official interface: :func:`moonmind.security.outbound_scan.scan_outbound_text`
  with enforced mode (``high_security_mode=True``), forced per file so a
  disabled operator default can never downgrade this journey to passthrough;
- licensing: in-repo MoonMind code, no new dependency;
- image provenance: not applicable -- in-process scan, no scanner image is
  pulled, installed into MoonMind's main image, or executed from a collection;
- configuration: :class:`PassiveScanConfig` bounds (files, per-file bytes,
  total bytes) recorded in result metadata;
- output behavior: ``OutboundFinding`` category/location/redacted-preview
  triples; raw matches never leave the scan boundary.

No ToolPack database/schema family, no scanner collection, no universal
findings model, no feed service, and no assessment-target network access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from moonmind.security.outbound_scan import scan_outbound_text
from moonmind.utils.logging import redact_sensitive_text

#: Stable tool identity recorded in result metadata (not a compatibility
#: fingerprint: it names the scan policy that produced a report and never
#: blocks unrelated jobs).
PASSIVE_SCAN_TOOL_REF = "moonmind.security.passive_repo_scan.v1"

#: Disclaimer carried by every summary: zero findings describe covered input
#: only, never whole-repository security.
ZERO_FINDINGS_DISCLAIMER = (
    "Zero findings cover only the scanned files listed above; "
    "they do not mean the whole repository is secure."
)

ScanVerdict = Literal["finding_present", "clean_with_coverage", "incomplete"]

_MAX_LOCATION_CHARS = 200
_MAX_PREVIEW_CHARS = 500
_MAX_SKIPPED_ENTRIES = 200


@dataclass(frozen=True, slots=True)
class PassiveScanConfig:
    """Existing job-control bounds for one passive scan."""

    max_files: int = 500
    max_bytes_per_file: int = 256 * 1024
    max_total_bytes: int = 5 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ScanFinding:
    category: str
    location: str
    redacted_preview: str


@dataclass(frozen=True, slots=True)
class SkippedFile:
    path: str
    reason: str


@dataclass(slots=True)
class PassiveScanReport:
    """Native machine-readable scan outcome plus a small readable summary."""

    verdict: ScanVerdict
    tool_ref: str = PASSIVE_SCAN_TOOL_REF
    input_digest: str = ""
    findings: list[ScanFinding] = field(default_factory=list)
    covered_files: list[str] = field(default_factory=list)
    files_scanned: int = 0
    bytes_scanned: int = 0
    files_skipped: list[SkippedFile] = field(default_factory=list)
    incomplete_reason: str | None = None
    cancelled: bool = False
    config: PassiveScanConfig = field(default_factory=PassiveScanConfig)
    feed: dict[str, str] = field(
        default_factory=lambda: {"name": "none", "reason": "offline regex scan"}
    )
    summary_markdown: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "toolRef": self.tool_ref,
            "verdict": self.verdict,
            "inputDigest": self.input_digest,
            "feed": dict(self.feed),
            "config": {
                "maxFiles": self.config.max_files,
                "maxBytesPerFile": self.config.max_bytes_per_file,
                "maxTotalBytes": self.config.max_total_bytes,
            },
            "filesScanned": self.files_scanned,
            "bytesScanned": self.bytes_scanned,
            "coveredFiles": list(self.covered_files),
            "findings": [
                {
                    "category": finding.category,
                    "location": finding.location,
                    "redactedPreview": finding.redacted_preview,
                }
                for finding in self.findings
            ],
            "filesSkipped": [
                {"path": skipped.path, "reason": skipped.reason}
                for skipped in self.files_skipped
            ],
            "incompleteReason": self.incomplete_reason,
            "cancelled": self.cancelled,
            "summaryMarkdown": self.summary_markdown,
        }


def _safe_relpath(root: Path, path: Path) -> str | None:
    """Return the bounded display path, or None when unsafe/unrepresentable."""

    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    text = relative.as_posix()
    if not text or text in {".", ".."} or text.startswith(("../", "/")):
        return None
    if any(part in {"", ".", ".."} for part in text.split("/")):
        return None
    return redact_sensitive_text(text)[:_MAX_LOCATION_CHARS]


def _snapshot_files(root: Path, *, config: PassiveScanConfig) -> tuple[list[Path], list[SkippedFile], str | None]:
    """List regular files under root; symlinks escaping root are unsafe."""

    collected: list[Path] = []
    skipped: list[SkippedFile] = []
    ordered = sorted(root.rglob("*"))
    for path in ordered:
        if path.is_symlink():
            try:
                resolved = path.resolve()
            except OSError:
                resolved = None
            if resolved is None or resolved != root and root not in resolved.parents:
                skipped.append(
                    SkippedFile(
                        path=_safe_relpath(root, path) or "scan:unrepresentable-path",
                        reason="unsafe symlink target outside snapshot",
                    )
                )
                continue
            if resolved.is_dir():
                continue
            collected.append(path)
            continue
        if path.is_dir():
            continue
        if path.is_file():
            collected.append(path)
    if len(collected) > config.max_files:
        skipped.append(
            SkippedFile(
                path="scan:snapshot",
                reason=(
                    f"file count exceeds max_files={config.max_files}; "
                    "remaining files not inspected"
                ),
            )
        )
        collected = collected[: config.max_files]
        return collected, skipped, "file-count bound exceeded"
    return collected, skipped, None


def _read_scan_text(path: Path, *, config: PassiveScanConfig) -> tuple[str | None, str | None]:
    """Return (text, skip_reason) for one file; binary/undecodable is skipped."""

    try:
        size = path.stat().st_size
    except OSError:
        return None, "unreadable file"
    if size > config.max_bytes_per_file:
        return None, f"exceeds max_bytes_per_file={config.max_bytes_per_file}"
    try:
        raw = path.read_bytes()
    except OSError:
        return None, "unreadable file"
    if b"\x00" in raw:
        return None, "binary content not inspected as text"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "unsupported encoding (not UTF-8 text)"


def _input_digest(entries: list[tuple[str, str]]) -> str:
    canonical = "\n".join(f"{relpath}:{digest}" for relpath, digest in sorted(entries))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _render_summary(
    *,
    verdict: ScanVerdict,
    findings: list[ScanFinding],
    covered_files: list[str],
    files_scanned: int,
    bytes_scanned: int,
    files_skipped: list[SkippedFile],
    incomplete_reason: str | None,
    input_digest: str,
    config: PassiveScanConfig,
) -> str:
    lines = [
        "# Passive repository scan",
        "",
        f"Tool: `{PASSIVE_SCAN_TOOL_REF}` (offline secret-exposure scan, no external feed).",
        f"Input digest: `{input_digest or 'unavailable'}`",
        (
            f"Bounds: max_files={config.max_files}, "
            f"max_bytes_per_file={config.max_bytes_per_file}, "
            f"max_total_bytes={config.max_total_bytes}."
        ),
        "",
        f"Verdict: **{verdict}**",
        f"Files scanned: {files_scanned} ({bytes_scanned} bytes).",
        "",
    ]
    if verdict == "finding_present":
        by_category: dict[str, int] = {}
        for finding in findings:
            by_category[finding.category] = by_category.get(finding.category, 0) + 1
        breakdown = ", ".join(f"{category}: {count}" for category, count in sorted(by_category.items()))
        lines.append(f"Findings: {len(findings)} ({breakdown}). Locations name scanned files only; secret values are redacted.")
        for finding in findings[:20]:
            lines.append(f"- {finding.category} at `{finding.location}`: {finding.redacted_preview}")
        if len(findings) > 20:
            lines.append(f"- ... and {len(findings) - 20} further redacted findings (see native output).")
        lines.append("")
    elif verdict == "clean_with_coverage":
        lines.append("Findings: none in the covered files listed below.")
        lines.append("")
    else:
        lines.append(
            f"Incomplete analysis: {redact_sensitive_text(incomplete_reason or 'coverage incomplete')[:500]}"
        )
        lines.append("This result is not clean: skipped or uninspected input cannot pass as clean.")
        lines.append("")
    if covered_files:
        lines.append("Covered files:")
        for relpath in covered_files[:50]:
            lines.append(f"- `{relpath}`")
        if len(covered_files) > 50:
            lines.append(f"- ... and {len(covered_files) - 50} further files (see native output).")
        lines.append("")
    if files_skipped:
        lines.append("Skipped (not inspected):")
        for skipped in files_skipped[:_MAX_SKIPPED_ENTRIES]:
            lines.append(f"- `{skipped.path}`: {redact_sensitive_text(skipped.reason)[:200]}")
        lines.append("")
    lines.append(f"Note: {ZERO_FINDINGS_DISCLAIMER}")
    lines.append("Source fixes or publication are separate authorized work, not a scan side effect.")
    return "\n".join(lines).rstrip() + "\n"


def run_passive_scan(
    snapshot: str | Path,
    *,
    config: PassiveScanConfig | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PassiveScanReport:
    """Scan one authorized snapshot directory without side effects or network."""

    effective = config or PassiveScanConfig()
    findings: list[ScanFinding] = []
    covered: list[str] = []
    skipped: list[SkippedFile] = []
    digest_entries: list[tuple[str, str]] = []
    bytes_scanned = 0
    incomplete_reason: str | None = None
    was_cancelled = False

    root = Path(snapshot)
    try:
        resolved_root = root.resolve()
    except OSError:
        resolved_root = None
    if resolved_root is None or not resolved_root.is_dir():
        report = PassiveScanReport(
            verdict="incomplete",
            input_digest="",
            files_scanned=0,
            bytes_scanned=0,
            files_skipped=[],
            incomplete_reason="snapshot is not a readable directory",
            config=effective,
        )
        report.summary_markdown = _render_summary(
            verdict="incomplete",
            findings=[],
            covered_files=[],
            files_scanned=0,
            bytes_scanned=0,
            files_skipped=[],
            incomplete_reason=report.incomplete_reason,
            input_digest="",
            config=effective,
        )
        return report

    files, early_skipped, early_reason = _snapshot_files(resolved_root, config=effective)
    skipped.extend(early_skipped)
    if early_reason is not None:
        incomplete_reason = early_reason

    for path in files:
        if cancelled is not None:
            try:
                if bool(cancelled()):
                    was_cancelled = True
                    incomplete_reason = "cancelled by caller"
                    break
            except Exception:
                was_cancelled = True
                incomplete_reason = "cancellation check failed; scan stopped"
                break
        relpath = _safe_relpath(resolved_root, path)
        if relpath is None:
            skipped.append(
                SkippedFile(path="scan:unrepresentable-path", reason="unsafe path inside snapshot")
            )
            incomplete_reason = incomplete_reason or "unsafe path inside snapshot"
            continue
        text, skip_reason = _read_scan_text(path, config=effective)
        if skip_reason is not None or text is None:
            skipped.append(SkippedFile(path=relpath, reason=skip_reason or "unreadable file"))
            incomplete_reason = incomplete_reason or f"unsupported input: {relpath}"
            continue
        encoded = text.encode("utf-8")
        if bytes_scanned + len(encoded) > effective.max_total_bytes:
            skipped.append(
                SkippedFile(
                    path=relpath,
                    reason=f"exceeds max_total_bytes={effective.max_total_bytes}",
                )
            )
            incomplete_reason = incomplete_reason or "total-bytes bound exceeded"
            continue
        digest_entries.append((relpath, hashlib.sha256(encoded).hexdigest()))
        bytes_scanned += len(encoded)
        covered.append(relpath)
        result = scan_outbound_text(text, location=f"scan:{relpath}", high_security_mode=True)
        for finding in result.findings:
            findings.append(
                ScanFinding(
                    category=str(finding.category),
                    location=redact_sensitive_text(finding.location)[:_MAX_LOCATION_CHARS],
                    redacted_preview=redact_sensitive_text(finding.redacted_preview)[
                        :_MAX_PREVIEW_CHARS
                    ],
                )
            )

    input_digest = _input_digest(digest_entries) if digest_entries else ""
    if was_cancelled or incomplete_reason is not None or skipped:
        verdict: ScanVerdict = "incomplete"
        if incomplete_reason is None:
            incomplete_reason = "coverage incomplete: files skipped"
    elif findings:
        verdict = "finding_present"
    else:
        verdict = "clean_with_coverage"

    summary = _render_summary(
        verdict=verdict,
        findings=findings,
        covered_files=covered,
        files_scanned=len(covered),
        bytes_scanned=bytes_scanned,
        files_skipped=skipped,
        incomplete_reason=incomplete_reason,
        input_digest=input_digest,
        config=effective,
    )
    return PassiveScanReport(
        verdict=verdict,
        input_digest=input_digest,
        findings=findings,
        covered_files=covered,
        files_scanned=len(covered),
        bytes_scanned=bytes_scanned,
        files_skipped=skipped,
        incomplete_reason=incomplete_reason,
        cancelled=was_cancelled,
        config=effective,
        summary_markdown=summary,
    )


def parse_scan_report_json(payload: bytes) -> PassiveScanReport:
    """Parse retained native output; malformed/truncated payloads are incomplete."""

    try:
        decoded = bytes(payload).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return _broken_report("retained output is not UTF-8")
    try:
        data = json.loads(decoded)
    except (json.JSONDecodeError, ValueError):
        return _broken_report("retained output is malformed JSON")
    if not isinstance(data, Mapping):
        return _broken_report("retained output has no report object")
    try:
        verdict = str(data.get("verdict"))
        if verdict not in {"finding_present", "clean_with_coverage", "incomplete"}:
            raise ValueError("unknown verdict")
        findings_raw = data.get("findings", [])
        skipped_raw = data.get("filesSkipped", [])
        covered_raw = data.get("coveredFiles", [])
        if not isinstance(findings_raw, list) or not isinstance(
            skipped_raw, list
        ) or not isinstance(covered_raw, list):
            raise ValueError("report collections malformed")
        findings: list[ScanFinding] = []
        for item in findings_raw:
            if not isinstance(item, Mapping):
                raise ValueError("finding entry malformed")
            findings.append(
                ScanFinding(
                    category=redact_sensitive_text(str(item.get("category", "")))[:64],
                    location=redact_sensitive_text(str(item.get("location", "")))[
                        :_MAX_LOCATION_CHARS
                    ],
                    redacted_preview=redact_sensitive_text(
                        str(item.get("redactedPreview", ""))
                    )[:_MAX_PREVIEW_CHARS],
                )
            )
        skipped: list[SkippedFile] = []
        for item in skipped_raw:
            if not isinstance(item, Mapping):
                raise ValueError("skipped entry malformed")
            skipped.append(
                SkippedFile(
                    path=redact_sensitive_text(str(item.get("path", "")))[
                        :_MAX_LOCATION_CHARS
                    ],
                    reason=redact_sensitive_text(str(item.get("reason", "")))[:200],
                )
            )
        covered = [redact_sensitive_text(str(item))[:_MAX_LOCATION_CHARS] for item in covered_raw]
        tool_ref = str(data.get("toolRef") or PASSIVE_SCAN_TOOL_REF)
        declared_digest = redact_sensitive_text(str(data.get("inputDigest") or ""))[:64]
        # A truncated or inconsistent payload must never become a clean result:
        # findings with a clean verdict, or skipped files with a clean verdict,
        # fail closed to incomplete.
        if verdict == "clean_with_coverage" and (findings or skipped):
            return _broken_report("retained output inconsistent: clean verdict with findings or skipped files")
        if verdict == "finding_present" and not findings:
            return _broken_report("retained output inconsistent: finding verdict without findings")
        return PassiveScanReport(
            verdict=verdict,  # type: ignore[arg-type]
            tool_ref=tool_ref,
            input_digest=declared_digest,
            findings=findings,
            covered_files=covered,
            files_scanned=int(data.get("filesScanned", len(covered))),
            bytes_scanned=int(data.get("bytesScanned", 0)),
            files_skipped=skipped,
            incomplete_reason=(
                redact_sensitive_text(str(data.get("incompleteReason") or ""))[:500]
                or None
            ),
            summary_markdown=redact_sensitive_text(str(data.get("summaryMarkdown") or ""))[
                :20000
            ],
        )
    except (ValueError, TypeError, AttributeError):
        return _broken_report("retained output failed validation")


def _broken_report(reason: str) -> PassiveScanReport:
    report = PassiveScanReport(
        verdict="incomplete",
        input_digest="",
        incomplete_reason=reason,
    )
    report.summary_markdown = _render_summary(
        verdict="incomplete",
        findings=[],
        covered_files=[],
        files_scanned=0,
        bytes_scanned=0,
        files_skipped=[],
        incomplete_reason=reason,
        input_digest="",
        config=report.config,
    )
    return report


def retain_scan_report(store: Any, report: PassiveScanReport) -> dict[str, str]:
    """Retain native output + summary through the existing artifact store.

    The returned artifact refs stay readable after the job container is
    cleaned up; the store (not the job workspace) owns the bytes.
    """

    payload = report.to_payload()
    native_ref = store.put_json(
        payload,
        metadata={
            "toolRef": PASSIVE_SCAN_TOOL_REF,
            "verdict": report.verdict,
            "inputDigest": report.input_digest,
        },
    )
    summary_ref = store.put_bytes(
        report.summary_markdown.encode("utf-8"),
        content_type="text/markdown",
        metadata={
            "toolRef": PASSIVE_SCAN_TOOL_REF,
            "verdict": report.verdict,
            "inputDigest": report.input_digest,
        },
    )
    return {
        "native_ref": native_ref.artifact_ref,
        "summary_ref": summary_ref.artifact_ref,
    }


def build_scan_job_workload(
    *,
    snapshot_relative_path: str = "repo",
    report_relative_path: str = "artifacts/passive-scan-report.json",
    summary_relative_path: str = "artifacts/passive-scan-summary.md",
    cpu_millis: int = 2000,
    memory_mib: int = 1024,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    """Return workload-only container-job fields running this scan.

    The caller stamps ``workspaceRef``/correlation through the existing
    ``container_job_submission`` path; this helper only declares the read-only,
    network-isolated, bounded workload (existing ``moonmind-python-tests``
    image source, which ships this module).
    """

    for label, value in (
        ("snapshot", snapshot_relative_path),
        ("report", report_relative_path),
        ("summary", summary_relative_path),
    ):
        normalized = str(value or "").strip().replace("\\", "/")
        parts = normalized.split("/")
        if not normalized or normalized.startswith("/") or any(
            part in {"", ".", ".."} for part in parts
        ):
            raise ValueError(f"scan job {label} path must be normalized and relative")
    return {
        "imageSourceRef": "moonmind-python-tests",
        "workspaceReadOnly": True,
        "command": [
            "python",
            "-m",
            "moonmind.security.passive_repo_scan",
            "--snapshot",
            snapshot_relative_path,
            "--report",
            report_relative_path,
            "--summary",
            summary_relative_path,
        ],
        "workdir": "/workspace",
        "networkMode": "none",
        "resources": {"cpuMillis": cpu_millis, "memoryMiB": memory_mib, "pids": 256},
        "timeoutSeconds": timeout_seconds,
        "outputs": [
            {"name": "scan-report", "relativePath": report_relative_path},
            {"name": "scan-summary", "relativePath": summary_relative_path},
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """Container-job entrypoint: scan a snapshot, write report + summary files."""

    parser = argparse.ArgumentParser(description="Passive repository secret-exposure scan")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--max-files", type=int, default=PassiveScanConfig().max_files)
    parser.add_argument(
        "--max-bytes-per-file",
        type=int,
        default=PassiveScanConfig().max_bytes_per_file,
    )
    parser.add_argument(
        "--max-total-bytes",
        type=int,
        default=PassiveScanConfig().max_total_bytes,
    )
    args = parser.parse_args(argv)
    try:
        config = PassiveScanConfig(
            max_files=args.max_files,
            max_bytes_per_file=args.max_bytes_per_file,
            max_total_bytes=args.max_total_bytes,
        )
        if (
            config.max_files < 1
            or config.max_bytes_per_file < 1
            or config.max_total_bytes < 1
        ):
            raise ValueError("bounds must be positive")
    except ValueError as exc:
        parser.error(str(exc))
    report = run_passive_scan(args.snapshot, config=config)
    try:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps(report.to_payload(), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary).write_text(report.summary_markdown, encoding="utf-8")
    except OSError:
        return 4
    if report.verdict == "incomplete":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PASSIVE_SCAN_TOOL_REF",
    "ZERO_FINDINGS_DISCLAIMER",
    "PassiveScanConfig",
    "PassiveScanReport",
    "ScanFinding",
    "SkippedFile",
    "build_scan_job_workload",
    "parse_scan_report_json",
    "retain_scan_report",
    "run_passive_scan",
]
