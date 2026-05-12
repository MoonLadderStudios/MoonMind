# MM-655 Verification Evidence

Date: 2026-05-12

## Focused Story Tests

- `pytest tests/unit/services/test_settings_catalog.py tests/unit/specs/test_mm655_traceability.py -q`
  - Result: passed, `62 passed`.
- `pytest tests/unit/api_service/api/routers/test_settings_api.py -q`
  - Result: passed, `30 passed`.
- `pytest tests/integration/api/test_settings_effective_values_contract.py tests/integration/api/test_settings_overrides_contract.py -m integration_ci -q`
  - Result: passed, `3 passed`.

## Repo Unit Verification

- `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
  - Python result: passed, `4877 passed, 1 xpassed, 114 warnings, 16 subtests passed`.
  - Frontend result: passed, `20 passed` test files, `338 passed`, `227 skipped`.

## Integration Verification

- `./tools/test_integration.sh`
  - Result: blocked by managed Docker environment.
  - Evidence: Docker Compose attempted to build `repo-pytest`, then Docker returned `403 Forbidden` with `Request forbidden by administrative rules`.
  - Mitigation: the MM-655 hermetic integration contract was still run directly with `pytest ... -m integration_ci -q` and passed.

## Scope

Verification covers `MM-655`, canonical source labels, operator locks, effective-value metadata, distinct diagnostics, SecretRef/provider-profile safety, and traceability to the original Jira preset brief.
