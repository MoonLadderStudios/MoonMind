# Feature Specification: Jira Orchestrate Preset

**Feature Branch**: `173-jira-orchestrate-preset`
**Created**: 2026-04-15
**Input**: User description: "Create a Jira Orchestrate preset. This preset should take a Jira issue identifier as input (e.g. MM-328), change the issue's status to In Progress, reproduce all the steps of moonspec-orchestrate with the issue's Jira preset brief as input, create a PR, and then change the status of the Jira issue to Code Review."

## User Story 1 - Run MoonSpec From A Jira Issue (Priority: P1)

As an operator, I can select a Jira Orchestrate preset, enter a Jira issue key, and get an expanded task flow that moves the issue to In Progress, uses the issue's Jira preset brief as the MoonSpec input, creates a pull request, and then moves the issue to Code Review.

**Independent Test**: Expand the seeded preset with `MM-328` and verify the generated steps include Jira status transitions, MoonSpec orchestration stages, PR creation, and final Code Review transition in the required order.

### Acceptance Scenarios

1. **Given** the seeded preset catalog is synchronized, **When** the Jira Orchestrate preset is loaded, **Then** it is available as a global active preset.
2. **Given** a Jira issue key input, **When** the preset is expanded, **Then** the first step changes that issue to In Progress through the Jira updater skill.
3. **Given** the same expansion, **When** the MoonSpec stages are inspected, **Then** they mirror the existing MoonSpec Orchestrate lifecycle while using the Jira preset brief as the canonical input.
4. **Given** verification succeeds, **When** the PR stage runs, **Then** the workflow requires a pull request whose title includes the Jira issue key.
5. **Given** the PR exists, **When** the final Jira transition runs, **Then** the issue is moved to Code Review through the Jira updater skill.

## Requirements

- **FR-001**: The system MUST seed a global `jira-orchestrate` task preset.
- **FR-002**: The preset MUST require a Jira issue key input.
- **FR-003**: The preset MUST transition the Jira issue to In Progress before implementation work.
- **FR-004**: The preset MUST instruct the runtime to use the issue's Jira preset brief as the canonical MoonSpec orchestration input.
- **FR-005**: The preset MUST include the MoonSpec Orchestrate lifecycle stages: classify/resume, specify, optional breakdown, plan, tasks, align, implement, and verify.
- **FR-006**: The preset MUST include a pull request creation stage after successful verification.
- **FR-007**: The preset MUST transition the Jira issue to Code Review only after the pull request exists.
- **FR-008**: Automated tests MUST validate seed synchronization and expansion behavior for the new preset.

## Success Criteria

- **SC-001**: Startup seed synchronization creates the `jira-orchestrate` preset.
- **SC-002**: Expansion with `MM-328` produces the expected Jira, MoonSpec, PR, and report steps with the issue key rendered.
- **SC-003**: Existing seeded presets continue to pass their catalog tests.
