"""Compact saved-work manifest contracts for immutable workspace exports.

Implements MoonLadderStudios/MoonMind#4015 (plan slice 4,
``docs/RepositoryAccessAndWorkspaceDesign.md`` ``CONTRACT-012``,
``QUALITY-004``, ``CONTRACT-011``, ``INV-004``, ``TEST-003``).

The saved-work manifest is a compact index of verified artifact/checkpoint
evidence, not a second object store. It extends the existing managed
checkpoint capture contracts with:

- one stable capture generation bound to a verified quiescent workspace,
- deterministic content/file-manifest identities,
- a small format profile (full snapshot vs exact-baseline delta vs
  selected-history vs report-only) with truthful portability claims,
- retries bound to exact owner/source-generation/policy/content identity,
- binary-safe deltas that never infer deletion from capture exclusion,
- confidentiality/secret controls bound to the exported bytes, and
- an immutable commit step where an upload without a usable manifest stays
  incomplete.

ACL and retention resolve through artifact ownership, never through editable
copied policy inside the manifest. Finalization, physical cleanup, retention,
and destination publication remain with their existing focused owners.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Mapping, Sequence

SAVED_WORK_MANIFEST_SCHEMA_VERSION = "saved-work-manifest/v1"
SAVED_WORK_CAPTURE_CONTRACT_VERSION = "saved-work-capture/v1"
SAVED_WORK_SCAN_POLICY_REF = "moonmind.saved_work.scan.v1"

SavedWorkFormatKind = Literal[
    "full_snapshot", "exact_baseline_delta", "selected_history", "report_only"
]
SavedWorkOutputStatus = Literal[
    "self_contained",
    "requires_dependencies",
    "inapplicable",
    "incomplete",
    "failed",
]

SUPPORTED_FORMAT_KINDS: tuple[str, ...] = (
    "full_snapshot",
    "exact_baseline_delta",
    "selected_history",
    "report_only",
)


def saved_work_path_exclusion(path: str) -> str | None:
    """One portable exclusion policy for managed and sandbox exports."""
    from pathlib import PurePosixPath
    value = PurePosixPath(path.replace("\\", "/"))
    if value.name in {".env", ".env.local", "credentials", "credentials.json"}:
        return "sensitive-filename-policy"
    if any(part in {".git", ".codex", ".ssh", ".gnupg", "node_modules", "__pycache__",
                    ".cache", ".docker", "credentials", "managed_runs", "managed_sessions"}
           for part in value.parts):
        return "sensitive-path-policy"
    if value.parts[:2] in {(".agents", "skills"), (".gemini", "skills")}:
        return "runtime-skill-overlay"
    if path.lower().endswith((".tmp", ".tar.gz", ".tgz", ".zip")):
        return "temporary-archive"
    return None

# Explicit supported format-size limits for saved-work exports. A fixed
# lower-level direct-upload limit must never be silently bypassed or reported
# as "no output": callers exceeding it receive an explicit incomplete/failed
# output claim instead.
SAVED_WORK_FORMAT_SIZE_LIMITS: dict[str, int] = {
    "max_file_count": 20_000,
    "max_file_bytes": 100 * 1024 * 1024,
    "max_total_bytes": 1024 * 1024 * 1024,
    # Bounded in-process spool for one streamed export chunk/write. Larger
    # exports must stream through the temp-file spool path, never by growing
    # an unbounded in-memory buffer.
    "max_spool_chunk_bytes": 8 * 1024 * 1024,
}

_CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\b(?:token|password|secret|api[_-]?key|credential)\s*[:=]\s*"
        r"(?:\"[^\"]+\"|'[^']+'|[^\s,;\"']+)"
    ),
    re.compile(r"(?i)\b(?:authorization\s*:\s*)?bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(?:ghp|gho|ghu|ghs|ghr|github_pat)[_-][A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r"(?is)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"
    ),
)

_RUNTIME_CREDENTIAL_PATH_HINTS: tuple[str, ...] = (
    ".codex/auth.json",
    ".config/gh/hosts.yml",
    ".docker/config.json",
    "managed_sessions/",
    "managed_runs/",
)

# Redaction sentinels emitted by ``moonmind.utils.logging.redact_sensitive_text``
# (``[REDACTED]``, ``[REDACTED_PRIVATE_KEY]``, ...). An assignment-shaped
# match whose value is exactly one of these sentinels carries no secret and
# must not fail checkpoint capture of an already-redacted tree. Mirrors the
# exemption in ``moonmind.security.outbound_scan``.
_REDACTED_SENTINEL_VALUE_PATTERN = re.compile(
    r"^\[REDACTED(?:_[A-Z0-9]+)?\]$", re.IGNORECASE
)


def _credential_match_is_benign(match_text: str) -> bool:
    """Return True when an assignment-shaped match carries no secret material."""
    separator = max(match_text.rfind("="), match_text.rfind(":"))
    value = match_text[separator + 1 :].strip() if separator != -1 else ""
    quoted = len(value) >= 2 and (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    )
    inner = value[1:-1].strip() if quoted else value
    if _REDACTED_SENTINEL_VALUE_PATTERN.match(inner):
        return True
    if not quoted and ("(" in value or ")" in value):
        # Credential-handling code such as ``token = str(explicit_token)``
        # is a call expression, not a credential value. Bare secret values
        # (tokens, base64, hex) never contain parentheses.
        return True
    return False


def resolve_saved_work_format_profile(
    *,
    is_git_workspace: bool,
    requested_result: str = "portable",
) -> dict[str, list[str]]:
    """Resolve a small required/optional format profile from the requested result.

    A report-only or non-Git task never synthesizes commits or a bundle: Git
    formats are inapplicable rather than faked. A Git workspace that produced
    useful files gets a full snapshot plus an exact-baseline delta; selected
    history stays optional until a caller explicitly requests refs.
    """
    normalized = (requested_result or "portable").strip().lower()
    if normalized in {"report", "report_only", "report-only"} or not is_git_workspace:
        return {
            "required": ["report_only"],
            "optional": ["full_snapshot"] if is_git_workspace else [],
        }
    if normalized in {"delta", "patch", "exact_baseline_delta"}:
        return {
            "required": ["full_snapshot", "exact_baseline_delta"],
            "optional": ["selected_history"],
        }
    if normalized in {"history", "bundle", "selected_history"}:
        return {
            "required": ["full_snapshot", "selected_history"],
            "optional": ["exact_baseline_delta"],
        }
    return {
        "required": ["full_snapshot"],
        "optional": ["exact_baseline_delta", "selected_history"],
    }


def describe_output_claim(
    *,
    fmt: str,
    status: str,
    ref: str | None = None,
    digest: str | None = None,
    size_bytes: int | None = None,
    dependencies: Sequence[str] | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """Build one truthful per-output portability claim."""
    if fmt not in SUPPORTED_FORMAT_KINDS:
        raise ValueError(f"unsupported saved-work format: {fmt}")
    if status not in (
        "self_contained",
        "requires_dependencies",
        "inapplicable",
        "incomplete",
        "failed",
    ):
        raise ValueError(f"unsupported output status: {status}")
    claim: dict[str, Any] = {"format": fmt, "status": status}
    if ref is not None:
        claim["ref"] = ref
    if digest is not None:
        claim["digest"] = digest
    if size_bytes is not None:
        claim["sizeBytes"] = size_bytes
    if dependencies:
        claim["dependencies"] = list(dependencies)
    if detail is not None:
        claim["detail"] = detail
    if status == "self_contained" and ref is None:
        raise ValueError("self-contained outputs require a stored ref")
    if status == "requires_dependencies" and not dependencies:
        raise ValueError("dependency-bound outputs must name immutable dependencies")
    return claim


def thin_bundle_is_portable(*, has_baseline_objects: bool) -> bool:
    """Decide whether a bundle/archive may claim source-credential independence.

    A thin bundle or worktree archive without its required baseline objects is
    not portable merely because its download succeeded.
    """
    return bool(has_baseline_objects)


def build_saved_work_manifest(
    *,
    capture_id: str,
    identity: Mapping[str, Any],
    source: Mapping[str, Any],
    content_digest: str,
    file_manifest_digest: str,
    capture_policy: Mapping[str, Any],
    capture_policy_version: str = SAVED_WORK_CAPTURE_CONTRACT_VERSION,
    required_formats: Sequence[str],
    optional_formats: Sequence[str] | None = None,
    outputs: Sequence[Mapping[str, Any]],
    exclusions: Sequence[Mapping[str, Any]] | None = None,
    scan: Mapping[str, Any] | None = None,
    dependencies: Sequence[str] | None = None,
    quiescence: Mapping[str, Any] | None = None,
    git: Mapping[str, Any] | None = None,
    capture_generation: str | None = None,
    artifact_scope: str | None = None,
    retention_ref: str | None = None,
) -> dict[str, Any]:
    """Assemble one compact saved-work manifest index.

    ACL/retention resolve through ``artifact_scope``/``retention_ref``
    ownership handles, never through editable copied policy text. Applicable
    Git baseline/head/patch/bundle fields stay optional so report-only and
    non-Git tasks never synthesize fake Git identities.
    """
    capture_id = str(capture_id or "").strip()
    if not capture_id:
        raise ValueError("capture_id must not be blank")
    manifest: dict[str, Any] = {
        "schemaVersion": SAVED_WORK_MANIFEST_SCHEMA_VERSION,
        "captureId": capture_id,
        "identity": dict(identity),
        "source": dict(source),
        "contentDigest": content_digest,
        "fileManifestDigest": file_manifest_digest,
        "capturePolicy": dict(capture_policy),
        "capturePolicyVersion": capture_policy_version,
        "requiredFormats": list(required_formats),
        "optionalFormats": list(optional_formats or []),
        "outputs": [dict(output) for output in outputs],
        "exclusions": [dict(exclusion) for exclusion in (exclusions or [])],
        "dependencies": list(dependencies or []),
    }
    manifest["scan"] = dict(scan) if scan is not None else {"disposition": "unknown"}
    manifest["quiescence"] = (
        dict(quiescence)
        if quiescence is not None
        else {"verified": False, "mechanism": "unknown"}
    )
    if git is not None:
        manifest["git"] = dict(git)
    if capture_generation is not None:
        manifest["captureGeneration"] = capture_generation
    if artifact_scope is not None:
        manifest["artifactScope"] = artifact_scope
    if retention_ref is not None:
        manifest["retentionRef"] = retention_ref
    manifest_digest = "sha256:" + hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest["manifestDigest"] = manifest_digest
    return manifest


def snapshot_capture_generation(
    *,
    git_head: str | None,
    status_digest: str,
    file_fingerprints: Mapping[str, str],
) -> dict[str, Any]:
    """Record one capture generation for a single consistency boundary."""
    return {
        "gitHead": git_head,
        "statusDigest": status_digest,
        "files": dict(file_fingerprints),
    }


def assert_single_capture_generation(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> None:
    """Fail when the workspace mutated between two generation observations.

    Reading HEAD and files at different times without a consistency boundary
    cannot establish one snapshot: any drift must retry or block explicitly
    rather than produce a falsely consistent capture.
    """
    if before.get("gitHead") != after.get("gitHead"):
        raise ValueError(
            "CHECKPOINT_CAPTURE_CONCURRENT_MUTATION: git HEAD changed during capture"
        )
    if before.get("statusDigest") != after.get("statusDigest"):
        raise ValueError(
            "CHECKPOINT_CAPTURE_CONCURRENT_MUTATION: git status changed during capture"
        )
    before_files = dict(before.get("files") or {})
    after_files = dict(after.get("files") or {})
    if before_files != after_files:
        changed = sorted(
            {*before_files, *after_files},
            key=str,
        )
        drifted = [path for path in changed if before_files.get(path) != after_files.get(path)]
        raise ValueError(
            "CHECKPOINT_CAPTURE_CONCURRENT_MUTATION: "
            f"workspace files changed during capture: {drifted[:10]}"
        )


def compute_saved_work_delta(    *,
    baseline_entries: Sequence[Mapping[str, Any]],
    capture_entries: Sequence[Mapping[str, Any]],
    excluded_paths: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Generate binary-safe add/modify/delete/rename/mode deltas.

    Identity is (sha256, mode, size, symlink target): bytes are never decoded,
    so binary content round-trips. A path absent from capture merely because it
    was excluded from capture is never inferred as deleted. Renames are
    detected by content identity (same sha, one baseline path gone, one capture
    path new) and reported explicitly instead of an add+delete pair.
    """
    excluded = set(excluded_paths or [])

    def _key(entry: Mapping[str, Any]) -> str:
        return str(entry.get("path"))

    baseline = {_key(e): dict(e) for e in baseline_entries}
    captured = {_key(e): dict(e) for e in capture_entries}

    def _identity(entry: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            entry.get("sha256"),
            entry.get("mode"),
            entry.get("size"),
            entry.get("linkTarget"),
            entry.get("type"),
        )

    deltas: list[dict[str, Any]] = []
    for path in sorted(set(baseline) | set(captured)):
        if path in excluded:
            continue
        old = baseline.get(path)
        new = captured.get(path)
        if old is None and new is not None:
            deltas.append({"path": path, "change": "added", "new": new})
        elif old is not None and new is None:
            deltas.append({"path": path, "change": "deleted", "old": old})
        elif old is not None and new is not None and _identity(old) != _identity(new):
            if old.get("mode") != new.get("mode") and old.get("sha256") == new.get("sha256"):
                deltas.append(
                    {"path": path, "change": "mode_changed", "old": old, "new": new}
                )
            else:
                deltas.append(
                    {"path": path, "change": "modified", "old": old, "new": new}
                )

    # Fold unambiguous content-identity add+delete pairs into renames.
    deleted_by_sha: dict[str, list[dict[str, Any]]] = {}
    added_by_sha: dict[str, list[dict[str, Any]]] = {}
    for delta in deltas:
        if delta["change"] == "deleted":
            deleted_by_sha.setdefault(str(delta["old"].get("sha256")), []).append(delta)
        elif delta["change"] == "added":
            added_by_sha.setdefault(str(delta["new"].get("sha256")), []).append(delta)
    folded: list[dict[str, Any]] = []
    consumed: set[int] = set()
    for delta in deltas:
        if id(delta) in consumed or delta["change"] != "deleted":
            continue
        sha = str(delta["old"].get("sha256"))
        candidates = [
            candidate
            for candidate in added_by_sha.get(sha, [])
            if id(candidate) not in consumed
        ]
        if len(candidates) == 1 and len(deleted_by_sha.get(sha, [])) == 1:
            added = candidates[0]
            consumed.add(id(delta))
            consumed.add(id(added))
            folded.append(
                {
                    "path": added["path"],
                    "change": "renamed",
                    "oldPath": delta["path"],
                    "old": delta["old"],
                    "new": added["new"],
                }
            )
        else:
            folded.append(delta)
    for delta in deltas:
        if id(delta) not in consumed and delta["change"] != "deleted":
            folded.append(delta)
    folded.sort(key=lambda item: (str(item.get("path")), str(item.get("change"))))
    return folded


