# Prompt B Remediation Summary

**Date**: 2026-04-03
**Input**: `/mnt/d/code/MoonMind/specs/127-codex-openrouter-phase3/remediation-a-v2.md`

---

## Issues Addressed

### CRITICAL-01: No task covers `test_managed_agent_adapter.py` (16 `auth_mode` references)

**Fix**: Added **T003b** — "Migrate `test_managed_agent_adapter.py` from `auth_mode` to `credential_source`" in `tasks.md`. Enumerates all 16 line references, specifies fixture dict key replacements and the assertion update at line ~185. Depends on T002.

### HIGH-01: Spec FR-007 silent on outgoing API response `auth_mode` emission

**Fix**: Extended **FR-007** in `spec.md` to explicitly state: outgoing API responses MUST NOT emit a derived `auth_mode` field alongside `credential_source`; `credential_source` is the sole canonical field. Any existing code path that projects `auth_mode` into API response dicts (e.g., `build_canonical_start_handle` in `artifacts.py`) MUST be updated to emit only `credential_source`.

### HIGH-02: Plan conflates adapter metadata dict vs DB-to-API response projection

**Fix**: Split the single "Metadata consumer audit" step in plan.md §3.2 into two distinct steps:
- **Step 4**: Adapter metadata dict consumer audit — grep for consumers of `metadata["auth_mode"]` from the adapter's output dict.
- **Step 5**: API response projection audit (`artifacts.py`) — review `build_canonical_start_handle` which projects DB rows directly to API response dicts (a separate data flow from the adapter metadata dict).

### HIGH-03: No task addresses `artifacts.py` line 2279 dual emission

**Fix**: Added **T003c** — "Remove legacy `auth_mode` from outgoing API response in `artifacts.py`" in `tasks.md`. Specifies removing the derived `auth_mode` key from the `build_canonical_start_handle` profile projection dict, per Constitution Principle XIII (pre-release: delete, don't deprecate). Depends on T001.

---

## Artifacts Modified

| File | Changes |
|------|---------|
| `specs/127-codex-openrouter-phase3/tasks.md` | Added T003b, T003c; updated dependency graph, execution order, complexity table, and runtime scope validation |
| `specs/127-codex-openrouter-phase3/plan.md` | Split §3.2 step 4 into two distinct audit steps (adapter metadata dict vs API response projection) |
| `specs/127-codex-openrouter-phase3/spec.md` | Extended FR-007 to cover outgoing API response behavior (no derived `auth_mode`) |

---

## Determination

**Safe to Implement: YES** — all four blocking issues from the remediation report are resolved.
