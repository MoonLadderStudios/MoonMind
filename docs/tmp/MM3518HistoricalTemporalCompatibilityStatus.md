# MM#3518 — Roadmap 6.3 execution status and test-evidence ledger

**Class:** run-local execution scaffolding (status checklist + test-path bookkeeping).
Per `AGENTS.md` (**Canonical docs are durable and declarative**), this milestone
execution status and its test-node bookkeeping live here rather than inside the
declarative roadmap (`docs/MoonMindRoadmap.md`) or the canonical evidence
contract. The durable evidence contract for these surfaces is
`docs/Omnigent/CodexSupportAndCutover.md`; delete this handoff once #3518 is
fully closed out.

## Roadmap 6.3 — Historical and Temporal compatibility

Status: complete for the compatibility gate (retirement stages 6.2/6.4 remain
gated on live evidence).

### Evidence ledger

- Persisted `codex_direct_compat` sessions keep truthful provenance and render
  through the shared Workflow Detail journal read model with no active direct
  runtime:
  - `tests/unit/omnigent/test_direct_compat_historical_reads.py::test_persisted_direct_compat_session_reads_without_live_runtime`
  - `tests/unit/omnigent/test_direct_compat_historical_reads.py::test_direct_compat_read_model_matches_omnigent_transport_shape`
  - `tests/unit/omnigent/test_direct_compat_historical_reads.py::test_direct_compat_reads_through_workflow_detail_http_routes`
    (HTTP boundary: `/bridge-sessions/resolve` + `/bridge-sessions/{id}/events`,
    ownership authorization and response-model serialization included).
- Pre-/post-cutover recorded histories replay on one current worker while
  runtime selection stays a submission-boundary side effect:
  - `tests/unit/workflows/temporal/test_run_replayer.py::test_github_3518_cutover_selection_never_runs_inside_workflow_code`
  - `tests/unit/workflows/temporal/test_run_replayer.py::test_github_3518_cutover_runtime_parameter_histories_replay`
  - `tests/unit/workflows/temporal/test_run_replayer.py::test_github_3518_pre_cutover_runtime_history_replays_on_current_worker`
    (faithful pre-cutover fixture generates the legacy history, replayed on the
    current worker).