def parse_git_diff_raw_to_deltas(
    diff_raw: str, excluded: set[str] | frozenset[str]
) -> list[dict[str, Any]]:
    """Parse `git diff HEAD --raw -z --find-renames` into binary-safe deltas.

    The raw format carries only modes, object ids, status codes, and paths,
    so binary content is never decoded. Export callers must pass
    `--no-ext-diff --no-textconv` and never execute untrusted hooks, filters,
    or helpers. Paths excluded from capture can never produce deletion (or
    any other) claims: without observing the path, the capture cannot
    distinguish exclusion from deletion.
    """

    tokens = diff_raw.split("\0")
    while tokens and tokens[-1] == "":
        tokens.pop()
    deltas: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        info = tokens[index]
        index += 1
        if not info.startswith(":"):
            continue
        fields = info[1:].split(" ")
        if len(fields) != 5:
            continue
        old_mode, new_mode, old_sha, new_sha, status = fields
        kind = status[:1]
        if kind in {"R", "C"}:
            if index + 1 >= len(tokens):
                break
            old_path, new_path = tokens[index], tokens[index + 1]
            index += 2
            if old_path in excluded or new_path in excluded:
                continue
            if kind == "R":
                deltas.append(
                    {
                        "path": new_path,
                        "change": "renamed",
                        "oldPath": old_path,
                        "oldMode": old_mode,
                        "newMode": new_mode,
                    }
                )
            else:
                deltas.append(
                    {
                        "path": new_path,
                        "change": "added",
                        "detail": f"copied-from {old_path}",
                        "newMode": new_mode,
                    }
                )
            continue
        if index >= len(tokens):
            break
        path = tokens[index]
        index += 1
        if path in excluded:
            continue
        if kind == "A":
            deltas.append({"path": path, "change": "added", "newMode": new_mode})
        elif kind == "D":
            deltas.append({"path": path, "change": "deleted", "oldMode": old_mode})
        elif kind == "U":
            deltas.append(
                {
                    "path": path,
                    "change": "modified",
                    "detail": "unmerged-path",
                }
            )
        elif old_sha == new_sha and old_mode != new_mode:
            deltas.append(
                {
                    "path": path,
                    "change": "mode_changed",
                    "oldMode": old_mode,
                    "newMode": new_mode,
                }
            )
        else:
            deltas.append(
                {
                    "path": path,
                    "change": "modified",
                    "oldMode": old_mode,
                    "newMode": new_mode,
                }
            )
    return deltas


