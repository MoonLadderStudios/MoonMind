# MoonSpec Breakdown — Settings System

**Source Document:** `docs/Security/SettingsSystem.md`
**Output Mode:** `jira`
**Story Count:** 13
**Coverage:** Sections 1–29 of the declarative design.

> Sections 1–4 (Summary, Document Boundaries, Goals, Non-Goals) and Section 28
> (Open Integration Points) frame scope; their requirements are absorbed into
> the 13 stories below. Example flows in §27 are exercised as acceptance
> scenarios within the relevant stories.

---

## STORY-001 — Backend-owned settings catalog registry and descriptor contract

**Source:** `docs/Security/SettingsSystem.md` §5.1, §5.9, §7.1, §7.2, §8, §26

Establish the backend-owned `SettingsRegistry` / `SettingsCatalogBuilder` that
emits typed `SettingDescriptor` records covering every field in §8.1. Setting
keys are stable dotted identifiers, never overloaded across scopes, and only
fields with explicit `moonmind` metadata become catalog entries. The registry
output is the single source of truth consumed by Settings UI, API clients, CLI
tooling, tests, diagnostics, onboarding flows, and documentation generators.

**Acceptance:**
- Catalog includes every explicitly exposed eligible setting and excludes
  everything else by default.
- Setting keys are unique, URL/JSON safe, stable across releases.
- Descriptors carry every required field from §8.1.
- A snapshot test detects accidental catalog drift.
- Removing or renaming a descriptor without a migration entry fails the build.

**Dependencies:** none

---

## STORY-002 — Eligibility rules and sensitive-name heuristics enforcement

**Source:** `docs/Security/SettingsSystem.md` §5.2, §7.8, §9, §22

Filter what may surface in Settings. A field is eligible only with explicit
`expose: true`, a non-plaintext-sensitive type, at least one editable scope, a
supported UI representation, server-side validation, no env-var mutation, and
no bypass of Secrets or Provider Profiles. Sensitive-name heuristics
(`secret`, `token`, `password`, `api_key`, `apikey`, `credential`,
`private_key`, `refresh`, `oauth`) cannot become plaintext inputs and must be
routed through SecretRef pickers or the Managed Secrets flow.

**Acceptance:**
- Settings without explicit metadata never reach the catalog.
- Sensitive-name fields cannot be exposed as plaintext (proven by tests).
- The §9.2 generic UI control mapping is enforced.
- Client-supplied descriptor metadata is ignored for authorization/validation.
- Ineligible classes (raw secrets, OAuth state, credential values, deployment
  infra, profile-owned, ops-owned) cannot be exposed as ordinary settings.

**Dependencies:** STORY-001

---

## STORY-003 — Scoped overrides persistence with sparse storage and reset

**Source:** `docs/Security/SettingsSystem.md` §5.4, §7.3, §7.4, §11, §27.3

Persist scoped overrides in the `settings_overrides` table per §11.1 with
unique `(scope, workspace_id, user_id, key)` keys, schema/value versions, and
audit columns. Defaults remain immutable, overrides are sparse, and reset
deletes only the override row.

**Acceptance:**
- Workspace and user overrides round-trip through the store independently.
- Reset deletes only the relevant row and returns the inherited effective value.
- Size limits and schema validation are enforced before persistence.
- Stored payloads never contain secret plaintext.
- Optimistic concurrency uses `value_version` and rejects stale writes.

**Dependencies:** STORY-001

---

## STORY-004 — Effective-value resolver with source explanation and operator locks

**Source:** `docs/Security/SettingsSystem.md` §5.5, §7.5–7.7, §10, §16.4, §27.1

Implement the resolver per §10. Default chain: `built-in default <
config/env < workspace override < user override`. Operator-locked chain adds
`operator_lock` last; locks force read-only. Each effective value carries a
`source` label drawn from §7.6 and a diagnostic for every missing/null/blocked
state in §10.5.

**Acceptance:**
- Workspace overrides shadow defaults, user overrides shadow workspace,
  operator locks shadow user.
