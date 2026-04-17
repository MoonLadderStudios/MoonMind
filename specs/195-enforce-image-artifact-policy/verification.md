# Verification: Enforce Image Artifact Storage and Policy

**Date**: 2026-04-17  
**Issue**: MM-368  
**Feature**: `specs/195-enforce-image-artifact-policy`  
**Verdict**: FULLY_IMPLEMENTED

## Original Request Source

Canonical Moon Spec orchestration input: `docs/tmp/jira-orchestration-inputs/MM-368-moonspec-orchestration-input.md`. The original MM-368 Jira preset brief is also preserved directly in `spec.md` under "Original Jira Preset Brief" so verification can compare against the active specification without resolving the external preserved brief file.

The input was classified as a single-story runtime feature request. The Jira brief points at `docs/Tasks/ImageSystem.md`; that document was treated as runtime source requirements, not docs-only work.

## Requirement Coverage

- **DESIGN-REQ-008**: Covered. Uploaded image refs are validated as existing completed artifacts, preserved in `task.inputAttachments` and `task.steps[n].inputAttachments`, linked into execution artifact refs, and included in the original task input snapshot metadata.
- **DESIGN-REQ-009**: Covered. Server policy defaults to `image/png`, `image/jpeg`, and `image/webp`; `image/svg+xml` is always rejected even if configured.
- **DESIGN-REQ-010**: Covered. Execution submission validates canonical ref shape, content type allowlist, max count, per-file size, total size, artifact completion, and stored artifact metadata. Artifact completion validates task input image content type and file signatures for PNG, JPEG, and WebP.
- **DESIGN-REQ-017**: Covered. Worker-side uploads are blocked from reserved input attachment namespaces, browser direct-storage assumptions are avoided, unsupported future attachment fields fail explicitly, and unsupported runtimes continue to fail through target runtime validation.

## Functional Coverage

- **FR-001**: Covered by artifact-backed attachment refs and snapshot metadata without image byte embedding.
- **FR-002**: Covered by normalized objective and step attachment preservation.
- **FR-003**: Covered by default allowed content types.
- **FR-004**: Covered by SVG/scriptable type rejection and allowlist checks.
- **FR-005**: Covered by server-side validation at artifact completion and execution start.
- **FR-006**: Covered by count, per-file size, total size, completion, and metadata consistency checks.
- **FR-007**: Covered by reserved input namespace rejection in the artifact service.
- **FR-008**: Covered by disabled-policy request rejection. Existing Create-page policy behavior already hides disabled attachment entry points, so no frontend change was required.
- **FR-009**: Covered by unsupported attachment field rejection and existing unsupported target runtime rejection.
- **FR-010**: Covered by authoritative task snapshot target binding and observability-only attachment metadata.

## Test Evidence

- Initial focused red run for new tests failed before implementation as expected.
- Focused green run:
  `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/api/routers/test_executions.py::test_create_task_shaped_execution_rejects_attachments_when_policy_disabled tests/unit/api/routers/test_executions.py::test_create_task_shaped_execution_rejects_unknown_attachment_fields tests/unit/api/routers/test_executions.py::test_create_task_shaped_execution_rejects_svg_attachment_type tests/unit/api/routers/test_executions.py::test_create_task_shaped_execution_rejects_attachment_policy_limits tests/unit/workflows/temporal/test_artifacts.py::test_write_complete_rejects_invalid_task_image_signature tests/unit/workflows/temporal/test_artifacts.py::test_create_rejects_reserved_input_attachment_storage_key tests/contract/test_temporal_execution_api.py::test_task_shaped_create_preserves_image_input_attachments -q`
  Result: 7 passed.
- Broader focused run:
  `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_artifacts.py tests/unit/api/routers/test_temporal_artifacts.py tests/contract/test_temporal_execution_api.py -q`
  Result: 111 passed, 13 warnings.
- Final unit suite:
  `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
  Result: Python 3467 passed, 1 xpassed, 101 warnings, 16 subtests passed; frontend Vitest suites 10 passed and 236 tests passed.

## Residual Risk

No blocking residual risk was found. Warnings observed during the final test suite are pre-existing warning-class output and did not indicate failed MM-368 coverage.
