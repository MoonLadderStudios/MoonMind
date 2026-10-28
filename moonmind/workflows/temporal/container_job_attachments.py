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

import errno
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
# Present once collection declined to publish an output that exists, so the
# outputs directory holds the only copy of it.
_RETAIN = "retain-outputs"


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

    @property
    def credentials(self) -> Path:
        return self.root / "credentials"

    @property
    def workload_credentials(self) -> Path:
        """Scoped workload credentials: ``files/`` is mounted, the rest is not."""
        return self.root / "workload-credentials"


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


_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOEXEC
# Non-blocking so a FIFO swapped in after classification cannot stall capture.
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _CLOEXEC
# The entry was replaced by a link, a different file type, or removed.
_REPLACED = {errno.ELOOP, errno.ENOENT, errno.ENOTDIR, errno.ENXIO}


def _open_at(name: str, flags: int, dir_fd: int) -> int:
    return os.open(name, flags, dir_fd=dir_fd)


def _is_link(name: str, dir_fd: int) -> bool:
    try:
        return stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except OSError:
        return False


def _open_source(workspace: Path, relative: str) -> int:
    """Open ``relative`` inside ``workspace`` one component at a time.

    No component may be a link, so a path that resolved inside the workspace
    when checked cannot be redirected outside it before it is opened.
    """

    fd = os.open(workspace, _DIRECTORY_FLAGS)
    try:
        for part in [] if relative == "." else relative.split("/"):
            try:
                child = _open_at(part, _DIRECTORY_FLAGS, fd)
            except OSError as exc:
                if exc.errno in _REPLACED and _is_link(part, fd):
                    raise AttachmentStorageError(
                        "input attachment escapes the admitted workspace"
                    ) from exc
                if exc.errno in _REPLACED:
                    raise AttachmentStorageError(
                        "input attachment source is not a directory"
                    ) from exc
                raise
            parent, fd = fd, child
            os.close(parent)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_checked(
    name: str,
    flags: int,
    dir_fd: int,
    expected: os.stat_result,
    kind: Callable[[int], bool],
) -> int | None:
    """Open the entry that was classified, or None when it was replaced."""

    try:
        fd = _open_at(name, flags, dir_fd)
    except OSError as exc:
        if exc.errno in _REPLACED:
            return None
        raise
    opened = os.fstat(fd)
    if kind(opened.st_mode) and (opened.st_dev, opened.st_ino) == (
        expected.st_dev,
        expected.st_ino,
    ):
        return fd
    os.close(fd)
    return None


def capture_directory(
    workspace: Path,
    relative: str,
    destination: Path,
    *,
    include_git: bool,
    max_files: int,
    max_bytes: int,
) -> dict:
    """Copy regular files and directories under ``workspace/relative``.

    Returns the captured identity and coverage. Every entry is opened through
    its parent's descriptor without following links and must still be the
    entry that was classified, so neither a link nor an entry swapped for one
    during capture can pull host content into the snapshot. Links, special
    files and entries replaced mid-capture are omitted and counted.
    """

    destination.mkdir(parents=True)
    source_fd = _open_source(workspace, relative)
    records: list[list[str]] = []
    omitted_links = omitted_special = changed = 0
    used_bytes = 0
    git = "absent"

    def copy_file(dir_fd: int, name: str, path: Path, info: os.stat_result) -> None:
        nonlocal changed, used_bytes
        if len(records) + 1 > max_files or used_bytes + info.st_size > max_bytes:
            raise AttachmentStorageError("input attachment exceeds its admitted bound")
        fd = _open_checked(name, _FILE_FLAGS, dir_fd, info, stat.S_ISREG)
        if fd is None:
            changed += 1
            return
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(fd, "rb") as reader, (destination / path).open("xb") as writer:
            while chunk := reader.read(1024 * 1024):
                size += len(chunk)
                if used_bytes + size > max_bytes:
                    raise AttachmentStorageError(
                        "input attachment exceeds its admitted bound"
                    )
                digest.update(chunk)
                writer.write(chunk)
            after = os.fstat(reader.fileno())
        os.chmod(destination / path, stat.S_IMODE(info.st_mode) | 0o444)
        if (after.st_size, after.st_mtime_ns) != (size, info.st_mtime_ns):
            changed += 1
        used_bytes += size
        records.append([path.as_posix(), digest.hexdigest()])

    def subdirectories(dir_fd: int, relative: Path) -> list[tuple[str, os.stat_result]]:
        nonlocal omitted_links, omitted_special, git, changed
        entries = []
        with os.scandir(dir_fd) as scan:
            for entry in scan:
                try:
                    entries.append((entry.name, entry.stat(follow_symlinks=False)))
                except FileNotFoundError:
                    changed += 1
        kept = []
        for name, info in sorted(entries, key=lambda item: item[0]):
            at_root_git = relative == Path(".") and name == ".git"
            if at_root_git and not stat.S_ISDIR(info.st_mode):
                # A worktree pointer names metadata outside the admitted source.
                git = "external" if include_git else "excluded"
            elif stat.S_ISLNK(info.st_mode):
                omitted_links += 1
            elif stat.S_ISDIR(info.st_mode):
                if at_root_git:
                    if not include_git:
                        git = "excluded"
                        continue
                    git = "directory"
                kept.append((name, info))
            elif stat.S_ISREG(info.st_mode):
                copy_file(dir_fd, name, relative / name, info)
            else:
                omitted_special += 1
        return kept

    # Descend depth-first holding one descriptor per level.
    stack: list[list] = [[source_fd, Path("."), None]]
    try:
        while stack:
            level = stack[-1]
            if level[2] is None:
                level[2] = iter(subdirectories(level[0], level[1]))
            child = next(level[2], None)
            if child is None:
                os.close(level[0])
                stack.pop()
                continue
            name, info = child
            path = level[1] / name
            child_fd = _open_checked(
                name, _DIRECTORY_FLAGS, level[0], info, stat.S_ISDIR
            )
            if child_fd is None:
                changed += 1
                continue
            # The stack owns every open descriptor from here on.
            stack.append([child_fd, path, None])
            (destination / path).mkdir()
    finally:
        for level in stack:
            os.close(level[0])
    shallow = git == "directory" and any(path == ".git/shallow" for path, _ in records)
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


