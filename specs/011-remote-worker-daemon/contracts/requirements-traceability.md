# Requirements Traceability Matrix: Remote Worker Daemon (015-Aligned)

**Feature**: `011-remote-worker-daemon`  
**Umbrella Source**: `specs/015-skills-workflow/spec.md`

| Requirement ID | Mapped FR(s) | Implementation Surface | Validation Strategy |
|----------------|--------------|------------------------|--------------------|
| `UMB-011-001` Startup enforces Speckit + Codex readiness | `FR-001`, `FR-002` | `moonmind/agents/codex_worker/cli.py` preflight checks | `tests/unit/agents/codex_worker/test_cli.py` |
| `UMB-011-002` Google embedding profile requires key material | `FR-003` | `moonmind/agents/codex_worker/cli.py::_validate_embedding_profile` | `tests/unit/agents/codex_worker/test_cli.py::test_run_preflight_google_embedding_requires_credential` |
| `UMB-011-003` Worker handles `codex_exec` + `codex_skill` claims | `FR-004`, `FR-005`, `FR-006` | `moonmind/agents/codex_worker/worker.py`, `moonmind/agents/codex_worker/handlers.py` | `tests/unit/agents/codex_worker/test_worker.py`, `tests/unit/agents/codex_worker/test_handlers.py` |
| `UMB-011-004` Skill allowlist policy is enforced locally | `FR-006` | `CodexWorkerConfig.from_env` + `CodexWorker.run_once` skill gate | `tests/unit/agents/codex_worker/test_worker.py::test_run_once_codex_skill_disallowed_skill_fails` |
| `UMB-011-005` Event payloads include execution metadata | `FR-007` | `CodexWorker._execution_metadata` + `_emit_event` payloads | `tests/unit/agents/codex_worker/test_worker.py::test_run_once_codex_skill_routes_through_skill_path` |
| `UMB-011-006` Heartbeat and terminal transitions remain robust | `FR-008` | `CodexWorker._heartbeat_loop`, `run_once` completion/failure flow | `tests/unit/agents/codex_worker/test_worker.py` heartbeat + success/failure tests |
| `UMB-011-007` Runtime changes are validated in unit gate | `FR-009` | repository-wide unit command | `./tools/test_unit.sh` |
| `UMB-011-008` Task-level codex model/effort overrides resolve with worker-default fallback | `FR-010` | `CodexExecPayload`/`CodexSkillPayload` parsing + `CodexExecHandler._build_codex_exec_command` + `CodexWorkerConfig.from_env` | `tests/unit/agents/codex_worker/test_handlers.py`, `tests/unit/agents/codex_worker/test_worker.py::test_config_from_env_uses_codex_fallback_env_vars` |
