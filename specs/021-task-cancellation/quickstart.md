# Quickstart: Validate Agent Queue Task Cancellation

## 1. Run unit tests

```bash
./tools/test_unit.sh
```

## 2. Focused verification targets

Validate these focused suites if you need faster iteration:

```bash
./tools/test_unit.sh tests/unit/workflows/agent_queue/test_repositories.py
./tools/test_unit.sh tests/unit/workflows/agent_queue/test_service_hardening.py
./tools/test_unit.sh tests/unit/api/routers/test_agent_queue.py
./tools/test_unit.sh tests/unit/mcp/test_tool_registry.py
./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py
./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py
```

## 3. Manual API smoke examples

### Queue job cancellation request

```bash
curl -X POST "http://localhost:5000/api/queue/jobs/<job_id>/cancel" \
  -H "Content-Type: application/json" \
  -d '{"reason":"operator requested cancel"}'
```

### Worker cancellation acknowledgement

```bash
curl -X POST "http://localhost:5000/api/queue/jobs/<job_id>/cancel/ack" \
  -H "Content-Type: application/json" \
  -H "X-MoonMind-Worker-Token: <worker-token>" \
  -d '{"workerId":"worker-1","message":"stopping due to cancellation"}'
```

## 4. Scope gate checks

```bash
.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

## 5. Latest validation results (2026-02-17)

- Cancellation regression suite: `457 passed` using:
  - `./tools/test_unit.sh tests/unit/workflows/agent_queue/test_repositories.py tests/unit/workflows/agent_queue/test_service_hardening.py tests/unit/api/routers/test_agent_queue.py tests/unit/api/routers/test_mcp_tools.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/mcp/test_tool_registry.py tests/unit/agents/codex_worker/test_worker.py tests/unit/agents/codex_worker/test_handlers.py`
- Scope gates:
  - `Scope validation passed: tasks check (runtime tasks=14, validation tasks=10).`
  - `Scope validation passed: diff check (runtime files=10, test files=8).`
