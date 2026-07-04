"""Tests for flattened git-symlink pointer detection."""

from moonmind.workflows.skills.pointer_files import (
    FLATTENED_SYMLINK_MAX_BYTES,
    flattened_symlink_target,
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
    path.write_text("./sibling/SKILL.md\n", encoding="utf-8")
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
