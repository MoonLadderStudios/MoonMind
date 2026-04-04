# Migration Tracker: 127-codex-openrouter-phase3

**Feature**: Codex OpenRouter Phase 3 — Generalization
**Branch**: `127-codex-openrouter-phase3`
**Created**: 2026-04-03
**Status**: In progress

## Purpose

Track in-flight implementation progress for Phase 3. This file should be removed once the feature ships and all tasks are merged.

## Migration Summary

The primary migration is aligning the `ManagedAgentProviderProfile` Pydantic model with the provider-profile contract:
- **Legacy field**: `auth_mode` (required) — removed entirely
- **Replacement field**: `credential_source` (required) — mapped from DB model
- **Legacy-to-new mapping** (one-time, data level): `oauth` → `oauth_volume`, `api_key` → `secret_ref`

## Implementation Tasks

| Task | Status | Notes |
|------|--------|-------|
| T001 — Rewrite `ManagedAgentProviderProfile` | pending | Remove `auth_mode`, add 13 new fields |
| T002 — Replace `auth_mode` in adapter | pending | 3 locations + metadata consumer audit |
| T003 — Update existing tests | pending | Replace `authMode` with `credentialSource` |
| T004 — Add validation tests | pending | 7 new test functions |
| T005 — Provider-agnostic code audit | pending | Grep audit of materializer/adapter/launcher |
| T006 — Multi-profile integration tests | pending | Depends on T001 |
| T007 — Full test suite + edge case audit + DB check | pending | Depends on T001–T004 |

## Data Verification Checklist

- [ ] Query `managed_agent_provider_profiles` for rows with NULL or invalid `credential_source`
- [ ] Confirm zero provider-specific branching in materializer/adapter/launcher
- [ ] Confirm all metadata consumers of `metadata["auth_mode"]` updated to `credential_source`

## DOC-REQ Traceability

| DOC-REQ | Tasks | Status |
|---------|-------|--------|
| DOC-REQ-001 (Reference implementation) | T005 | pending |
| DOC-REQ-002 (Additional model defaults) | T006 | pending |
| DOC-REQ-003 (Legacy auth-profile alignment) | T001, T002, T003, T004, T007 | pending |

## Remediation History

- **Remediation A** (2026-04-03): Initial remediation discovery — 3 CRITICAL, 4 HIGH, 5 MEDIUM findings
- **Remediation B** (2026-04-03): Applied all CRITICAL and HIGH items, plus MEDIUM items. Spec CRITICAL-01 (OAuthProviderSpec scoping), plan CRITICAL-01 (fields-to-retain), tasks CRITICAL-01 (T006 dependency). See `specs/127-codex-openrouter-phase3/remediation-b-summary.md`.