- Operator-locked descriptors are read-only with a populated `read_only_reason`.
- Each diagnostic state from §10.5 produces a distinct, actionable explanation.
- The resolver answers every question listed in §5.5.

**Dependencies:** STORY-003

---

## STORY-005 — Server-side validation and cross-setting policy enforcement

**Source:** `docs/Security/SettingsSystem.md` §5.7, §16.3, §18

Centralize all writes through a validator that enforces existence, exposure,
scope, authorization, type, constraints, SecretRef syntax, dependency
satisfaction, and workspace policy. Cross-setting rules (profile selectors,
canary/feature pairs, allowed runtimes, allowed SecretRef backends, ops vs.
maintenance modes) must each be regression-tested. Validation runs at every
boundary listed in §18.3 and never silently falls back.

**Acceptance:**
- Type validation passes for all listed types and rejects mismatches.
- Numeric/string constraints are enforced at write and preview time.
- §18.2 cross-setting rules each have a regression test.
- Validation runs at all boundaries from §18.3.

**Dependencies:** STORY-001, STORY-003

---

## STORY-006 — Settings HTTP API surface

**Source:** `docs/Security/SettingsSystem.md` §12

Expose the documented endpoints: catalog, effective, update (PATCH), reset
(DELETE), validate, preview, and audit, with structured error envelopes
covering all error codes in §12.7.

**Acceptance:**
- All listed endpoints exist with documented query parameters/bodies.
- Catalog responses are grouped into the three top-level sections.
- Updates use `expected_versions` and emit `version_conflict` on stale writes.
- Errors match §12.7 with `error`, `message`, `key`, `scope`, `details`.
- Audit reads honor descriptor redaction policy.

**Dependencies:** STORY-001, STORY-003, STORY-004, STORY-005

---

## STORY-007 — Authorization model with permission-scoped reads and writes

**Source:** `docs/Security/SettingsSystem.md` §5.6, §20, §22

Enforce least-privilege authorization across every Settings boundary using the
permission set in §20.1, mapped to the roles in §20.2. Authorization runs on
every write and every sensitive metadata read; hidden UI is never the security
boundary. Operator locks block ordinary user/workspace writes.

**Acceptance:**
- Each permission is checked exactly where required and tests cover positive
  and negative cases per role.
- Operator locks block ordinary writes with `operator_locked`.
- Auditor role reads audit logs without plaintext access.
- CSRF/session protections are enforced on all settings APIs.

**Dependencies:** STORY-006

---

## STORY-008 — Settings audit events with descriptor-aware redaction

**Source:** `docs/Security/SettingsSystem.md` §11.2, §21

Persist setting-change audit events per §11.2. Capture every field listed in
§21.1, redact every class listed in §21.2, and surface the diagnostic answers
listed in §21.3. SecretRef values are recorded only when policy permits.

**Acceptance:**
- Every successful and failed write produces an audit event with documented
  columns.
- Redaction is descriptor-driven, with unit tests for each sensitivity class.
- Audit reads obey the `settings.audit.read` permission.
- Each §21.3 diagnostic surface has a backing test case.

**Dependencies:** STORY-006

---

## STORY-009 — Secrets integration with SecretRef pickers and no-plaintext readback

**Source:** `docs/Security/SettingsSystem.md` §5.3, §7.9, §13.5, §13.6, §14, §22, §27.2

Wire Settings to the Secrets System without redefining secret semantics.
Sensitive settings store SecretRef pointers only; generic overrides reject raw
credentials. Managed secret create/replace/rotate/disable/re-enable/delete/
validate/inspect/copy flows are write-only with no reveal action. SecretRef
pickers expose metadata and the SecretRef value but never plaintext. Validation
resolves in memory, runs a provider/integration-aware check, discards
plaintext, stores redacted metadata, and returns redacted diagnostics.

**Acceptance:**
- Sensitive values cannot land in `settings_overrides` (proven by tests).
- Plaintext is never returned by any settings endpoint after submission.
- Broken/missing/disabled/revoked SecretRefs surface explicit diagnostics and
  block affected launches where appropriate.
