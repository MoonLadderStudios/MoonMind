# Feature Specification: Task Presets Strategy Alignment

**Feature Branch**: `024-task-presets`
**Created**: 2026-03-01
**Status**: Draft
**Input**: User description: "Update specs/024-task-presets to make it align with the current state and strategy of the MoonMind project. Implement all of the updated tasks when done. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Runtime orchestration preset follows MoonMind publish strategy (Priority: P1)

Platform operators apply the global `agentkit-orchestrate` task preset to queue jobs and expect runtime execution to stop at implementation/reporting while MoonMind publish stage controls commit/PR behavior.

**Why this priority**: Runtime workers must not perform direct git publish actions when MoonMind wrapper stages own publish semantics.

**Independent Test**: Expand the seeded preset and verify final instructions explicitly prohibit runtime commit/push and describe report handoff instead of opening PRs.

**Acceptance Scenarios**:

1. **Given** the seeded `agentkit-orchestrate` template, **When** step instructions are loaded from the catalog, **Then** no step instructs direct commit or PR creation from runtime execution.
2. **Given** runtime mode orchestration, **When** final validation gates pass, **Then** the preset directs the agent to return a final report and defer publish actions to MoonMind publish stage.

---

### User Story 2 - Existing deployments receive preset behavior updates safely (Priority: P1)

Platform maintainers migrate existing databases and expect the seeded `agentkit-orchestrate` template row/version content to align with the current YAML definition without manual SQL edits.

**Why this priority**: Seed file updates alone do not modify environments that have already applied prior migrations.

**Independent Test**: Apply the new migration against an environment containing the preset and verify template/version records refresh using seed-derived capabilities and steps.

**Acceptance Scenarios**:

1. **Given** an existing `agentkit-orchestrate` template row, **When** the alignment migration runs, **Then** template/version `required_capabilities` and `steps` are updated from the current seed file.
2. **Given** a deployment missing the seed file or template row, **When** migration runs, **Then** it exits without error and leaves schema/data intact.

---

### User Story 3 - Spec artifact set reflects current implemented architecture (Priority: P2)

Engineers using `specs/024-task-presets` as a reference see accurate file paths, migration IDs, endpoint surface, and test locations that match today’s repository.

**Why this priority**: Stale specs create implementation drift and misleading remediation work.

**Independent Test**: Cross-check `spec.md`, `plan.md`, `tasks.md`, and contract docs against current source tree paths and API routes.

**Acceptance Scenarios**:

1. **Given** updated spec artifacts, **When** reviewers inspect listed implementation paths, **Then** referenced files exist in the repository and match the current module layout.
2. **Given** updated tasks, **When** implementation completes, **Then** every task is marked complete and points to real runtime/test/doc surfaces.

### Edge Cases

- Seed YAML unavailable at migration time; migration must no-op instead of failing deploy.
- Template slug exists but version row is missing; migration should still be best-effort and not break unrelated upgrades.
- Future seed revisions may add more steps; alignment logic should update stored step payload wholesale from YAML source.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `specs/024-task-presets` artifacts MUST describe the current task preset implementation surfaces (actual migration IDs, service modules, router paths, and test locations used in this repository).
- **FR-002**: The seeded `agentkit-orchestrate` preset MUST not instruct runtime agents to create commits, push branches, or open pull requests directly.
- **FR-003**: The `agentkit-orchestrate` preset MUST instruct runtime execution to return a final report and defer publish behavior to MoonMind publish stage.
- **FR-004**: A new idempotent Alembic migration MUST synchronize existing `agentkit-orchestrate` template/version records with the current seed YAML definition.
- **FR-005**: Automated tests MUST validate the seeded preset’s publish-stage-safe instruction language and protect against regressions.
- **FR-006**: Validation MUST run through `./tools/test_unit.sh` per repository testing policy.
- **FR-007**: Delivery MUST include production runtime code changes and validation tests; documentation/spec-only updates are out of scope for completion.

### Key Entities

- **TaskPresetSeedDocument**: YAML definition in `api_service/data/task_step_templates/agentkit-orchestrate.yaml` containing required capabilities and ordered orchestration steps.
- **TaskPresetTemplateRecord**: `task_step_templates` row storing scope, slug, and top-level required capabilities.
- **TaskPresetVersionRecord**: `task_step_template_versions` row storing versioned step blueprints and required capabilities.
- **PresetAlignmentMigration**: Alembic migration that updates existing seeded preset records to match current YAML content.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Seeded `agentkit-orchestrate` final step language contains explicit "do not commit/push" guidance and contains no direct commit/PR creation directives.
- **SC-002**: Migration applies cleanly in existing environments and updates seeded preset template/version fields using YAML-derived data.
- **SC-003**: Unit test coverage includes regression checks for preset instruction language and seed capability/runtime neutrality.
- **SC-004**: Updated `specs/024-task-presets/tasks.md` is fully completed (`[X]`) with references to implemented files and executed validation command.
- **SC-005**: Completion evidence shows both runtime production code updates and validation test updates in the same delivery.
