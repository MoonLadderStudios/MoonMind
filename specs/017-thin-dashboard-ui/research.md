# Research: Thin Dashboard Task UI

## Decision 1: Host MVP dashboard from existing FastAPI service

- **Decision**: Add a new router that serves dashboard pages from `api_service/templates/` and static assets from `api_service/static/`.
- **Rationale**: This avoids introducing a new frontend build/deploy pipeline and delivers Strategy 1 quickly.
- **Alternatives considered**:
  - Dedicated Next.js container as MVP: rejected for initial implementation due to higher setup and deployment complexity.
  - Open-WebUI plugin customization first: rejected due to higher coupling and slower iteration.

## Decision 2: Use one HTML shell plus client-side path rendering

- **Decision**: Serve one dashboard shell template for `/tasks` and `/tasks/...` routes, with JavaScript deciding which list/form/detail panel to render.
- **Rationale**: Keeps server route handlers thin while still exposing concrete URLs for each page.
- **Alternatives considered**:
  - Many server-rendered templates: rejected due to repetitive view logic and slower iteration.
  - Hash-only routing: rejected because it hides route intent and complicates direct links.

## Decision 3: Normalize source statuses in frontend adapter

- **Decision**: Implement a frontend normalization layer (`DashboardRun`) that maps queue/orchestrator statuses to a common display status and exposes queue skill ids for SpecKit task discovery.
- **Rationale**: Enables one consolidated running view without backend schema changes.
- **Alternatives considered**:
  - Add a unified backend runs API now: rejected for MVP because Strategy 1 should avoid backend re-modeling.

## Decision 4: Polling-first updates with partial failure handling

- **Decision**: Use polling intervals (lists: ~5s, detail: ~2s, queue events: ~1s) and keep rendering healthy sources when one source fails.
- **Rationale**: Existing APIs already support this and no push infrastructure is required.
- **Alternatives considered**:
  - SSE first: deferred to future phase; not required for Strategy 1 MVP.
  - WebSockets first: rejected as unnecessary complexity for current requirements.

## Decision 5: Keep auth model user-context only for dashboard flows

- **Decision**: Dashboard requests use user-authenticated context and never use worker-token endpoints for user actions.
- **Rationale**: Aligns with existing API boundaries and avoids accidental privilege misuse.
- **Alternatives considered**:
  - Reusing worker tokens from UI: rejected for security and ownership reasons.

## Decision 6: Validate with unit tests through project-standard script

- **Decision**: Add focused unit tests for dashboard router/template rendering and status normalization helper logic, run via `./tools/test_unit.sh`.
- **Rationale**: Matches repository testing guidance and keeps verification lightweight for MVP.
- **Alternatives considered**:
  - Browser e2e as MVP gate: deferred due to higher execution cost and setup complexity.
