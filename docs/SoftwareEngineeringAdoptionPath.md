# Software-Engineering Adoption Path

**Document Class:** Canonical declarative
**Viewpoint:** System / Feature Design View
**Status:** Proposed
**Updated:** 2026-09-06
**Audience:** Engineering teams evaluating coding agents, operators, security reviewers, and workflow authors
**Authority:** Epic-level target behavior for MoonLadderStudios/MoonMind#3930 — the first integrated software-engineering journey, its authorization boundary, terminal evidence, recovery behavior, local-first preservation, README traceability, and child ownership
**Owning Surface:** Workflow admission, repository access, runtime and publication boundaries, artifact evidence, Secrets System, and Provider Profiles
**Related Docs:** [MoonMind Architecture](MoonMindArchitecture.md), [MoonMind Roadmap](MoonMindRoadmap.md), [Repository Access and Workspace Design](RepositoryAccessAndWorkspaceDesign.md), [Secrets System](Security/SecretsSystem.md), [Provider Profiles](Security/ProviderProfiles.md), [Workflow Architecture](Workflows/WorkflowArchitecture.md), [Workflow Runs API](Workflows/WorkflowRunsApi.md), [Workflow Presets](Workflows/WorkflowPresetsSystem.md), [Workflow Publishing](Workflows/WorkflowPublishing.md), [Workflow Remediation](Workflows/WorkflowRemediation.md), [Documentation Architecture](DocumentationArchitecture.md)
**Related Implementation:** [`api_service/api/routers/executions.py`](../api_service/api/routers/executions.py), [`moonmind/workflows/executions/repository_contract.py`](../moonmind/workflows/executions/repository_contract.py), [`moonmind/publish/service.py`](../moonmind/publish/service.py), [`api_service/services/secrets.py`](../api_service/services/secrets.py), [`moonmind/workflows/adapters/github_service.py`](../moonmind/workflows/adapters/github_service.py)
**Epic:** MoonLadderStudios/MoonMind#3930

> This document is the epic-level definition of done for #3930. It describes desired target behavior and ownership, not deployment evidence. Child issues own their implementation and qualification evidence. Sequencing, per-PR disposition, and rollout state belong in `docs/tmp/` or GitHub issues, not in this file.

## Advance organizer

**One sentence:** An explicitly authorized operator submission starts bounded agent work; the operator inspects progress and a verified result, while repository events, public content, and signatures alone authorize nothing.

**One paragraph:** Engineering teams evaluating coding agents are the initial audience for this journey. Local-first users, repository-independent work, and the broader AI-security mission remain in scope as target behavior. Installation opt-in and repository opt-in are distinct. A valid webhook signature or public issue content cannot authorize model spend or repository mutation. Security, resilience, and observability claims stay traceable to supported paths and named code boundaries, with planned, implemented, and qualified behavior kept distinct.

## 1. First integrated journey

### JOURNEY-001 Supported entrypoint

The supported entrypoint for the first integrated journey is an explicitly authorized operator submission: the dashboard Create flow or `POST /api/executions` carrying a preset or authored workflow with admission-resolved profile, policy, workspace, Skill, and publication intent. The preset and admission boundary selects the immutable execution plan before any agent compute begins. Repository events do not submit work directly; any event-driven path must pass through this same admission boundary under an explicit installation, repository, and actor policy before work starts.

### JOURNEY-002 Authorization boundary

Authorization is explicit and compound. Starting bounded work or spending model budget requires an admitted operator submission scoped to an authorized installation, an admitted repository target where repository work is requested, and an authorized actor. Public issue content is untrusted input. A valid webhook signature proves delivery origin, not operator authorization to spend or mutate. Repository credentials never authorize model-provider behavior, and model-provider authority never authorizes repository mutation. Missing, ambiguous, disabled, revoked, or insufficient authority fails closed with an actionable error; it never falls back to another credential, an anonymous downgrade, a broader execution path, or a substituted profile or model value.

### JOURNEY-003 Terminal evidence

The verified result is artifact-backed evidence, not prose or a status projection. Compute, save, and publication outcomes remain separate: durable saved work is verified through the artifact and checkpoint systems with an immutable manifest, and remote publication is verified through provider-aware publication evidence linked to that saved work. Missing scan, review, or cleanup evidence remains visible as missing; it never becomes a clean bill of health. A process exit, wrapper completion, timestamp, or dashboard projection alone is not proof of durable saving or remote publication.

### JOURNEY-004 Recovery behavior

