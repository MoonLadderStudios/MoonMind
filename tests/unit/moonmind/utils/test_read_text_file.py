from unittest.mock import mock_open, patch

import pytest

from moonmind.utils.read_text_file import read_text_file


def test_read_text_file_valid(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("Hello, World!", encoding="utf-8")

    content = read_text_file(str(file_path))

    assert content == "Hello, World!"


@pytest.mark.parametrize("path", [None, ""])
def test_read_text_file_empty_path(path):
    assert read_text_file(path) is None


def test_read_text_file_not_found(tmp_path):
    file_path = tmp_path / "non_existent.txt"

    content = read_text_file(str(file_path))

    assert content is None


def test_read_text_file_path_traversal_blocked(tmp_path):
    base_dir = tmp_path / "safe_dir"
    base_dir.mkdir()

    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside content", encoding="utf-8")

    # Attempting to read outside the base_dir
    content = read_text_file(str(outside_file), safe_base_dir=str(base_dir))

    assert content is None


def test_read_text_file_path_traversal_with_dot_dot_blocked(tmp_path):
    base_dir = tmp_path / "safe_dir"
    base_dir.mkdir()

    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside content", encoding="utf-8")

    # Constructing a path using .. to break out
    traversal_path = base_dir / ".." / "outside.txt"

    content = read_text_file(str(traversal_path), safe_base_dir=str(base_dir))

    assert content is None


def test_read_text_file_default_path_traversal_blocked(tmp_path):
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside content", encoding="utf-8")

    # Attempting to read outside via .. without safe_base_dir
    traversal_path = tmp_path / "safe_dir" / ".." / "outside.txt"
    content = read_text_file(str(traversal_path))
    assert content is None


def test_read_text_file_within_safe_dir(tmp_path):
    base_dir = tmp_path / "safe_dir"
    base_dir.mkdir()

    inside_file = base_dir / "public.txt"
    inside_file.write_text("public content", encoding="utf-8")

    content = read_text_file(str(inside_file), safe_base_dir=str(base_dir))

    assert content == "public content"


def test_read_text_file_symlink_traversal_blocked(tmp_path):
    base_dir = tmp_path / "safe_dir"
    base_dir.mkdir()

    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside content", encoding="utf-8")

    symlink_in_safe_dir = base_dir / "link_to_secret"
    try:
        symlink_in_safe_dir.symlink_to("../outside.txt")
    except (NotImplementedError, OSError):
        pytest.skip("Symlink creation is not supported in this environment")

    assert symlink_in_safe_dir.resolve() == outside_file

    content = read_text_file(str(symlink_in_safe_dir), safe_base_dir=str(base_dir))

    assert content is None


def test_read_text_file_is_directory(tmp_path):
    directory = tmp_path / "subdir"
    directory.mkdir()

    content = read_text_file(str(directory))

    assert content is None


@patch("builtins.open", new_callable=mock_open)
def test_read_text_file_exception(mock_file, tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("dummy")  # Create it so exists and isfile pass

    # Mock open to raise an exception
    mock_file.side_effect = PermissionError("Permission denied")

    content = read_text_file(str(file_path))

    assert content is None