def _export_findings_in_text(text: str, location: str) -> int:
    findings = 0
    for index, pattern in enumerate(_CREDENTIAL_PATTERNS):
        for match in pattern.finditer(text):
            if index == 0 and _credential_match_is_benign(match.group(0)):
                continue
            findings += 1
    for hint in _RUNTIME_CREDENTIAL_PATH_HINTS:
        if hint in location:
            findings += 1
            break
    return findings


# Bytes of overlap retained between streamed scan windows. Any single secret
# construct no longer than this overlap is fully contained in at least one
# window even when it spans a chunk boundary. 8 KiB covers the largest
# supported construct (a multi-kilobyte PEM private-key block) with margin,
# while staying negligible next to the multi-megabyte spool chunk size.
_SCAN_CHUNK_OVERLAP_BYTES = 8 * 1024


def scan_saved_work_export_stream(
    chunks: Sequence[bytes] | Any, *, export_digest: str, location: str
) -> dict[str, Any]:
    """Stream confidentiality/secret controls over exported bytes in chunks.

    Chunk windows overlap by ``_SCAN_CHUNK_OVERLAP_BYTES`` so
    credential-shaped content spanning a chunk boundary is still detected
    without holding the whole export in memory. Same evidence contract as
    :func:`scan_saved_work_export`.
    """
    findings = 0
    decodable = True
    tail = b""
    for chunk in chunks:
        window = tail + bytes(chunk)
        try:
            window.decode("utf-8")
        except UnicodeDecodeError:
            decodable = False
        findings += _export_findings_in_text(
            window.decode("utf-8", errors="ignore"), location
        )
        tail = window[-_SCAN_CHUNK_OVERLAP_BYTES:]
    if findings:
        return {
            "disposition": "blocked",
            "policyRef": SAVED_WORK_SCAN_POLICY_REF,
            "exportDigest": export_digest,
            "location": location,
            "coverage": "export-bytes",
            "limitations": [],
            "detail": "credential-shaped content detected in exported bytes",
        }
    if not decodable:
        return {
            "disposition": "unsupported",
            "policyRef": SAVED_WORK_SCAN_POLICY_REF,
            "exportDigest": export_digest,
            "location": location,
            "coverage": "export-bytes-text-projection",
            "limitations": [
                "binary export is not fully inspectable as text; "
                "no clean-scan claim is made for non-text bytes"
            ],
        }
    return {
        "disposition": "clean",
        "policyRef": SAVED_WORK_SCAN_POLICY_REF,
        "exportDigest": export_digest,
        "location": location,
        "coverage": "export-bytes",
        "limitations": [],
    }
