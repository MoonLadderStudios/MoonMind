"""Remove redundant bootstrap configuration without a new settings system.

Source issue: MoonLadderStudios/MoonMind#3941.

Coherent group under test: duplicated bootstrap definitions whose values are
already determined by a single owner.

* ``MODEL_CACHE_REFRESH_INTERVAL`` (legacy, unit-less) versus
  ``MODEL_CACHE_REFRESH_INTERVAL_SECONDS`` (canonical): two template lines
  and two ``AppSettings`` fields for one value with zero consumers. The
  legacy name is honored once through the existing ``AppSettings`` owner;
  the canonical name wins on conflict and a blank value behaves like an
  omitted one, while an explicit ``0`` stays ``0``.
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

import pytest

from api_service.services.settings_catalog import SettingsCatalogService
from moonmind.config.settings import AppSettings
from moonmind.memory.context_pack import DEFAULT_MEMORY_CONTEXT_BUDGET_TOKENS

_CANONICAL_MODEL_CACHE_ENV = "MODEL_CACHE_REFRESH_INTERVAL_SECONDS"
_LEGACY_MODEL_CACHE_ENV = "MODEL_CACHE_REFRESH_INTERVAL"
_MODEL_CACHE_DEFAULT = 3600


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


def test_env_template_advertises_single_model_cache_interval() -> None:
    """Only the canonical model-cache interval remains in the example."""

    values = dict(_template_assignments())

    assert values[_CANONICAL_MODEL_CACHE_ENV] == str(_MODEL_CACHE_DEFAULT)
    assert _LEGACY_MODEL_CACHE_ENV not in values


def test_env_template_memory_block_matches_code_default() -> None:
    """The single retained MEMORY block agrees with the code default."""

    values = dict(_template_assignments())

    assert values["MEMORY_CONTEXT_BUDGET_TOKENS"].strip('"') == str(
        DEFAULT_MEMORY_CONTEXT_BUDGET_TOKENS
    )
    assert DEFAULT_MEMORY_CONTEXT_BUDGET_TOKENS == 4096


def test_model_cache_defaults_without_either_variable(monkeypatch) -> None:
    """No-``.env`` startup resolves the documented default."""

    monkeypatch.delenv(_CANONICAL_MODEL_CACHE_ENV, raising=False)
    monkeypatch.delenv(_LEGACY_MODEL_CACHE_ENV, raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.model_cache_refresh_interval_seconds == _MODEL_CACHE_DEFAULT


def test_model_cache_legacy_alias_is_honored_once(monkeypatch) -> None:
    """Deployments exporting only the legacy name keep working."""

    monkeypatch.delenv(_CANONICAL_MODEL_CACHE_ENV, raising=False)
    monkeypatch.setenv(_LEGACY_MODEL_CACHE_ENV, "1800")

    settings = AppSettings(_env_file=None)

    assert settings.model_cache_refresh_interval_seconds == 1800


def test_model_cache_canonical_wins_on_conflict(monkeypatch) -> None:
    """A conflict resolves deterministically instead of diverging fields."""

    monkeypatch.setenv(_CANONICAL_MODEL_CACHE_ENV, "120")
    monkeypatch.setenv(_LEGACY_MODEL_CACHE_ENV, "1800")

    settings = AppSettings(_env_file=None)

    assert settings.model_cache_refresh_interval_seconds == 120


def test_model_cache_blank_behaves_like_omitted(monkeypatch) -> None:
    """An empty value is the documented default, not a startup failure."""

    monkeypatch.setenv(_CANONICAL_MODEL_CACHE_ENV, "")
    monkeypatch.delenv(_LEGACY_MODEL_CACHE_ENV, raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.model_cache_refresh_interval_seconds == _MODEL_CACHE_DEFAULT


def test_model_cache_explicit_zero_is_preserved(monkeypatch) -> None:
    """Unset/empty must not collapse an explicit zero."""

    monkeypatch.setenv(_CANONICAL_MODEL_CACHE_ENV, "0")
    monkeypatch.delenv(_LEGACY_MODEL_CACHE_ENV, raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.model_cache_refresh_interval_seconds == 0


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
            scopes=("workspace",),
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

    effective = service.effective_value(
        "test.redundant_example_flag", scope="workspace"
    )

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

    effective = service.effective_value(
        "test.redundant_example_flag", scope="workspace"
    )

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
            scopes=("workspace",),
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

    effective = service.effective_value(
        "test.redundant_example_token", scope="workspace"
    )

    assert effective.source == "environment"
    assert "operator-held-reference" not in (effective.source_explanation or "")
    assert "operator-held-reference" not in (
        (effective.diagnostics[0].message if effective.diagnostics else "") or ""
    )
