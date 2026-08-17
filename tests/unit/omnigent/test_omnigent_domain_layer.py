"""Characterization + purity tests for the Omnigent domain layer.

Covers MoonLadderStudios/MoonMind#3711: the domain layer is the single source of
truth for status/failure vocabulary, is free of infrastructure imports, and the
legacy modules delegate to it without behavior change.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from moonmind.omnigent.domain import (
    compatibility,
    failures,
    observations,
    session_state,
)
from moonmind.omnigent.domain.session_state import coalesce_session_status
from moonmind.omnigent.domain.transitions import can_transition, next_status

REPO_ROOT = Path(__file__).resolve().parents[3]
DOMAIN_DIR = REPO_ROOT / "moonmind" / "omnigent" / "domain"

_FORBIDDEN_IN_DOMAIN = {
    "sqlalchemy",
    "fastapi",
    "starlette",
    "temporalio",
    "httpx",
    "aiohttp",
    "requests",
    "docker",
    "opentelemetry",
    "subprocess",
}


@pytest.mark.parametrize("path", sorted(DOMAIN_DIR.glob("*.py")), ids=lambda p: p.name)
def test_domain_modules_have_no_infrastructure_imports(path: Path) -> None:
    tree = ast.parse(path.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    leaked = imported & _FORBIDDEN_IN_DOMAIN
    assert not leaked, f"{path.name} imports forbidden infrastructure: {leaked}"


def test_domain_does_not_read_environment() -> None:
    for path in DOMAIN_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
                assert not (
                    isinstance(node.value, ast.Name) and node.value.id == "os"
                ), f"{path.name} reads environment variables"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("running", "active"),
        ("waiting", "active"),
        ("awaiting_approval", "active"),
        ("intervention_requested", "active"),
        ("declared", "declared"),
        ("creating", "creating"),
        ("active", "active"),
        ("completed", "completed"),
        ("failed", "failed"),
        ("canceled", "canceled"),
        ("timed_out", "timed_out"),
        ("cancelled", "canceled"),
        ("timeout", "timed_out"),
    ],
)
def test_coalesce_session_status(value: str, expected: str) -> None:
    assert coalesce_session_status(value) == expected


def test_coalesce_rejects_unknown_status() -> None:
    with pytest.raises(ValueError):
        coalesce_session_status("nonsense")


def test_timed_out_is_never_collapsed_into_failed() -> None:
    assert failures.failure_class_for_terminal_status("timed_out") == "system_error"
    assert failures.failure_class_for_terminal_status("failed") == "execution_error"
    assert failures.failure_class_for_terminal_status(
        "timed_out"
    ) != failures.failure_class_for_terminal_status("failed")


def test_event_type_status_mapping_and_fallthrough() -> None:
    assert observations.normalized_status_for_event_type("response.completed") == (
        True,
        "completed",
    )
    assert observations.normalized_status_for_event_type("stream.done") == (True, None)
    # Recognized but not status-bearing -> caller inspects payload.
    assert observations.normalized_status_for_event_type("session.status") == (
        False,
        None,
    )


def test_provider_status_aliases_are_canonicalized() -> None:
    assert compatibility.canonicalize_provider_status("CANCELLED") == "canceled"
    assert compatibility.canonicalize_provider_status("timeout") == "timed_out"
    assert compatibility.canonicalize_provider_status("running") == "running"


def test_terminal_status_is_absorbing() -> None:
    assert can_transition("declared", "active") is True
    assert can_transition("active", "completed") is True
    assert can_transition("completed", "active") is False
    assert next_status("failed", "running") == "failed"
    assert next_status("creating", "running") == "active"


def test_legacy_failure_classification_reexports_domain() -> None:
    from moonmind.omnigent import failure_classification as legacy

    assert legacy.failure_class_for_terminal_status is (
        failures.failure_class_for_terminal_status
    )
    assert legacy.OmnigentFailureReason is failures.OmnigentFailureReason


def test_legacy_bridge_store_delegates_vocabulary() -> None:
    from moonmind.omnigent import bridge_store

    # Same objects: no duplicate vocabulary table remains.
    assert bridge_store._TERMINAL_STATUSES is session_state.TERMINAL_STATUSES
    assert (
        bridge_store._NON_TERMINAL_NORMALIZED_STATUSES
        is session_state.NON_TERMINAL_NORMALIZED_STATUSES
    )
    assert bridge_store._STATUS_ALIASES is compatibility.PROVIDER_STATUS_ALIASES
    # Delegating functions preserve behavior.
    assert bridge_store.coalesce_bridge_status("running") == "active"
    assert bridge_store.bridge_failure_class("timed_out") == "system_error"
    assert bridge_store.bridge_failure_class("cancelled") == "system_error"
