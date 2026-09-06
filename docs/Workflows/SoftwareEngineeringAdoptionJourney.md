# Software-Engineering Adoption Journey

**Document Class:** Canonical declarative
**Viewpoint:** System / Feature Design View
**Status:** Active
**Updated:** 2026-09-06
**Audience:** Engineering teams evaluating MoonMind coding agents, operators, security reviewers, workflow and integration authors
**Authority:** Epic-level target behavior for MoonMind/MoonMind#3930: the first integrated software-engineering journey across existing preset/admission, runtime, publication, and evidence boundaries
**Owning Surface:** Workflow admission, repository access, workspace materialization, publication, Secrets System, Provider Profiles, and governance evidence boundaries
**Related Docs:** [MoonMind Roadmap](MoonMindRoadmap.md), [Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md), [Secrets System](../Security/SecretsSystem.md), [Workflow Architecture](WorkflowArchitecture.md), [Workflow Presets System](WorkflowPresetsSystem.md), [Workflow Publishing](WorkflowPublishing.md), [Omnigent Primary Runtime Provider Strategy](../Omnigent/PrimaryRuntimeProviderStrategy.md)
**Related Implementation:** [`api_service/api/routers/executions.py`](../../api_service/api/routers/executions.py), [`repository_contract.py`](../../moonmind/workflows/executions/repository_contract.py), [`moonmind/auth/github_credentials.py`](../../moonmind/auth/github_credentials.py), [`moonmind/publish/`](../../moonmind/publish/), [`api_service/services/secrets.py`](../../api_service/services/secrets.py)

> Inspected combination: `main` at `7643ae6ab` (in sync with `origin/main`; rebaseline `6c9da3585a474c8623fc43a25b33dda2cd476951` from 2026-09-05). No post-rebaseline commits reference #3930 or children #3967-3970. Unresolved conditions owned by the children are recorded in Section 7 rather than claimed as complete.

## Advance organizer

**One sentence:** An explicitly authorized operator submission starts bounded agent work; the operator inspects progress and a verified result, while repository events alone can never spend model budget or mutate repositories.

**One paragraph:** The supported engineering journey reuses existing boundaries end to end: the dashboard Create page or an authenticated `POST /api/executions` call submits workflow intent, preset expansion and capability admission validate it, Provider Profiles authorize model spend, `RepositoryConnection` and the repository contract authorize repository reads and publication, sandboxed runtimes execute the selected agent, and artifact-backed saved work plus publication evidence form the inspectable result. Installation opt-in and repository opt-in stay distinct. Local-first and repository-independent work remain complete product paths. Security, resilience, and observability claims trace to the code boundaries named in Section 5, not to file counts or aspirational prose. Child issues #3967-3970 own the remaining enforcement, explanation, reporting, and tool-pack gaps; closed-not-planned #3971 is excluded and is not recreated here.

## 1. The supported journey

### JOURNEY-001 Exact supported entrypoint

The only supported entrypoints that can start agent work and spend model budget are operator-authenticated submissions:

| Entrypoint | Authority | Evidence |
| --- | --- | --- |
| Dashboard Create page submitting a workflow | Authenticated operator session; `get_current_user` dependency on execution routes | Workflow Execution identity (`workflowId`/`runId`), Temporal Visibility records |
| Authenticated `POST /api/executions` (and its preset/rerun/continuation variants under the same router) | `api_service/api/routers/executions.py` requires `get_current_user()`; unauthenticated callers receive an auth failure before any admission, launch, or spend | Same Workflow Execution identity; step ledger, artifacts, and diagnostics linked to the run |

Preset expansion (`docs/Workflows/WorkflowPresetsSystem.md`) is deterministic and backend-owned; expanded steps pass through the same validation, policy, runtime, and publishing controls as manually authored steps. There is no supported inbound repository webhook, issue-event listener, or unauthenticated trigger that creates executions.

### JOURNEY-002 Authorization boundary

Explicit policy authorizes each authority independently before work starts:

