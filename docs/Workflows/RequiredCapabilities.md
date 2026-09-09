# Required Capabilities

**Document Class:** Canonical declarative  
**Viewpoint:** Module Contract Specification  
**Status:** Draft  
**Owners:** MoonMind Engineering  
**Updated:** 2026-09-06  
**Audience:** Workflow, runtime, integration, and admission contributors and operators  
**Authority:** requiredCapabilities declaration, derivation, normalization, readiness checks, provenance, and failure semantics. Capability requirements do not grant repository or publication authority.  
**Owning Surface:** Shared execution requirement compiler and pre-launch readiness boundary  
**Related Implementation:** `moonmind/workflows/executions/execution_contract.py`, resolved Skill metadata, and the existing runtime admission owners.

**Related Docs:** `docs/Workflows/WorkflowArchitecture.md`, `docs/Steps/StepTypes.md`, `docs/Steps/SkillSystem.md`, `docs/Workflows/SkillAndPlanContracts.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/Workflows/WorkflowPublishing.md`, `docs/RepositoryAccessAndWorkspaceDesign.md`, `docs/Workflows/LoreVcsIntegrationDesign.md`

## 1. Purpose

Required Capabilities state what MoonMind must provide before a workflow execution, step, Tool, Skill, preset-derived operation, or runtime session starts. They make local Git, repository preparation, hosting/tracker operations, runtime support, container access, and fan-out requirements explicit.

They describe requirements. They do not grant authorization, contain credentials, invoke tools, select a Skill, or replace policy checks.

## 2. Desired-state summary

MoonMind has one normalized contract:

```ts
type RequiredCapabilities = string[];
```

The control plane compiles an ordered, deduplicated execution-facing list from runtime, source/workspace, resolved publication role, selected Skills and supporting-Skill closure, Tools, preset expansion, output format, and additive explicit requirements. Worker routing, preparation, and readiness consume this evidence rather than rediscovering requirements after launch.

Compilation is scoped to the relevant execution and effect owner. The parent's plan can validate known child requirements, but it does not inject every descendant's credentials or write authority into the coordinator. A batch's root PR intent and the coordinator's own no-publication role are distinct. Child admission verifies the frozen inherited scope and prepares only the child's required operations.

Unsatisfied required capabilities block before the affected launch/effect with a structured reason. Unsupported scope/policy combinations are rejected, not repaired by changing the user's publishing mode.

## 3. Terminology

A required capability is a normalized token such as git, gh, jira, docker, or execution.fanout. Its source identifies the declared operation or compiler decision. Satisfied means the required readiness evidence is valid for the exact target, policy, and current authorization; unavailable or unknown required evidence is not satisfaction.

Materialization prepares concrete state such as workspace content, sanitized tracker artifacts, an active Skill bundle, or scoped runtime delivery. It does not mean every declared integration credential is copied into the agent.

Selectable Tool/Skill/Preset catalog capabilities and execution requirement tokens are separate concepts.

## 4. Non-goals and boundaries

This is not a new repository/tracker access system, permission store, Tool invocation, SkillSet selector, runtime command, approval mechanism, or substitute for Provider Profiles.

Git can mean local Git without a remote repository. GitHub operations require the selected supported access path and appropriate authority; the presence of gh does not prove a token is needed for every qualified read, or authorize anonymous fallback. Jira content can use trusted prefetched artifacts where the operation permits it; a mutation still requires its own authorized Tool boundary.

## 5. Contract surfaces

### 5.1 Top-level execution contract

The normalized execution payload carries its final requirement list. A worker-facing list is authoritative only as part of the admitted immutable plan. A caller cannot attest readiness by authoring the same strings.

### 5.2 Workflow or task Skill field

A selected Skill can contribute explicit requirements in its Skill selector, additive with resolved metadata. New repository/branch/publication choices remain workflow-level and cannot be smuggled into Skill inputs by calling them capabilities.

### 5.3 Step Skill field

Step declarations contribute to the containing execution's requirements and retain provenance. Read-only steps do not erase a later publishing step's requirements. Conversely, the workflow's PR intent does not make a read-only step an independent publisher.

### 5.4 Tool field

Typed Tool definitions and steps contribute the worker/integration requirements needed for their declared operations. This does not convert Tools to Skills or grant all actions in an integration.

### 5.5 Preset metadata and expansion

Resolved preset metadata and generated steps contribute requirements before launch. Included assessment or coordinator roles are preserved. The worker never consults a live preset catalog to acquire extra requirements for a pinned run.

### 5.6 Agent Skill metadata

```yaml
metadata:
  required-capabilities:
    - jira
    - git
    - gh
```

