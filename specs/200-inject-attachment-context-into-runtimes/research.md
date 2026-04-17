# Research: Inject Attachment Context Into Runtimes

## Input Classification

Decision: Treat MM-372 as a single-story runtime feature request.

Rationale: The Jira brief selects one independently testable behavior: inject target-scoped prepared attachment context into runtime prompts and adapter-visible metadata. The referenced source document sections are runtime requirements, not documentation-only work.

Alternatives considered: Treating `docs/Tasks/ImageSystem.md` as a broad design was rejected because the Jira brief already selects sections 10 and 15 and excludes other stories such as submission, materialization, and vision generation.

## Instruction Injection Surface

Decision: Render the `INPUT ATTACHMENTS` block in the existing worker step-instruction composition path, before the `WORKSPACE` section.

Rationale: Text-first runtimes consume composed instruction text. The worker already has a single helper for runtime step instructions, and prompt ordering can be validated without changing provider-specific adapters.

Alternatives considered: Adding provider-specific message payloads was rejected because MM-372 explicitly keeps provider multimodal formats outside the control-plane contract.

## Prepared Context Source

Decision: Use `.moonmind/attachments_manifest.json` as the source of manifest entries and `.moonmind/vision/image_context_index.json` as the optional source of generated context paths.

Rationale: Adjacent MM-370 and MM-371 work already define and implement these prepared artifacts. MM-372 consumes them and should not infer target binding from filenames or artifact metadata.

Alternatives considered: Recomputing attachment paths from the execution payload was rejected because the prepared manifest is the desired source of truth for runtime-visible workspace paths.

## Step Scoping

Decision: Step execution includes objective entries and entries whose `stepRef` matches the executing step. Other step targets are omitted from full detail by default.

Rationale: The source design requires objective plus current-step context, and forbids implicit cross-step sharing. The existing resolved step includes `step_id`, which aligns with manifest `stepRef` generated during materialization.

Alternatives considered: Including all entries with a "do not inspect yet" warning was rejected because it still flattens non-current context into the active step.

## Planning Inventory

Decision: Add a compact renderer that can summarize step-scoped attachments by target without full workspace/context detail for future planning prompts.

Rationale: The source design requires task-level planning to understand later-step inputs without flattening them into current step context. Even if not all planners call it immediately, the helper provides a tested contract for the runtime boundary.

Alternatives considered: Using the step prompt renderer for planning was rejected because it intentionally filters non-current step data.

## Test Strategy

Decision: Use focused pytest coverage in `tests/unit/agents/codex_worker/test_worker.py` for instruction ordering, step filtering, compact inventory, absent manifest behavior, byte/data URL guardrails, and integration-style worker boundary coverage over prepared manifest plus vision index artifacts.

Rationale: The behavior is pure worker instruction composition over prepared workspace files and does not require Docker or external credentials. The required integration-style boundary is the worker prepare/instruction seam, so it belongs in the required unit suite rather than the Docker-backed hermetic integration suite.

Alternatives considered: Full Temporal integration was rejected for this story because no workflow/activity signature or persisted payload shape changes are introduced.
