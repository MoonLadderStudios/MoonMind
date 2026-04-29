# Quickstart: Promote Proposals Without Live Preset Drift

1. Run targeted service and API tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py
```

2. Run the final unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

3. Verify traceability:

```bash
rg -n "MM-560|DESIGN-REQ-014|DESIGN-REQ-018|DESIGN-REQ-019" specs/280-promote-proposals-no-drift
```
