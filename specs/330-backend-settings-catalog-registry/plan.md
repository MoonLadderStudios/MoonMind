# Implementation Plan: Backend-Owned Settings Catalog Registry and Descriptor Contract

**Spec**: `specs/330-backend-settings-catalog-registry/spec.md`
**Jira**: MM-652
**Created**: 2026-05-08

## Context

Spec 267 (MM-537) delivered the read-side settings catalog and effective-value contract, implementing `SettingsCatalogService` with a static `_REGISTRY` tuple of 7 hardcoded `SettingRegistryEntry` objects and full API routes. MM-652 layers on:

1. Named `SettingsRegistry` component with migration gate
2. Named `SettingsCatalogBuilder` component
3. `moonmind.expose` annotation support for Pydantic model fields
4. Committed `_CATALOG_KEY_LEDGER` and migration-gate validation
5. Snapshot test for catalog drift detection

## Implementation Strategy

**Targeted refactoring** of `api_service/services/settings_catalog.py`:

- Extract a `SettingsRegistry` class that wraps `_REGISTRY` entries and adds:
  - Key format validation (`^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*)*$`)
  - Duplicate key detection
  - Migration gate against `_CATALOG_KEY_LEDGER`
  - `from_pydantic_model()` class method
- Extract a `SettingsCatalogBuilder` class that owns `build()` (section+scope filter → `SettingsCatalogResponse`)
- Update `SettingsCatalogService` to internally create `SettingsRegistry` and use `SettingsCatalogBuilder`; backward-compatible constructor signature preserved
- Add `_CATALOG_KEY_LEDGER: frozenset[str]` constant with the 7 current keys
- Create the committed snapshot file and snapshot test

**No new database tables**. **No changes to API routes or response shapes**.

## File Changes

| File | Change |
|---|---|
| `api_service/services/settings_catalog.py` | Add `SettingsRegistry`, `SettingsCatalogBuilder`, `_CATALOG_KEY_LEDGER`; update `SettingsCatalogService` |
| `moonmind/config/settings.py` | Add `moonmind.expose` metadata to 7 `WorkflowSettings` fields |
| `tests/unit/services/test_settings_catalog_snapshot.py` | New snapshot test |
| `tests/unit/services/snapshots/settings_catalog_snapshot.json` | Committed catalog snapshot |
| `tests/unit/services/test_settings_catalog.py` | Add tests for `SettingsRegistry` and `SettingsCatalogBuilder` |

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I. Orchestrate, Don't Recreate | PASS | No new agent or cognitive logic |
| II. One-Click Deployment | PASS | No new services or infra |
| III. Avoid Vendor Lock-In | PASS | Pure backend Python, no vendor deps |
| IV. Own Your Data | PASS | No new data stores |
| V. Skills Are First-Class | PASS | No skill system changes |
| VI. Bittersweet Lesson | PASS | Thin scaffold; thick contracts |
| VII. Runtime Configurability | PASS | `moonmind.expose` metadata is runtime config |
| VIII. Modular Architecture | PASS | Named components behind stable interfaces |
| IX. Resilient by Default | PASS | Migration gate provides fail-fast on catalog drift |
| X. Continuous Improvement | PASS | Snapshot test enables regression detection |
| XI. Spec-Driven Development | PASS | Full spec/plan/tasks before implementation |
| XII. Canonical Docs Separation | PASS | No canonical doc modification |
| XIII. Delete, Don't Deprecate | PASS | Old `_REGISTRY` tuple replaced by `SettingsRegistry`; `_validate_registry()` internalized |

## Complexity Tracking

No cross-cutting changes. `SettingsCatalogService.__init__` signature is backward-compatible (still accepts `registry: tuple[SettingRegistryEntry, ...]`). The new `SettingsRegistry` and `SettingsCatalogBuilder` classes are introduced in the same file and tested in existing + new test modules.

## Implementation Details

### SettingsRegistry class

