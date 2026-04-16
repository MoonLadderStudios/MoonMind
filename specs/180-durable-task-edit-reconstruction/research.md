# Research: Durable Task Edit Reconstruction

## Current Reconstruction Gap

Decision: Replace input-parameter-first reconstruction with snapshot-first reconstruction.

Rationale: `frontend/src/lib/temporalTaskEditing.ts` currently builds `taskInstructions` only from `inputParameters.task.instructions`, `inputParameters.task.steps[].instructions`, or an `inputArtifactRef` payload loaded by `frontend/src/entrypoints/task-create.tsx`. The create path in `api_service/api/routers/executions.py` persists normalized planner parameters, which can omit original form state for skill-only, template-derived, or structured-input tasks.

Alternatives considered: Continue enriching `inputParameters`; rejected because normalized execution state is mixed with derived runtime/planner state and cannot faithfully represent all original form controls.

## Canonical Source

Decision: Create a versioned immutable `OriginalTaskInputSnapshot` artifact for each create/edit/rerun submission and expose a compact `taskInputSnapshot` descriptor from execution detail.

Rationale: The artifact system is already the canonical mechanism for large immutable inputs outside Temporal history. It supports execution linkage, retention classes, metadata, and refs, matching the constraints in the artifact docs.

Alternatives considered: New database table; not chosen initially because the artifact index already stores immutable blobs and execution links. Use plan artifacts; rejected as authoritative source because plans are derived execution output.

## Plan Artifact Fallback

Decision: Generated plan artifacts may be used only for degraded, read-only recovery assistance when no snapshot exists.

Rationale: Plan artifacts can contain synthesized instructions that differ from operator input. Treating them as original input would hide the exact failure reported by the user and violate the original-input versus derived-output distinction.

Alternatives considered: Automatically copy plan instructions into rerun draft; rejected because it creates a hidden compatibility transform and may change operator intent.

## Complex Task Shapes

Decision: The snapshot stores the create-form draft shape, not just planner payload shape.

Rationale: Template inputs, selected skills, skill args, per-step attachments, feature request text, draft customization state, and dependency choices are create-form concerns that may not survive normalization. Reconstructing from current template catalog or generated steps would drift.

Alternatives considered: Rebuild from normalized task payload plus current template catalog; rejected because templates and skill catalogs can change after submission.

## Cutover

Decision: Newly created supported executions require snapshots. Pre-cutover executions without snapshots do not receive broad backfill or hidden compatibility transforms.

Rationale: MoonMind is pre-release and the compatibility policy favors clear cuts. Operator-safe behavior is to disable edit/rerun or show a degraded read-only warning when only derived evidence exists.

Alternatives considered: Bulk backfill from existing `inputParameters` and plan artifacts; rejected because it would label inferred values as original input.

## Test Strategy

Decision: Add tests at four boundaries: frontend reconstruction, API/execution detail contract, artifact lifecycle/linking, and Temporal workflow/update payload refs.

Rationale: The failure spans UI, API persistence, artifact access, and workflow/update contracts. Isolated helper tests would miss boundary drift.

Alternatives considered: Frontend-only regression test; rejected because it cannot prove new executions persist authoritative snapshots.
