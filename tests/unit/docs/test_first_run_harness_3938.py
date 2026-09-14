"""Doc-parity contract for the #3938 first-run harness (REQ-07).

The README Quick Start stays the supported production journey; the new
``docs/FirstRunHarness.md`` binds the executable harness to that journey
without rewording it. These tests pin the linkage: same commands/URLs, no
universal time promise, no weakened-security troubleshooting, and live-tier
separation.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"
HARNESS = REPO_ROOT / "docs" / "FirstRunHarness.md"
COMPOSE = REPO_ROOT / "docker-compose.yaml"
LIVE_TOOL = REPO_ROOT / "tools" / "first_run_live_qualification.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_harness_doc_exists_and_names_quick_start_as_journey() -> None:
    assert HARNESS.exists()
    text = _read(HARNESS)
    assert "Quick Start" in text
    assert "FirstRunServiceInventory" in text
    assert "moonmind-test" in text
    assert "`moonmind` deployment project is never" in text


def test_harness_uses_the_same_commands_and_urls_as_quick_start() -> None:
    quick_start = _read(README)[_read(README).find("## Quick Start") :]
    harness = _read(HARNESS)
    for token in (
        "docker compose up -d",
        "http://localhost:7000",
        "/healthz",
        "/workflows/{workflowId}",
    ):
        assert token in quick_start, token
        assert token in harness, token


def test_harness_states_measured_budget_not_guarantee() -> None:
    harness = _read(HARNESS)
    assert "not a guarantee" in harness
    assert not re.search(
        r"in (under |less than )?(ten|10)[ -]minutes?", harness
    ), "harness must not promise a universal time-to-result"


def test_harness_troubleshooting_never_suggests_weakened_security() -> None:
    harness = _read(HARNESS)
    for phrase in (
        "disable authentication",
        "--no-verify",
    ):
        assert phrase not in harness, phrase
    # Global-prune commands may only appear as refused/never-executed actions.
    for phrase in ("docker volume prune", "down -v"):
        assert phrase in harness, phrase
        window = harness[max(0, harness.find(phrase) - 200) : harness.find(phrase)]
        assert "refus" in window.lower() or "never" in window.lower(), phrase


def test_live_tier_is_explicitly_separate_from_required_ci() -> None:
    harness = _read(HARNESS)
    assert "never gates a PR" in harness or "never part of required CI" in harness
    tool = _read(LIVE_TOOL)
    assert "--live" in tool
    assert "--check-only" in tool
    assert "integration_ci" not in tool


def test_harness_references_compose_boundary_services() -> None:
    harness = _read(HARNESS)
    compose = _read(COMPOSE)
    for service in ("api", "postgres", "temporal", "omnigent"):
        assert service in compose
        assert service in harness


def test_cli_parity_is_explicitly_owned_by_sibling_3939() -> None:
    """REQ-07: this harness claims README Quick Start parity only; CLI-example
    parity is recorded as owned by sibling #3939, not silently asserted."""
    harness = _read(HARNESS)
    assert "#3939" in harness
    assert "CLI" in harness
    assert "owns" in harness.lower() or "owned by #3939" in harness
