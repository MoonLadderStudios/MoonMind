# Research: Live Logs Phase 7 Hardening and Rollback

## Decision 1: Reuse the existing StatsD emitter for router metrics

- **Decision**: Instrument `task_runs.py` with `get_metrics_emitter()` instead of introducing a new observability helper abstraction.
- **Rationale**: The emitter is already the shared best-effort metrics boundary in MoonMind and the SSE route already depends on it.
- **Alternatives considered**:
  - Add logging-only instrumentation. Rejected because Phase 7 explicitly calls for latency/disconnect operational metrics.
  - Introduce a route-local metrics wrapper. Rejected because it adds indirection without improving the contract.

## Decision 2: Model history-source metrics from the actual reconstruction path

- **Decision**: Teach the history-loading helper to report whether the request used `journal`, `spool`, or `artifacts`, then tag metrics with that source.
- **Rationale**: Phase 7 needs operational visibility into which fallback path is carrying traffic during rollout.
- **Alternatives considered**:
  - Emit latency only. Rejected because it hides which fallback path produced the response.
  - Infer source in tests only. Rejected because operators need runtime telemetry, not just assertions.

## Decision 3: Add a dedicated structured-history rollback flag

- **Decision**: Add `live_logs_structured_history_enabled` under feature flags, defaulting to `true`, and expose it to the dashboard as `liveLogsStructuredHistoryEnabled`.
- **Rationale**: The existing timeline flag controls viewer eligibility, not whether the browser should call `/observability/events`. Phase 7 needs a kill switch for that path.
- **Alternatives considered**:
  - Reuse `liveLogsSessionTimelineEnabled`. Rejected because it would disable the entire session-aware viewer instead of rolling back only the structured-history fetch path.
  - Reuse `logStreamingEnabled`. Rejected because that flag controls live SSE transport, not historical loading.

## Decision 4: Add owner-access regression coverage directly on `/observability/events`

- **Decision**: Mirror the owner-versus-cross-owner tests already used for summary and artifact-session routes on the structured-history endpoint.
- **Rationale**: The endpoint is already protected through shared access helpers, so the highest-value Phase 7 hardening is explicit regression coverage rather than a new auth model.
- **Alternatives considered**:
  - Rely on summary-route coverage. Rejected because `/observability/events` is a distinct route added later in the rollout and deserves direct protection.
