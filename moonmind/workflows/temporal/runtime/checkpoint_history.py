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
    env=None,
    executable="git",
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
                executable,
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
            env=env,
            start_new_session=env is not None,
        )

        def stop_processes():
            if env is not None:
                import os
                import signal

                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    # The child may have exited before bounded cleanup raced it.
                    pass
            elif process.poll() is None:
                process.kill()

        expired = threading.Event()

        def expire():
            expired.set()
            stop_processes()

        timer = threading.Timer(remaining, expire)
        timer.start()
        try:
            while chunk := process.stdout.read(1024 * 1024):
                if output.tell() + len(chunk) > limit:
                    raise ValueError("checkpoint Git history exceeds export bounds")
                output.write(chunk)
            status = process.wait()
            if status == 1 and allow_unrelated:
                return b""
            # Name only the subcommand: stderr may quote repository content.
            if expired.is_set():
                raise ValueError(
                    f"checkpoint Git history exhausted its capture budget during git {arguments[0]}"
                )
            if status:
                raise ValueError(
                    f"checkpoint Git history could not be verified: git {arguments[0]} exited with status {status}"
                )
            output.seek(0)
            return output.read()
        finally:
            timer.cancel()
            stop_processes()
            process.wait()
            process.stdout.close()


def capture_git_history(
    workspace: Path,
    head: str,
    *,
    high_security_mode: bool = True,
    self_contained: bool = False,
    full_ancestry: bool = False,
    env=None,
    extra_heads: tuple[str, ...] = (),
    executable="git",
):
    """Export only HEAD's ancestry beyond a declared origin baseline.

    Existing origin objects remain an explicit restore dependency. No unrelated
    branch, credential configuration, reflog, or repository hooks are exported.
    A repository without an origin baseline gets a bounded self-contained HEAD.
    An empty boundary walk selects an already published HEAD without more traversal.
    """
    if extra_heads:
        import re

        if (
            not full_ancestry
            or len(extra_heads) > 8
            or any(not re.fullmatch(r"[0-9a-f]{40}", value) for value in extra_heads)
        ):
            raise ValueError(
                "additional history roots require bounded full commit identities"
            )
    if full_ancestry and not self_contained:
        raise ValueError("full ancestry requires self-contained history")
    workspace = workspace.resolve()
    git = partial(_git, workspace, deadline=time.monotonic() + 120, env=env, executable=executable)
    if git(["rev-parse", "HEAD"]).decode().strip() != head:
        raise ValueError("checkpoint HEAD changed before history capture")
    baseline = None
    if git(["for-each-ref", "--count=1", "refs/remotes/origin"], limit=4096):
        # One walk regardless of branch count: per-ref merge-base calls made
        # clones with thousands of origin branches exhaust the capture budget.
        # Boundary commits are HEAD ancestors already published to origin.
        unpublished = (
            git(
                ["rev-list", "--boundary", head, "--not", "--remotes=origin"],
                limit=4 * 1024 * 1024,
            )
            .decode()
            .splitlines()
        )
        if not unpublished:
            baseline = head
        for line in unpublished:
            if not line.startswith("-"):
                continue
            ancestor = line[1:]
            if (
                baseline is None
                or git(
                    ["merge-base", baseline, ancestor],
                    limit=4096,
                    allow_unrelated=True,
                )
                .decode()
                .strip()
                == baseline
            ):
                baseline = ancestor
    evidence = {
        "headCommit": head,
        "baselineCommit": baseline,
        "requiresSourceObjects": baseline is not None,
    }
    if self_contained:
        return _capture_self_contained_history(
            git,
            workspace,
            head,
            None if full_ancestry else baseline,
            high_security_mode,
            extra_heads,
        )
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


