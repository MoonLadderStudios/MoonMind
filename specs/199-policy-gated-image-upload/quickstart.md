# Quickstart: Policy-Gated Image Upload and Submit

## Focused UI Validation

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

Expected result:
- Attachment entry points are hidden when policy is disabled.
- Image-only policy shows image-specific attachment labels.
- Count, per-file size, total size, and content type validation errors block submit.
- Upload failure and preview failure remain scoped to the affected target.
- Create, edit, and rerun flows upload local images before sending execution payloads.
- Submitted payloads use `task.inputAttachments` and `task.steps[n].inputAttachments`.

## Final Unit Validation

```bash
./tools/test_unit.sh
```

Expected result:
- The full unit suite passes, including frontend tests prepared by the repo test runner.

## Manual Story Check

1. Load the Create page with attachment policy disabled.
2. Confirm attachment controls are hidden and a text-only task can still be authored.
3. Load the Create page with image-only attachment policy enabled.
4. Add objective and step image selections.
5. Trigger count, per-file size, total size, and content type validation.
6. Trigger or simulate upload and preview failures.
7. Remove or retry failed attachments and confirm unrelated draft fields remain intact.
8. Submit create, edit, and rerun drafts with valid local images.
9. Confirm local images upload before the execution payload is sent.
10. Confirm the payload contains objective refs under `task.inputAttachments` and step refs under the owning `task.steps[n].inputAttachments`.

## MoonSpec Verification Focus

Final `/moonspec-verify` should confirm:
- MM-380 appears in the canonical input and feature artifacts.
- DESIGN-REQ-016, DESIGN-REQ-021, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-025, and DESIGN-REQ-006 are covered.
- Unit and integration-style UI tests cover the story's success criteria.
