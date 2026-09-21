"""The Git author/committer identity MoonMind prepares repositories with.

Publication and the sandbox workspace an agent commits in resolve their
identity here, so a repository MoonMind materialized authors commits the
same way wherever the commit is created. The deployment already determines
this value, so an undeclared identity resolves to a documented default
instead of blocking a commit-capable run.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_GIT_USER_NAME = "MoonMind Worker"
DEFAULT_GIT_USER_EMAIL = "moonmind-worker@users.noreply.github.com"


def resolve_git_identity() -> tuple[str, str]:
    """Return the configured commit identity, or the documented default."""

    from moonmind.config.settings import settings

    name = str(settings.workflow.git_user_name or "").strip() or DEFAULT_GIT_USER_NAME
    email = (
        str(settings.workflow.git_user_email or "").strip() or DEFAULT_GIT_USER_EMAIL
    )
    return name, email


def _quote_config_value(value: str) -> str:
    """Quote a git-config value so deployment text stays data, not syntax."""

    if "\n" in value or "\r" in value:
        raise ValueError("git identity value must not contain a line break")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _resolve_workspace_git_dir(workspace: Path) -> Path | None:
    """Return the workspace's own git directory, or None when not a checkout.

    A directory that merely contains a ``.git`` path (for example a bare
    ``.git/info`` left by an input projection) is not a repository: only a
    git directory carrying its own ``HEAD`` is treated as a Git checkout so
    identity is never fabricated into a non-repository.
    """

    dot_git = workspace / ".git"
    try:
        if dot_git.is_symlink():
            return None
        if dot_git.is_dir():
            git_dir = dot_git
        elif dot_git.is_file():
            try:
                pointer = dot_git.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not pointer.startswith("gitdir:"):
                return None
            raw = pointer[len("gitdir:"):].strip()
            if not raw:
                return None
            candidate = (workspace / raw) if not Path(raw).is_absolute() else Path(raw)
            try:
                resolved = candidate.resolve()
                workspace_root = workspace.resolve()
            except OSError:
                return None
            if resolved != workspace_root and not resolved.is_relative_to(
                workspace_root
            ):
                return None
            if not resolved.is_dir() or resolved.is_symlink():
                return None
            git_dir = resolved
        else:
            return None
    except OSError:
        return None
    try:
        if not (git_dir / "HEAD").is_file():
            return None
    except OSError:
        return None
    return git_dir


def _write_user_identity(lines: list[str], name: str, email: str) -> list[str]:
    """Rewrite only the ``[user]`` name/email entries, preserving the rest."""

    wanted = {"name": name, "email": email}
    out: list[str] = []
    section = ""
    seen: set[str] = set()
    user_section_open = False

    def _flush_missing() -> None:
        for key in ("name", "email"):
            if key not in seen:
                out.append(f"\t{key} = {_quote_config_value(wanted[key])}")
                seen.add(key)

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if user_section_open:
                _flush_missing()
                user_section_open = False
            section = stripped.lower()
            if section == "[user]":
                user_section_open = True
            out.append(line)
            continue
        if section == "[user]":
            key, sep, _ = stripped.partition("=")
            if not sep:
                key, _, _ = stripped.partition(" ")
                sep = " " if key != stripped else ""
            if sep and key.strip().lower() in wanted:
                matched = key.strip().lower()
                indent = line[: len(line) - len(line.lstrip())]
                out.append(
                    f"{indent}{matched} = {_quote_config_value(wanted[matched])}"
                )
                seen.add(matched)
                continue
        out.append(line)
    if user_section_open:
        _flush_missing()
    headers = {
        line.strip().lower()
        for line in out
        if line.strip().startswith("[")
    }
    if "[user]" not in headers:
        if out and out[-1].strip():
            out.append("")
        out.append("[user]")
        _flush_missing()
    return out


def ensure_workspace_git_identity(
    workspace: Path | str,
    *,
    runtime_uid: int,
    runtime_gid: int,
) -> bool:
    """Reapply the resolved commit identity to a materialized Git workspace.

    Clone-time identity does not survive workspace reconciliation: retries
    reuse an existing attempt workspace so no clone runs, an authoritative
    checkpoint restore replaces ``.git/config``, and an additive restore can
    overwrite it with the imported config. This rewrites only the ``[user]``
    name/email entries of the workspace's own config, preserving every other
    entry and all checked-out work, then hands the config to the selected
    runtime owner like the rest of the promoted tree.

    Returns True when an identity was (re)applied, False when the workspace
    is not a Git checkout.
    """

    name, email = resolve_git_identity()
    root = Path(workspace)
    git_dir = _resolve_workspace_git_dir(root)
    if git_dir is None:
        return False
    config = git_dir / "config"
    try:
        if config.exists():
            existing = config.read_text(encoding="utf-8").splitlines()
        else:
            existing = []
    except OSError:
        return False
    updated = _write_user_identity(existing, name, email)
    config.write_text("\n".join(updated) + "\n", encoding="utf-8")
    os.chown(config, runtime_uid, runtime_gid, follow_symlinks=False)
    return True
