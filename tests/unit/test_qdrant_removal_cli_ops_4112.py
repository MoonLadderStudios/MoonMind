"""Qdrant CLI/diagnostics/maintenance retirement tests (#4112).

Covers the portable scripts/CLI/ops surface owned by
MoonLadderStudios/MoonMind#4112:

- no shipped CLI registration advertises vector search/indexing/overlay,
- doctor/ops outputs attempt no vector network access,
- deployment updater never recreates or demands the missing service,
- diagnostics and upgrades stay non-destructive and leak no secrets.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from typer.main import get_command
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[3]
UPDATE_SCRIPT = (
    ROOT / ".agents" / "skills" / "update-moonmind" / "scripts"
    / "run-update-moonmind.sh"
)


def test_moonmind_help_advertises_no_vector_operations() -> None:
    import moonmind.cli as moonmind_cli

    runner = CliRunner()
    result = runner.invoke(moonmind_cli.app, ["--help"])
    assert result.exit_code == 0, result.output
    lowered = result.output.lower()
    for token in ("qdrant", "overlay", "vector search", "vector retrieval"):
        assert token not in lowered, token
    # Unrelated commands survive.
    assert "worker" in lowered
    assert "manifest" in lowered
    assert "container" in lowered


def test_moonmind_help_runs_without_qdrant_package(monkeypatch) -> None:
    """Help must not require the retired Qdrant SDK at import or render."""

    import sys

    monkeypatch.setitem(sys.modules, "qdrant_client", None)
    import moonmind.cli as moonmind_cli

    runner = CliRunner()
    result = runner.invoke(moonmind_cli.app, ["--help"])
    assert result.exit_code == 0, result.output
    worker_result = runner.invoke(moonmind_cli.app, ["worker", "--help"])
    assert worker_result.exit_code == 0, worker_result.output


def test_no_rag_subcommand_registered() -> None:
    import moonmind.cli as moonmind_cli

    command = get_command(moonmind_cli.app)
    names = set((command.commands or {}).keys())
    assert "rag" not in names


def test_vector_admin_utility_deleted() -> None:
    assert not (ROOT / "tools" / "get-qdrant.py").exists()


def test_overlay_maintenance_module_deleted() -> None:
    assert not (ROOT / "moonmind" / "rag" / "overlay_cleanup.py").exists()


def test_guardrail_module_performs_no_vector_imports_or_network() -> None:
    source = (ROOT / "moonmind" / "rag" / "guardrails.py").read_text(
        encoding="utf-8"
    )
    lowered = source.lower()
    assert "ragqdrantclient" not in lowered
    assert "qdrant_client" not in lowered
    assert "ensure_collection_ready" not in lowered
    assert "httpx" not in lowered
    assert '"/health"' not in source
    # No stale secret-bearing fields are plumbed through the boundary.
    assert "QDRANT_API_KEY" not in source
    assert "qdrant_api_key" not in lowered


def test_worker_preflight_wires_no_vector_probe() -> None:
    source = (ROOT / "moonmind" / "agents" / "codex_worker" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert "ensure_rag_ready" not in source
    assert "from moonmind.rag.guardrails" not in source
    assert "from moonmind.rag.settings" not in source


def test_worker_capability_metadata_never_advertises_vector_search() -> None:
    from moonmind.agents.codex_worker.worker import CodexWorker

    metadata = CodexWorker._rag_capability_metadata()
    assert isinstance(metadata, dict)
    assert "ragCommand" not in metadata
    assert metadata.get("ragAvailable") is False


def test_ops_diagnostics_default_services_exclude_qdrant() -> None:
    from moonmind.workflows.skills.ops_diagnostics_execution import (
        DEFAULT_MOONMIND_SERVICES,
    )

    assert "qdrant" not in set(DEFAULT_MOONMIND_SERVICES)
    # Unrelated real services remain detectable.
    for service in ("api", "postgres", "minio"):
        assert service in set(DEFAULT_MOONMIND_SERVICES)


def test_update_script_has_no_qdrant_inventory_or_destructive_cleanup() -> None:
    script = UPDATE_SCRIPT.read_text(encoding="utf-8")
    # Exact-token match: comments may name the retired backend for operator
    # guidance, but no service inventory entry may recreate or demand it.
    assert '"$service" == "qdrant"' not in script
    assert "| qdrant |" not in script
    assert " qdrant " not in script.replace("\n", " ").replace("|", " ")
    for destructive in (
        "down -v",
        "down --volumes",
        "--remove-orphans",
        "volume prune",
        "volume rm",
        "rm -rf /",
    ):
        assert destructive not in script, destructive


def test_update_script_emits_no_raw_secret_names() -> None:
    """Upgrade warnings must not print raw secret values or assignments."""

    script = UPDATE_SCRIPT.read_text(encoding="utf-8")
    for token in ("QDRANT_API_KEY", "VECTOR_STORE", "QDRANT_URL"):
        assert token not in script, token


def test_no_skill_instruction_advertises_retired_vector_search() -> None:
    import re

    # Fail on active invocations (command with flags/continuations), not on
    # retirement notes that name the removed command to forbid it.
    invocation = re.compile(r"moonmind rag search\s*(?:\\|--)")
    skills_root = ROOT / ".agents" / "skills"
    offenders: list[str] = []
    for path in skills_root.rglob("SKILL.md"):
        text = path.read_text(encoding="utf-8")
        if invocation.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_ops_diagnosis_redacts_secret_bearing_payloads() -> None:
    from moonmind.workflows.skills.ops_diagnostics_execution import (
        _redact_ops_diagnosis_payload,
    )

    payload = {
        "services": ["api"],
        "note": "token ghp_abcdefghijklmnop123456 here",
        "nested": {"qdrant_api_key": "super-secret-value"},
    }
    redacted = _redact_ops_diagnosis_payload(payload)
    rendered = str(redacted)
    assert "ghp_abcdefghijklmnop123456" not in rendered
    assert "super-secret-value" not in rendered