- Secret usage views show references and object names only.
- §27.2 (Add GitHub Token) end-to-end scenario passes, including the
  fine-grained PAT permission probe and SecretRef-alias resolution.

**Dependencies:** STORY-001, STORY-005

---

## STORY-010 — Provider Profiles section integration with role-based SecretRef binding and readiness

**Source:** `docs/Security/SettingsSystem.md` §2.3, §10.3, §15

Treat Provider Profiles as first-class resources inside `providers-secrets`.
Provide structured profile forms covering everything in §15.1, role-aware
SecretRef pickers per §15.2, and readiness reporting per §15.3. User/Workspace
settings may reference defaults but never inline launch semantics. Profile
materialization never persists resolved plaintext.

**Acceptance:**
- Profile create/update/delete/validate/enable/disable/select-default flows
  respect `provider_profiles.read|write` permissions.
- Profile selectors validate that they reference enabled, ready profiles.
- Readiness reports each contributing factor and surfaces broken SecretRef or
  OAuth volume state.
- A regression test proves no plaintext credential ever lands in
  `settings_overrides` from a profile binding.

**Dependencies:** STORY-009

---

## STORY-011 — Operations section with command semantics, confirmation, and audit

**Source:** `docs/Security/SettingsSystem.md` §2.4, §6.3, §13.7, §17, §27.4

Build the Operations section as discoverable from Settings but modeled as
authorized commands with the metadata listed in §17.2. Disruptive actions from
§17.3 require explicit confirmation. Operations subsystems remain authoritative
for worker and runtime semantics; Settings only mediates discovery and command
issuance.

**Acceptance:**
- §27.4 (Pause Workers) end-to-end scenario passes.
- Operations endpoints honor `operations.read` and `operations.invoke`.
- Idempotency keys prevent duplicate execution under retry.
- Confirmation is enforced for the disruptive action classes from §17.3.

**Dependencies:** STORY-007, STORY-008

---

## STORY-012 — Change application semantics with apply modes and reload events

**Source:** `docs/Security/SettingsSystem.md` §13, §19

Make every setting declare an apply mode from §19.1. Emit structured
`setting_changed` events on commit so Mission Control, task creation, profile
manager, workers, and operations subsystems can react. Restart-required
settings show pending vs. active state until activation completes.

**Acceptance:**
- Each descriptor declares an apply mode (CI fails when missing).
- Successful writes emit a `setting_changed` event with the §19.2 schema.
- Subscribers refresh on the appropriate event class.
- Restart-required settings display pending vs. active state.

**Dependencies:** STORY-006

---

## STORY-013 — Migration, deprecation, backup integrity, and snapshot drift coverage

**Source:** `docs/Security/SettingsSystem.md` §8.4, §23, §24, §25, §29

Cover the catalog’s longevity contract: rename/remove/type-change migrations
preserve effective values and never silently lose operator intent; backups
include overrides and audit data but never raw secrets; restored references
show clearly when broken; and the §25 testing matrix is implemented in full,
including the snapshot test for accidental catalog drift.

**Acceptance:**
- Rename migration preserves effective values via mapped overrides.
- Removal rejects new writes, preserves/migrates existing values, and surfaces
  deprecation diagnostics.
- Type-change migration prevents ambiguous JSON reinterpretation.
- Restore-from-backup integration test surfaces broken SecretRef/profile/
  OAuth references as diagnostics.
- The §25.20 snapshot test detects accidental catalog drift and the full §25
  testing matrix passes.

**Dependencies:** STORY-001, STORY-003, STORY-009, STORY-010

---

## Coverage Map

