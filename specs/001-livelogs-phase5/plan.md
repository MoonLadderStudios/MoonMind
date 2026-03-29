# Implementation Plan: Fully implement Phase 5 from docs/tmp/009-LiveLogsPlan.md

## Technical Context
We are implementing Phase 5 of the Live Logs pipeline which requires cleanly separating observation (Live Logs) from control (Intervention Panels). The application uses Temporal for orchestration, FastAPI/Python for the backend, and React/Next.js/TypeScript for the frontend.

## Constitution Check
- The decoupling reduces complexity and aligns with standardizing on Temporal signals for operator inputs. No novel database or custom auth mechanisms are added.
- The separation conforms to Phase 5 of docs/tmp/009-LiveLogsPlan.md.

## Phase 0: Outline & Research
*(Research resolved: The Temporal signal endpoints `POST /api/task-runs/{id}/signal` already exist. The UI just needs to invoke them independently of the `LiveLogs` component.)*

## Phase 1: Design & Contracts
- The UI will have an `InterventionPanel` component next to the `LiveLogs` panel.
- Pause/Resume and Cancel buttons will route to Temporal signals. 
- Logging for operator intervention will be integrated natively via Temporal workflow history, bypassing stdout.

## Phase 2: Implementation Steps
1. Create frontend Intervention controls uncoupled from the terminal viewer.
2. Update backend routers if necessary to ensure operator intervention triggers Temporal signals.
3. Remove legacy `tmate` or PTY-based text-injection commands for control flow.
4. Verify tests pass without live log connections.
