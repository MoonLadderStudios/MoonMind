"""Factual-contract review tests for README claims (MoonMind#3968).

These tests pin the editorial contracts — qualified boundaries, opt-in/default
limits, current-vs-planned distinctions, and valid canonical links — rather
than incidental marketing wording.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"


def _text() -> str:
    return README.read_text(encoding="utf-8")


def test_no_unqualified_absolute_security_claims() -> None:
    text = _text()
    assert "boundaries the agent can't cross" not in text
    assert "automatically redacted from logs, artifacts, and outbound text" not in text


def test_no_unqualified_absolute_recovery_claims() -> None:
    text = _text()
    assert "Completed work is never re-bought" not in text
    assert "does not produce duplicates" not in text
    assert "close your laptop, and let MoonMind handle the rest" not in text
    assert "close my laptop and trust the workflow to continue" not in text
    assert "survive container crashes, worker restarts, and host reboots" not in text


def test_no_unqualified_universal_observability_claims() -> None:
    text = _text()
    assert "Every log line carries correlation IDs" not in text
    assert "Exact runtime provenance" not in text


def test_direct_paths_not_misclassified_as_replay_only() -> None:
    text = _text()
    assert "Managed compatibility paths" not in text
    assert "replay, rollback, migration, and historical-read compatibility" not in text
    assert "legacy_retirement.py" in text


def test_supported_first_path_precedes_runtime_direction() -> None:
    text = _text()
    first_path = text.index("Supported first path")
    runtime_direction = text.index("## Runtime direction")
    assert first_path < runtime_direction
    assert "docker compose up -d" in text[:runtime_direction]


def test_high_security_scan_limits_are_stated() -> None:
    text = _text()
    assert "default off" in text
    assert "MOONMIND_HIGH_SECURITY_MODE" in text
    assert "Binary attachments, terminal input, and browser automation" in text


def test_secret_memory_limit_is_stated() -> None:
    text = _text()
    assert "SecretRef" in text
    assert "process memory" in text


def test_host_suspend_distinction_is_stated() -> None:
    text = _text()
    assert "suspend" in text.lower()
    assert "another device" in text


def test_checkpoint_capability_gating_is_stated() -> None:
    text = _text()
    assert "codex_cli" in text
    assert "Checkpoint Resume Promotion" in text


def test_external_effects_promise_no_exactly_once() -> None:
    text = _text()
    assert "exactly-once" in text
    assert "reconcile" in text.lower()


def test_planned_governance_report_is_distinguished() -> None:
    text = _text()
    assert "#3969" in text
    assert "planned" in text.lower()


def test_canonical_owner_links_resolve() -> None:
    text = _text()
    targets = re.findall(r"\]\((docs/[^)]+)\)", text)
    assert targets, "README should link canonical owner docs"
    for target in targets:
        assert (REPO_ROOT / target).exists(), f"README links missing doc: {target}"


def test_rollout_and_cutover_authorities_are_linked() -> None:
    text = _text()
    assert "docs/Omnigent/RuntimeProviderRollout.md" in text
    assert "docs/Omnigent/CodexSupportAndCutover.md" in text
    assert "docs/Security/SecretsSystem.md" in text
    assert "docs/Security/RestrictedEgress.md" in text
    assert "docs/Temporal/WorkflowTypeCatalogAndLifecycle.md" in text