| Doc Section | Covered By |
|---|---|
| 1 Summary | (framing — covered across all stories) |
| 2.1 Settings owns | STORY-001..013 (system scope) |
| 2.2 Secrets ownership boundary | STORY-009 |
| 2.3 Provider Profiles boundary | STORY-010 |
| 2.4 Operations boundary | STORY-011 |
| 2.5 Runtime strategy boundary | STORY-005, STORY-010 |
| 3 Goals | (absorbed across stories) |
| 4 Non-Goals | (enforced by STORY-002, STORY-009, STORY-010, STORY-011) |
| 5.1 Backend-owned truth | STORY-001 |
| 5.2 Explicit exposure | STORY-002 |
| 5.3 References over secrets | STORY-009 |
| 5.4 Scoped overrides not mutable defaults | STORY-003 |
| 5.5 Explainability | STORY-004 |
| 5.6 Least privilege | STORY-007 |
| 5.7 Fail fast | STORY-005 |
| 5.8 UI as a safe control plane | STORY-006, STORY-007 |
| 5.9 Durable contracts | STORY-001 |
| 6 Settings page topology | STORY-001 (taxonomy), STORY-009/010/011 (sections) |
| 7.1 Setting key | STORY-001 |
| 7.2 Setting descriptor | STORY-001 |
| 7.3 Scope | STORY-003 |
| 7.4 Override | STORY-003 |
| 7.5 Effective value | STORY-004 |
| 7.6 Source | STORY-004 |
| 7.7 Lock | STORY-004 |
| 7.8 Eligibility | STORY-002 |
| 7.9 SecretRef setting | STORY-009 |
| 8 Settings catalog contract | STORY-001 |
| 9 Eligibility rules | STORY-002 |
| 10 Resolution model | STORY-004 (10.1, 10.2, 10.5), STORY-010 (10.3), STORY-009 (10.4) |
| 11 Persistence model | STORY-003 (11.1, 11.3, 11.4), STORY-008 (11.2) |
| 12 API contract | STORY-006 |
| 13 UI contract | STORY-006, STORY-009 (13.5/13.6), STORY-011 (13.7), STORY-012 (13.4) |
| 14 Secrets integration | STORY-009 |
| 15 Provider profiles integration | STORY-010 |
| 16 User / workspace settings | STORY-002 (16.1), STORY-005 (16.3), STORY-004 (16.4) |
| 17 Operations settings | STORY-011 |
| 18 Validation model | STORY-005 |
| 19 Change application semantics | STORY-012 |
| 20 Authorization model | STORY-007 |
| 21 Audit and observability | STORY-008 |
| 22 Security requirements | STORY-002, STORY-007, STORY-008, STORY-009, STORY-010, STORY-011 |
| 23 Backup and recovery | STORY-013 |
| 24 Migration and deprecation | STORY-013 |
| 25 Testing requirements | STORY-013 (with cross-cutting evidence in stories 001..012) |
| 26 Suggested internal components | STORY-001 (registry/builder), STORY-003 (override store), STORY-004 (resolver), STORY-005 (validator), STORY-008 (audit writer), STORY-012 (change publisher), STORY-007 (authorization service), STORY-010 (profiles panel), STORY-011 (operations panel), STORY-009 (secret picker / managed secrets panel) |
| 27.1 Change workspace default runtime | STORY-004 |
| 27.2 Add GitHub token | STORY-009 |
| 27.3 Reset user override | STORY-003 |
| 27.4 Pause workers | STORY-011 |
| 28 Open integration points | (intentional extension surface; constrained by STORY-001 contract) |
| 29 Desired-state invariants | STORY-001..013 collectively (1↔001, 2↔005, 3↔005, 4↔009, 5↔009, 6↔009, 7↔010, 8↔004, 9↔003, 10↔004/007, 11↔011, 12↔013) |

---

## Suggested Linear Blocker Chain

`STORY-001 → STORY-002 → STORY-003 → STORY-004 → STORY-005 → STORY-006 →
STORY-007 → STORY-008 → STORY-009 → STORY-010 → STORY-011 → STORY-012 →
STORY-013`

Each story is independently testable; the chain reflects the natural buildup
of contract → persistence → resolution → validation → API → authorization →
audit → secrets/profiles/operations → reload → migration safety.
