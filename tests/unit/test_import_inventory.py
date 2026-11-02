from pathlib import Path
from unittest.mock import patch

from tests.helpers import import_inventory


def test_cached_inventory_preserves_relative_imports_and_observes_edits(tmp_path: Path):
    source = tmp_path / "package" / "module.py"
    source.parent.mkdir()
    source.write_text("import os, sys\nfrom .child import value\n")
    with patch.object(
        import_inventory.ast, "parse", wraps=import_inventory.ast.parse
    ) as parse:
        assert import_inventory.imports(tmp_path, "package/module.py") == (
            "os",
            "sys",
            "package.child",
        )
        assert import_inventory.imports(tmp_path, "package/module.py") == (
            "os",
            "sys",
            "package.child",
        )
        assert parse.call_count == 1
        source.write_text("from tests.negative_fixture import forbidden\n")
        assert import_inventory.imports(tmp_path, "package/module.py") == (
            "tests.negative_fixture",
        )
        assert parse.call_count == 2
