"""Bounded selected-branch Git evidence, shared by workspace capture owners."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import threading
import time
from functools import partial
from pathlib import Path

from moonmind.schemas.saved_work_models import (
    SAVED_WORK_FORMAT_SIZE_LIMITS,
    scan_saved_work_export_stream,
)


def _git(
    workspace,
    arguments,
    *,
    input_bytes=None,
    limit=None,
    deadline=None,
    allow_unrelated=False,
):
    """Bound output, wall time and stderr without retaining repository secrets."""
    limit = limit or SAVED_WORK_FORMAT_SIZE_LIMITS["max_total_bytes"]
    remaining = (deadline or time.monotonic() + 120) - time.monotonic()
    if remaining <= 0:
        raise ValueError("checkpoint Git history exhausted its capture budget")
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as output:
        if input_bytes:
            source.write(input_bytes)
        source.seek(0)
        process = subprocess.Popen(
            [
                "git",
                "-c",
                f"safe.directory={workspace}",
                "-C",
                str(workspace),
                *arguments,
            ],
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        timer = threading.Timer(remaining, process.kill)
        timer.start()
        try:
            while chunk := process.stdout.read(1024 * 1024):
                if output.tell() + len(chunk) > limit:
                    raise ValueError("checkpoint Git history exceeds export bounds")
                output.write(chunk)
            status = process.wait()
            if status == 1 and allow_unrelated:
                return b""
            if status:
                raise ValueError(
                    "checkpoint Git history could not be verified within its execution budget"
                )
            output.seek(0)
            return output.read()
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def capture_git_history(workspace: Path, head: str):
    """Export only HEAD's ancestry beyond a declared origin baseline.

    Existing origin objects remain an explicit restore dependency. No unrelated
    branch, credential configuration, reflog, or repository hooks are exported.
    A repository without an origin baseline gets a bounded self-contained HEAD.
    """
    workspace = workspace.resolve()
    git = partial(_git, workspace, deadline=time.monotonic() + 120)
    if git(["rev-parse", "HEAD"]).decode().strip() != head:
        raise ValueError("checkpoint HEAD changed before history capture")
    refs = (
        git(
            ["for-each-ref", "--format=%(objectname)", "refs/remotes/origin"],
            limit=1024 * 1024,
        )
        .decode()
        .splitlines()
    )
    baseline = None
    for ref in dict.fromkeys(refs):
        ancestor = (
            git(["merge-base", head, ref], limit=4096, allow_unrelated=True)
            .decode()
            .strip()
        )
        if not ancestor:
            continue
        if (
            baseline is None
            or git(["merge-base", baseline, ancestor], limit=4096).decode().strip()
            == baseline
        ):
            baseline = ancestor
    evidence = {
        "headCommit": head,
        "baselineCommit": baseline,
        "requiresSourceObjects": baseline is not None,
    }
    if baseline == head:
        return None, evidence
    revisions = ["HEAD", *([f"^{baseline}"] if baseline else [])]
    objects = git(
        ["rev-list", "--objects", "--no-object-names", *revisions],
        limit=4 * 1024 * 1024,
    )
    if len(objects.splitlines()) > SAVED_WORK_FORMAT_SIZE_LIMITS["max_file_count"]:
        raise ValueError("checkpoint Git history exceeds object-count bounds")
    unpacked = git(["cat-file", "--batch"], input_bytes=objects)
    scan = scan_saved_work_export_stream(
        iter(
            unpacked[offset : offset + 1024 * 1024]
            for offset in range(0, len(unpacked), 1024 * 1024)
        ),
        export_digest="pending",
        location="checkpoint.selected-history",
    )
    if scan["disposition"] == "blocked":
        raise ValueError("checkpoint Git history failed export secret scanning")
    bundle = git(["bundle", "create", "-", *revisions])
    if git(["rev-parse", "HEAD"]).decode().strip() != head:
        raise ValueError("checkpoint HEAD changed during history capture")
    return bundle, {
        **evidence,
        "digest": "sha256:" + hashlib.sha256(bundle).hexdigest(),
    }
