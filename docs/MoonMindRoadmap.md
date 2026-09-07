# MoonMind Roadmap

> MoonMind is a secure, resilient, and observable orchestration platform for agentic work.
> Omnigent is the primary runtime provider for Codex, Claude Code, OpenCode, and future approved harnesses.
> MoonMind owns durable workflow authority, policy, credentials, workspaces, recovery, evidence, and publication.
>
> **Document class:** canonical declarative entrypoint. Durable desired state only.
> Dated status, rollout sequencing, and per-PR disposition live in the disposable
> tracker at [`docs/tmp/MoonMindRoadmapExecutionTracker.md`](tmp/MoonMindRoadmapExecutionTracker.md)
> and in GitHub issues. When they disagree, this document wins.
> Canonical direction: [`docs/Omnigent/PrimaryRuntimeProviderStrategy.md`](Omnigent/PrimaryRuntimeProviderStrategy.md),
> [`docs/Omnigent/OmnigentHarnessPlatformDesign.md`](Omnigent/OmnigentHarnessPlatformDesign.md),
> [`docs/Omnigent/RuntimeProviderRollout.md`](Omnigent/RuntimeProviderRollout.md).

## Advance organizer

**One sentence:** MoonMind lets people direct provider-maintained agents and specialized
tools through one durable, policy-controlled platform that is simple for normal use and
deeply inspectable when something goes wrong.

**One paragraph:** Omnigent becomes the preferred runtime beneath MoonMind authority.
Codex, Claude Code, OpenCode, and future harnesses share one execution, session, chat,
evidence, recovery, and cleanup plane. Guided cybersecurity workflows, simpler product
abstractions, portable outputs, and replaceable connectors build on that foundation.
Normal operation stays local-first and useful with minimal configuration.

## Product destination

A normal request resolves through workflow or session, immutable Agent Profile and
policy, compatible Provider Profile, authorized workspace and Skills, a
provider-maintained agent through Omnigent, and canonical session evidence into a
durable result, artifact, repository output, or connector publication with checkpoint,
recovery, verification, and cleanup. The product answers what to do, what sources may
be used, which agent or capability performs it, what authority governs it, and where
the result is preserved. Users never need Host Classes, materializers, runtime packs,
or lease generations for the normal path. Local-first operation works without a GitHub
credential; GitHub and other providers are output options, and portable artifacts,
patches, bundles, local commits, and workspace archives remain first-class.

## Target ownership split

- **MoonMind owns** Temporal orchestration, Agent/Provider Profile selection, policy,
  credentials, canonical workspaces, Skills, checkpoints, remediation, artifacts,
  publication, cleanup, and audit evidence.
- **Omnigent owns** the host and runner protocol, harness discovery, the live provider
  process, provider-session interactions, and upstream events.
- **The MoonMind Omnigent bridge owns** session creation, canonical session/turn
  correlation, event normalization and replay, Workflow Detail projection, controls,
  resource harvesting, artifact publication, and retry-safe external-state evidence.
- **Specialized tool images own** their binaries and portable invocation; MoonMind owns
  admission, capability policy, durable execution, evidence, and cleanup.
- **Connector providers own** provider APIs and object semantics; MoonMind owns
  connection authority, normalized capabilities, side-effect evidence, and retry policy.
- Direct Codex/Claude and the profile-bound Omnigent realizer remain migration
  compatibility substrate until evidence-gated retirement criteria pass.

## Milestone outcomes

| Milestone | Outcome | Canonical owner |
| --- | --- | --- |
| 1. Complete the Omnigent Agent Platform | One generic lifecycle for all harnesses: execution, chat, evidence, recovery, cleanup | [Strategy](Omnigent/PrimaryRuntimeProviderStrategy.md), [Harness platform](Omnigent/OmnigentHarnessPlatformDesign.md), [Rollout](Omnigent/RuntimeProviderRollout.md) |
| 2. Guided Cybersecurity Workbench | Scoped repository-first tool packs, normalized findings, remediation and verification | [Restricted egress](Security/RestrictedEgress.md), [Remediation](Workflows/WorkflowRemediation.md) |
| 3. Make MoonMind Feel Smaller | Progressive disclosure; deletions and vocabulary cleanup only after cutover gates | [Workflow architecture](Workflows/WorkflowArchitecture.md) |
| 4. Connectors and replaceable capabilities | Versioned connectors, Git-transport/code-host split, portable outputs, replaceable RAG/memory | [Publishing](Workflows/WorkflowPublishing.md), [RAG](Rag/WorkflowRag.md) |

Product milestone numbers above differ from the dated execution-tracker numbering; the difference is editorial and does not retire any claim.

