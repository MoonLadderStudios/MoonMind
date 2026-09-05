# Checkpoint persistence queue histories

Captured from `MoonMindCheckpointBranchTurnWorkflow` at the cumulative candidate
`0ff48324335c77e8462d45650eefe740cca545b5` using the Temporal time-skipping server.
During capture only `checkpoint-branch-artifact-fleet-v1` was disabled, selecting
the retained production path that emits persistence commands on the workflow
queue. Activity responses are hermetic scenario inputs; these histories prove
command compatibility, not external provider or deployment qualification.

`success.json` includes checkpoint capture and verification handoff;
`canceled.json` contains cancellation and shielded terminal persistence;
`rejected.json` includes terminal rejection after invalid terminal evidence.
Tests replay these immutable histories against the production registry with all
current patches intact. Integration tests separately exercise retained real
persistence handlers and current routing, retries and resource ownership.
