"""Unit tests for skill artifact materialization and cache linking."""

from __future__ import annotations

import socket
import subprocess
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from moonmind.workflows.skills.materializer import (
    SkillMaterializationError,
    _extract_archive,
    _resolve_source_root,
    _validate_public_remote_host,
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


def test_extract_archive_rejects_zip_path_traversal(tmp_path):
    archive = tmp_path / "malicious.zip"
    destination = tmp_path / "extract"
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive, mode="w") as bundle:
        bundle.writestr("../evil.txt", "pwnd")

    with pytest.raises(SkillMaterializationError, match="not allowed") as exc:
        _extract_archive(archive, destination)

    assert exc.value.code == "unsafe_bundle_member"
    assert not (tmp_path / "evil.txt").exists()


def test_extract_archive_rejects_tar_path_traversal(tmp_path):
    archive = tmp_path / "malicious.tar"
    destination = tmp_path / "extract"
    destination.mkdir(parents=True)
    with tarfile.open(archive, mode="w") as bundle:
        info = tarfile.TarInfo(name="../evil.txt")
        info.size = len(b"pwnd")
        bundle.addfile(info, fileobj=BytesIO(b"pwnd"))

    with pytest.raises(SkillMaterializationError, match="not allowed") as exc:
        _extract_archive(archive, destination)

    assert exc.value.code == "unsafe_bundle_member"
    assert not (tmp_path / "evil.txt").exists()


def test_validate_public_remote_host_rejects_private_ip(monkeypatch):
    monkeypatch.setattr(
        "moonmind.workflows.skills.materializer.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443))
        ],
    )

    with pytest.raises(SkillMaterializationError, match="non-public address") as exc:
        _validate_public_remote_host("https://example.com/skill.zip")

    assert exc.value.code == "bundle_fetch_failed"


def test_resolve_source_root_uses_git_clone_end_of_options_separator(
    tmp_path, monkeypatch
):
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(
        "moonmind.workflows.skills.materializer.subprocess.run",
        fake_run,
    )

    entry = ResolvedSkill(
        skill_name="speckit",
        version="1.0.0",
        source_uri="git+https://github.com/example/repo.git",
    )

    _resolve_source_root(entry, tmp_path)

    assert calls
    assert calls[0][:5] == ["git", "clone", "--depth", "1", "--"]
