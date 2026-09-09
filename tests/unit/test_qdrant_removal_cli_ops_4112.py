"""Vector CLI/diagnostics/maintenance removal tests (MoonLadderStudios/MoonMind#4112)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UPDATE_SCRIPT = (
    ROOT / ".agents" / "skills" / "update-moonmind" / "scripts" / "run-update-moonmind.sh"
)


def _code_without_comments(source: str) -> str:
    """Strip full-line and inline `#` comments for behavioral assertions."""
    kept: list[str] = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        kept.append(line.split("#", 1)[0])
    return "\n".join(kept)


def test_guardrails_module_has_no_vector_backend_import():
    source = (ROOT / "moonmind" / "rag" / "guardrails.py").read_text(encoding="utf-8")
    code = _code_without_comments(source)
    assert "RagQdrantClient" not in code
    assert "qdrant_client" not in code.lower()
    assert "ensure_collection_ready" not in code


def test_guardrails_vector_free_noop_without_network(monkeypatch):
    from moonmind.rag.guardrails import ensure_rag_ready
    from moonmind.rag.settings import RagRuntimeSettings

    settings = RagRuntimeSettings.from_env(
        {
            "RAG_ENABLED": "true",
            "QDRANT_ENABLED": "false",
            "DEFAULT_EMBEDDING_PROVIDER": "google",
            "GOOGLE_API_KEY": "test-key",
        }
    )
    import httpx

    called = []

    def _fail(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("vector-free path must not perform network access")

    monkeypatch.setattr(httpx, "get", _fail)
    ensure_rag_ready(settings)
    assert called == []


def test_retired_vector_cli_files_removed():
    assert not (ROOT / "tools" / "get-qdrant.py").exists()
    assert not (ROOT / "moonmind" / "rag" / "overlay_cleanup.py").exists()


def test_ops_diagnostics_service_inventory_has_no_qdrant():
    from moonmind.workflows.skills.ops_diagnostics_execution import (
        DEFAULT_MOONMIND_SERVICES,
    )

    assert "qdrant" not in set(DEFAULT_MOONMIND_SERVICES)


def test_ops_diagnostics_rejects_retired_qdrant_service():
    from moonmind.workflows.skills.ops_diagnostics_execution import _parse_services
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure

    try:
        _parse_services(["api", "qdrant"])
    except ToolFailure as exc:
        assert "qdrant" in str(exc.message).lower() or "qdrant" in str(
            exc.details
        ).lower()
    else:
        raise AssertionError("retired qdrant service must be rejected")


def test_update_moonmind_script_has_no_qdrant_baseline():
    script = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "qdrant" not in script.lower()


def test_update_and_diagnostics_are_non_destructive():
    script = UPDATE_SCRIPT.read_text(encoding="utf-8")
    lowered = script.lower()
    assert "docker compose down -v" not in lowered
    assert "volume prune" not in lowered
    # The updater must not pass --remove-orphans on its own compose up calls.
    assert "--remove-orphans" not in script

    ops_source = (
        ROOT
        / "moonmind"
        / "workflows"
        / "skills"
        / "ops_diagnostics_execution.py"
    ).read_text(encoding="utf-8")
    assert "down -v" not in ops_source
    assert "volume prune" not in ops_source


def test_diagnostics_redact_vector_credentials():
    from moonmind.utils.logging import redact_sensitive_payload

    payload = {
        "environment": {"QDRANT_API_KEY": "super-secret-value"},
        "logs": "QDRANT_API_KEY=super-secret-value ready",
    }
    redacted = redact_sensitive_payload(payload)
    assert "super-secret-value" not in str(redacted)
