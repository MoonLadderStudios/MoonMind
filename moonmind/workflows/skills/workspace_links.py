"""Filesystem helpers for shared Codex/Gemini skill adapter links."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from moonmind.utils.workspace_paths import open_directory

class SkillWorkspaceError(RuntimeError):
    """Raised when workspace adapter links cannot be created or validated."""

class SkillAliasStatus(str, Enum):
    """Result for one optional runtime skill alias."""

    CREATED = "created"
    REUSED = "reused"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    FAILED = "failed"

@dataclass(frozen=True, slots=True)
class SkillAliasResult:
    """Outcome from attempting to expose one optional runtime skill alias."""

    path: Path
    status: SkillAliasStatus
    available: bool
    reason: str | None = None

@dataclass(frozen=True, slots=True)
class SkillProjectionCleanupResult:
    """Outcome from removing MoonMind-owned adapter projections."""

    removed_paths: tuple[Path, ...] = ()
    skipped_paths: tuple[Path, ...] = ()

@dataclass(frozen=True, slots=True)
class SkillWorkspaceLinks:
    """Resolved adapter link paths for one run workspace."""

    skills_active_path: Path
    agents_skills_path: Path
    gemini_skills_path: Path
    agents_skills_available: bool = True
    agents_skills_status: str = SkillAliasStatus.REUSED.value
    agents_skills_error: str | None = None
    gemini_skills_available: bool = True
    gemini_skills_status: str = SkillAliasStatus.REUSED.value
    gemini_skills_error: str | None = None

    def to_payload(self) -> dict[str, str]:
        payload = {
            "skillsActivePath": str(self.skills_active_path),
            "agentsSkillsPath": str(self.agents_skills_path),
            "geminiSkillsPath": str(self.gemini_skills_path),
        }
        if not self.gemini_skills_available:
            payload["geminiSkillsAvailable"] = "false"
            if self.gemini_skills_error:
                payload["geminiSkillsError"] = self.gemini_skills_error
        return payload

def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True

def is_moonmind_owned_projection(
    path: Path,
    *,
    target: Path,
    owned_roots: Sequence[Path] = (),
) -> bool:
    """Return true when an existing projection path is safe for MoonMind to update."""

    if not path.is_symlink():
        return False
    resolved = path.resolve(strict=False)
    target_resolved = target.resolve(strict=False)
    if resolved == target_resolved:
        return True
    for root in owned_roots:
        root_resolved = root.resolve(strict=False)
        if resolved == root_resolved or _is_relative_to(resolved, root_resolved):
            return True
    manifest_path = resolved / "_manifest.json"
    if manifest_path.is_file():
        return True
    return False

def _replace_link(
    path: Path,
    *,
    target: Path,
    owned_roots: Sequence[Path] = (),
    optional: bool = False,
    owner_uid: int | None = None,
    owner_gid: int | None = None,
) -> SkillAliasResult:
    try:
        with open_directory(path.parent, create=True) as parent_fd:
            return _replace_link_at(
                path,
                target=target,
                owned_roots=owned_roots,
                optional=optional,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
                parent_fd=parent_fd,
            )
    except OSError as exc:
        message = f"Unsafe adapter projection parent at {path.parent}: {exc}"
        if optional:
            return SkillAliasResult(
                path=path,
                status=SkillAliasStatus.BLOCKED,
                available=False,
                reason=message,
            )
        raise SkillWorkspaceError(message) from exc


def _replace_link_at(
    path: Path,
    *,
    target: Path,
    owned_roots: Sequence[Path],
    optional: bool,
    owner_uid: int | None,
    owner_gid: int | None,
    parent_fd: int,
) -> SkillAliasResult:
    # Inspect and mutate the same pinned directory, even if an agent renames
    # its lexical path while the worker is preparing the projection.
    inspected = Path(f"/proc/self/fd/{parent_fd}") / path.name
    if inspected.exists() or inspected.is_symlink():
        if inspected.is_symlink():
            current = inspected.resolve(strict=False)
            if current == target.resolve(strict=False):
                _align_projection_ownership(
                    path,
                    owner_uid=owner_uid,
                    owner_gid=owner_gid,
                    parent_fd=parent_fd,
                )
                return SkillAliasResult(
                    path=path,
                    status=SkillAliasStatus.REUSED,
                    available=True,
                )
            if not is_moonmind_owned_projection(
                inspected,
                target=target,
                owned_roots=owned_roots,
            ):
                message = (
                    f"Cannot replace adapter link at {path}: existing symlink does "
                    "not resolve under a MoonMind-owned active skill root; "
                    "repair through the workspace/materialization owner without "
                    "deleting or relocating repo-authored skill sources or "
                    "discarding run work"
                )
                if optional:
                    return SkillAliasResult(
                        path=path,
                        status=SkillAliasStatus.BLOCKED,
                        available=False,
                        reason=message,
                    )
                raise SkillWorkspaceError(message)
            os.unlink(path.name, dir_fd=parent_fd)
        else:
            message = (
                f"Cannot create adapter link at {path}: existing non-symlink path present; "
                "leaving repo-authored sources untouched and preserving run work; "
                "the run-scoped active visiblePath remains authoritative; "
                "repair through the workspace/materialization owner"
            )
            if optional:
                return SkillAliasResult(
                    path=path,
                    status=SkillAliasStatus.SKIPPED,
                    available=False,
                    reason=message,
                )
            raise SkillWorkspaceError(message)

    relative_target = Path(os.path.relpath(target, path.parent))
    os.symlink(str(relative_target), path.name, dir_fd=parent_fd)
    _align_projection_ownership(
        path,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
        parent_fd=parent_fd,
    )
    return SkillAliasResult(
        path=path,
        status=SkillAliasStatus.CREATED,
        available=True,
    )


def _align_projection_ownership(
    path: Path,
    *,
    owner_uid: int | None,
    owner_gid: int | None,
    parent_fd: int,
) -> None:
    if owner_uid is None or owner_gid is None:
        return
    if os.name != "posix":
        return
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        return
    try:
        os.fchown(parent_fd, owner_uid, owner_gid)
        os.chown(
            path.name, owner_uid, owner_gid, dir_fd=parent_fd, follow_symlinks=False
        )
    except OSError as exc:
        raise SkillWorkspaceError(
            f"Failed to align adapter projection ownership for {path}: {exc}"
        ) from exc


def ensure_shared_skill_links(
    *,
    run_root: Path,
    skills_active_path: Path,
    require_gemini_link: bool = True,
    require_agents_link: bool = True,
    owned_roots: Sequence[Path] = (),
    owner_uid: int | None = None,
    owner_gid: int | None = None,
) -> SkillWorkspaceLinks:
    """Create safe optional adapter links to `skills_active`."""

    if not skills_active_path.exists() or not skills_active_path.is_dir():
        raise SkillWorkspaceError(
            f"skills_active path does not exist or is not a directory: {skills_active_path}"
        )

    agents_skills = run_root / ".agents" / "skills"
    gemini_skills = run_root / ".gemini" / "skills"

    agents_result = _replace_link(
        agents_skills,
        target=skills_active_path,
        owned_roots=owned_roots,
        optional=not require_agents_link,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )
    gemini_skills_available = False
    gemini_skills_status = SkillAliasStatus.SKIPPED.value
    gemini_skills_error = None
    if require_gemini_link:
        try:
            gemini_result = _replace_link(
                gemini_skills,
                target=skills_active_path,
                owned_roots=owned_roots,
                optional=False,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
            )
            gemini_skills_available = gemini_result.available
            gemini_skills_status = gemini_result.status.value
            gemini_skills_error = gemini_result.reason
        except SkillWorkspaceError:
            raise

    links = SkillWorkspaceLinks(
        skills_active_path=skills_active_path,
        agents_skills_path=agents_skills,
        gemini_skills_path=gemini_skills,
        agents_skills_available=agents_result.available,
        agents_skills_status=agents_result.status.value,
        agents_skills_error=agents_result.reason,
        gemini_skills_available=gemini_skills_available,
        gemini_skills_status=gemini_skills_status,
        gemini_skills_error=gemini_skills_error,
    )
    validate_shared_skill_links(
        links,
        require_gemini_link=require_gemini_link,
        require_agents_link=require_agents_link,
    )
    return links

def cleanup_moonmind_skill_projections(
    *,
    run_root: Path,
    skills_active_path: Path | None = None,
    owned_roots: Sequence[Path] = (),
) -> SkillProjectionCleanupResult:
    """Remove only MoonMind-owned runtime adapter projections from a workspace."""

    resolved_run_root = run_root.absolute()
    active_path = (
        skills_active_path
        if skills_active_path is not None
        else resolved_run_root / "runtime" / "skills_active"
    )
    owned = tuple(owned_roots) or (active_path, resolved_run_root / "skills_active")
    candidates = (
        resolved_run_root / ".agents" / "skills",
        resolved_run_root / ".gemini" / "skills",
        resolved_run_root / "skills_active",
    )
    removed: list[Path] = []
    skipped: list[Path] = []
    for candidate in candidates:
        try:
            with open_directory(candidate.parent) as parent_fd:
                inspected = Path(f"/proc/self/fd/{parent_fd}") / candidate.name
                if not inspected.exists() and not inspected.is_symlink():
                    continue
                if not is_moonmind_owned_projection(
                    inspected, target=active_path, owned_roots=owned
                ):
                    skipped.append(candidate)
                    continue
                os.unlink(candidate.name, dir_fd=parent_fd)
                removed.append(candidate)
        except FileNotFoundError:
            continue
        except (OSError, RuntimeError):
            skipped.append(candidate)
            continue
        if candidate.parent.name == ".gemini":
            try:
                with open_directory(candidate.parent.parent) as root_fd:
                    os.rmdir(candidate.parent.name, dir_fd=root_fd)
            except OSError:
                # Removing the empty adapter parent is optional.
                pass
    return SkillProjectionCleanupResult(
        removed_paths=tuple(removed),
        skipped_paths=tuple(skipped),
    )

def validate_shared_skill_links(
    links: SkillWorkspaceLinks,
    *,
    require_gemini_link: bool | None = None,
    require_agents_link: bool | None = None,
) -> None:
    """Validate adapter symlink invariants for a run workspace."""

    if require_gemini_link is None:
        require_gemini_link = links.gemini_skills_available
    if require_agents_link is None:
        require_agents_link = links.agents_skills_available

    if not links.skills_active_path.exists() or not links.skills_active_path.is_dir():
        raise SkillWorkspaceError(
            f"skills_active directory missing: {links.skills_active_path}"
        )

    if require_agents_link and not links.agents_skills_path.is_symlink():
        raise SkillWorkspaceError(
            f"Expected symlink at {links.agents_skills_path}, found non-symlink"
        )
    if require_gemini_link and not links.gemini_skills_path.is_symlink():
        raise SkillWorkspaceError(
            f"Expected symlink at {links.gemini_skills_path}, found non-symlink"
        )

    active_resolved = links.skills_active_path.resolve(strict=True)
    if not require_agents_link:
        if not require_gemini_link:
            return
    else:
        agents_resolved = links.agents_skills_path.resolve(strict=True)

        if agents_resolved != active_resolved:
            raise SkillWorkspaceError(
                f".agents/skills does not resolve to skills_active ({agents_resolved} != {active_resolved})"
            )
    if not require_gemini_link:
        return

    gemini_resolved = links.gemini_skills_path.resolve(strict=True)
    if gemini_resolved != active_resolved:
        raise SkillWorkspaceError(
            f".gemini/skills does not resolve to skills_active ({gemini_resolved} != {active_resolved})"
        )
