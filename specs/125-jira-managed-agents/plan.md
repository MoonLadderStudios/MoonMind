# Implementation Plan: Jira Tools for Managed Agents

**Branch**: `125-jira-managed-agents` | **Date**: 2026-04-03 | **Spec**: `specs/125-jira-managed-agents/spec.md`  
**Input**: Feature specification from `/specs/125-jira-managed-agents/spec.md`

## Summary

Implement trusted Jira tool execution for managed agents by adding a MoonMind-owned Jira integration package, wiring Jira actions into the existing MCP discovery/call router, resolving SecretRef-backed Jira bindings just in time inside the trusted tool path, and covering auth, retry, validation, ADF conversion, router dispatch, and redaction behavior with automated tests. The selected orchestration mode is **runtime**; docs-only output is a failing outcome for this feature.

## Technical Context

**Language/Version**: Python 3.10+ service code, Pydantic v2 models, existing FastAPI app/runtime  
**Primary Dependencies**: FastAPI, Pydantic v2, `httpx`, MoonMind SecretRef resolvers, existing MCP router/registry types  
**Storage**: Managed secrets store and environment-backed SecretRefs for Jira bindings; no new durable Jira-specific database tables  
**Testing**: `./tools/test_unit.sh` (required), plus focused unit/router coverage under `tests/config/`, `tests/unit/integrations/`, `tests/unit/mcp/`, and `tests/unit/api/`  
**Target Platform**: Docker Compose MoonMind API/runtime stack with existing TLS trust roots and Python dependencies  
**Project Type**: Backend runtime feature extending the API MCP surface and integration/service modules  
**Performance Goals**: Tool calls fail fast on validation/policy errors, use 10s connect and 30s read defaults, and keep retries bounded to a maximum of 3 attempts for transient Jira failures  
**Constraints**: Do not inject Jira tokens into managed agent environments; do not expose arbitrary Jira HTTP to normal agents; keep errors/logs sanitized; keep tool contracts strict; preserve existing Atlassian indexing/planning behavior outside this feature’s scope  
**Scale/Scope**: One trusted Jira binding and explicit Jira tool surface for managed agents, with both auth modes supported and no new workflow-level contract changes

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. Orchestrate, Don’t Recreate**: PASS. The feature adds a provider-specific adapter/tool boundary without changing MoonMind’s core execution model.
- **II. One-Click Agent Deployment**: PASS. Uses existing dependencies (`httpx`, TLS roots) already available in the repo/runtime path.
- **III. Avoid Vendor Lock-In**: PASS. Jira-specific behavior is isolated behind a dedicated integration package and MCP registry rather than spread across core execution code.
- **IV. Own Your Data**: PASS. Jira credentials remain operator-controlled through SecretRefs; no credential material is pushed into external SaaS beyond Jira requests themselves.
- **V. Skills Are First-Class and Easy to Add**: PASS. This feature extends the executable tool surface without changing agent instruction-skill semantics.
- **VI. Design for Evolution / Scientific Method Loop**: PASS. Auth resolution, tool request models, and retry behavior are covered by explicit tests and can be replaced independently.
- **VII. Powerful Runtime Configurability**: PASS. Auth mode, secret refs, policy allowlists, timeouts, and retry behavior are config-driven.
- **VIII. Modular and Extensible Architecture**: PASS. New Jira logic stays within dedicated integration, router, and test boundaries.
- **IX. Resilient by Default**: PASS. Bounded retries, structured errors, and verification tooling keep Jira behavior safe for unattended execution.
- **X. Facilitate Continuous Improvement**: PASS. Structured verify/error results and tests create operator-visible outcomes for future refinement.
- **XI. Spec-Driven Development**: PASS. `DOC-REQ-001` through `DOC-REQ-012` are carried through spec, plan, tasks, and traceability artifacts.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. The implementation uses the canonical Jira design doc as the source contract and keeps implementation planning here rather than modifying the doc into a migration tracker.

### Post-Design Re-Check

- PASS. Design keeps secrets at the trusted tool boundary and does not introduce workflow payload or managed-runtime credential leakage.
- PASS. Runtime scope is explicit through production code surfaces and validation tests.
- PASS. No constitution violations require complexity exceptions.

## Project Structure

### Documentation (this feature)

```text
specs/125-jira-managed-agents/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── jira-tools.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
└── api/routers/
    └── mcp_tools.py

moonmind/
├── config/
│   └── settings.py
├── integrations/
│   └── jira/
│       ├── __init__.py
│       ├── adf.py
│       ├── auth.py
│       ├── client.py
│       ├── errors.py
│       ├── models.py
│       └── tool.py
└── mcp/
    ├── __init__.py
    └── jira_tool_registry.py

tests/
├── config/test_atlassian_settings.py
├── unit/api/test_mcp_tools_router.py
├── unit/integrations/
│   ├── test_jira_auth.py
│   ├── test_jira_client.py
│   └── test_jira_tool_service.py
└── unit/mcp/test_jira_tool_registry.py
```

