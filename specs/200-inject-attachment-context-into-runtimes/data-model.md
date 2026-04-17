# Data Model: Inject Attachment Context Into Runtimes

## Attachment Injection Entry

Represents one prepared attachment entry selected for a runtime instruction.

Fields:
- `artifactId`: Source artifact reference preserved from the manifest.
- `filename`: Original filename for operator recognition.
- `contentType`: Declared content type.
- `sizeBytes`: Declared byte size.
- `targetKind`: `objective` or `step`.
- `stepRef`: Present for step-scoped entries.
- `stepOrdinal`: Optional step index from the manifest.
- `workspacePath`: Prepared workspace path from `.moonmind/attachments_manifest.json`.
- `visionContextPath`: Optional generated context path matched from `.moonmind/vision/image_context_index.json`.

Validation rules:
- Target kind must be `objective` or `step` before an entry is selected.
- Step entries match the current step only by explicit `stepRef`.
- Unknown optional manifest fields are ignored by prompt rendering.

## Attachment Injection Block

The text section inserted before `WORKSPACE` for text-first runtimes.

Fields:
- Manifest path.
- Safety notice that image-derived text is untrusted reference data.
- Objective entries relevant to all steps.
- Current-step entries relevant only to the executing step.
- Optional generated context paths.

Validation rules:
- Must not include raw bytes or data URLs.
- Must omit full non-current step entries unless a future explicit cross-step access mode is added.
- Must be absent when no prepared attachment manifest exists or no relevant entries exist.

## Planning Attachment Inventory

Compact planning-only summary of attachment targets.

Fields:
- Objective artifact ids and filenames.
- Step target step refs.
- Step target artifact ids and filenames.
- Generated context availability by target.

Validation rules:
- Must not include full non-current step workspace paths.
- Must not include image-derived text content.
- Must not alter source artifact refs, target bindings, or control-plane payload shape.
