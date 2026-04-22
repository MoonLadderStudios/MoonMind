# Implementation Plan: Merge Gate Head Drift Resilience

**Branch**: `219-merge-gate-head-drift` | **Date**: 2026-04-21 | **Spec**: [spec.md](./spec.md)

## Summary

Update `MoonMind.MergeAutomation` so a pull request head change observed before any resolver launch refreshes the tracked revision and reuses the existing readiness classifier for the new head. Update the Jira Orchestrate seed template so the pull request creation step explicitly creates a non-draft PR or marks it ready before Jira Code Review transition.

## Technical Context

**Language/Version**: Python 3.12 and YAML seed templates
**Dependencies**: Temporal Python SDK, Pydantic v2, pytest, existing task template expansion tests
**Storage**: Existing Temporal history, memo, and artifact outputs only
**Testing**: Focused pytest targets for merge automation workflow behavior and Jira Orchestrate template expansion

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Uses existing workflow and GitHub/Jira tool paths.
- **II. One-Click Agent Deployment**: PASS. No new services or setup.
- **III. Avoid Vendor Lock-In**: PASS. GitHub-specific evidence remains in the adapter/activity boundary.
- **IV. Own Your Data**: PASS. State remains in Temporal/artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. Jira Orchestrate remains a seed template using existing skills.
- **VI. Design for Deletion / Scientific Method**: PASS. Narrow behavioral change with regression tests.
- **VII. Powerful Runtime Configurability**: PASS. Existing merge automation policy remains authoritative.
- **VIII. Modular and Extensible Architecture**: PASS. Workflow logic changes are confined to merge automation.
- **IX. Resilient by Default**: PASS. Handles normal pre-resolver head drift without failing the parent run.
- **X. Facilitate Continuous Improvement**: PASS. Operator-visible blockers still explain active gates.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This spec records the requested behavior.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. No canonical docs changed.
- **XIII. Pre-Release Compatibility Policy**: PASS. No compatibility alias or fallback contract added.

## Validation

- Add a workflow unit test for pre-resolver head refresh and revision-scoped resolver launch.
- Extend Jira Orchestrate template expansion coverage for non-draft PR instructions.
