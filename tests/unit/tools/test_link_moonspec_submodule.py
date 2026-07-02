from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[3] / "tools" / "link_moonspec_submodule.py"
)
SPEC = importlib.util.spec_from_file_location("link_moonspec_submodule", MODULE_PATH)
assert SPEC is not None
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
assert SPEC.loader is not None
SPEC.loader.exec_module(mod)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _bundle(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = tmp_path / "repo"
    source_root = repo_root / "moonspec"
    bundle_root = source_root / "bundle"
    _write(
        bundle_root / "moonspec.bundle.yaml",
        "schemaVersion: 1\n"
        "projections:\n"
        "  moonmind:\n"
        "    path: projections/moonmind.yaml\n",
    )
    _write(
        bundle_root / "projections/moonmind.yaml",
        "schemaVersion: 1\n"
        "consumer: moonmind\n"
        "mappings:\n"
        "  - from: skills/\n"
        "    to: .agents/skills/\n"
        "    mode: directory\n"
        "  - from: presets/moonspec-orchestrate.yaml\n"
        "    to: api_service/data/presets/moonspec-orchestrate.yaml\n"
        "    mode: file\n"
        "unexpectedLegacy:\n"
        "  - .gemini/commands/speckit.*.toml\n",
    )
    _write(bundle_root / "skills/moonspec-test/SKILL.md", "# Skill\n")
    _write(bundle_root / "skills/moonspec-test/agents/openai.yaml", "model: test\n")
    _write(bundle_root / "presets/moonspec-orchestrate.yaml", "name: test\n")
    return repo_root, source_root


def _plan(tmp_path: Path) -> tuple[Path, Path, list[object], list[str]]:
    repo_root, source_root = _bundle(tmp_path)
    files, unexpected = mod._planned_files(source_root, "moonmind", repo_root)
    return repo_root, source_root, files, unexpected


def test_projection_plan_reads_moonspec_manifest(tmp_path: Path) -> None:
    _repo_root, _source_root, files, unexpected = _plan(tmp_path)
    targets = {item.target_rel.as_posix() for item in files}

    assert ".agents/skills/moonspec-test/SKILL.md" in targets
    assert ".agents/skills/moonspec-test/agents/openai.yaml" in targets
    assert "api_service/data/presets/moonspec-orchestrate.yaml" in targets
    assert ".gemini/commands/speckit.*.toml" in unexpected


def test_write_creates_relative_file_level_symlinks(tmp_path: Path) -> None:
    _repo_root, _source_root, files, _unexpected = _plan(tmp_path)
    mod._write_projection(files, replace_generated=False)
    skill = next(item for item in files if item.target.name == "SKILL.md")

    assert skill.target.is_symlink()
    assert os.readlink(skill.target) == os.path.relpath(skill.source, skill.target.parent)
    assert skill.target.resolve() == skill.source.resolve()
    assert mod._drift(files) == []


def test_check_detects_missing_non_symlink_and_wrong_target(tmp_path: Path) -> None:
    _repo_root, _source_root, files, _unexpected = _plan(tmp_path)
    mod._write_projection(files, replace_generated=False)
    skill, wrapper = files[0], files[1]
    skill.target.unlink()
    wrapper.target.unlink()
    _write(wrapper.target, "regular file\n")
    preset = next(item for item in files if item.target.name == "moonspec-orchestrate.yaml")
    preset.target.unlink()
    preset.target.symlink_to("wrong-target")

    drift = mod._drift(files)

    assert f"missing: {skill.target_rel}" in drift
    assert f"non-symlink projection target: {wrapper.target_rel}" in drift
    assert f"wrong symlink target: {preset.target_rel}" in drift


def test_mixed_directories_preserve_unrelated_regular_files(tmp_path: Path) -> None:
    repo_root, _source_root, files, unexpected = _plan(tmp_path)
    moonmind_owned = repo_root / ".agents/skills/jira-implement/SKILL.md"
    _write(moonmind_owned, "# Jira\n")

    mod._write_projection(files, replace_generated=False)
    removed = mod._remove_stale_symlinks(files, unexpected, repo_root, repo_root / "moonspec")

    assert removed == []
    assert moonmind_owned.is_file()
    assert not moonmind_owned.is_symlink()


def test_refuses_source_and_target_path_escapes(tmp_path: Path) -> None:
    repo_root, source_root = _bundle(tmp_path)
    recipe = source_root / "bundle/projections/moonmind.yaml"
    recipe.write_text(
        "schemaVersion: 1\n"
        "mappings:\n"
        "  - from: ../outside.md\n"
        "    to: .agents/skills/outside.md\n"
        "    mode: file\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="source path escapes"):
        mod._planned_files(source_root, "moonmind", repo_root)

    recipe.write_text(
        "schemaVersion: 1\n"
        "mappings:\n"
        "  - from: skills/moonspec-test/SKILL.md\n"
        "    to: ../outside.md\n"
        "    mode: file\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="target path escapes"):
        mod._planned_files(source_root, "moonmind", repo_root)


def test_prunes_stale_bundle_symlinks_only(tmp_path: Path) -> None:
    repo_root, source_root, files, unexpected = _plan(tmp_path)
    mod._write_projection(files, replace_generated=False)
    stale_source = source_root / "bundle/skills/stale.md"
    _write(stale_source, "stale\n")
    stale_link = repo_root / ".agents/skills/stale.md"
    stale_link.symlink_to(os.path.relpath(stale_source, stale_link.parent))
    regular = repo_root / ".agents/skills/regular.md"
    _write(regular, "regular\n")

    removed = mod._remove_stale_symlinks(files, unexpected, repo_root, source_root)

    assert ".agents/skills/stale.md" in removed
    assert not stale_link.exists()
    assert regular.exists()


def test_replace_generated_files_requires_explicit_flag(tmp_path: Path) -> None:
    _repo_root, _source_root, files, _unexpected = _plan(tmp_path)
    skill = next(item for item in files if item.target.name == "SKILL.md")
    _write(
        skill.target,
        "<!-- Generated from moonspec/bundle/skills/moonspec-test/SKILL.md; "
        "edit MoonSpec repo instead. -->\n\n# Skill\n",
    )

    with pytest.raises(ValueError, match="--replace-generated"):
        mod._write_projection(files, replace_generated=False)

    mod._write_projection(files, replace_generated=True)

    assert skill.target.is_symlink()
    assert skill.target.resolve() == skill.source.resolve()


def test_refuses_to_replace_directories(tmp_path: Path) -> None:
    _repo_root, _source_root, files, _unexpected = _plan(tmp_path)
    skill = next(item for item in files if item.target.name == "SKILL.md")
    skill.target.mkdir(parents=True)

    with pytest.raises(ValueError, match="refusing to replace directory"):
        mod._write_projection(files, replace_generated=True)
