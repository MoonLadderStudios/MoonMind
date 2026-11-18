"""Remove redundant bootstrap configuration without a new settings system.

Source issue: MoonLadderStudios/MoonMind#3941.

Coherent group under test: duplicated bootstrap definitions whose values are
already determined by a single owner.

* ``MOONMIND_IMAGE`` advertised twice in ``.env-template`` with the same
  value.
* The ``MEMORY_*`` block advertised twice in ``.env-template`` with
  conflicting ``MEMORY_CONTEXT_BUDGET_TOKENS`` values (``4096`` versus
  ``2000``); the single retained block matches the code default.

The Codex cutover phases, generic qualification booleans, rollout JSON
document, rollback controls, canary cohorts, and host-image aliases named in
the issue remain active implementation inputs owned by #3830/#3831/#3833,
#3835, and #3931, so they are triaged, not removed, here: removing them in
this step would broaden admission authority or duplicate those owners.
"""

from __future__ import annotations

from pathlib import Path

from api_service.services.settings_catalog import SettingsCatalogService
from moonmind.memory.context_pack import DEFAULT_MEMORY_CONTEXT_BUDGET_TOKENS


def _template_assignments() -> list[tuple[str, str]]:
    assignments: list[tuple[str, str]] = []
    for line in Path(".env-template").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        assignments.append((key.strip(), value.strip()))
    return assignments


def test_env_template_has_no_duplicate_keys() -> None:
    """Every template key is advertised exactly once."""

    keys = [key for key, _ in _template_assignments()]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})

    assert duplicates == []


def test_env_template_memory_block_matches_code_default() -> None:
    """The single retained MEMORY block agrees with the code default."""

    values = dict(_template_assignments())

    assert values["MEMORY_CONTEXT_BUDGET_TOKENS"].strip('"') == str(
        DEFAULT_MEMORY_CONTEXT_BUDGET_TOKENS
    )
    assert DEFAULT_MEMORY_CONTEXT_BUDGET_TOKENS == 4096


def _boolean_registry() -> tuple:
    entry_type = SettingsCatalogService(env={})._registry[0].__class__
    return (
        entry_type(
            key="test.redundant_example_flag",
            title="Example Flag",
            category="Test",
            section="user-workspace",
            value_type="boolean",
            ui="toggle",
            default_value=False,
            order=1,
            apply_mode="worker_reload",
            requires_reload=True,
            env_aliases=("TEST_REDUNDANT_EXAMPLE_FLAG",),
            applies_to=("worker",),
        ),
    )


def test_resolver_preserves_explicit_false_over_default() -> None:
    """Unset versus explicit false stays distinguishable without a store."""

    service = SettingsCatalogService(
        env={"TEST_REDUNDANT_EXAMPLE_FLAG": "false"},
        session=None,
        registry=_boolean_registry(),
    )

    effective = service.effective_value("test.redundant_example_flag")

    assert effective.value is False
    assert effective.source == "environment"
    # Application timing is explicit: worker reload, not immediate read.
    assert effective.apply_mode == "worker_reload"
    assert effective.requires_reload is True


def test_resolver_reset_to_default_without_store() -> None:
    """Removing the override restores the default with default provenance."""

    service = SettingsCatalogService(
        env={},
        session=None,
        registry=_boolean_registry(),
    )

    effective = service.effective_value("test.redundant_example_flag")

    assert effective.value is False
    assert effective.source == "default"


def test_resolver_unavailable_store_never_exposes_secrets() -> None:
    """Session-None (unavailable-store) resolution carries no secret material."""

    entry_type = SettingsCatalogService(env={})._registry[0].__class__
    registry = (
        entry_type(
            key="test.redundant_example_token",
            title="Example Token",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            default_value="",
            order=2,
            apply_mode="immediate",
            env_aliases=("TEST_REDUNDANT_EXAMPLE_TOKEN",),
            applies_to=("catalog",),
        ),
    )
    service = SettingsCatalogService(
        env={"TEST_REDUNDANT_EXAMPLE_TOKEN": "operator-held-reference"},
        session=None,
        registry=registry,
    )

    effective = service.effective_value("test.redundant_example_token")

    assert effective.source == "environment"
    assert "operator-held-reference" not in (effective.source_explanation or "")
    assert "operator-held-reference" not in (
        (effective.diagnostics[0].message if effective.diagnostics else "") or ""
    )
