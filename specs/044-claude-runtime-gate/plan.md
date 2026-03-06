# Implementation Plan: Claude Runtime API-Key Gating

**Branch**: `038-claude-runtime-gate` | **Date**: February 24, 2026 | **Spec**: `specs/038-claude-runtime-gate/spec.md`
**Input**: Feature specification from `/specs/038-claude-runtime-gate/spec.md`

## Summary

MoonMind currently exposes Claude runtime toggles even when no Anthropic API key is configured, which causes workers to boot without required credentials, lets API clients enqueue unclaimable tasks, and surfaces unusable options in the dashboard. This plan delivers PR 1 of the Claude API-key migration by replacing every remaining Claude auth dependency with a single “Claude enabled” rule: Claude runtime is allowed only when either `ANTHROPIC_API_KEY` or its legacy alias `CLAUDE_API_KEY` is non-empty. The change set touches the worker preflight (CLI verification + capability-driven key requirement), queue validation (including startup checks for a `claude` default), and dashboard runtime config builder (server-side gating for runtime dropdowns). Each surface gains fast-fail errors plus unit tests that exercise both enabled and disabled paths.

## Technical Context

**Language/Version**: Python 3.11 services + FastAPI API layer, Celery workers, pytest-based tests orchestrated via `./tools/test_unit.sh`.  
**Primary Dependencies**: FastAPI for HTTP routing, Celery workers, Pydantic + pydantic-settings for config, Anthropic Claude CLI, GitHub CLI, Codex CLI, Gemini CLI; runtime gate helpers in `moonmind.claude.runtime`.  
**Storage**: PostgreSQL (queue metadata + API persistence) and RabbitMQ (Celery broker); this feature only reads configuration, so no schema changes are required.  
**Testing**: `./tools/test_unit.sh` (pytest wrapper) with existing suites under `tests/unit/**`; unit coverage must be expanded for preflight, queue router/service, Mission Control, and settings validation.  
**Target Platform**: Dockerized Linux services (api, celery workers, dashboard) plus optional Claude worker container invoked via docker-compose profiles.  
**Project Type**: Backend services and CLI workers inside a monorepo (`api_service`, `moonmind`, `tests`).  
**Performance Goals**: Preflight failure should surface in <2s, queue validation remains O(1) string checks, dashboard config building stays synchronous (<20ms). No throughput regressions expected.  
**Constraints**: All checks must rely on the shared `resolve_anthropic_api_key` helper to avoid divergent logic; runtime capability parsing must stay backward compatible with comma-delimited env vars; HTTP responses must keep existing JSON schema while updating error codes/messages deterministically.  
**Scale/Scope**: Applies to every deployment that enables Claude workers or allows `targetRuntime=claude`; must remain safe when thousands of tasks hit the queue concurrently because validation is synchronous and stateless.

## Constitution Check

The project constitution placeholders do not enumerate enforceable principles, so there are no blocking gates. Proceeding under the implicit standards already satisfied by the spec: automated tests are required, docs must remain accurate, and no new unbounded complexity can be introduced. Constitution check: **PASS (no stated requirements in constitution template)**.

## Project Structure

### Documentation (this feature)

```text
specs/038-claude-runtime-gate/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/
    ├── agent_queue.md
    └── task_dashboard_config.md
```

### Source Code (repository root)

```text
api_service/
└── api/routers/
    ├── agent_queue.py                # Maps queue validation errors to HTTP responses
    └── task_dashboard_view_model.py  # Builds runtime dropdown config

moonmind/
├── agents/codex_worker/cli.py        # Worker preflight + CLI verification
├── claude/runtime.py                 # Shared Anthropic key helpers
├── config/settings.py                # Pydantic settings + startup validation
└── workflows/agent_queue/service.py  # Queue normalization + payload enforcement

tests/
├── unit/agents/codex_worker/test_cli.py
├── unit/api/routers/test_agent_queue.py
├── unit/api/routers/test_task_dashboard_view_model.py
├── unit/config/test_settings.py
└── unit/workflows/agent_queue/test_service_hardening.py
```

**Structure Decision**: Single-repo backend/worker layout. API routers expose HTTP entrypoints, workers read from the same config module, and tests mirror runtime packages. No new packages or directories are required; only targeted edits within the listed modules plus new spec artifacts.

## Complexity Tracking

No constitution violations anticipated; additional abstractions (e.g., `moonmind.claude.runtime`) already exist and will be reused.

## Implementation Strategy

### 1. Shared Claude runtime gate

1. Extend `moonmind.claude.runtime` if necessary so both `resolve_anthropic_api_key` and `is_claude_runtime_enabled` accept either explicit `api_key` arguments or env fallbacks, trimming whitespace. This helper becomes the single source of truth for every other section in this plan.  
2. Confirm that `resolve_anthropic_api_key(env=source)` is used by worker preflight, queue service, dashboard config builder, and settings validation; update any ad-hoc environment reads to call this helper instead of duplicating alias logic.  
3. Provide tiny unit coverage (or keep existing tests) to protect alias handling so future PRs don’t regress support for `CLAUDE_API_KEY`.

### 2. Worker preflight CLI enforcement (`moonmind/agents/codex_worker/cli.py`)

