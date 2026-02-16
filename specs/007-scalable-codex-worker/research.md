# Research: Scalable Codex Worker (015 Alignment)

**Feature**: `007-scalable-codex-worker`  
**Date**: 2026-02-14

## Decision Log

### 1. Queue Compatibility vs Codex Isolation

- **Decision**: Keep `celery_codex_worker` bound to both `speckit` and `codex` queues in Compose for backward-compatible discovery + Codex stage handling.
- **Rationale**: 015 umbrella requires preserving existing queue behavior while introducing skills-first semantics.
- **Impact**: 007 language is updated from strict "codex-only" worker behavior to compatibility queue bindings.

### 2. Speckit Always-On Capability

- **Decision**: Treat Speckit CLI verification as a mandatory startup check for Codex and Gemini workers.
- **Rationale**: 015 umbrella requires workers to always have Speckit capability regardless of selected skill policy.
- **Impact**: Worker startup diagnostics explicitly surface Speckit availability before task processing.

### 3. Startup Embedding Prerequisite Validation

- **Decision**: Add shared worker startup validation for embedding runtime profile and fail fast when Google embeddings are configured without credentials.
- **Rationale**: Fast-path operation requires deterministic failures for misconfigured credential state.
- **Impact**: New startup helper validates `DEFAULT_EMBEDDING_PROVIDER` + key availability and emits structured log context.

### 4. Skills-First Metadata in Workflow Tasks

- **Decision**: Keep skills-first metadata attached to task payloads (`selectedSkill`, `executionPath`, `usedSkills`, `usedFallback`, `shadowModeRequested`) for discover/submit/publish stages.
- **Rationale**: This preserves compatibility while satisfying umbrella telemetry needs.
- **Impact**: API consumers retain existing fields and gain explicit stage-path diagnostics.

### 5. Non-Speckit Skill Overrides

- **Decision**: Preserve allowlist-driven skill selection (`SPEC_WORKFLOW_ALLOWED_SKILLS`) with Speckit as default.
- **Rationale**: Umbrella policy requires skills-based orchestration without requiring separate Speckit-only mode.
- **Impact**: Stage overrides remain policy-controlled and backward compatible.

## Validation/Tooling Notes

- `.specify/scripts/bash/validate-implementation-scope.sh` is not present in this repository, so the orchestrate scope gate cannot be executed directly.
- Unit validation remains enforceable via `./tools/test_unit.sh` and is treated as the mandatory implementation gate.
