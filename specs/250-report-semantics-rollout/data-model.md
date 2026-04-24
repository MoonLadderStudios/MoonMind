# Data Model: Report Semantics Rollout

## Overview

MM-497 does not introduce new persistent storage. It defines and verifies the bounded runtime concepts that let generic outputs and explicit report workflows coexist during the staged report rollout.

## Entities

### 1. Generic Output Workflow

- Purpose: Represents an existing workflow that continues to publish generic outputs such as `output.primary` and `output.summary`.
- Key fields:
  - `canonical_primary_link_type`: expected generic primary output link type
  - `uses_report_semantics`: always `false`
  - `ui_fallback_behavior`: generic artifact presentation path
- Validation rules:
  - Must not be treated as a report producer by local UI heuristics alone.
  - May coexist with report workflows in the same product surface without being reclassified.

### 2. Report-Producing Workflow

- Purpose: Represents a workflow that explicitly opts into canonical report behavior.
- Key fields:
  - `report_type`: bounded producer-defined report family such as `unit_test_report`, `coverage_report`, `security_pentest_report`, or `benchmark_report`
  - `required_link_types`: includes `report.primary` and any optional supporting report link types
  - `report_scope`: execution or step-level scope carried through existing report-bundle contracts
- Validation rules:
  - Must publish `report.primary` for canonical report behavior.
  - Must prefer explicit `report.*` semantics over generic `output.primary`.

### 3. Incremental Rollout Boundary

- Purpose: Captures the migration contract that lets generic outputs and report outputs coexist without a flag-day change.
- Key fields:
  - `generic_output_compatibility`: whether existing generic outputs remain valid
  - `explicit_report_opt_in`: whether new report workflows require explicit report semantics
  - `deferred_follow_on_capabilities`: future projection, retention, filter, and pinning work that may ship later
- Validation rules:
  - Existing generic outputs continue functioning.
  - New report workflows use explicit report semantics.
  - Deferred capabilities remain follow-on work rather than silent requirements.

### 4. Representative Workflow Mapping

- Purpose: Defines the preserved example families that demonstrate the shared report contract.
- Key fields:
  - `workflow_family`: unit-test, coverage, pentest/security, or benchmark
  - `report_type`
  - `expected_report_links`
  - `source_reference`
- Validation rules:
  - Each representative mapping must remain compatible with the shared `report.*` contract.
  - Mappings are examples of supported semantics, not separate storage models.

### 5. Deferred Report Decision

- Purpose: Tracks product choices intentionally preserved for later stories.
- Key fields:
  - `topic`: report type enum, auto-pinning, projection timing, export semantics, evidence grouping, or multi-step projections
  - `state`: deferred
  - `preserved_in`: source design and feature-local planning/verification artifacts
- Validation rules:
  - Deferred topics must remain explicit.
  - MM-497 must not silently decide these topics.

## Relationships

- A `Generic Output Workflow` and a `Report-Producing Workflow` both participate in the `Incremental Rollout Boundary`.
- A `Report-Producing Workflow` may have one or more `Representative Workflow Mapping` entries.
- `Deferred Report Decision` items constrain the rollout boundary but are not implementation-complete in this story.

## State Transitions

### Workflow Classification

1. Existing workflow starts as generic-output behavior.
2. If it adopts explicit `report.*` semantics, it becomes a report-producing workflow.
3. UI and API consumers rely on explicit report semantics and server-defined report behavior rather than heuristic reclassification.

### Rollout Progress

1. Phase 1: report intent is recognized through metadata conventions and existing artifact APIs.
2. Phase 2: explicit `report.*` link types and UI report surfacing are added.
3. Phase 3: compact report-bundle result contracts are standardized.
4. Phase 4: report-aware projections, filters, retention defaults, and pinning affordances are layered in where useful.

MM-497 verifies that the current repository already supports this staged progression without forcing a flag-day migration.
