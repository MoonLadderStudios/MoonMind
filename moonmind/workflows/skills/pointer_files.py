"""Detection of git symlinks checked out as plain pointer files.

A repository may store a skill file as a git symlink (mode ``120000``), for
example ``.agents/skills/moonspec-*`` linking into the ``moonspec`` submodule
bundle. When such a repository is checked out with ``core.symlinks=false`` or
on a filesystem without symlink support, the working tree contains a regular
file whose entire content is the relative link target (for example
``../../../moonspec/bundle/skills/moonspec-verify/SKILL.md``). Copying that
file into a resolved skill snapshot contaminates the snapshot with pointer
text instead of skill content, and the agent runtime cannot resolve the
relative target from the projected snapshot.

Skill source loading must therefore treat a flattened pointer file the same
way it treats a symlink: dereference it when the target is available, and
fail fast with an actionable error when it is not.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Git stores a symlink target as the blob content; targets are short relative
# paths, so anything larger than this is real file content, not a pointer.
FLATTENED_SYMLINK_MAX_BYTES = 4096

SUBMODULE_REMEDIATION_HINT = (
    "materialize the real content in the skill source checkout "
    "(for a target inside a git submodule: git submodule update --init) "
    "and re-resolve the skill snapshot"
)


@dataclass(frozen=True, slots=True)
class FlattenedSkillSymlink:
    """A verified flattened skill symlink and its resolved target path."""

    target: str
    target_path: Path


def flattened_symlink_target(path: Path) -> str | None:
    """Return the relative link target when *path* is a flattened git symlink.

    A flattened git symlink is a regular file (not an actual symlink) whose
    entire content is a single relative path starting with ``./`` or ``../``.
    Returns ``None`` for anything that does not match that shape.
    """

    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > FLATTENED_SYMLINK_MAX_BYTES:
            return None
        content = path.read_bytes()
    except OSError:
        return None

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None

    target = text.rstrip("\r\n")
    if not target or "\n" in target or "\r" in target or "\x00" in target:
        return None
    if not (target.startswith("../") or target.startswith("./")):
        return None
    return target


def resolve_flattened_skill_symlink(
    path: Path,
    *,
    skill_dir: Path,
    allowed_root: Path | None = None,
) -> FlattenedSkillSymlink | None:
    """Return a trusted flattened symlink target for a file in *skill_dir*.

    Pointer-shaped text alone is not enough to dereference a file because repo
    and local skill sources are untrusted inputs. The file must either still be
    tracked by Git as a symlink, or point to the expected external skill bundle
    shape: ``bundle/skills/<skill-name>/<same-relative-path>``.
    """

    target = flattened_symlink_target(path)
    if target is None:
        return None

    target_path = (path.parent / target).resolve(strict=False)
    if allowed_root is not None and not _is_relative_to(
        target_path,
        allowed_root.resolve(strict=False),
    ):
        return None
    if _git_index_symlink_target(path) == target or _matches_skill_bundle_target(
        path,
        target_path=target_path,
        skill_dir=skill_dir,
    ):
        return FlattenedSkillSymlink(target=target, target_path=target_path)
    return None


def skill_source_allowed_root(skill_dir: Path) -> Path:
    """Return the broadest local root a skill source may dereference within."""

    resolved = skill_dir.resolve(strict=False)
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
            return candidate.resolve(strict=False)
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _matches_skill_bundle_target(
    path: Path,
    *,
    target_path: Path,
    skill_dir: Path,
) -> bool:
    source_root = skill_dir.resolve(strict=False)
    try:
        relative_path = path.resolve(strict=False).relative_to(source_root)
    except ValueError:
        return False

    for candidate in target_path.parents:
        if candidate.name != source_root.name:
            continue
        try:
            bundle_dir = candidate.parent.parent
        except IndexError:
            continue
        if candidate.parent.name != "skills" or bundle_dir.name != "bundle":
            continue
        try:
            target_relative = target_path.relative_to(candidate)
        except ValueError:
            continue
        if target_relative == relative_path:
            return True
    return False


def _git_index_symlink_target(path: Path) -> str | None:
    repo_root = _git_repository_root(path)
    if repo_root is None:
        return None

    try:
        relative_path = path.resolve(strict=False).relative_to(repo_root).as_posix()
    except ValueError:
        return None

    ls_files = _run_git(
        repo_root,
        "ls-files",
        "-s",
        "--",
        relative_path,
    )
    if ls_files is None:
        return None

    for line in ls_files.splitlines():
        metadata, separator, _tracked_path = line.partition("\t")
        if not separator:
            continue
        fields = metadata.split()
        if len(fields) < 2 or fields[0] != "120000":
            continue
        blob = _run_git(repo_root, "cat-file", "-p", fields[1])
        if blob is None:
            return None
        return blob.rstrip("\r\n")
    return None


def _git_repository_root(path: Path) -> Path | None:
    anchor = path if path.is_dir() else path.parent
    output = _run_git(anchor, "rev-parse", "--show-toplevel")
    if not output:
        return None
    return Path(output.strip()).resolve(strict=False)


def _run_git(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout
