from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_cli() -> ModuleType:
    path = (
        Path(__file__).parents[3]
        / "services"
        / "omnigent"
        / "scripts"
        / "moonmind-container-cli.py"
    )
    spec = importlib.util.spec_from_file_location("moonmind_container_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_explicit_request_id_produces_stable_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _load_cli()
    monkeypatch.setenv("MOONMIND_AGENT_RUN_ID", "run-1")

    assert cli._idempotency_key("container-run", "replay-1") == (
        "container-run:run-1:replay-1"
    )
    assert cli._idempotency_key("container-run", "replay-1") == (
        "container-run:run-1:replay-1"
    )


def test_omitted_request_id_remains_unique(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = _load_cli()
    monkeypatch.setenv("MOONMIND_AGENT_RUN_ID", "run-1")

    assert cli._idempotency_key("container-run") != cli._idempotency_key(
        "container-run"
    )


def test_bearer_token_reads_omnigent_capability_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _load_cli()
    capability_file = tmp_path / "container-jobs"
    capability_file.write_text("scoped-file-token\n", encoding="utf-8")
    monkeypatch.delenv("MOONMIND_CONTAINER_JOBS_BEARER_TOKEN", raising=False)
    monkeypatch.setenv(
        "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN_FILE", str(capability_file)
    )
    monkeypatch.setenv(
        "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN", "stale-inline-token"
    )

    assert cli._container_jobs_bearer_token() == "scoped-file-token"


def test_bearer_token_file_selector_fails_closed_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _load_cli()
    monkeypatch.setenv(
        "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN_FILE", str(tmp_path / "missing")
    )
    monkeypatch.setenv(
        "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN", "must-not-be-used"
    )

    with pytest.raises(cli.CliError, match="BEARER_TOKEN_FILE is unavailable"):
        cli._container_jobs_bearer_token()


def test_log_reader_follows_cursors_and_keeps_terminal_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _load_cli()
    calls: list[dict[str, object]] = []
    pages = [
        {"entries": [{"text": "old-1"}, {"text": "old-2"}], "nextCursor": "c1"},
        {"entries": [{"message": "tail-1"}], "nextCursor": "c2"},
        {"entries": [{"text": "tail-2"}]},
    ]

    def _call(_tool: str, arguments: dict[str, object]) -> dict[str, object]:
        calls.append(arguments)
        return pages[len(calls) - 1]

    monkeypatch.setattr(cli, "_call", _call)

    assert cli._read_log_tail("job-1", max_lines=2) == ("tail-1", "tail-2")
    assert calls == [
        {"jobId": "job-1", "limit": 500},
        {"jobId": "job-1", "limit": 500, "cursor": "c1"},
        {"jobId": "job-1", "limit": 500, "cursor": "c2"},
    ]


def test_log_reader_rejects_repeated_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _load_cli()
    monkeypatch.setattr(
        cli,
        "_call",
        lambda _tool, _arguments: {"entries": [], "nextCursor": "repeat"},
    )

    with pytest.raises(cli.CliError, match="repeated cursor"):
        cli._read_log_tail("job-1")
