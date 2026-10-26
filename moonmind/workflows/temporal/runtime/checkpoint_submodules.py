"""Select nested worktrees without exporting their local Git authority."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from moonmind.schemas.saved_work_models import (
    SAVED_WORK_FORMAT_SIZE_LIMITS,
    saved_work_path_exclusion,
)

from .checkpoint_history import _git, capture_git_index_patch


def checkpoint_worktrees(
    workspace: Path, *, include_untracked: bool, include_ignored: bool
) -> tuple[set[str], dict[str, Path], set[str]]:
    """Return selected paths and initialized repositories, relative to workspace.

    Git does not enumerate files below gitlinks. Walk each initialized gitlink
    explicitly; an empty checkout needs no Git export. Never follow a replaced
    directory or silently omit files in a populated, non-repository gitlink.
    """
    members: set[str] = set()
    repositories: dict[str, Path] = {}
    exclusions: set[str] = set()

    def visit(repo: Path, prefix: Path):
        if len(prefix.parts) > 64:
            raise ValueError("checkpoint submodule nesting exceeds path bounds")
        staged = _git(repo, ["ls-files", "--stage", "-z"]).decode().split("\0")
        gitlinks = {
            entry.partition("\t")[2] for entry in staged if entry.startswith("160000 ")
        }
        head_gitlinks = (
            {
                entry.partition("\t")[2]
                for entry in _git(repo, ["ls-tree", "-r", "-z", "HEAD"])
                .decode()
                .split("\0")
                if entry.startswith("160000 ")
            }
            if gitlinks
            else set()
        )
        args = ["ls-files", "--cached", "--exclude-standard", "-z"]
        if include_untracked:
            args.append("--others")
        paths = set(_git(repo, args).decode().split("\0")) - {""}
        if include_ignored:
            paths.update(
                _git(
                    repo,
                    ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
                )
                .decode()
                .split("\0")
            )
            paths.discard("")
        members.update((prefix / name).as_posix() for name in paths)
        exclusions.update(
            (prefix / name).as_posix()
            for name in paths
            if saved_work_path_exclusion(name)
            or saved_work_path_exclusion((prefix / name).as_posix())
        )
        if len(members - exclusions) > SAVED_WORK_FORMAT_SIZE_LIMITS["max_file_count"]:
            raise ValueError("checkpoint submodule worktrees exceed path-count bounds")
        for name in sorted(gitlinks):
            relative = prefix / name
            if saved_work_path_exclusion(relative.as_posix()):
                continue
            module = workspace / relative
            if module.resolve() != module or not module.is_dir():
                raise ValueError(
                    f"Workspace snapshot cannot preserve submodule worktree state: {relative}"
                )
            if not any(module.iterdir()):
                if name not in head_gitlinks:
                    raise ValueError(
                        f"Workspace snapshot cannot preserve newly staged empty submodule: {relative}"
                    )
                continue
            if (
                not (module / ".git").exists()
                or Path(
                    _git(module, ["rev-parse", "--show-toplevel"]).decode().strip()
                ).resolve()
                != module
            ):
                raise ValueError(
                    f"Workspace snapshot cannot preserve non-repository submodule contents: {relative}"
                )
            repositories[relative.as_posix()] = module
            visit(module, relative)

    visit(workspace, Path())
    return members, repositories, exclusions


def submodule_capture_states(
    repositories: Mapping[str, Path],
    members: set[str],
    *,
    high_security_mode: bool,
    exclusions: set[str],
) -> dict[str, dict[str, Any]]:
    """Bind nested HEAD, index, status and selected files to one capture."""
    states = {}
    for name, repo in repositories.items():
        head = _git(repo, ["rev-parse", "HEAD"]).decode().strip()
        patch, paths = capture_git_index_patch(
            repo, head, high_security_mode=high_security_mode
        )
        status = _git(repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
        fingerprints = {}
        for member in members:
            if not member.startswith(name + "/") or member in exclusions:
                continue
            path = repo / member.removeprefix(name + "/")
            if path.exists() or path.is_symlink():
                st = path.lstat()
                fingerprints[member] = (
                    st.st_ino,
                    st.st_size,
                    st.st_mtime_ns,
                    st.st_mode,
                )
        states[name] = {
            "headCommit": head,
            "statusDigest": "sha256:" + hashlib.sha256(status).hexdigest(),
            "indexPatch": patch,
            "indexPaths": paths,
            "fingerprints": fingerprints,
        }
    return states
