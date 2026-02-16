"""Unit tests for skill artifact materialization and cache linking."""

from __future__ import annotations

import io
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from moonmind.workflows.skills import materializer as skill_materializer
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
    assert (
        workspace.links.agents_skills_path.resolve()
        == workspace.links.skills_active_path.resolve()
    )
    assert (
        workspace.links.gemini_skills_path.resolve()
        == workspace.links.skills_active_path.resolve()
    )
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


def test_materialize_run_skill_workspace_rejects_zip_path_traversal(tmp_path):
    cache_root = tmp_path / "cache"
    run_root = tmp_path / "runs" / "zip-slip"
    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, mode="w") as handle:
        handle.writestr("../escape.txt", "bad")
        handle.writestr("speckit/SKILL.md", "---\nname: speckit\n---\n")

    selection = RunSkillSelection(
        run_id="zip-slip",
        selection_source="job_override",
        skills=(
            ResolvedSkill(
                skill_name="speckit",
                version="1.0.0",
                source_uri=bundle.resolve().as_uri(),
            ),
        ),
    )

    with pytest.raises(
        SkillMaterializationError, match="Archive member escapes destination"
    ) as exc:
        materialize_run_skill_workspace(
            selection=selection,
            run_root=run_root,
            cache_root=cache_root,
        )
    assert exc.value.code == "unsafe_archive_member"


def test_materialize_run_skill_workspace_rejects_tar_path_traversal(tmp_path):
    cache_root = tmp_path / "cache"
    run_root = tmp_path / "runs" / "tar-slip"
    bundle = tmp_path / "bundle.tar"
    with tarfile.open(bundle, mode="w") as handle:
        good = tarfile.TarInfo("speckit/SKILL.md")
        good_data = b"---\nname: speckit\n---\n"
        good.size = len(good_data)
        handle.addfile(good, fileobj=io.BytesIO(good_data))

        bad = tarfile.TarInfo("../../escape.txt")
        bad_data = b"bad"
        bad.size = len(bad_data)
        handle.addfile(bad, fileobj=io.BytesIO(bad_data))

    selection = RunSkillSelection(
        run_id="tar-slip",
        selection_source="job_override",
        skills=(
            ResolvedSkill(
                skill_name="speckit",
                version="1.0.0",
                source_uri=bundle.resolve().as_uri(),
            ),
        ),
    )

    with pytest.raises(
        SkillMaterializationError, match="Archive member escapes destination"
    ) as exc:
        materialize_run_skill_workspace(
            selection=selection,
            run_root=run_root,
            cache_root=cache_root,
        )
    assert exc.value.code == "unsafe_archive_member"


def test_materialize_run_skill_workspace_uses_git_option_separator(
    tmp_path, monkeypatch
):
    cache_root = tmp_path / "cache"
    run_root = tmp_path / "runs" / "git"
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        destination = Path(command[-1])
        _make_skill(destination, "speckit")
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(skill_materializer.subprocess, "run", fake_run)

    selection = RunSkillSelection(
        run_id="git",
        selection_source="job_override",
        skills=(
            ResolvedSkill(
                skill_name="speckit",
                version="1.0.0",
                source_uri="git+--upload-pack=evil",
            ),
        ),
    )

    materialize_run_skill_workspace(
        selection=selection,
        run_root=run_root,
        cache_root=cache_root,
    )

    assert commands
    assert commands[0][:5] == ["git", "clone", "--depth", "1", "--"]


def test_materialize_run_skill_workspace_rejects_incomplete_cache_entry(
    tmp_path, monkeypatch
):
    source_root = tmp_path / "source"
    cache_root = tmp_path / "cache"
    run_root = tmp_path / "runs" / "cache-wait"
    _make_skill(source_root, "speckit")
    (cache_root / "fixedhash").mkdir(parents=True)

    monkeypatch.setattr(
        skill_materializer,
        "_hash_skill_directory",
        lambda *_args, **_kwargs: "fixedhash",
    )
    monkeypatch.setattr(skill_materializer, "_CACHE_READY_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(skill_materializer, "_CACHE_READY_POLL_INTERVAL_SECONDS", 0.005)

    selection = RunSkillSelection(
        run_id="cache-wait",
        selection_source="job_override",
        skills=(
            ResolvedSkill(
                skill_name="speckit",
                version="1.0.0",
                source_uri=(source_root / "speckit").resolve().as_uri(),
            ),
        ),
    )

    with pytest.raises(
        SkillMaterializationError, match="did not become ready in time"
    ) as exc:
        materialize_run_skill_workspace(
            selection=selection,
            run_root=run_root,
            cache_root=cache_root,
        )

    assert exc.value.code == "cache_entry_incomplete"
