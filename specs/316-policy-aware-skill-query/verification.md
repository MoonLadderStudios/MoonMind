# Verification: Policy-Aware Skill Query

**Feature**: `specs/316-policy-aware-skill-query`
**Jira**: MM-613
**Verdict**: FULLY_IMPLEMENTED
**Verified**: 2026-05-08

## Summary

MM-613 is implemented for the selected single story. Managed-runtime Skills On Demand query support now returns policy-aware, metadata-only Skill catalog results when enabled, preserves disabled behavior, validates blank input, marks source-policy ineligible matches, marks current snapshot membership when active snapshot context is supplied, keeps results bounded, and does not materialize or mutate Skill snapshots.

## Requirement Coverage

| ID | Verdict | Evidence |
| --- | --- | --- |
| FR-001 | VERIFIED | `AgentSkillsActivities.query_on_demand` remains the governed activity path and delegates to `SkillsOnDemandService.query`. |
| FR-002 | VERIFIED | `SkillsOnDemandQueryRequest` preserves bounded `max_results`; service rejects blank queries and snapshot mismatches. |
| FR-003 | VERIFIED | `SkillCatalogSearchResult` provides typed metadata-only result fields; enabled query returns `status="ok"`. |
| FR-004 | VERIFIED | Tests assert result serialization omits body refs, content digests, and source paths. |
| FR-005 | VERIFIED | Active snapshot membership is derived from supplied compact snapshot context. |
| FR-006 | VERIFIED | Resolver-backed `query_catalog` reuses source loaders and precedence; service marks repo/local sources ineligible when disabled. |
| FR-007 | VERIFIED | Ineligible local matches return `eligible=false` and a compact safe diagnostic. |
| FR-008 | VERIFIED | Activity-boundary test patches materializer and asserts query does not materialize. |
| FR-009 | VERIFIED | Query result count honors accepted `max_results`; metadata reports result count. |
| FR-010 | VERIFIED | Query result includes compact metadata with result count, denial state, and normalized query hash. |
| FR-011 | VERIFIED | `MM-613` and canonical Jira preset brief are preserved in spec, plan, tasks, and this verification report. |

## Source Design Coverage

| Source ID | Verdict | Evidence |
| --- | --- | --- |
| DESIGN-REQ-002 | VERIFIED | Query discovers Skill metadata without body/content-ref exposure. |
| DESIGN-REQ-003 | VERIFIED | Query is side-effect free and does not mutate/materialize snapshots. |
| DESIGN-REQ-010 | VERIFIED | Query/result contracts validate input and return metadata with eligibility and snapshot indicators. |
| DESIGN-REQ-013 | VERIFIED | Activity-boundary query checks enabled mode, searches resolver-backed metadata, and returns bounded results. |
| DESIGN-REQ-014 | VERIFIED | Security guardrails are covered by metadata-only projection and ineligible diagnostics tests. |

## Test Evidence

- `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`: PASS, 12 Python tests passed; frontend suite also passed through the unit runner.
- `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py tests/unit/services/test_skill_resolution.py`: PASS, 39 Python tests passed; frontend suite also passed through the unit runner.
- `./tools/test_unit.sh`: PASS, 4509 Python tests passed, 1 xpassed, 16 subtests passed; frontend Vitest suite passed with 20 files and 324 tests passed, 223 skipped.

## Notes

- Red-first evidence: the first focused run failed during collection because `SkillCatalogSearchResult` was missing, confirming the new typed result contract gap before implementation.
- `python -m ruff check ...` was attempted for touched files, but `ruff` is not installed in this runtime.
- `.specify/scripts/bash/setup-plan.sh --json` and `.specify/scripts/bash/update-agent-context.sh codex` could not run because this managed branch name is not in numeric MoonSpec branch format; artifacts were created under the resolved `specs/316-policy-aware-skill-query` directory instead.
