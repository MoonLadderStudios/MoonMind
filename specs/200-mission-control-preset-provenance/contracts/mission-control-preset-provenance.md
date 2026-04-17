# Contract: Mission Control Preset Provenance Surfaces

## Scope

This contract defines the operator-visible behavior Mission Control must follow when showing preset-derived work.

## Preview And Submit

- `/tasks/new` may show composed preset previews before submission.
- Preview may group steps by preset binding or include path, but submitted runtime work must be flat resolved steps.
- Unresolved preset includes must be blocked before runtime submission.

## Task List And Detail

- Task list surfaces may show compact preset-derived context only when it helps scanning.
- Task detail may show provenance summaries and chips for Manual, Preset, and Preset path.
- Flat steps remain the primary execution ordering model in all task detail views.

## Evidence Hierarchy

- Canonical execution evidence is flat steps, logs, diagnostics, and output artifacts.
- Expansion tree artifacts or summaries are secondary explanatory evidence.
- The UI must not infer runtime completion, latest output, or execution order from expansion summaries.

## Vocabulary

- User-facing language should use preset for operator concepts.
- Internal details should use binding or provenance.
- Preset includes must not be labeled as subtasks, sub-plans, or separate workflow runs.

## Traceability

- MoonSpec artifacts and verification evidence must preserve MM-387.
- Source mappings: DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-022, DESIGN-REQ-025, DESIGN-REQ-026.
