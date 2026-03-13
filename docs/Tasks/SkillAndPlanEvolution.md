# Skill & Plan Design Evolution (MoonMind & Temporal)

**Executive Summary:** The MoonMind orchestrator uses **“skills”** and **“plans”** atop Temporal to manage agent tasks【61†L261-L270】. A *Skill* is a named capability with input/output schemas, execution bindings, retries, etc. (not a workflow)【61†L343-L352】. A *Plan* is a DAG of Skill invocations with explicit dependencies and policies【12†L531-L540】. This design leverages Temporal’s deterministic workflows (orchestration) vs activities (side-effects)【61†L263-L272】. We recommend tightening JSON-schema contracts, enforcing idempotency/retries, and enhancing observability (queries/metrics), while aligning terminology with analogous frameworks. For example, citing Temporal best practices: workflows orchestrate (deterministic), activities do I/O (LLM calls, APIs)【61†L263-L272】【36†L93-L102】. We also compare alternative names (“capability”, “action”, “operator”, etc.) and outline a migration path (version pinning, compatibility). Diagrams illustrate the plan/workflow execution. 

## Current MoonMind Skill & Plan Contracts

MoonMind’s **SkillDefinition** (in a registry) includes `name`, `version`, JSON schemas for inputs/outputs, executor binding, capability requirements, and default policies (timeouts, retries)【61†L379-L388】. For example, a skill YAML has fields for `inputs.schema`, `outputs.schema`, `executor.activity_type`, `requirements.capabilities`, and `policies.{timeouts,retries}`【61†L379-L388】【61†L390-L399】. A SkillInvocation in a plan is a JSON node with a unique `id`, skill name+version, inputs, and optional overrides【61†L410-L422】. The **Plan** format is a JSON object with a DAG: an array of `nodes` (each a SkillInvocation) and `edges` listing dependencies【12†L531-L540】【12†L557-L560】.  Example edges `[{ "from": "n1", "to": "n2" }]` mean “n2 may start only after n1 succeeds”【12†L557-L560】.  Large inputs/outputs (plans, artifacts, transcripts) are stored outside the workflow; workflows and activities carry only opaque `artifact_ref`s【61†L318-L327】【61†L332-L335】.  MoonMind emphasizes **determinism boundaries**: workflow code is purely orchestration (no nondeterministic calls)【61†L288-L296】, while skills (activities) do all external I/O【61†L288-L296】.  Progress is tracked via structured counters (nodes pending/running/succeeded/failed) and exposed by Temporal queries and optional progress artifacts【13†L683-L692】【13†L699-L704】.

### Observed Gaps and Characteristics

- **Versioning & Pinning:** Plans carry a `registry_snapshot` (digest + artifact) to pin skill versions【12†L531-L540】. This ensures reproducibility (plan re-runs see the same skill definitions)【13†L745-L754】.  
- **Error Model:** Skills return a structured `SkillResult` with status, small JSON outputs, list of `output_artifacts`, and optional progress info【12†L441-L449】. Failures use a standard schema (`error_code`, `message`, `retryable`, etc.)【12†L468-L476】, driving retry logic (e.g. `non_retryable_error_codes` in the skill def)【12†L481-L489】【12†L494-L499】.
- **Policies & Retries:** Default activity timeouts/retries come from SkillDefinition, with overrides allowed within safe bounds【61†L412-L421】. Retry is “at least once” by default (Temporal will retry until success)【58†L139-L142】, so **Activities must be idempotent or side-effects safe**.

Overall, the current spec is thorough. However, implementation is greenfield: we must enforce these contracts in code. For example, a “plan.validate” activity should enforce JSON-schema and DAG invariants【13†L727-L735】.  Worker fleets must honor `requirements.capabilities` to route skills to the right task queue【12†L523-L531】. Telemetry (StatsD, logs) is mentioned in older docs and should be extended to Temporal metrics and search attributes.

## Best Practices (Temporal + Agent Orchestration)

**Deterministic Orchestration:** Temporal requires deterministic workflow logic; it excels at *orchestration* and state durability【36†L93-L102】. As MoonMind’s doc states, “Workflow code orchestrates only. No nondeterministic behavior in workflow code. All external I/O and LLM calls are Activities”【61†L288-L296】.  This aligns perfectly with Temporal guidance: workflows replay reliably using past decisions, while non-deterministic AI calls live in Activities【36†L99-L102】. Thus, skill-invocation and plan execution belong in Activities, while the plan interpreter (in the Workflow) only schedules nodes, updates progress, and applies failure policies. 

