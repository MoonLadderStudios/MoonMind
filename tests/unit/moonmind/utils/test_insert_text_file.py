
from moonmind.utils.insert_text_file import insert_text_file


def test_insert_beginning(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\n", encoding="utf-8")

    success = insert_text_file(str(f), "inserted\n", 1)

    assert success is True
    assert f.read_text(encoding="utf-8") == "inserted\nline1\nline2\n"


def test_insert_middle(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\n", encoding="utf-8")

    success = insert_text_file(str(f), "inserted\n", 2)

    assert success is True
    assert f.read_text(encoding="utf-8") == "line1\ninserted\nline2\n"


def test_insert_end(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\n", encoding="utf-8")

    # line_number 3 means after line 2
    success = insert_text_file(str(f), "inserted\n", 3)

    assert success is True
    assert f.read_text(encoding="utf-8") == "line1\nline2\ninserted\n"


def test_insert_clamp_beginning(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\n", encoding="utf-8")

    success = insert_text_file(str(f), "inserted\n", 0)

    assert success is True
    assert f.read_text(encoding="utf-8") == "inserted\nline1\n"


def test_insert_clamp_end(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\n", encoding="utf-8")

    success = insert_text_file(str(f), "inserted\n", 10)

    assert success is True
    assert f.read_text(encoding="utf-8") == "line1\ninserted\n"


def test_insert_with_blank_lines(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\n", encoding="utf-8")

    success = insert_text_file(
        str(f), "inserted", 2, blank_lines_before=1, blank_lines_after=2
    )

    assert success is True
    # line1\n + \n (before) + inserted\n (text) + \n\n (after)
    assert f.read_text(encoding="utf-8") == "line1\n\ninserted\n\n\n"


def test_insert_ensures_newline(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\n", encoding="utf-8")

    success = insert_text_file(str(f), "inserted_no_newline", 1)

    assert success is True
    assert f.read_text(encoding="utf-8") == "inserted_no_newline\nline1\n"


def test_insert_empty_text_only_blank_lines(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\n", encoding="utf-8")

    success = insert_text_file(str(f), "", 2, blank_lines_before=1)

    assert success is True
    assert f.read_text(encoding="utf-8") == "line1\n\nline2\n"


def test_insert_file_not_found(tmp_path):
    f = tmp_path / "non_existent.txt"

    success = insert_text_file(str(f), "text", 1)

    assert success is False


def test_insert_not_a_file(tmp_path):
    d = tmp_path / "subdir"
    d.mkdir()

    success = insert_text_file(str(d), "text", 1)

    assert success is False


def test_insert_no_path():
    success = insert_text_file("", "text", 1)
    assert success is False
