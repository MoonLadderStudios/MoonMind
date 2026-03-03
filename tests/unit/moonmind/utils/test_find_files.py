import os
import pytest
from moonmind.utils.find_files import find_files

def test_find_files_basic(tmp_path):
    (tmp_path / "file1.txt").write_text("hello")
    (tmp_path / "file2.txt").write_text("world")

    files = list(find_files(str(tmp_path), ".txt"))
    assert len(files) == 2
    assert str(tmp_path / "file1.txt") in files
    assert str(tmp_path / "file2.txt") in files

def test_find_files_without_dot(tmp_path):
    (tmp_path / "file1.txt").write_text("hello")

    files = list(find_files(str(tmp_path), "txt"))
    assert len(files) == 1
    assert str(tmp_path / "file1.txt") in files

def test_find_files_case_insensitivity(tmp_path):
    (tmp_path / "file1.TXT").write_text("hello")
    (tmp_path / "file2.Txt").write_text("world")

    files = list(find_files(str(tmp_path), ".txT"))
    assert len(files) == 2
    assert str(tmp_path / "file1.TXT") in files
    assert str(tmp_path / "file2.Txt") in files

def test_find_files_subdirectories(tmp_path):
    (tmp_path / "file1.txt").write_text("hello")
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "file2.txt").write_text("world")

    files = list(find_files(str(tmp_path), ".txt"))
    assert len(files) == 2
    assert str(tmp_path / "file1.txt") in files
    assert str(subdir / "file2.txt") in files

def test_find_files_ignores_other_extensions(tmp_path):
    (tmp_path / "file1.txt").write_text("hello")
    (tmp_path / "file2.md").write_text("world")

    files = list(find_files(str(tmp_path), ".txt"))
    assert len(files) == 1
    assert str(tmp_path / "file1.txt") in files

def test_find_files_no_matching_files(tmp_path):
    (tmp_path / "file1.md").write_text("hello")

    files = list(find_files(str(tmp_path), ".txt"))
    assert len(files) == 0

def test_find_files_symlink_dir_not_followed(tmp_path):
    # Create a directory with a txt file
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "file1.txt").write_text("hello")

    # Create another directory, which will be the search dir
    search_dir = tmp_path / "search"
    search_dir.mkdir()
    (search_dir / "file2.txt").write_text("world")

    # Create a symlink in search_dir pointing to target_dir
    symlink_path = search_dir / "symlinked_dir"
    os.symlink(str(target_dir), str(symlink_path), target_is_directory=True)

    files = list(find_files(str(search_dir), ".txt"))
    # Should only find file2.txt in search_dir, not file1.txt in the symlinked dir
    assert len(files) == 1
    assert str(search_dir / "file2.txt") in files

def test_find_files_not_found(tmp_path):
    non_existent_dir = tmp_path / "non_existent"

    with pytest.raises(FileNotFoundError, match="does not exist or is not a directory"):
        list(find_files(str(non_existent_dir), ".txt"))

def test_find_files_not_a_directory(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("hello")

    with pytest.raises(FileNotFoundError, match="does not exist or is not a directory"):
        list(find_files(str(file_path), ".txt"))
