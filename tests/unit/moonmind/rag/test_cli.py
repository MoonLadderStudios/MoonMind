"""Vector CLI retirement tests (MoonLadderStudios/MoonMind#4112)."""

from moonmind.rag import cli as rag_cli


def test_vector_entry_points_removed():
    """Retired vector helpers are gone; no no-op/alias/deprecation wrapper."""

    for name in (
        "run_search",
        "run_overlay_upsert",
        "run_overlay_clean",
        "run_sync_embedding",
    ):
        assert not hasattr(rag_cli, name), name


def test_top_level_cli_imports_without_schema_cycle():
    import moonmind.cli as moonmind_cli

    assert moonmind_cli.app is not None


def test_top_level_cli_has_no_rag_subcommand():
    from typer.main import get_command

    import moonmind.cli as moonmind_cli

    command = get_command(moonmind_cli.app)
    assert "rag" not in (command.commands or {})
    assert "worker" in (command.commands or {})
    assert "manifest" in (command.commands or {})
    assert "container" in (command.commands or {})


def test_worker_doctor_has_no_vector_help_or_options():
    from typer.main import get_command

    import moonmind.cli as moonmind_cli

    command = get_command(moonmind_cli.app)
    worker = command.commands["worker"]
    assert worker.commands is not None
    assert "doctor" in worker.commands
    doctor = worker.commands["doctor"]
    help_text = str(getattr(doctor, "help", "") or "").lower()
    assert "rag" not in help_text
    assert "qdrant" not in help_text
    assert "vector" not in help_text
    assert "overlay" not in help_text