## Milestone sequencing

Milestone 1 is the primary near-term dependency. Milestone 2 can begin during
Milestone 1 with the generic tool-pack contract, repository security workflows,
normalized findings, and report outputs. Network-active security workflows remain
gated on proven target authorization and restricted-egress enforcement. Milestone 3
deletions follow Milestone 1 cutover decisions. Milestone 4 broad expansion follows
stable runtime abstractions. Detailed order, cohorts, and ownership live in the
tracker and GitHub issues. Repository-security tool-pack work is permitted during
runtime convergence; it is not blocked on unrelated GitHub App/CLI work.

## Omnipresent goals

- **Security:** credential, filesystem, network, target, tool, publish, approval,
  retrieval, connector, and control boundaries enforced at trusted substrate boundaries.
- **Resilience:** idempotent retry, evidence-gated resume, branch isolation, bounded
  degraded mode, portable saved work, and durable cleanup; never silently substitute
  authority, profile, policy, target, or checkpoint.
- **Observability:** live state, outcomes, denials, artifacts, findings, side effects,
  cleanup, recovery, retrieval, and rollout state inspectable through MoonMind projections.
- **Simplicity, portability, maintainability:** one explicit contract over parallel
  aliases; standard files, CLIs, containers, Git, and artifacts; providers own
  replaceable implementations behind governed boundaries.

## Completion and evidence rules

Roadmap status follows evidence, not issue bookkeeping. Merged code or a closed issue
is substrate until independently resolvable acceptance evidence links to the exact
combination. A verifier reporting `ADDITIONAL_WORK_NEEDED`, `BLOCKED`, or missing live
proof does not close a gate. Stubs, self-asserted fields, installed binaries, and
configured networks are not support proof. Normal paths fail closed. A closed tracker
with known residual work needs a reopened or follow-up issue before its claim is owned.

## Durable acceptance-claim identifiers

These exact identifiers are stable. Relocating them never marks them met; changing or
removing a meaning needs an explicit owner-approved contract decision. All five remain
unresolved (`[ ]`); successors below are the traceable owners.

- [ ] **5.1 Checkpoint boundary and completeness** — implementation foundation landed; independently resolvable acceptance evidence remains required. Successor: [`docs/Steps/StepExecutionsAndCheckpointing.md`](Steps/StepExecutionsAndCheckpointing.md), [`docs/Temporal/CheckpointResumePromotion.md`](Temporal/CheckpointResumePromotion.md); tracking MoonLadderStudios/MoonMind#3510 (slice MoonLadderStudios/MoonMind#3509).
- [ ] **5.4 Resume-from-checkpoint default flow** — production orchestration must choose validated reattach, cold restore, branch-required, or explicit unavailable outcomes. Successor: [`docs/Temporal/CheckpointResumePromotion.md`](Temporal/CheckpointResumePromotion.md); tracking MoonLadderStudios/MoonMind#3510.
- [ ] **5.5 Checkpoint Branch UI and runtime-profile gaps** — isolated corrected-instruction turns, selectors, compare, promote, and archive in Workflow Detail. Successor: [`docs/Workflows/CheckpointBranchSystem.md`](Workflows/CheckpointBranchSystem.md); tracking MoonLadderStudios/MoonMind#3510.
- [ ] **6.2 Omnigent remediation context enrichment** — bounded evidence with target-authorized typed actions and closed residual authority gaps. Successor: [`docs/Workflows/WorkflowRemediation.md`](Workflows/WorkflowRemediation.md); tracking MoonLadderStudios/MoonMind#3512 (residual MoonLadderStudios/MoonMind#3511).
- [ ] **7.1 Initial context injection for Omnigent** — durable controlling verification evidence for first-message `ContextPack` injection. Successor: [`docs/Rag/WorkflowRag.md`](Rag/WorkflowRag.md); tracking MoonLadderStudios/MoonMind#3514 (residual MoonLadderStudios/MoonMind#3513).

Changing an identifier above is a deliberate owner-approved invariant change. Update the pinning contract tests in the same change rather than deleting an identifier to pass.

## Where the imperative tracker lives

Open execution items, residual closed-but-incomplete work, and unique security/qualification obligations live in the disposable handoff at [`docs/tmp/MoonMindRoadmapExecutionTracker.md`](tmp/MoonMindRoadmapExecutionTracker.md) (MoonLadderStudios/MoonMind#3507, #3508, #3510, #3512, #3514, #3516, #3517, #3518 plus residuals #3509, #3511, #3513, #3515, #3519, #3520). Delete that file only after every active item has a durable issue owner and all references are updated.