**Idempotency & Retries:** By default Temporal will retry an Activity on failure (up to `max_attempts`)【58†L139-L142】. We must design Skills so that **re-running an Activity is safe or idempotent**【58†L139-L142】. For example, writing to a DB should use unique keys or check existing records (idempotency keys)【58†L154-L163】【58†L178-L187】. Activities like `mm.skill.execute` can compute an idempotency key (e.g. `workflowRunId-activityId`) and skip duplicate side-effects【58†L178-L187】. We should make clear in the Skill contract how Activities achieve idempotency (e.g. through transactional design or dedup keys). Non-idempotent logic should raise retriable vs non-retriable errors appropriately, consistent with our `SkillFailure` codes【12†L468-L477】【58†L139-L142】.

**Observability:** Temporal supports *Query* calls and Search Attributes for workflows.  MoonMind’s progress model (nodes pending/succeeded etc.) should be exposed via a Workflow Query (as planned)【13†L699-L704】. We should also register relevant Search Attributes (e.g. plan title, status) so operators can list workflows. Periodic progress snapshots (the `progress.json` artifact) can be supplemented by logs or metrics. We should instrument key events (node started, succeeded, failed) with logs and metrics (e.g. StatsD or OpenTelemetry). Temporal workers can emit metrics (like how many skills executed, failures, latencies), possibly tagged by skill name/version. The design already mentions emitting StatsD and writing artifacts【54†L1-L4】; ensure the new Temporal version includes these.

**Failure Modes & Concurrency:** The plan policy allows `failure_mode: FAIL_FAST` or `CONTINUE`【12†L541-L549】. The interpreter must implement these: e.g. on a node failure, either cancel all (fail-fast) or let independent branches finish (continue). Concurrency (`max_concurrency`) caps parallel skill executions【12†L541-L549】. In code, we can track ready nodes and only schedule up to N. If using the Temporal Python SDK, we might fan-out activities or even spawn child workflows to parallelize branches. The docs even suggest optional patterns like conditional edges (future work)【13†L771-L780】.

**Data Contracts & Validation:** All inputs/outputs use JSON Schema. We must enforce these at runtime: a validation Activity (`plan.validate`) should deeply check that plan nodes, skill parameters, and references are correct【13†L727-L735】. The workflow itself can do quick checks (IDs unique, acyclic, etc.)【13†L727-L735】. Plans and skills should be versioned: e.g. “plan_version”: "1.0" in the plan schema【12†L531-L540】. Future versions can add fields (like conditions) with version checks. The contract should specify that newer fields are ignored in v1.

## Terminology Alternatives

MoonMind currently uses **Skill** and **Plan**. Comparable systems use varied terms (skills, tools, capabilities, actions, tasks). We compare options:

| Term (Skill)   | Pros                                         | Cons                                      |
| -------------- | -------------------------------------------- | ----------------------------------------- |
| **Skill** (current) | Matches Claude/AutoGen terminology. Conveys “capability.” Easy to map to “skill registry” and existing docs【61†L343-L352】. | Some may confuse with human “skill.” |
| **Capability**  | Emphasizes what agent **can do**. Used in crewAI (“tools and capabilities”). Neutral technical term. | Less common in LLM agent literature for modules; “capability” is buzzwordy. |
| **Action**      | Generic and simple. Connotes a function call. | Too generic; clashes with planning “action” semantics (HTN/PDDL). |
| **Operator**    | Suggests an executable operator; could map to CRDT “operator”. Uncommon in LLM context. | Confusing to new users; often means transformation or the company’s name. |
| **Task** (node) | Common term for a unit of work. | “Task” is already used by Temporal (task queues, child workflows). Could confuse “task vs TaskQueue.” |
| **Tool/Action** (like LangChain) | Implies simple API. | MoonMind skills are richer (schemas, versions, etc.) than simple “tools.” 
| **Function**    | Technical, clear. | Feels like coding, not LLM context. Loses AI agent flavor. |
| **Service/Activity** | Overlaps with Temporal terms. | Might confuse users (Temporal “Activities” vs MoonMind “activities”). |

