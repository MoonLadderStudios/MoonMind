# Research: Step-First Draft and Attachment Targets

## Runtime Intent

Decision: Treat MM-377 as runtime implementation work against the existing Create page.

Rationale: The Jira brief points to `docs/UI/CreatePage.md` as source requirements and asks for observable authoring/submission behavior, not documentation edits.

Alternatives considered: Docs-only alignment was rejected because the selected mode is runtime and the acceptance criteria describe browser behavior and submitted payloads.

## Attachment Target State

Decision: Track objective-scoped files separately from step-scoped files, and keep step files keyed by stable `step.localId`.

Rationale: Stable local IDs already survive reorder operations, so attachments can move with the owning step without relying on rendered order or filename matching. A separate objective file list prevents task-level images from being treated as primary-step images.

Alternatives considered: Reusing primary-step attachments for task-level `inputAttachments` was rejected because it copies target meaning and causes objective images and step images to collapse into the same target.

## Instruction Text Policy

Decision: Submit attachment refs through structured `inputAttachments` only and stop appending generated attachment markdown to task or step instructions.

Rationale: The source design explicitly says image inputs are structured task inputs and not part of instruction text. Keeping refs structured avoids untrusted attachment metadata appearing as instructions and makes target ownership explicit.

Alternatives considered: Keeping the generated "Step input attachments" instruction block was rejected because it violates MM-377 FR-005 and duplicates structured refs.

## Test Strategy

Decision: Use focused Vitest coverage for Create page draft/submission behavior and route final validation through `./tools/test_unit.sh`.

Rationale: The acceptance boundary is the browser draft and execution create payload. Existing tests already mock artifact creation and `/api/executions`, which exercises the contract without Docker or external services.

Alternatives considered: Docker-backed integration was rejected for this story because no backend service behavior or external provider boundary changes are required.
