"""Tests for flattened git-symlink pointer detection."""

import subprocess

from moonmind.workflows.skills.pointer_files import (
    FLATTENED_SYMLINK_MAX_BYTES,
    flattened_symlink_target,
    resolve_flattened_skill_symlink,
)


def _record_git_symlink(repo: str, path: str, target: str) -> None:
    subprocess.run(["git", "-C", repo, "init"], check=True, capture_output=True)
    blob = subprocess.run(
        ["git", "-C", repo, "hash-object", "-w", "--stdin"],
        input=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "-C",
            repo,
            "update-index",
            "--add",
            "--cacheinfo",
            "120000",
            blob,
            path,
        ],
        check=True,
        capture_output=True,
    )


def test_detects_relative_pointer_content(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text(
        "../../../moonspec/bundle/skills/moonspec-verify/SKILL.md",
        encoding="utf-8",
    )
    assert (
        flattened_symlink_target(path)
        == "../../../moonspec/bundle/skills/moonspec-verify/SKILL.md"
    )


def test_detects_pointer_with_trailing_newline(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("./sibling/SKILL.md\r\n", encoding="utf-8")
    assert flattened_symlink_target(path) == "./sibling/SKILL.md"


def test_ignores_real_skill_content(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\nname: moonspec-verify\ndescription: test\n---\n# Body\n",
        encoding="utf-8",
    )
    assert flattened_symlink_target(path) is None


def test_ignores_multiline_content_starting_with_dotdot(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("../first\n../second\n", encoding="utf-8")
    assert flattened_symlink_target(path) is None


def test_ignores_actual_symlinks(tmp_path):
    target = tmp_path / "real.md"
    target.write_text("content", encoding="utf-8")
    link = tmp_path / "SKILL.md"
    link.symlink_to("real.md")
    assert flattened_symlink_target(link) is None


def test_ignores_missing_and_oversized_files(tmp_path):
    assert flattened_symlink_target(tmp_path / "absent.md") is None
    big = tmp_path / "big.md"
    big.write_text("../" + "a" * FLATTENED_SYMLINK_MAX_BYTES, encoding="utf-8")
    assert flattened_symlink_target(big) is None


def test_resolves_git_verified_flattened_symlink_target(tmp_path):
    repo = tmp_path / "repo"
    skill_dir = repo / ".agents" / "skills" / "external"
    target = "../../../outside/real.md"
    skill_dir.mkdir(parents=True)
    path = skill_dir / "SKILL.md"
    path.write_text(f"{target}\r\n", encoding="utf-8")
    _record_git_symlink(str(repo), ".agents/skills/external/SKILL.md", target)

    resolved = resolve_flattened_skill_symlink(path, skill_dir=skill_dir)

    assert resolved is not None
    assert resolved.target == target
    assert resolved.target_path == (path.parent / target).resolve(strict=False)


def test_rejects_untrusted_pointer_outside_expected_skill_bundle(tmp_path):
    repo = tmp_path / "repo"
    skill_dir = repo / ".agents" / "skills" / "evil"
    skill_dir.mkdir(parents=True)
    path = skill_dir / "SKILL.md"
    path.write_text("../../../var/secrets/key", encoding="utf-8")

    assert resolve_flattened_skill_symlink(path, skill_dir=skill_dir) is None
