# Tasks: Integration Test Improvements — Phase 5 (Repo Conventions & Specs)

**Input**: Design documents from `/specs/133-integration-test-improvements-phase5/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: No new test logic changes. Marker additions only. Validation via running existing test scripts.

**Organization**: Tasks are grouped by work area rather than user stories, since this feature has no end-user stories — it is a documentation and convention update.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions

---

## Phase 1: PowerShell Provider Verification Script

**Goal**: Create `tools/test-provider.ps1` as the PowerShell counterpart to `tools/test_jules_provider.sh`.

**Independent Test**: Running `.\tools\test-provider.ps1` without `JULES_API_KEY` set should fail fast with a clear error message.

- [x] T001 Create `tools/test-provider.ps1` mirroring `tools/test_jules_provider.sh` behavior
  - Check `JULES_API_KEY` env var, fail fast if missing (`Write-Error` + `exit 1`)
  - Detect Docker Compose availability (`docker compose` or `docker-compose`)
  - Ensure `.env` exists (copy from `.env-template` if missing)
  - Ensure Docker network exists (create `$env:MOONMIND_DOCKER_NETWORK` or `local-network`)
  - Build pytest compose service
  - Run: `pytest tests/provider/jules -m 'provider_verification and jules' -q --tb=short -s`
  - Bring down compose services after run

---

## Phase 2: AGENTS.md Testing Instructions Update

**Goal**: Update the "Testing Instructions" section in AGENTS.md to explicitly document the hermetic integration vs. provider verification taxonomy.

**Independent Test**: Reading AGENTS.md should clearly distinguish the two test categories and reference the correct scripts.

- [x] T002 Update AGENTS.md "Testing Instructions" section
  - Add definition of **hermetic integration tests**: compose-backed, no external credentials, marked with `integration_ci`, run via `./tools/test_integration.sh` or `tools/test-integration.ps1`
  - Add definition of **provider verification tests**: real third-party providers, require credentials, marked with `provider_verification`, run via `./tools/test_jules_provider.sh` or `tools/test-provider.ps1`
  - State that Jules unit tests remain in the required unit suite
  - Reference both script families clearly

---

## Phase 3: Marker Coverage for Hermetic Integration Tests

**Goal**: Add `@pytest.mark.integration_ci` to all hermetic integration test files that lack it.

**Independent Test**: `./tools/test_integration.sh` should run all newly marked files successfully.

### Temporal integration tests (all CI-safe)

- [x] T003 [P] Add `@pytest.mark.integration_ci` to `tests/integration/temporal/test_compose_foundation.py`
- [x] T004 [P] Add `@pytest.mark.integration_ci` to `tests/integration/temporal/test_execution_rescheduling.py`
- [x] T005 [P] Add `@pytest.mark.integration_ci` to `tests/integration/temporal/test_integrations_monitoring.py`
- [x] T006 [P] Add `@pytest.mark.integration_ci` to `tests/integration/temporal/test_interventions_temporal.py`
- [x] T007 [P] Add `@pytest.mark.integration_ci` to `tests/integration/temporal/test_live_logs_performance.py`
- [x] T008 [P] Add `@pytest.mark.integration_ci` to `tests/integration/temporal/test_managed_runtime_live_logs.py`
- [x] T009 [P] Add `@pytest.mark.integration_ci` to `tests/integration/temporal/test_manifest_ingest_runtime.py`
- [x] T010 [P] Add `@pytest.mark.integration_ci` to `tests/integration/temporal/test_namespace_retention.py`
- [x] T011 [P] Add `@pytest.mark.integration_ci` to `tests/integration/temporal/test_oauth_session.py`
- [x] T012 [P] Add `@pytest.mark.integration_ci` to `tests/integration/temporal/test_upgrade_rehearsal.py`

### Services and workflow integration tests (all CI-safe)

- [x] T013 [P] Add `@pytest.mark.integration_ci` to `tests/integration/services/temporal/workflows/test_agent_run.py`
- [x] T014 [P] Add `@pytest.mark.integration_ci` to `tests/integration/workflows/temporal/test_schedule_timezone_handling.py`
- [x] T015 [P] Add `@pytest.mark.integration_ci` to `tests/integration/workflows/temporal/test_task_5_14.py`
- [x] T016 [P] Add `@pytest.mark.integration_ci` to `tests/integration/workflows/temporal/workflows/test_run_agent_dispatch.py`
- [x] T017 [P] Add `@pytest.mark.integration_ci` to `tests/integration/workflows/temporal/workflows/test_run.py`

### Top-level integration tests (CI-safe — hermetic/mocked)

- [x] T018 [P] Add `@pytest.mark.integration_ci` to `tests/integration/test_interventions.py`
- [x] T019 [P] Add `@pytest.mark.integration_ci` to `tests/integration/test_profile_chat_flow.py`
- [x] T020 [P] Add `@pytest.mark.integration_ci` to `tests/integration/test_profile_creation_on_register.py`
- [x] T021 [P] Add `@pytest.mark.integration_ci` to `tests/integration/test_projection_sync.py`
- [x] T022 [P] Add `@pytest.mark.integration_ci` to `tests/integration/test_startup_profile_seeding.py`
- [x] T023 [P] Add `@pytest.mark.integration_ci` to `tests/integration/test_startup_secret_env_seeding.py`
- [x] T024 [P] Add `@pytest.mark.integration_ci` to `tests/integration/test_startup_task_template_seeding.py`
- [x] T025 [P] Add `@pytest.mark.integration_ci` to `tests/integration/api/test_live_logs.py`

### Explicitly excluded from `integration_ci` (require external credentials)

These files must NOT receive the `integration_ci` marker:
- `tests/integration/test_gemini_embeddings.py` (needs Gemini API key)
- `tests/integration/test_ollama_embeddings.py` (needs external Ollama service)
- `tests/integration/test_openai_embeddings.py` (needs OpenAI API key)
- `tests/integration/test_multi_profile_openrouter.py` (needs OpenRouter key)

No tasks needed for these — they remain unmarked by design.

---

## Phase 4: Spec/Doc Reference Updates

**Goal**: Update docs that still describe Jules as "required compose-backed integration."

**Independent Test**: Reading the updated docs should clarify Jules is an optional external provider.

- [x] T026 Update `docs/Temporal/ActivityCatalogAndWorkerTopology.md`
  - Already correctly frames Jules as an optional provider family in the activity catalog — no changes needed
- [x] T027 Update `docs/Temporal/IntegrationsMonitoringDesign.md`
  - Already states "provider-neutral" design with Jules as default example — no changes needed

---

## Phase 5: Validation

**Goal**: Verify all changes work correctly.

- [x] T028 Verify `./tools/test_integration.sh` runs successfully (hermetic tests only)
- [x] T029 Verify `./tools/test_jules_provider.sh` fails fast without `JULES_API_KEY`
- [x] T030 Verify `tools/test-provider.ps1` syntax is valid (pwsh not installed on this host — deferred to CI/Windows developer verification)
- [x] T031 Run `./tools/test_unit.sh` to ensure no regressions from marker additions (2557 passed, 87 frontend passed)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (PowerShell script)**: No dependencies — can start immediately
- **Phase 2 (AGENTS.md)**: No dependencies — can start immediately
- **Phase 3 (Marker coverage)**: No dependencies — all tasks are independent file edits
- **Phase 4 (Doc updates)**: No dependencies — can start immediately
- **Phase 5 (Validation)**: Depends on Phases 1–4 completion

### Parallel Opportunities

- All Phase 3 tasks (T003–T025) are independent `[P]` tasks on different files — all can run in parallel
- Phase 1, Phase 2, and Phase 4 tasks can all run in parallel with each other
- Only Phase 5 requires all prior phases to complete

### Within Each Phase

- Phases 1–4 are independent of each other
- Phase 5 runs last as the validation gate

---

## Implementation Strategy

### MVP First

1. Complete Phase 3 (marker additions) — this is the core functional change
2. Complete Phase 1 (PowerShell script) — parity with existing bash script
3. Validate with `./tools/test_integration.sh`
4. Then complete Phases 2, 4 (documentation)
5. Final validation (Phase 5)

### Parallel Team Strategy

With multiple developers:
- Developer A: Phase 3 (marker additions — all 23 files)
- Developer B: Phase 1 (PowerShell script) + Phase 2 (AGENTS.md)
- Developer C: Phase 4 (doc updates)
- All converge on Phase 5 (validation)

---

## Notes

- [P] tasks = different files, no dependencies
- Each marker addition is a one-line `@pytest.mark.integration_ci` decorator at the top of the test file
- The PowerShell script (T001) is the most complex single task — mirror the bash script faithfully
- No test logic, assertions, or CI workflow YAMLs change
- Files explicitly excluded from `integration_ci` (embeddings, OpenRouter) must NOT be touched
