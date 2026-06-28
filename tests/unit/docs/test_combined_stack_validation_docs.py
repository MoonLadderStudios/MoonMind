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
        "http://localhost:8000",
        "http://localhost:7000",
        "docker compose up -d",
        "docker compose up -d postgres",
        "docker compose up init-db",
        "docker compose up -d api",
        "docker compose logs api",
        "docker compose logs init-db",
        "curl -fsS http://localhost:8000/healthz",
        "curl -fsS http://localhost:7000/health",
        "docker compose --profile <omnigent-host-profile> up -d <omnigent-host-service>",
    ):
        assert required in text

    for absent_service in ("omnigent-db-init", "docker compose up -d api omnigent"):
        assert absent_service not in text


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
        "Built-in accounts mode is the expected default",
        "OIDC is a future or operator-provided configuration path",
        "/data/admin-credentials",
        "omnigent-data",
    ):
        assert required in text


def test_readme_links_to_combined_stack_guide_and_host_urls() -> None:
    text = README.read_text(encoding="utf-8")

    assert "http://localhost:8000" in text
    assert "http://localhost:7000" not in text
    assert "docs/Omnigent/CombinedStackValidationAndRollback.md" in text
