# Tasks: Proxy-First Execution Paths

- [x] T001 Update `moonmind/workflows/tasks/adapters/managed_agent_adapter.py` to identify proxy-compatible `ProviderProfile` structures (DOC-REQ-002, DOC-REQ-003)
- [x] T002 In `ManagedAgentAdapter`, suppress `db_encrypted:` extraction and inject `MOONMIND_PROXY_TOKEN` and `ANTHROPIC_BASE_URL` if proxy mode is supported (DOC-REQ-004)
- [x] T003 Expose `/api/v1/proxy/anthropic/{path}` route in `api_service/api/routers/secrets.py` (or new `proxy.py`) mapping the token back to the actual db secret for pass-through (DOC-REQ-003)
- [x] T004 Validation: Add tests in `tests/unit/` showing proxy token fallback, verifying environments don't leak raw `db_encrypted:` when proxy mode enabled
- [x] T005 Update `docs/Security/ProviderProfiles.md` classifying escape-hatch runtimes vs proxy-first runtimes (DOC-REQ-001, DOC-REQ-005)
