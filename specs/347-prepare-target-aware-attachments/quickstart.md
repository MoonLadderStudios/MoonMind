# Quickstart: Prepare-Time Target-Aware Attachment Materialization

1. Confirm `specs/347-prepare-target-aware-attachments/spec.md` preserves `MM-648` and the canonical Jira preset brief.
2. Run focused prepared-context unit tests:
   `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py`
3. Run focused worker materialization tests:
   `./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py`
4. Run existing target-aware workflow integration coverage when Docker/compose is available:
   `./tools/test_integration.sh`
5. Inspect generated or fixture manifests and verify each attachment entry preserves `targetKind`, stable `stepRef` for step targets, `workspacePath`, and `status`, with no binary bytes.
