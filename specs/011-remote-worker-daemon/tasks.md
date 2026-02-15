# Tasks: Remote Worker Daemon (015-Aligned)

**Input**: Design documents from `/specs/011-remote-worker-daemon/`  
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`

## Phase 1: Existing Milestone Foundation

- [x] T001 Standalone worker package + CLI entrypoint implemented in `moonmind/agents/codex_worker/` and `pyproject.toml`.
- [x] T002 Queue claim/heartbeat/complete/fail/artifact upload primitives implemented in `moonmind/agents/codex_worker/worker.py`.
- [x] T003 `codex_exec` checkout/execute/artifact flow implemented in `moonmind/agents/codex_worker/handlers.py`.

---

## Phase 2: 015 Umbrella Runtime Alignment

- [x] T004 Add Speckit CLI startup gate in `moonmind/agents/codex_worker/cli.py`.
- [x] T005 Add embedding readiness startup gate for Google profiles in `moonmind/agents/codex_worker/cli.py`.
- [x] T006 Extend worker policy defaults to include skills configuration (`default_skill`, `allowed_skills`) and `codex_skill` claim support in `moonmind/agents/codex_worker/worker.py`.
- [x] T007 Implement `codex_skill` compatibility mapping path in `moonmind/agents/codex_worker/handlers.py`.
- [x] T008 Emit skills execution metadata in worker events in `moonmind/agents/codex_worker/worker.py`.

---

## Phase 3: Test Coverage Alignment

- [x] T009 Update CLI preflight tests for Speckit + embedding checks in `tests/unit/agents/codex_worker/test_cli.py`.
- [x] T010 Update worker loop tests for `codex_skill` support and allowlist enforcement in `tests/unit/agents/codex_worker/test_worker.py`.
- [x] T011 Add handler tests for `codex_skill` payload mapping and failure paths in `tests/unit/agents/codex_worker/test_handlers.py`.

---

## Phase 4: Spec/Contract Alignment

- [x] T012 Update `specs/011-remote-worker-daemon/spec.md` to 015-aligned stories and requirements.
- [x] T013 Update `specs/011-remote-worker-daemon/plan.md`, `research.md`, and `data-model.md`.
- [x] T014 Update `specs/011-remote-worker-daemon/contracts/codex-worker-runtime-contract.md` and `contracts/requirements-traceability.md`.
- [x] T015 Update `specs/011-remote-worker-daemon/quickstart.md` and `checklists/requirements.md`.

---

## Phase 5: Validation

- [ ] T016 Run required scope validation helper for tasks/diff checks (blocked if script is absent).
- [x] T017 Run full unit validation via `./tools/test_unit.sh`.

---

## Phase 6: Task-Level Codex Runtime Overrides

- [x] T018 Add payload parsing for `codex.model` and `codex.effort` in `moonmind/agents/codex_worker/handlers.py`.
- [x] T019 Implement command resolution precedence (`task override -> worker defaults -> Codex CLI defaults`) for `codex_exec` and `codex_skill` in `moonmind/agents/codex_worker/handlers.py`.
- [x] T020 Extend worker config/env plumbing for codex model/effort defaults in `moonmind/agents/codex_worker/worker.py` and `moonmind/agents/codex_worker/cli.py`.
- [x] T021 Add unit coverage for payload validation, override forwarding, and fallback behavior in `tests/unit/agents/codex_worker/test_handlers.py` and `tests/unit/agents/codex_worker/test_worker.py`.
