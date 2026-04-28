# Feature Specification: Settings Migration Invariants

**Feature Branch**: `[275-settings-migration-invariants]`
**Created**: 2026-04-28
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-546 as the canonical Moon Spec orchestration input.

Canonical Jira preset brief:

MM-546: Migration, non-goals, invariants, and test gate

Source Reference
Source Document: docs/Security/SettingsSystem.md
Source Title: Settings System
Source Sections:
- 4. Non-Goals
- 24. Migration and Deprecation
- 25. Testing Requirements
- 28. Open Integration Points
- 29. Desired-State Invariants
Coverage IDs:
- DESIGN-REQ-020
- DESIGN-REQ-021
- DESIGN-REQ-024
- DESIGN-REQ-027
- DESIGN-REQ-028

As a maintainer, I can evolve the Settings System safely because migrations, non-goals, future integrations, and desired-state invariants are enforced by tests and diagnostics rather than tribal knowledge.

Acceptance Criteria
- Renaming a setting uses a new descriptor key, migrates old overrides, exposes deprecation diagnostics where helpful, records audit visibility, and tests effective-value preservation.
- Removing a setting rejects new writes, preserves or migrates existing values, explains deprecated values in diagnostics, and avoids silent loss of operator intent.
- Changing a setting type requires explicit migration and the resolver does not ambiguously reinterpret existing JSON values.
- Regression coverage proves catalog exposure, scope declarations, backend validation, secret safety, SecretRef validation/audit without plaintext, provider profile secret references, source explainability, reset inheritance, operator locks, operational audit, and intentional catalog changes.
- Future integration contracts explicitly preserve descriptor-driven exposure, scoped overrides, server-side validation, auditability, and secret-safe behavior.

Requirements
- The Settings System must not become a generic database editor, generic secret manager, raw env editor, generic admin UI, or frontend-authoritative validation layer.
- Catalog drift must be visible through snapshot or equivalent regression tests.
- Non-goals and invariants must remain enforceable as the implementation evolves.

Canonical source: synthesized from trusted Jira issue fields because the MCP issue response did not expose recommended preset instructions or a normalized preset brief.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-546 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata."

## User Story - Settings Evolution Safety Gate

**Summary**: As a maintainer, I want Settings System migration rules, non-goals, and invariants to be enforced by tests and diagnostics so that future catalog changes cannot silently weaken operator intent, validation, or secret safety.

**Goal**: Maintainers can safely rename, remove, or change typed settings while receiving deterministic test and diagnostic evidence that effective values, auditability, explicit exposure, scoped overrides, and secret-safe behavior remain intact.

**Independent Test**: Can be fully tested by running the Settings System unit and integration coverage for migration/deprecation behavior and catalog invariants, then confirming the suite fails on unsafe catalog changes and passes when migrations, diagnostics, and invariants are correctly preserved.

**Acceptance Scenarios**:

1. **Given** a setting key is renamed and an old override exists, **When** effective settings are resolved after the migration, **Then** the new descriptor key is used, the old override value is preserved or migrated, audit/deprecation visibility is available, and tests prove the effective value did not change unexpectedly.
2. **Given** a setting has been removed or deprecated, **When** a new write targets the removed key, **Then** the write is rejected, existing operator intent is preserved or migrated, diagnostics explain the deprecated value, and no generic settings path silently drops the value.
3. **Given** a setting's type changes, **When** existing stored JSON values are evaluated, **Then** the system requires explicit migration behavior and fails rather than ambiguously reinterpreting old values.
4. **Given** the Settings System catalog and resolution behavior are tested, **When** the regression suite runs, **Then** it proves explicit catalog exposure, scope declarations, backend validation, SecretRef safety, provider profile secret references, source explainability, reset inheritance, operator locks, operational audit, and intentional catalog changes.
5. **Given** a future integration consumes settings data, **When** it depends on the Settings System contract, **Then** descriptor-driven exposure, scoped overrides, server-side validation, auditability, and secret-safe behavior remain preserved.

### Edge Cases

- Existing overrides may reference keys that are no longer editable but still need diagnostic visibility.
- Secret-like or SecretRef values must never be exposed as plaintext during migration, diagnostics, audit, or tests.
- Catalog drift may be accidental; regression evidence must distinguish intentional descriptor changes from unreviewed exposure changes.
- Operator locks and scope declarations must still block ordinary user or workspace writes during migration and reset flows.
- Non-goal behavior must stay excluded: raw environment editing, generic database editing, generic secret management, frontend-authoritative validation, and generic admin UI behavior.

## Assumptions