Recoverable failures preserve the authoritative workspace and immutable inputs, perform bounded continuation or retry under the same admitted authority, and publish a remotely verified recovery checkpoint before cleanup when retries exhaust. Failed or cancelled compute can still save useful work without becoming successful compute. Publication failure does not overwrite verified compute or save evidence. Publication-only recovery re-admits the destination and reconciles exact remote evidence without rerunning the agent or mutating the original saved result. Changed instructions or authority-sensitive choices create an explicit new turn, branch, or execution rather than rewriting historical input.

## 2. Repository-event policy

### EVENT-001 Events authorize nothing by themselves

Repository events cannot launch work or spend model budget outside explicit installation, repository, and actor policy. The optional GitHub App path is owned by #3967 and evolves behind the existing RepositoryConnection and Secrets boundary. An App installation is never mandatory for ordinary local use. A repository credential is never reinterpreted as model-provider authority. First-run verification and its documented commands are owned by #3938 and #3940 respectively, not by this epic.

## 3. Local-first and repository-independent behavior

### LOCAL-001 Preserved target behavior with honest limitations

Repository-independent work is a complete product path: useful scratch, uploaded-project, checkpoint-restore, public-anonymous-read, report, patch, bundle, and workspace-archive results do not require a repository connection or a GitHub credential. Current implementation limitations are stated honestly where they apply: GitHub App event integration, the consolidated governance report, and the bounded repository-security tool pack are child-owned work that is planned but not yet implemented or qualified. Configuration files, a registered Skill, or an installed image are not proof of end-to-end enforcement.

## 4. Evidence-backed claims

### CLAIM-001 README statements stay traceable

README security, resilience, and observability statements are backed by identified code and boundary evidence, not by source counts or aspirational prose. The three existing pillars are preserved: security boundaries enforced in the execution substrate, resilient durable execution, and observable evidence-producing runs. Each pillar traces to its owning boundary:

| Pillar | Owning boundary and evidence |
| --- | --- |
| Security | Provider Profiles as policy, sandboxed execution without the host Docker socket, secret references resolved only at controlled launch boundaries with redaction, deterministic high-security outbound scanning when enabled, and fail-fast behavior without silent substitution, per the Secrets System and Provider Profiles |
| Resilience | Temporal-backed durable workflows with step-boundary checkpoints, stuck detection with escalating intervention, rate-limit-aware concurrency and cooldowns, idempotent externally visible side effects, and scheduled execution, per the workflow and remediation contracts |
| Observability | Dashboard run views, session-aware live-log timelines with artifact-backed replay, artifact-first outputs, correlated structured logs, and runtime provenance, per the workflow, artifact, and bridge contracts |

Statements under "Where this is headed" describe planned direction, not current enforcement. Planned, implemented, and qualified behavior remain distinct, following the roadmap evidence rules: "supported" means the support matrix links passing evidence for that exact combination, and weaker states such as implemented, installed, connected, or designed do not qualify as support.

### CLAIM-002 Governance results require evidence

The governance result view is owned by #3969 and reports explicit coverage and failure states from scan, review, and cleanup evidence. Until that consolidated report is implemented and qualified, existing protected artifacts remain usable as the result view, and absent evidence stays visible rather than passing silently.

## 5. Child ownership and exclusions

### OWN-001 Child issues own their deliverables

| Child | Ownership |
| --- | --- |
| #3967 Authorized, durable GitHub App event integration | Owns the optional App event path, durable admission, and installation, repository, and actor policy enforcement |
| #3968 Evidence-backed README and security-boundary explanation | Owns README-to-code traceability, mode and credential limitations, and the planned, implemented, and qualified distinction |
| #3969 Governance report with explicit coverage and failure states | Owns the consolidated result view with visible missing-evidence states |
| #3970 Bounded repository-security tool-pack design and evidence-based sequencing | Owns the bounded repository-security capability design, separating passive repository analysis from future network-active work with documented prerequisites |

Related issues #763, #809, #983, #2835, #3938, and #3940 are related context, not completion dependencies of this epic.

### OWN-002 Closed work stays excluded

#3971 remains closed as not planned and is excluded from completion criteria. It is not relabeled as implemented, and its sample-repository and screen-recording scope is not recreated inside the other children. No closed-not-planned task is reintroduced as required work by this epic.

## 6. Adoption assessment

### ADOPT-001 Evidence over assertion

Adoption is assessed through observed task completion, actionable failure behavior, setup friction, and operator understanding. Claims about market validation require user evidence and are never derived from Skill counts, file counts, or search results. This epic reuses tests and evidence from child owners rather than adding a second umbrella functional suite; the durable guard for this document is the docs-contract test that pins its journey, boundary, evidence, recovery, ownership, and exclusion statements.
