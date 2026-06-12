"""Tests for shared skills workspace symlink invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonmind.workflows.skills.workspace_links import (
    SkillWorkspaceError,
    cleanup_moonmind_skill_projections,
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

    links = ensure_shared_skill_links(
        run_root=run_root, skills_active_path=skills_active
    )

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

def test_ensure_shared_skill_links_can_treat_gemini_link_as_optional(tmp_path):
    run_root = tmp_path / "runs" / "run-optional-gemini"
    skills_active = run_root / "skills_active"
    skills_active.mkdir(parents=True)

    links = ensure_shared_skill_links(
        run_root=run_root,
        skills_active_path=skills_active,
        require_gemini_link=False,
    )

    assert links.agents_skills_path.is_symlink()
    assert links.agents_skills_path.resolve() == skills_active.resolve()
    assert not links.gemini_skills_path.is_symlink()
    assert links.gemini_skills_available is False
    assert links.gemini_skills_status == "skipped"
    assert links.gemini_skills_error is None
    assert not (run_root / ".gemini").exists()
    validate_shared_skill_links(links, require_gemini_link=False)


def test_ensure_shared_skill_links_chowns_created_links(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    run_root = tmp_path / "runs" / "run-owned"
    skills_active = run_root / "skills_active"
    skills_active.mkdir(parents=True)
    chowned_dirs: list[Path] = []
    lchowned_links: list[Path] = []

    def _fake_chown(
        path: str | Path,
        uid: int,
        gid: int,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        assert uid == 1000
        assert gid == 1000
        assert follow_symlinks is True
        chowned_dirs.append(Path(path))

    def _fake_lchown(path: str | Path, uid: int, gid: int) -> None:
        assert uid == 1000
        assert gid == 1000
        lchowned_links.append(Path(path))

    monkeypatch.setattr(
        "moonmind.workflows.skills.workspace_links.os.chown",
        _fake_chown,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.workspace_links.os.geteuid",
        lambda: 0,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.workspace_links.os.lchown",
        _fake_lchown,
        raising=False,
    )

    links = ensure_shared_skill_links(
        run_root=run_root,
        skills_active_path=skills_active,
        owner_uid=1000,
        owner_gid=1000,
    )

    assert links.agents_skills_path in lchowned_links
    assert links.gemini_skills_path in lchowned_links
    assert links.agents_skills_path.parent in chowned_dirs
    assert links.gemini_skills_path.parent in chowned_dirs


def test_ensure_shared_skill_links_chowns_reused_link_parents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    run_root = tmp_path / "runs" / "run-reused-owned"
    skills_active = run_root / "skills_active"
    skills_active.mkdir(parents=True)
    agents = run_root / ".agents" / "skills"
    gemini = run_root / ".gemini" / "skills"
    agents.parent.mkdir(parents=True)
    gemini.parent.mkdir(parents=True)
    agents.symlink_to(skills_active)
    gemini.symlink_to(skills_active)
    chowned_dirs: list[Path] = []

    def _fake_chown(
        path: str | Path,
        uid: int,
        gid: int,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        assert uid == 1000
        assert gid == 1000
        assert follow_symlinks is True
        chowned_dirs.append(Path(path))

    monkeypatch.setattr(
        "moonmind.workflows.skills.workspace_links.os.chown",
        _fake_chown,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.workspace_links.os.geteuid",
        lambda: 0,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.workspace_links.os.lchown",
        lambda _path, _uid, _gid: None,
        raising=False,
    )

    links = ensure_shared_skill_links(
        run_root=run_root,
        skills_active_path=skills_active,
        owner_uid=1000,
        owner_gid=1000,
    )

    assert links.agents_skills_status == "reused"
    assert links.gemini_skills_status == "reused"
    assert links.agents_skills_path.parent in chowned_dirs
    assert links.gemini_skills_path.parent in chowned_dirs


def test_ensure_shared_skill_links_replaces_stale_owned_agents_symlink(tmp_path):
    run_root = tmp_path / "runs" / "run-stale-owned"
    skills_active = run_root / "runtime" / "skills_active" / "active"
    stale_active = run_root / "runtime" / "skills_active" / "stale"
    skills_active.mkdir(parents=True)
    stale_active.mkdir(parents=True)
    (stale_active / "_manifest.json").write_text(
        '{"snapshot_id": "stale"}\n',
        encoding="utf-8",
    )
    agents = run_root / ".agents" / "skills"
    agents.parent.mkdir(parents=True)
    agents.symlink_to(stale_active)

    links = ensure_shared_skill_links(
        run_root=run_root,
        skills_active_path=skills_active,
        owned_roots=(run_root / "runtime" / "skills_active",),
    )

    assert links.agents_skills_path.is_symlink()
    assert links.agents_skills_path.resolve() == skills_active.resolve()
    assert links.agents_skills_status == "created"
    validate_shared_skill_links(links)


def test_validate_shared_skill_links_detects_target_drift(tmp_path):
    run_root = tmp_path / "runs" / "run-3"
    skills_active = run_root / "skills_active"
    skills_active.mkdir(parents=True)

    other_target = run_root / "other"
    other_target.mkdir(parents=True)

    gemini = run_root / ".gemini" / "skills"
    gemini.parent.mkdir(parents=True)
    gemini.symlink_to(Path("../skills_active"))

    links = ensure_shared_skill_links(
        run_root=run_root, skills_active_path=skills_active
    )
    # Force drift after creation.
    agents = links.agents_skills_path
    agents.unlink()
    agents.symlink_to(Path("../other"))

    with pytest.raises(SkillWorkspaceError, match="does not resolve to skills_active"):
        validate_shared_skill_links(links)

def test_ensure_shared_skill_links_rejects_unknown_symlink(tmp_path):
    run_root = tmp_path / "runs" / "run-unknown-link"
    skills_active = run_root / "skills_active"
    external = tmp_path / "external"
    skills_active.mkdir(parents=True)
    external.mkdir()
    agents = run_root / ".agents" / "skills"
    agents.parent.mkdir(parents=True)
    agents.symlink_to(external)

    with pytest.raises(SkillWorkspaceError, match="existing symlink"):
        ensure_shared_skill_links(run_root=run_root, skills_active_path=skills_active)


def test_cleanup_moonmind_skill_projections_removes_only_owned_symlinks(tmp_path):
    run_root = tmp_path / "runs" / "run-clean"
    active = run_root / "runtime" / "skills_active" / "active"
    active.mkdir(parents=True)
    (active / "_manifest.json").write_text('{"snapshot_id": "active"}\n')
    agents = run_root / ".agents" / "skills"
    gemini = run_root / ".gemini" / "skills"
    agents.parent.mkdir(parents=True)
    gemini.parent.mkdir(parents=True)
    agents.symlink_to(active)
    gemini.symlink_to(active)
    repo_authored = run_root / ".agents" / "local"
    repo_authored.mkdir(parents=True)

    result = cleanup_moonmind_skill_projections(
        run_root=run_root,
        skills_active_path=active.parent,
        owned_roots=(active.parent,),
    )

    assert result.removed_paths == (agents, gemini)
    assert not agents.exists()
    assert not agents.is_symlink()
    assert not gemini.exists()
    assert not gemini.is_symlink()
    assert repo_authored.is_dir()
    assert (run_root / ".agents").is_dir()
    assert not (run_root / ".gemini").exists()


def test_cleanup_moonmind_skill_projections_preserves_repo_authored_agents_skills(
    tmp_path,
):
    run_root = tmp_path / "runs" / "run-preserve"
    active = run_root / "runtime" / "skills_active" / "active"
    active.mkdir(parents=True)
    repo_skill = run_root / ".agents" / "skills" / "repo-skill"
    repo_skill.mkdir(parents=True)

    result = cleanup_moonmind_skill_projections(
        run_root=run_root,
        skills_active_path=active.parent,
        owned_roots=(active.parent,),
    )

    assert result.removed_paths == ()
    assert result.skipped_paths == (run_root / ".agents" / "skills",)
    assert repo_skill.is_dir()
