# Quickstart: Isolate Spec Kit References and Skill-First Runtime

## 1. Validate canonical and legacy workflow API routes

- Call canonical route `GET /api/workflows/runs` and verify payload semantics.

## 2. Validate adapter resolution behavior

- Configure a non-agentkit skill override for a workflow stage.
- Run stage dispatch.
- Verify stage execution records skill-path execution through adapter resolution.
- Configure unsupported skill and verify fast failure with adapter-missing error.

## 3. Validate Agentkit dependency isolation

- In an environment without Agentkit installed, execute non-agentkit runtime preflight and stage flow.
- Verify startup and stage flow do not fail due to missing Agentkit.
- Configure agentkit-selected flow and verify missing Agentkit fails with clear error.

## 4. Run unit tests

```bash
./tools/test_unit.sh
```

## 5. Run runtime scope gate

```bash
.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```
