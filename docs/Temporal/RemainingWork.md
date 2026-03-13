# Temporal Remaining Work

This is the canonical Temporal migration backlog. Do not create or use a separate
`TemporalMigrationPlan.md`; that content is merged here.

Completion status: all tasks below are implemented as of 2026-03-12.

## Priority 0: Remove Active Placeholders and Dummy Paths

- [x] Replace `_dummy_planner` in `moonmind/workflows/temporal/worker_runtime.py`
      with a real planner implementation/wiring.
  - Current placeholder output still emits `reg:sha256:dummy` and
    `art:sha256:dummy`, which causes downstream `artifact.read` failures.
  - Remove dummy plan node defaults (`dummy-node`, `dummy.skill`) from runtime paths.
- [x] Add fail-fast behavior for planner wiring.
  - If planner dependency is missing, startup/configuration should fail before run
    execution, rather than generating placeholder refs.
- [x] Add a regression unit test that proves `plan.generate` in real runtime
      cannot return placeholder registry snapshot refs.

## Priority 1: Worker Runtime and Fleet Completeness

- [x] Validate each Temporal fleet (`workflow`, `artifacts`, `llm`, `sandbox`,
      `integrations`) boots as an active polling worker with expected
      workflows/activities registered.
- [x] Keep worker launch defaults pointed to
      `python -m moonmind.workflows.temporal.worker_runtime` in compose/runtime
      for Temporal worker services.
- [x] Add/maintain startup diagnostics proving queue bindings and registered
      activity types per fleet.
- [x] Harden generic runtime execution behavior in `_auto_skill_handler`:
  - Validate runtime mode explicitly and fail for unsupported values.
  - Keep model/effort passthrough exact (no silent compatibility transforms).
  - Remove implicit placeholder defaults that can mask malformed input payloads.

## Priority 2: Temporal as Source of Truth

- [x] Keep `TemporalExecutionService` operations Temporal-authoritative for create,
      list, detail, signal/update, and cancel.
- [x] Maintain projection sync as a read model only (idempotent rehydrate from
      Temporal describe/list data).
- [x] Ensure no new code path mutates local DB state as workflow authority.

## Priority 3: Artifact System Completion

- [x] Enforce strict artifact reference validity end-to-end.
  - Planning output must include resolvable artifact refs only.
  - `artifact.read` call sites must receive persisted refs, never placeholders.
- [x] Preserve the artifact link contract for execution-scoped writes
      (`namespace`, `workflow_id`, `run_id`, `link_type`).
- [x] Ensure run/manifest flows always persist and expose expected refs
      (`input`, `plan`, `summary`, `logs`, manifest/node outputs) for Mission Control.
- [x] Keep `ArtifactRef` v1 usage consistent in API and UI projection payloads.

## Priority 4: Mission Control Cutover

- [x] Keep list/detail views sourced from Temporal-authoritative state and
      projection cache only.
- [x] Ensure run actions (pause/resume/approve/cancel/rerun variants) map to
      workflow signals/updates, not local-only state flips.
- [x] Enable Temporal dashboard submit/actions flags by default only after
      end-to-end acceptance criteria are met.
- [x] Keep `/tasks` default routing on Temporal execution list and remove stale
      legacy-first navigation paths after cutover completion.

## Priority 5: Integrations and External Wait States

- [x] Keep callback correlation and resume behavior durable through Temporal
      signals and workflow waiting state.
- [x] Confirm integration polling backoff and terminal state handling produce
      correct user-visible waiting reason / final status projection.

## Priority 6: Test and Release Gate

- [x] Unit tests for:
  - planner wiring failure mode (no dummy fallback in production path),
  - artifact read failure surfacing for invalid refs,
  - worker binding/registration by fleet,
  - signal/update action routing and validation.
- [x] CI/release gate check:
  - generated plan artifacts must never contain placeholder values matching
    `*:sha256:dummy` (including registry snapshot digest/ref fields).
- [x] End-to-end test for Temporal run lifecycle:
  - submit run, generate/resolve plan, execute activities, persist artifact refs,
    perform at least one operator action, verify final status in API/UI model.
- [x] Local bring-up docs remain accurate for compose services and worker startup.

## Merged Migration Tasks (from former `TemporalMigrationPlan.md`)

- [x] Launch polling workers by default.
- [x] Complete and harden `MoonMind.Run`.
- [x] Complete and harden `MoonMind.ManifestIngest`.
- [x] Keep Temporal client layer authoritative for start/list/describe.
- [x] Maintain Temporal-authoritative execution service.
- [x] Maintain DB projection sync from Temporal.
- [x] Keep UI actions mapped to workflow signals/updates.
- [x] Keep large outputs externalized via artifact storage.
- [x] Keep integrations modeled as durable workflow wait states.
- [x] Keep list/detail APIs consistent with Temporal visibility/projections.
- [x] Finalize dashboard feature flag rollout.
- [x] Maintain local bring-up and full E2E acceptance path.

## Documentation Follow-up

- [x] Update `docs/Temporal/TemporalAgentExecution.md` where it still describes
      execution-stage stubs that no longer match the current workflow
      implementation.
