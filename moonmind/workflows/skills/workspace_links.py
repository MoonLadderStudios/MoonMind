"""Filesystem helpers for shared Codex/Gemini skill adapter links."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class SkillWorkspaceError(RuntimeError):
    """Raised when workspace adapter links cannot be created or validated."""


@dataclass(frozen=True, slots=True)
class SkillWorkspaceLinks:
    """Resolved adapter link paths for one run workspace."""

    skills_active_path: Path
    agents_skills_path: Path
    gemini_skills_path: Path
    gemini_skills_available: bool = True
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


def _replace_link(path: Path, *, target: Path) -> None:
    if path.exists() or path.is_symlink():
        if path.is_symlink():
            current = path.resolve(strict=False)
            if current == target.resolve(strict=False):
                return
            path.unlink()
        else:
            raise SkillWorkspaceError(
                f"Cannot create adapter link at {path}: existing non-symlink path present"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    relative_target = Path(os.path.relpath(target, path.parent))
    path.symlink_to(relative_target)


def ensure_shared_skill_links(
    *,
    run_root: Path,
    skills_active_path: Path,
    require_gemini_link: bool = True,
) -> SkillWorkspaceLinks:
    """Create `.agents/skills` and `.gemini/skills` links to `skills_active`."""

    if not skills_active_path.exists() or not skills_active_path.is_dir():
        raise SkillWorkspaceError(
            f"skills_active path does not exist or is not a directory: {skills_active_path}"
        )

    agents_skills = run_root / ".agents" / "skills"
    gemini_skills = run_root / ".gemini" / "skills"

    _replace_link(agents_skills, target=skills_active_path)
    gemini_skills_available = True
    gemini_skills_error = None
    try:
        _replace_link(gemini_skills, target=skills_active_path)
    except SkillWorkspaceError as ex:
        if require_gemini_link:
            raise
        gemini_skills_available = False
        gemini_skills_error = str(ex)

    links = SkillWorkspaceLinks(
        skills_active_path=skills_active_path,
        agents_skills_path=agents_skills,
        gemini_skills_path=gemini_skills,
        gemini_skills_available=gemini_skills_available,
        gemini_skills_error=gemini_skills_error,
    )
    validate_shared_skill_links(links, require_gemini_link=require_gemini_link)
    return links


def validate_shared_skill_links(
    links: SkillWorkspaceLinks,
    *,
    require_gemini_link: bool | None = None,
) -> None:
    """Validate adapter symlink invariants for a run workspace."""

    if require_gemini_link is None:
        require_gemini_link = links.gemini_skills_available

    if not links.skills_active_path.exists() or not links.skills_active_path.is_dir():
        raise SkillWorkspaceError(
            f"skills_active directory missing: {links.skills_active_path}"
        )

    if not links.agents_skills_path.is_symlink():
        raise SkillWorkspaceError(
            f"Expected symlink at {links.agents_skills_path}, found non-symlink"
        )
    if require_gemini_link and not links.gemini_skills_path.is_symlink():
        raise SkillWorkspaceError(
            f"Expected symlink at {links.gemini_skills_path}, found non-symlink"
        )

    agents_resolved = links.agents_skills_path.resolve(strict=True)
    active_resolved = links.skills_active_path.resolve(strict=True)

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
