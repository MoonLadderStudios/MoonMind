# Implementation Plan: Codex Session Skill Projection Stability

## Summary

Fix managed Codex session ordering so the final cloned workspace exists before selected skill materialization and instruction preparation. Harden publish filtering so runtime-only skill projection symlinks cannot leak into target repositories.

## Technical Context

- Python 3.12
- Temporal workflow/activity boundaries
- Managed Codex session adapter
- Agent skill materialization under `.agents/skills`
- Existing pytest unit coverage

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The fix preserves Codex as the agent runtime and adjusts orchestration order only.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisites added.
- **III. Avoid Vendor Lock-In**: PASS. Changes are isolated to Codex session adapter and generic publish filtering for skill projections.
- **IV. Own Your Data**: PASS. Runtime artifacts remain local and inspectable.
- **V. Skills Are First-Class and Easy to Add**: PASS. Selected skills remain visible through the canonical `.agents/skills` path.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The result contract is unchanged; only runtime setup ordering is fixed.
- **VII. Runtime Configurability**: PASS. No hardcoded operator-facing config added.
- **VIII. Modular Architecture**: PASS. Changes stay inside adapter and activity boundaries.
- **IX. Resilient by Default**: PASS. The run fails before turn send if selected skill projection cannot be materialized.
- **X. Continuous Improvement**: PASS. Failure mode is covered by regression tests.
- **XI. Spec-Driven Development**: PASS. This artifact records the bugfix contract.
- **XII. Desired-State Docs**: PASS. Migration details remain in this feature artifact.
- **XIII. Pre-release Compatibility**: PASS. No compatibility aliases or fallback semantics introduced.

## Implementation Strategy

1. Add a launch metadata preflight that calls turn preparation with selected-skill materialization skipped.
2. Reorder `CodexSessionAdapter.start` so `_ensure_remote_session` runs before the real `_instructions_for_request`.
3. Keep selected-skill materialization and validation inside the real `agent_runtime.prepare_turn_instructions`; now it runs against the final workspace.
4. Extend publish path filtering to ignore `.gemini/skills` and root `skills_active` symlink projections.
5. Add targeted tests for cold-session order, launch metadata preservation, and publish filtering.

## Risk & Mitigation

- **Risk**: Preparing selected skills before launch can create a projection that is deleted by workspace clone.
  **Mitigation**: The pre-launch preparation path skips selected-skill materialization and only harvests compact durable metadata.

## Verification

- `./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- `./tools/test_unit.sh` before finalizing.
