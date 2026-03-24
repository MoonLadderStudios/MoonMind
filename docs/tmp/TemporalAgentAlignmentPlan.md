# Final recommendation report

## Executive summary

MoonMind should **adjust its framing and a few architectural boundaries, but not radically rewrite its desired state**.

The repo docs already define a largely idiomatic Temporal target: Workflow Executions as the durable orchestration primitive, Activities for all side effects, Temporal Visibility as the source for Temporal-managed list/query/count, artifact-by-reference payload discipline, and Signals/Updates for runtime interaction  . The docs also already establish the right child-workflow pattern for true agent runtimes: `tool.type = "agent_runtime"` dispatches to `MoonMind.AgentRun`, while normal tools execute as Activities  .

So the main gap is **not** “MoonMind is non-Temporal.”
The main gap is this:

**MoonMind still risks sounding and occasionally structuring itself like a platform with its own orchestration substrate that happens to use Temporal underneath, rather than a platform whose execution substrate is Temporal itself.**

That is the core point the Gemini analysis gets right.

## What MoonMind should keep

These parts are already well aligned and should remain foundational:

1. **Few root workflow types.**
   Keeping `MoonMind.Run` and `MoonMind.ManifestIngest` as the main roots is a good Temporal shape; the docs are right to avoid provider-specific root workflow taxonomies .

2. **`MoonMind.AgentRun` as a child workflow for true agent runtimes.**
   This is the correct Temporal move for long-lived managed/external agents. The docs explicitly separate agent runtimes from one-shot LLM calls and avoid treating them as one giant blocking Activity .

3. **Activities as the only side-effect boundary.**
   The worker topology and activity catalog are already framed the right way: tools, LLM calls, sandbox work, integrations, and artifact I/O all live in Activities, with task queues treated as routing plumbing only .

4. **Artifacts by reference.**
   MoonMind’s payload discipline is strongly aligned with Temporal best practice and should remain non-negotiable  .

5. **Signals/Updates/Queries as the interaction model.**
   The docs are already moving approvals, edits, pause/resume, and external events into Temporal-native interaction patterns  .

## Main gaps to close

### 1. Make Temporal the execution substrate in the language, not just in the runtime

The current docs still present too much of the system as a **migration from tasks/queues into Temporal-managed flows**, which is honest, but it leaves the end-state underemphasized. `TemporalArchitecture.md` is explicitly a bridge document, which is useful, but the project needs a crisper final stance: Temporal is not just the scheduler for workflow-driven automation; it is the durable execution substrate for MoonMind’s orchestration model .

**Recommendation:** add one short top-level architecture statement, repeated consistently across docs:

> MoonMind execution is Temporal-native. MoonMind adds domain contracts above Temporal—Tool, Plan, Artifact, Agent Adapter—but does not introduce a parallel orchestration substrate.

That single sentence would resolve a lot of ambiguity.

### 2. Narrow the role of “Plan” so it is clearly data, not a competing orchestration engine

The docs say the right things: plans are data, workflows orchestrate, and plans are interpreted deterministically by workflow code . But the Gemini critique is still useful here: language like “plan interpreter,” “dispatcher,” or “supervisor” can make MoonMind sound like it is rebuilding a custom orchestration layer above Temporal.

This is mostly a **naming and boundary problem**, not necessarily a logic problem.

**Recommendation:**

* Keep **Plan** as a first-class MoonMind concept.
* Treat it explicitly as an **execution spec artifact** or **workflow input graph**.
* Ensure the “interpreter” is just workflow code in `MoonMind.Run`, not a portable engine with its own hidden runtime semantics.

I would strongly consider renaming “Plan Interpreter” to **Plan Executor** or **Plan Driver**. “Interpreter” implies a shadow orchestrator. “Executor” makes it clearer that the Workflow is still the durable owner.

### 3. Add an explicit first-class model for workflow-native agents, not only delegated agents

This is the biggest architectural addition I would make.

Right now, the desired-state docs are strongest on:

* plan-driven execution, and
* delegated/managed agent runtimes via `MoonMind.AgentRun` child workflows  .

That is good, but Temporal’s strongest AI-foundation story is also about a **workflow-native reasoning loop**: tool-call, observe, decide, continue.

MoonMind should not replace its delegated agent architecture with that model. But it **should add it as an official execution shape**.

**Recommendation:** define two sanctioned agent execution modes:

1. **Workflow-native agentic loop**
   The reasoning loop lives in workflow code; each model/tool interaction is an Activity.

2. **Delegated agent runtime**
   The agent runs outside the workflow as a managed or external runtime; `MoonMind.AgentRun` owns its durable lifecycle.

That gives MoonMind a much stronger Temporal story:

* native loop when MoonMind owns the cognition loop,
* delegated child workflow when MoonMind orchestrates an external or CLI agent.

That is cleaner than trying to force everything into one model.

### 4. Make Queries a first-class live inspection mechanism, not just an optional detail

The docs mention progress Query patterns and Visibility well, but the overall desired state still leans heavily on projections and task-style views  .

That is fine for lists and history. It is weaker for **live run introspection**.

**Recommendation:** formalize this split:

* **Visibility + projections** for list pages, counts, filtering, history, dashboards.
* **Queries** for live execution detail, current progress, bounded working memory, awaiting reason, active step, and current intervention point.

That will align MoonMind better with Temporal’s strengths and reduce temptation to over-read from Postgres projections.

