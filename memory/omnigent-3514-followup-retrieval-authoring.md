---
name: omnigent-3514-followup-retrieval-authoring
description: How #3514 follow-up retrieval authoring flows UI → launch snapshot → gateway, and the tenant/branch decisions
metadata:
  type: project
---

Issue #3514 (Omnigent in-session/follow-up retrieval) split: the server-side gateway/capability/budget/evidence layer (items 1–5) shipped earlier in PR #3552. The remaining authoring controls (item 6) + Workflow Detail diagnostics (item 7) were added on branch `github-issue-implement-moonladderstudios-ed6f9fd0`.

Data flow (non-obvious, no generic passthrough): authored config must be a **payload-level** key `rag` / `followUpRetrieval` on the create request. Two backend hops each use an allowlist:
1. `api_service/api/routers/executions.py` `_create_execution_from_workflow_request` lifts `payload["rag"|"followUpRetrieval"]` into `initial_parameters` (next to the `omnigent` lift).
2. `moonmind/workflows/temporal/workflows/run.py` `_build_agent_execution_request` param-key allowlist copies them into `request.parameters`.
Then `moonmind/omnigent/profile_bound_execution.py` `compile_follow_up_retrieval_policy` compiles `request.parameters["followUpRetrieval"]` into the launch snapshot's top-level `followUpRetrieval` block (before the digest), which the gateway `_bridge_authoritative_issue` reads.

Key decisions:
- **MoonMind has no tenant concept anywhere** (grep tenant in rag/omnigent/policies = nothing), but the retrieval budget snapshot requires a non-empty `tenant_id`. The coordinator defaults it via `MOONMIND_FOLLOWUP_RETRIEVAL_DEFAULT_TENANT` (fallback `"default"`); otherwise enabling follow-up retrieval would always disable with `incomplete_follow_up_retrieval_scope`.
- Follow-up retrieval is an **authority boundary → opt-in / disabled by default**.
- **Checkpoint branch turn** launch uses a specialized `context_payload`, NOT the normal parameters channel, so a branch turn **inherits the parent run's compiled follow-up retrieval policy**. The branch create/continue/fork request models gained an optional `follow_up_retrieval` field (recorded as authored intent, folded into the idempotency digest); full per-turn launch consumption is follow-on.

Shared frontend: `frontend/src/lib/contextRetrievalAuthoring.ts` (compile/clamp/parse/denials) + `frontend/src/components/ContextRetrievalControls.tsx`, wired into workflow-start, schedules edit, omnigent policy editor, and the branch panel. Diagnostics: `GET /retrieval/bridge-sessions/{id}/follow-up-retrieval` (registry `summarize_bridge_session`) rendered in workflow-detail. Related: [[omnigent-3507-workspace-materialization]].
