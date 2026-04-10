# Tasks: live-logs-phase-1

**Feature Branch**: `110-live-logs-phase-1`  
**Created**: 2026-03-28  
**Aligned Plan**: `plan.md`

**Tests**: Runtime validation is required for this feature. Final verification must run `./tools/test_unit.sh` against the runtime launcher/log-streamer/supervisor test suite named in `plan.md`.

## Phase 1: Runtime Launch Foundation

**Purpose**: Cover the managed-launch behavior that enables artifact-first log capture without terminal relays.

- [ ] T001 Update or verify `moonmind/workflows/temporal/runtime/launcher.py` so managed subprocesses always start with piped `stdout` and `stderr`. (Covers DOC-REQ-001; supports FR-001, FR-002)
- [ ] T002 Remove or verify removal of any `tmate`/terminal-relay dependency from the managed runtime launch path in `moonmind/workflows/temporal/runtime/launcher.py`. (Covers DOC-REQ-002; supports FR-006)

## Phase 2: Runtime Capture and Persistence

**Purpose**: Cover the production runtime code that drains output, writes durable artifacts, and persists terminal metadata.

- [ ] T003 Update or verify `moonmind/workflows/temporal/runtime/log_streamer.py` so raw subprocess output is captured without framework-log normalization and drained in bounded chunks suitable for long-running streams. (Covers DOC-REQ-004, DOC-REQ-005; supports FR-001, FR-002, FR-003, NFR-001, NFR-002)
- [ ] T004 Update or verify `moonmind/workflows/temporal/runtime/log_streamer.py` so every managed run writes `stdout.log`, `stderr.log`, and `diagnostics.json`. (Covers DOC-REQ-006, DOC-REQ-007, DOC-REQ-008; supports FR-001, FR-002, FR-003)
- [ ] T005 Update or verify `moonmind/workflows/temporal/runtime/supervisor.py` so heartbeat/timeout handling runs concurrently with log draining and persists terminal artifact refs plus summary metadata onto `ManagedRunRecord`. (Covers DOC-REQ-003, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012; supports FR-004, FR-005, NFR-003)

## Phase 3: Validation and Traceability

**Purpose**: Ensure the runtime behavior is proven with deterministic validation coverage and explicit DOC-REQ mapping.

- [ ] T006 Update or verify `tests/unit/services/temporal/runtime/test_launcher.py` coverage for piped stdio launch behavior and absence of `tmate` in the managed launch path. (Validates DOC-REQ-001, DOC-REQ-002)
- [ ] T007 Update or verify `tests/unit/services/temporal/runtime/test_log_streamer.py` coverage for raw stream fidelity, artifact naming, and diagnostics content. (Validates DOC-REQ-004, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008)
- [ ] T008 Update or verify `tests/unit/services/temporal/runtime/test_supervisor.py` and `tests/unit/services/temporal/runtime/test_supervisor_live_output.py` coverage for concurrent draining, successful runs, failed runs, timed-out runs, abrupt termination, and interleaved high-volume output. (Validates DOC-REQ-003, DOC-REQ-005, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014)
- [ ] T009 Keep `specs/110-live-logs-phase-1/contracts/requirements-traceability.md` synchronized so every `DOC-REQ-*` has explicit implementation and validation coverage. (Validates DOC-REQ-001 through DOC-REQ-014)
- [ ] T010 Run `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_launcher.py tests/unit/services/temporal/runtime/test_log_streamer.py tests/unit/services/temporal/runtime/test_supervisor.py tests/unit/services/temporal/runtime/test_supervisor_live_output.py` and record the result. (Validation gate for FR-001 through FR-006, NFR-001 through NFR-003)

## Dependencies & Order

- T001 and T002 can proceed in parallel.
- T003 and T004 depend on Phase 1 only if launch-path assumptions change; otherwise they can be evaluated in parallel with T001/T002.
- T005 depends on the runtime capture model from T003/T004.
- T006, T007, and T008 depend on the production runtime code scope being settled.
- T009 should be updated whenever any DOC-REQ coverage changes.
- T010 runs last.
