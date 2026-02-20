# Quickstart: Isolate Spec Kit References and Skill-First Runtime

## 1. Validate canonical and legacy workflow API routes

- Call canonical route: `GET /api/workflows/runs`
- Call legacy route: `GET /api/workflows/speckit/runs`
- Verify both return equivalent payload semantics.
- Verify legacy response includes deprecation headers.

## 2. Validate adapter resolution behavior

- Configure a non-speckit skill override for a workflow stage.
- Run stage dispatch.
- Verify stage execution records skill-path execution through adapter resolution.
- Configure unsupported skill and verify fast failure with adapter-missing error.

## 3. Validate Speckit dependency isolation

- In an environment without Speckit installed, execute non-speckit runtime preflight and stage flow.
- Verify startup and stage flow do not fail due to missing Speckit.
- Configure speckit-selected flow and verify missing Speckit fails with clear error.

## 4. Run unit tests

```bash
./tools/test_unit.sh
```

## 5. Run runtime scope gate

```bash
.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```
