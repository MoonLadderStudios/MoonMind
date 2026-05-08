# Quickstart: Canonical Remediation Submissions

## Purpose

Verify the MM-617 single story: canonical remediation submissions preserve nested remediation metadata, pin target run identity, persist durable directed links, reject invalid inputs, expose inbound/outbound relationship records, and do not behave like dependencies.

## Focused Unit Verification

Run the focused unit and integration-boundary checks:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py
```

Expected coverage:
- Valid remediation creation preserves `task.remediation` and exactly one link.
- Omitted target run ID is resolved before workflow start.
- Invalid target, self-reference, nested remediation, authority, action policy, and taskRunIds fail before workflow start.
- Inbound and outbound relationship reads expose compact fields.
- Remediation creation does not create dependency prerequisites.

## Integration Strategy

The story's integration boundary is the FastAPI route plus async Temporal execution service/persistence boundary. Existing pytest coverage in the unit runner exercises these boundaries without requiring external credentials or compose-backed providers.

Run compose-backed integration only if downstream implementation changes touch artifact lifecycle, service topology, or API behavior outside the existing router/service boundary:

```bash
./tools/test_integration.sh
```

## Test-First Contingency

If focused verification fails:
1. Add or update the smallest failing router or service test first.
2. Confirm the new test fails for the intended MM-617 behavior.
3. Update only the affected boundary: `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py`, `api_service/db/models.py`, or the relevant migration.
4. Rerun the focused command before final verification.

## End-To-End Story Check

A valid remediation create request against an existing target should produce:
- a normal MoonMind run with nested remediation metadata,
- a pinned target run ID,
- a durable directed remediation link,
- no dependency prerequisites,
- inbound and outbound relationship records with compact lifecycle fields,
- no raw evidence bodies, storage paths, presigned URLs, or secrets in link metadata.

## Traceability Check

Final verification and pull request metadata must include `MM-617` and the active feature path:

```text
specs/317-canonical-remediation-submissions
```
