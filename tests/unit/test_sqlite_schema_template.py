import hashlib
import shutil
import sqlite3


def test_template_is_closed_empty_and_copies_cannot_leak_data(
    sqlite_schema_template, tmp_path
):
    before = hashlib.sha256(sqlite_schema_template.read_bytes()).digest()
    first, second = tmp_path / "first.db", tmp_path / "second.db"
    for path in (first, second):
        shutil.copyfile(sqlite_schema_template, path)
    with sqlite3.connect(first) as connection:
        connection.execute("CREATE TABLE isolation_witness (value TEXT)")
        connection.execute("INSERT INTO isolation_witness VALUES ('first only')")
    with sqlite3.connect(second) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='isolation_witness'"
        ).fetchone() == (0,)
    assert hashlib.sha256(sqlite_schema_template.read_bytes()).digest() == before
    assert not sqlite_schema_template.with_name("empty.db-wal").exists()
