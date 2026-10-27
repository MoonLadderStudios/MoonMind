"""Job-owned Container Job attachment storage.

A job that declares ``inputAttachments`` gets one directory under the
deployment's container-job workspace store, keyed by its job id and claimed by
its ownership token. Inputs are captured there once and mounted read-only, so
edits to the upstream working copy after capture never reach the job and every
retry of the job sees the same bytes. Outputs and scratch are separate writable
directories in the same root; nothing the job writes lands in an input.

The root lives inside the workspace store only so the Docker daemon can mount
it through the same deployment volume as workspaces. It is never a workspace:
workspace resolution refuses it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

ATTACHMENT_ROOT_NAME = ".container-job-attachments"
_MANIFEST = "inputs.json"
_LEDGER = "published.json"
_OWNER = "owner"


class AttachmentStorageError(RuntimeError):
    """Job-owned attachment storage cannot be used safely."""


@dataclass(frozen=True)
class JobAttachmentStorage:
    root: Path

    @property
    def relative(self) -> str:
        return f"{ATTACHMENT_ROOT_NAME}/{self.root.name}"

    @property
    def inputs(self) -> Path:
        return self.root / "inputs"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def scratch(self) -> Path:
        return self.root / "scratch"

    @property
    def staging(self) -> Path:
        return self.root / "staging"


def storage_for(workspace_root: Path, job_id: str) -> JobAttachmentStorage:
    # Job ids are validated ``container-job:<32 hex>`` identities.
    return JobAttachmentStorage(
        workspace_root / ATTACHMENT_ROOT_NAME / job_id.rsplit(":", 1)[-1]
    )


def _owner(storage: JobAttachmentStorage) -> str | None:
    try:
        return (storage.root / _OWNER).read_text()
    except FileNotFoundError:
        return None


def claim(storage: JobAttachmentStorage, ownership_token: str) -> None:
    """Create the root with its owner marker atomically, or verify ownership."""

    if not storage.root.exists():
        storage.root.parent.mkdir(parents=True, exist_ok=True)
        pending = storage.root.parent / f".{storage.root.name}.{uuid.uuid4().hex}"
        pending.mkdir()
        (pending / _OWNER).write_text(ownership_token)
        try:
            os.rename(pending, storage.root)
        except OSError:
            # A concurrent attempt claimed it first; verify below.
            shutil.rmtree(pending, ignore_errors=True)
    if storage.root.is_symlink() or _owner(storage) != ownership_token:
        raise AttachmentStorageError("job attachment storage is owned by another job")


def _write_json(path: Path, value: object) -> None:
    pending = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    pending.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
    os.replace(pending, path)


def load_manifest(storage: JobAttachmentStorage) -> dict | None:
    try:
        return json.loads((storage.root / _MANIFEST).read_text())
    except FileNotFoundError:
        return None


def _content_identity(records: list[list[str]]) -> str:
    encoded = json.dumps(sorted(records), separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def capture_directory(
    source: Path,
    destination: Path,
    *,
    include_git: bool,
    max_files: int,
    max_bytes: int,
) -> dict:
    """Copy regular files and directories from ``source`` without following links.

    Returns the captured identity and coverage. Symlinks and special files are
    omitted and counted rather than followed, so a link cannot pull host
    content into the snapshot.
    """

    if source.is_symlink() or not source.is_dir():
        raise AttachmentStorageError("input attachment source is not a directory")
    destination.mkdir(parents=True)
    records: list[list[str]] = []
    omitted_links = omitted_special = changed = 0
    used_bytes = 0
    git = "absent"
    for directory, dirs, files in os.walk(source, followlinks=False):
        relative = Path(directory).relative_to(source)
        kept = []
        for name in sorted(dirs):
            if (Path(directory) / name).is_symlink():
                omitted_links += 1
                continue
            if relative == Path(".") and name == ".git":
                if not include_git:
                    git = "excluded"
                    continue
                git = "directory"
            kept.append(name)
            (destination / relative / name).mkdir()
        dirs[:] = kept
        for name in sorted(files):
            original = Path(directory) / name
            path = relative / name
            if relative == Path(".") and name == ".git":
                # A worktree pointer names metadata outside the admitted source.
                git = "external" if include_git else "excluded"
                continue
            info = original.lstat()
            if stat.S_ISLNK(info.st_mode):
                omitted_links += 1
                continue
            if not stat.S_ISREG(info.st_mode):
                omitted_special += 1
                continue
            if len(records) + 1 > max_files or used_bytes + info.st_size > max_bytes:
                raise AttachmentStorageError(
                    "input attachment exceeds its admitted bound"
                )
            digest = hashlib.sha256()
            size = 0
            with original.open("rb") as reader, (destination / path).open(
                "xb"
            ) as writer:
                while chunk := reader.read(1024 * 1024):
                    size += len(chunk)
                    if used_bytes + size > max_bytes:
                        raise AttachmentStorageError(
                            "input attachment exceeds its admitted bound"
                        )
                    digest.update(chunk)
                    writer.write(chunk)
            os.chmod(destination / path, stat.S_IMODE(info.st_mode) | 0o444)
            after = original.lstat()
            if (after.st_size, after.st_mtime_ns) != (size, info.st_mtime_ns):
                changed += 1
            used_bytes += size
            records.append([path.as_posix(), digest.hexdigest()])
    shallow = git == "directory" and (source / ".git" / "shallow").exists()
    limitations = sorted(
        item
        for item, present in (
            ("files_changed_during_capture", changed),
            ("git_history_excluded", git == "excluded"),
            ("git_metadata_absent", git == "absent"),
            ("git_metadata_external", git == "external"),
            ("shallow_git_history", shallow),
            ("special_files_omitted", omitted_special),
            ("symlinks_omitted", omitted_links),
        )
        if present
    )
    return {
        "identity": _content_identity(records),
        "files": len(records),
        "bytes": used_bytes,
        "coverage": {
            "git": git,
            "shallow": shallow,
            "symlinksOmitted": omitted_links,
            "specialFilesOmitted": omitted_special,
            "filesChangedDuringCapture": changed,
            "limitations": limitations,
        },
    }


def capture_bytes(payload: bytes, destination: Path, file_name: str) -> dict:
    destination.mkdir(parents=True)
    (destination / file_name).write_bytes(payload)
    os.chmod(destination / file_name, 0o444)
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "identity": _content_identity([[file_name, digest]]),
        "files": 1,
        "bytes": len(payload),
        "coverage": {"limitations": []},
    }


async def prepare(
    storage: JobAttachmentStorage,
    *,
    ownership_token: str,
    names: list[str],
    capture: Callable[[str, Path], Awaitable[dict]],
) -> dict:
    """Capture every input once; a retry reuses the recorded capture.

    The manifest is written only after every input is in place, so an
    interrupted attempt leaves no manifest and is captured afresh, while a
    completed capture (including one whose acknowledgment was lost) is reused
    unchanged.
    """

    claim(storage, ownership_token)
    manifest = load_manifest(storage)
    if manifest is not None:
        if sorted(manifest.get("inputs", {})) != sorted(names):
            raise AttachmentStorageError(
                "captured inputs differ from the admitted attachments"
            )
    else:
        shutil.rmtree(storage.staging, ignore_errors=True)
        storage.staging.mkdir()
        captured = {}
        for name in names:
            captured[name] = await capture(name, storage.staging / name)
        shutil.rmtree(storage.inputs, ignore_errors=True)
        os.rename(storage.staging, storage.inputs)
        manifest = {"inputs": captured}
        _write_json(storage.root / _MANIFEST, manifest)
    for directory, mode in ((storage.outputs, 0o777), (storage.scratch, 0o1777)):
        directory.mkdir(exist_ok=True)
        # Container users differ from the worker user; these locations hold
        # only job-written data, never source.
        os.chmod(directory, mode)
    return manifest


async def publish_once(
    storage: JobAttachmentStorage,
    name: str,
    payload: bytes,
    publish: Callable[[], Awaitable[str]],
    *,
    ownership_token: str,
) -> str:
    """Reuse a confirmed publication of identical bytes on retry.

    A job that ended before its storage existed has no ledger to consult.
    """

    if not storage.root.is_dir():
        return await publish()
    if storage.root.is_symlink() or _owner(storage) != ownership_token:
        raise AttachmentStorageError("job attachment storage is owned by another job")
    ledger_path = storage.root / _LEDGER
    try:
        ledger = json.loads(ledger_path.read_text())
    except FileNotFoundError:
        ledger = {}
    digest = hashlib.sha256(payload).hexdigest()
    confirmed = ledger.get(name)
    if isinstance(confirmed, dict) and confirmed.get("sha256") == digest:
        return confirmed["artifactRef"]
    ref = await publish()
    ledger[name] = {"sha256": digest, "artifactRef": ref}
    _write_json(ledger_path, ledger)
    return ref


def release(
    storage: JobAttachmentStorage, *, ownership_token: str, retain_outputs: bool
) -> str:
    """Remove this job's storage; keep outputs when they are the only copy."""

    if not storage.root.exists():
        return "absent"
    if storage.root.is_symlink() or _owner(storage) != ownership_token:
        raise AttachmentStorageError("job attachment storage is owned by another job")
    for directory in (storage.staging, storage.inputs, storage.scratch):
        _remove_tree(directory)
    if retain_outputs:
        return "outputs_retained"
    _remove_tree(storage.outputs)
    for name in (_MANIFEST, _LEDGER):
        (storage.root / name).unlink(missing_ok=True)
    (storage.root / _OWNER).unlink()
    storage.root.rmdir()
    return "removed"


def _remove_tree(path: Path) -> None:
    # Never follow a link out of the job-owned root.
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
