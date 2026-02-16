# Research: Skills-First Spec Automation Pipeline

## Decision 1: Preserve Legacy Phase Values, Normalize Skills Metadata
- **Decision**: Keep existing persisted `speckit_*` phase values and add normalized skills metadata extraction in runtime/API layers.
- **Rationale**: Avoids destructive schema migration while aligning behavior with umbrella 015 skills-first semantics.
- **Alternatives Considered**:
  - Replace all phase values with new generic values immediately: rejected due to migration risk and backward compatibility impact.
  - Leave phase responses unchanged: rejected because it fails 015 metadata requirements.

## Decision 2: API-Level Skills Metadata Exposure
- **Decision**: Expose `selected_skill`, `execution_path`, `used_skills`, `used_fallback`, and `shadow_mode_requested` in `SpecAutomationPhaseState`.
- **Rationale**: Operators and clients can inspect stage policy outcomes directly without parsing free-form metadata payloads.
- **Alternatives Considered**:
  - Keep metadata embedded-only: rejected because clients would need brittle metadata parsing.

## Decision 3: Legacy Defaults for Missing Metadata
- **Decision**: When legacy `speckit_*` phases have no explicit metadata, default to `selectedSkill=speckit` and `executionPath=skill`.
- **Rationale**: Preserves historical behavior while making skills-path semantics explicit.
- **Alternatives Considered**:
  - Return null for missing values: rejected because it weakens observability and breaks deterministic interpretation.

## Decision 4: Contract-Level Stage Coverage Expansion
- **Decision**: Extend contract phase enum to include `speckit_analyze` and `speckit_implement` while preserving existing values.
- **Rationale**: Aligns 002 contracts with umbrella stage coverage goals without forcing immediate runtime pipeline rewrites.
- **Alternatives Considered**:
  - Keep contract limited to specify/plan/tasks: rejected because it conflicts with 015 stage contract goals.

## Decision 5: Fast-Path Runtime Profile Alignment
- **Decision**: Update quickstart/docs to standardize Codex volume auth + Google Gemini embedding defaults for automation contexts.
- **Rationale**: Ensures operational guidance for 002 is consistent with umbrella 015 startup expectations.
- **Alternatives Considered**:
  - Leave 002 quickstart unchanged: rejected due to drift from umbrella requirements.

## Decision 6: Scope Validation Script Availability
- **Decision**: Treat missing `.specify/scripts/bash/validate-implementation-scope.sh` as an operational blocker to strict scope-gate automation; continue with explicit manual scope validation and reporting.
- **Rationale**: The script is required by the orchestration workflow but currently absent in repository scripts.
- **Alternatives Considered**:
  - Halt all implementation work: rejected because user requested implementation progress now.
