# Contract: Plans Overview Preset Boundary

## Jira Traceability

This contract implements the MM-389 runtime architecture story. MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata must preserve `MM-389`.

## Overview Placement Contract

The plans overview or repository-current equivalent must include a concise boundary clarification near the tasks, skills, presets, and plans content.

The clarification must not create a new migration checklist or replace the existing index. It should explain how the existing task preset and plan contract documents relate.

## Authoring-Time Preset Contract

The clarification must state that preset composition belongs to the control plane and is resolved before `PlanDefinition` creation.

The authoring-time semantics link must point to `docs/Tasks/TaskPresetsSystem.md`.

## Runtime Plan Contract

The clarification must state that runtime plans remain flattened execution graphs of concrete nodes and edges.

The runtime semantics link must point to `docs/Tasks/SkillAndPlanContracts.md`.

## Validation Evidence

The story is complete when `docs/tmp/101-PlansOverview.md` contains the contract language above and final MoonSpec verification confirms coverage of FR-001 through FR-009 and DESIGN-REQ-001, DESIGN-REQ-020, DESIGN-REQ-024, DESIGN-REQ-025, and DESIGN-REQ-026.
