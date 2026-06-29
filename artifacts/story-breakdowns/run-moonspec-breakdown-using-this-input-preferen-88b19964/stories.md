# Skill Input Schema UI Generation Story Breakdown

- Source: `docs/Steps/SkillInputSchemaUIGeneration.md`
- Source document class: `canonical-declarative`
- Source Jira issue key: `MM-1047`
- Extracted at: `2026-06-29T23:50:40Z`
- Output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The design defines target-state support for optional structured Skill inputs. MoonMind should parse Skill frontmatter into the same normalized capability input contract used by presets, expose those contracts through catalog APIs with content evidence, render Skill-step fields through the shared Create page schema-form renderer, validate submitted inputs in the backend, hand compact validated inputs to runtime adapters, and enforce untrusted-metadata security, diagnostics, observability, and stale-draft behavior. Skills without inputSchema remain selectable and executable through instruction-driven fallback behavior.

## Coverage Points

- `DESIGN-REQ-001` (requirement) Unified capability input contract: One normalized contract shape covers Skills, Presets, and Tools.
- `DESIGN-REQ-002` (constraint) Schema-less Skills remain usable: Skills without inputSchema remain selectable and executable through instructions/context.
- `DESIGN-REQ-003` (requirement) Shared parser/normalizer: Shared modules parse and normalize Skill metadata into capability contracts.
- `DESIGN-REQ-004` (artifact) Skill source and content evidence: All eligible Skill sources produce evidence-bound contracts.
- `DESIGN-REQ-005` (constraint) Non-brittle error policy: Malformed or unsupported optional metadata degrades safely with diagnostics unless strict policy applies.
- `DESIGN-REQ-006` (requirement) Schema subset and widgets: Supported schema signals map to registered reusable widgets and safe fallbacks.
- `DESIGN-REQ-007` (security) uiSchema presentation boundary: uiSchema is presentation-only and cannot affect validation or execution.
- `DESIGN-REQ-008` (requirement) Create page Skill flow: Skill steps render, validate, and store inputs through the shared authoring flow.
- `DESIGN-REQ-009` (state-model) Deterministic defaults: Defaults resolve in a fixed order and never carry secrets.
- `DESIGN-REQ-010` (requirement) Backend-owned validation: Backend validates against content-bound inputSchema and returns field-addressable errors.
- `DESIGN-REQ-011` (integration) Compact runtime handoff: Runtime receives refs, digests, and validated inputs without adapter re-parse.
- `DESIGN-REQ-012` (integration) Catalog/API exposure: Skill catalog exposes normalized contracts or contract refs.
- `DESIGN-REQ-013` (artifact) Persistence strategy: Deployment artifacts and file-backed discovery carry normalized contract metadata safely.
- `DESIGN-REQ-014` (security) Security/trust boundaries: Schemas are untrusted; safe parsing, no code/remote refs, registered widgets, and sanitized rendering are required.
- `DESIGN-REQ-015` (observability) Diagnostics/observability: Structured diagnostics and anonymized metrics/traces cover contract parsing/rendering/validation events.
- `DESIGN-REQ-016` (state-model) Draft staleness: Drafts compare digests, preserve values, and validate against current or pinned contracts.
- `DESIGN-REQ-017` (migration) Legacy and preset compatibility: Readers normalize legacy args while authoring writes inputs; preset APIs remain compatible.
- `DESIGN-REQ-018` (non-goal) Explicit non-goals: Avoid mandatory schemas, per-Skill custom components, executable metadata, remote UI, lost inference, or Skill/Tool conflation.

## Ordered Story Candidates

### STORY-001: Normalize Skill input contracts through shared capability parsing

- Short name: `skill-input-contracts`
- Source reference: `docs/Steps/SkillInputSchemaUIGeneration.md`
- Sections: Purpose, Design Goals, Core Contract, Skill Authoring Shape, Parser Pipeline, Shared Code Placement
- Claim IDs: `docs/Steps/SkillInputSchemaUIGeneration.md#C001-purpose`, `docs/Steps/SkillInputSchemaUIGeneration.md#C002-goals`, `docs/Steps/SkillInputSchemaUIGeneration.md#C005-core-contract`, `docs/Steps/SkillInputSchemaUIGeneration.md#C006-authoring`, `docs/Steps/SkillInputSchemaUIGeneration.md#C008-parser`, `docs/Steps/SkillInputSchemaUIGeneration.md#C020-shared-code`
- Coverage IDs: `DESIGN-REQ-001`, `DESIGN-REQ-003`, `DESIGN-REQ-018`
- Dependencies: None
- Independent test: Unit tests feed Skill frontmatter and preset-equivalent schemas through the shared normalizer and assert equivalent contracts, deterministic digests, camelCase output, preserved safe hints, and diagnostics.

Acceptance criteria:
- SKILL.md with inputSchema/uiSchema/defaults produces a kind=skill CapabilityInputContract with digests, source, and diagnostics.
- input_schema/ui_schema source casing is accepted while API output remains inputSchema/uiSchema.
- Non-object schemas are diagnosed for generated fields without invalidating the Skill itself.
- The same logical Skill and Preset schema normalize to equivalent renderer-facing contract fields.

