# Execution availability regression prevention

**Document Class:** Imperative working document
**Type:** Implementation Plan
**Status:** Active; incident recovery and implementation progress recorded below
**Owner:** MoonMind Platform
**Created:** 2026-09-13
**Source:** Operator request to restore recurring MoonMind work and prevent repeated version/availability regressions
**Canonical owners:** [AGENTS.md](../../AGENTS.md), [Temporal architecture](../Temporal/TemporalArchitecture.md), [deployment updates](../Steps/DockerComposeUpdateSystem.md), [scheduling](../Temporal/TemporalScheduling.md), [issue lifecycle](../Workflows/GitHubIssueStatusStateMachineDesign.md)

This is an executable improvement plan, not a claim that the safeguards below
are implemented. Keep durable rules in the linked canonical documents. Archive
this plan after implementation and retain the regression journeys and receipts.

## 1. What failed and why existing safeguards did not suffice

The incident crossed several independent authority boundaries. Repairing the
first boundary did not establish that the user's recurring work could finish.

| Finding | Observed evidence / implementation | Consequence |
| --- | --- | --- |
| Current Temporal route had no matching worker | Effective Build ID began `5247d298`; all installed workers advertised `50d6eb39`. The midnight UTC occurrence retained only execution-start and workflow-task-scheduled events. | Work could be admitted indefinitely without starting. |
| Readiness reported success for an unroutable deployment | `bootstrap_version_routing` returned `awaiting_promotion`; `worker_runtime.mark_ready` still marked pollers started. All seven fleets reported ready. | Healthy containers concealed a product outage. |
| The installed replacement lacked a durable release receipt | Containers used the local Compose file and `latest`; no release submission/job existed before incident recovery. | The qualified updater did not own that replacement. This observation does not identify who or what launched it. |
| A similar regression test already existed | `test_schedule_recovers_when_installed_release_replaces_absent_current_worker` reproduces a two-event schedule history, then calls `promote_version` from the test. | It proves manual repair, not the promised automatic recovery path. |
| Run-now feedback invented enqueueing | `trigger_schedule` can return an empty result; `create_manual_run` unconditionally persists `ENQUEUED`. Live manual rows lacked workflow/run IDs while Temporal reported overlap skips. | Clicking appeared successful without creating work. |
| Cancellation trusted a stale projection | The cancel endpoint returned canceled while a direct Temporal describe still returned RUNNING. A direct terminate closed the exact run. | Force cancellation could be short-circuited by a false terminal projection. |
| Recurring starts omitted recovery evidence | Failed schedule occurrences exposed `original_task_input_snapshot_missing` and no failed-step recovery. | A routine failure could not use the ordinary recovery UI/API. |
| Temporary capacity escaped as fatal input failure | The recovered occurrence reached dispatch, then ended with `OMNIGENT_HOST_CAPACITY_UNAVAILABLE`, `wait_for_host_capacity`, CPU pressure, and a non-retryable parent ValueError. | Successful dispatch still did not mean useful work could finish. The precise retry-budget/host-pressure cause requires its own replay. |
| Remote issue attempts were checked too late | The 08:08:20 and 08:09:53 UTC occurrences selected issues 4269 and 4268. Other deployments' preparing comments existed; new SQL receipts had `announcement_started=true` with no own comment ID. | An ordinary conflicting candidate poisoned the occurrence instead of being skipped safely. |
| Architectural authority contradicted production | Temporal architecture section 18 said Worker Deployment routing was not in use. | Agents reading the architecture could make changes against an obsolete deployment model. |
| Remediation evidence was not a usable runtime input | The 08:21:51 occurrence's fourth remediation had `gateResultRef` and `remainingWorkRef` only in parameters; its host received only brief/assessment attachments. A later Continue-As-New activated attachment forwarding, but explicit local paths were still absent. | Old histories wasted remediation attempts without editing; patch activation was accidentally required for input delivery. |
| A provider outage bypassed durable retry | The same occurrence failed at draft publication when GitHub returned HTTP 500. The adapter returned `created=false`; the workflow classified that result as `user_error`. | Temporal saw a completed Activity, so its configured retry policy never ran. |

The repeated pattern is treating a local success as a completed authority
handoff: process ready as dispatch ready, trigger accepted as enqueued, a
projection as terminal, an adapter result as an unrecoverable parent failure,
and a test-driven repair as autonomous recovery. Adding more retries or more
strict equality checks in each caller will not repair this structural gap.

