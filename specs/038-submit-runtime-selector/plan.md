# Implementation Plan: Submit Runtime Selector

**Branch**: `038-submit-runtime-selector` | **Date**: 2026-02-24 | **Spec**: `specs/038-submit-runtime-selector/spec.md`
**Input**: Phase 1 scope for the shared submit component that adds a 4-way runtime selector and conditional payload routing.

MoonMind needs one dashboard submit surface that can launch queue worker jobs (Codex/Gemini/Claude) or orchestrator runs without forcing users to re-enter data or understand two different forms. Phase 1 delivers a single `SubmitWorkForm` rendered on both legacy routes, runtime-aware validation, and draft swapping in memory so operators can explore both flows safely.

## Summary

Implement `renderSubmitWorkPage` inside `api_service/static/task_dashboard/dashboard.js` so `/tasks/queue/new` and `/tasks/orchestrator/new` mount the same DOM. The form loads worker runtime options from `system.supportedTaskRuntimes`, appends an "Orchestrator" entry, and exposes two conditional sections. Worker-specific inputs keep the existing queue payload contract, while the orchestrator section gathers `targetService`, `priority`, and optional `approvalToken`. Runtime changes save/restore drafts through a new `createSubmitDraftController`, and submission logic picks the correct endpoint (`sources.queue.create` vs `sources.orchestrator.create`) before redirecting to the appropriate detail route. Add focused dashboard unit tests that lock down draft separation, endpoint routing, and orchestrator validation.

## Technical Context

**Language/Version**: Python 3.11 (FastAPI backend) and vanilla ES2020 JavaScript bundled into `dashboard.js`; CSS built via Tailwind/PostCSS (Node 20).  
**Primary Dependencies**: Dashboard runtime config from `api_service/api/routers/task_dashboard_view_model.build_runtime_config`, queue/orchestrator REST endpoints, dashboard utility helpers already defined in `dashboard.js` (fetchJson, endpoint, normalizeTaskRuntimeInput).  
**Storage**: No persistence beyond existing queue/orchestrator APIs; Phase 1 drafts live only in browser memory.  
**Testing**: `./tools/test_unit.sh` orchestrates pytest suites and Node-based dashboard unit tests under `tests/task_dashboard/`.  
**Target Platform**: `/tasks/queue/new` and `/tasks/orchestrator/new` pages served from FastAPI template shell plus bundled JS; must continue to work in Chromium- and WebKit-based browsers.  
**Project Type**: Thin dashboard (static JS, server-rendered shell) talking to backend APIs.  
**Performance Goals**: Keep dashboard bundle growth under 5 KB gzip for this feature; no extra API calls beyond existing skill/template lookups.  
**Constraints**: Maintain backwards-compatible queue payload contract (`type="task"`); orchestrator payload stays `{ instruction, targetService, priority, approvalToken? }`.  
**Scale/Scope**: Form is used dozens of times per day by operations staff; drafts should hold several kilobytes of text without lag; must obey current auth/session model.

## Constitution Check

`.specify/memory/constitution.md` is a placeholder with no ratified principles, so there are no blocking gates. Continue following internal quality bars (tests required, lint clean) and re-run this gate once a real constitution lands.

## Project Structure

### Documentation (feature artifacts)

```text
specs/038-submit-runtime-selector/
├── spec.md
├── plan.md                # this document
├── research.md            # Phase 0 decisions
├── data-model.md          # SubmitTargetOption + draft state
├── quickstart.md          # manual QA + validation steps
└── contracts/
    └── submit-work-form.md    # payload routing + validation contract
```

### Source Code & Runtime Assets

```text
api_service/
├── api/routers/task_dashboard.py        # serves dashboard + runtime config
├── api/routers/task_dashboard_view_model.py  # exposes supportedTaskRuntimes/defaults
└── static/task_dashboard/
    ├── dashboard.js                     # renderSubmitWorkPage + helpers
    ├── dashboard.tailwind.css           # styles for submit form
    └── templates/task_dashboard.html    # shell that hosts the SPA

tests/task_dashboard/
├── __fixtures__/                        # shared DOM + config helpers
└── test_submit_runtime.js               # new runtime selector tests

tools/test_unit.sh                       # master script for pytest + JS tests
```