| Term (Plan)     | Pros                                        | Cons                                      |
| --------------- | ------------------------------------------- | ----------------------------------------- |
| **Plan** (current) | Reflects agent planning (SoK: “Plan is a reasoning artifact”【42†L282-L287】). Neutral. | “Plan” can be broad; might overlap with “workflow” notion. |
| **Workflow**    | In Temporal context, a *Workflow* is the orchestrator. Calling user-specified plan a “workflow” may confuse with Temporal’s use. | Overlap, risk of confusion. |
| **Recipe/Process** | Conveys a set of steps; intuitive metaphor. | Less standard in AI agents; might sound high-level/UX. |
| **Pipeline**    | Suggests data flow; used in ETL contexts. | Implies linear flow; not obviously DAG metadata. |
| **TaskGraph**   | Explicitly hints at DAG of tasks. | Clunky; not common jargon in AI. |
| **Blueprint**   | Nice metaphor (plan blueprint). | Non-technical, might confuse technical readers. |
| **Specification** | Formal, implies structure. | Abstract; “spec” is generic. |
| **Policy**      | Misleading: usually means rules, not sequence. | Too different semantic. |

**Recommendation:** Retain **Skill** (capability) and **Plan** for consistency with the design doc and common usage (SoK and Claude use “skills”)【42†L282-L287】【61†L343-L352】. However, define them clearly (e.g. “skill = callable capability module”). We can alias internally if needed (e.g. variable names: `SkillDef`, `PlanSpec`). If rebranding, “Capability” and “WorkflowSpec” are alternatives, but risk confusion. Whichever terms are chosen, use them consistently in docs/code.

## Recommendations & Migration Strategy

- **Schema & Contracts:** Implement strict JSON-schema validation for Skills and Plans. Use a central **Skill Registry loader** that validates required fields (name, version, I/O schemas, executor, policies)【61†L400-L407】. On startup, register a digest of the skill set for snapshot pinning. Store plans as versioned artifacts. Use Temporal search attributes (indexed fields) for plan metadata (title, creation time) and skill IDs for easier monitoring.  

- **Plan Execution (Workflow):** Write the Plan Interpreter as a Temporal Workflow (`MoonMind.Run`). Pseudocode: 

  ```python
  @workflow.defn
  async def RunPlanWorkflow(ctx, plan_ref: ArtifactRef):  # iterates over ready Steps and calls ToolDefinitions.
      plan = await activities.load_plan(plan_ref)           # Activity reads/validates plan artifact
      validate_plan_structure(plan)                        # cheap checks in workflow
      ready = find_ready_nodes(plan)
      running = {}  # node_id -> Future
      while not all_nodes_done(plan):
          for node in ready:
              # schedule up to max_concurrency
              if len(running) < plan.policy.max_concurrency:
                  # schedule skill activity
                  running[node.id] = workflow.execute_activity(
                      skill_dispatcher,
                      node.skill.name, node.skill.version, node.inputs,
                      timeouts=node.options.timeouts_override,
                      retry_options=node.options.retries_override,
                  )
          # wait for first completion (or poll)
          done, _ = await workflow.wait_any(running.values())
          for node_id, fut in running.items():
              if fut in done:
                  result = await fut
                  store_result(node_id, result)       # small outputs in state, large in artifacts
                  update_progress_metrics(node_id, result)
                  running.pop(node_id)
                  # enqueue dependents if their deps all succeeded
                  for succ in plan.dependents(node_id):
                      if deps_satisfied(succ):
                          ready.add(succ)
                  break
      return summarize_execution() 
  ```
  (A **Dispatcher Activity** `mm.skill.execute` routes to the actual skill implementation based on worker capability).

- **Activity Design:** Each Skill invocation becomes one Activity. The default activity type (`mm.skill.execute`) handles generic invocation: it looks up the `SkillDefinition` (from the snapshot), validates inputs, performs the operation (LLM, API call, etc.), writes any artifacts, and returns a `SkillResult` (status, outputs, artifact refs)【12†L441-L450】. For special cases, some skills may bind to custom activity types (e.g. `artifact.read`, `integration.github.call`) for isolation【13†L842-L847】. Activities must catch exceptions and wrap them into our `SkillFailure` format (error code, message) to inform retry policies【12†L468-L477】. Use idempotency keys inside Activities to avoid duplicate side-effects【58†L178-L187】.

- **Concurrency & Failure Policy:** Implement max concurrency by tracking how many nodes are currently running (as above). Enforce **FAIL_FAST** by canceling outstanding activities when one fails (then let Temporal cancel others via `ctx.cancel()`), or **CONTINUE** by letting independent branches finish and collecting failures. On completion, write a final summary artifact (with overall status, any failed node errors) and exit. Use Temporal signals/queries if you want to allow mid-flight cancellation or progress queries (queries are already planned).

