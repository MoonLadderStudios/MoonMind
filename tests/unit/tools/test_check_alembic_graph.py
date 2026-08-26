from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parents[3] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import check_alembic_graph


def _script(*revisions: SimpleNamespace) -> SimpleNamespace:
    """Fake ScriptDirectory exposing the members ``main()`` actually reads."""

    by_id = {revision.revision: revision for revision in revisions}
    return SimpleNamespace(
        walk_revisions=lambda: tuple(revisions),
        get_heads=lambda: list(by_id),
        get_revision=by_id.get,
    )


def test_repository_migration_graph_has_exactly_one_head(capsys) -> None:
    assert check_alembic_graph.main() == 0

    assert capsys.readouterr().out.startswith(
        "Alembic migration graph has one head: "
    )


def test_main_accepts_exactly_one_head(capsys) -> None:
    script = _script(SimpleNamespace(revision="revision_1", doc=""))

    with patch.object(
        check_alembic_graph.ScriptDirectory,
        "from_config",
        return_value=script,
    ):
        assert check_alembic_graph.main() == 0

    assert capsys.readouterr().out == (
        "Alembic migration graph has one head: revision_1\n"
    )


def test_main_accepts_a_revision_id_at_the_storable_limit(capsys) -> None:
    revision_id = "r" * check_alembic_graph.VERSION_NUM_MAX_LENGTH
    script = _script(SimpleNamespace(revision=revision_id, doc=""))

    with patch.object(
        check_alembic_graph.ScriptDirectory,
        "from_config",
        return_value=script,
    ):
        assert check_alembic_graph.main() == 0

    assert capsys.readouterr().out == (
        f"Alembic migration graph has one head: {revision_id}\n"
    )


def test_main_rejects_a_revision_id_that_cannot_be_stored(capsys) -> None:
    oversized_id = "r" * (check_alembic_graph.VERSION_NUM_MAX_LENGTH + 1)
    script = _script(
        SimpleNamespace(revision=oversized_id, doc="Too long"),
        SimpleNamespace(revision="revision_1", doc="First migration"),
    )

    with patch.object(
        check_alembic_graph.ScriptDirectory,
        "from_config",
        return_value=script,
    ):
        assert check_alembic_graph.main() == 1

    output = capsys.readouterr().out
    assert (
        f"alembic_version.version_num holds "
        f"{check_alembic_graph.VERSION_NUM_MAX_LENGTH} characters"
    ) in output
    assert oversized_id in output
    assert "Rename the revision id" in output
    # The unstorable id is reported before the head count is even considered.
    assert "Expected exactly one head" not in output


def test_main_reports_all_heads_and_resolution(capsys) -> None:
    script = _script(
        SimpleNamespace(revision="revision_1", doc="First migration\nMore detail"),
        SimpleNamespace(revision="revision_2", doc="Second migration"),
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
