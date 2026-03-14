# Research: Task Presets Strategy Alignment

## Decision: Treat `agentkit-orchestrate.yaml` as source of truth for seeded behavior
- **Decision**: Use the YAML seed file as the authoritative runtime instruction contract and synchronize existing DB rows from it via migration.
- **Rationale**: Existing environments already contain seeded rows; seed file edits alone do not propagate after initial migrations.
- **Alternatives considered**:
  - *Manual SQL patching*: error-prone and non-repeatable across environments.
  - *Service startup auto-backfill*: introduces runtime side effects and complicates rollout safety.

## Decision: Remove direct commit/PR directives from runtime preset instructions
- **Decision**: Replace final preset step language with report-only handoff that explicitly defers commit/push/PR actions to MoonMind publish stage.
- **Rationale**: Current project strategy separates runtime execution from publish stage responsibilities; runtime instructions should not conflict.
- **Alternatives considered**:
  - *Keep commit/PR directives and rely on operator discipline*: leaves contradictory guidance and repeated policy violations.
  - *Delete final step entirely*: loses useful final status-report guidance.

## Decision: Add regression tests around seed content
- **Decision**: Add focused unit tests that parse the seed YAML and assert publish-stage-safe wording plus runtime-neutral capabilities.
- **Rationale**: Seed regressions can silently reintroduce policy drift unless explicitly tested.
- **Alternatives considered**:
  - *Rely on docs review only*: too fragile for policy-critical wording.
  - *Full migration integration test suite*: heavier than needed for this narrow alignment change.

## Decision: Preserve mode-aware orchestration behavior in seed instructions
- **Decision**: Keep the seed template wired to `inputs.orchestration_mode` for scope validation gates and remediation severity, so runtime mode enforces implementation scope while docs mode remains documentation-only permissive.
- **Rationale**: MoonMind strategy now separates execution intent by mode; hardcoding runtime semantics would break docs workflows and hidden fallback behavior violates fail-fast clarity.
- **Alternatives considered**:
  - *Hardcode runtime-only validation*: rejects legitimate docs-mode orchestration.
  - *Split into two separate presets*: duplicates maintenance and risks behavior drift between templates.
