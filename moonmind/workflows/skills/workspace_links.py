"""Filesystem helpers for shared Codex/Gemini skill adapter links."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

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
) -> SkillAliasResult:
    if path.exists() or path.is_symlink():
        if path.is_symlink():
            current = path.resolve(strict=False)
            if current == target.resolve(strict=False):
                return SkillAliasResult(
                    path=path,
                    status=SkillAliasStatus.REUSED,
                    available=True,
                )
            if not is_moonmind_owned_projection(
                path,
                target=target,
                owned_roots=owned_roots,
            ):
                message = (
                    f"Cannot replace adapter link at {path}: existing symlink does "
                    "not resolve under a MoonMind-owned active skill root"
                )
                if optional:
                    return SkillAliasResult(
                        path=path,
                        status=SkillAliasStatus.BLOCKED,
                        available=False,
                        reason=message,
                    )
                raise SkillWorkspaceError(message)
            path.unlink()
        else:
            message = (
                f"Cannot create adapter link at {path}: existing non-symlink path present"
            )
            if optional:
                return SkillAliasResult(
                    path=path,
                    status=SkillAliasStatus.SKIPPED,
                    available=False,
                    reason=message,
                )
            raise SkillWorkspaceError(message)

    path.parent.mkdir(parents=True, exist_ok=True)
    relative_target = Path(os.path.relpath(target, path.parent))
    path.symlink_to(relative_target)
    return SkillAliasResult(
        path=path,
        status=SkillAliasStatus.CREATED,
        available=True,
    )

def ensure_shared_skill_links(
    *,
    run_root: Path,
    skills_active_path: Path,
    require_gemini_link: bool = True,
    require_agents_link: bool = True,
    owned_roots: Sequence[Path] = (),
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
    )
    gemini_skills_available = True
    gemini_skills_status = SkillAliasStatus.REUSED.value
    gemini_skills_error = None
    try:
        gemini_result = _replace_link(
            gemini_skills,
            target=skills_active_path,
            owned_roots=owned_roots,
            optional=not require_gemini_link,
        )
        gemini_skills_available = gemini_result.available
        gemini_skills_status = gemini_result.status.value
        gemini_skills_error = gemini_result.reason
    except SkillWorkspaceError as ex:
        if require_gemini_link:
            raise
        gemini_skills_available = False
        gemini_skills_status = SkillAliasStatus.FAILED.value
        gemini_skills_error = str(ex)

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
