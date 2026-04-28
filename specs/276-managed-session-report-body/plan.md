# Implementation Plan: Managed Session Report Body

## Technical Context

**Language/Runtime**: Python 3.12, Pydantic v2, Temporal activity boundaries
**Primary Files**: `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, `tests/unit/workflows/temporal/test_activity_runtime.py`
**Storage**: Existing artifact store and managed-session metadata only; no new storage.

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The fix carries provider-authored final text; it does not synthesize an alternate report.
- **IV. Own Your Data**: PASS. Text remains in local MoonMind artifact/metadata paths.
- **IX. Resilient by Default**: PASS. Session-summary enrichment is best-effort and does not fail terminal runs.
- **X. Facilitate Continuous Improvement**: PASS. Report artifacts become meaningful terminal evidence.
- **XI. Spec-Driven Development**: PASS. This feature directory records the runtime change and verification.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. This implementation plan stays feature-local.
- **XIII. Pre-release Compatibility Policy**: PASS. No compatibility alias or semantic fallback is introduced; the existing report body precedence is completed.

## Implementation

1. Add a small activity-runtime helper that, for managed-session run records, calls `fetch_session_summary` through the configured session controller and extracts bounded `lastAssistantText` metadata.
2. Merge meaningful assistant text into fetch-result metadata as `lastAssistantText` and `operator_summary` before `agent_runtime.publish_artifacts` runs.
3. Keep failures to load session metadata as debug-only best-effort behavior.
4. Add tests for fetch-result enrichment and report bundle publication from `lastAssistantText`.

## Verification

- Focused unit tests for the changed activities.
- Full `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before publishing.
