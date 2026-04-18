# MM-319 Skill System Story Breakdown

Source design requested by Jira: `docs/Tools/SkillSystem.md`
Original source document reference path for each story: `docs/Tools/SkillSystem.md`
Readable current source documents used for coverage: `docs/Tasks/AgentSkillSystem.md`, `docs/Tools/SkillInjection.md`, `docs/Tasks/SkillAndPlanContracts.md`
Note: `docs/Tools/SkillSystem.md` is not present in the current checkout; the current canonical skill-system docs above were used instead.
Story extraction date: 2026-04-18T00:12:13Z
Requested output mode: `jira`

Coverage gate result:

```text
PASS - every major design point is owned by at least one story.
```

## Design Summary

MM-319 asks for a MoonSpec story breakdown of the skill-system design named by Jira as docs\Tools\SkillSystem.md. That exact file is absent in this checkout, so the breakdown uses the current canonical Agent Skill System, Skill Injection, and Tool/Plan contract documents. The design separates agent instruction bundles from executable tools, treats skills as versioned deployment-scoped data, resolves allowed sources into immutable snapshots before runtime launch, materializes selected bundles into .agents/skills without mutating repo sources, and requires operator-visible provenance plus boundary tests.

## Coverage Points

| ID | Type | Source Section | Design Point |
| --- | --- | --- | --- |
| DESIGN-REQ-001 | constraint | AgentSkillSystem §3; SkillAndPlanContracts §1.1 | MoonMind must keep executable ToolDefinition contracts, runtime-native commands, and AgentSkillDefinition instruction bundles as separate typed concepts. |
| DESIGN-REQ-002 | requirement | AgentSkillSystem §2, §4, §6, §9 | Agent skills are first-class deployment-scoped data with versioned definitions, versions, skill sets, and entries rather than incidental markdown files. |
| DESIGN-REQ-003 | state-model | AgentSkillSystem §6 | Built-in, deployment-stored, repo checked-in, repo local-only, and explicit task or step overrides can contribute candidate skills. |
| DESIGN-REQ-004 | security | AgentSkillSystem §7, §17 | Resolution applies deterministic precedence and deployment policy gates, treating repo and local-only skill content as potentially untrusted input. |
| DESIGN-REQ-005 | constraint | AgentSkillSystem §8, §14; SkillInjection §6, §8 | The runtime-visible active path is .agents/skills, while .agents/skills/local remains a local-only overlay input and not the durable source of truth. |
| DESIGN-REQ-006 | state-model | AgentSkillSystem §10 | Skill body versions and ResolvedSkillSet snapshots are immutable; retries reuse the same snapshot and reruns reuse it unless re-resolution is explicit. |
| DESIGN-REQ-007 | requirement | AgentSkillSystem §11 | Tasks and steps express skill intent through selectors for skill sets, includes, excludes, pins, materialization preferences, and explicit empty selections where allowed. |
| DESIGN-REQ-008 | integration | AgentSkillSystem §12, §15 | Skill resolution happens before runtime launch at activity or service boundaries, collecting selectors, loading allowed sources, applying policy and precedence, and returning compact refs. |
| DESIGN-REQ-009 | constraint | AgentSkillSystem §7.2, §12.4 | Missing required skills, unsatisfied pins, nondeterministic collisions, policy blocks, and runtime incompatibility must fail before runtime launch. |
| DESIGN-REQ-010 | artifact | AgentSkillSystem §10.3, §15.3; SkillAndPlanContracts §3 | Large skill bodies, manifests, bundles, and source traces live in artifacts or blob storage; workflow history carries refs and compact metadata only. |
| DESIGN-REQ-011 | integration | SkillInjection §2, §4, §7 | Managed runtimes use one standard injection model: compact inline activation summary plus full selected skill bundle materialized on disk. |
| DESIGN-REQ-012 | state-model | SkillInjection §6, §9, §10 | MoonMind materializes the resolved active skill snapshot into a run-scoped internal active directory and projects it to the runtime-visible path. |
| DESIGN-REQ-013 | security | SkillInjection §8, §13, §16 | The active tree contains only selected skills for the current run or step; adapters must not scan for additional skills or add unselected skills during execution. |
| DESIGN-REQ-014 | constraint | AgentSkillSystem §8.4; SkillInjection §12, §13, §16 | Repo-provided skill folders may influence resolution, but checked-in skill sources must not be rewritten in place during runtime setup. |
| DESIGN-REQ-015 | requirement | SkillInjection §11 | Step instructions include a compact activation block naming active skills, .agents/skills, immediate hard constraints, and optional first-read hints without duplicating full bodies. |
| DESIGN-REQ-016 | constraint | SkillInjection §12 | Projection conflicts at .agents or .agents/skills must fail before runtime launch with the conflicting path, object kind, attempted action, and operator remediation. |
| DESIGN-REQ-017 | integration | SkillInjection §16; ManagedAndExternalAgentExecutionModel §1-§2.5 | Runtime adapters consume pinned snapshot refs, materialize exactly once per snapshot, expose .agents/skills, inject the activation summary, and avoid re-resolution. |
| DESIGN-REQ-018 | observability | AgentSkillSystem §18; SkillInjection §15, §18 | MoonMind surfaces selected skills, versions, provenance, resolved snapshot refs, manifest artifacts, materialization mode, visible path, projection failures, and activation summary evidence. |
| DESIGN-REQ-019 | requirement | AgentSkillSystem §19 | Proposals, schedules, reruns, retries, and continue-as-new preserve skill intent or resolved snapshots clearly; re-resolution is an explicit operator action. |
| DESIGN-REQ-020 | verification | SkillInjection §19 | Skill injection changes require real adapter or activity boundary tests covering single and multi-skill projections, read-only materialization, activation summaries, collisions, replay, and repo-skill input without mutation. |
| DESIGN-REQ-021 | non-goal | SkillInjection §3.1, §20; AgentSkillSystem §13 | Retrieval-first loading, multiple managed-runtime injection modes, custom visible paths, and per-skill leaf mounting are not the canonical managed-runtime path for now. |