Needs clarification: None

### STORY-002: Expose Skill input contracts from catalog and persisted Skill evidence

- Short name: `skill-catalog-contracts`
- Source reference: `docs/Steps/SkillInputSchemaUIGeneration.md`
- Sections: Parsing Sources, Catalog And API Design, Persistence Strategy, Target-State Implementation Constraints
- Claim IDs: `docs/Steps/SkillInputSchemaUIGeneration.md#C007-sources`, `docs/Steps/SkillInputSchemaUIGeneration.md#C018-catalog`, `docs/Steps/SkillInputSchemaUIGeneration.md#C019-persistence`, `docs/Steps/SkillInputSchemaUIGeneration.md#C024-acceptance`
- Coverage IDs: `DESIGN-REQ-004`, `DESIGN-REQ-012`, `DESIGN-REQ-013`, `DESIGN-REQ-017`
- Dependencies: `STORY-001`
- Independent test: Catalog/service tests create deployment-stored and file-backed Skills with and without schemas and assert normalized contract exposure, source evidence, digests, diagnostics, empty schemas, and no checked-in file mutation.

Acceptance criteria:
- Catalog responses include inputSchema, uiSchema, defaults, contractDigest, diagnostics, and source/content evidence.
- Large schemas can be exposed through hasInputSchema and inputContractRef with a detailed endpoint.
- AgentSkillsService.update_skill_content stores extracted contract metadata with content artifacts.
- Preset catalog responses remain API-compatible while sharing contract behavior.

Needs clarification: None

### STORY-003: Render Skill-step inputs on the Create page with schema and fallback flows

- Short name: `skill-create-ui`
- Source reference: `docs/Steps/SkillInputSchemaUIGeneration.md`
- Sections: Existing Preset Pattern To Reuse, Schema Subset For Generated Skill Fields, UI Schema Handling, Widget Registry, Create Page Flow, Fallback UI When No Skill Schema Exists, Defaulting Rules, Acceptance Criteria
- Claim IDs: `docs/Steps/SkillInputSchemaUIGeneration.md#C004-preset-pattern`, `docs/Steps/SkillInputSchemaUIGeneration.md#C010-schema-subset`, `docs/Steps/SkillInputSchemaUIGeneration.md#C011-ui-schema`, `docs/Steps/SkillInputSchemaUIGeneration.md#C012-widgets`, `docs/Steps/SkillInputSchemaUIGeneration.md#C013-create-flow`, `docs/Steps/SkillInputSchemaUIGeneration.md#C014-fallback`, `docs/Steps/SkillInputSchemaUIGeneration.md#C015-defaults`, `docs/Steps/SkillInputSchemaUIGeneration.md#C024-acceptance`
- Coverage IDs: `DESIGN-REQ-002`, `DESIGN-REQ-006`, `DESIGN-REQ-007`, `DESIGN-REQ-008`, `DESIGN-REQ-009`, `DESIGN-REQ-017`, `DESIGN-REQ-018`
- Dependencies: `STORY-002`
- Independent test: Frontend tests render equivalent Skill and Preset schemas through the same schema-form path and assert field parity, default precedence, local validation, unknown-widget fallback preserving values, schema-less fallback, and inputs storage.

Acceptance criteria:
- A Skill with inputSchema renders generated fields through the preset-shared schema-form renderer.
- Supported field types and registered integration widgets render correctly with safe fallbacks.
- uiSchema can only select registered presentation hints and cannot create validation semantics.
- A Skill without inputSchema shows instructions/context fallback and remains executable.
- New drafts write step.skill.inputs and older args/selectedSkillArgs load into inputs.

Needs clarification: None

### STORY-004: Validate Skill-step inputs in the backend and hand off compact runtime inputs

- Short name: `skill-input-validation`
- Source reference: `docs/Steps/SkillInputSchemaUIGeneration.md`
- Sections: Backend Validation, Runtime Handoff, Create Page Flow, Target-State Implementation Constraints, Acceptance Criteria
- Claim IDs: `docs/Steps/SkillInputSchemaUIGeneration.md#C013-create-flow`, `docs/Steps/SkillInputSchemaUIGeneration.md#C016-backend-validation`, `docs/Steps/SkillInputSchemaUIGeneration.md#C017-runtime`, `docs/Steps/SkillInputSchemaUIGeneration.md#C024-acceptance`
- Coverage IDs: `DESIGN-REQ-010`, `DESIGN-REQ-011`, `DESIGN-REQ-017`
- Dependencies: `STORY-001`, `STORY-002`
- Independent test: Backend and workflow/adapter-boundary tests submit valid, invalid, and legacy Skill-step payloads and assert normalized values, field errors, preserved values, safe defaults, and compact runtime payloads without SKILL.md re-parse.

