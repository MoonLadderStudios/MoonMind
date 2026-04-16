# Research: Merge Automation Visibility

## Parent Run Summary Projection

Decision: Add a compact top-level `mergeAutomation` object to `reports/run_summary.json` when parent publish context shows merge automation was enabled or active.

Rationale: The source design requires root terminal summary visibility, while existing parent workflow state already records merge automation child status and result in `_publish_context`.

Alternatives considered: Require Mission Control to parse nested publish context only. Rejected because the story explicitly names a `mergeAutomation` summary object.

## Child Artifact Writes

Decision: `MoonMind.MergeAutomation` writes JSON artifacts through the existing artifact activities and records artifact ids in its compact summary payload.

Rationale: Artifact activities are the existing durable storage boundary, and keeping refs in workflow state avoids embedding full histories.

Alternatives considered: Store all snapshots in memo. Rejected because memo/search attributes must stay compact and are not durable inspectable reports.

## Mission Control Display

Decision: Render merge automation state from `runSummary.mergeAutomation`, falling back to `runSummary.publishContext` where needed.

Rationale: The UI already displays run summary and publish context. A scoped panel there avoids creating a separate dependency or schedule surface.

Alternatives considered: Add a new route or page for merge automation. Rejected by the source design.