**Structure Decision**: Follow the design doc’s suggested Jira package split inside `moonmind/integrations/jira/`, then bridge that package into the existing MCP router through a dedicated Jira registry. Keep auth resolution, client transport, request models, and policy logic separate so secret-handling and retry behavior are testable at the adapter boundary.

## Phase 0 - Research Summary

`research.md` resolves these implementation decisions:

1. Use the existing MCP discovery and tool-call endpoints instead of creating a Jira-specific API surface for managed agents.
2. Add Jira-specific settings for tool enablement, policy controls, timeout/retry defaults, and SecretRef-backed credential bindings while keeping legacy Atlassian indexing settings intact.
3. Resolve Jira bindings just in time with MoonMind’s existing SecretRef resolver stack and raw-env fallback only for explicit local-development paths.
4. Implement a dedicated Jira REST client with bounded retry logic and redacted error handling rather than using `atlassian-python-api` or Forge MCP for the managed-agent mutation path.
5. Use strict Pydantic request models with `extra="forbid"` for input validation and normalize plain-text descriptions/comments into ADF only at the trusted tool boundary.
6. Keep project/action allowlist enforcement in the high-level Jira tool service rather than in the low-level HTTP client.

## Phase 1 - Design Outputs

- **Research decisions**: `research.md`
- **Data model**: `data-model.md`
- **Contract surface**: `contracts/jira-tools.openapi.yaml`
- **Traceability matrix**: `contracts/requirements-traceability.md`
- **Execution guide**: `quickstart.md`

## Implementation Strategy

### 1. Settings and binding resolution first

- Extend Atlassian settings with Jira tool enablement, policy, timeout/retry, and SecretRef binding fields.
- Normalize site URL and allow raw-env local-development fallback only when operators explicitly configure the Jira tool path.
- Fail fast on unsupported or incomplete Jira auth-mode configurations.

### 2. Isolate the trusted Jira integration package

- Implement `moonmind/integrations/jira/` with separate modules for auth resolution, ADF conversion, low-level HTTP transport, strict request models, high-level tool orchestration, and sanitized error types.
- Keep SecretRef resolution and credential material strictly inside the auth/tool boundary.
- Use `httpx` with explicit timeouts and manual bounded retry handling that honors `Retry-After`.

### 3. Expose Jira through the existing MCP router

- Add a dedicated `JiraToolRegistry` parallel to the existing Jules registry.
- Register Jira tools only when Jira tool execution is enabled.
- Extend the `/mcp/tools` discovery and `/mcp/tools/call` dispatch paths to support Jira actions and map sanitized Jira failures to stable HTTP error responses.

### 4. Enforce policy and correctness in the tool layer

- Validate strict request models before dispatch.
- Enforce project/action allowlists and transition validation in the high-level tool service.
- Separate edit behavior from transition behavior and provide metadata helper actions so models do not guess issue types or required fields.

### 5. Cover the security and retry boundaries with tests

- Add unit tests for auth resolution, header/base-URL selection, and settings parsing.
- Add unit tests for HTTP retry logic, `Retry-After`, and sanitized error behavior.
- Add registry and router tests for discovery and dispatch.
- Add tool-service tests for ADF conversion, allowlists, strict validation, and secret-redaction guarantees.

## Runtime-vs-Docs Mode Alignment Gate

- Selected orchestration mode: **runtime**
- Required deliverables include:
  - production runtime code changes in `moonmind/` and `api_service/`
  - configuration exposure in `.env-template` / settings
  - automated validation tests under `tests/`
- Docs-only completion is explicitly out of scope for success.

## Risks & Mitigations

- **Risk**: Jira responses or exception bodies may echo credential-like values.
  - **Mitigation**: Centralize redaction in Jira error/client code and test redaction explicitly with token-containing failures.
- **Risk**: Policy enforcement could drift between the registry and the service.
  - **Mitigation**: Keep tool-policy checks in the high-level service and keep registries focused on schema validation/dispatch only.
- **Risk**: SecretRef-backed bindings may be configured incompletely.
  - **Mitigation**: Fail fast with explicit configuration errors in auth resolution and cover mixed/missing binding cases with tests.
- **Risk**: Existing Atlassian settings used by indexers/planners could be disrupted.
  - **Mitigation**: Add new Jira tool settings without removing legacy Atlassian fields used by existing code paths.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| _None_ | — | — |
