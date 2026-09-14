# Deployment Overview (read-only conversational)

**Document Class:** Canonical declarative
**Status:** Accepted target
**Owner:** MoonMind Dashboard / Platform
**Audience:** dashboard, backend, workflow authors
**Implementation tracking:** MoonLadderStudios/MoonMind#424

**Authority:** This document owns the read-only conversational deployment
overview: UI entry, read capabilities, evidence/freshness contract, model-data
boundary, and failure behavior. It preserves existing chat, diagnostics,
status, and artifact ownership instead of copying their protocols:

- live agent interaction: `docs/UI/WorkflowChatPanel.md` (native chat binding),
- read-only collector + artifact writer:
  `moonmind/workflows/skills/ops_diagnostics_execution.py`,
- read-only diagnose tool contract:
  `moonmind/workflows/skills/deployment_tools.py`
  (`moonmind.ops_diagnose_stack`),
- projection implementation:
  `moonmind/workflows/skills/deployment_overview.py`.

## 1. UI entry

The initial entry is a clearly labeled **deployment overview** affordance in
the existing dashboard/runtime experience — not an attachment to every active
agent session. For a specific active workflow, answers link to the existing
Workflow Detail (`/workflows/{workflowId}`) and native chat binding
(`/workflows/{workflowId}/chat`) and their supported controls. No second
composer, transcript, agent controller, or always-on chat/metrics service is
created.

## 2. Read capabilities

Exactly four canonical questions, backed by current sources:

| Question | Source owner |
|---|---|
| What is running? | Authorized workflow list/detail |
| What is waiting (capacity wait)? | Recorded wait reason / capacity ledger |
| What recently failed? | Recorded terminal outcomes + artifact refs |
| Which deployment checks need attention? | Existing runtime readiness + bounded `moonmind.ops_diagnose_stack` evidence |

Each answer field carries its owner, permission, collection timestamp, and
freshness. Deployment-wide details remain operator-only; ordinary users see
only their permitted workflows. The principal is resolved on the server;
possession of a workflow id or URL is not authorization, and every follow-up
request (including link traversal) is independently re-checked.

Privileged collection stays on the existing deployment-control worker and
delivers a minimal safe result to the model. No free-form command, SQL,
arbitrary URL, environment dump, raw Docker inspect, or reusable credential
reaches the assistant.

## 3. Truthful observations

Process status, application probe, worker availability, and store access are
separate checks with `succeeded` / `failed` / `unavailable` /
`not_requested` outcomes:

- `docker compose ps` proves container presence only — never API
  reachability, worker queue polling, artifact round-trip success, or
  measured CPU/memory availability. `disk_memory_cpu` reports Docker storage
  only. Per-include support labels live in `OBSERVATION_SUPPORT`.
- Optional profile-gated services (`temporal-ui`, `docker-proxy`) absent or
  stopped are reported as `optional_absent`, not failures. One-shot init
  containers (`init-db`) that exited after success are `one_shot_complete`.
- Missing telemetry is `unavailable`, never zero backlog. A partial probe
  never claims a global all-clear. Stale observations (older than the
  freshness TTL) require re-collection; contradictory signals stay
  distinguishable instead of being merged into confirmed health.

## 4. Model-data boundary

Logs, repository text, exception messages, and tool output are untrusted
data, not instructions: redacted/allowlisted before model disclosure, with
detailed evidence kept behind artifact authorization. Summaries separate
observations from hypotheses and recommended next checks, and never invent
root cause, quota, completion, or cleanup success. Prompt-injected content is
withheld with a safe reason; sensitive values are redacted; requests for
arbitrary commands cannot widen assistant authority or expose secrets.

The assistant's own overview execution is identifiable so it cannot
recursively launch diagnostic assistants or count its presence as
unexplained user work.

## 5. Bounds and failure behavior

Query fan-out (25 workflows/answer), log tails, tokens, refresh, and
retention are bounded; cached observations are reused only within their
scope/freshness contract. If the API, model, or deployment-control worker is
unavailable, the UI shows the normal deterministic status/error view rather
than promising in-stack self-diagnosis.

A request to pause, cancel, retry, approve, deploy, or rotate credentials
leaves the read-only flow and uses the existing explicit
authorization/confirmation/expected-state operation; the first increment may
simply link to that control. The read path performs no workflow-control,
deployment, credential, or publication mutation.

## 6. Acceptance evidence

Hermetic tests use deterministic question/tool fixtures through the real
authorization/projection boundaries
(`tests/unit/workflows/skills/test_deployment_overview.py`); optional live
model evaluation is separate and never fabricates successful checks.
