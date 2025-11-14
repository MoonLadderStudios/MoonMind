# Data Model – MoonMind Orchestrator Implementation

**Purpose**: Define the entities, attributes, and relationships required to track orchestrator instructions, approvals, step execution, and artifacts as described in the feature specification and Phase 0 research.

---

## 1. OrchestratorRun
- **Description**: Canonical record for a single high-level instruction processed by the orchestrator.
- **Key Fields**:
  - `run_id` (UUID) – primary identifier, shared across Celery tasks and artifact paths.
  - `instruction` (text) – operator-supplied intent string.
  - `target_service` (enum referencing compose service names) – validated before plan generation.
  - `action_plan_id` (FK → ActionPlan).
  - `status` (enum: `pending`, `running`, `awaiting_approval`, `succeeded`, `failed`, `rolled_back`).
  - `started_at`, `completed_at` (timestamps).
  - `approval_gate_id` (nullable FK → ApprovalGate).
  - `approval_token` (nullable text) – evidence of granted approval.
  - `metrics_snapshot` (JSON) – StatsD counters/timers cached for UI.
  - `artifact_root` (text) – path under `var/artifacts/spec_workflows/<run_id>`.
- **Validation**:
  - `target_service` must exist in `docker-compose.yaml`.
  - `approval_token` required when `target_service` is tagged as protected.
  - `completed_at` must be ≥ `started_at` when set.
- **State Transitions**:
  - `pending` → `running` when plan execution starts.
  - `running` → `awaiting_approval` if preconditions fail due to missing approval.
  - `running` → `succeeded` only when verify step passes.
  - `running` → `failed` when a step errors and rollback not attempted.
  - `running` → `rolled_back` when rollback finishes (success or failure reason captured).

## 2. ActionPlan
- **Description**: Ordered list of orchestrator steps derived from the instruction and service metadata.
- **Key Fields**:
  - `action_plan_id` (UUID) – primary key referenced by runs.
  - `steps` (array of PlanStep records or JSON structure) – `analyze`, `patch`, `build`, `restart`, `verify`, `rollback`.
  - `service_context` (JSON) – file allow list, health endpoints, compose flags.
  - `generated_at` (timestamp).
  - `generated_by` (enum: `operator`, `llm`, `system`) – indicates who produced the plan.
- **Validation**:
  - Steps must be unique and appear in canonical order.
  - `rollback` step must include at least one strategy (git revert, rebuild, image swap).
- **Relationship**:
  - One `ActionPlan` can be reused by multiple OrchestratorRuns when retried; each run stores plan snapshot to avoid mutation issues.

### PlanStep (embedded/child entity)
- **Fields**:
  - `name` (enum of allowed steps).
  - `parameters` (JSON) – e.g., list of files to edit, compose service, health URL.
  - `status` (enum: `pending`, `in_progress`, `succeeded`, `failed`).
  - `started_at`, `completed_at`.
  - `artifact_refs` (array of RunArtifact IDs).
- **Validation**:
  - `parameters.files` must stay within allow list.
  - `verify` step must declare timeout/backoff policy.

## 3. RunArtifact
- **Description**: Structured reference to files produced during a run (diffs, logs, metrics snapshots).
- **Key Fields**:
  - `artifact_id` (UUID).
  - `run_id` (FK → OrchestratorRun).
  - `type` (enum: `patch_diff`, `build_log`, `verify_log`, `rollback_log`, `metrics`, `plan_snapshot`).
  - `path` (text) – relative to `artifact_root`.
  - `checksum` (SHA256 string) – ensures tamper detection.
  - `size_bytes` (integer).
  - `created_at` (timestamp).
- **Validation**:
  - `path` must reside within the run’s artifact root.
  - `checksum` required for log/diff artifacts; optional for derived summaries.
- **Relationship**:
  - Each OrchestratorRun must have at least one `patch_diff` when files were changed and at least one `build_log`.

## 4. ApprovalGate
- **Description**: Policy record describing which services require human approval before orchestration steps that modify code.
- **Key Fields**:
  - `approval_gate_id` (UUID).
  - `service_name` (enum).
  - `requirement` (enum: `none`, `pre-run`, `pre-verify`).
  - `approver_roles` (array of role identifiers).
  - `valid_for_minutes` (integer) – approval lifetime.
  - `last_updated_at` (timestamp).
- **Validation**:
  - `valid_for_minutes` must be ≥ 5.
  - `approver_roles` cannot be empty when `requirement` is not `none`.
- **Relationship**:
  - `ApprovalGate` is referenced by OrchestratorRun (single active policy per service) and by MoonMind API when issuing tokens.

## 5. TaskState (aligns with `spec_workflow_task_states`)
- **Description**: Persists Celery task lifecycle for each plan step to power the MoonMind UI timelines.
- **Key Fields**:
  - `task_state_id` (UUID).
  - `run_id` (FK → OrchestratorRun).
  - `plan_step` (enum).
  - `celery_task_id` (UUID string).
  - `state` (enum: `PENDING`, `STARTED`, `RETRY`, `SUCCESS`, `FAILURE`).
  - `message` (text) – summary or error snippet.
  - `artifact_refs` (array of RunArtifact IDs).
  - `created_at`, `updated_at`.
- **Validation**:
  - `state` transitions must follow Celery semantics.
  - `message` is required for failure states.

---

## Relationships Overview

- **OrchestratorRun** 1 — 1 **ActionPlan** (plan snapshot)  
- **OrchestratorRun** 1 — N **PlanStep / TaskState** (execution tracking)  
- **OrchestratorRun** 1 — N **RunArtifact** (artifact catalog)  
- **ApprovalGate** 1 — N **OrchestratorRun** (policy enforcement)  
- **PlanStep** references **RunArtifact** entries produced during execution, enabling UI links between steps and artifacts.

---

## Validation & Audit Rules

1. Every run touching a protected service must store a non-expired `approval_token` before the patch step transitions to `in_progress`.
2. Artifact directories are created lazily but must exist before the build step completes; absence should fail the run with actionable guidance.
3. Rollback actions append a `rollback_log` artifact and update the run status to `rolled_back`, regardless of success, ensuring operators can inspect remediation attempts.
4. Metrics snapshots captured at run completion must include duration per step so success criteria (20-minute patch→verify, artifact availability) are auditable.

These structures provide the scaffolding needed for Phase 1 contracts and implementation tasks while aligning with the orchestrator specification.
