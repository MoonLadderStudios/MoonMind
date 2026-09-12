"""Bounded, deterministic workspace exports at the filesystem boundary."""

from __future__ import annotations

import gzip
import hashlib
import os
import stat
import tarfile
import tempfile
from pathlib import Path

from moonmind.schemas.saved_work_models import (
    SAVED_WORK_FORMAT_SIZE_LIMITS,
    saved_work_path_exclusion,
    scan_saved_work_export_stream,
)


def staged_paths_from_status(status):
    records = iter(status.split("\0"))
    paths = []
    for line in records:
        if len(line) < 4:
            continue
        if line[0] not in {" ", "?"}:
            paths.append(line[3:])
        if line[0] in {"R", "C"}:
            original = next(records, "")
            if original:
                paths.append(original)
    return paths


def build_workspace_archive(workspace: Path, *, members: set[str] | None = None):
    """Never upload an unstable, oversized, or credential-shaped export."""
    limits = SAVED_WORK_FORMAT_SIZE_LIMITS
    entries = []
    fingerprints = {}
    total = 0
    paths = (
        workspace.rglob("*")
        if members is None
        else (workspace / name for name in members)
    )
    with tempfile.TemporaryFile() as spool:
        with tarfile.open(
            fileobj=spool, mode="w", format=tarfile.PAX_FORMAT
        ) as archive:
            for path in sorted(paths):
                relative = path.relative_to(workspace).as_posix()
                if saved_work_path_exclusion(relative):
                    continue
                if not path.exists() and not path.is_symlink():
                    continue
                if not path.resolve().is_relative_to(workspace):
                    raise ValueError("workspace archive member escapes workspace")
                before = path.lstat()
                if stat.S_ISDIR(before.st_mode):
                    continue
                if not (stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)):
                    raise ValueError("workspace archive has unsupported file type")
                total += before.st_size
                if (
                    before.st_size > limits["max_file_bytes"]
                    or total > limits["max_total_bytes"]
                    or len(entries) >= limits["max_file_count"]
                ):
                    raise ValueError("workspace archive exceeds saved-work limits")
                fingerprints[path] = (
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_mode,
                )
                info = archive.gettarinfo(str(path), arcname=relative)
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                entry = {
                    "path": relative,
                    "mode": format(stat.S_IMODE(before.st_mode), "04o"),
                }
                if stat.S_ISLNK(before.st_mode):
                    archive.addfile(info)
                    entry.update(type="symlink", target=os.readlink(path))
                else:
                    digest = hashlib.sha256()

                    class Reader:
                        def read(self, size=-1):
                            data = source.read(size)
                            digest.update(data)
                            return data

                    with path.open("rb") as source:
                        archive.addfile(info, Reader())
                    entry.update(
                        type="file",
                        digest="sha256:" + digest.hexdigest(),
                        bytes=info.size,
                    )
                entries.append(entry)
        for path, expected in fingerprints.items():
            after = path.lstat()
            if (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_mode,
            ) != expected:
                raise ValueError("workspace changed during archive capture")
        spool.seek(0)
        scan = scan_saved_work_export_stream(
            iter(lambda: spool.read(limits["max_spool_chunk_bytes"]), b""),
            export_digest="pending",
            location="checkpoint.archive.tar",
        )
        if scan["disposition"] == "blocked":
            raise ValueError("workspace archive failed export secret scanning")
        spool.seek(0)
        with tempfile.TemporaryFile() as compressed:
            with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0) as output:
                for chunk in iter(
                    lambda: spool.read(limits["max_spool_chunk_bytes"]), b""
                ):
                    output.write(chunk)
            if compressed.tell() > limits["max_total_bytes"]:
                raise ValueError("workspace archive exceeds saved-work limits")
            compressed.seek(0)
            return compressed.read(), entries
