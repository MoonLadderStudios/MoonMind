# Feature Specification: Backend-Owned Settings Catalog Registry and Descriptor Contract

**Feature Branch**: `330-backend-settings-catalog-registry`
**Created**: 2026-05-08
**Status**: Draft
**Input**: Jira preset brief MM-652 from trusted `jira.get_issue` MCP response.

Preserved source Jira preset brief: `MM-652` from the trusted `jira.get_issue` response, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Classification: single-story runtime feature request.
Resume decision: no existing MoonSpec feature directory matched `MM-652` under `specs/`; Specify is the first incomplete stage.

## Original Preset Brief

```text
# MM-652 MoonSpec Orchestration Input

## Source

- Jira issue: MM-652
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: Backlog
- Summary: Backend-owned settings catalog registry and descriptor contract
- Labels: moonmind-workflow-mm-3997e9d9-e676-4b50-8e8d-e319fc13ef97
- Trusted fetch tool: jira.get_issue via MoonMind MCP

## Source Reference

Source Document: docs/Security/SettingsSystem.md
Source Title: Settings System
Source Sections:
- 5.1 Backend-Owned Truth
- 5.9 Durable Contracts Over Ad Hoc Forms
- 7.1 Setting Key
- 7.2 Setting Descriptor
- 8 Settings Catalog Contract
- 26 Suggested Internal Components

Coverage IDs:
- S5.1, S5.9, S7.1, S7.2, S8.1, S8.2, S8.3, S8.4
- S22.4, S22.5, S25.1, S25.20
- S26.SettingsRegistry, S26.SettingsCatalogBuilder
- S29.1

## Story

Establish the backend-owned settings catalog registry that emits typed
SettingDescriptor records (key, title, description, category, section, type,
ui, scopes, default/effective/override values, source, options, constraints,
sensitive flag, secret_role, read_only, reload metadata, applies_to,
depends_on, audit policy). The registry must enforce stable dotted setting
keys, derive descriptors from typed backend models plus explicit MoonMind
metadata (moonmind.expose, section, category, scopes, ui, requires_reload),
and refuse to expose any field that lacks explicit metadata. Registry output
drives Settings UI, API clients, CLI tooling, tests, diagnostics, onboarding
flows, and documentation generators.

## Acceptance

- Catalog generation includes every explicitly exposed eligible setting and
  excludes everything else by default.
- Setting keys are unique, stable, URL/JSON safe, and never overloaded across
  scopes.
- Descriptor records carry every required field from §8.1 with
  backwards-compatible enum values, scope semantics, and option ordering.
- A snapshot test detects accidental catalog drift (added/removed keys,
  changed scopes, changed types).
- Changing or deleting a descriptor without an explicit migration entry fails
  the catalog build.
```

## Classification

- Input type: Single-story feature request.
- Selected mode: Runtime.
- Source design: `docs/Security/SettingsSystem.md` sections 5.1, 5.9, 7.1, 7.2, 8, 26.
- Resume decision: No existing MoonSpec artifact for MM-652 found; Specify is the first incomplete stage.
- Related prior work: spec 267 (`267-settings-catalog-effective-values` / MM-537) delivered the read-side catalog and effective-value contract. MM-652 layers on named `SettingsRegistry` and `SettingsCatalogBuilder` components, `moonmind.expose` annotation support for Pydantic models, and the snapshot + migration-gate contract that prevents silent catalog drift.

## User Story

**Summary**: As a MoonMind backend developer, I can define exposed settings using `moonmind.expose` metadata on Pydantic fields, register them in a named `SettingsRegistry`, and trust that the `SettingsCatalogBuilder` will fail fast if a descriptor is removed or changed without an explicit migration entry — so catalog stability is enforced by the build, not just convention.

**Goal**: The `SettingsRegistry` component owns descriptor registration and eligibility filtering. The `SettingsCatalogBuilder` component owns catalog construction from that registry. A committed stable-key ledger and migration gate prevent silent descriptor removal. A snapshot test catches accidental drift in keys, scopes, or types. Pydantic fields with `json_schema_extra={"moonmind": {"expose": True, ...}}` can auto-contribute registry entries.

**Independent Test**: Build a `SettingsRegistry`, remove an entry without a migration rule, verify the build fails. Build with a migration rule, verify it succeeds. Generate a catalog from the default registry, compare to the committed snapshot, verify no drift. Call `SettingsRegistry.from_pydantic_model()` with a model that has `moonmind.expose`, verify the resulting entry has the correct metadata.

## Acceptance Scenarios

1. **Given** a `SettingsRegistry` is constructed with a key in the stable-key ledger that is absent from the current entries and has no migration rule, **When** the registry is built, **Then** it raises `ValueError` with error code `catalog_integrity_error` listing the unmigrated keys.

2. **Given** a `SettingsRegistry` is constructed with the same absent key but a corresponding `SettingMigrationRule` covering it, **When** the registry is built, **Then** it succeeds without error.

3. **Given** a `SettingsCatalogBuilder` receives a `SettingsRegistry`, **When** `build()` is called with optional section and scope filters, **Then** the result is a `SettingsCatalogResponse` containing only descriptors matching the filters, grouped by category, ordered by `order`.

4. **Given** a Pydantic settings model with one field that has `json_schema_extra={"moonmind": {"expose": True, "key": "workflow.my_setting", "section": "user-workspace", "category": "Workflow", "scopes": ["workspace"], "ui": "toggle"}}`, **When** `SettingsRegistry.from_pydantic_model()` is called with that model, **Then** the resulting registry contains an entry for `workflow.my_setting` with the declared metadata.

