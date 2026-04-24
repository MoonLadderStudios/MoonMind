# MoonSpec Verification Report

**Feature**: Step-First Draft and Attachment Targets
**Jira Issue**: MM-377
**Spec**: `specs/196-step-first-draft-attachment-targets/spec.md`
**Verdict**: FULLY_IMPLEMENTED

## Evidence

- Canonical source input: `spec.md` (Input)
- Runtime implementation: `frontend/src/entrypoints/task-create.tsx`
- Focused tests: `frontend/src/entrypoints/task-create.test.tsx`
- Typecheck: `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json` passed
- Lint: `./node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src/entrypoints/task-create.tsx frontend/src/entrypoints/task-create.test.tsx` passed
- Focused UI tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` passed, 122 tests
- Full unit suite: `./tools/test_unit.sh` passed, 3483 Python tests plus 243 frontend tests

## Requirement Coverage

- **FR-001 / DESIGN-REQ-005 / DESIGN-REQ-024**: VERIFIED. The Create page now tracks objective attachments separately from step attachments while preserving selected/uploaded target state through structured refs.
- **FR-002 / DESIGN-REQ-006 / DESIGN-REQ-007**: VERIFIED. Existing primary-step validation remains covered, including single-step primary instructions or explicit skill and multi-step primary instruction requirements.
- **FR-003 / DESIGN-REQ-008 / SC-002**: VERIFIED. Step-scoped image inputs render inside step cards and submit through owning `task.steps[n].inputAttachments`.
- **FR-004 / DESIGN-REQ-009 / SC-001**: VERIFIED. Objective-scoped images render under `Feature Request / Initial Instructions` and submit through task-level `inputAttachments` only.
- **FR-005 / SC-005**: VERIFIED. Attachment refs are no longer appended to task or step instruction text.
- **FR-006 / SC-003**: VERIFIED. Reordering steps preserves attachment ownership by stable step local identity.
- **FR-007 / FR-008 / DESIGN-REQ-025**: VERIFIED. Selected files remain target-specific, remove controls are available before submit, failed upload paths still block submission, and controls have accessible labels.
- **FR-009**: VERIFIED. MM-377 and the canonical Jira preset brief are preserved in spec artifacts and this verification report.

## Residual Risk

- Artifact-backed edit/rerun attachment reconstruction remains limited to existing behavior; this story only changed new local selected objective and step attachments during Create page submission.
