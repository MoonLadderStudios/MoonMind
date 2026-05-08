# Tasks: Backend-Owned Settings Catalog Registry and Descriptor Contract

**Input**: `specs/330-backend-settings-catalog-registry/spec.md`, `specs/330-backend-settings-catalog-registry/plan.md`
**Unit Test Command**: `./tools/test_unit.sh`
**Source Traceability**: `MM-652`; FR-001 through FR-008; SC-001 through SC-006; S5.1, S5.9, S7.1, S7.2, S8.1–S8.4, S26.SettingsRegistry, S26.SettingsCatalogBuilder

## Phase 1: Setup

- [X] T001 Create `specs/330-backend-settings-catalog-registry/` MoonSpec artifacts preserving MM-652. (FR-008, SC-006)

## Phase 2: Foundational

- [X] T002 Add `_SETTING_KEY_RE`, `_CATALOG_KEY_LEDGER`, `SettingsRegistry`, and `SettingsCatalogBuilder` to `api_service/services/settings_catalog.py`. (FR-001, FR-002, FR-004, FR-005, FR-007)
- [X] T003 Add `moonmind.expose` metadata to the 7 relevant fields in `moonmind/config/settings.py` `WorkflowSettings`. (FR-003, S8.3)
- [X] T004 Update `SettingsCatalogService` to internally construct a `SettingsRegistry` from its `registry` param and use `SettingsCatalogBuilder` in `catalog()` and `catalog_async()`. (FR-001, FR-004)

## Phase 3: Story Tests

- [X] T005 Add tests for `SettingsRegistry` (migration gate, key format validation, duplicate key rejection) in `tests/unit/services/test_settings_catalog.py`. (FR-001, FR-002, FR-005, SC-001)
- [X] T006 Add test for `SettingsRegistry.from_pydantic_model()` (exposed field produces entry; unexposed field skipped). (FR-003, SC-002)
- [X] T007 Add tests for `SettingsCatalogBuilder.build()` (section filter, scope filter, category grouping, order). (FR-004, SC-003)
- [X] T008 Create `tests/unit/services/snapshots/settings_catalog_snapshot.json` with the committed catalog shape for 7 current entries. (FR-006, SC-004)
- [X] T009 Create `tests/unit/services/test_settings_catalog_snapshot.py` with the snapshot drift test. (FR-006, SC-004)

## Phase 4: Validation

- [X] T010 Run `./tools/test_unit.sh` and confirm all existing and new tests pass. 52 settings-catalog tests pass; 29 pre-existing failures on main are unrelated to MM-652. (SC-001 through SC-005)
- [X] T011 Verify traceability: `MM-652` and S-prefixed IDs present in spec artifacts. Confirmed in spec.md — S5.1, S5.9, S7.1, S7.2, S8.1–S8.4, S26.SettingsRegistry, S26.SettingsCatalogBuilder, S29.1. (FR-008, SC-006)

## Dependencies

1. T001 before all.
2. T002 and T003 before T004.
3. T002 before T005, T006, T007.
4. T003 before T006.
5. T008 before T009.
6. T004 through T009 before T010.
7. T010 before T011.
