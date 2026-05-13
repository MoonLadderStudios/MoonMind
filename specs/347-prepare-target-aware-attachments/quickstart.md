# Quickstart: Prepare-Time Target-Aware Attachment Materialization

1. Confirm `specs/347-prepare-target-aware-attachments/spec.md` preserves `MM-648` and the canonical Jira preset brief.
2. Run focused prepared-context unit tests:
   `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py`
3. Run focused worker materialization tests:
   `./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py`
4. Run target-aware workflow integration coverage when Docker/compose is available:
   `./tools/test_integration.sh`
5. If Docker is unavailable, run the focused local workflow boundary fallback and document the Docker blocker:
   `pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -q --tb=short`
6. Add or run integration-level coverage proving missing or invalid attachment preparation fails with the affected target before final `/moonspec-verify` closes SC-003.
7. Inspect generated or fixture manifests and verify each attachment entry preserves `targetKind`, stable `stepRef` for step targets, `workspacePath`, and `status`, with no binary bytes.