Deployment-stored content preserves the corresponding normalized required_capabilities metadata. Supporting Skills selected through required-skills contribute their requirements through the resolved closure. The immutable content/source evidence participates in policy checks.

### 5.7 Runtime and publish derivation

Publication requirements come from the shared compiler's **resolved execution role**, not directly from a generic authored string:

| Context | Derivation |
| --- | --- |
| Authored Auto/default | Resolve declared behavior first; default is never a worker capability or unresolved execution mode. |
| Managed GitHub PR publication | Local candidate/repository and hosting PR operations for the actual publisher. |
| Branch publication | Required branch/write mechanics without automatically requiring PR or issue-write authority. |
| Skill-owned auto | Declared Skill operations, exact target, supporting Skills, and terminal evidence. |
| Coordinator under a PR batch scope | Discovery/tracker/fan-out requirements, not an unnecessary parent repository publisher. |
| Explicit scope None | Independent local/tracker/dispatch requirements remain; push/merge-required objectives are rejected as incompatible. |
| Scratch/report or anonymous source | Only supported source/output capabilities, not a hypothetical future publisher. |

Runtime mode contributes the qualified runtime requirement; container operations contribute docker where needed. A PR-capable external provider can satisfy declared publishing mechanics only through its qualified adapter. It cannot implement None or Branch by silently creating a PR.

Repository provider support and evidence are part of that qualification. Managed and agent-owned new results both use `moonmind.publish.repository.v1` with the provider's actual admitted connection/client and revision proof. A GitHub projection does not make a Git-only publisher eligible for a Lore-authoritative target. The retired workspace publish default cannot influence new role/capability derivation.

## 6. Normalization and merge rules

Tokens are strings, trimmed, lowercased under the current registry, nonblank, and deduplicated in first-seen order. Invalid types and blanks fail. The backend recomputes declarations even when the browser omits them.

Sources are additive within the compiled execution: explicit incoming requirements, runtime/source requirements, derived publishing/effect requirements, preset metadata/expanded steps, selected Skill closure, explicit Skill requirements, Tools, and adapter/container requirements. Preserve stable source provenance.

Removing a required operation is not ordinary authoring. A contradictory policy is rejected; the system does not drop Skill requirements, choose the most permissive mode, or modify explicit None to make a plan launch. A separately supported narrowing must have a validated compatible objective and current authorization.

Per-child lists are compiled at child admission with the parent's frozen scope and declared target mapping. Neither a coordinator-local None nor a latest-catalog child default substitutes for that scope. Top-level requirement flattening is not blanket credential inheritance.

## 7. Capability semantics

### 7.1 git

Git requires the needed local Git or supported repository-workspace mechanics. When a repository is used, the target/access policy and applicable branch/base/head can be prepared. Scratch Git exports can require local Git with no remote target or credentials. Applicable source/output/publication constraints must be satisfiable before launch.

Git never implies push permission. A selected implementation base remains distinct from a generated PR head, and a resolved existing-PR head comes from trusted target evidence rather than a duplicate input override.

### 7.2 gh

Gh requires supported GitHub repository/PR operations through the qualified CLI or equivalent hosting path. Verify applicable repository/PR access, selected connection/access intent, operation scopes, and current authorization. Mutation and comment requirements are distinct from reads and from each other.

A read-only probe or an endpoint-required permission header does not prove write authority. Unknown observation remains unknown. A connector 404 alone does not establish whether the selected runtime path is unavailable; evaluate the actual canonical bound path without credential shopping or target substitution.

### 7.3 jira

Jira requires trusted issue/project operations or qualifying prefetched trusted content. Known issue targets are authorized, private payloads are sanitized/materialized when needed, and raw Atlassian credentials are not placed in managed shells or Skill files. Prefetched issue content cannot satisfy a declared later mutation by itself.

### 7.4 docker

Docker requires an enabled qualified container path, allowed deployment/workflow policy, enforced resource/network limits, and correct ownership of workspace side effects. Availability of a daemon is not permission to mount arbitrary paths or acquire repository credentials.

The planning Activity requires the container-job HTTP/MCP service feature flag
(`MOONMIND_CONTAINER_JOBS_ENABLED`) and validates deployment-owned configuration
through the shared Docker Backend settings resolver. Compose passes the same
enabled default and explicit override to the API, planning, and agent-runtime
workers; outside Compose an omitted service flag remains disabled. Blockers
identify the offending setting and constraint without exposing authored values.
Planning does not require a local socket,
`DOCKER_HOST`, or raw Docker CLI access on the LLM/planning worker. These are not
readiness evidence for the selected execution owner, and their presence cannot
override a disabled or invalid backend.