def deliver_credential(
    storage: JobAttachmentStorage,
    body: bytes,
    *,
    ownership_token: str,
    file_name: str = "config.json",
) -> None:
    """Write one worker-resolved credential file for the job's read-only mount.

    It sits beside the captured inputs, never in them, and is replaced on each
    launch and removed after the container exits and on release. The file is
    world-readable only so an image's non-root user can read it; the store
    itself is reachable only by the worker and the Docker daemon.
    """

    if storage.root.is_symlink() or _owner(storage) != ownership_token:
        raise AttachmentStorageError("job attachment storage is owned by another job")
    _remove_tree(storage.credentials)
    storage.credentials.mkdir(mode=0o755)
    fd = os.open(
        storage.credentials / file_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.write(fd, body)
        os.fchmod(fd, 0o444)
    finally:
        os.close(fd)


def remove_credentials(storage: JobAttachmentStorage) -> None:
    _remove_tree(storage.credentials)


def write_plan(directory: Path, body: bytes) -> None:
    """Replace a worker-rendered plan in the job's own output directory."""

    directory.mkdir(parents=True, exist_ok=True)
    # The image's non-root user writes its reports beside the plan.
    os.chmod(directory, 0o777)
    plan = directory / "plan.yaml"
    if plan.is_symlink() or plan.exists():
        plan.unlink()
    _write_new(plan, body, 0o444)


def _owned(storage: JobAttachmentStorage, ownership_token: str) -> None:
    if storage.root.is_symlink() or _owner(storage) != ownership_token:
        raise AttachmentStorageError("job attachment storage is owned by another job")


def _write_new(path: Path, body: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, body)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)


def deliver_workload_credentials(
    storage: JobAttachmentStorage,
    *,
    ownership_token: str,
    files: dict[str, bytes],
    environment: dict[str, str],
) -> Path | None:
    """Write this launch's workload credentials; return the env file, if any.

    Files under ``files/`` are mounted read-only; like the target-registry
    config they are world-readable so a non-root tool user can read them,
    while the store itself is reachable only by the worker and the daemon.
    The env file stays worker-only (0600) and is read by the Docker CLI, so
    no value appears in argv.
    """

    _owned(storage, ownership_token)
    root = storage.workload_credentials
    if root.is_symlink():
        root.unlink()
    root.mkdir(mode=0o700, exist_ok=True)
    retire_workload_credentials(storage)
    (root / "files").mkdir(mode=0o755)
    for name, body in files.items():
        _write_new(root / "files" / name, body, 0o444)
    if not environment:
        return None
    lines = []
    for name, value in environment.items():
        if "\n" in value or "\r" in value or "\0" in value:
            raise AttachmentStorageError("environment credential is not a single line")
        lines.append(f"{name}={value}\n")
    _write_new(root / "env", "".join(lines).encode(), 0o600)
    return root / "env"


