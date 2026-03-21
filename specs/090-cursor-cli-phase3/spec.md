# Spec: Cursor CLI Phase 3 — Auth Profile Support

**Source Document**: [CursorCli.md](file:///Users/nsticco/MoonMind/docs/ManagedAgents/CursorCli.md)
**Phase**: 3 of 5

---

## Document Requirement Identifiers

| ID | Source Section | Requirement |
|----|---------------|-------------|
| DOC-REQ-P3-001 | §5 Auth Profile Seeding | Seed default `cursor_cli` auth profile row in `managed_agent_auth_profiles` table |
| DOC-REQ-P3-002 | §5 AuthProfileManager Startup | Ensure `AuthProfileManager` starts for `cursor_cli` runtime on first agent run |
| DOC-REQ-P3-003 | §5 Docker Compose | `cursor_auth_volume` available in docker-compose (completed in Phase 1) |

---

## User Stories

### US1: Default Auth Profile for Cursor CLI
**As a** MoonMind operator deploying Cursor CLI  
**I want** a default auth profile seeded in the database for `cursor_cli`  
**So that** the `AuthProfileManager` can find and use it when the first Cursor CLI agent run starts

### US2: Automatic AuthProfileManager Startup
**As a** MoonMind agent runtime system  
**I want** the `AuthProfileManager` to auto-start for `cursor_cli` when the first agent run requests it  
**So that** slot leases and rate limiting work identically to other managed runtimes

---

## Functional Requirements

| ID | Requirement | DOC-REQ | Testable |
|----|-------------|---------|----------|
| FR-001 | Alembic migration inserts a default `cursor_cli` row into `managed_agent_auth_profiles` with `auth_mode=api_key`, `max_parallel_runs=1`, `rate_limit_policy=backoff` | DOC-REQ-P3-001 | Unit test |
| FR-002 | Migration is reversible (downgrade removes the seeded row) | DOC-REQ-P3-001 | Unit test |
| FR-003 | Existing `auth_profile.ensure_manager` activity starts `AuthProfileManager` for `runtime_id="cursor_cli"` without code changes | DOC-REQ-P3-002 | Existing test (runtime_id agnostic) |
| FR-004 | `auth_profile.list` activity returns cursor_cli profiles from DB | DOC-REQ-P3-002 | Existing code path |

---

## Success Criteria

| # | Criterion | Verification |
|---|-----------|-------------|
| SC-1 | Alembic migration applies cleanly via `alembic upgrade head` | Migration file structure validation |
| SC-2 | Default `cursor_cli` profile row exists with correct attributes | Unit test validates migration SQL |
| SC-3 | All existing unit tests continue to pass (`./tools/test_unit.sh`) | CLI verification |