1. Remove `_run_claude_auth_status_check` and the legacy auth-status hook so no OAuth-related subprocesses are invoked.  
2. In `run_preflight`, determine `claude_required = runtime == "claude" or "claude" in _worker_capabilities(...)`. When true, call `resolve_anthropic_api_key(env=source)` and raise `RuntimeError("ANTHROPIC_API_KEY must be configured when Claude runtime is enabled")` if empty (keep message consistent with spec/tests).  
3. Only verify the Claude CLI (`verify_cli_is_executable` + `claude --version`) when `claude_required` is true; skip the command entirely otherwise.  
4. Maintain existing Codex, Gemini, Speckit, and GitHub validation order to avoid regressions.  
5. Tests (`tests/unit/agents/codex_worker/test_cli.py`): 
   - `runtime=claude` without key -> raises `RuntimeError` containing missing-key message.  
   - `runtime=claude` with key -> verifies `claude --version` and skips legacy auth-status checks.  
   - `runtime=universal` without `claude` capability -> no Claude verification is invoked.  
   - `runtime=universal` with `claude` capability -> requires key and runs `claude --version`.  
   These tests already exist but will be updated to align with the new gating behavior and ensure no legacy auth-status commands execute.

### 3. Queue validation + default runtime settings (`moonmind/workflows/agent_queue/service.py`, `moonmind/config/settings.py`)

1. In `_enrich_task_payload_defaults` / `normalize_task_job_payload`, after runtime resolution but before `normalize_queue_job_payload`, call `is_claude_runtime_enabled` using the Anthropic settings value. If disabled and runtime is `claude`, raise `AgentQueueValidationError("targetRuntime=claude requires ANTHROPIC_API_KEY to be configured")`.  
2. Keep error propagation so `api_service/api/routers/agent_queue.py` inspects the exception text and maps it to HTTP 400 (`code=claude_runtime_disabled`).  
3. Update `moonmind/config/settings.py` `model_post_init` to raise a `ValueError` during startup whenever `workflow.default_task_runtime == "claude"` but no key exists (this already occurs; ensure message matches queue validation).  
4. Unit tests: 
   - `tests/unit/workflows/agent_queue/test_service_hardening.py` should cover both acceptance and rejection paths for `targetRuntime=claude`.  
   - `tests/unit/api/routers/test_agent_queue.py` ensures HTTP 400 mapping remains intact.  
   - `tests/unit/config/test_settings.py` asserts that a `claude` default without a key raises ValueError and that providing a key allows boot.

### 4. Dashboard runtime config gating (`api_service/api/routers/task_dashboard_view_model.py`)

1. Replace the static `_SUPPORTED_TASK_RUNTIMES` tuple with a dynamic builder that always exposes `["codex", "gemini"]` and appends `claude` only when `is_claude_runtime_enabled(...)` is true.  
2. In `build_runtime_config`, ensure `defaultTaskRuntime` never returns `claude` when it is not in the supported list by falling back to the first available runtime (codex -> gemini). Honor `MOONMIND_WORKER_RUNTIME` and `settings.workflow.default_task_runtime` only when those values appear in `supported_task_runtimes`.  
3. Tests (`tests/unit/api/routers/test_task_dashboard_view_model.py`) should cover: 
   - Default environment (no key) -> `supportedTaskRuntimes == ["codex", "gemini"]` and fallback default is `codex`.  
   - Inject Anthropic key -> list becomes `["codex", "gemini", "claude"]`.  
   - Environment default `MOONMIND_WORKER_RUNTIME=claude` with no key -> fallback to codex while still hiding claude.

### 5. Error-flow + helper coverage

1. Ensure `moonmind/workflows/agent_queue/service.py` attaches runtime defaults (model/effort) only to codex while leaving claude unaffected.  
2. Confirm `moonmind.workflows.agent_queue.task_contract.normalize_queue_job_payload` continues to receive normalized runtime fields (no schema changes).  
3. Add targeted tests for `moonmind/workflows/orchestrator/test_skill_executor.py` if it depends on runtime gating (currently only ensures job-submission path respects queue validation).

### 6. Validation + Tooling

1. Run `./tools/test_unit.sh` locally; this is the canonical CI path and will exercise the updated suites automatically.  
2. Smoke-test dashboard config by hitting `/api/task-dashboard/config` (or whichever endpoint feeds the view-model) with and without `ANTHROPIC_API_KEY` exported to ensure JSON output matches expectations.  
3. Verify worker preflight manually by running `python -m moonmind.agents.codex_worker.cli --runtime claude` (or via `make` target) once without a key (expect failure) and once with `ANTHROPIC_API_KEY=dummy` (expect success through `claude --version` invocation only).

### 7. Risks & Mitigations

- **Edge: Legacy `CLAUDE_API_KEY` only** – mitigate by keeping alias support in `moonmind.claude.runtime` tests.  
- **Edge: Universal workers auto-claim Claude tasks** – ensure capabilities parsing is case-insensitive and deduplicated; add a test verifying `MOONMIND_WORKER_CAPABILITIES="CODEX,CLAUDE"` still triggers gating.  
- **Regression: Dashboard default runtime mismatch** – tests now validate fallback logic so unsupported defaults never leak to clients.  
- **Operational clarity** – expose consistent error messages (“targetRuntime=claude requires ANTHROPIC_API_KEY to be configured”) across worker preflight and queue validation for easier troubleshooting.

With these steps complete, Claude runtime will be fully gated on API-key availability without any residual OAuth tooling, satisfying PR 1 of the larger Claude tooling-removal effort.