## Ordered Story Candidates

### STORY-001: Agent Skill Catalog and Source Policy

Short name: `skill-catalog-policy`
Source reference: `docs/Tools/SkillSystem.md`
Current source sections: AgentSkillSystem §1-§7; AgentSkillSystem §9; AgentSkillSystem §17; SkillAndPlanContracts §1.1

Why: As a MoonMind operator, I can rely on agent skills being modeled as deployment-scoped, versioned instruction data with explicit source precedence and policy gates, so managed runs do not confuse instruction bundles with executable tools or silently trust repo-local content.

Scope:
- Define or preserve concrete AgentSkillDefinition, AgentSkillVersion, SkillSet, and SkillSetEntry contracts with source kind and version metadata.
- Keep executable ToolDefinition and runtime-native command contracts separate from agent-skill instruction bundles in validation and docs.
- Apply deployment policy gates to repo and local-only skill sources before selection or materialization.
- Treat repo-provided and local-only skill content as potentially untrusted input.

Out of scope:
- Runtime materialization mechanics.
- Mission Control skill-selection UI.
- Executable tool registry changes unrelated to agent instruction bundles.

Independent test:
- Create deployment, built-in, repo, and local-only skill candidates with conflicting names, then verify the catalog and resolution service distinguish AgentSkillDefinition from ToolDefinition and apply source policy before precedence.

Acceptance criteria:
- Given executable tools and agent instruction bundles share the historical word skill, when contracts are validated, then ToolDefinition, runtime command, AgentSkillDefinition, SkillSet, and ResolvedSkillSet remain typed separately.
- Given a deployment-stored skill is edited, when the change is saved, then a new immutable version is created instead of mutating the previous version.
- Given built-in, deployment, repo, and local-only candidates exist, when policy allows them, then deterministic precedence selects the winner and records source provenance.
- Given policy forbids repo or local-only skills, when those sources contain candidates, then they are excluded before precedence is applied and cannot silently affect the run.

Dependencies: None

Risks or open questions:
- Ambiguous historical Skill* names in code can make accidental contract mixing easy unless tests assert the canonical types.

Owned coverage: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004

### STORY-002: Skill Selection and Snapshot Resolution

