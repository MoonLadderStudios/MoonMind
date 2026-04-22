# Quickstart: Remediation Evidence Tools

Run focused verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py
```

Expected result:

- remediation context builder tests pass,
- typed evidence tools read only context-declared target artifacts,
- typed evidence tools read only context-declared taskRunIds,
- live follow is rejected until the context marks it supported,
- live follow returns a resume cursor handoff when supported.