def scan_saved_work_export(
    payload: bytes, *, export_digest: str, location: str
) -> dict[str, Any]:
    """Apply confidentiality/secret controls to actual exported bytes.

    Scan evidence binds to the export digest with explicit coverage and
    limitations. Unsupported binary/history inspection is reported as
    ``unsupported`` under the scanner policy, never fabricated as clean. This
    returns evidence only; callers decide block/restrict/quarantine through
    existing artifact policy.
    """

    return scan_saved_work_export_stream(
        [payload], export_digest=export_digest, location=location
    )


def redacted_preview_is_restorable() -> bool:
    """State the preview/report contract explicitly: never restorable."""
    return False


def verify_captured_artifact_evidence(
    *,
    expected_digest: str,
    expected_size_bytes: int,
    artifact: Any,
) -> None:
    """Verify returned artifact evidence against the expected capture candidate.

    The same request with the same immutable candidate may reuse the committed
    result; a reused COMPLETE artifact carrying different bytes must fail (or
    start an explicitly new attempt) rather than bind the wrong bytes to the
    claimed manifest. Concurrent first writes, lost upload acknowledgments,
    and reused COMPLETE artifacts therefore cannot misassociate content.
    """
    status = getattr(artifact, "status", None)
    status_value = getattr(status, "value", status)
    if status_value is not None and str(status_value).upper() != "COMPLETE":
        raise ValueError(
            f"SAVED_WORK_EVIDENCE_NOT_COMPLETE: artifact status is {status_value}"
        )
    stored_digest = getattr(artifact, "sha256", None)
    stored_size = getattr(artifact, "size_bytes", None)
    assert_complete_payload_matches(
        stored_digest=str(stored_digest) if stored_digest is not None else None,
        stored_size=int(stored_size) if stored_size is not None else None,
        new_digest=expected_digest,
        new_size=int(expected_size_bytes),
    )