def replace_workload_credential_file(
    storage: JobAttachmentStorage, *, ownership_token: str, name: str, body: bytes
) -> None:
    """Atomically swap one mounted file, as a renewed session does."""

    _owned(storage, ownership_token)
    files = storage.workload_credentials / "files"
    if files.is_symlink() or not files.is_dir():
        raise AttachmentStorageError("workload credentials were not delivered")
    pending = files / f".{name}.{uuid.uuid4().hex}"
    _write_new(pending, body, 0o444)
    current = files / name
    if current.is_file() and not current.is_symlink():
        # The container may already have written the replaced values.
        os.link(current, _retired(storage) / uuid.uuid4().hex)
    os.replace(pending, current)


def _retired(storage: JobAttachmentStorage) -> Path:
    retired = storage.workload_credentials / "retired"
    if retired.is_symlink():
        retired.unlink()
    retired.mkdir(mode=0o700, exist_ok=True)
    return retired


def retire_workload_credentials(storage: JobAttachmentStorage) -> None:
    """Take delivered values out of the job's reach, kept only for redaction.

    Evidence can still hold any value a launch or renewal ever delivered, and
    a publication retry must redact it again, so values stay worker-only
    until cleanup removes them.
    """

    root = storage.workload_credentials
    if root.is_symlink() or not root.is_dir():
        return
    files = root / "files"
    delivered = [root / "env"]
    if files.is_dir() and not files.is_symlink():
        delivered.extend(files.iterdir())
    for path in delivered:
        if path.is_file() and not path.is_symlink():
            os.replace(path, _retired(storage) / uuid.uuid4().hex)
    _remove_tree(files)
    (root / "env").unlink(missing_ok=True)


def delivered_workload_values(storage: JobAttachmentStorage) -> list[str]:
    """Every value delivered to the job, read back only to redact evidence."""

    root = storage.workload_credentials
    bodies = []
    for path in (
        root / "env",
        *sorted((root / "files").glob("[!.]*")),
        *sorted((root / "retired").glob("*")),
    ):
        if path.is_file() and not path.is_symlink():
            bodies.append(path.read_text(errors="replace"))
    values = []
    for body in bodies:
        values.append(body.strip())
        for line in body.splitlines():
            if not line.startswith("["):
                values.append(line.split("=", 1)[-1].strip())
    return sorted({value for value in values if len(value) >= 8}, key=len, reverse=True)


def load_workload_lease(storage: JobAttachmentStorage) -> dict | None:
    try:
        return json.loads((storage.workload_credentials / "lease.json").read_text())
    except (FileNotFoundError, NotADirectoryError):
        return None


def write_workload_lease(
    storage: JobAttachmentStorage, lease: dict, *, ownership_token: str
) -> None:
    """Value-free record of issued sessions, kept beside (not in) the mount."""

    _owned(storage, ownership_token)
    storage.workload_credentials.mkdir(mode=0o700, exist_ok=True)
    _write_json(storage.workload_credentials / "lease.json", lease)


def remove_workload_credentials(storage: JobAttachmentStorage) -> None:
    """Delete every delivered and retired value; the lease record stays."""

    root = storage.workload_credentials
    if root.is_symlink():
        root.unlink()
        return
    _remove_tree(root / "files")
    _remove_tree(root / "retired")
    (root / "env").unlink(missing_ok=True)


def retain_outputs(storage: JobAttachmentStorage, *, ownership_token: str) -> None:
    """Keep outputs through release; collection left one unpublished."""

    if not storage.root.is_dir():
        return
    if storage.root.is_symlink() or _owner(storage) != ownership_token:
        raise AttachmentStorageError("job attachment storage is owned by another job")
    (storage.root / _RETAIN).touch()


def release(
    storage: JobAttachmentStorage, *, ownership_token: str, retain_outputs: bool
) -> str:
    """Remove this job's storage; keep outputs when they are the only copy."""

    if not storage.root.exists():
        return "absent"
    if storage.root.is_symlink() or _owner(storage) != ownership_token:
        raise AttachmentStorageError("job attachment storage is owned by another job")
    for directory in (
        storage.staging,
        storage.inputs,
        storage.scratch,
        storage.credentials,
        storage.workload_credentials,
    ):
        _remove_tree(directory)
    if retain_outputs or (storage.root / _RETAIN).exists():
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
