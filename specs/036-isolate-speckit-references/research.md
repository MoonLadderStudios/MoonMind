# Research: Isolate Spec Kit References and Skill-First Runtime

## Decision 1: Adapter resolution must be explicit and authoritative

- **Decision**: Replace implicit compatibility behavior with explicit adapter mapping and unsupported-skill errors.
- **Rationale**: Current behavior labels non-speckit execution as `skill` while silently using direct execution, which obscures runtime truth and weakens failure handling.
- **Alternatives considered**:
  - Keep silent fallback for unknown skills: rejected because it hides configuration errors.
  - Disable skills mode entirely for unknown skills: rejected because that still masks adapter registration problems.

## Decision 2: Speckit executable checks should be skill-scoped, not global

- **Decision**: Run Speckit CLI verification only when the selected/effective skill set includes `speckit`.
- **Rationale**: Non-speckit workflows should not fail startup or stage execution because Speckit is unavailable.
- **Alternatives considered**:
  - Keep global Speckit checks for safety: rejected because it blocks valid non-speckit workflows.
  - Remove all Speckit checks: rejected because speckit-selected workflows still need guardrails.

## Decision 3: Introduce canonical workflow routes while keeping deprecated aliases

- **Decision**: Expose canonical workflow routes (`/api/workflows/runs/*`) and keep `/api/workflows/speckit/*` as deprecated aliases.
- **Rationale**: Canonical naming reduces long-term coupling, while alias support avoids breaking existing clients.
- **Alternatives considered**:
  - Rename routes in place without aliases: rejected due to immediate API breakage.
  - Keep only legacy routes: rejected because it preserves naming debt.

## Decision 4: Preserve storage names and use migration-by-interface

- **Decision**: Keep persisted SPEC-prefixed table names unchanged in this feature.
- **Rationale**: DB/table renaming is high-risk and not required to decouple runtime adapter semantics.
- **Alternatives considered**:
  - Full table/model renaming now: rejected due to migration complexity and blast radius.

## Decision 5: Track legacy alias usage in logs/headers for migration visibility

- **Decision**: Emit deprecation headers and structured logs when legacy API aliases are used.
- **Rationale**: Teams need observability to plan and verify migration away from legacy endpoints.
- **Alternatives considered**:
  - Soft deprecation docs only: rejected because usage visibility would be weak.
