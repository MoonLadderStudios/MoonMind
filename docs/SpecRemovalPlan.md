# Spec Removal Status and Next Steps

## Purpose

This document is the current status report for the spec-removal migration. It is
not a completion record.

As of `2026-03-16`, the migration is only partially implemented:

- Docs/spec cleanup is largely complete.
- Runtime naming parity is still incomplete.
- Completion cannot be claimed until runtime verification passes.

The active implementation tracking artifacts for this work are:

- `specs/047-spec-removal/spec.md`
- `specs/047-spec-removal/plan.md`
- `specs/047-spec-removal/tasks.md`
- `specs/047-spec-removal/quickstart.md`

Some generated content inside those artifacts still references
`specs/040-spec-removal`. In this repository, those `040` references are stale
historical text and are not the authoritative feature path.

## Current Status

### Docs/spec migration status

The checked-in validation evidence records a docs/spec verification pass on
`2026-03-01`.

- Recorded command:

```bash
./tools/verify_workflow_naming.sh --mode docs-spec --exceptions-file specs/040-spec-removal/contracts/legacy-naming-exceptions.regex
```

- Recorded result:
  - `PASS`
  - `[docs-spec] PASS: No unapproved legacy naming matches found.`

This means the documentation/spec cleanup work was far enough along to pass the
docs/spec scan at that point in time.

### Runtime migration status

The checked-in validation evidence for the same date records that runtime
verification was still failing.

- Recorded command:

```bash
./tools/verify_workflow_naming.sh --mode runtime --exceptions-file specs/040-spec-removal/contracts/legacy-naming-exceptions.regex
```

- Recorded result:
  - `FAIL`
  - legacy tokens still present across runtime surfaces, including
    `moonmind/config/settings.py`, `api_service/api/routers/automation.py`, and
    `api_service/main.py`

This remains the authoritative status reflected by the repo artifacts. The
documented runtime migration is not complete.

## What Is Already Done

The following items are supported by checked-in repository evidence:

1. Canonical `/api/workflows/runs/*` usage exists in active tests and contracts.
2. `MOONMIND_CODEX_MODEL` exists in env/config surfaces such as
   `.env-template`, `docker-compose.yaml`, and `moonmind/config/settings.py`.
3. A database migration exists that renames `spec_workflow_*` tables to
   `workflow_*`.
4. The naming verification helper exists at `tools/verify_workflow_naming.sh`.
5. Validation artifacts and task tracking exist under `specs/047-spec-removal/`.

These are meaningful migration steps, but they do not constitute a finished
runtime rename across the full intended surface.

## What Remains

The active task list under `specs/047-spec-removal/tasks.md` still shows open
runtime work. At the time of this review, the remaining work includes:

### Foundational runtime work

- `T004-T006`
- Canonical env/settings updates
- Runtime schema/type normalization
- Artifact root handling updates

### Runtime validation work

- `T015-T016`
- Route/contract regression updates
- Runtime naming regression coverage for config, storage, and automation
  surfaces

### Runtime implementation work

- `T017-T021`
- Route family canonicalization
- Remaining env/settings cleanup
- Runtime schema identifier cleanup
- Metrics namespace cleanup
- Artifact directory naming cleanup

### Final polish and closeout

- `T027-T029`
- Final operational alias cleanup
- Contract wording alignment
- Final docs/spec/runtime scan evidence capture

Until these tasks are complete and runtime verification passes, the migration
must be treated as in progress.

## Known Gaps Still Present in the Repo

Current checked-in repo state still shows legacy or mixed naming in places that
matter to runtime parity, including:

1. Legacy `SPEC_SKILLS_*` aliases still exist in runtime configuration and test
   coverage.
2. Legacy `spec-automation` naming still appears in active contract/data-model
   documentation.
3. Runtime verification was explicitly recorded as failing in the active
   migration artifacts.

These gaps are why this document no longer claims the migration is complete.

## Proper Path Forward

The correct next step is to finish the remaining runtime migration work tracked
in `specs/047-spec-removal/tasks.md`, then re-run the required validation gates.

### Required implementation sequence

1. Complete the remaining runtime naming tasks:
   - `T004-T006`
   - `T015-T016`
   - `T017-T021`
   - `T027-T029`
2. Re-run runtime verification:

```bash
./tools/verify_workflow_naming.sh --mode runtime --exceptions-file specs/047-spec-removal/contracts/legacy-naming-exceptions.regex
```

3. Re-run required unit tests:

```bash
./tools/test_unit.sh
```

4. Update this document only after:
   - runtime verification passes
   - the required tests pass
   - the remaining tracked tasks are actually complete

## Historical Notes

Earlier versions of this document described the migration as a completed
codebase-wide rename. That was inaccurate relative to the checked-in repo state
and active task tracking.

This document now treats checked-in repository artifacts as authoritative and
uses them to separate completed docs/spec work from incomplete runtime work.
