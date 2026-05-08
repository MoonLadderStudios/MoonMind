---
name: document-update
description: Align a technical document with the actual code implementation and correct drift. Use when a user asks to update docs to match current behavior, audit a technical document against the repository, or fix stale architecture, API, workflow, or operator documentation.
---

# Document Update

Update one or more technical documents so they accurately describe the implementation that exists in the current checkout.

## Purpose

Correct documentation drift by comparing the document's claims against source code, tests, schemas, configuration, and runtime entrypoints, then editing the document to describe the actual system behavior.

For canonical documentation under `docs/`, do not downgrade the documented desired state to match a buggy or incomplete implementation. When code contradicts the intended architecture, contract, operator-visible behavior, or target semantics, report the implementation gap clearly and preserve the canonical desired-state framing unless the user explicitly asks to change the target contract.

## Inputs

- Required: target document path, documentation topic, or explicit drift report.
- Required: current repository checkout containing the implementation to verify.
- Optional: feature/spec path that explains intended behavior.
- Optional: scope limits, such as specific sections, APIs, workflows, UI behavior, or files.
- Optional: verification commands requested by the user or repository instructions.

## Documentation Boundaries

- Treat repository files, tests, schemas, and executable configuration as the source of truth for implementation behavior.
- Treat retrieved context, old docs, comments, generated artifacts, and issue text as reference material until confirmed against the current checkout.
- Keep canonical docs under `docs/` focused on desired state: architecture, contracts, operator-visible behavior, and target semantics.
- Put migration notes, rollout checklists, implementation backlogs, and temporary investigation details in feature-local artifacts under `specs/<feature-id>/` or the `artifacts/` directory for tool handoffs, not as the main framing of canonical docs.
- When a superseded behavior is no longer implemented, remove or replace the stale description instead of preserving compatibility-era ambiguity.

## Workflow

1. Resolve the update target.
   - Identify the document path or section to update.
   - If the user provides only a topic, search likely docs with `rg` and choose the narrowest document that owns the topic.
   - If multiple documents conflict, identify the canonical owner before editing and update cross-references only when needed.

2. Extract the document claims.
   - Read the relevant sections and list the concrete claims they make about behavior, contracts, configuration, data flow, APIs, UI, workflows, or operator actions.
   - Separate durable system semantics from historical notes, TODOs, migration plans, or examples.
   - Mark claims that are ambiguous instead of silently guessing their intent.

3. Inspect the implementation.
   - Search the repository for the named classes, functions, routes, models, settings, activities, schemas, tests, and UI components.
   - Prefer direct evidence from source files, tests, migrations, fixtures, route contracts, type definitions, and committed configuration.
   - For workflow or activity boundaries, inspect the real invocation shape and payload models, not only helper functions.
   - For UI behavior, inspect the component, state helpers, API client, and tests that exercise the user-visible behavior.

4. Build a drift ledger before editing.
   - For each document claim, classify it as:
     - `accurate`
     - `stale`
     - `missing`
     - `ambiguous`
     - `out_of_scope`
   - Record concise evidence for every `stale`, `missing`, or `ambiguous` item.
   - If the implementation appears internally inconsistent, document the inconsistency in the ledger and avoid inventing a new contract.

5. Edit the document.
   - Update only the sections needed to correct the drift.
   - Describe the current behavior directly and concretely.
   - Remove obsolete compatibility language, stale migration framing, and contradicted examples.
   - Preserve the document's existing voice, heading structure, and terminology where they remain accurate.
   - Add or update cross-references only when they help readers find the canonical contract.

6. Verify the update.
   - Re-read the changed sections and compare them against the drift ledger.
   - Run targeted documentation checks if available.
   - Run the repository-mandated programmatic checks from `AGENTS.md`, even for documentation-only updates.
   - Add any extra targeted code tests needed when the documentation change depends on behavior that is not already evident from existing tests or source inspection.
   - If verification cannot be run, record the exact blocker.

7. Finalize the evidence.
   - Summarize which document changed, what drift was corrected, and the implementation evidence used.
   - Include validation commands and results.
   - If requested by the task, commit the documentation update with a concise message.

## Outputs

- Updated document path(s).
- Drift ledger summary with each corrected or intentionally deferred claim.
- Implementation evidence references, including file paths and relevant tests or schemas.
- Validation commands run and their result, or the reason validation was not run.
- Commit hash when the user requested a commit.

## Failure Modes

- Target document cannot be identified: stop and report the candidate documents or missing path.
- Implementation evidence cannot be found: mark the claim `ambiguous`; do not assert behavior as implemented.
- Multiple canonical docs conflict: update the identified owner and note any remaining conflict that needs a separate decision.
- The document describes intended behavior that code does not implement: report the gap clearly and update only if the user's goal is to document actual behavior.
- Required verification cannot run: keep the doc update if source evidence is sufficient, but report the blocked command and reason.
- Secret-like content appears in examples or copied logs: redact it before writing or reporting.