- Runtime mode is active; the source document is treated as runtime source requirements, not a documentation-only target.
- The MM-546 Jira preset brief represents one independently testable Settings System maintenance-safety story.
- Existing Settings System code and tests may already satisfy some invariants, but this story requires traceable evidence and gaps must be closed with tests or implementation.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope Status | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-020 | `docs/Security/SettingsSystem.md` section 4, Non-Goals | The Settings System must remain a typed product-specific configuration plane and must not become a generic database editor, generic secret manager, raw environment editor, generic admin UI, provider-profile replacement, or frontend-authoritative validation layer. | In scope | FR-001, FR-010 |
| DESIGN-REQ-021 | `docs/Security/SettingsSystem.md` section 24, Migration and Deprecation | Renames, removals, and type changes must use explicit migration/deprecation behavior that preserves operator intent, rejects unsafe new writes, avoids ambiguous JSON reinterpretation, and exposes audit or diagnostic visibility. | In scope | FR-002, FR-003, FR-004, FR-005 |
| DESIGN-REQ-024 | `docs/Security/SettingsSystem.md` section 25, Testing Requirements | Regression coverage must prove catalog exposure, sensitive-field exclusion, SecretRef behavior, key/scope/type validation, effective value inheritance, reset, audit redaction, version conflicts, provider profile references, operational authorization, and catalog drift detection. | In scope | FR-006, FR-007, FR-008, FR-009 |
| DESIGN-REQ-027 | `docs/Security/SettingsSystem.md` section 28, Open Integration Points | Future integrations must preserve descriptor-driven exposure, scoped overrides, server-side validation, auditability, and secret-safe behavior. | In scope | FR-010 |
| DESIGN-REQ-028 | `docs/Security/SettingsSystem.md` section 29, Desired-State Invariants | The Settings System must enforce explicit editability, declared scopes, backend validation, no raw secret storage or plaintext readback, SecretRef validation/audit without plaintext, provider profile secret references, source explainability, reset inheritance, operator lock protection, authorized/audited operations, and intentional catalog changes. | In scope | FR-001, FR-006, FR-007, FR-008, FR-009 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Settings System MUST enforce that only explicitly catalog-exposed settings can be edited and MUST preserve non-goal boundaries against raw environment editing, generic database editing, generic secret management, generic admin UI behavior, and frontend-authoritative validation.
- **FR-002**: The Settings System MUST support a deterministic setting-rename migration path that maps old overrides to a new descriptor key while preserving effective values and exposing deprecation or audit visibility where helpful.
- **FR-003**: The Settings System MUST reject new writes to removed or deprecated setting keys while preserving or migrating existing stored values and explaining deprecated values in diagnostics.
- **FR-004**: The Settings System MUST require explicit migration behavior for setting type changes and MUST fail instead of ambiguously reinterpreting existing JSON values.
- **FR-005**: Migration and deprecation flows MUST provide audit or diagnostic evidence sufficient for maintainers to understand what changed without exposing secrets.
- **FR-006**: Regression coverage MUST verify catalog exposure, sensitive-field exclusion, declared scopes, unknown-key rejection, invalid-scope rejection, type validation, numeric/string constraints, and intentional catalog drift.
- **FR-007**: Regression coverage MUST verify SecretRef safety, including no generic raw secret storage, no plaintext readback, SecretRef validation and audit without plaintext, and provider profile references to secrets without embedding them.
- **FR-008**: Regression coverage MUST verify effective value source explainability, workspace/user override inheritance, reset-to-inheritance behavior, and operator lock enforcement.
- **FR-009**: Regression coverage MUST verify operational audit behavior, audit redaction, version conflict handling, and authorization gating for operations controls.
- **FR-010**: Future settings integration contracts MUST preserve descriptor-driven exposure, scoped overrides, server-side validation, auditability, and secret-safe behavior.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-546` and the canonical Jira preset brief for traceability.

### Key Entities

- **Setting Descriptor**: Declares a setting key, type, eligible scopes, editability, validation constraints, sensitivity/SecretRef behavior, and metadata used to expose settings safely.
- **Setting Override**: Stores operator/user/workspace intent for a setting at a declared scope and must be migrated, preserved, reset, or rejected according to descriptor rules.
- **Effective Setting Value**: The resolved setting value with source explanation after defaults, workspace/user overrides, operator locks, migration state, and SecretRef rules are applied.
- **Migration or Deprecation Rule**: A versioned rule that describes renames, removals, or type changes and the diagnostics/audit evidence required to preserve operator intent.
- **Settings Diagnostic**: A safe, redacted explanation of deprecated values, rejected writes, migration outcomes, catalog drift, or invariant failures.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A rename migration test proves at least one old override resolves to the new descriptor key with the same effective value and recorded audit or deprecation evidence.
- **SC-002**: A removal/deprecation test proves new writes to a removed key are rejected while existing intent remains visible through diagnostics or migration evidence.
- **SC-003**: A type-change test proves ambiguous reinterpretation of existing JSON values fails without an explicit migration.
- **SC-004**: Catalog and invariant regression coverage fails when an unsafe descriptor exposure, scope, validation, SecretRef, source explanation, reset, operator lock, audit, or operation authorization invariant is broken.
- **SC-005**: Integration or contract evidence demonstrates future settings consumers remain bound to descriptor-driven exposure, scoped overrides, server-side validation, auditability, and secret-safe behavior.
- **SC-006**: Traceability evidence preserves `MM-546`, the canonical Jira preset brief, and DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-024, DESIGN-REQ-027, and DESIGN-REQ-028 in MoonSpec artifacts and verification output.
