from unittest.mock import mock_open, patch

from moonmind.utils.read_text_file import read_text_file


def test_read_text_file_valid(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("Hello, World!", encoding="utf-8")

    content = read_text_file(str(file_path))

    assert content == "Hello, World!"


def test_read_text_file_empty_path():
    assert read_text_file(None) is None
    assert read_text_file("") is None


def test_read_text_file_not_found(tmp_path):
    file_path = tmp_path / "non_existent.txt"

    content = read_text_file(str(file_path))

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
