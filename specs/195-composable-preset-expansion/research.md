# Research: Composable Preset Expansion

## Input Classification

Decision: Treat MM-383 as a single-story runtime feature request.

Rationale: The Jira brief contains one user story for task platform engineers and a bounded acceptance set around composable preset expansion. The requested mode is runtime, and the brief points at task preset system requirements, so behavior is implemented in the catalog expansion service rather than as documentation-only work.

Alternatives considered: Treating the request as a broad declarative design was rejected because the brief already selects one independently testable story. Treating it as docs-only was rejected because runtime mode was explicitly selected.

## Source Document Availability

Decision: Use the preserved MM-383 Jira preset brief as the canonical source for DESIGN-REQ mappings.

Rationale: The Jira brief references `docs/Tasks/PresetComposability.md`, but that file is absent in the current checkout. The existing `docs/Tasks/TaskPresetsSystem.md` provides the current catalog baseline and is the canonical documentation target.

Alternatives considered: Blocking on the missing source document was rejected because the trusted Jira issue already includes the source requirements needed for this story.

## Include Representation

Decision: Represent preset version entries as a union of default concrete steps and `kind: include` entries with `slug`, pinned `version`, `alias`, optional `scope`, and `inputMapping`.

Rationale: This preserves existing concrete-step payloads while making composition explicit and deterministic. Existing presets without `kind` remain concrete steps.

Alternatives considered: Introducing a separate include table was rejected because MM-383 requires no new persistent storage and composition can be represented in the existing JSON step payload.

## Expansion Strategy

Decision: Resolve includes recursively inside `TaskTemplateCatalogService.expand_template`, producing the existing flat `steps[]` response plus `composition` and per-step `presetProvenance`.

Rationale: The catalog service already owns expansion, input validation, deterministic IDs, capabilities, recents, and warnings. Resolving before returning steps keeps nested semantics away from executor boundaries.

Alternatives considered: Deferring include resolution to the plan executor was rejected because the brief requires compile-time control-plane behavior and no runtime execution semantic changes.

## Failure Handling

Decision: Fail fast with `TaskTemplateValidationError` messages that include a human-readable include path.

Rationale: Existing service tests already assert validation exceptions. Path-bearing messages satisfy cycle and limit diagnostics without adding a new error hierarchy.

Alternatives considered: Returning partial expansion warnings was rejected because missing, inactive, incompatible, cyclic, or scope-invalid includes must not produce executable steps.

## Test Strategy

Decision: Add focused pytest service tests covering successful include flattening, provenance/composition metadata, scope rejection, cycle path rejection, inactive or incompatible child rejection, and flattened limit enforcement.

Rationale: The task template catalog service is the real boundary used by API routes and seed synchronization, and unit-level async database tests already exist for it. Full integration tests are unchanged unless Docker-backed CI is available.

Alternatives considered: Browser/UI tests were rejected because the selected story is expansion-contract behavior, not catalog UI rendering.
