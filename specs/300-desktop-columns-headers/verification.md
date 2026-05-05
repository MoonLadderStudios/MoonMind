# MoonSpec Verification Report

**Feature**: `specs/300-desktop-columns-headers`  
**Original Request Source**: `spec.md` Input preserving trusted Jira preset brief for `MM-587`  
**Verdict**: FULLY_IMPLEMENTED  
**Verified At**: 2026-05-05

## Summary

The MM-587 Tasks List desktop table story is fully implemented. The normal table keeps the task-focused columns, excludes Kind, Workflow Type, Entry, and Started, separates every visible header into sort and filter targets, provides header popovers for Status, Repository, and Runtime filters, keeps active filters as clickable chips, and sends `targetRuntime` through the task-scoped Temporal list query.

## Requirement Coverage

| ID | Status | Evidence |
| --- | --- | --- |
| FR-001 | VERIFIED | `TABLE_COLUMNS` in `frontend/src/entrypoints/tasks-list.tsx`; `tasks-list.test.tsx` table column assertions |
| FR-002 | VERIFIED | UI tests assert Kind, Workflow Type, Entry, and Started are absent |
| FR-003 | VERIFIED | Compound header markup in `tasks-list.tsx`; `separates desktop header sorting from filter popovers` |
| FR-004 | VERIFIED | Sort button test confirms sort changes without opening Scheduled filter |
| FR-005 | VERIFIED | Filter button test confirms Repository popover opens without changing Scheduled sort |
| FR-006 | VERIFIED | Existing and updated UI tests confirm `aria-sort` and visible sort state |
| FR-007 | VERIFIED | Existing scheduled sort test and timestamp sort logic preserved |
| FR-008 | VERIFIED | Header popovers provide Status, Repository, and Runtime filter controls; top filter form removed |
| FR-009 | VERIFIED | Active chips are buttons and reopen matching filter popovers |
| FR-010 | VERIFIED | Clear filters clears status, repository, runtime, and resets list state |
| FR-011 | VERIFIED | Existing status pill, date, task link, runtime label, and dependency summary tests pass |
| FR-012 | VERIFIED | Existing legacy scope normalization tests pass; API runtime filter remains combined with `scope=tasks` |
| FR-013 | VERIFIED | `MM-587` is preserved in spec, plan, tasks, and this verification report |
| DESIGN-REQ-006 | VERIFIED | Row model and dependency metadata remain covered by UI tests |
| DESIGN-REQ-007 | VERIFIED | Desktop table sort, status pill, timestamp, and dependency behaviors pass |
| DESIGN-REQ-008 | VERIFIED | Column header filters and chips replace top status/repository filters; live updates and pagination remain outside filters |
| DESIGN-REQ-010 | VERIFIED | Desired default columns are preserved; excluded columns remain absent |
| DESIGN-REQ-011 | VERIFIED | Separate sort/filter controls, indicators, `aria-sort`, and single primary sort are covered |
| DESIGN-REQ-027 | VERIFIED | No system workflow browsing or multi-column sort controls were introduced |

## Validation Commands

| Command | Result | Notes |
| --- | --- | --- |
| `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx` | NOT RUN | The managed workspace path contains a colon, which breaks npm's PATH lookup for `node_modules/.bin`; direct bin invocation was used instead. |
| `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx` | PASS | 19 tests passed |
| `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/mission-control.test.tsx` | PASS | 49 tests passed |
| `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json` | PASS | TypeScript frontend typecheck passed |
| `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py` | PASS | 132 Python tests passed; full frontend suite also passed through the wrapper |
| `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` | PASS | 4324 Python tests passed, 1 xpassed, 16 subtests passed; 19 frontend test files passed |

## Residual Risk

Advanced filter types from the broader desired design, such as date ranges and full value checklists for every visible column, remain outside this MM-587 first-story scope. The implemented story covers the required compound header model and the active Status, Repository, and Runtime filters.