- **Observability & Telemetry:** Expose a Workflow Query for progress (using the structured object model【13†L683-L692】). Emit logs/metrics at key points (node start/completion, pipeline end). Map each plan/workflow to identifiable search attributes (e.g. `PlanId`, `PlanStatus`). Use StatsD or OpenTelemetry to push metrics about skills executed, durations, failures (the original code hints at StatsD【54†L1-L4】 – preserve or upgrade to OTEL). Periodically (or on significant events) write a `progress.json` artifact as a durable snapshot【13†L699-L704】.

- **Security & Access:** The SkillDefinition includes `allowed_roles`【61†L395-L398】 – during invocation, check caller identity (if using a User->API->Temporal flow) and forbid unauthorized usage. Artifacts and plan data may contain sensitive info; consider encrypting artifact storage (the doc hints at encryption support【61†L333-L335】). Ensure that Activity workers run in isolated environments if needed (e.g. sandboxed container for risky skills).

- **Versioning & Migration:** Since plans pin a skill registry snapshot【13†L758-L767】, we can evolve skill schemas by introducing new versions without breaking old plans. We should include a version in the plan schema (`plan_version`)【12†L531-L540】 so we can parse old vs new formats. Introduce any new fields (e.g. `edges[].condition`) in a backward-compatible way (ignored in v1)【13†L771-L780】. On deployment, update the Skill registry (new versions) alongside worker code, then update the orchestrator. Existing running workflows will continue using the old registry snapshot for consistency. 

- **Data-driven Agent Patterns:** Frame planning itself as a Skill (e.g. `plan.generate`) that outputs a Plan artifact【12†L552-L556】【12†L585-L590】. This fits the idea that agents use LLMs/algorithms to propose multi-step plans, which are then executed deterministically. Each plan node is like a sub-goal. The SoK on agentic skills notes that *“plans are reasoning artifacts... not directly executable without interpretation”* whereas *skills “persist across sessions... carry executable policies, and expose callable interfaces”*【42†L282-L287】. This matches our model: plans (data) vs skills (executable modules).

## Naming Comparison

| Concept  | MoonMind Term | Alternative           | Pros                                        | Cons                                          |
|----------|---------------|-----------------------|---------------------------------------------|-----------------------------------------------|
| **Skill** (executable unit) | Skill        | Capability            | Emphasizes agent capability; used in AI (Claude, AutoGen). | “Capability” is generic marketing term. |
| |             | Action                | Simple, intuitive.                          | Too generic; conflicts with “action” in planning. |
| |             | Operator              | Conveys execution.                          | Uncommon in LLM agents; might confuse with K8s “Operator”. |
| |             | Function / Endpoint   | Technical clarity.                          | Loses “agentic” flavor; implies code-centric view. |
| |             | Tool                  | Common in LangChain.                        | Suggests simpler API; MoonMind skills have richer schema. |
| **Plan** (sequence/DAG) | Plan         | Workflow / Flow       | Aligns with term “workflow” (implicitly a DAG). | Conflicts with Temporal Workflow (execution instance). |
| |             | Recipe / Pipeline     | Intuitive (series of steps).                | “Pipeline” suggests linear data flow; “Recipe” is non-technical. |
| |             | TaskGraph / DAGSpec   | Explicitly a DAG.                           | Uncommon, technical jargon. |
| |             | Blueprint / Spec      | Implies blueprint of steps.                 | Vague, less recognized.                        |

**Recommendation:** Continue using **“Skill”** for the capability (it matches existing docs and industry usage) and **“Plan”** for the execution graph (as per the SoK and current design)【42†L282-L287】【61†L343-L352】. Introduce alternate labels only if confusion arises; e.g. in user-facing docs, one might say “task” for a plan node, but reserve “Plan” for the overall DAG.

## Implementation Roadmap

1. **Registry & Loader:** Define YAML/JSON schema for SkillDefinitions. Implement a registry loader that validates each entry (using a JSON Schema validator) at build-time and startup【11†L356-L364】. Compute a registry snapshot digest (SHA) and publish it as an artifact for reproducibility【13†L745-L754】.

2. **Validation Activity:** Build an activity `plan.validate(artifactRef, registryDigest)`. It reads the plan, checks structural rules (node IDs unique, edges valid, acyclic) and JSON-schema rules (inputs match skills)【13†L727-L735】. Return either a validated plan reference or a SkillFailure. Workflow should invoke this before execution【13†L810-L818】.