The agent-runtime worker owns live daemon and egress qualification and publishes
`containerBackend` readiness. Container-job admission and launch retain workspace,
resource, network, and authorization checks. Planning acceptance proves static
configuration eligibility, not live daemon health or permission to execute a job.
This division is identical for managed and Omnigent runtimes and applies to
historical planning payloads without changing their serialized contract.

### 7.5 execution.fanout

Fan-out is bounded child creation and child-only inspection under the current parent, not general MoonMind API access.

Trusted normalization derives the requirement from resolved sideEffect.kind enqueue_children metadata. Built-in/deployment-managed provenance and normal policy produce the immutable allow/deny attestation required before bearer minting. An authored top-level token does not grant authority; repository/local metadata remains untrusted. Missing historical attestation is allowed only under the recorded replay contract, not a new-write bypass.

Readiness requires a permitted child contract, a short-lived capability bound to parent workflow/run, agent run, step, runtime session/id, reachable allowlisted create/describe routes, and the capability file/material available before model execution. Runtime advertisements are readiness inputs, not permission.

The API requires stable idempotency and runtimeInheritance caller. It validates publication-scope inheritance server-side against the authenticated parent and pinned definitions. No optional helper flag, copied parent ID, alternate create shape, or local None can bypass it. Children receive validated target-derived PR heads where declared; arbitrary repository overrides remain forbidden. Schedules, unrelated reads, and broader operations are denied.

### 7.6 Runtime-mode capabilities

Runtime requirements such as codex_cli, claude_code, or omnigent require enabled adapters, qualified worker launch, compatible Profile/model policy, and supported workspace/Skill/artifact/prompt contracts. The trusted runtime-selection owner supplies evidence; strings do not attest themselves.

For hybrid Omnigent, keep the runtime requirement in the normalized execution contract and prove it through selected plan authority. Do not add it to historical v1 ClassAdmissionDecision.requiredSatisfied or its exact-host digest when older workers interpret that field as host-advertised capabilities. The exact host still proves harness, image/vendor runtime, workspace, network, model, Skill, and Tool support.

### 7.7 Scoped future capabilities

Coarse tokens can be refined by supported aliases such as jira.read, jira.comment.write, github.pr.read, github.pr.merge, or repo.branch.write through the same versioned capability compiler. A finer name is not a parallel access model or proof of confinement.

## 8. Readiness and blocking model

Resolve selected definitions, context bindings, preset expansion, source/target roles, and publication intent before deriving readiness. Check required capabilities before session/agent/Tool launch and before any effect relying on them.

Validate static child incompatibilities before parent launch or tracker mutation when known. Dynamic discovery validates each target before child dispatch. External partial failure preserves accepted child IDs and outcomes instead of claiming atomic rollback.

Blockers identify status, capability, contributing source, safe target identifiers, check, reason, and remediation. Never include raw tokens, auth headers, cookies, API keys, environment dumps, or unnecessary private content.

New recurring executions check the current declared requirements of their saved
presets and their current include graphs before planning or agent launch,
including executions with a saved plan. Both canonical `workflow` parameters and
persisted `task` parameters carry this provenance.
`plan.check_preset_capabilities` reads the scoped catalog at an Activity boundary;
the workflow carries only compact preset provenance and admitted requirements.
A missing capability or unavailable source returns `saved_preset_capabilities_stale`
with the affected presets and a plan-refresh path. Unknown readiness never
authorizes launch. A preset content change that adds no unmet requirement does
not block an otherwise valid schedule. The check covers newly included presets
using the catalog's normal scope, availability, and cycle rules. It reads declared
requirements without rendering a new plan; conditional include requirements
remain conservative until the operator reapplies the preset with its inputs.

Saved schedules do not inherit new capabilities merely because a deployment
updated its presets. Reapply the current presets in Workflow Create and admit a
replacement schedule, preserving authored inputs, repository, provider profile,
model, effort, publication policy, and cadence; retire the previous schedule
after the replacement is verified. Refreshing admission evidence or restarting
workers does not rewrite the saved task. The check does not grant permissions,
edit steps, or change runtime selection. Historical provenance without a scope
uses the preset catalog's global default; personal presets use the execution
owner and cannot substitute another owner's definition. Recorded pre-check
histories and Continue-As-New executions retain their admitted progress.

## 9. Authorization and policy boundary

Capability satisfaction requires deployment policy, target policy, principal authorization, approval/autonomy policy, qualified runtime preparation, and safe credential handling. It cannot exceed the admitted publication scope or turn a requested None into permission for recovery push.

Managed publication keeps destination write material outside the agent where the contract requires it. Skill-owned publication needs the qualified mediated or explicitly accepted exposure policy. A prompt or routing allowlist does not confine an agent holding broader credentials; unsupported required guarantees block rather than being advertised as enforced.

