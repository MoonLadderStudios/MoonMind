# Implementation Plan: Jira Orchestrate Preset

**Branch**: `173-jira-orchestrate-preset` | **Date**: 2026-04-15 | **Spec**: [spec.md](./spec.md)

## Summary

Add a seeded global task preset at `api_service/data/task_step_templates/jira-orchestrate.yaml`. The preset uses the existing Jira updater skill for workflow transitions and copies the MoonSpec Orchestrate stage structure for the implementation lifecycle. The preset remains within the existing task template schema, so no database or runtime contract changes are required.

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The preset composes existing Jira and MoonSpec skills instead of implementing new agent behavior.
- **II. One-Click Agent Deployment**: PASS. The preset is optional seeded catalog data and introduces no mandatory dependency.
- **III. Avoid Vendor Lock-In**: PASS. Jira-specific behavior stays in the existing optional Jira integration and trusted tool surface.
- **IV. Own Your Data**: PASS. The workflow operates through MoonMind-managed task and Jira surfaces.
- **V. Skills Are First-Class and Easy to Add**: PASS. The change adds a skill-composed preset with explicit inputs, outputs, and side effects.
- **VI. Evolving Scaffolds**: PASS. The preset is data and can be replaced without runtime refactoring.
- **VII. Runtime Configurability**: PASS. Preset expansion remains parameterized by inputs; runtime/publish settings remain task-level configuration.
- **VIII. Modular Architecture**: PASS. Changes are isolated to seeded preset data and tests.
- **IX. Resilient by Default**: PASS. Jira transitions require trusted tools and PR creation blockers stop before Code Review.
- **X. Continuous Improvement**: PASS. Final report captures outcomes, tests, risks, PR URL, and Jira status.
- **XI. Spec-Driven Development**: PASS. This spec, plan, and task list track the change.
- **XII. Canonical Documentation Separation**: PASS. No migration backlog is added to canonical docs.
- **XIII. Pre-Release Compatibility**: PASS. No compatibility aliases or fallback contracts are introduced.

## Implementation Scope

- Add `jira-orchestrate.yaml` to the seeded task template directory.
- Add unit coverage for catalog seed synchronization and template expansion.
- Add startup seeding integration coverage proving the preset is synchronized with the default catalog.

## Validation

- `pytest tests/unit/api/test_task_step_templates_service.py -q`
- `pytest tests/integration/test_startup_task_template_seeding.py -q`
- `./tools/test_unit.sh`
