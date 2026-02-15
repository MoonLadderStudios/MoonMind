---
description: "Task list for Scalable Codex Worker (015-aligned)"
---

# Tasks: Scalable Codex Worker (015-Aligned)

**Input**: Design documents from `/specs/007-scalable-codex-worker/`  
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`  
**Tests**: Unit validation via `./tools/test_unit.sh`

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel
- **[Story]**: User story mapping (`US1`, `US2`, `US3`)

---

## Phase 1: Existing Foundation

- [x] T001 [US1] Provision `celery_codex_worker` service and shared worker image in `docker-compose.yaml`.
- [x] T002 [US1] Define persistent Codex auth volume (`CODEX_VOLUME_NAME`) and mount path (`CODEX_VOLUME_PATH`) in `docker-compose.yaml`.
- [x] T003 [US1] Enforce non-interactive Codex config template wiring (`CODEX_TEMPLATE_PATH`) for worker startup.

---

## Phase 2: Skills-First Compatibility Baseline

- [x] T004 [US2] Add skills-first execution contracts/runner/registry in `moonmind/workflows/skills/`.
- [x] T005 [US2] Emit stage execution metadata in workflow task payloads for discover/submit/publish in `moonmind/workflows/speckit_celery/tasks.py`.
- [x] T006 [US2] Add policy/metadata coverage tests in `tests/unit/workflows/test_skills_runner.py` and `tests/unit/workflows/test_tasks.py`.

---

## Phase 3: 015 Umbrella Alignment for Worker Startup (Current)

- [x] T007 [US1] Add shared startup validation helper for embedding runtime readiness in `celery_worker/startup_checks.py`.
- [x] T008 [US1] Wire embedding readiness checks into `celery_worker/speckit_worker.py`.
- [x] T009 [US1] Wire embedding readiness checks into `celery_worker/gemini_worker.py`.
- [x] T010 [P] [US1] Add unit tests for worker startup checks in `tests/unit/workflows/test_worker_entrypoints.py`.

---

## Phase 4: Spec Artifact Alignment (Current)

- [x] T011 [US1] Update `specs/007-scalable-codex-worker/spec.md` for 015-aligned requirements and outcomes.
- [x] T012 [US1] Update `specs/007-scalable-codex-worker/plan.md`, `research.md`, and `data-model.md` for runtime parity.
- [x] T013 [US1] Update `specs/007-scalable-codex-worker/quickstart.md` for fastest Codex+Gemini worker launch path.
- [x] T014 [US1] Update `specs/007-scalable-codex-worker/checklists/requirements.md` metadata and scope notes.

---

## Phase 5: Validation

- [x] T015 Run unit validation via `./tools/test_unit.sh` and record result.

---

## Dependencies & Execution Order

- Foundation -> skills compatibility -> startup hardening -> spec/doc alignment -> validation.
- `T007` is required before `T008`/`T009`.
- `T010` depends on `T007`.
