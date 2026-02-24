# Phase 0 Research — Claude Runtime API-Key Gating

## Decision: Canonical Claude enablement helper
- **Decision**: Use `moonmind.claude.runtime.resolve_anthropic_api_key` + `is_claude_runtime_enabled` as the single gate for every runtime surface (worker preflight, queue normalization, dashboard config, settings validation).
- **Rationale**: The helper already normalizes whitespace, honors `ANTHROPIC_API_KEY` and legacy `CLAUDE_API_KEY`, and can accept an explicit `api_key` for settings-driven flows. Centralizing prevents drift between workers and API services.
- **Alternatives Considered**: Reading environment variables inline inside each consumer was rejected because it duplicates alias logic and makes it easy to miss `CLAUDE_API_KEY` support.

## Decision: Worker preflight capability detection
- **Decision**: Treat Claude as “required” when either `MOONMIND_WORKER_RUNTIME == "claude"` or `MOONMIND_WORKER_CAPABILITIES` (comma-delimited, case-insensitive) contains `claude`; do not rely on OAuth commands.
- **Rationale**: Specialized Claude workers and universal workers that can claim Claude jobs both need the key. Capabilities already influence claim filters, so reusing them keeps semantics aligned and avoids new env vars.
- **Alternatives Considered**: Adding a dedicated `MOONMIND_WORKER_ENABLE_CLAUDE` flag was rejected to avoid another configuration axis and because it could drift from capabilities.

## Decision: Queue validation location
- **Decision**: Enforce runtime gating inside `AgentQueueService.normalize_task_job_payload` before delegating to `normalize_queue_job_payload`, and let the existing `AgentQueueValidationError` bubble up to the FastAPI router.
- **Rationale**: This function already finalizes `targetRuntime` defaults, so it has the canonical value. Throwing there ensures the same logic applies whether requests arrive via HTTP, CLI submissions, or orchestrator jobs.
- **Alternatives Considered**: Adding validation inside the FastAPI router alone was rejected because other code paths (e.g., orchestrator-generated jobs) bypass HTTP and would still enqueue invalid payloads.

## Decision: Settings + dashboard gating
- **Decision**: Validate `settings.spec_workflow.default_task_runtime` during `AppSettings.model_post_init` and compute dashboard `supportedTaskRuntimes` via a helper that appends `claude` only when the gate passes.
- **Rationale**: Startup validation prevents operators from silently defaulting to a broken runtime, and the dashboard already reads from settings + env, so we can reuse the same helper for both supported list and default fallback.
- **Alternatives Considered**: Hard-coding `("codex", "gemini", "claude")` and relying on the UI to hide unsupported entries was rejected because it would duplicate logic and still let unsupported runtimes leak into the JSON payload.
