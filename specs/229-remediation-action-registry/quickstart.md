# Quickstart: Remediation Action Registry

## Validation Commands

Run the focused MM-454 unit/service-boundary validation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py
```

Run the red/green focused checks used during implementation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py -k 'lists_policy_compatible_actions or enforces_profile_permissions_and_risk'
```

## Expected Coverage

The tests should verify:
- policy-compatible action listing returns typed risk/input metadata;
- action requests validate links, permissions, profile state, risk, approval, and idempotency;
- high-risk actions require approval;
- unsupported raw access fails closed;
- duplicate requests reuse the original decision;
- request/result/audit payloads are v1-shaped and redaction-safe;
- prepared action context can be evaluated against the authority service.