Short name: `skill-snapshot-resolution`
Source reference: `docs/Tools/SkillSystem.md`
Current source sections: AgentSkillSystem §10-§12; AgentSkillSystem §15; AgentSkillSystem §19; ManagedAndExternalAgentExecutionModel §2.5

Why: As a task author, I can express task-wide and step-specific skill intent and have MoonMind resolve it into an immutable, artifact-backed snapshot before runtime launch, so retries, reruns, and audits reproduce the same skill set unless re-resolution is explicit.

Scope:
- Collect task and step skill selectors before runtime launch.
- Resolve allowed source kinds through activity or service boundaries, not deterministic workflow code.
- Pin exact skill versions and write resolved manifest artifacts.
- Fail fast for missing required skills, unsatisfied pins, nondeterministic collisions, policy blocks, or runtime incompatibility.
- Preserve proposal, schedule, retry, rerun, and replay semantics for skill intent and resolved snapshots.

Out of scope:
- Workspace projection implementation.
- Mission Control rendering beyond metadata needed for audit.
- Retrieval-based on-demand loading.

Independent test:
- Submit a task with task.skills plus a step override, then assert the resolution activity returns a compact ResolvedSkillSet ref with exact versions and that a retry or rerun reuses the original snapshot unless explicitly re-resolved.

Acceptance criteria:
- Given task.skills defines a baseline and step.skills excludes one inherited skill, when the step is prepared, then the resolved snapshot reflects the override without mutating task-level intent.
- Given a pinned skill version cannot be resolved, when resolution runs, then the workflow fails before runtime launch with an actionable validation error.
- Given a ResolvedSkillSet is produced, then workflow history contains only compact refs and metadata while large manifests, bodies, bundles, and source traces are artifact-backed.
- Given a retry, continue-as-new, or ordinary rerun occurs, then the same resolved snapshot is reused unless the caller explicitly requests re-resolution.

Dependencies: STORY-001

Risks or open questions:
- Temporal payload shape changes are compatibility-sensitive and need boundary coverage if existing executions may be in flight.

Owned coverage: DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-019

### STORY-003: Managed Runtime Skill Projection

Short name: `runtime-skill-projection`
Source reference: `docs/Tools/SkillSystem.md`
Current source sections: AgentSkillSystem §8; AgentSkillSystem §13-§16; SkillInjection §2-§16; ManagedAndExternalAgentExecutionModel §1-§2.5

Why: As a managed runtime adapter, I can materialize a pinned skill snapshot into a run-scoped active backing store and expose exactly that selected set at .agents/skills with a compact activation summary, so agents see the expected path without MoonMind rewriting checked-in skill folders.

Scope:
- Materialize the active skill bundle into a MoonMind-owned run-scoped backing directory exactly once per snapshot.
- Project the active backing store at .agents/skills for managed runtimes using adapter-compatible mechanics.
- Include only selected skills and a MoonMind-owned active manifest in the runtime-visible tree.
- Inject a compact activation summary naming active skills, visible path, hard rules, and first-read hints.
- Do not use retrieval-first loading, custom visible paths, or per-skill leaf mounting as the canonical managed-runtime path.

Out of scope:
- Skill catalog CRUD.
- Task and step selector UX.
- Runtime-native command execution.

Independent test:
- Prepare a managed runtime step with one selected skill and then multiple selected skills, verifying the run-scoped backing store, .agents/skills projection, activation summary, selected-only tree, and pre-launch collision failure behavior.

Acceptance criteria:
- Given a resolved snapshot with one skill, when the adapter prepares the runtime, then .agents/skills contains a full active root with _manifest.json and that skill's SKILL.md.
- Given a resolved snapshot with multiple skills, then only selected skills appear in the active projection and unselected repo skills are absent.
- Given a checked-in .agents/skills directory exists, then MoonMind may use it as a resolution input but does not rewrite it in place during runtime setup.
- Given .agents or .agents/skills is an incompatible file or unprojectable path, then preparation fails before runtime launch with path, object kind, attempted action, and remediation guidance.
- Given the runtime starts, then the instruction payload includes a compact activation summary and full skill bodies are available on disk, not duplicated inline.

Dependencies: STORY-002

Risks or open questions:
- Filesystem projection mechanisms vary by adapter; tests should assert logical behavior rather than a single mount implementation.

