"""Vector-free CLI assertions for MoonLadderStudios/MoonMind#4112."""

from __future__ import annotations


def test_top_level_cli_imports_without_schema_cycle():
    import moonmind.cli as moonmind_cli

    assert moonmind_cli.app is not None


def test_top_level_cli_has_no_vector_rag_subcommands():
    from typer.main import get_command

    import moonmind.cli as moonmind_cli

    command = get_command(moonmind_cli.app)
    assert "rag" not in (command.commands or {}), (
        "retired vector `rag` command group must not be registered"
    )


def test_rag_cli_helpers_expose_no_vector_entry_points():
    from moonmind.rag import cli as rag_cli

    for retired in (
        "run_search",
        "run_overlay_upsert",
        "run_overlay_clean",
        "run_sync_embedding",
    ):
        assert not hasattr(rag_cli, retired), (
            f"retired vector helper {retired} must be removed"
        )
    # Generic parsing helpers remain for non-vector surfaces.
    assert callable(rag_cli.parse_filters)
    assert callable(rag_cli.parse_budget_args)


def test_worker_doctor_is_vector_free():
    import inspect

    import moonmind.cli as moonmind_cli

    source = inspect.getsource(moonmind_cli.worker_doctor)
    assert "qdrant" not in source.lower()
    assert "ensure_rag_ready" not in source
    assert "RagRuntimeSettings" not in source
