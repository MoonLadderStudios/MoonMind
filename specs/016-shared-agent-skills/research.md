# Research: Unified Agent Skills Directory

## Decision 1: Keep Agent Skills format as the canonical artifact

- **Decision**: Treat Agent Skills directory format (`SKILL.md` + skill payload) as the only canonical representation in MoonMind skill storage and runtime.
- **Rationale**: Both Codex and Gemini can consume this format directly, reducing conversion logic and drift risk.
- **Alternatives considered**:
  - Separate Codex-specific and Gemini-specific bundles: rejected due duplicated authoring and synchronization overhead.
  - Internal JSON-only manifest transformed at runtime: rejected because it adds a translation layer and loses native tool compatibility.

## Decision 2: Use a private Skill Registry with immutable version metadata

- **Decision**: Introduce a registry record per skill version containing `skill_name`, `version`, `content_hash`, `source_uri`, optional `signature`, and compatibility notes.
- **Rationale**: Selection and provenance become deterministic and auditable across runs.
- **Alternatives considered**:
  - Pull latest from branch/head on every run: rejected because results are non-deterministic.
  - Rely only on filename versioning without hash pinning: rejected due weak integrity guarantees.

## Decision 3: Materialize skills per run using immutable cache + active symlink set

- **Decision**: Fetch and verify artifacts into `/var/lib/moonmind/skill_cache/<sha256>/...`, then build run-local `/work/runs/<run_id>/skills_active/` as symlinks to cache entries.
- **Rationale**: This provides deduplication, integrity reuse, and run-level isolation.
- **Alternatives considered**:
  - Copy full skill trees into each run workspace: rejected due wasted I/O and storage.
  - Mount one global shared writable skill directory: rejected because per-run selection and isolation become fragile.

## Decision 4: Expose one active set through two adapter symlinks

- **Decision**: In each run workspace, set `.agents/skills -> ../skills_active` and `.gemini/skills -> ../skills_active`.
- **Rationale**: Both CLIs discover exactly the same active skills with minimal platform-specific logic.
- **Alternatives considered**:
  - Use only user-global skill install locations: rejected because worker runs need ephemeral and job-specific control.
  - Maintain duplicate folders for each CLI: rejected due drift risk and extra materialization time.

## Decision 5: Resolve selected skills from layered policy with explicit precedence

- **Decision**: Resolve `RunSkillSelection` in this order: job override -> queue profile -> global default allowlist.
- **Rationale**: Operators can set safe defaults while allowing explicit run overrides.
- **Alternatives considered**:
  - Only global allowlist: rejected because it cannot support per-run variation.
  - Only job overrides: rejected because it requires every job to restate baseline policy.

## Decision 6: Enforce trust and collision guardrails before activation

- **Decision**: Materialization fails if any selected skill has hash/signature mismatch, invalid metadata, or duplicate `name`.
- **Rationale**: Skills may include scripts; activation must be blocked when trust checks fail.
- **Alternatives considered**:
  - Warn-only on hash mismatch: rejected because compromised skills could execute.
  - Resolve name collisions by random or source order: rejected because behavior becomes non-deterministic.

## Decision 7: Keep headless operation configurable with safe defaults

- **Decision**: Worker runtime exposes explicit Gemini approval-mode/tool policy settings and keeps fully unrestricted automation as an opt-in profile.
- **Rationale**: Non-interactive operation is needed, but unrestricted approvals should be an explicit decision.
- **Alternatives considered**:
  - Always run unrestricted approvals: rejected for stricter environments.
  - Always run interactive approvals: rejected because queue workers are non-interactive.
