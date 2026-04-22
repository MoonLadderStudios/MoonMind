# Quickstart: Remediation Mission Control Surfaces

## Focused Frontend Tests

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx
```

Expected result:
- Target detail renders the Create remediation task action for eligible states.
- Target detail renders inbound Remediation Tasks metadata.
- Remediation detail renders target, evidence, lock, and approval panels.
- Non-remediation task detail and create behavior remain unchanged.

## Focused API Tests

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py
```

Expected result:
- Remediation create route still expands into the canonical task remediation contract.
- Inbound and outbound remediation link read surfaces return compact data.
- Approval decision route, if added, enforces permission and audit semantics.

## Traceability Check

```bash
rg -n "MM-437|STORY-007|Remediation Mission Control|DESIGN-REQ-00[1-8]" specs/224-remediation-mission-control
```

Expected result:
- The orchestration target, story ID, source summary, and source design mappings are present in spec, plan, tasks, and verification artifacts.

## Final Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py
```

Then run `moonspec-verify` for `specs/224-remediation-mission-control` and record the result in `verification.md`.