def assert_complete_payload_matches(
    *,
    stored_digest: str | None,
    stored_size: int | None,
    new_digest: str,
    new_size: int,
) -> None:
    """Compare a newly supplied payload to an already-COMPLETE result.

    Shared by the artifact write boundary and saved-work orchestration so both
    enforce the same retry-identity rule. ``sha256:`` prefixes are normalized
    before comparison so content-addressed rows and bare-hex digests compare
    by content identity.
    """

    def _normalized(value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        return text.removeprefix("sha256:")

    if _normalized(stored_digest) is not None and _normalized(stored_digest) != _normalized(
        new_digest
    ):
        raise ValueError(
            "SAVED_WORK_RETRY_CONFLICT: reused COMPLETE artifact "
            "carries a different payload digest"
        )
    if stored_size is not None and stored_size != new_size:
        raise ValueError(
            "SAVED_WORK_RETRY_CONFLICT: reused COMPLETE artifact "
            "carries a different payload size"
        )


def commit_saved_work_manifest(
    manifest: Mapping[str, Any],
    *,
    required_refs_available: Mapping[str, bool],
    preview_failures: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Verify required objects/dependencies, then commit the manifest result.

    A successful upload without a committed usable manifest remains an
    incomplete capture. Missing required objects or dependency metadata keep
    the capture incomplete so #4016/#4017 can reconcile orphan uploads.
    Optional preview/report failures stay separate and never fail a capture
    whose format profile does not require them.
    """
    manifest = dict(manifest)
    missing = sorted(
        name for name, available in required_refs_available.items() if not available
    )
    scan_disposition = str((manifest.get("scan") or {}).get("disposition", "unknown"))
    if scan_disposition == "blocked":
        return {
            "status": "incomplete",
            "manifestDigest": manifest.get("manifestDigest"),
            "reason": "scan-blocked",
            "missingRequiredObjects": missing,
            "orphanAction": "quarantine-via-artifact-policy",
        }
    if missing:
        return {
            "status": "incomplete",
            "manifestDigest": manifest.get("manifestDigest"),
            "reason": "missing-required-objects",
            "missingRequiredObjects": missing,
            "orphanAction": "reconcile-with-finalization-owner",
        }
    outputs = list(manifest.get("outputs") or [])
    required_formats = set(manifest.get("requiredFormats") or [])
    failed_required = sorted(
        str(output.get("format"))
        for output in outputs
        if str(output.get("format")) in required_formats
        and str(output.get("status")) in {"incomplete", "failed"}
    )
    if failed_required:
        return {
            "status": "incomplete",
            "manifestDigest": manifest.get("manifestDigest"),
            "reason": "required-format-failed",
            "missingRequiredObjects": missing,
            "failedRequiredFormats": failed_required,
            "orphanAction": "reconcile-with-finalization-owner",
        }
    result: dict[str, Any] = {
        "status": "committed",
        "manifestDigest": manifest.get("manifestDigest"),
        "reason": None,
    }
    if preview_failures:
        # Optional preview/report evidence is informational only.
        result["previewFailures"] = list(preview_failures)
    return result
