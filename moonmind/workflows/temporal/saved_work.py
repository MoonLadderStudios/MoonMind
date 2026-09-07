"""Pure saved-work capture helpers (GitHub issue #4015).

All functions here are deterministic, side-effect-free helpers owned by the
saved-work boundary. They implement the assessment backlog (reqs 2-8) at the
contract level so Temporal workflow/activity code can consume them without
embedding large payloads in workflow history:

- req 2: format-profile helpers (thin-bundle completeness rule).
- req 3: stable capture-generation fingerprint + quiescence verification.
- req 4: deterministic digests, bounded spool/stream hashing, explicit
  format-size limits with a direct-upload-limit guard.
- req 5: retry-identity binding + returned-evidence verification.
- req 6: binary-safe deltas, scoped history-bundle validation,
  path/case-collision and symlink checks.
- req 7: export-bound scan evidence, redacted-preview distinction,
  runtime-credential exclusion, quarantine decision.
- req 8: immutable saved-result commit verification with preview/report
  failure separation.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal, Mapping, Sequence

from moonmind.schemas.saved_work_models import (
    SavedWorkFormatEntry,
    SavedWorkFormatKind,
    SavedWorkOutputState,
)

SAVED_WORK_SCHEMA_DIGEST = "sha256:saved-work-manifest-v1"

#: Explicit supported per-format size limits (req 4). These are product
#: export limits, distinct from the lower-level artifact direct-upload
#: limit; exceeding either is an explicit failure, never a silent bypass.
SAVED_WORK_FORMAT_SIZE_LIMITS: dict[str, int] = {
    "full_snapshot": 1024 * 1024 * 1024,
    "baseline_delta": 256 * 1024 * 1024,
    "selected_history": 512 * 1024 * 1024,
    "worktree_archive": 1024 * 1024 * 1024,
    "report": 16 * 1024 * 1024,
}

#: Runtime-issued credentials, token-bearing configs, live handles, and
#: authorization state that must never enter an export (req 7).
RUNTIME_CREDENTIAL_EXCLUDED_NAMES = frozenset(
    {".env", ".env.local", "credentials", "credentials.json", ".netrc", ".git-credentials"}
)
RUNTIME_CREDENTIAL_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".codex",
        ".ssh",
        ".gnupg",
        ".docker",
        "credentials",
        "managed_runs",
        "managed_sessions",
        "node_modules",
    }
)

OUTBOUND_SCAN_POLICY_REF = "moonmind.security.outbound_scan.v1"


class SavedWorkError(ValueError):
    """Deterministic saved-work contract violation (message is the code)."""


# ---------------------------------------------------------------------------
# req 2: format profile
# ---------------------------------------------------------------------------


def is_source_credential_independent(entry: SavedWorkFormatEntry) -> bool:
    """A thin bundle/archive without required baseline objects is not portable."""
    if entry.state == "self-contained":
        return True
    if entry.state == "requires-dependencies":
        return bool(entry.dependencies)
    return False


def check_thin_bundle_completeness(
    *,
    has_bundle_without_baseline: bool,
    download_succeeded: bool,
) -> None:
    """Reject credential-independence claims for thin bundles (req 2)."""
    if has_bundle_without_baseline and download_succeeded:
        raise SavedWorkError(
            "SAVED_WORK_THIN_BUNDLE_INCOMPLETE: bundle requires named baseline "
            "objects and is not source-credential-independent"
        )


def report_only_exempt(
    kinds: Iterable[SavedWorkFormatKind], *, is_report_only: bool, is_git: bool
) -> list[SavedWorkFormatKind]:
    """Report-only / non-Git tasks need not synthesize commits or bundles."""
    selected = list(kinds)
    if is_report_only or not is_git:
        selected = [kind for kind in selected if kind in {"report", "full_snapshot"}]
    return selected


# ---------------------------------------------------------------------------
# req 3: stable capture generation / quiescence
# ---------------------------------------------------------------------------


def capture_generation_fingerprint(
    *, head_commit: str, status_digest: str, workspace_digest: str = ""
) -> str:
    """One stable capture generation from a verified quiescent workspace."""
    payload = json.dumps(
        {
            "head": head_commit.strip(),
            "statusDigest": status_digest.strip(),
            "workspaceDigest": workspace_digest.strip(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def verify_quiescent_capture(*, pre_generation: str, post_generation: str) -> str:
    """Detect mutation during capture; retry/block explicitly on mismatch."""
    if not pre_generation.strip() or not post_generation.strip():
        raise SavedWorkError("SAVED_WORK_QUIESCENCE_UNKNOWN: capture generation missing")
    if pre_generation != post_generation:
        raise SavedWorkError(
            "SAVED_WORK_WORKSPACE_MUTATED: workspace changed during capture; "
            "stop/fence writers or retry the capture"
        )
    return pre_generation


# ---------------------------------------------------------------------------
# req 4: deterministic identities, bounded spool, explicit size limits
# ---------------------------------------------------------------------------


def normalize_manifest_entries(
    entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Defined ordering + metadata normalization for file-manifest identities."""
    normalized = [dict(entry) for entry in entries]
    normalized.sort(key=lambda entry: str(entry.get("path", "")))
    return normalized


