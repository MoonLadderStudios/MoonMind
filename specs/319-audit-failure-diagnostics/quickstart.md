# Quickstart: Skills On Demand Audit and Diagnostics

## Prerequisites

- Run from the repository root.
- Use managed-agent local test mode where relevant: `MOONMIND_FORCE_LOCAL_TESTS=1`.
- No external provider credentials are required.

## Unit Test Strategy

Add focused unit tests first under `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`.

Expected coverage:

- Query emits exactly one `skills_on_demand.query` event for successful and denied paths.
- Query audit evidence includes `query_hash` and omits raw long query text.
- Request emits exactly one `skills_on_demand.request` event for `denied`, `no_change`, and `activated` paths.
- Failure diagnostics expose stable codes/messages, safe current snapshot refs, and diagnostics refs when available.
- Audit/diagnostic payloads do not expose secrets, Skill bodies, hidden source paths, unrestricted artifact refs, or repo projection mutation details.

Focused iteration command:

```bash
./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py
```

Final unit command:

```bash
./tools/test_unit.sh
```

## Integration Test Strategy

Add or extend hermetic Temporal activity-boundary tests under `tests/integration/temporal/`.

Expected coverage:

- Disabled feature request/query emits bounded audit evidence and preserves the active snapshot.
- Allowed request activation emits one request event with compact snapshot/materialization refs.
- Materialization failure emits `materialization_failed` diagnostics and preserves the previous snapshot/projection.
- Runtime refresh failure emits `runtime_refresh_failed` diagnostics and preserves the previous snapshot.
- Audit evidence from activity invocation shape remains compact and redacted.

Focused iteration command:

```bash
pytest tests/integration/temporal/test_skills_on_demand_disabled.py tests/integration/temporal/test_skills_on_demand_request_activation.py -m "integration_ci" -q --tb=short
```

Final integration command:

```bash
./tools/test_integration.sh
```

## End-to-End Validation

1. Confirm `specs/319-audit-failure-diagnostics/spec.md` preserves `MM-616` and the original Jira preset brief.
2. Run focused unit tests and confirm audit/diagnostic assertions fail before implementation.
3. Implement bounded query/request audit event and diagnostic contracts.
4. Run focused unit tests until they pass.
5. Run focused integration activity-boundary tests until they pass.
6. Run `./tools/test_unit.sh`.
7. Run `./tools/test_integration.sh` or record the exact local blocker.

Expected result: every exercised Skills On Demand query/request path produces exactly one bounded event, stable failure diagnostics are available for denied and failed paths, active snapshots are preserved on failures, and no audit or diagnostic output exposes secrets, Skill bodies, raw long query text, arbitrary artifact/database access, or repo-authored projection mutations.