5. **Given** a Pydantic field without `moonmind.expose`, **When** `SettingsRegistry.from_pydantic_model()` processes it, **Then** the field produces no registry entry.

6. **Given** the default `_REGISTRY` entries are unchanged, **When** the catalog snapshot test runs, **Then** it passes with no drift.

7. **Given** a registry entry is added, removed, or its `type` / `scopes` changed without updating the snapshot file, **When** the catalog snapshot test runs, **Then** it fails with a descriptive diff showing the exact drift.

8. **Given** MoonSpec artifacts and downstream evidence reference this work, **When** traceability is reviewed, **Then** the Jira issue key `MM-652` and coverage IDs S5.1, S5.9, S7.1, S7.2, S8.1–S8.4, S26.SettingsRegistry, S26.SettingsCatalogBuilder are present.

### Edge Cases

- A key that is `"secret"` or matches a sensitive-name heuristic token in `_UNSAFE_FIELD_TOKENS` must be rejected by the registry unless the entry explicitly marks `sensitive=True` and `ui="secret_ref_picker"` or `ui="readonly"`.
- `from_pydantic_model()` skips nested sub-models — only top-level fields with `moonmind.expose` are extracted.
- A valid dotted key must match `^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*)*$`; the registry rejects any entry with a key that does not match.
- Duplicate keys within the same registry raise `ValueError` immediately.

## Assumptions

- The existing `_REGISTRY` tuple and `SettingsCatalogService` from MM-537 remain the runtime source of truth for the 7 currently exposed settings; MM-652 wraps them in named components without removing existing test coverage.
- `moonmind.expose` metadata on AppSettings fields is additive; no existing field currently carries it, so MM-652 must add it to the fields corresponding to the 7 current registry entries.
- The stable-key ledger is committed as a Python constant `_CATALOG_KEY_LEDGER: frozenset[str]` in `settings_catalog.py`; it starts with the 7 current keys.
- Snapshot file is committed under `tests/unit/services/snapshots/settings_catalog_snapshot.json` and the snapshot test lives in `tests/unit/services/test_settings_catalog_snapshot.py`.

## Source Design Requirements

| ID | Source | Requirement | Scope |
|---|---|---|---|
| S5.1 | SettingsSystem.md §5.1 | The backend owns the settings catalog, setting types, validation rules, and eligibility. | In scope |
| S5.9 | SettingsSystem.md §5.9 | A setting descriptor must be usable by UI, API clients, CLI tooling, tests, diagnostics, onboarding, and documentation generators. | In scope |
| S7.1 | SettingsSystem.md §7.1 | Setting keys are stable, dotted, unique, URL/JSON safe, and scope-independent. | In scope |
| S7.2 | SettingsSystem.md §7.2 | A setting descriptor includes key, title, description, category, section, type, UI, scopes, values, constraints, options, sensitivity, read-only, reload, dependencies, and audit policy. | In scope |
| S8.1 | SettingsSystem.md §8.1 | The descriptor shape is the full SettingDescriptor contract. | In scope |
| S8.2 | SettingsSystem.md §8.2 | Descriptors may be generated from typed backend models. | In scope — `from_pydantic_model` |
| S8.3 | SettingsSystem.md §8.3 | A field must have explicit `moonmind.expose` metadata before becoming UI-editable. | In scope |
| S8.4 | SettingsSystem.md §8.4 | The catalog is a durable API contract; changes require migration rules and snapshot updates. | In scope — migration gate + snapshot test |
| S26.SettingsRegistry | SettingsSystem.md §26 | `SettingsRegistry` owns descriptor registration and eligibility filtering. | In scope |
| S26.SettingsCatalogBuilder | SettingsSystem.md §26 | `SettingsCatalogBuilder` builds catalog responses. | In scope |

## Functional Requirements

- **FR-001**: A `SettingsRegistry` class MUST own descriptor registration, uniqueness enforcement, key format validation, and eligibility filtering.
- **FR-002**: `SettingsRegistry` MUST accept a committed `stable_key_ledger` and fail with `catalog_integrity_error` if any ledger key is absent from the current entries without a covering `SettingMigrationRule`.
- **FR-003**: `SettingsRegistry` MUST expose a `from_pydantic_model()` class method that extracts `SettingRegistryEntry` objects from Pydantic model fields with `json_schema_extra.moonmind.expose == True`.
- **FR-004**: A `SettingsCatalogBuilder` class MUST own catalog-response construction from a `SettingsRegistry`, supporting section and scope filters and category ordering.
- **FR-005**: Setting keys MUST match `^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*)*$`; invalid keys MUST be rejected at registry construction.
- **FR-006**: A snapshot test MUST detect accidental drift in key set, scopes, or types of the default catalog.
- **FR-007**: The `_CATALOG_KEY_LEDGER` constant MUST start with the 7 keys currently registered in `_REGISTRY` and MUST be updated when new keys are added.
- **FR-008**: MoonSpec artifacts MUST preserve `MM-652` and the S-prefixed coverage IDs listed above.

## Success Criteria

- **SC-001**: `SettingsRegistry` raises `catalog_integrity_error` for a removed key without a migration rule.
- **SC-002**: `SettingsRegistry.from_pydantic_model()` produces entries for `moonmind.expose` fields and skips non-exposed fields.
- **SC-003**: `SettingsCatalogBuilder.build()` returns `SettingsCatalogResponse` with correct section/scope filtering and category grouping.
- **SC-004**: Snapshot test passes with the current committed snapshot; it fails when an entry is intentionally mutated without updating the snapshot.
- **SC-005**: All existing `test_settings_catalog.py` and `test_settings_api.py` tests continue to pass after refactoring.
- **SC-006**: Traceability confirms `MM-652` in artifacts and commit/PR metadata.
