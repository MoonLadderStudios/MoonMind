# Implementation Plan: Docker-Out-of-Docker Phase 0 Contract Lock

**Branch**: `143-dood-phase0` | **Date**: 2026-04-09 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/143-dood-phase0/spec.md`

## Summary

Implement Phase 0 of the Docker-out-of-Docker rollout by locking the canonical architecture wording across the DooD, Codex managed-session, and managed-execution docs; adding a DooD remaining-work tracker under `docs/tmp/remaining-work/`; and introducing an automated unit test that fails when the agreed glossary, execution primitive, or tracker reference drifts.

## Technical Context

**Language/Version**: Markdown documentation plus Python 3.12+ for validation tests  
**Primary Dependencies**: pytest, repository markdown docs, `pathlib`-based file assertions  
**Storage**: Repository files only  
**Testing**: Focused pytest unit test followed by `./tools/test_unit.sh`  
**Target Platform**: MoonMind repository documentation and unit-test suite  
**Project Type**: Documentation contract lock with executable validation  
**Performance Goals**: Keep the validation test fast and deterministic while covering the Phase 0 contract surface  
**Constraints**: Phase 0 only; no launcher/runtime/tooling implementation beyond documentation and validation; preserve Constitution Principle XII by keeping rollout tracking under `docs/tmp/remaining-work/`  
**Scale/Scope**: Three canonical docs, one remaining-work tracker, one focused unit test

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The slice clarifies orchestration boundaries instead of introducing new runtime behavior.
- **II. One-Click Agent Deployment**: PASS. No deployment changes are introduced.
- **III. Avoid Vendor Lock-In**: PASS. The wording keeps workload tools as control-plane concepts rather than hardwiring Docker into session identity.
- **IV. Own Your Data**: PASS. The plan reinforces artifacts and bounded metadata as durable truth.
- **V. Skills Are First-Class and Easy to Add**: PASS. The execution primitive remains the documented `tool.type = "skill"` tool path.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. This phase hardens contracts and keeps implementation sequencing out of canonical docs.
- **VII. Powerful Runtime Configurability**: PASS. No runtime configuration behavior changes.
- **VIII. Modular and Extensible Architecture**: PASS. The slice preserves the session-plane, tool-path, and agent-runtime boundaries.
- **IX. Resilient by Default**: PASS. Automated validation guards the contract against future documentation drift.
- **XI. Spec-Driven Development**: PASS. The work is fully driven by spec, plan, and tasks artifacts.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical docs describe desired-state boundaries; the rollout checklist lives under `docs/tmp/remaining-work/`.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. No compatibility aliases or dual semantic paths are introduced.

## Research

- `docs/ManagedAgents/DockerOutOfDocker.md` already contains the target glossary (`session container`, `workload container`, `runner profile`, `session-assisted workload`) and already states that one-shot workload containers are the initial emphasis.
- `docs/ManagedAgents/CodexCliManagedSessions.md` currently links to the DooD doc but does not explicitly say that session-plane steps may invoke control-plane workload tools whose containers remain outside session identity.
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` currently defines `MoonMind.AgentRun` for true agent runtimes and excludes generic executable tools, but it does not yet name Docker-backed workload tools as ordinary executable tools distinct from managed agent runs.
- `docs/tmp/remaining-work/README.md` lists active trackers but there is no DooD rollout tracker yet, even though `docs/ManagedAgents/DockerOutOfDocker.md` already links to one.
- The repo does not already contain a documentation-contract unit test for this DooD boundary, so the fastest durable guard is a focused pytest file that asserts the required phrases and tracker path exist.

## Project Structure

- Update `docs/ManagedAgents/DockerOutOfDocker.md` to ensure the tracker link and Phase 0 contract wording stay aligned.
- Update `docs/ManagedAgents/CodexCliManagedSessions.md` with a short cross-reference for session-assisted workload tools.
- Update `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` with a short note that Docker-backed workload tools are ordinary executable tools unless they launch a true managed runtime.
- Add `docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md` as the DooD rollout tracker and list it in `docs/tmp/remaining-work/README.md`.
- Add `tests/unit/docs/test_dood_phase0_contract.py` for automated validation.

## Data Model

- See [data-model.md](./data-model.md) for the glossary and validation entities guarded by this phase.

## Contracts

- [contracts/dood-phase0-doc-contract.md](./contracts/dood-phase0-doc-contract.md)

## Implementation Plan

1. Add a failing unit test that asserts the required DooD/session-plane/execution-model wording and the presence of the remaining-work tracker.
2. Add the DooD tracker under `docs/tmp/remaining-work/` and register it in `docs/tmp/remaining-work/README.md`.
3. Update the canonical session-plane and execution-model docs with the missing Phase 0 cross-reference and execution-boundary wording.
4. Tighten the canonical DooD doc wording only where needed to keep the tracker reference and Phase 0 decisions explicit.
5. Rerun the focused pytest test, then run `./tools/test_unit.sh` for final verification.

## Verification Plan

### Automated Tests

1. `pytest -q tests/unit/docs/test_dood_phase0_contract.py`
2. `./tools/test_unit.sh`

### Manual Validation

1. Read the three canonical docs together and confirm they share one glossary and one execution-boundary story.
2. Confirm the remaining-work tracker exists under `docs/tmp/remaining-work/` and is linked from the canonical DooD doc.
3. Confirm the validation test fails if the required wording or tracker reference is removed.