Acceptance criteria:
- Backend validation resolves selected Skill content evidence before loading the input contract.
- Validation uses inputSchema, not uiSchema, and returns field-addressable errors under steps[n].skill.inputs.
- Start/API workflow paths cannot bypass required Skill input validation.
- Runtime receives skill name, contentRef, contentDigest, inputContractDigest, and validated inputs.
- Adapters do not re-parse SKILL.md to discover inputs.

Needs clarification: None

### STORY-005: Enforce Skill schema trust boundaries, diagnostics, observability, and stale-draft behavior

- Short name: `skill-schema-guardrails`
- Source reference: `docs/Steps/SkillInputSchemaUIGeneration.md`
- Sections: Frontmatter Error Policy, Security And Trust Boundaries, Diagnostics, Observability, Versioning And Draft Staleness, Acceptance Criteria
- Claim IDs: `docs/Steps/SkillInputSchemaUIGeneration.md#C009-errors`, `docs/Steps/SkillInputSchemaUIGeneration.md#C021-security`, `docs/Steps/SkillInputSchemaUIGeneration.md#C022-diagnostics`, `docs/Steps/SkillInputSchemaUIGeneration.md#C023-staleness`, `docs/Steps/SkillInputSchemaUIGeneration.md#C024-acceptance`
- Coverage IDs: `DESIGN-REQ-005`, `DESIGN-REQ-014`, `DESIGN-REQ-015`, `DESIGN-REQ-016`
- Dependencies: `STORY-001`, `STORY-003`, `STORY-004`
- Independent test: Security, service, and UI tests cover malformed YAML, oversized/invalid schemas, secret defaults, remote refs, unknown hints/widgets, sanitized descriptions, diagnostics visibility, anonymized metrics, digest mismatch, and pinned validation.

Acceptance criteria:
- Safe YAML parsing and size limits are enforced.
- Secret-like defaults are rejected or redacted before UI exposure.
- Schema metadata never executes code, fetches remote refs by default, or loads arbitrary components.
- Diagnostics are structured and user-safe.
- Observability records parse/render/validation/fallback/digest events without raw values.
- Draft reload/submit preserves values and revalidates against current or pinned contracts.

Needs clarification: None

## Coverage Matrix

- `DESIGN-REQ-001` -> `STORY-001`
- `DESIGN-REQ-002` -> `STORY-003`
- `DESIGN-REQ-003` -> `STORY-001`
- `DESIGN-REQ-004` -> `STORY-002`
- `DESIGN-REQ-005` -> `STORY-005`
- `DESIGN-REQ-006` -> `STORY-003`
- `DESIGN-REQ-007` -> `STORY-003`
- `DESIGN-REQ-008` -> `STORY-003`
- `DESIGN-REQ-009` -> `STORY-003`
- `DESIGN-REQ-010` -> `STORY-004`
- `DESIGN-REQ-011` -> `STORY-004`
- `DESIGN-REQ-012` -> `STORY-002`
- `DESIGN-REQ-013` -> `STORY-002`
- `DESIGN-REQ-014` -> `STORY-005`
- `DESIGN-REQ-015` -> `STORY-005`
- `DESIGN-REQ-016` -> `STORY-005`
- `DESIGN-REQ-017` -> `STORY-002`, `STORY-003`, `STORY-004`
- `DESIGN-REQ-018` -> `STORY-001`, `STORY-003`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C001-purpose` -> `STORY-001`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C002-goals` -> `STORY-001`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C003-non-goals` -> `STORY-001`, `STORY-003`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C004-preset-pattern` -> `STORY-003`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C005-core-contract` -> `STORY-001`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C006-authoring` -> `STORY-001`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C007-sources` -> `STORY-002`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C008-parser` -> `STORY-001`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C009-errors` -> `STORY-005`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C010-schema-subset` -> `STORY-003`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C011-ui-schema` -> `STORY-003`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C012-widgets` -> `STORY-003`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C013-create-flow` -> `STORY-003`, `STORY-004`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C014-fallback` -> `STORY-003`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C015-defaults` -> `STORY-003`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C016-backend-validation` -> `STORY-004`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C017-runtime` -> `STORY-004`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C018-catalog` -> `STORY-002`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C019-persistence` -> `STORY-002`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C020-shared-code` -> `STORY-001`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C021-security` -> `STORY-005`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C022-diagnostics` -> `STORY-005`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C023-staleness` -> `STORY-005`
- `docs/Steps/SkillInputSchemaUIGeneration.md#C024-acceptance` -> `STORY-002`, `STORY-003`, `STORY-004`, `STORY-005`

## Dependencies Between Stories

- `STORY-001` depends on None
- `STORY-002` depends on `STORY-001`
- `STORY-003` depends on `STORY-002`
- `STORY-004` depends on `STORY-001`, `STORY-002`
- `STORY-005` depends on `STORY-001`, `STORY-003`, `STORY-004`

## Out Of Scope

- No spec.md files, specs/ directories, implementation plans, tasks, code changes, Jira issue creation, PR creation, or publishing during breakdown.
- No mandatory inputSchema for Skills.
- No executable schema/UI metadata or remote component loading.
- No custom React component per Skill.
- No conversion of Skills into Tools.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
