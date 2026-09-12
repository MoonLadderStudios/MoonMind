"""Bounded Git index and selected-history evidence for workspace checkpoints."""

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
    saved_work_path_exclusion,
    scan_saved_work_export_stream,
)


def capture_git_index_patch(
    workspace: Path, head: str, *, high_security_mode: bool = True
):
    """Export the selected index independently of subsequent worktree edits.

    The patch is relative to the saved HEAD and contains binary data and modes.
    Raw index blobs are scanned before Git's binary encoding can hide secrets.
    """
    git = partial(_git, workspace.resolve(), deadline=time.monotonic() + 120)
    if git(["ls-files", "--unmerged", "-z"]):
        raise ValueError("checkpoint cannot export an unresolved Git index")
    paths = sorted(
        name.decode("utf-8")
        for name in git(
            ["diff", "--cached", "--name-only", "--no-renames", "-z", head]
        ).split(b"\0")
        if name and not saved_work_path_exclusion(name.decode("utf-8"))
    )
    if len(paths) > SAVED_WORK_FORMAT_SIZE_LIMITS["max_file_count"]:
        raise ValueError("checkpoint Git index exceeds path-count bounds")
    if not paths:
        return b"", paths
    payload = git(
        [
            "diff",
            "--cached",
            "--binary",
            "--full-index",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            head,
            "--",
            *paths,
        ]
    )
    if not high_security_mode:
        # Also used for immutable restore comparison: this is not a new export
        # authorization, and changing deployment policy must not rewrite it.
        return payload, paths
    entries = git(["ls-files", "--stage", "-z", "--", *paths])
    objects = b"\n".join(
        entry.split(b"\t", 1)[0].split()[1] for entry in entries.split(b"\0") if entry
    )
    raw = git(["cat-file", "--batch"], input_bytes=objects + b"\n") if objects else b""
    for location, exported in (("patch", payload), ("blobs", raw)):
        scan = scan_saved_work_export_stream(
            iter(
                exported[offset : offset + 1024 * 1024]
                for offset in range(0, len(exported), 1024 * 1024)
            ),
            export_digest="pending",
            location="checkpoint.index." + location,
            high_security_mode=high_security_mode,
        )
        if scan["disposition"] == "blocked":
            raise ValueError("checkpoint Git index failed export secret scanning")
    return payload, paths


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
                "--literal-pathspecs",
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


def capture_git_history(workspace: Path, head: str, *, high_security_mode: bool = True):
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
    # A bundle preserves exact commits, so excluded credential paths cannot be
    # removed from it. Check every selected commit's introduced/modified paths
    # (including merges and duplicate blob aliases), independent of heuristics.
    # Deleting a baseline-owned path does not export its credential contents.
    commits = git(["rev-list", *revisions], limit=4 * 1024 * 1024)
    changed_paths = git(
        [
            "diff-tree",
            "--stdin",
            "--root",
            "-r",
            "-m",
            "--no-commit-id",
            "--name-only",
            "--no-renames",
            "--diff-filter=ACMRT",
            "-z",
        ],
        input_bytes=commits,
        limit=4 * 1024 * 1024,
    )
    for path in changed_paths.split(b"\0"):
        if not path:
            continue
        reason = saved_work_path_exclusion(path.decode("utf-8"))
        if reason and reason.startswith("sensitive-"):
            raise ValueError(
                "checkpoint Git history contains a credential-sensitive path"
            )
    unpacked = git(["cat-file", "--batch"], input_bytes=objects)
    scan = scan_saved_work_export_stream(
        iter(
            unpacked[offset : offset + 1024 * 1024]
            for offset in range(0, len(unpacked), 1024 * 1024)
        ),
        export_digest="pending",
        location="checkpoint.selected-history",
        high_security_mode=high_security_mode,
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
