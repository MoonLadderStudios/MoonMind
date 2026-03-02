# Requirements Traceability: Worker Self-Heal System (Phase 1)

| Source Requirement | Spec Mapping | Runtime Surface | Validation Strategy | Status |
| --- | --- | --- | --- | --- |
| DOC-REQ-001 Detect stuck states (wall/idle/no-progress) | FR-001, FR-002 | `moonmind/agents/codex_worker/self_heal.py`, `moonmind/agents/codex_worker/worker.py` | Worker unit tests for retry/no-progress paths and timeout primitives; full `./tools/test_unit.sh` run | Implemented |
| DOC-REQ-002 Enforce bounded budgets/defaults | FR-001 | `SelfHealConfig.from_env`, `celery_worker/speckit_worker.py` env defaults | `test_self_heal_config_from_env_defaults/overrides` + full unit suite | Implemented |
| DOC-REQ-003 Failure classification buckets | FR-003 | `_classify_step_failure`, `is_failure_retryable` | Worker unit tests for retryable vs deterministic behavior | Implemented |
| DOC-REQ-004 Soft reset retries | FR-004 | `_run_codex_step_with_self_heal` attempt loop | `test_run_once_self_heal_soft_resets_retryable_step_and_recovers` | Implemented |
| DOC-REQ-005 Hard reset + replay | FR-013 (Deferred), DR-001 | `HardResetWorkspaceBuilder` scaffold only | Phase 2 activation gate: add runtime-path worker tests validating hard-reset replay/resume behavior before marking implemented. | Deferred |
| DOC-REQ-006 Minimal retry context envelope | FR-014 (Deferred), DR-004 | Base step instruction remains unchanged between retries | Phase 1 guard tests assert retries reuse the same base instruction; Phase 2+ contract gate adds retry-context artifact/schema assertions and retry-prompt coverage before enabling this requirement. | Deferred (Phase 2+ activation gate) |
| DOC-REQ-007 Persist checkpoints/state artifacts | FR-006 | `state/steps/*.json`, `state/self_heal/*.json`, storage helpers | Worker tests + artifact storage unit tests | Implemented |
| DOC-REQ-008 Attempt loop respects cancel/pause/takeover and queue escalation | FR-005, FR-010 | Worker attempt loop + pause/cancel gating + queue retry reason | Worker unit tests for retryable exhaustion and control compatibility behaviors | Implemented |
| DOC-REQ-009 Recovery API actions (`retry_step`, `hard_reset_step`, `resume_from_step`) | FR-015 (Deferred), FR-016 (Deferred), DR-002, DR-003 | Not present in task-run control API/service; allowlist remains `pause`, `resume`, `takeover` | Phase 1 guard tests reject deferred actions in router/service layers; Phase 3 activation gate adds API contract + router/service/dashboard integration tests for each recovery action before enabling, including recovery lifecycle events. | Deferred (Phase 3) |
| DOC-REQ-010 Emit recovery events/artifacts metadata | FR-007 | `task.step.attempt.*`, `task.self_heal.*`, attempt/step artifacts | Worker unit tests inspect events/artifacts; full unit suite | Implemented (Phase 1 event set) |
| DOC-REQ-011 StatsD metrics | FR-008 | `moonmind/agents/codex_worker/metrics.py` | `tests/unit/agents/codex_worker/test_metrics.py` | Implemented |
| DOC-REQ-012 Secret scrubbing | FR-009 | `build_failure_signature`, worker payload redaction | `test_build_failure_signature_scrubs_secrets` + worker failure redaction tests | Implemented |
| DOC-REQ-013 Runtime deliverables include production code + validation tests | FR-011, FR-012 | Worker runtime execution path and validation suite | Runtime diff inspection + `./tools/test_unit.sh` | Implemented |
