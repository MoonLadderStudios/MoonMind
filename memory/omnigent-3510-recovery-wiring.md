---
name: omnigent-3510-recovery-wiring
description: Status and exact remaining seams for wiring Omnigent evidence-gated resume + Checkpoint Branch execution (issue #3510)
metadata:
  type: project
---

Issue MoonLadderStudios/MoonMind#3510 ("Wire evidence-gated resume and Checkpoint Branch execution into Workflow Detail") is a P0 epic. Verdict on entry was PARTIALLY_IMPLEMENTED. The decision layer already existed (recovered work on branch `mm/a54b7c14-.../terminal-recovered-work`): `decide_recovery_outcome` + `OmnigentRecoveryOutcome` (live_reattach/cold_restore/branch_required/resume_unavailable) in `moonmind/omnigent/checkpoints.py`, and typed activity contracts in `moonmind/omnigent/recovery_activity_models.py`.

**Done in this pass (tested, non-breaking):**
- Registered the coordinator methods as real production Temporal activities: `integration.omnigent.recover_from_checkpoint` and `integration.omnigent.branch_from_checkpoint`. Added the activity funcs in `moonmind/workflows/temporal/activities/omnigent_activities.py` (extracted `_omnigent_coordinator()` async CM), dispatch methods + mapping in `activity_runtime.py` (`TemporalAgentRuntimeActivities`), catalog entries in `activity_catalog.py`, and typed overloads in `typed_execution.py`. Boundary tests: `tests/unit/workflows/temporal/test_omnigent_recovery_activities.py`.
- Workflow Detail UI (`frontend/src/entrypoints/workflow-detail.tsx` `RecoveryEvidencePanel`): resume is now the primary/recommended affordance driven by the backend `defaultAction`/`operatorGuidance` (not just `eligible`); added first-class "Host recovery decision" narrative (reattach / cold-restore replacement / rejected). Tests in `workflow-detail.test.tsx`.

**Remaining (deliberately deferred — need their own replay-safe cutover + new surface):**
- AC2 execution-time routing: `moonmind/workflows/temporal/workflows/run.py` (22k-line `MoonMindRunWorkflow`) still re-executes recovery steps via `integration.omnigent.execute` (coordinator.execute), not the new recover/branch activities. Seam: `_restore_checkpoint_recovery_workspace` (~run.py:3608) + step dispatch (~run.py:11373). Requires Temporal patch/version gating for in-flight runs.
- AC6/7/8 real branch-turn execution: `CheckpointBranchService.launch_turn` (`api_service/services/checkpoint_branch_service.py`) is still persistence-only and requires caller-supplied `created_step_execution_id`. Needs a NEW branch-turn workflow that calls `integration.omnigent.branch_from_checkpoint` (the executor added this pass), started from the launch route (`POST .../checkpoint-branches/{branch_id}/turns/{turn_id}/launch`, executions.py ~13248) via `TemporalClientAdapter.start_workflow` (pattern: `api_service/services/remediation_actions.py`).
- AC9 branch-turn selectors (Provider Profile / execution profile / launch policy / model / effort) intentionally NOT added: the create body (`CheckpointBranchCreateModel`) has no such fields and branch turns don't execute yet, so the selectors would be dead scaffolding until AC6/7/8 land.
