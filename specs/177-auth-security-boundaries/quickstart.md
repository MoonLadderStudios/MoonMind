# Quickstart: Auth Security Boundaries

## Focused Unit Verification

Run the targeted MM-335 unit checks:

```bash
./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/workloads/test_workload_models.py tests/unit/workloads/test_docker_launcher.py
```

Expected evidence:

- Unauthorized provider-profile management calls return `403`.
- OAuth session responses remain owner-scoped and sanitized.
- Workload mount validation rejects auth-like volumes by default.
- Workload stdout/stderr, diagnostics, and result metadata redact secret-like values.

## Full Unit Verification

Run the required unit suite before finalizing:

```bash
./tools/test_unit.sh
```

## Integration Verification

Run hermetic integration only if implementation touches Temporal workflow/activity boundaries:

```bash
./tools/test_integration.sh
```

No provider credentials are required for MM-335 verification.