Incident evidence is retained locally under
`artifacts/incidents/2026-09-13-release-routing/`; the routing recovery and
release records live under deployment-owned `deploy/state/`. They contain no
authority to change provider/model/source intent. Source traceability for the
bounded claim fix is commit `c5118b0400562b7686827a68399201016b6fdc76`.
The reviewed lock-boundary correction is commit
`1a553a1a45bf09770f1f859b6884488ad994531d`.

## 2. Immediate recovery and completion evidence

- Routing recovery qualified the installed release through both user-workflow
  and merge-automation workflow queues, exercising the fleet Activity queues.
  Promotion used the production compare-and-set helper and recorded the prior
  and resulting server routes.
- The stranded occurrence advanced from 2 to 379 history events, then failed
  at a separate capacity boundary. This was partial recovery, not completion.
- The claim-selection fix checks authenticated live comments before candidate
  reservation and under the claim lock before authorizing an announcement.
  Serialization survives the durable intent commit and remote write; competing
  work is neither released nor stolen. Unreadable evidence remains a failure.
  Existing uncertain intents remain pinned. Review reproduced an additional
  pre-POST race in two failing tests before this lock-boundary correction.
- Two original-defect tests failed before the first fix. The expanded suite passed
  260 targeted unit/boundary tests and 20 real PostgreSQL/HTTP/Temporal tests,
  including selecting a usable successor, preserving a remote contender,
  observing a retry blocked on the actual database lock, and canceled waiters.
- A new image was derived from the exact installed image plus the committed
  production-file change, with a new source label and generated release
  manifest. The reproducible Dockerfile is retained with incident artifacts.
  It was published only under its source-SHA tag and installed by the portable
  updater, without moving the shared `latest` channel.
- Release submission `794e6904-44c4-4faa-a776-5459a7deac65` completed with
  retained prior workers, qualified replacement workers/API, CAS promotion,
  and verified existing operator access. The immutable image digest begins
  `0bc4f5af`; the release manifest Build ID begins `a49f486b`.
- Final claim-lock submission `998820d3-4109-4f62-8427-bb8ca9ead071`
  completed at 08:46:36 UTC with source revision `1a553a1a4` and immutable
  image digest `sha256:0207269263b468ba2642604f1463808470ef79bb2692ac7ce2c0e1d6be1dc9c7`.
  Operator dashboard/assets verification passed. The existing recurrence
  continued through remediation and re-verification on its retained cohort.
- PR #4289's required `ci-required` and `migration-gate` checks passed for
  `1a553a1a4`, including the selected reliability and Temporal boundary jobs.
  Those are the actual required contexts in the inspected main-branch
  protection; the independent Code Quality upload failure and its server-error
  retries are separate from those successful checks.
