"""Unit tests for skill artifact materialization and cache linking."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonmind.workflows.skills.materializer import (
    SkillMaterializationError,
    materialize_run_skill_workspace,
)
from moonmind.workflows.skills.resolver import ResolvedSkill, RunSkillSelection


def _make_skill(root: Path, name: str, *, with_metadata: bool = True) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    if with_metadata:
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\n",
            encoding="utf-8",
        )
    (skill_dir / "steps.md").write_text("do work", encoding="utf-8")
    return skill_dir


def test_materialize_run_skill_workspace_creates_cache_and_links(tmp_path):
    source_root = tmp_path / "source"
    cache_root = tmp_path / "cache"
    run_root = tmp_path / "runs" / "run-1"
    _make_skill(source_root, "speckit")

    selection = RunSkillSelection(
        run_id="run-1",
        selection_source="job_override",
        skills=(
            ResolvedSkill(
                skill_name="speckit",
                version="1.0.0",
                source_uri=(source_root / "speckit").resolve().as_uri(),
            ),
        ),
    )

    workspace = materialize_run_skill_workspace(
        selection=selection,
        run_root=run_root,
        cache_root=cache_root,
    )

    assert workspace.links.skills_active_path.is_dir()
    assert workspace.links.agents_skills_path.is_symlink()
    assert workspace.links.gemini_skills_path.is_symlink()
    assert workspace.links.agents_skills_path.resolve() == workspace.links.skills_active_path.resolve()
    assert workspace.links.gemini_skills_path.resolve() == workspace.links.skills_active_path.resolve()
    assert workspace.skills[0].cache_path.is_dir()
    assert (workspace.skills[0].cache_path / "SKILL.md").is_file()


def test_materialize_run_skill_workspace_rejects_hash_mismatch(tmp_path):
    source_root = tmp_path / "source"
    cache_root = tmp_path / "cache"
    run_root = tmp_path / "runs" / "run-2"
    _make_skill(source_root, "speckit")

    selection = RunSkillSelection(
        run_id="run-2",
        selection_source="job_override",
        skills=(
            ResolvedSkill(
                skill_name="speckit",
                version="1.0.0",
                source_uri=(source_root / "speckit").resolve().as_uri(),
                content_hash="deadbeef",
            ),
        ),
    )

    with pytest.raises(SkillMaterializationError, match="Hash mismatch") as exc:
        materialize_run_skill_workspace(
            selection=selection,
            run_root=run_root,
            cache_root=cache_root,
        )

    assert exc.value.code == "hash_mismatch"


def test_materialize_run_skill_workspace_requires_skill_md(tmp_path):
    source_root = tmp_path / "source"
    cache_root = tmp_path / "cache"
    run_root = tmp_path / "runs" / "run-3"
    _make_skill(source_root, "speckit", with_metadata=False)

    selection = RunSkillSelection(
        run_id="run-3",
        selection_source="job_override",
        skills=(
            ResolvedSkill(
                skill_name="speckit",
                version="1.0.0",
                source_uri=(source_root / "speckit").resolve().as_uri(),
            ),
        ),
    )

    with pytest.raises(SkillMaterializationError, match="Missing SKILL.md") as exc:
        materialize_run_skill_workspace(
            selection=selection,
            run_root=run_root,
            cache_root=cache_root,
        )

    assert exc.value.code == "missing_skill_md"


def test_materialize_run_skill_workspace_rejects_duplicate_names(tmp_path):
    source_root = tmp_path / "source"
    cache_root = tmp_path / "cache"
    run_root = tmp_path / "runs" / "run-4"
    _make_skill(source_root, "speckit")

    uri = (source_root / "speckit").resolve().as_uri()
    selection = RunSkillSelection(
        run_id="run-4",
        selection_source="job_override",
        skills=(
            ResolvedSkill(skill_name="speckit", version="1", source_uri=uri),
            ResolvedSkill(skill_name="speckit", version="2", source_uri=uri),
        ),
    )

    with pytest.raises(SkillMaterializationError, match="Duplicate skill name") as exc:
        materialize_run_skill_workspace(
            selection=selection,
            run_root=run_root,
            cache_root=cache_root,
        )

    assert exc.value.code == "duplicate_skill_name"


def test_materialize_run_skill_workspace_does_not_touch_global_codex_config(tmp_path):
    source_root = tmp_path / "source"
    cache_root = tmp_path / "cache"
    run_root = tmp_path / "runs" / "run-5"
    global_codex_config = tmp_path / ".codex" / "config.toml"
    _make_skill(source_root, "speckit")

    selection = RunSkillSelection(
        run_id="run-5",
        selection_source="job_override",
        skills=(
            ResolvedSkill(
                skill_name="speckit",
                version="1.0.0",
                source_uri=(source_root / "speckit").resolve().as_uri(),
            ),
        ),
    )

    materialize_run_skill_workspace(
        selection=selection,
        run_root=run_root,
        cache_root=cache_root,
    )

    assert not global_codex_config.exists()
