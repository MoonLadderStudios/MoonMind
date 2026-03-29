# Implementation Plan: Fully implement Phase 6 from docs/tmp/009-LiveLogsPlan.md

## Technical Context
Phase 6 marks the deprecation and removal of legacy `tmate` and PTY/terminal-based observability hooks from the backend and frontend. 

## Phase 1: Design & Contracts
- The UI must handle missing artifacts correctly by either rendering nothing or displaying legacy text (it already skips live logs gracefully if unsupported).
- Any residual PTY endpoints in `proxy.py` or `.specify/` configuration pointing to `web_ro` can be retired.
- Python tests that simulated legacy data must test graceful degradation.

## Phase 2: Implementation Steps
1. Scrub references to `web_ro` and `TaskRunLiveSession`.
2. Delete unused fallback terminal endpoints for managed runs.
3. Validate Temporal tests pass omitting `tmate`.