def _capture_self_contained_history(
    git, workspace, head, baseline, high_security_mode, extra_heads=()
):
    """Keep unpublished ancestry plus a complete, shallow origin boundary.

    Submodule remotes are not restore authority. Export their selected objects
    instead of copying .git or requiring a later fetch from a copied URL. A
    clean dependency needs only its current commit/tree, not its entire past.
    """
    revisions = [head, *extra_heads, *([f"^{baseline}"] if baseline else [])]
    selected = (
        git(["rev-list", "--boundary", *revisions], limit=4 * 1024 * 1024)
        .decode()
        .splitlines()
    )
    commits = {line.removeprefix("-") for line in selected} | {head}
    if len(commits) > SAVED_WORK_FORMAT_SIZE_LIMITS["max_file_count"]:
        raise ValueError("checkpoint submodule history exceeds commit-count bounds")
    shallow = {line[1:] for line in selected if line.startswith("-")}
    if baseline == head:
        shallow.add(head)
    shallow_path = Path(git(["rev-parse", "--git-path", "shallow"]).decode().strip())
    if not shallow_path.is_absolute():
        shallow_path = workspace / shallow_path
    if shallow_path.exists():
        shallow.update(set(shallow_path.read_text().splitlines()) & commits)
    objects = set(commits)
    for commit in sorted(commits):
        for path in git(
            ["ls-tree", "-r", "--name-only", "-z", commit], limit=4 * 1024 * 1024
        ).split(b"\0"):
            reason = saved_work_path_exclusion(path.decode()) if path else None
            if reason and reason.startswith("sensitive-"):
                raise ValueError(
                    "checkpoint submodule history contains a credential-sensitive path"
                )
    trees = "".join(f"{commit}^{{tree}}\n" for commit in sorted(commits)).encode()
    objects.update(
        git(
            ["rev-list", "--objects", "--no-object-names", "--stdin"],
            input_bytes=trees,
            limit=4 * 1024 * 1024,
        )
        .decode()
        .splitlines()
    )
    if len(objects) > SAVED_WORK_FORMAT_SIZE_LIMITS["max_file_count"]:
        raise ValueError("checkpoint submodule history exceeds object-count bounds")
    object_ids = ("\n".join(sorted(objects)) + "\n").encode()
    raw = git(["cat-file", "--batch"], input_bytes=object_ids)
    scan = scan_saved_work_export_stream(
        iter(
            raw[offset : offset + 1024 * 1024]
            for offset in range(0, len(raw), 1024 * 1024)
        ),
        export_digest="pending",
        location="checkpoint.submodule-history",
        high_security_mode=high_security_mode,
    )
    if scan["disposition"] == "blocked":
        raise ValueError("checkpoint submodule history failed export secret scanning")
    payload = git(["pack-objects", "--stdout"], input_bytes=object_ids)
    if git(["rev-parse", "HEAD"]).decode().strip() != head:
        raise ValueError("checkpoint submodule HEAD changed during history capture")
    return payload, {
        "headCommit": head,
        "baselineCommit": baseline,
        "requiresSourceObjects": False,
        "shallowCommits": sorted(shallow),
        "contentType": "application/x-git-pack",
        "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
    }


def capture_initial_source_successor_history(
    workspace, head, *, admitted_refs, high_security_mode, env
):
    """Retain surviving source refs plus this attempt's actual current branch."""
    git = partial(_git, workspace, deadline=time.monotonic() + 120, env=env)
    branch = git(["symbolic-ref", "-q", "HEAD"], allow_unrelated=True).decode().strip()
    names = sorted(set(admitted_refs) | ({branch} if branch else set()))
    if len(names) > 8 or any(
        not name.startswith(("refs/heads/", "refs/remotes/origin/")) for name in names
    ):
        raise ValueError("initial source reference set exceeds its bound")
    for name in names:
        git(["check-ref-format", name])
    args = ["for-each-ref", "--format=%(refname) %(objectname) %(symref)", "--", *names]
    before = git(args)
    refs = {}
    for line in before.decode().splitlines():
        fields = line.split()
        if len(fields) not in {2, 3} or fields[0] not in names:
            raise ValueError("initial source reference selection changed")
        refs[fields[0]] = {
            "commit": fields[1],
            **({"symbolicTarget": fields[2]} if len(fields) == 3 else {}),
        }
    for name, value in refs.items():
        target = value.get("symbolicTarget")
        if target is not None and (
            name != "refs/remotes/origin/HEAD"
            or target not in refs
            or not target.startswith("refs/remotes/origin/")
            or refs[target].get("commit") != value["commit"]
            or refs[target].get("symbolicTarget")
        ):
            raise ValueError(
                "initial source symbolic reference exceeds saved authority"
            )
    payload, evidence = capture_git_history(
        workspace,
        head,
        high_security_mode=high_security_mode,
        self_contained=True,
        full_ancestry=True,
        env=env,
        extra_heads=tuple(sorted({value["commit"] for value in refs.values()})),
    )
    if git(args) != before:
        raise ValueError("initial source references changed during capture")
    evidence["localRefs"] = refs
    return payload, evidence
