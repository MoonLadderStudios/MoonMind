# Quickstart: Claude Surfaces Handoff

## Focused Unit Tests

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_surfaces_handoff.py
```

Expected result: tests fail before implementation because MM-348 surface lifecycle exports and helpers are missing, then pass after implementation.

## Focused Integration-Style Boundary Test

```bash
pytest tests/integration/schemas/test_claude_surfaces_handoff_boundary.py -q
```

Expected result: tests fail before implementation because `build_claude_surface_handoff_fixture_flow` is missing, then pass after implementation.

## Full Required Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
./tools/test_integration.sh
```

The integration runner requires Docker access. If Docker is unavailable in the managed agent container, record the exact blocker and rely on focused integration-style pytest evidence.
