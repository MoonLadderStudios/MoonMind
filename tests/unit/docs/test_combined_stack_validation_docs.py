from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DOC = REPO_ROOT / "docs" / "Omnigent" / "CombinedStackValidationAndRollback.md"
README = REPO_ROOT / "README.md"


def _doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_combined_stack_doc_exists_with_mm_972_traceability() -> None:
    text = _doc_text()

    assert DOC.exists()
    assert "**Document Class:** Canonical declarative" in text
    assert "MM-972" in text
    assert "MM-968" in text


def test_combined_stack_doc_covers_required_operator_commands() -> None:
    text = _doc_text()

    for required in (
        "http://localhost:7000",
        "http://localhost:8000",
        "docker compose up -d",
        "docker compose up -d postgres",
        "docker compose up omnigent-db-init",
        "docker compose up -d api omnigent",
        "docker compose logs omnigent",
        "docker compose logs omnigent-host",
        "curl -fsS http://localhost:7000/healthz",
        "curl -fsS http://localhost:8000/health",
        "docker compose --profile omnigent-host up -d omnigent-host",
    ):
        assert required in text


def test_combined_stack_doc_separates_normal_rollback_from_destructive_cleanup() -> None:
    text = _doc_text()

    normal_rollback = text.index("## DOC-REQ-009 Normal Rollback")
    destructive_cleanup = text.index("## DOC-REQ-010 Optional Destructive Cleanup")
    assert normal_rollback < destructive_cleanup
    assert "Normal rollback preserves PostgreSQL, MoonMind, and Omnigent volumes." in text
    assert "optional, destructive, and separate from normal rollback" in text
    assert "docker compose down -v" in text


def test_combined_stack_doc_covers_required_troubleshooting_topics() -> None:
    text = _doc_text()

    for required in (
        "### GHCR Authentication",
        "### Existing PostgreSQL Volumes",
        "### Port Conflicts",
        "### Host Config Mount Conflicts",
        "### Built-In Accounts and OIDC",
        "Built-in accounts mode is the documented default",
        "OIDC is a future or operator-provided configuration path",
        "create the first admin account",
        "omnigent-data",
    ):
        assert required in text


def test_readme_links_to_combined_stack_guide_and_host_urls() -> None:
    text = README.read_text(encoding="utf-8")

    assert "http://localhost:7000" in text
    assert "http://localhost:8000" not in text
    assert "docs/Omnigent/CombinedStackValidationAndRollback.md" in text


def test_combined_stack_doc_documents_omnigent_host_codex_auth_volume() -> None:
    text = _doc_text()

    assert "## DOC-REQ-012 Omnigent Host Codex Subscription Auth" in text

    # omnigent-host mounts the MoonMind-managed Codex OAuth volume at the
    # container Codex home and pins CODEX_HOME to it.
    assert "codex_auth_volume:/root/.codex" in text
    assert "CODEX_HOME=/root/.codex" in text

    # Subscription auth must not depend on API-key or CODEX_ACCESS_TOKEN paths,
    # and the mount must stay read-write so token refreshes persist.
    assert "CODEX_ACCESS_TOKEN" in text
    assert "read-write" in text
    assert "read-only" in text
