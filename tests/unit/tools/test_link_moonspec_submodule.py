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


@pytest.fixture()
def projection_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    source = repo / "moonspec"
    bundle = source / "bundle"
    repo.mkdir()
    monkeypatch.setattr(mod, "REPO_ROOT", repo)
    monkeypatch.setattr(mod, "DEFAULT_SOURCE", source)

    _write(
        bundle / "moonspec.bundle.yaml",
        "schemaVersion: 1\nprojections:\n  moonmind:\n    path: projections/moonmind.yaml\n",
    )
    _write(
        bundle / "projections/moonmind.yaml",
        "\n".join(
            [
                "schemaVersion: 1",
                "consumer: moonmind",
                "mappings:",
                "  - from: skills/",
                "    to: .agents/skills/",
                "    mode: directory",
                "  - from: presets/moonspec-orchestrate.yaml",
                "    to: api_service/data/presets/moonspec-orchestrate.yaml",
                "    mode: file",
                "unexpectedLegacy:",
                "  - .agents/skills/moonspec-old/SKILL.md",
                "",
            ]
        ),
    )
    _write(bundle / "skills/moonspec-test/SKILL.md", "# Test skill\n")
    _write(bundle / "skills/moonspec-test/agents/openai.yaml", "name: test\n")
    _write(bundle / "presets/moonspec-orchestrate.yaml", "slug: moonspec-orchestrate\n")
    _write(repo / ".agents/skills/moonmind-owned/SKILL.md", "# MoonMind owned\n")
    return repo, source


def test_projection_plan_reads_moonspec_bundle_manifest(
    projection_repo: tuple[Path, Path],
) -> None:
    repo, source = projection_repo
    links, unexpected = mod._planned_links(source, "moonmind", repo_root=repo)
    targets = {item.target.relative_to(repo).as_posix() for item in links}

    assert ".agents/skills/moonspec-test/SKILL.md" in targets
    assert ".agents/skills/moonspec-test/agents/openai.yaml" in targets
    assert "api_service/data/presets/moonspec-orchestrate.yaml" in targets
    assert ".agents/skills/moonspec-old/SKILL.md" in unexpected


def test_write_creates_relative_file_level_symlinks_and_preserves_mixed_directory(
    projection_repo: tuple[Path, Path],
) -> None:
    repo, source = projection_repo
    links, unexpected = mod._planned_links(source, "moonmind", repo_root=repo)

    changed = mod._write_links(links, replace_generated=False)
    drift = mod._drift(links, unexpected, source / "bundle", prune=True)

    skill_link = repo / ".agents/skills/moonspec-test/SKILL.md"
    assert ".agents/skills/moonspec-test/SKILL.md" in changed
    assert skill_link.is_symlink()
    assert not Path(os.readlink(skill_link)).is_absolute()
    assert skill_link.resolve() == source / "bundle/skills/moonspec-test/SKILL.md"
    assert (repo / ".agents/skills/moonmind-owned/SKILL.md").is_file()
    assert drift == []


def test_check_detects_missing_non_symlink_and_wrong_target(
    projection_repo: tuple[Path, Path],
) -> None:
    repo, source = projection_repo
    links, _unexpected = mod._planned_links(source, "moonmind", repo_root=repo)
    target = repo / ".agents/skills/moonspec-test/SKILL.md"

    assert mod._link_status(links[0]) == "missing: .agents/skills/moonspec-test/SKILL.md"

    _write(target, "regular file\n")
    assert mod._link_status(links[0]) == "not a symlink: .agents/skills/moonspec-test/SKILL.md"

    target.unlink()
    target.symlink_to("../../wrong")
    assert mod._link_status(links[0]) == (
        "wrong target: .agents/skills/moonspec-test/SKILL.md -> ../../wrong"
    )


def test_refuses_source_and_target_path_escapes(
    projection_repo: tuple[Path, Path],
) -> None:
    repo, source = projection_repo
    projection = source / "bundle/projections/moonmind.yaml"

    projection.write_text(
        "schemaVersion: 1\nconsumer: moonmind\nmappings:\n"
        "  - from: ../outside.txt\n    to: .agents/outside.txt\n    mode: file\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="source escapes allowed root"):
        mod._planned_links(source, "moonmind", repo_root=repo)

    projection.write_text(
        "schemaVersion: 1\nconsumer: moonmind\nmappings:\n"
        "  - from: skills/moonspec-test/SKILL.md\n    to: ../outside.txt\n    mode: file\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="target escapes allowed root"):
        mod._planned_links(source, "moonmind", repo_root=repo)


def test_prunes_only_stale_moonspec_symlinks(
    projection_repo: tuple[Path, Path],
) -> None:
    repo, source = projection_repo
    links, unexpected = mod._planned_links(source, "moonmind", repo_root=repo)
    mod._write_links(links, replace_generated=False)
    stale_source = source / "bundle/skills/removed/SKILL.md"
    _write(stale_source, "# Removed\n")
    stale_link = repo / ".agents/skills/moonspec-removed/SKILL.md"
    stale_link.parent.mkdir(parents=True, exist_ok=True)
    stale_link.symlink_to(os.path.relpath(stale_source, stale_link.parent))
    external_source = repo / "local.txt"
    _write(external_source, "local\n")
    external_link = repo / ".agents/skills/moonspec-external/SKILL.md"
    external_link.parent.mkdir(parents=True, exist_ok=True)
    external_link.symlink_to(os.path.relpath(external_source, external_link.parent))

    stale = mod._prune_stale_symlinks(
        links,
        unexpected,
        (source / "bundle").resolve(strict=False),
        write=True,
    )

    assert ".agents/skills/moonspec-removed/SKILL.md" in stale
    assert not stale_link.exists()
    assert external_link.is_symlink()


def test_replaces_generated_files_only_when_requested(
    projection_repo: tuple[Path, Path],
) -> None:
    repo, source = projection_repo
    links, _unexpected = mod._planned_links(source, "moonmind", repo_root=repo)
    target = repo / ".agents/skills/moonspec-test/SKILL.md"
    _write(
        target,
        "<!-- Generated from moonspec/bundle/skills/moonspec-test/SKILL.md; edit MoonSpec repo instead. -->\n",
    )

    with pytest.raises(ValueError, match="--replace-generated"):
        mod._write_links(links, replace_generated=False)

    mod._write_links(links, replace_generated=True)
    assert target.is_symlink()
    assert target.resolve() == source / "bundle/skills/moonspec-test/SKILL.md"


def test_refuses_to_replace_directories(projection_repo: tuple[Path, Path]) -> None:
    repo, source = projection_repo
    links, _unexpected = mod._planned_links(source, "moonmind", repo_root=repo)
    target = repo / ".agents/skills/moonspec-test/SKILL.md"
    target.mkdir(parents=True)

    with pytest.raises(ValueError, match="refusing to replace directory"):
        mod._write_links(links, replace_generated=True)
