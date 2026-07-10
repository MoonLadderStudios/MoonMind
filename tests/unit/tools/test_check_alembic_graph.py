from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[3] / "tools" / "check_alembic_graph.py"
SPEC = importlib.util.spec_from_file_location("check_alembic_graph", SCRIPT_PATH)
assert SPEC and SPEC.loader
check_alembic_graph = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_alembic_graph)


def test_main_accepts_exactly_one_head(capsys) -> None:
    script = SimpleNamespace(get_heads=lambda: ["revision_1"])

    with patch.object(
        check_alembic_graph.ScriptDirectory,
        "from_config",
        return_value=script,
    ):
        assert check_alembic_graph.main() == 0

    assert capsys.readouterr().out == (
        "Alembic migration graph has one head: revision_1\n"
    )


def test_main_reports_all_heads_and_resolution(capsys) -> None:
    revisions = {
        "revision_1": SimpleNamespace(doc="First migration\nMore detail"),
        "revision_2": SimpleNamespace(doc="Second migration"),
    }
    script = SimpleNamespace(
        get_heads=lambda: list(revisions),
        get_revision=revisions.get,
    )

    with patch.object(
        check_alembic_graph.ScriptDirectory,
        "from_config",
        return_value=script,
    ):
        assert check_alembic_graph.main() == 1

    output = capsys.readouterr().out
    assert "Expected exactly one head, found 2" in output
    assert "revision_1: First migration" in output
    assert "revision_2: Second migration" in output
    assert "create an Alembic merge revision" in output