### 5. Be stricter about what is an adapter concern vs a workflow concern

The external and managed agent docs are generally good here. `ExternalAgentIntegrationSystem.md` correctly places provider transport behind adapters and gives `MoonMind.AgentRun` ownership of the lifecycle . `ManagedAndExternalAgentExecutionModel.md` also correctly keeps long-lived agent runtimes out of one-shot LLM Activity semantics .

The remaining risk is not the high-level design; it is drift in implementation and naming.

**Recommendation:** institutionalize one rule:

> Adapters translate provider/runtime semantics. Workflows own lifecycle semantics.

That means:

* adapters may normalize states,
* adapters may launch/start/status/fetch/cancel,
* adapters may expose capability descriptors,
* but only workflows decide phase progression, waiting, retries-at-the-orchestration-level, HITL transitions, and durable lifecycle state.

### 6. Tighten the project’s internal vocabulary to reduce Temporal collisions

The docs are already moving from Skill to Tool, which is a good change . I would continue that cleanup.

## Naming recommendations

### Keep

* **task** as the public top-level product term during migration.
* **workflow execution** as the internal Temporal term.
* **Tool** as the canonical executable capability term.
* **Artifact** as the canonical large payload term.
* **AgentRun** for true delegated agent lifecycle.

### Change

* **Plan Interpreter** → **Plan Executor** or **Plan Driver**
* **Review Gate** → **Approval Policy** or **HITL Policy**
* **Pause/Resume** docs that sound worker-centric → distinguish **workflow pause** from **fleet quiesce/drain**
* Any user-visible **queue** wording that implies ordering semantics

### Avoid

* Treating **Task Queue** as a product queue
* Using **Task** internally when you really mean Temporal workflow execution or activity task
* Reintroducing “dispatcher/supervisor” language for logic that is actually just Workflow + Activity behavior

## Architecture recommendations

### 1. Officially adopt a dual execution model

MoonMind should document three execution shapes clearly:

* **Tool Activity**: ordinary side-effecting work
* **Workflow-native agent loop**: MoonMind owns the reasoning loop
* **Delegated agent runtime (`MoonMind.AgentRun`)**: MoonMind orchestrates an external or managed agent lifecycle

That would be the single most valuable conceptual improvement.

### 2. Make `MoonMind.Run` the only general orchestration owner

This is already close to the desired state. Keep `MoonMind.Run` as the top-level durable owner, and ensure any plan, tool, approval, or agent execution mechanism compiles into:

* inline workflow logic,
* Activities,
* child workflows,
* Signals,
* Updates,
* Queries,
* Timers,
* Continue-As-New.

Not into a second orchestration substrate.

### 3. Preserve `MoonMind.AgentRun`, but sharpen its contract

Do not remove it. Gemini’s critique would be a mistake if interpreted as “everything should become a native while loop.”

For MoonMind’s product, `MoonMind.AgentRun` is correct and should stay. But define it more sharply as:

> The durable lifecycle wrapper for delegated cognition.

That makes clear it is not the model for every agent, only for the ones MoonMind does not execute stepwise inside workflow logic.

### 4. Treat plan compilation and validation as preprocessing, not runtime sovereignty

`plan.generate` and `plan.validate` are fine as Activities  . The important part is to keep the workflow in charge after validation.

A good mental model is:

* **plan generation** creates candidate structure,
* **plan validation** proves it is safe/consistent,
* **workflow execution** remains the real runtime.

### 5. Formalize “read models are never execution truth”

You are already very close here. I would make it explicit in every relevant doc:

* workflow state/history + artifacts = execution truth
* Visibility = indexed execution query plane
* projections = UI/read optimization
* projections must never be read by workflows to decide what to do next

That would close one of the biggest possible anti-Temporal failure modes.

## Concrete keep / change / delete guidance

### Keep

* Small workflow type catalog
* Minimal task queue topology
* Artifact-ref discipline
* Child workflow model for agent runtimes
* Adapter pattern for external providers
* Visibility-backed list/filter model
* Continue-As-New posture

### Change

* Add workflow-native agent loop as an explicit first-class pattern
* Rename “interpreter/gate” language to more Temporal-native terms
* Elevate Query-based live inspection in the docs
* Tighten the boundary between workflow lifecycle semantics and adapter transport semantics

### Delete or actively prevent

* Any custom runtime layer that independently decides retries/waits/phase transitions outside the workflow
* Any product/UI language that treats Temporal task queues as customer-visible queues
* Any future design that lets projections or DB rows become execution truth
* Any trend toward burying multiple tool calls or reasoning steps inside one opaque long-running Activity when MoonMind could own the loop directly

## Bottom line

MoonMind does **not** need a wholesale redesign to align with Temporal as an AI agent foundation.

It needs three things:

1. **A clearer end-state statement that Temporal is the execution substrate, not just the scheduler.**
2. **A cleaner vocabulary that stops shadowing Temporal with “interpreter/dispatcher/gate/queue” language.**
3. **An explicit dual-mode agent model: workflow-native agents when MoonMind owns the loop, and `MoonMind.AgentRun` child workflows when MoonMind delegates to managed/external runtimes.**

If MoonMind makes those adjustments, it will be much more obviously aligned with idiomatic Temporal while still preserving what makes it distinct: it is not just an agent runtime, but a durable orchestration and control plane for many kinds of agent execution.
