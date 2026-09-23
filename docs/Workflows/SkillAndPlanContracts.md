# Execution Tool and Plan Contracts

**Status:** Active contracts and specialized execution paths, with remaining general tool-plan integration tracked separately. This document is not evidence that every declared tool can execute through every product entrypoint.  
**Updated:** 2026-09-21  
**Authority:** Executable tool and plan semantics under [AGENTS.md](../../AGENTS.md). Instruction Skills remain owned by the [Skill System](../Steps/SkillSystem.md).

Implementation gaps belong in existing issues, not a second checklist or registry in this document. The [previous full specification](https://github.com/MoonLadderStudios/MoonMind/blob/50262ac4d68f71ce5b73f83a37e2755dfd31ff5b/docs/Workflows/SkillAndPlanContracts.md) remains available for historical examples. Retained payloads keep their original meaning.

## 1) Document boundary and Purpose

Temporal Workflow code coordinates recorded work. Activities and existing external services perform I/O and side effects. MoonMind's tool registry, plan contracts, Step Execution ledger, and artifact services supply the application boundary. Reuse them rather than creating another interpreter, scheduler, or result store.

This document covers ToolDefinition, invocation, dependency/data-reference semantics, bounded execution, and progress. It does not own AgentSkillDefinition, SkillSet resolution, `.agents/skills`, or instruction materialization. A deterministic tool should not need an agent wrapper merely to invoke it, while an instruction Skill should not be converted into native policy merely because its name resembles a tool.

### 1.1 Terminology policy (Temporal era)

| Concept | Existing meaning |
| --- | --- |
| Workflow execution | The admitted user request, interpreted by the existing execution schema. |
| Step | A compiled executable plan node with stable identity. |
| `tool.type = "skill"` | The retained wire spelling for an executable tool contract, not an instruction bundle. |
| `tool.type = "agent_runtime"` | Agent work dispatched through the existing AgentRun/runtime boundary. |
| Runtime-native command | A typed selection within agent-runtime work, not a third tool type or an executable-registry entry. |
| Agent Skill | A portable instruction bundle resolved by the Skill System. |

ToolDefinition, ToolResult, ToolFailure, Step, and ToolPolicies retain their existing Python aliases where actual consumers require them. Class aliases are not authored capability names. New tool invocations identify the tool by name; the existing parser owns legacy object spelling and rejected version fields. Do not add a new alias registry or rewrite old serialized records to match new terminology.

**Support boundary:** At the September 21 source baseline, the plan parser recognizes `skill` and `agent_runtime`, and `container.run_job` has dedicated canonical integration contracts. Recognition, registration, or a directly callable Activity does not establish complete normal-plan dispatch. The old document simultaneously described agent-only dispatch and generic tool execution. Those statements must not be used as a blanket completion claim. #973 owns verification and completion of the actual admitted tool path, preserving working specialized routes rather than rebuilding them.

## 2) Design principles

Use one existing owner for each decision or effect. Plans are data, not executable code. Keep orchestration deterministic, side effects at trusted boundaries, and large payloads in artifacts. Preserve accepted results and retry only unfinished work.

Validate required interfaces, input/output contracts, and authority. Artifact digests establish identity and integrity, not a universal requirement that API, worker, host, and tool build strings match. No replacement compatibility fingerprint, whole-platform qualification matrix, or mandatory model review is needed for a narrow deterministic operation.

One operator can run concurrent workflows and use multiple accounts. Operator admission and scoped machine/resource permissions replace human tenancy, not enforcement. A registry entry cannot grant arbitrary infrastructure or credential access.

## 3) Artifact reference contract

### 3.1 ArtifactRef (canonical)

Use the existing artifact reference and result schemas. References are opaque to ordinary callers and carry or resolve the required identity, content type, size, and provenance. Historical forms remain interpreted by their actual readers. This document does not introduce a new URI format or require every tool to implement its own artifact adapter.

### 3.2 Rules

Committed content is immutable. Large plans, input/output bodies, logs, patches, and transcripts stay outside Workflow history. Read through the authorized artifact owner with bounded size, scope, and required digest/completeness checks. A reference or safe preview is not authority for raw restore or publication. A pathname, incomplete upload, or fabricated digest is not a durable result.

Use existing capture/finalization to preserve required content before destructive cleanup. Optional reporting failure cannot erase committed output. A source without Git can still produce saved artifacts; requested remote publication has its separate authority and result under [Workflow Publishing](WorkflowPublishing.md) and [Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md).

## 4) Tool contract

### 4.1 Definition

A ToolDefinition declares a trusted named operation, input/output schemas, execution binding, capability requirements, and bounded policies. It is not another Workflow or an instruction Skill. Keep actual handler/routing information in the existing registry and composition, not mirrored in prose or reconstructed by the browser.

| Invocation | Required execution ownership |
| --- | --- |
| Agent-runtime work | Existing AgentRun, selected runtime/Profile, and session lifecycle. |
| Supported ordinary deterministic tool | Existing declared Activity/dispatcher, without an unnecessary AgentRun or model lease. General product wiring must be demonstrated, not inferred from registration. |
| `container.run_job` | Existing canonical container-job submission, status, cancellation, and durable wait. No nested Docker or second job supervisor. |

Container jobs retain parent-injected workflow/run/step ownership, trusted submission, bounded status Activities and Workflow timers, and idempotent cancellation. Compact results contain actual job identity, state/exit or failure classification, and log/artifact references. A cancellation request is not confirmed process shutdown.

Legacy raw-Docker, helper, integration-CI, Unreal, and workload aliases must not reappear as alternative new tool APIs. Keep already-recorded Activity implementations only where real histories need them. Remove obsolete registrations and compatibility with their final consumer rather than building a permanent retirement catalog.

### 4.2 ToolDefinition schema (registry entry)

The supplying executable model owns exact serialization. Retain the tool name, input/output schemas, declared executor binding, required capabilities, and bounded timeout/retry policies. An authored override can narrow or choose within allowed policy, never add credentials, arbitrary commands, import paths, privileged mounts, endpoints, or task queues.

A single general dispatcher is not a reason to broaden every worker's privileges. Reuse existing curated bindings when actual isolation or credential needs require them. Do not add a worker fleet for every new tool or human `user/admin` roles merely because the earlier example included them.

### 4.3 ToolInvocation schema

A compiled node retains its stable `id`, display-safe title, existing `tool` reference, small inputs, and permitted options. Names select declared operations; registry snapshot evidence identifies the contract used. New semantic tool-version fields and parallel request envelopes are not required.

Inputs may contain authorized artifact references and existing upstream-result references. Validate supplied values against the actual selected definition. Preserve explicit false, zero, empty, and omitted meanings where supported. The existing parser owns historical aliases and rejects conflicting new input. No silent fallback to another operation or credential source.

### 4.3.1 Runtime selection for `agent_runtime` steps

The existing `inputs.runtimeSelection` distinguishes `kind = "agent_skill"` from `kind = "runtime_command"`, with name and small validated arguments. This selection belongs only to agent-runtime work. Native commands use their runtime capability boundary and normal AgentRunResult/artifact handling, not the deterministic tool registry.

Normal authoring retains Runtime and one Profile. Internal harness, Host Class, materializer, and realizer details are not additional required controls. Preserve meaningful model, cost/privacy, source, and publication choices. Do not hardcode direct Codex as the universal example/default or reinterpret an admitted session after a catalog refresh.

Retained `selectedSkill`/`selectedSkillArgs` decoding belongs at its existing ingress/history boundary until actual consumers are gone. Fresh destination admission and live-session continuation remain distinct. Removing old field handling requires the relevant consumer/replay disposition, not an arbitrary release-count rule.

### 4.4 ToolResult schema

Keep the existing typed status, small outputs, and output-artifact references. A parsed result is not necessarily success. The actual owner distinguishes completed effect, failed operation, uncertain delivery, and pending external work using the providing contract.

Validate outputs before making them available to dependents. Preserve confirmed tool output through later progress, preview, verification, or publication failure. Do not rerun a completed operation to reconstruct an optional report or relabel failed compute as successful because saving worked.

### 4.5 Error model (ToolFailure)

Reuse the existing error taxonomy and original cause. Invalid input, denied authority, conflict, unavailable observation, throttling, timeout, cancellation, and actual external failure must not collapse into a generic retry. Keep bounded diagnostic references rather than raw logs or sensitive payloads in Workflow history.

Derive retry bounds from the admitted tool policy once. A normally returned failure object is not an exception automatically retried by an Activity RetryPolicy. Normalize that result at the existing consumer or Activity boundary, without overlapping whole-operation loops. A non-idempotent tool needs its existing effect reconciliation before another attempt. Unknown delivery is not proof that nothing happened.

## 5) Executable tool registry spec

### 5.1 Declaration format

Reuse the current registry loader, supplying schemas, and focused behavior tests. Tool definitions are trusted deployment input, not arbitrary workflow-authored Python, shell, or plugin code. Do not build another dynamic registry or repeat a full inventory check at every layer.

### 5.2 Discovery model (v1: static)

The existing static snapshot records selected definitions. Preserve its digest and original bytes where required for plan interpretation. Shipping a definition does not prove an appropriate handler is registered, available, or authorized. Discovery and temporary capacity are different facts.

### 5.3 Worker capability model

Resolve the declared binding through current routing and worker capability policy. Validate actual necessary dependencies and credentials at their owner. One queue name does not establish process isolation, and a running worker is not proof that every tool is supported. No second scheduler or credential-discovery loop belongs here.

### 5.4 Story Output Tools

Story breakdown and instruction interpretation remain with the existing portable Skills. A structured deterministic output tool may consume an already resolved target and bounded story inputs through its actual integration adapter. Its definition alone does not prove admission through the normal plan path.

Reuse existing story handoffs under `artifacts/story-breakdowns/`, doc-index/slice artifacts, and Source Packets where those producing features require them. Derived `doc-slices.json` and `implementation-packets.json` are temporary execution inputs, not canonical design documents. They contain references and bounded metadata rather than document bodies in Workflow history. Do not introduce a mandatory doc-indexing pipeline for a fully specified tool request.

The `story.create_jira_issues` and `story.create_github_issues` owners retain target resolution, source traceability, returned mappings, and partial-write reconciliation. Prefer inline bounded input or authorized artifact references to a protected branch push merely for handoff. Repository fallback reads stay explicitly scoped. A new plan must still use a genuinely supported dispatch path.

Preserve provider-neutral `issueCreation` semantics and the providing legacy `jiraCreation` decoder. Already implemented work is skipped; partial work preserves original traceability and describes remaining work. A routine evidence/tooling gap uses the existing authorized automated continuation rather than becoming a human-only review request. Genuine approval or unresolved authority is not silently waived. Historical `manual_review` records keep their meaning without becoming the default for new missing-evidence cases.

Canonical document sources retain `sourceReference.path` and real stable `claimIds`. Generated extraction `coverageIds` are run-local and must not be promoted into fabricated canonical IDs. Imperative or pasted input need not invent a source-file path. Explicit traceability policy is enforced through the supplying schema, not imposed on every workflow.

Tracker success, saved output, and Git publication are distinct effects. A requested fallback must already be authorized; a tracker outage cannot silently choose a new publication destination. Reconcile existing issue mappings before retrying a batch so accepted issues are not duplicated. GitHub issue dependency claims require actual established provider relationships. Downstream MoonMind workflow dependencies are different and use their existing preset/admission owner.

## 6) Plan contract

### 6.1 Definition

A Plan is the existing flattened executable graph, not a nested preset tree. Resolve authoring includes before persistence. Each admitted node has an executable operation, inputs, dependency edges, and applicable policy. Optional source provenance explains origin but does not select a different tool, widen permissions, or change dependencies.

### 6.2 Plan schema (DAG-first)

The current model owns `plan_version`, metadata, policy, nodes, and edges. Reuse its stable node IDs and registry/artifact references. This document is not authority to create a new Plan version or copy its full schema into another validator.

Examples in the historical specification illustrate contracts, not guaranteed runnable tools. In particular, `repo.apply_patch`, `repo.run_tests`, or `plan.generate` examples do not establish current registry/dispatch support. Implementation tests must choose a real supported operation.

### 6.3 Dependency semantics

Dependencies determine readiness and must agree with the Step Execution ledger. A dependent consumes successful required predecessor results, not a merely terminal or partially saved predecessor. Independent branches can continue only under the existing admitted failure policy. Preset origin, optional provenance, or display ordering does not create an implicit dependency.

### 6.4 Data references between nodes

Use the existing `ref.node` and `json_pointer` form where supported. Resolve it from recorded upstream outputs and validate the receiving input. Missing nodes, inaccessible artifacts, invalid pointers, or incompatible values are explicit errors, not empty success or guessed defaults. Read large content through Activities outside Workflow history.

### 6.5 Concurrency

Use the existing plan concurrency bound together with actual worker, provider, and resource limits. Deterministic tools do not consume a model lease unless they actually invoke that provider capability. Do not add a plan-specific global capacity service or assume per-worker settings cap every queued operation.

### 6.6 Failure policy (v1)

`FAIL_FAST` stops new dependent work and requests cancellation through existing owners. It cannot undo already committed effects or prove that external consumers stopped. `CONTINUE` allows eligible independent branches and reports failures; it does not run dependents with missing required outputs. Preserve useful results and remaining cleanup obligations in either case.

Conditional edges are not introduced by this cleanup. Unknown conditional fields must not be silently ignored to run unauthorized work. No new condition-node engine, plan-segment service, or failure-policy vocabulary is required.

## 7) Plan production

### 7.1 Planning is expressed as an executable tool

This retained heading describes one supported production mechanism, not a requirement that every already-authored plan call a planner or model. Manual/structured input and existing preset expansion use the same compiler. Where planning needs an agent or tool, it uses the existing admitted path rather than a new planning service.

### 7.2 Plans are artifacts

Persist validated execution inputs and plan references through the existing artifact owner. Preserve original intent and source identity. A retry reads the recorded plan rather than expanding a changed live preset and calling it the same attempt.

## 8) Determinism boundaries

### 8.1 Workflow code responsibilities (deterministic)

The existing root Workflow schedules ready work, handles recorded results, enforces admitted bounds, and maintains compact state. No direct filesystem/network calls, live registry fetches, or model decisions belong in Workflow code. Avoid a parallel executor around that root.

### 8.2 Activity responsibilities (nondeterministic allowed)

Trusted Activities resolve/materialize artifacts, invoke declared operations, and observe external effects. Their side effects remain scoped and retry-aware. Agent Skill materialization remains a separate existing responsibility, not a reason to turn every tool into agent execution.

## 9) Execution semantics (Plan → Activity invocations)

### 9.1 The Plan Executor (workflow algorithm)

Use the existing normalization, validation, readiness, dispatch, Step Execution, result, and finalization path. Complete only missing handoffs. The conceptual sequence is load/validate, select ready nodes within bounds, invoke their owner, record validated results, and apply the failure policy. It is not a new interpreter implementation checklist.

General deterministic-tool support requires a real normal-plan integration test. Keep the working agent and canonical container-job paths intact. A helper test or document marked Implemented cannot substitute for that evidence.

### 9.2 Mapping a node to an Activity invocation

Resolve the declared ToolDefinition and permitted execution binding, then pass small validated inputs, references, correlation/operation identity, and bounded options. Agent-runtime work instead uses the existing AgentExecutionRequest and separate instruction/context resolution. Special external jobs retain their actual submit/status/cancel owner rather than being squeezed into an unbounded blocking Activity.

Persist/reconcile operation identity before repeating effects. Reuse recorded outputs for already completed nodes. Match external result identity before advancing dependencies or releasing resources. Publication-only recovery belongs to the publisher, not another agent run or a blanket replay of the graph.

## 10) Progress and intermediate outputs

### 10.1 Progress model (v1)

The [Step Ledger and Progress Model](../Temporal/StepLedgerAndProgressModel.md) owns execution-detail state. The existing typed AgentRun progress schema owns compact child observations. Do not add another progress/result store or generic parent-metadata writer to make deterministic tools visible.

### 10.2 How progress is exposed (v1)

Use existing Workflow queries and authorized API/projection surfaces. Bind child updates to actual parent, step, attempt/generation, and revision. Reject stale or foreign observations. Legitimate wait-to-running transitions follow the real lifecycle, not an assumed rank of every status name. A progress update cannot reopen terminal work, grant authority, or overwrite a replacement attempt.

Delivery and projection repair stay bounded and observational. A reporting outage cannot cause repeated successful compute. Optional progress artifacts are not a second mandatory source of truth or a new per-poll write requirement. Keep original errors available without an LLM or live chat.

### 10.3 Intermediate outputs

Keep small results in recorded interpreter state and large content in authorized artifacts. A partial stream is not a committed final result. Intermediate refs grant no extra read rights. Preserve confirmed output when a later summary, review, or remote publication fails.

## 11) Validation rules (authoritative)

### 11.1 Executable tool registry validation

The existing validator checks name uniqueness, schemas, declared bindings/capabilities, and policy bounds. Validate actual behavior at supplying/consuming boundaries rather than duplicate the same schema check in a series of services. Registration remains separate from real execution support.

### 11.2 Plan validation (v1 rules)

Check supported schema, unique node IDs, valid acyclic edges, resolved includes, referenced tool definitions, bounded inputs/options, and valid output dependencies. Optional provenance may be absent. Supplied provenance remains bounded metadata and must not masquerade as execution authority. Failure preserves useful diagnostics without a model-based permission decision.

### 11.3 Execution invariants

Consume the flattened recorded graph and declared policy. Do not re-expand live presets, infer runtime choice from a label, or use optional provenance to change execution. No artifact, progress summary, comparison score, or review verdict can authorize additional tools or publication.

## 12) Open questions — resolved with recommended solutions

### Q1) Do we pin Plans to a tool registry snapshot or resolve “latest” tools at runtime?

Preserve the selected definition snapshot and original serialized intent where the current contract requires it. Its digest identifies the definition used. It does not require all deployed components to have equal build IDs or create another rollout policy. A fresh admitted operation may use a compatible installed implementation under the existing compiler, without rewriting the old plan. Incompatible schemas, corrupted artifacts, or missing required capabilities remain actionable failures.

### Q2) Do we allow conditional edges in v1?

No new conditional semantics are introduced here. Keep the existing dependency behavior. A future actual requirement belongs in a deliberately scoped change, not speculative reserved fields or a new branching framework built to complete #973.

### Q3) How much validation happens in workflow code vs in an Activity?

Keep cheap deterministic structural checks in the existing Workflow/compiler where appropriate. External reads and required deep validation use the current Activity boundary. Reuse `plan.validate` where it is the actual owner rather than add a second mandatory validation workflow or independently repeat every check at each layer. Changing a validator does not automatically make changed recorded decisions replay-compatible.

### Q4) Should `mm.tool.execute` / `mm.skill.execute` be the only Activity Type, or should there be per-tool activity types?

Reuse the existing dispatcher and curated bindings. The registry declares the route; the interpreter does not guess from names. Keep a distinct binding only where a real protocol, credential, or isolation need justifies it. Existing legacy Activity names remain solely for their actual history consumers. No new universal executor or per-tool worker fleet is required.

## 13) Deliverables (this doc’s outputs)

The deliverable is a consistent contract used by existing code, not a second collection of schemas, catalogs, or mandatory status reports. #973 covers the missing real tool-plan handoff, #1088 progress, #2615 optional workspace admission, and #1090 publication. Their implementation evidence stays with the existing issues and tests.

Optional configuration comparison and quality/risk assessment under #2215 and #983 reuse ordinary admitted runs, artifact readers, and reports. They do not create an experiment platform, authorize new inference from a read request, or become default gates on deterministic tools. A model's advisory assessment cannot replace required tests, security enforcement, or explicit approval.

## 14) Engineering backlog

Inspect current code and existing PRs before adding a component named in the historical backlog. The registry, plan model, dispatcher bindings, progress schema, and container-job path already have owners. Extend or remove a proven gap, not a duplicate implementation. An evidence-backed no-code disposition is valid where the requested behavior already works.

Use focused development tests and existing broader GitHub Actions. A mixed deterministic-tool/agent journey should prove actual dispatch, output dependencies, least privilege, bounded failure/cancellation, and preserved results without unnecessary model calls. Add only missing integration and replay cases. Do not unit-test document wording, headings, counts, or metadata, or require a full local suite before opening a PR.

Missing local tools use the existing authorized CI/container continuation path. Preserve the candidate, accepted work, concrete gap, original error, and consumed budget. Do not replace that handoff with mandatory human-only review or claim a continuation was scheduled unless its owner accepted it. This document authorizes no live provider spending, production mutation, or weakened execution safeguards.

## Objective acceptance evidence

The portable `moonspec-verify` bundle owns the [acceptance policy](../../.agents/skills/moonspec-verify/references/acceptance-policy.md). Native hosts validate its declared terminal evidence and perform authorized reads/effects; they do not independently classify requirements.

The existing `StepGateResult.validatedRefs` carries `acceptance` with schema
`acceptance/v1`. The typed supplying adapter is
`moonmind/workflows/skills/acceptance_contract.py`. It binds the candidate repository,
revision and content/checkpoint digest; complete original scope and source digest;
mandatory requirement IDs and objective evidence refs; required freshness policy;
and intended completion target ref, revision and content digest. A dirty workspace
cannot be identified by HEAD alone. Missing or malformed bindings withhold new
issue-flow objective success and return the existing gate contract-repair handoff.

The immutable initial assessment remains distinct from later objective verification.
A later success never rewrites its verdict or implies that the candidate was landed
at assessment time. Status finalization validates current target identity through
the owning repository reader. The default is the remote default branch; an explicit
`completion_target_ref` such as `refs/heads/release` selects an alternative without changing the comparison base
or publication branch.

Temporal histories before `run-acceptance-evidence-v1` replay their recorded gate
routing and initial-assessment projections. Legacy gate artifacts remain readable
with their serialized meaning; absence of objective evidence grants no new
completion authority at a current issue-finalization boundary. Existing remediation,
checkpoint, publication, and post-action verification owners retain their roles.
