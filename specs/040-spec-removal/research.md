# Research: Canonical Workflow Surface Naming

## Decision 1: Migration scope is documentation/specs only

- **Decision**: Restrict implementation planning and execution to `docs/` and `specs/` artifacts listed in `docs/SpecRemovalPlan.md`; do not edit runtime code, deployment manifests, or test/runtime fixture files.
- **Rationale**: The request is explicitly a planning-only pass focused on canonical naming alignment and includes explicit scope restrictions in the plan source.
- **Alternatives considered**:
  - Full-system migration including runtime files now: rejected because it broadens blast radius and violates the stated scope constraints.
  - Partial runtime migration with docs-only artifacts: rejected because it creates inconsistent naming across docs and runtime, undermining the migration intent.

## Decision 2: Canonical token map comes from a single source

- **Decision**: Treat the mapping in `docs/SpecRemovalPlan.md` as the source of truth for token replacements.
- **Rationale**: A single canonical map reduces drift and prevents conflicting migration strategies across participating features.
- **Alternatives considered**:
  - Per-file or per-author mapping: rejected due to risk of inconsistent naming and incomplete migration.
  - Auto-generated semantic mapping tools only: rejected without a hardcoded baseline because legacy naming may carry historical intent that must be preserved selectively.

## Decision 3: Legacy naming retention is appendix-only

- **Decision**: Keep intentional legacy terms only in explicit historical/context sections with traceability, primarily within `docs/SpecRemovalPlan.md`.
- **Rationale**: The feature requires operational guidance to be canonical while preserving auditability for migration rationale.
- **Alternatives considered**:
  - Keep aliases in active docs for transition convenience: rejected because it introduces naming ambiguity.
  - Remove all legacy terms including historical notes: rejected because it loses migration context needed for later follow-up planning.

## Decision 4: Validation uses grep-based token discovery with allow-list exception

- **Decision**: Use explicit token scanning to verify the migration scope and allow only approved legacy references outside active operational guidance.
- **Rationale**: Legacy-token verification provides deterministic pass/fail criteria and supports a verifiable handoff artifact.
- **Alternatives considered**:
  - Manual review only: rejected due to weak repeatability and hidden misses.
  - Full static analysis AST parsing: rejected as overkill for markdown-first artifacts and higher maintenance cost.

## Decision 5: No aliasing language in active operational docs

- **Decision**: Avoid phrases like “old name/new alias” in guides, runbooks, and plan/spec bodies, replacing them with canonical names only.
- **Rationale**: The objective is canonical terminology adoption and migration clarity.
- **Alternatives considered**:
  - Continue dual naming prose during transition: rejected because it preserves confusion and invites mixed usage.
  - Rename without historical context anywhere: rejected because it may erase the rationale for controlled exceptions.

## Decision 6: Requirements traceability is mandatory for all DOC-REQ entries

- **Decision**: Generate one `requirements-traceability.md` row per `DOC-REQ-*` with concrete validation strategy.
- **Rationale**: The pipeline requires traceability gates, and the current feature explicitly introduces ten `DOC-REQ-*` entries.
- **Alternatives considered**:
  - Skip traceability because plan scope is docs: rejected as this contradicts repository orchestration requirements.
  - Single aggregate mapping row: rejected for insufficient audit granularity.

## Decision 7: Runtime-mode and docs-mode behavior remain aligned by explicit non-production scope

- **Decision**: Document the behavior alignment by explicitly stating that runtime assets are excluded and runtime behavior remains unchanged in this step.
- **Rationale**: This avoids false assumptions when users later run this feature under runtime orchestration mode.
- **Alternatives considered**:
  - Force runtime-mode checks despite no runtime edits: rejected because it would introduce false failures.
  - Omit mode alignment language: rejected because it diverges from current orchestration instructions.

