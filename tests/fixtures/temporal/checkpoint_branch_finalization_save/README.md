# Checkpoint Branch failed-capture history before finalization-save resume

Captured from `MoonMindCheckpointBranchTurnWorkflow` at the cumulative candidate
`542cbf0094788a8833dd6594ce74400455fd86da`, before the branch resumed its
checkpoint phase from the child's finalization save
(`checkpoint-branch-finalization-save-v1`). The Temporal time-skipping server
ran the real `MoonMind.AgentRun` child. Its finalization owner reported a
complete verified `savedWorkspaceCheckpoint`, and every
`workspace.capture_checkpoint` attempt failed, so the turn persisted a failed
terminal naming that save for reconciliation.

Activity failure stack traces and worker identities were blanked; replay does not
read them. Activity responses are hermetic scenario inputs. The history proves
command compatibility for unpatched executions, not provider or deployment
qualification.