def compute_file_manifest_digest(entries: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        {
            "schemaVersion": "moonmind-saved-work-file-manifest/v1",
            "entries": normalize_manifest_entries(entries),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def stream_sha256_of_file(path: str | os.PathLike[str]) -> str:
    """Bounded streaming hash; never allocates the whole file in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def spool_bytes_bounded(
    chunks: Iterable[bytes], *, max_bytes: int, spill_path: str | None = None
) -> tuple[str, int]:
    """Spool chunks through a bounded temp file; enforce actual byte counts.

    Returns ``(sha256_digest, total_bytes)``. Raises :class:`SavedWorkError`
    when ``max_bytes`` is exceeded or no storage is available, instead of
    allocating the entire workspace export in memory.
    """
    total = 0
    digest = hashlib.sha256()
    spool = tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024, dir=spill_path)
    try:
        for chunk in chunks:
            total += len(chunk)
            if total > max_bytes:
                raise SavedWorkError(
                    "SAVED_WORK_FORMAT_SIZE_EXCEEDED: export exceeds supported "
                    f"format limit ({max_bytes} bytes)"
                )
            digest.update(chunk)
            spool.write(chunk)
        return "sha256:" + digest.hexdigest(), total
    finally:
        spool.close()


def ensure_export_within_limits(
    *,
    format_kind: SavedWorkFormatKind,
    size_bytes: int,
    direct_upload_max_bytes: int,
) -> None:
    """Make supported format-size limits explicit (req 4).

    A fixed lower-level direct-upload limit cannot be silently bypassed or
    reported as ``no output``: oversized exports raise here.
    """
    limit = SAVED_WORK_FORMAT_SIZE_LIMITS[format_kind]
    if size_bytes > limit:
        raise SavedWorkError(
            "SAVED_WORK_FORMAT_SIZE_EXCEEDED: "
            f"{format_kind} size {size_bytes} exceeds limit {limit}"
        )
    if size_bytes > direct_upload_max_bytes and format_kind != "report":
        raise SavedWorkError(
            "SAVED_WORK_DIRECT_UPLOAD_LIMIT: export exceeds the direct-upload "
            "limit; use a bounded multipart/spool upload path instead of "
            "silent bypass or empty output"
        )


# ---------------------------------------------------------------------------
# req 5: retry-identity binding + returned-evidence verification
# ---------------------------------------------------------------------------


def build_retry_identity(
    *,
    owner_scope: str,
    capture_generation: str,
    capture_policy: str,
    content_identity: str,
) -> str:
    """Bind a retry to exact owner + generation + policy + content identity."""
    for value, name in (
        (owner_scope, "owner_scope"),
        (capture_generation, "capture_generation"),
        (capture_policy, "capture_policy"),
        (content_identity, "content_identity"),
    ):
        if not str(value or "").strip():
            raise SavedWorkError(f"SAVED_WORK_RETRY_IDENTITY_INVALID: {name} missing")
    payload = json.dumps(
        {
            "owner": owner_scope.strip(),
            "generation": capture_generation.strip(),
            "policy": capture_policy.strip(),
            "content": content_identity.strip(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def check_retry_reuse(
    *,
    retry_identity: str,
    committed_identity: str | None,
    candidate_identity: str,
    committed: bool,
) -> Literal["reuse", "new-attempt"]:
    """Same request + same immutable candidate may reuse; conflicts fail."""

    if committed and committed_identity == retry_identity == candidate_identity:
        return "reuse"
    if committed and committed_identity != candidate_identity:
        raise SavedWorkError(
            "SAVED_WORK_RETRY_CONFLICT: conflicting candidate cannot reuse the "
            "committed result; create an explicitly new attempt"
        )
    return "new-attempt"


def verify_returned_evidence(
    *,
    expected_digest: str,
    expected_size: int,
    expected_status: str,
    returned_digest: str | None,
    returned_size: int | None,
    returned_status: str | None,
) -> None:
    """Verify artifact digest/size/status before referencing it (req 5).

    Concurrent first writes, lost upload acknowledgments, and reused
    COMPLETE artifacts must never associate different bytes with one
    claimed manifest.
    """
    if returned_status != expected_status:
        raise SavedWorkError(
            "SAVED_WORK_EVIDENCE_STATUS_MISMATCH: "
            f"expected {expected_status!r} got {returned_status!r}"
        )
    if returned_digest != expected_digest:
        raise SavedWorkError(
            "SAVED_WORK_EVIDENCE_DIGEST_MISMATCH: returned artifact does not "
            "match the expected capture candidate"
        )
    if returned_size != expected_size:
        raise SavedWorkError(
            "SAVED_WORK_EVIDENCE_SIZE_MISMATCH: returned artifact size does not "
            "match the expected capture candidate"
        )


# ---------------------------------------------------------------------------
# req 6: binary-safe deltas, selected history, collisions, symlinks
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FileState:
    sha256: str
    mode: str
    size: int


@dataclass(frozen=True, slots=True)
class DeltaEntry:
    path: str
    change: str  # add | modify | delete | rename | mode
    previous_path: str | None = None
    mode: str | None = None


def compute_binary_safe_deltas(
    *,
    baseline: Mapping[str, FileState],
    captured: Mapping[str, FileState],
    excluded: Iterable[str] = (),
) -> list[DeltaEntry]:
    """Binary-safe add/modify/delete/rename/mode deltas vs recorded baseline.

    Excluded paths are never treated as deletions: a path missing from the
    capture because it was excluded from capture is simply absent.
    """
    excluded_set = set(excluded)
    deltas: list[DeltaEntry] = []
    baseline_digests: dict[str, list[str]] = {}
    for path, state in baseline.items():
        baseline_digests.setdefault(state.sha256, []).append(path)

    captured_by_digest: dict[str, list[str]] = {}
    for path, state in captured.items():
        captured_by_digest.setdefault(state.sha256, []).append(path)

    used_captured: set[str] = set()
    for path, base in baseline.items():
        if path in excluded_set:
            continue
        current = captured.get(path)
        if current is None:
            # Possible rename: same bytes appear under a new path.
            renamed_from: str | None = None
            for candidate in captured_by_digest.get(base.sha256, []):
                if candidate not in baseline and candidate not in used_captured:
                    renamed_from = candidate
                    break
            if renamed_from is not None:
                used_captured.add(renamed_from)
                deltas.append(
                    DeltaEntry(
                        path=renamed_from, change="rename", previous_path=path
                    )
                )
            else:
                deltas.append(DeltaEntry(path=path, change="delete"))
        elif current.sha256 != base.sha256:
            deltas.append(DeltaEntry(path=path, change="modify"))
        elif current.mode != base.mode:
            deltas.append(DeltaEntry(path=path, change="mode", mode=current.mode))

    for path, current in captured.items():
        if path not in baseline and path not in used_captured:
            deltas.append(DeltaEntry(path=path, change="add"))

    deltas.sort(key=lambda entry: (entry.path, entry.change))
    return deltas


def validate_history_export_request(
    *,
    refs: Sequence[str],
    baseline_commit: str | None,
    allow_all: bool = False,
    run_hooks: bool = False,
) -> list[str]:
    """Scope selected-history bundles to intended reachable refs (req 6)."""
    normalized = [ref.strip() for ref in refs if ref.strip()]
    if allow_all or any(ref == "--all" for ref in normalized):
        raise SavedWorkError(
            "SAVED_WORK_HISTORY_ALL_FORBIDDEN: selected-history bundles must "
            "include only intended reachable refs, never git bundle --all"
        )
    if run_hooks:
        raise SavedWorkError(
            "SAVED_WORK_HISTORY_HOOKS_FORBIDDEN: do not execute untrusted "
            "hooks/filters/helpers while exporting"
        )
    if not normalized:
        raise SavedWorkError("SAVED_WORK_HISTORY_NO_REFS: no intended refs selected")
    if not (baseline_commit or "").strip():
        raise SavedWorkError(
            "SAVED_WORK_HISTORY_NO_BASELINE: selected history must declare its "
            "baseline dependency"
        )
    return normalized


def check_path_case_collisions(paths: Iterable[str]) -> None:
    """Coordinate target-platform path/case collisions before claiming a format."""
    seen: dict[str, str] = {}
    for path in paths:
        folded = PurePosixPath(path).as_posix().casefold()
        if folded in seen and seen[folded] != path:
            raise SavedWorkError(
                "SAVED_WORK_PATH_COLLISION: case-colliding paths "
                f"{seen[folded]!r} and {path!r} cannot share one portable format"
            )
        seen.setdefault(folded, path)


def validate_safe_symlink(*, link_path: str, target: str, workspace: str) -> None:
    """Safe symlink semantics shared with the restore path (#4014)."""
    resolved = (Path(workspace) / Path(link_path).parent / target).resolve()
    if not resolved.is_relative_to(Path(workspace).resolve()):
        raise SavedWorkError(
            f"SAVED_WORK_SYMLINK_ESCAPE: symlink escapes workspace: {link_path}"
        )


# ---------------------------------------------------------------------------
# req 7: confidentiality + secret controls on exported bytes/history
# ---------------------------------------------------------------------------


def is_excluded_runtime_credential(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if PurePosixPath(path).name in RUNTIME_CREDENTIAL_EXCLUDED_NAMES:
        return True
    return any(part in RUNTIME_CREDENTIAL_EXCLUDED_PARTS for part in parts)


@dataclass(frozen=True, slots=True)
class ExportScanEvidence:
    disposition: str
    policy_ref: str
    export_digest: str
    scanned_bytes: int
    unscanned_bytes: int
    limitations: tuple[str, ...]


def bind_scan_evidence(
    *,
    export_digest: str,
    scanned_bytes: int,
    unscanned_bytes: int,
    blocked: bool,
    unsupported: bool = False,
    limitations: Sequence[str] = (),
) -> ExportScanEvidence:
    """Bind scan evidence to the export digest with coverage/limitations."""
    if not export_digest.strip():
        raise SavedWorkError("SAVED_WORK_SCAN_NO_DIGEST: export digest is required")
    if blocked:
        disposition = "blocked"
    elif unsupported:
        disposition = "unsupported"
    elif unscanned_bytes > 0:
        disposition = "unsupported"
    else:
        disposition = "clean"
    merged_limitations = list(limitations)
    if unscanned_bytes > 0 and "binary/history bytes outside text inspection" not in merged_limitations:
        merged_limitations.append("binary/history bytes outside text inspection")
    return ExportScanEvidence(
        disposition=disposition,
        policy_ref=OUTBOUND_SCAN_POLICY_REF,
        export_digest=export_digest,
        scanned_bytes=scanned_bytes,
        unscanned_bytes=unscanned_bytes,
        limitations=tuple(merged_limitations),
    )


def is_byte_identical_restorable(*, is_redacted_preview: bool) -> bool:
    """A redacted preview is never byte-identical restorable source."""
    return not is_redacted_preview


def quarantine_decision(*, blocked: bool, has_recoverable_content: bool) -> str:
    """Quarantine unsafe recoverable content without disclosing raw findings."""
    if blocked and has_recoverable_content:
        return "quarantine-restricted"
    if blocked:
        return "blocked-no-content"
    return "allow"


# ---------------------------------------------------------------------------
# req 8: commit the immutable manifest/reference set as the saved result
# ---------------------------------------------------------------------------


def verify_saved_result_commit(
    *,
    format_states: Mapping[SavedWorkFormatKind, SavedWorkOutputState],
    required_kinds: Sequence[SavedWorkFormatKind],
    manifest_committed: bool,
    dependency_metadata_present: bool,
) -> None:
    """Verify required objects + dependency metadata, then commit (req 8)."""
    missing = [
        kind for kind in required_kinds if format_states.get(kind) in {None, "incomplete", "failed"}
    ]
    if missing:
        raise SavedWorkError(
            "SAVED_WORK_COMMIT_INCOMPLETE: required outputs missing: "
            + ", ".join(sorted(missing))
        )
    if not dependency_metadata_present:
        raise SavedWorkError(
            "SAVED_WORK_COMMIT_NO_DEPENDENCIES: dependency metadata is required "
            "before committing the saved result"
        )
    if not manifest_committed:
        raise SavedWorkError(
            "SAVED_WORK_COMMIT_NO_MANIFEST: a successful upload without a "
            "committed usable manifest remains an incomplete capture"
        )


def separate_preview_failure(*, preview_failed: bool, content_verified: bool) -> str:
    """Optional preview/report failure stays separate from verified content."""
    if preview_failed and content_verified:
        return "content-saved-preview-failed"
    if preview_failed:
        return "preview-failed"
    return "ok"


def stat_mode_string(mode: int) -> str:
    return f"{stat.S_IMODE(mode):06o}"
