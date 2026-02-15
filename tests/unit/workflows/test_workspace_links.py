"""Tests for shared skills workspace symlink invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonmind.workflows.skills.workspace_links import (
    SkillWorkspaceError,
    ensure_shared_skill_links,
    validate_shared_skill_links,
)


def test_ensure_shared_skill_links_points_both_adapters_to_same_path(tmp_path):
    run_root = tmp_path / "runs" / "run-1"
    skills_active = run_root / "skills_active"
    skill_dir = skills_active / "speckit"
    skills_active.mkdir(parents=True)
    target = tmp_path / "cache" / "hash" / "speckit"
    target.mkdir(parents=True)
    skill_dir.symlink_to(target)

    links = ensure_shared_skill_links(run_root=run_root, skills_active_path=skills_active)

    assert links.agents_skills_path.is_symlink()
    assert links.gemini_skills_path.is_symlink()
    assert links.agents_skills_path.resolve() == skills_active.resolve()
    assert links.gemini_skills_path.resolve() == skills_active.resolve()
    validate_shared_skill_links(links)


def test_ensure_shared_skill_links_rejects_existing_non_symlink(tmp_path):
    run_root = tmp_path / "runs" / "run-2"
    skills_active = run_root / "skills_active"
    skills_active.mkdir(parents=True)
    (run_root / ".agents").mkdir(parents=True)
    (run_root / ".agents" / "skills").mkdir(parents=True)

    with pytest.raises(SkillWorkspaceError, match="non-symlink"):
        ensure_shared_skill_links(run_root=run_root, skills_active_path=skills_active)


def test_validate_shared_skill_links_detects_target_drift(tmp_path):
    run_root = tmp_path / "runs" / "run-3"
    skills_active = run_root / "skills_active"
    skills_active.mkdir(parents=True)

    other_target = run_root / "other"
    other_target.mkdir(parents=True)

    agents = run_root / ".agents" / "skills"
    gemini = run_root / ".gemini" / "skills"
    agents.parent.mkdir(parents=True)
    gemini.parent.mkdir(parents=True)
    agents.symlink_to(Path("../other"))
    gemini.symlink_to(Path("../skills_active"))

    links = ensure_shared_skill_links(run_root=run_root, skills_active_path=skills_active)
    # Force drift after creation.
    agents.unlink()
    agents.symlink_to(Path("../other"))

    with pytest.raises(SkillWorkspaceError, match="does not resolve to skills_active"):
        validate_shared_skill_links(links)
