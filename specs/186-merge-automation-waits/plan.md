# Implementation Plan: Merge Automation Waits

**Branch**: `186-merge-automation-waits` | **Date**: 2026-04-16 | **Spec**: `specs/186-merge-automation-waits/spec.md`

## Input

Single-story runtime feature specification from `specs/186-merge-automation-waits/spec.md`, generated from Jira issue `MM-351` and the canonical preset brief in `docs/tmp/jira-orchestration-inputs/MM-351-moonspec-orchestration-input.md`.

## Summary

Implement MM-351 by making `MoonMind.MergeAutomation` the canonical Temporal workflow for post-publish merge readiness waits. The existing merge-gate implementation becomes the merge automation workflow: parent `MoonMind.Run` starts it with compact publish-context refs and PR identity, readiness activities decide whether the current head SHA is ready, external event signals wake the workflow before bounded fallback polling, Continue-As-New preserves compact wait state, and resolver launch requests remain child `MoonMind.Run` requests with publish mode `none`. Validation focuses on model tests, workflow helper tests, parent-start tests, and Temporal workflow-boundary tests with fake readiness and resolver activities.

## Technical Context

- Language/version: Python 3.12.
- Primary dependencies: Temporal Python SDK, Pydantic v2, pytest, existing GitHub/Jira integration activity surfaces, existing pr-resolver skill.
- Storage: Existing Temporal workflow history, memo/search attributes, and artifact refs only; no new persistent storage.
- Unit testing: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification, focused Python targets during iteration.
- Integration testing: `./tools/test_integration.sh` for compose-backed `integration_ci` when Docker is available; workflow-boundary behavior is covered by unit-level Temporal tests with fakes.
- Target platform: Linux worker containers and local Docker Compose Temporal deployment.
- Project type: Temporal-backed orchestration service.
- Performance goals: Long-lived readiness waits avoid busy loops; fallback re-evaluation is bounded by configured `fallbackPollSeconds`; workflow history remains compact across waits.
- Constraints: Workflow code remains deterministic; external GitHub/Jira reads and resolver creation stay in activities; start input must carry compact refs rather than large publish payloads; no `MoonMind.MergeGate` compatibility alias is retained.
- Scale/scope: One merge automation workflow per merge-automation-enabled PR publication, tracking one current PR head SHA and compact resolver attempt history.

## Constitution Check

- I Orchestrate, Don't Recreate: PASS. The workflow gates existing pr-resolver child runs rather than replacing resolver behavior.
- II One-Click Agent Deployment: PASS. Uses existing Temporal workers and integration activity surfaces.
- III Avoid Vendor Lock-In: PASS. GitHub/Jira-specific evidence remains activity-backed behind normalized readiness output.
- IV Own Your Data: PASS. Gate state and artifacts remain in operator-controlled Temporal/artifact surfaces.
- V Skills Are First-Class: PASS. Resolver execution uses the existing pr-resolver skill through child `MoonMind.Run`.
- VI Bittersweet Lesson: PASS. The implementation is a thin orchestration contract around observable readiness evidence.
- VII Runtime Configurability: PASS. Fallback polling and readiness policy are configured through merge automation input.
- VIII Modular Architecture: PASS. Parent run, merge automation wait, readiness activities, and resolver child run remain separate boundaries.
- IX Resilient by Default: PASS. Continue-As-New state preservation, duplicate resolver prevention, and deterministic blockers are required and tested.
- X Continuous Improvement: PASS. Operator-visible blockers and summaries explain waiting and terminal outcomes.
- XI Spec-Driven Development: PASS. Work follows `specs/186-merge-automation-waits/spec.md` and preserves MM-351 traceability.
- XII Canonical Docs Separation: PASS. Runtime tracking stays under specs and docs/tmp; canonical docs only receive target-state naming alignment when touched.
- XIII Pre-Release Compatibility Policy: PASS. `MoonMind.MergeGate` is removed as a canonical internal contract instead of preserved as an alias.

## Project Structure

- Spec: `specs/186-merge-automation-waits/spec.md`
- Research: `specs/186-merge-automation-waits/research.md`
- Data model: `specs/186-merge-automation-waits/data-model.md`
- Contract: `specs/186-merge-automation-waits/contracts/merge-automation-contract.md`
- Quickstart: `specs/186-merge-automation-waits/quickstart.md`
- Tasks: `specs/186-merge-automation-waits/tasks.md`
- Production touchpoints: `moonmind/schemas/temporal_models.py`, `moonmind/workflows/temporal/workflows/merge_gate.py`, `moonmind/workflows/temporal/workflows/run.py`, `moonmind/workflows/temporal/activity_catalog.py`, `moonmind/workflows/temporal/activity_runtime.py`, `moonmind/workflows/temporal/workers.py`, `moonmind/workflows/temporal/worker_entrypoint.py`
- Test targets: `tests/unit/workflows/temporal/test_merge_gate_models.py`, `tests/unit/workflows/temporal/test_merge_gate_workflow.py`, `tests/unit/workflows/temporal/test_run_merge_gate_start.py`, `tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py`, `tests/unit/workflows/temporal/test_temporal_workers.py`

## Test Strategy

- Unit strategy: update red-first tests for the canonical `MoonMind.MergeAutomation` name, compact start payload validation, fallback polling normalization, blocker classification, resolver request construction, and Continue-As-New payload preservation.
- Integration strategy: use workflow-boundary unit tests with fake activities for signal/poll wait behavior, expiration, and duplicate resolver prevention; run hermetic integration if Docker is available after focused unit verification.

## Complexity Tracking

None.
