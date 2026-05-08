# Implementation Notes: Remediation Action Contracts

**Traceability**: Jira issue `MM-620`; feature path `specs/320-remediation-action-contracts`.

## Red-First Evidence

- Focused unit command:
  `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/workflows/temporal/test_temporal_service.py -q`
- Initial result: failed before production changes for the expected gaps:
  missing top-level v1 request artifact fields, unsupported executor result status accepted, duplicate idempotency key with changed request shape not denied, raw `redaction_bypass` classified as a generic unsupported action, and unsupported action parameter accepted.
- Integration command:
  `pytest tests/integration/temporal/test_remediation_action_contracts.py -q`
- Initial result: failed before production changes for the expected gaps:
  missing top-level v1 request artifact fields and raw `redaction_bypass` not returning `raw_access_action_denied`.

## Implementation Evidence

- Added unit coverage for v1 request/result artifacts, status validation, idempotency request-shape reuse, raw-operation denial classes, and action parameter validation in `tests/unit/workflows/temporal/test_remediation_context.py`.
- Added hermetic integration coverage for the real remediation action artifact boundary in `tests/integration/temporal/test_remediation_action_contracts.py`.
- Updated `moonmind/workflows/temporal/remediation_actions.py` to reject changed request shapes for reused idempotency keys, validate action parameters against catalog input metadata, and classify raw volume/network/secret/redaction-bypass operations as raw access denials.
- Updated `moonmind/workflows/temporal/remediation_tools.py` to publish the v1 request payload at the artifact top level, validate result status values fail-closed, publish complete v1 result evidence, and redact sensitive request/result payload content.

## Verification Evidence

- Focused unit command after implementation:
  `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/workflows/temporal/test_temporal_service.py -q`
- Result: PASS.
- Targeted integration command after implementation:
  `pytest tests/integration/temporal/test_remediation_action_contracts.py -q`
- Result: PASS, 2 tests.
- Integration wrapper command after implementation:
  `./tools/test_integration.sh tests/integration/temporal/test_remediation_action_contracts.py`
- Result: BLOCKED by managed Docker environment. Docker Compose reached image build, then the daemon returned `403 Forbidden` with "Request forbidden by administrative rules."
- Full unit command after implementation:
  `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Result: PASS; Python unit suite reported `4542 passed, 1 xpassed, 107 warnings, 16 subtests passed`, and frontend unit suite reported `20 passed` test files with `324 passed | 223 skipped`.

## Quickstart Validation

- The quickstart unit flow was validated by the focused unit and full unit wrapper results above.
- The quickstart integration behavior was validated directly through `pytest tests/integration/temporal/test_remediation_action_contracts.py -q`; the repository Docker wrapper remains blocked in this managed runtime by daemon policy.
