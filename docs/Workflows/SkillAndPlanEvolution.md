# Tool and Plan Execution

**Status:** Implemented execution owners, with supported paths verified at their actual boundaries. The detailed contract is [Execution Tool and Plan Contracts](SkillAndPlanContracts.md); instruction bundles belong to the [Skill System](../Steps/SkillSystem.md).

MoonMind is a single-operator application. A plan coordinates ordinary deterministic operations and agent work through the same Step Execution ledger. It does not need a second dispatcher, a tool-specific scheduler, or human roles.

## Existing owners

| Responsibility | Implementation |
| --- | --- |
| Trusted executable definitions and authority-bearing bindings | `moonmind/workflows/skills/tool_definitions.py` |
| Immutable registry snapshot and digest | `moonmind/workflows/skills/tool_registry.py` |
| Structured workflow-to-plan compilation | `moonmind/workflows/temporal/worker_runtime.py` (`_build_runtime_planner`) |
| Plan artifact admission and validation | `TemporalPlanActivities` and `moonmind/workflows/skills/plan_validation.py` |
| Readiness, dependency results, execution identity and progress | `MoonMindRunWorkflow._run_execution_stage` |
| Deterministic Activity execution | `TemporalSkillActivities.mm_tool_execute` and `ToolActivityDispatcher` |
| Agent lifecycle and provider capacity | `MoonMind.AgentRun` and its selected Provider Profile |
| Durable large inputs and outputs | Existing Temporal artifact services and readers |

An explicit authored `type: tool` Step becomes a plan node with the retained wire discriminator `tool.type: skill`. The name selects a server-owned executable definition and registered handler. It is not an instruction Skill and does not start an AgentRun. An agent Step instead becomes `tool.type: agent_runtime` and uses the existing agent lifecycle.

The Python `SkillDefinition`, `SkillResult`, `SkillFailure`, and related aliases remain compatibility exports of the Tool contracts. They do not create another capability registry or change serialized historical records.

## A supported deterministic operation

`document.discover` is an existing registered deterministic tool. It lists supported document paths in a workspace accessible to its sandbox worker, or uses its existing repository reader when remote discovery is needed. Its input/output schema describes the actual handler, including `documentPaths`, `documentCount`, and the observed source. A local discovery does not need a GitHub connection or a model Profile. A remote read still needs the authority required by that integration.

A normal plan can pass `documentPaths` to a dependent tool or agent with the existing reference form:

```json
{
  "ref": {
    "node": "discover",
    "json_pointer": "/outputs/documentPaths"
  }
}
```

Admission verifies reference identity and dependency reachability. The executor resolves the value from the recorded completed result before dispatch; it does not rerun the producer to reconstruct an output. The registered document-discovery handler validates resolved inputs against its server-owned definition before filesystem or integration reads. Agent inputs continue through their existing AgentRun contract rather than acquiring a synthetic ToolDefinition. Agent Steps with inline instructions receive resolved authored inputs as prompt data, without promoting those values into runtime authority.

Invalid document-discovery input is a non-retryable input failure. A business-failure result retains its own status and diagnostics. The effect owner remains responsible for truthful completed outputs and reconciliation; the dispatcher does not retroactively impose new output-contract rejection on retained registry snapshots.

## Authority and artifacts

Registry artifacts pin schemas and execution policy. Their digest alone does not authorize a different Activity, worker queue, capability, credential, endpoint, or mount. The runtime checks authority-bearing bindings against the trusted definition and checks the admitted machine principal where required. Resource and integration owners retain their scoped checks.

Artifact references are read by the existing authorized artifact service. Plan references select recorded data, not access rights. Large bodies stay outside Workflow history. Deterministic tool work inherits only necessary context; a model selection is not blanket authorization for deterministic operations.

Local filesystem access is the worker's existing sandbox/workspace boundary. This document does not introduce an independent filesystem permission system or promise that an arbitrary path exists on a remote agent host. Workspace-source admission and publication have their own owners.

## Retries, cancellation, and confirmed work

Temporal orchestration remains deterministic. Activities and their effect owners perform I/O. An execution's stable operation identity is passed to the handler; the handler must reconcile uncertain delivery before repeating a mutation. Activity retries are bounded by the admitted policy. A normally returned failure result is not automatically retried by Temporal, and there is no generic exactly-once guarantee for arbitrary handlers.

`container.run_job` retains its dedicated submit/status/cancel lifecycle, stable job identity, and completed-job reconciliation. It must not be wrapped in another container supervisor or replaced with raw Docker work.

Cancellation is a request to the existing effect owner, not proof that a process stopped or that a committed effect was undone. Step results and durable artifacts preserve confirmed work through later progress or reporting failures. Resumption reuses recorded completed steps and their saved references.

## Verification boundary

The focused `test_run_deterministic_tool_refs_973.py` tests cover dependency resolution, preserved results, dispatch shape, and the container-job lifecycle. Registered handler and Activity tests cover schema failures and truthful failure envelopes. The registered-tool journey additionally crosses normal plan production/admission, the real Activity dispatcher, and the `document.discover` handler, including a mixed tool/agent dependency and Temporal worker-replacement/replay evidence.

These checks demonstrate supported plan execution operations. They do not certify every declared tool, external provider, deployment, or authored plan, and they do not establish profile-free creation through the outer HTTP submission API. Live provider access, workspace-source choices, and publication semantics remain with their actual execution owners. Reuse affected CI evidence and targeted tests rather than adding a second qualification matrix or mandatory human signoff.