**Structure Decision**: Implement entirely inside the existing dashboard bundle so both legacy routes get the same code path. No new backend endpoints, bundles, or node modules are required.

## Implementation Strategy

### 1. Shared `SubmitWorkForm` shell (FR-001, FR-002)
- Create `renderSubmitWorkPage(presetRuntime)` that both `/tasks/queue/new` and `/tasks/orchestrator/new` call (replace their duplicated markup).  
- Read `supportedTaskRuntimes` / `defaultTaskRuntime` from the parsed dashboard config; append a hard-coded `orchestrator` option if it is not already listed.  
- Render the runtime `<select>` and shared instruction textarea before the two conditional sections so switching runtimes never loses the objective text.  
- Ensure the `setView` title/description emphasize “Submit Work” to keep copy consistent on both routes.

### 2. Worker field group (FR-003, FR-005, FR-007)
- Move the existing queue submit markup (step editor, preset drawer, repo/branch inputs, publish mode, priority/maxAttempts, propose tasks checkbox) into a `<section data-submit-section="worker">`.  
- Reuse helper utilities already in `dashboard.js` (`createStepStateEntry`, `parseCapabilitiesCsv`, template catalog helpers) so migrations do not fork logic.  
- When runtime is a worker option, keep the section visible and bind `instruction` to the primary step so both fields stay in sync.  
- Validation and payload serialization remain identical to the current queue submit flow; ensure `repository` fallback still respects `defaultRepository` and `isValidRepositoryInput` guard rails.

### 3. Orchestrator field group (FR-004, FR-006, FR-008)
- Add a `<section data-submit-section="orchestrator">` containing `targetService`, `priority` (enum), and optional `approvalToken`.  
- Introduce a helper `validateOrchestratorSubmission` that trims inputs, enforces required fields, normalizes priority, and strips blank tokens.  
- On orchestrator runtime selection, hide worker-only DOM, update the primary button label ("Submit Orchestrator Run"), and skip queue-specific validation.

### 4. Draft management + runtime switch (FR-005, FR-006)
- Implement `createSubmitDraftController(workerDefaults, orchestratorDefaults)` that deep-clones stored drafts to avoid mutation leaks.  
- On runtime change, save the current section’s state, swap visibility, load the other draft, and rehydrate inputs (steps array, preset state, orchestrator fields).  
- Keep runtime-specific defaults (model/effort) per worker runtime; when switching worker runtimes update placeholders only if the user has not overridden the prior default.  
- Maintain instructions text as the single source of truth per runtime to satisfy in-memory draft preservation acceptance tests.

### 5. Submission routing + error handling (FR-007, FR-008, FR-009)
- Create `determineSubmitDestination(runtimeValue, { queue, orchestrator })` returning `{ endpoint, mode }`.  
- Worker submission path: build the existing `{ type: "task", payload, priority, maxAttempts }` body and POST to `sources.queue.create || "/api/queue/jobs"`, redirecting to `/tasks/queue/:id`.  
- Orchestrator path: call `validateOrchestratorSubmission`, POST to `sources.orchestrator.create || "/orchestrator/runs"`, expect a `runId`/`id`, and redirect to `/tasks/orchestrator/:runId`.  
- In both flows, convert API errors into inline notices without clearing drafts, and keep debug logs in `console.error` to aid troubleshooting.

### 6. Testing & verification (FR-011)
- Extend `tests/task_dashboard/test_submit_runtime.js` to cover:  
  - `createSubmitDraftController` preserving independent worker/orchestrator drafts (deep clone).  
  - `determineSubmitDestination` returning the proper endpoint/mode.  
  - `validateOrchestratorSubmission` enforcing `instruction` + `targetService` and normalizing `priority`/`approvalToken`.  
- Wire the test into `./tools/test_unit.sh` if not already (Node harness).  
- Manual QA checklist (captured in quickstart) should cover runtime toggling, worker submission, orchestrator submission, and error surfacing while watching the network tab to confirm endpoints.

## Complexity Tracking

No additional architectural complexity beyond the single shared form, so the table remains empty.
