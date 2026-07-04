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

from pathlib import Path

# Git stores a symlink target as the blob content; targets are short relative
# paths, so anything larger than this is real file content, not a pointer.
FLATTENED_SYMLINK_MAX_BYTES = 4096

SUBMODULE_REMEDIATION_HINT = (
    "materialize the real content in the skill source checkout "
    "(for a target inside a git submodule: git submodule update --init) "
    "and re-resolve the skill snapshot"
)


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

    target = text.rstrip("\n")
    if not target or "\n" in target or "\x00" in target:
        return None
    if not (target.startswith("../") or target.startswith("./")):
        return None
    return target