- **Model spend:** the selected Provider Profile (runtime, provider, credential source, materialization, concurrency, cooldowns) authorizes which model account pays for the run. A repository credential is never model-provider authority.
- **Repository reads and mutation:** a named `RepositoryConnection` plus the repository contract (`ensure_repository_ready`, `validate_connection_and_client` in `moonmind/workflows/executions/repository_contract.py`) authorize each declared source, collaboration, and destination role. Missing, ambiguous, disabled, revoked, or insufficient authority fails closed without credential shopping, anonymous downgrade, source substitution, or a broader execution path (INV-001 in the repository design).
- **Installation vs repository opt-in:** a GitHub App installation (child #3967) and a repository connection are separate opt-ins. Installing an App never implicitly authorizes every repository, and configuring a repository connection never authorizes model spend.
- **Untrusted content is never authority:** public issue text, PR bodies, comments, or a bare webhook signature alone cannot authorize model spend or repository mutation. They are data, not policy.

### JOURNEY-003 Terminal evidence

A run ends with separately inspectable compute, save, and publication outcomes:

- **Compute evidence:** Temporal step ledger, per-step logs/diagnostics, and the dashboard Workflow Detail projection.
- **Save evidence:** the artifact-backed saved-work manifest and checkpoint references. Failed or cancelled compute can still save useful work without becoming successful compute.
- **Publication evidence:** for MoonMind-managed modes, branch/PR records linked to the saved-work digest; for agent-owned `auto`, `artifacts/publish_result.json` with schema `moonmind.publish.auto.v1` validated against the terminal contract (`docs/Workflows/WorkflowPublishing.md`).

Process exit, assistant prose, a timestamp, a raw filesystem path, or a dashboard projection alone is not objective completion.

### JOURNEY-004 Recovery behavior

- Failed saving retries the same capture with stable idempotency while the authoritative workspace is retained under bounded recovery; ordinary cleanup never destroys the only copy.
- Blocked or failed publication offers publication-only recovery (`Publish Saved Work`) without rerunning the agent and without mutating the original saved result.
- Failed-step resume retries from the last good step boundary when compatible workspace capture/restore evidence exists; otherwise the operator edits input and retries, or branches with explicitly re-admitted authority.
- Recovery never silently substitutes credentials, provider profiles, billing-relevant model values, source authority, or less-constrained execution paths.

## 2. Repository events cannot launch work

### INV-001 No webhook-driven execution path

MoonMind product code contains no inbound repository-event route that creates Workflow Executions. The only `webhook` references in execution-adjacent code are outbound execution-completion notification payloads (for example `execution.notification.webhook.payload` in `moonmind/workflows/temporal/activity_runtime.py`), which send results after an already-authorized run ends. Adding an authorized, durable GitHub App event integration is explicitly owned by child #3967 and must enforce installation/repository/actor policy before any future event can start work or spend budget.

Until #3967 lands, the invariant is enforced by absence plus authentication: execution creation routes require an authenticated operator, and no unauthenticated event surface bypasses that gate. The regression test `tests/unit/docs/test_software_engineering_adoption_journey.py` pins this invariant.

## 3. Local-first and repository-independent behavior

### DOC-REQ-001 Repository-independent work stays complete

A supported execution can produce, download, and continue useful work without selecting a repository or configuring a GitHub PAT: contained scratch workspaces, authorized artifact/checkpoint imports and restores, explicit anonymous public-repository reads, local commits/branches, patches, Git bundles, reports, and workspace archives. Publication can be requested with execution or independently afterward (`Publish Saved Work`). This matches the repository design (`DOC-REQ-001`) and the roadmap's Milestone 1 clause on useful operation without GitHub credentials.

### QUALITY-001 Limitations are stated honestly

Planned, implemented, and qualified behavior are distinct states (see the roadmap's completion and evidence rules):

| Claim | State at the inspected combination | Owner of the remaining proof |
| --- | --- | --- |
| Scratch and anonymous-public local work without a PAT | Implemented path; hermetic capability qualification per combination remains execution work | Repository design TEST-001/TEST-004; child #3967 for App-backed private flows |
| Private-repository and publication flows via PAT-backed `RepositoryConnection` | Implemented contract boundary (`repository_contract.py`, Secrets System); per-combination qualification evidence remains execution work | Same as above |
| GitHub App installation flow | Planned design (`RepositoryConnection` App variant, acquisition adapter); not implemented | Child #3967 |
| High-security outbound scanning | Implemented mode defaulting to `false`; per-boundary enforcement evidence per combination remains execution work | `docs/Security/SecretsSystem.md` Section 1.1; child #3969 for consolidated reporting |
| Governance report with explicit coverage/failure states | Not implemented; no `GovernanceReport` implementation exists | Child #3969 |
| Bounded repository-security tool pack | Planned direction (generic tool-pack contract, graduated capability levels); no tool-pack design shipped | Child #3970, roadmap Milestone 2 |

`docker compose up -d` with no `.env` plus UI-entered secrets remains the baseline personal-use path. A fresh install without model credentials stays usable for settings, artifacts, and diagnostics and reports the real blocking boundary (described here as `no_eligible_free_model`; the product-code gate reasons are `no_eligible_profile` in the failure taxonomy, surfaced in the execution catalog as `no_eligible_codex_oauth_profile` per `api_service/api/routers/omnigent_catalog.py`) without paid fallback or an irrelevant PAT demand. Child #3968 owns folding this mapping into the full per-statement README traceability.

## 4. README and security-boundary traceability

The README preserves its three existing pillars. Each pillar traces to an identified code or boundary, not to Skill/file counts or searches (which remain historical audit observations):

| Pillar | Representative claims | Backing boundary |
| --- | --- | --- |
| Security | Provider Profiles as policy; sandboxed execution without the host Docker socket; secret references with launch-only materialization; high-security outbound scans; fail fast without silent substitution | `docs/Security/ProviderProfiles.md`, `docs/Security/SecretsSystem.md` (including default-`false` high-security mode), `moonmind/auth/`, `api_service/services/secrets.py`, Docker Backend Service contract, `repository_contract.py` admission |
| Resilience | Temporal durability; step-boundary checkpoints; stuck detection and intervention; rate-limit-aware scheduling; idempotent side effects; scheduled workflows | Temporal workflows/activities, step ledger and checkpoint systems, `docs/Temporal/`, `docs/Workflows/WorkflowRemediation.md` |
| Observability | Dashboard run/step views; session-aware live logs with artifact-backed replay; artifact-first outputs; correlated structured logs; exact runtime provenance | Dashboard and artifact systems, SSE live-log streaming, `docs/Observability/`, Omnigent bridge evidence |

Configuration files, a registered Skill, or an installed image alone are not proof of end-to-end enforcement. Full per-combination traceability prose and the implemented-vs-qualified distinction for every README statement are owned by child #3968, which must preserve the three pillars while adding that evidence mapping.

## 5. Governance result view

The consolidated governance report owned by child #3969 is the future result view for this journey. Until it ships:

- Existing protected artifacts (saved-work manifests, publication evidence, audit events, scan diagnostics) remain usable directly.
- Missing scan, review, or cleanup evidence must remain visible as missing. It cannot be rendered as a clean bill of health, and no interim summary in this epic may imply one.
- #3969 must model explicit coverage/failure states (verified, denied, unknown, stale, incomplete) rather than adding a second audit engine beside the existing boundaries.

## 6. Child ownership and excluded work

| Issue | Ownership | Relationship to this epic |
| --- | --- | --- |
| #3967 Authorized, durable GitHub App event integration | Optional App path; uses the existing `RepositoryConnection`/Secrets boundary as it evolves | Only future authorized event source; never mandatory for ordinary local use |
| #3968 Evidence-backed README and security-boundary explanation | README traceability to supported paths, mode/credential limits, planned-vs-implemented-vs-qualified distinctions | Explains this journey; preserves the three pillars |
| #3969 Governance report with explicit coverage/failure states | Consolidated result view over existing evidence | Renders this journey's outcomes; missing evidence stays visible |
| #3970 Bounded repository-security tool-pack design and evidence-based sequencing | Passive repository analysis separated from future network-active work; real prerequisites documented | First security capability built on this journey; distribution milestones are not automatic technical blockers for a portable local scanner |
| #3971 Sample-repository/screen-recording project | Closed as **not planned**; not a dependency of this epic | Excluded. Nothing in this epic recreates it inside the other children, relabels it as implemented, or reintroduces it as required work |

Related context (not completion criteria): #763 command semantics, #809 scanning, #983 pre-action review, #2835 screenshots, #3938 first-run verification, #3940 documented commands. #3938 owns first-run verification and #3940 owns its documented commands; this journey reuses their evidence rather than duplicating it.

## 7. Verification approach

Per the epic plan, this umbrella adds no second test suite beside the child owners. Coverage for this epic is:

- This document as the durable journey definition with exact entrypoint, boundary, terminal evidence, and recovery behavior.
- Reuse of child-owner tests and evidence (#3967 admission tests, #3968 README traceability checks, #3969 report-state tests, #3970 tool-pack contract tests) when those children land.
- One epic-scoped regression pin (`tests/unit/docs/test_software_engineering_adoption_journey.py`) covering only what this epic directly asserts: the journey description exists with the required sections, the no-webhook-execution invariant holds in product code, local-first target behavior is preserved in prose, and #3971 stays excluded.

## 8. Scope boundaries

### NON-GOAL-001 This epic does not

- Make a GitHub App installation mandatory for ordinary local use, reinterpret a repository credential as model-provider authority, or let public content authorize spend.
- Add a parallel publishing engine, a second audit engine, a general credential microservice, or another always-on container.
- Claim market validation, buyer demand, or universal security guarantees from file counts, grep results, or audience framing. Adoption is assessed through observed task completion, actionable failure, setup friction, and operator understanding.
- Broaden repository, model, artifact, or publication authority beyond the owning contracts named in the header.
