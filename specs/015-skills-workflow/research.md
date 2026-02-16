# Research: Skills-First Workflow Umbrella

## Decision 1: Treat Speckit as baseline capability, not a separate workflow mode

- **Decision**: Keep Speckit installed and verified on worker startup for all relevant workers, but remove the notion of a unique "Speckit workflow mode" from orchestration policy.
- **Rationale**: This preserves current execution safety and operator expectations while allowing broader skills-based routing.
- **Alternatives considered**:
  - Keep Speckit as a dedicated workflow mode: rejected because it blocks generic skills-first expansion.
  - Remove Speckit defaults entirely: rejected because it risks immediate regressions for existing workflows.

## Decision 2: Add a skills adapter layer with direct-stage fallback

- **Decision**: Introduce a `skills` adapter layer that resolves stage requests to allowlisted skills first and falls back to direct implementations when configured.
- **Rationale**: This creates a controlled migration path without requiring disruptive rewrites of existing task logic.
- **Alternatives considered**:
  - Full rewrite to skills-only execution with no fallback: rejected due migration risk.
  - Keep ad hoc stage-to-function dispatch only: rejected because it does not support multi-skill policy.

## Decision 3: Keep existing queue and API compatibility while adding execution metadata

- **Decision**: Maintain existing queue names and `/api/workflows/speckit` compatibility while enriching run/task metadata with skill id, execution path, and timings.
- **Rationale**: Fastest path avoids client breakage and allows incremental rollout.
- **Alternatives considered**:
  - Rename queues/routes immediately to generic names: rejected because migration blast radius is unnecessary for first rollout.

## Decision 4: Use explicit rollout controls (shadow, canary, per-stage toggles)

- **Decision**: Add global and per-stage skills flags, plus shadow/canary controls, with default policy set to skills-first and Speckit defaults.
- **Rationale**: Enables safe, measurable rollout and rapid rollback.
- **Alternatives considered**:
  - Big-bang switch to skills-first for all traffic: rejected due operational risk.
  - Manual per-run switches only: rejected due insufficient governance at scale.

## Decision 5: Optimize operator path in docs + compose around Codex auth and Gemini embedding defaults

- **Decision**: Standardize quickstart on one-time Codex auth-volume login plus required env defaults (`GOOGLE_API_KEY`, `DEFAULT_EMBEDDING_PROVIDER=google`, `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`, `CODEX_ENV`, `CODEX_MODEL`, `GITHUB_TOKEN`).
- **Rationale**: Operator friction is the largest practical blocker to adoption.
- **Alternatives considered**:
  - Keep credential/setup guidance fragmented across multiple docs: rejected because it slows onboarding and increases failure rate.
