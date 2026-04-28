# Research: Settings Catalog and Effective Values

## FR-001 / DESIGN-REQ-003 Backend-Owned Catalog

Decision: Add `SettingsCatalogService` with an explicit registry of exposed settings and route it through `GET /api/v1/settings/catalog`.
Evidence: No existing settings catalog route was present; existing application settings live in `moonmind/config/settings.py`.
Rationale: A backend service keeps descriptor metadata authoritative and reusable by UI, CLI, tests, diagnostics, and future documentation generators.
Alternatives considered: Annotating every Pydantic settings field now was rejected because this story needs a narrow read-side contract and many existing settings are not eligible for generic editing.
Test implications: Unit and API route tests.

## FR-002 / DESIGN-REQ-005 Descriptor Shape

Decision: Define Pydantic response models for descriptor metadata including key, title, section, category, type, UI, scopes, defaults, effective value, source, options, constraints, sensitivity, read-only state, reload flags, dependencies, order, audit, version, and diagnostics.
Evidence: `docs/Security/SettingsSystem.md` §8.1 lists the desired descriptor shape.
Rationale: Contract models make missing fields visible in tests and OpenAPI output.
Alternatives considered: Returning untyped dictionaries was rejected because descriptor shape stability is core to the story.
Test implications: Unit tests assert representative descriptor fields.

## FR-003 / FR-007 / DESIGN-REQ-007 Explicit Exposure

Decision: Only registry entries are exposed. Raw or unregistered backend fields such as `workflow.github_token` are omitted from catalog output and rejected as `setting_not_exposed` on write.
Evidence: `docs/Security/SettingsSystem.md` §8.3 and §9 require explicit metadata before UI editability.
Rationale: Default-deny exposure avoids leaking secrets or deployment-only settings.
Alternatives considered: Reflecting all Pydantic fields with heuristics was rejected because it would risk exposing ineligible values.
Test implications: Unit and API tests cover omission and write rejection.

## FR-004 / FR-005 / DESIGN-REQ-008 Effective Resolution and Diagnostics

Decision: Resolve values from deployment environment aliases first, then loaded application settings/defaults, and include diagnostics for inherited null and unresolved SecretRefs.
Evidence: `docs/Security/SettingsSystem.md` §10 requires source explanations and explicit diagnostics for missing/null/unresolved states.
Rationale: This gives read clients deterministic explanations without adding persistence before `MM-538`.
Alternatives considered: Implementing user/workspace override storage now was rejected because it belongs to linked issue `MM-538`.
Test implications: Unit tests cover environment source, inherited null, and unresolved SecretRef diagnostics.

## FR-009 / DESIGN-REQ-022 Structured Errors

Decision: Return stable JSON error payloads with `error`, `message`, `key`, `scope`, and `details` from settings routes.
Evidence: `docs/Security/SettingsSystem.md` §12.7 defines the structured error shape.
Rationale: Clients can branch on error codes without scraping messages.
Alternatives considered: Plain `HTTPException` strings were rejected because they do not meet the documented contract.
Test implications: API route tests assert exact error shape for unknown/unexposed settings.