- Product verification requires a real occurrence to complete its intended
  work. The incident receipt in [PR #4289](https://github.com/MoonLadderStudios/MoonMind/pull/4289)
  records the latest observation separately from release success: exact
  workflow/run, terminal outcome, relevant issue/PR evidence, and the next
  scheduled occurrence. A synthetic, skipped overlap, forced empty query, or
  marked-success override cannot satisfy this requirement.

## 3. Durable architecture decisions

1. Extend the existing image-owned release controller as the single promotion,
   installation, rollback, and routing-repair owner. Reuse its durable jobs,
   locks, canaries, CAS, retained cohorts, and cleanup reconciliation.
2. Host its availability supervisor in the existing trusted deployment-control
   service. The supervisor can call Temporal administration and the Docker
   Backend without receiving an application workflow task. It cannot rely only
   on a maintenance schedule on the broken queue. No new idle container.
3. Separate process liveness, candidate qualification readiness, and installed
   product availability. Required routing observations are refreshed, not
   captured once at startup. Preserve candidate qualification before promotion.
4. Keep release identity and semantic compatibility separate. A digest changes
   for an ordinary build; it does not automatically invalidate a task. Upgrade
   code only through replay-compatible histories/readers or retained exact
   workers. Profile/Skill/provider changes use their own immutable contracts
   and proven allowed adaptations. No implicit credential, model, effort,
   billing, repository, or publication substitution.
5. Preserve accepted work through durable waits/continuations and cumulative
   budgets. At terminal boundaries, retain primary results, workspace evidence,
   pending external effects, and issue ownership until their owners settle them.
6. Verify the default public journey and the automatic owner. A green helper
   test, identity probe, or candidate-only canary is supporting evidence only.
7. Bind the supported release channel to verified artifact receipts. Source CI,
   built-image conformance, and live post-install checks are separate gates.

## 4. Bounded implementation stories

### A. Restore recurring issue selection without weakening ownership — P0

**Owner/files:** GitHub integration; `story_output_tools.py`,
`issue_claim_store.py`, `test_issue_claim_journey.py`, and
`test_issue_claim_concurrency_journey.py`.

**Implemented portion:** The committed preflight fix and escaped regression
journeys described above. Preserve its HTTP/SQL tests in required CI.

**Acceptance:** The deployed recurring preset selects an eligible
issue after encountering a remote preparing attempt and completes normally.
Add an explicit unreadable/malformed contender matrix if current adjacent tests
do not establish complete fail-closed evidence. A known conflicting candidate
can be skipped before effects; an ambiguous attempted remote write cannot.

**Dependencies:** None. Keep this bounded incident fix independent of the
broader release supervisor design.

### B. Make supported replacement own routing and recover drift — P0

**Owner/files:** `deployment_release.py`, `release_routing.py`,
`worker_runtime.py`, deployment worker entrypoint, Compose, and portable updater.

**Build:** Extend the existing controller with a read-only observation and
fenced repair operation over desired release, installed images, effective
routes, live pollers, and retained executions. The deployment service resumes
it directly at startup and periodically without depending on application task
routing. Reuse durable submission IDs and budgets, including after restart.
Ordinary authorized repair runs by default; unsupported authority/compatibility
states name the precise owner while preserving any working cohort.

**Acceptance:** In disposable Compose, establish release A, start work and a
schedule, stop A's workers, and introduce B without changing Temporal's route.
Invoke only the production startup/supervisor path. Within 60 seconds it
reports lost routability; within five minutes an available authorized cohort
serves ordinary traffic. Test missing old image, unqualified B, concurrent
promotion, lost promotion acknowledgement, and updater restart. Never disable
versioning or manually call promotion from the test to obtain success.

**Dependencies:** Existing release controller, already present. No new database
or always-on service. Keep Temporal-unreachable recovery distinct from a
missing-worker condition.

### C. Prove product readiness and preserve old work at cutover — P0

**Owner/files:** `worker_healthcheck.py`, executable worker specifications and
grouped roles, `release_canary.py`, `deployment_release.py`, API diagnostics.

**Build:** Derive the required workflow/Activity queue closure from all actual
worker roles, including merge automation and control queues. Observe current,
ramping and pinned versions with live poller freshness. Keep candidate and
serving readiness distinct. Qualify a candidate, then run ordinary unpinned
work after CAS and installed replacement. Refresh observations after promotion.

**Acceptance:** A pinned candidate canary passes while the ordinary route is
broken, and product readiness remains degraded. Wrong-version pollers, one
missing grouped-role queue, stale startup metadata, and a stale proxy cannot
pass. A prior-release execution survives replacement; pinned work retains A,
and an upgrade-compatible workflow progresses on B. Unknown drainage retains
workers. Replay representative nontrivial UserWorkflow/AgentRun histories; an
identity-only canary must not stand in for replay compatibility.

**Dependencies:** B for automatic repair; readiness and qualification can land
first. Avoid a circular gate requiring B to receive normal traffic before it
can pass candidate qualification.

### D. Make run-now and cancellation outcomes truthful — P0

**Owner/files:** recurring service/router, Temporal schedule adapter,
`schedules.tsx`, execution cancel service/router and projection sync.

**Build:** Replace unconditional enqueueing with observed start, explicit skip,
or pending observation owned by reconciliation. Correlate concurrent manual
triggers and lost responses using the supported schedule contract; a single
immediate describe cannot prove a skip. Keep overlap policy unchanged. Force
cancel verifies the exact live Temporal run before a projected terminal-state
shortcut, performs termination first, and reconciles auxiliary cleanup after.

**Acceptance:** Real API/Temporal/browser journeys cover start, overlap skip,
delayed visibility, concurrent clicks, lost responses, unreachable Temporal,
stale canceled projection with a RUNNING execution, and hung cleanup. Show the
blocking occurrence or pending owner. A failed workflow task cannot prevent
forced termination. Assert server terminal evidence, not only response JSON.

**Dependencies:** Existing adapters. Independently mergeable from B/C.

### E. Preserve the complete recurring occurrence recovery contract — P0

**Owner/files:** recurring workflow action construction, UserWorkflow ingress,
canonical execution projection, authored snapshot and checkpoint owners.

**Build:** Make schedule-native starts materialize the same immutable authored
inputs and recovery evidence as API starts through the existing ingress owner.
Do not add a second workflow compiler or bypass the schedule model. Historical
occurrences reconstruct evidence only from their authoritative recorded start
inputs/artifacts, not the latest mutable schedule definition.

**Acceptance:** Omitted/default and explicit-equivalent recurring inputs reach
the same production path. Cause an isolated step failure, then use the public
failed-step recovery operation successfully with preserved source/run, profile,
model, issue claim, workspace and budget. Cover an older recorded occurrence
and a schedule edited after that occurrence started. No fabricated snapshot or
manual database update is allowed in the passing journey.

**Dependencies:** Existing recovery contracts; D provides honest controls.

### F. Keep transient capacity and failed attempts in the recovery loop — P0

**Owner/files:** AgentRun capacity handling, provider failure envelope,
UserWorkflow result handling, runtime admission and issue terminal reconciler.

**Build:** Preserve typed recoverability across dispatch errors, returned
AgentRunResult, child completion, and parent interpretation. Separate queue and
capacity wait from execution budget; prevent three immediate retries from
masquerading as durable waiting. Re-admit the same operation after capacity
returns, honoring stable session/lease ownership and cumulative bounds. On
terminal exhaustion, drive the existing issue finalization owner with verified
writer stop and remote effect evidence. Do not release a claim based on a
timestamp or parent closure alone.

**Acceptance:** Replay the observed host-capacity envelope through the real
AgentRun-to-UserWorkflow path. Hold CPU/host capacity in a disposable substrate,
release it, and observe the same work proceed without a new provider/model or
duplicate host. Cover exhaustion, cancellation during wait, lost worker,
post-admission resource loss, and heartbeat-only progress. A failed attempt's
claim is either released after authoritative settlement or carries a durable
reconciliation owner; the next tick does not repeatedly rediscover it as fresh.

**Dependencies:** E for resuming old failed occurrences; ordinary wait behavior
is independently testable. Do not raise resource thresholds to hide the defect.

### G. Gate supported release artifacts on production journeys — P1

**Owner/files:** `pytest-unit-tests.yml`, `docker-publish.yml`,
`select_test_suites.py`, existing reliability Compose and exact-artifact drivers.

**Build:** Make qualification receipts bind source SHA, exact candidate image
digest, base/prior artifact, required queue closure, tests and outcomes. Build
artifacts may be published for qualification, but promotion to the supported
default channel requires successful receipts for that artifact. A build job's
success alone is insufficient. Retain existing aggregate gate names where
possible; inspect branch protection rather than assuming a named job is required.

**Acceptance:** Relevant release/runtime/schedule/adapter edits select the
journeys. Empty or failed change detection fails open to the required suites.
Missing, skipped, stale-SHA and wrong-digest receipts reject promotion. Test
the actual publish orchestration, including main-push and manual entrypoints.
Required CI uses isolated `moonmind-test-*` projects and no real provider
credentials. Record platform coverage explicitly rather than claiming an
amd64 run qualifies arm64 execution behavior.

**Dependencies:** B–F supply minimized journeys; gate wiring can land before all
new scenarios. Existing CI already has reliability and exact-artifact jobs;
extend them rather than creating parallel test hierarchies.

### H. Make regressions visible and learning enforceable — P1

**Owner/files:** service-health projection/metrics/runbook, release diagnostics,
AGENTS.md, canonical architecture and PR template/gate ownership.

**Build:** Expose dispatch-unavailable duration, oldest actionable queued work,
route/poller mismatch, schedule trigger disposition, last ordinary synthetic,
and recovery owner/phase/budget. Use bounded metric labels; workflow IDs belong
in traces/artifacts. Extend the PR evidence format to name the user journey,
authority handoff, original-defect replay, prior-history case, default inputs,
and release rollback/continuation evidence. Make those fields point to executed
checks, not a prose checklist accepted on trust.

**Acceptance:** Inject route drift in isolated Compose, observe degraded product
state without false process death, see automatic recovery progress, and verify
the alert clears only after real work resumes. A documentation update removes
contradictory current guidance and passes link/architecture checks. No report
may mark recovery complete from a container check or a workflow merely starting.

**Dependencies:** B/C for live recovery evidence. Canonical corrections and
agent rules are authored with this incident and do not wait for all code.

### I. Make recovery inputs and provider retry semantics executable — P0

**Owner/files:** Generic host workspace materialization, artifact projection,
first-message binding, GitHub adapter and `repo.create_pr` Activity.

**Build:** Admit existing named verifier refs at the host boundary, independently
of workflow patch activation. Project paths only after artifact authorization and
verified file writes. Re-admit inputs on a ready workspace without replaying its
checkpoint. Preserve transient GitHub failures as retryable Activity failures;
lookup failure must stop creation, and a subsequent attempt must adopt a PR
whose creation acknowledgment was lost.

**Acceptance:** Replay the actual parameter-only request with shared/separate
verifier artifacts through the real artifact service, workspace materializer,
generic host binding, and first message. Cover fresh and already-ready
workspaces, repeated admission of read-only files, wrong-owner evidence, and
preservation of uncommitted work, and Activity revocation after host readiness
before first-turn delivery. Exercise HTTP 500/503/429, rate-limited versus
permission-denied HTTP 403, provider cooldowns, and transport loss
through the production adapter and Activity; prove the failure is retryable and
that retry observes/adopts the existing PR with exactly one create request.
Retain source/request evidence and observe a real recurring terminal outcome
before claiming the incident resolved. Draft preservation of an incomplete
issue does not establish successful issue implementation.

**Dependencies:** No workflow command-order change or new patch marker is
required for the host and Activity fixes. Broader terminal recovery accounting
and automatic replay remain owned by E/F; this bounded repair does not claim
those stories are complete.

## 5. Required verification matrix

| Journey | Minimum real boundary / fault | Objective completion evidence |
| --- | --- | --- |
| Fresh default install and explicit auto | Real Compose + API + Temporal + artifact backend | Ordinary workflow and schedule occurrence complete; no hidden enablement. |
| Populated A to B update | Production updater + old history + new image | New ordinary traffic succeeds; existing work progresses or remains on retained A. |
| Orphaned current route | Stop A; start B; normal recovery owner only | Detection and bounded repair receipts plus formerly queued run progress. |
| Candidate-only success | Pinned B canary with broken ordinary route | Product readiness remains degraded until ordinary dispatch is proven. |
| Missing queue / stale worker | Real grouped role or Activity poller absent | Exact missing capability reported; no false release success. |
| Interrupted promotion / two updaters | Kill caller, lose response, concurrent CAS | One durable winning release; loser does not overwrite or clean it. |
| Pinned and upgrade-compatible histories | Real Temporal + minimized recorded histories | Compatible replay and correct retained cohort; no unsupported auto-upgrade. |
| Manual schedule actions | Real API + Temporal + browser | Started/skipped/pending shown truthfully; no invented ID or duplicate trigger. |
| Workerless force cancel | Live execution plus false terminal DB projection | Exact Temporal run becomes TERMINATED before auxiliary cleanup. |
| Capacity contention | Actual admission boundary + delayed capacity release | Same work resumes with preserved semantics and cumulative budget. |
| Remote issue contention | HTTP adapter + SQL ownership + real tool entrypoint | Competing work untouched; eligible successor selected; unknown writes retained. |
| Failed-step recurring recovery | Native schedule start + public recovery API | Authored snapshot, issue/remote effects and workspace remain authoritative. |
| Operator ingress | Existing authorized hostname/port/auth + baked assets | Health, dashboard/assets, and read-only API verified before/after. |
| Release channel promotion | Real gate/publish orchestration with wrong receipt | Wrong SHA/digest/skipped check cannot qualify default release. |

Use the existing `./tools/test_unit.sh --python-only <targets>` command for
targeted Python verification and `--ui-args` for targeted frontend tests.
For real service boundaries, use the existing reliability Compose definition
with an explicit test-only project and run the affected
`tests/integration/reliability` journeys. Managed agents submit these workloads
through the Docker Backend. Keep diagnostics, JUnit and sanitized server history
as artifacts; tear down test-owned services automatically.

## 6. Rollout and acceptance

1. Complete and verify the bounded live recovery; retain the exact immutable
   recovery image and release receipt. Observe one real successful recurring
   occurrence, including its business outcome, before claiming the incident fixed.
2. Land A and the canonical/agent-rule corrections. Preserve all escaped
   regression tests in the existing required CI selection.
3. Implement B/C and D in bounded changes; qualify against a disposable populated
   prior release before production. Keep the old release available throughout.
4. Implement E/F against the recorded occurrence and capacity failures. Verify
   terminal issue/claim settlement and useful continuation, not only routing.
5. Complete G/H and exercise the full matrix against the exact release artifact.
   Enable the routine supervisor and synthetics on the default path. Genuine
   authority overrides remain explicit and documented.
6. Close the implementation effort only when the public default journey survives
   replacement and the fault matrix without operator rescue. Archive this plan;
   keep canonical contracts, required regression journeys and durable receipts.

Each implementation story must report code shipped, checks executed, remaining
gaps, and release state separately. Missing verification calls for preparing
the isolated environment or implementing the missing fixture. It is not a
routine request for human testing or permission. A real missing credential or
unauthorized side effect names the exact restriction while independent work
continues.