3. **Workflow (Interpreter):** Implement the Plan Interpreter algorithm as a Temporal workflow (`MoonMind.Run`). Use a deterministic loop: compute ready nodes, schedule activities, wait for completions, update state/progress, apply policy【13†L634-L644】【13†L649-L657】. Use `workflow.ExecuteActivity` to call `mm.skill.execute` for each node.

4. **Activity Dispatcher:** In the worker fleet, code `mm.skill.execute(context, skillName, skillVersion, inputs, overrides)` to perform a skill call. It should resolve the `SkillDefinition` (from pinned snapshot), enforce timeouts/retries, route to the correct queue, perform the action (LLM call, shell command, etc.), and collect outputs/artifacts. For example, if `executor.activity_type` is custom (per-skill activity), route accordingly【14†L841-L847】.

5. **Progress & Query:** Maintain counters in workflow state. Implement a Query handler returning `{total_nodes, pending, running, succeeded, failed, last_event, updated_at}`【13†L683-L692】. Optionally write `progress.json` to artifact storage on schedule or completion【13†L699-L704】.

6. **Logging & Metrics:** Add instrumentation: increment a metric per skill start/finish, gauge of running nodes, counters of failures by error_code, etc. Use Temporal interceptors or wrappers. For observability, consider exposing metrics via Prometheus/StatsD and log structured events on the console or an event store.

7. **Security & Secrets:** Integrate a secret manager for any credentials (e.g. integration APIs). Enforce `SkillDefinition.security.allowed_roles` by checking caller identity (if any). Encrypt artifact storage if needed (the design hints at this).

8. **Migration:** Ship v1 of these interfaces and workflows. Document that plans/tasks must conform to v1 schema. When updating (v2), use Temporal’s versioning APIs to evolve workflow logic without breaking in-flight workflows. Provide upgrade scripts or backwards-compatible code so older plans still run (e.g. ignore new fields).

## Diagram: Plan Execution (Mermaid)  
```mermaid
flowchart LR
  subgraph Workflow["MoonMind Workflow (orchestrator)"]
    A[Load Plan Artifact] --> B[Validate Plan (Activity)]
    B --> C{Plan Valid?}
    C -- No --> E[Fail Workflow]
    C -- Yes --> D[Compute Ready Nodes]
    D -->|Node n1 ready| A1[ExecuteActivity: Skill:n1]
    D -->|Node n2 ready| A2[ExecuteActivity: Skill:n2]
    A1 --> F1[SkillResult n1]
    A2 --> F2[SkillResult n2]
    F1 & F2 --> U{Update Results \n & Progress}
    U --> D
    U -->|All done| G[Generate Summary Artifact]
  end

  subgraph Activities["Worker Fleet"]
    A1 -->|calls| X1[(Skill Definition for n1)]
    A2 -->|calls| X2[(Skill Definition for n2)]
    X1 -->|runs| Y1[LLM/API/Tool]
    X2 -->|runs| Y2[LLM/API/Tool]
    Y1 --> Z1[Output Artifacts + JSON]
    Y2 --> Z2[Output Artifacts + JSON]
    Z1 -->|return| F1
    Z2 -->|return| F2
  end
```
This illustrates the workflow reading a plan, validating it, then in rounds scheduling ready nodes (activities). Each activity uses a SkillDefinition to invoke the actual work (LLM or integration), producing a `SkillResult` that the workflow collects.

## Conclusion

MoonMind’s emerging skill-and-plan framework aligns with modern agent orchestration practice: modular skills, data-driven plans, and durable execution via Temporal. By enforcing strong schemas, idempotent activities, and clear interfaces (in code and documentation) we ensure reliability. In particular, adopting Temporal patterns (query for progress, signals for control, versioned workflows) will yield robust, observable agents【36†L93-L102】【58†L139-L142】. The terminology of “skills” and “plans” is consistent with literature【42†L282-L287】, but we should document any synonyms carefully. The migration path is straightforward: implement the above in stages (registry → validate activity → interpreter → dispatcher → progress), test with simple plans (linear, parallel, fail-fast), and expand to full feature set. With these changes, MoonMind will support scalable, maintainable, and secure AI workflows.  

**Sources:** MoonMind docs【61†L263-L272】【61†L343-L352】【61†L412-L421】【12†L531-L540】, Temporal blog/patterns【36†L93-L102】【36†L106-L115】【58†L139-L142】, and agent-skill literature【42†L282-L287】.