```python
_SETTING_KEY_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*)*$")

_CATALOG_KEY_LEDGER: frozenset[str] = frozenset({
    "workflow.default_task_runtime",
    "workflow.default_publish_mode",
    "workflow.default_provider_profile_ref",
    "skills.policy_mode",
    "skills.canary_percent",
    "live_sessions.default_enabled",
    "integrations.github.token_ref",
})

class SettingsRegistry:
    def __init__(
        self,
        entries: tuple[SettingRegistryEntry, ...],
        migration_rules: tuple[SettingMigrationRule, ...] = (),
        stable_key_ledger: frozenset[str] | None = _CATALOG_KEY_LEDGER,
    ) -> None:
        self._entries = entries
        self._entries_by_key = {e.key: e for e in entries}
        self._migration_rules = migration_rules
        self._validate()

    def _validate(self) -> None:
        # Key format + uniqueness
        seen: set[str] = set()
        for entry in self._entries:
            if not _SETTING_KEY_RE.match(entry.key):
                raise ValueError(f"invalid_key_format: {entry.key!r}")
            if entry.key in seen:
                raise ValueError(f"duplicate_key: {entry.key!r}")
            seen.add(entry.key)
        # Migration gate
        if self._stable_key_ledger is not None:
            migrated = {r.old_key for r in self._migration_rules}
            removed_without_migration = self._stable_key_ledger - seen - migrated
            if removed_without_migration:
                raise ValueError(
                    f"catalog_integrity_error: {sorted(removed_without_migration)}"
                )

    @classmethod
    def from_pydantic_model(cls, model_class: type, ...) -> "SettingsRegistry":
        ...
```

### SettingsCatalogBuilder class

```python
class SettingsCatalogBuilder:
    def __init__(self, registry: SettingsRegistry) -> None:
        self._registry = registry

    def build(
        self,
        section: SettingSection | None = None,
        scope: SettingScope | None = None,
        descriptor_fn: Callable[[SettingRegistryEntry], SettingDescriptor] = ...,
    ) -> SettingsCatalogResponse:
        categories: dict[str, list[SettingDescriptor]] = {}
        for entry in sorted(self._registry.entries, key=lambda e: e.order):
            if section is not None and entry.section != section:
                continue
            if scope is not None and scope not in entry.scopes:
                continue
            descriptor = descriptor_fn(entry)
            categories.setdefault(entry.category, []).append(descriptor)
        return SettingsCatalogResponse(section=section, scope=scope, categories=categories)
```

### moonmind.expose metadata on AppSettings

For each of the 7 currently registered fields in `WorkflowSettings`, add:
```python
default_task_runtime: str = Field(
    ...,
    json_schema_extra={
        "moonmind": {
            "expose": True,
            "key": "workflow.default_task_runtime",
            "section": "user-workspace",
            "category": "Workflow",
            "scopes": ["workspace"],
            "ui": "select",
            "requires_reload": False,
        }
    },
)
```

`SettingsRegistry.from_pydantic_model()` iterates `model_class.model_fields`, checks for `json_schema_extra.moonmind.expose == True`, and produces `SettingRegistryEntry` objects. This is validated by SC-002.

### Snapshot test

```python
# tests/unit/services/test_settings_catalog_snapshot.py
import json
from pathlib import Path
from api_service.services.settings_catalog import SettingsCatalogService

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "settings_catalog_snapshot.json"

def test_catalog_snapshot_no_drift():
    service = SettingsCatalogService(env={})
    catalog = service.catalog()
    actual = {
        entry.key: {"type": entry.type, "scopes": sorted(entry.scopes), "section": entry.section}
        for section_items in catalog.categories.values()
        for entry in section_items
    }
    expected = json.loads(SNAPSHOT_PATH.read_text())
    assert actual == expected, f"Catalog drift: {set(actual) ^ set(expected)}"
```

The committed snapshot file contains the current 7-entry catalog shape.

## Risk and Mitigations

| Risk | Mitigation |
|---|---|
| `SettingsCatalogService` backward compat breaks existing tests | Keep `registry: tuple[SettingRegistryEntry, ...]` constructor param; internally wrap in `SettingsRegistry` |
| `from_pydantic_model` over-extracts nested models | Only iterate top-level `model_class.model_fields`; skip FieldInfo without `moonmind.expose` |
| Snapshot file gets stale | CI fails on any catalog change; developer must update snapshot intentionally |
| `_CATALOG_KEY_LEDGER` grows stale | `SettingsRegistry._validate()` fails at construction if new key added to registry without updating ledger (detect via test) |
