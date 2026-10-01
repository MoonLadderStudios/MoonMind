"""Materialize verified saved work for publication without the original source.

GitHub issue MoonLadderStudios/MoonMind#4018.

A saved-work manifest (#4015) indexes the immutable capture artifacts. This
module reads only those artifacts through the caller's authorized reader,
verifies the manifest -> snapshot archive -> file manifest -> recorded delta
chain, and materializes the captured files into a fresh owned directory for
``PublishService.prepare_saved_candidate``. It never clones or fetches the
original source repository and never runs a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from moonmind.publish.saved_candidate import (
    SavedContent,
    SavedEntry,
    SavedPublicationError,
    _safe_relative_path,
)
from moonmind.schemas.saved_work_models import (
    SAVED_WORK_FORMAT_SIZE_LIMITS,
    SAVED_WORK_MANIFEST_SCHEMA_VERSION,
    workspace_content_digest,
)

SAVED_WORK_MANIFEST_CONTENT_TYPE = (
    "application/vnd.moonmind.saved-work-manifest+json;version=1"
)
WORKTREE_ARCHIVE_CONTENT_TYPE = "application/vnd.moonmind.worktree-archive"
SAVED_WORK_DELTA_CONTENT_TYPE = (
    "application/vnd.moonmind.saved-work-delta+json;version=1"
)

ArtifactReader = Callable[[str, frozenset[str]], Awaitable[bytes]]


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def saved_work_artifact_id(ref: str) -> str:
    """Return the artifact id behind a compact saved-work artifact ref."""

    text = str(ref or "").strip()
    for prefix in ("artifact://", "artifact:"):
        text = text.removeprefix(prefix)
    if not text or any(ch in text for ch in "\0\n\r/"):
        raise SavedPublicationError(
            "PUBLICATION_SAVED_WORK_INVALID", "saved-work ref is not an artifact id"
        )
    return text


def _invalid(message: str, *details: str) -> SavedPublicationError:
    return SavedPublicationError(
        "PUBLICATION_CONTENT_INVALID", message, details=details
    )


def _json_object(payload: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise _invalid(f"{name} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise _invalid(f"{name} must be a JSON object")
    return value


def _extract_member(
    tar: tarfile.TarFile, member: tarfile.TarInfo, root: Path, budget: int
) -> dict[str, Any]:
    """Materialize one archive member and return its capture-shaped entry."""

    limits = SAVED_WORK_FORMAT_SIZE_LIMITS
    try:
        path = _safe_relative_path(member.name, "archive path")
    except ValueError as exc:
        raise _invalid("saved snapshot has an unsafe path", member.name) from exc
    target = root.joinpath(*path.split("/"))
    for parent in target.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise _invalid("saved snapshot traverses a symlink parent", path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = member.mode & 0o7777
    if member.isreg():
        if member.size > min(limits["max_file_bytes"], budget):
            raise _invalid("saved snapshot exceeds the byte limit", path)
        stream = tar.extractfile(member)
        payload = stream.read(member.size + 1) if stream else b""
        if len(payload) != member.size:
            raise _invalid("saved snapshot member size mismatch", path)
        target.write_bytes(payload)
        os.chmod(target, mode & 0o777)
        return {
            "path": path,
            "type": "file",
            "mode": f"{mode:06o}",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    if member.issym():
        link = member.linkname
        contained = root.resolve()
        resolved = (target.parent / link).resolve(strict=False)
        if resolved != contained and contained not in resolved.parents:
            raise _invalid("saved snapshot symlink escapes its root", path)
        target.symlink_to(link)
        encoded = link.encode()
        return {
            "path": path,
            "type": "symlink",
            "mode": f"{mode:06o}",
            "size": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "linkTarget": link,
        }
    raise _invalid("saved snapshot has an unsupported entry type", path)


def _extract_snapshot(archive: bytes, root: Path) -> list[dict[str, Any]]:
    """Extract a capture snapshot and rebuild its checkpoint entry list.

    Entries use the capture's own shape so ``workspace_content_digest`` over
    them must equal the manifest's ``fileManifestDigest``.
    """

    limits = SAVED_WORK_FORMAT_SIZE_LIMITS
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    remaining = limits["max_total_bytes"]
    try:
        with tarfile.open(fileobj=BytesIO(archive), mode="r:*") as tar:
            for member in tar:
                if len(entries) >= limits["max_file_count"]:
                    raise _invalid("saved snapshot exceeds the file-count limit")
                if member.name in seen:
                    raise _invalid("saved snapshot repeats a path", member.name)
                seen.add(member.name)
                entry = _extract_member(tar, member, root, remaining)
                if entry["type"] == "file":
                    remaining -= entry["size"]
                entries.append(entry)
    except tarfile.TarError as exc:
        raise _invalid("saved snapshot is not a readable archive") from exc
    return entries


def _recorded_deletions(delta: dict[str, Any]) -> tuple[str, ...]:
    deleted: set[str] = set()
    for change in delta.get("changes") or []:
        if not isinstance(change, dict):
            raise _invalid("recorded delta has a malformed change")
        if change.get("change") == "deleted":
            deleted.add(str(change.get("path") or ""))
        elif change.get("change") == "renamed" and change.get("oldPath"):
            deleted.add(str(change.get("oldPath")))
    deleted.discard("")
    return tuple(sorted(deleted))


@dataclass(frozen=True)
class SavedWorkPublicationObject:
    """One raw artifact the saved-work publisher must read."""

    ref: str
    digest: str
    content_type: str


def parse_saved_work_publication_manifest(
    manifest_bytes: bytes,
) -> tuple[dict[str, Any], tuple[SavedWorkPublicationObject, ...]]:
    """Validate the manifest and identify the publisher's exact raw closure.

    Admission checks this closure's availability without downloading archives.
    Materialization reads these same snapshot/delta objects, including a delta
    named only in ``git.deltaRef`` rather than in the output-format summary.
    """

    manifest = _json_object(manifest_bytes, "saved-work manifest")
    if manifest.get("schemaVersion") != SAVED_WORK_MANIFEST_SCHEMA_VERSION:
        raise SavedPublicationError(
            "PUBLICATION_SAVED_WORK_UNSUPPORTED",
            "saved-work manifest schema is not supported for publication",
        )
    scan = manifest.get("scan") if isinstance(manifest.get("scan"), dict) else {}
    if scan.get("disposition") in {"blocked", "quarantined"}:
        raise SavedPublicationError(
            "PUBLICATION_CONTENT_UNSAFE",
            "saved work was blocked or quarantined by its export scan",
        )
    snapshot = next(
        (
            output
            for output in manifest.get("outputs") or []
            if isinstance(output, dict)
            and output.get("format") == "full_snapshot"
            and output.get("status") == "self_contained"
        ),
        None,
    )
    if (
        snapshot is None
        or not snapshot.get("ref")
        or not snapshot.get("digest")
        or snapshot.get("digest") != manifest.get("contentDigest")
    ):
        raise SavedPublicationError(
            "PUBLICATION_SAVED_WORK_UNSUPPORTED",
            "saved work has no self-contained snapshot to publish",
        )
    objects = [
        SavedWorkPublicationObject(
            ref=str(snapshot["ref"]),
            digest=str(snapshot["digest"]),
            content_type=WORKTREE_ARCHIVE_CONTENT_TYPE,
        )
    ]
    git = manifest.get("git") if isinstance(manifest.get("git"), dict) else None
    if git is not None and git.get("deltaRef"):
        if not git.get("deltaDigest"):
            raise _invalid("recorded delta has no digest")
        objects.append(
            SavedWorkPublicationObject(
                ref=str(git["deltaRef"]),
                digest=str(git["deltaDigest"]),
                content_type=SAVED_WORK_DELTA_CONTENT_TYPE,
            )
        )
    return manifest, tuple(objects)


async def materialize_saved_work(
    *,
    read: ArtifactReader,
    saved_work_ref: str,
    saved_work_digest: str,
    root: Path,
) -> SavedContent:
    """Verify one immutable saved result and materialize it under ``root``.

    ``read(ref, content_types)`` returns authorized artifact bytes. Every
    object is bound to the digest recorded by the already verified manifest,
    so no editable copy or live source can substitute content.
    """

    root = Path(root)
    if root.exists() and any(root.iterdir()):
        raise SavedPublicationError(
            "PUBLICATION_WORKSPACE_NOT_FRESH",
            "saved content requires a fresh owned directory",
        )
    root.mkdir(parents=True, exist_ok=True)
    manifest_bytes = await read(
        saved_work_ref, frozenset({SAVED_WORK_MANIFEST_CONTENT_TYPE})
    )
    if _digest(manifest_bytes) != saved_work_digest:
        raise SavedPublicationError(
            "PUBLICATION_CONTENT_MISMATCH",
            "saved-work manifest bytes differ from the admitted digest",
        )
    manifest, objects = parse_saved_work_publication_manifest(manifest_bytes)
    snapshot = objects[0]
    archive = await read(snapshot.ref, frozenset({snapshot.content_type}))
    if _digest(archive) != snapshot.digest:
        raise SavedPublicationError(
            "PUBLICATION_CONTENT_MISMATCH",
            "saved snapshot bytes differ from the manifest digest",
        )
    entries = _extract_snapshot(archive, root)
    if workspace_content_digest(entries) != manifest.get("fileManifestDigest"):
        raise SavedPublicationError(
            "PUBLICATION_CONTENT_MISMATCH",
            "saved snapshot entries differ from the recorded file manifest",
        )

    git = manifest.get("git") if isinstance(manifest.get("git"), dict) else None
    baseline: str | None = None
    recorded_deletions: tuple[str, ...] | None = None
    if git is not None:
        baseline = str(git.get("baselineCommit") or "") or None
        # Without a recorded delta nothing proves a deletion, so none is made.
        recorded_deletions = ()
        if git.get("deltaRef"):
            delta_source = objects[1]
            delta_bytes = await read(
                delta_source.ref, frozenset({delta_source.content_type})
            )
            if _digest(delta_bytes) != delta_source.digest:
                raise SavedPublicationError(
                    "PUBLICATION_CONTENT_MISMATCH",
                    "recorded delta bytes differ from the manifest digest",
                )
            delta = _json_object(delta_bytes, "recorded delta")
            if delta.get("baselineCommit") != baseline:
                raise SavedPublicationError(
                    "PUBLICATION_CONTENT_MISMATCH",
                    "recorded delta baseline differs from the manifest",
                )
            recorded_deletions = _recorded_deletions(delta)
    excluded = tuple(
        sorted(
            {
                str(item.get("path"))
                for item in manifest.get("exclusions") or []
                if isinstance(item, dict) and item.get("path")
                # Absent tracked files are deletions, not capture exclusions.
                and item.get("reason") != "absent-from-worktree"
            }
        )
    )
    return SavedContent(
        root=root,
        entries=tuple(
            SavedEntry(
                path=entry["path"],
                kind=entry["type"],
                executable=entry["type"] == "file"
                and bool(int(entry["mode"], 8) & 0o111),
            )
            for entry in entries
        ),
        digest=saved_work_digest,
        baseline_commit=baseline,
        excluded_paths=excluded,
        recorded_deletions=recorded_deletions,
    )


__all__ = [
    "SAVED_WORK_DELTA_CONTENT_TYPE",
    "SAVED_WORK_MANIFEST_CONTENT_TYPE",
    "WORKTREE_ARCHIVE_CONTENT_TYPE",
    "materialize_saved_work",
    "saved_work_artifact_id",
]
