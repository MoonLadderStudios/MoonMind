from moonmind.utils.insert_text_file import insert_text_file

def test_insert_beginning(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("line1\nline2\n")

    success = insert_text_file(file_path, "inserted\n", 1)

    assert success
    assert file_path.read_text() == "inserted\nline1\nline2\n"

def test_insert_middle(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("line1\nline2\n")

    success = insert_text_file(file_path, "inserted\n", 2)

    assert success
    assert file_path.read_text() == "line1\ninserted\nline2\n"

def test_insert_end(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("line1\nline2\n")

    # line_number 3 means after line 2
    success = insert_text_file(file_path, "inserted\n", 3)

    assert success
    assert file_path.read_text() == "line1\nline2\ninserted\n"

def test_insert_clamp_beginning(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("line1\n")

    success = insert_text_file(file_path, "inserted\n", 0)

    assert success
    assert file_path.read_text() == "inserted\nline1\n"

def test_insert_clamp_end(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("line1\n")

    success = insert_text_file(file_path, "inserted\n", 10)

    assert success
    assert file_path.read_text() == "line1\ninserted\n"

def test_insert_with_blank_lines(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("line1\n")

    success = insert_text_file(
        file_path,
        "inserted",
        2,
        blank_lines_before=1,
        blank_lines_after=2,
    )

    assert success
    # line1\n + \n (before) + inserted\n (text) + \n\n (after)
    assert file_path.read_text() == "line1\n\ninserted\n\n\n"

def test_insert_ensures_newline(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("line1\n")

    success = insert_text_file(file_path, "inserted_no_newline", 1)

    assert success
    assert file_path.read_text() == "inserted_no_newline\nline1\n"

def test_insert_empty_text_only_blank_lines(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("line1\nline2\n")

    success = insert_text_file(file_path, "", 2, blank_lines_before=1)

    assert success
    assert file_path.read_text() == "line1\n\nline2\n"

def test_insert_file_not_found(tmp_path):
    file_path = tmp_path / "non_existent.txt"

    success = insert_text_file(file_path, "text", 1)

    assert not success

def test_insert_not_a_file(tmp_path):
    directory = tmp_path / "subdir"
    directory.mkdir()

    success = insert_text_file(directory, "text", 1)

    assert not success

def test_insert_no_path():
    success = insert_text_file("", "text", 1)
    assert not success

def test_insert_rejects_parent_traversal(tmp_path):
    unsafe_path = tmp_path / ".." / "malicious.txt"

    success = insert_text_file(unsafe_path, "text", 1)

    assert not success