## 10. Skill interaction rules

Selected and supporting Skills contribute requirements, but not unrestricted Tool access. Tools remain governed by policy and approvals. The runtime uses the immutable resolved Skill closure, not newly discovered files after launch.

A Skill-owned publisher remains the semantic/effect owner under compiled Auto and exact evidence. User-facing Auto only selects its declared behavior. An incompatible explicit None requires correction, not a name-based capability fallback or a native substitute resolver.

## 11. Preset interaction rules

Presets contribute definition metadata and generated step requirements through the same compiler. Included read-only/assessment roles do not overwrite the root publication intent. A coordinator can validate that children require PR support without receiving their publishing credentials itself.

Rerun/detail provenance explains definition, task inputs, bound context, recommended versus explicit policy, and per-execution requirements. A child follows the frozen scope, not its latest standalone default. Main issue batches, breakdown families, document orchestration, and resolver families use this same rule.

## 12. Replay, rerun, and Resume semantics

Normalized requirements are part of the admitted contract. Exact recovery uses original intent/definition evidence and rechecks current authorization. Explicitly edited new admission may recompute requirements, but cannot mutate already-admitted children or historical hashes.

Resume from a failed step preserves its original requirements; a formerly available but now unavailable capability fails before execution. Historical alias decoding and the old Auto/None behavior remain versioned. New authoring does not silently re-resolve changing defaults or drop requirements on retry.

## 13. Observability

Authorized detail/debug views expose normalized requirements, source provenance, readiness and materialization evidence, safe blockers, and whether each requirement belongs to a runtime, local step, publisher, or child.

The UI can summarize details but must distinguish authored publication intent from coordinator-local mode and descendant authority. A coordinator with PR children is not mislabeled as globally publish-disabled. Busy qualified capacity is not structural incompatibility or permission to choose another profile.

## 14. Security invariants

No secrets in capability or definition metadata. No runtime broadening after launch. No raw integration credential delivery merely because a coarse token exists. Prefer trusted sanitized content. Treat repository/local metadata as untrusted until normalized and policy-checked. Materialization and readiness retries are idempotent or safely reconciled with exact ownership.

## 15. Core invariants

One normalized capability contract and one backend compiler serve all producers. Declarations are requirements, not grants. Equivalent direct Skill and preset-backed work derives equivalent scoped requirements. Known incompatibilities block before effects. Provenance is inspectable. No live catalog lookup or helper-local default changes admitted authority. Data remains safe for history, artifacts, and logs.

## 16. Validation and test requirements

Production-boundary tests cover token normalization, source/runtime/container/Tool/preset/Skill-closure contribution, omitted browser metadata, hard readiness failures, safe diagnostics, and exact recovery.

Publication-specific coverage proves:

- Auto resolves before derivation and is distinct from Skill-owned execution Auto.
- Managed GitHub PR publication has required hosting operations at the publisher, not automatically inside a coordinator.
- Branch, local Git, anonymous reads, scratch output, and tracker-only work do not acquire irrelevant PR credentials.
- Coordinator None with PR/Auto descendants preserves scope and child-only authority through nesting.
- Explicit None cannot be bypassed by a publishing Skill, review/merge child, raw payload, or recovery branch.
- Per-PR target heads and selected issue-batch bases survive binding and readiness checks.
- Static incompatible branch/dependency handoffs fail before effects; dynamic failures remain truthful and bounded.
- Fan-out capability provenance, missing/forged attestation, expired bearer, idempotency conflicts, and unrelated API operations cannot broaden scope.
- Mixed-version replay and edited admission preserve historical evidence and current authorization separately.
- New publication writers/readers share the provider-neutral evidence contract; old workspace defaults and legacy evidence formats cannot supply new authority or missing proof.

Required hermetic tests and protected-live/runtime conformance are identified separately. A parser or UI result alone does not qualify execution.

## 17. Documentation boundaries

Workflow Publishing owns authored policy and compiled publication roles. Repository Access and Workspace Design owns source/access/save separation. LoreVcsIntegrationDesign owns provider compatibility and the shared repository evidence schema. Step Types owns selectable categories, Skill System owns resolution/materialization, SkillAndPlanContracts owns executable Tool/plan interfaces, and managed/external runtime docs own their substrate. This document owns requirement derivation and readiness, not a second implementation of those domains.

## 18. Summary

Required Capabilities make execution prerequisites explicit and enforceable before launch. They use the single authored context and publication policy to derive the requirements of each actual effect owner without turning a batch coordinator into a publisher or treating a token as authorization.
