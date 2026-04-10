# Implementation Plan: live-logs-phase-1

**Feature Branch**: `110-live-logs-phase-1`  
**Created**: 2026-03-28  
**Aligned Spec**: `spec.md`

## Summary

Phase 1 makes managed-run stdout, stderr, and diagnostics artifact-first and independent from terminal relays. This plan keeps the work in runtime mode: the authoritative production surfaces are the launcher, supervisor, and log streamer, with explicit validation in the runtime test suite. Even where code may already exist, the plan remains execution-ready by naming the production runtime files and the verification files required to prove each `DOC-REQ-*`.

## Technical Context

- **Primary runtime components**: `moonmind/workflows/temporal/runtime/launcher.py`, `moonmind/workflows/temporal/runtime/supervisor.py`, `moonmind/workflows/temporal/runtime/log_streamer.py`
- **Primary persisted contract**: `ManagedRunRecord` in `moonmind/schemas/agent_runtime_models.py`
- **Primary validation files**: `tests/unit/services/temporal/runtime/test_launcher.py`, `tests/unit/services/temporal/runtime/test_log_streamer.py`, `tests/unit/services/temporal/runtime/test_supervisor.py`, `tests/unit/services/temporal/runtime/test_supervisor_live_output.py`
- **Traceability contract**: `contracts/requirements-traceability.md`
- **Execution mode**: runtime
- **Scope boundary**: production runtime code and validation only; no frontend or API viewer work belongs in this Phase 1 slice

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The work stays at the orchestration/runtime boundary and does not add a MoonMind-specific cognitive layer.
- **II. One-Click Agent Deployment**: PASS. No new operator prerequisites or infrastructure are introduced.
- **III. Avoid Vendor Lock-In**: PASS. Stdout/stderr/diagnostics remain portable artifact outputs independent of a provider-specific terminal transport.
- **IV. Own Your Data**: PASS. Logs and diagnostics are persisted as MoonMind-owned artifacts and run metadata.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill storage or execution contract changes are required.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The plan hardens launcher/supervisor/log-streamer contracts and validates them with boundary tests.
- **VII. Powerful Runtime Configurability**: PASS. No new operator-facing config switches are required for Phase 1.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay within the existing runtime modules and their tests.
- **IX. Resilient by Default**: PASS. Concurrent draining, heartbeat integration, and timeout-safe capture directly reinforce unattended execution reliability.
- **X. Facilitate Continuous Improvement**: PASS. Diagnostics and terminal summaries remain persisted and reviewable after completion.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. `spec.md`, `plan.md`, `tasks.md`, and `contracts/requirements-traceability.md` are aligned to the same runtime slice.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Migration sequencing stays in `docs/tmp`; this plan only describes the implementation slice.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The plan removes dependence on `tmate` rather than adding compatibility indirection around it.

## Project Structure

- **Runtime production code**
  - `moonmind/workflows/temporal/runtime/launcher.py`
  - `moonmind/workflows/temporal/runtime/log_streamer.py`
  - `moonmind/workflows/temporal/runtime/supervisor.py`
  - `moonmind/schemas/agent_runtime_models.py`
- **Validation**
  - `tests/unit/services/temporal/runtime/test_launcher.py`
  - `tests/unit/services/temporal/runtime/test_log_streamer.py`
  - `tests/unit/services/temporal/runtime/test_supervisor.py`
  - `tests/unit/services/temporal/runtime/test_supervisor_live_output.py`
- **Traceability**
  - `specs/110-live-logs-phase-1/contracts/requirements-traceability.md`

## Implementation Strategy

### Phase 1 - Runtime launch and stream-capture foundations

1. Ensure the managed launcher always starts subprocesses with piped stdout/stderr and that no `tmate` path remains in the managed runtime launch flow.
2. Ensure the log streamer preserves raw stream fidelity, drains in bounded chunks, and writes durable `stdout.log` / `stderr.log` / `diagnostics.json` artifacts.

### Phase 2 - Supervision and persistence

1. Ensure the supervisor runs heartbeating/timeout handling concurrently with stream draining.
2. Ensure terminal artifact refs, summary metadata, exit classification, and timestamps are persisted onto `ManagedRunRecord`.
3. Ensure capture and persistence remain valid even when no UI client ever connects.

### Phase 3 - Validation and traceability

1. Add or update unit coverage for successful, failed, timed-out, abrupt-exit, high-volume, and interleaved-output scenarios.
2. Keep `DOC-REQ-*` implementation and validation mappings synchronized in `contracts/requirements-traceability.md`.

## Verification Plan

### Automated Validation

1. `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_launcher.py tests/unit/services/temporal/runtime/test_log_streamer.py tests/unit/services/temporal/runtime/test_supervisor.py tests/unit/services/temporal/runtime/test_supervisor_live_output.py`
2. Review `contracts/requirements-traceability.md` to confirm every `DOC-REQ-*` has both implementation and validation coverage.

### Manual Validation

1. Confirm managed runtime launch paths no longer depend on `tmate` or any similar terminal wrapper.
2. Confirm successful and timed-out runs both leave behind stdout/stderr/diagnostics artifacts plus terminal metadata in `ManagedRunRecord`.