Owned coverage: DESIGN-REQ-005, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-021

### STORY-004: Skill Runtime Observability and Verification

Short name: `skill-observability-tests`
Source reference: `docs/Tools/SkillSystem.md`
Current source sections: AgentSkillSystem §18-§23; SkillInjection §15, §18-§19; docs/tmp/004-AgentSkillSystemPlan.md §2-§5

Why: As an operator or maintainer, I can inspect which skills were selected, where they came from, how they were materialized, and whether projection succeeded, with boundary tests proving the real adapter and activity behavior rather than isolated helper behavior only.

Scope:
- Surface resolved skill metadata in submit, detail, and debug contexts appropriate to operator permissions.
- Record active backing path, visible path, projected skills and versions, read-only state, collision failures, and activation summary evidence.
- Preserve artifact and payload discipline by linking manifests or prompt indexes instead of logging full bodies by default.
- Add or maintain boundary-level tests for adapter and activity behavior when skill injection or resolution behavior changes.

Out of scope:
- Full admin skill catalog management UI.
- Provider auth or runtime launch semantics outside skill materialization.
- External-agent provider-specific presentation beyond shared metadata.

Independent test:
- Run adapter or activity boundary tests that exercise projection and then assert Mission Control/API-visible metadata includes snapshot refs, selected versions, provenance, manifest links, visible path summary, read-only state, collision errors, and delivered activation summary evidence without leaking full skill bodies by default.

Acceptance criteria:
- Given a task detail view or API response for a run with skills, then it exposes resolved snapshot ID, selected skill versions, source provenance, materialization mode, visible path summary, and manifest or prompt-index artifact refs where appropriate.
- Given a projection collision occurs, then operator-visible diagnostics include the path, object kind, attempted projection action, and remediation without dumping full skill bodies.
- Given proposal, schedule, or rerun metadata is inspected, then skill intent or resolved snapshot reuse is explicit and re-resolution is never silent.
- Given the skill injection implementation changes, then real adapter or activity boundary tests cover single-skill and multi-skill projections, read-only materialization, activation summary injection, collision failure, replay reuse, and repo-skill input without in-place mutation.

Dependencies: STORY-002, STORY-003

Risks or open questions:
- Operator visibility must avoid turning untrusted skill bodies into broadly exposed logs or UI payloads.

Owned coverage: DESIGN-REQ-010, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-020

## Coverage Matrix

| Coverage Point | Owning Stories |
| --- | --- |
| DESIGN-REQ-001 | STORY-001 |
| DESIGN-REQ-002 | STORY-001 |
| DESIGN-REQ-003 | STORY-001 |
| DESIGN-REQ-004 | STORY-001 |
| DESIGN-REQ-005 | STORY-003 |
| DESIGN-REQ-006 | STORY-002 |
| DESIGN-REQ-007 | STORY-002 |
| DESIGN-REQ-008 | STORY-002 |
| DESIGN-REQ-009 | STORY-002 |
| DESIGN-REQ-010 | STORY-002, STORY-004 |
| DESIGN-REQ-011 | STORY-003 |
| DESIGN-REQ-012 | STORY-003 |
| DESIGN-REQ-013 | STORY-003 |
| DESIGN-REQ-014 | STORY-003 |
| DESIGN-REQ-015 | STORY-003 |
| DESIGN-REQ-016 | STORY-003 |
| DESIGN-REQ-017 | STORY-003 |
| DESIGN-REQ-018 | STORY-004 |
| DESIGN-REQ-019 | STORY-002, STORY-004 |
| DESIGN-REQ-020 | STORY-004 |
| DESIGN-REQ-021 | STORY-003 |

## Dependencies

- STORY-001 depends on no prior stories
- STORY-002 depends on STORY-001
- STORY-003 depends on STORY-002
- STORY-004 depends on STORY-002, STORY-003

## Out Of Scope

- Creating or modifying spec.md files.
- Creating directories under specs/.
- Implementing the skill system or changing runtime code during breakdown.
- Creating Jira issues; this output is Jira-ready story data only.
- Using retrieval-first or MCP-based skill loading as the normal managed-runtime path.

## Coverage Gate

PASS - every major design point is owned by at least one story.
