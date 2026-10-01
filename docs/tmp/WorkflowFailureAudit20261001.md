# Workflow failure audit — 2026-10-01

**Document Class:** Imperative working document\
**Working Type:** Status / Checklist Tracker\
**Status:** Investigation complete; MoonMind remediation implemented, verification in progress\
**Canonical Target:** [PR Merge Automation](../Workflows/PrMergeAutomation.md), [GitHub PR Resolver](../Steps/SkillGithubPrResolver.md), [Docker Backend Service](../ManagedAgents/DockerBackendService.md), [Omnigent Lifecycle Reconciler](../Omnigent/OmnigentLifecycleReconciler.md)\
**Delete/Archive Trigger:** Archive after the findings have been resolved or incorporated into their existing implementation owners.\
**Source:** Operator request to investigate the preceding six hours of workflow failures, especially Tactics PR resolution.\
**Authority:** Observed evidence and proposed follow-up work; this report does not change publication, merge, review, or deployment policy.

## Scope and result

The fixed audit window is **2026-10-01 10:25:13–16:25:13 UTC**, equivalent to **03:25:13–09:25:13 PDT**. Root workflow closure timestamps determine inclusion, so some included executions began before the window. Later GitHub observations, through approximately 16:49 UTC, are marked separately.

Seven root workflows closed in the window: **five failed and two completed**. Four failures were Tactics `pr-resolver` runs; the fifth was a MoonMind UI task. The two completed roots were the deployment update and another UI implementation task. Completion here describes the recorded workflow state, not independent verification of their output.

All four failed Tactics resolvers ended with `attempts_exhausted` / `timeout`. Their finalization histories contain 48–54 observations, mostly waiting for external readiness. Three retained and pushed repairs. The workflows are expiring while CI or external status delivery is still outstanding, and subsequent CI has also found real frontend failures on two candidates.

| Target | Root workflow | Closed UTC | Retained candidate | Failure in the audited execution | Later GitHub observation |
| --- | --- | --- | --- | --- | --- |
| [Tactics #2770](https://github.com/MoonLadderStudios/Tactics/pull/2770) | `mm:a635c817-d414-52a7-a580-4d13adeffd8c` | 10:29:20 | `229fdec0e106` | 49 gate observations; CI waiting, then degraded signal on the pushed head; timeout | Preflight did not start until 16:32:35; it passed. Unreal job remains queued. |
| [Tactics #2771](https://github.com/MoonLadderStudios/Tactics/pull/2771) | `mm:fff39f44-9d07-5420-bd8d-a370587b98fd` | 11:40:27 | `be5bbdcb4c09` | 48 observations; CI repair and conflict resolution were pushed; timeout while CI was outstanding | Unreal CI failed the Demo LAN reset journey at stage 2. CI Gate subsequently failed. |
| [Tactics #2774](https://github.com/MoonLadderStudios/Tactics/pull/2774) | `mm:b5aca59a-f1d7-5418-ab11-989ff5ff5cb4` | 12:57:33 | `9f500e92c512` | 52 observations; conflict resolution pushed; timeout before preflight began | Unreal CI later failed typed coordinator reset delivery and the Demo LAN journey. |
| [Tactics #2776](https://github.com/MoonLadderStudios/Tactics/pull/2776) | `mm:fdd74df0-4713-576a-b56d-a8024243652c` | 13:51:28 | `d6d116824e4f` | 54 identical `external_state_transient` observations; two-hour wait exhausted | Unreal CI and CI Gate passed on this head; both GitBook statuses remain pending from September 29. |
| MoonMind workflows action-menu task | `mm:0f79a1ef-6697-4e2e-929b-be08bcea8714` | 12:38:31 | No verified candidate or checkpoint | Omnigent activity hit its six-hour StartToClose timeout | Journal shows only heartbeats after 06:39:31, with no verified completion. |

All four Tactics PRs remained open at the follow-up observation. [#2777](https://github.com/MoonLadderStudios/Tactics/pull/2777), candidate `2aee71f26998`, was still active and must not be counted as a fifth failed Tactics root in this window. Its Python/Bash and PowerShell checks passed; Unreal preflight and one GitBook status were outstanding.

## 1. Give external PR waits to the existing Temporal owner

**Highest impact MoonMind change.** The batch submitted ordinary standalone `pr-resolver` tasks, with no admitted MergeAutomation context. Diagnostics explicitly recorded `prResolverMergeGateOwned=false`. The portable resolver correctly refuses to manufacture a successful durable continuation for a standalone invocation; it stays in foreground polling until its bounded elapsed budget expires.

The current default elapsed budget in [`pr_resolve_orchestrate.py`](../../.agents/skills/pr-resolver/bin/pr_resolve_orchestrate.py) is 7,200 seconds. PR #2771 consumed 8,181 seconds including work around gate checks; #2776 consumed 7,343 seconds. Polling observations are not independent code-repair attempts.

The `codex_openai_oauth` Provider Profile has one parallel slot. Foreground external waiting occupies that slot; Tactics #2778, #2780, and #2781 were already waiting for provider capacity from approximately 08:13 UTC. Raising OAuth concurrency blindly would conflict with the existing exclusive-account constraint.

Extend the existing batch producer in [`batch_pr_resolver.py`](../../.agents/skills/batch-pr-resolver/bin/batch_pr_resolver.py) to use qualified existing-PR adoption through the existing [MergeAutomation contract](../Workflows/PrMergeAutomation.md). Temporal should own the external wait and invoke the same resolved portable Skill when readiness or concrete remediation requires an agent. Preserve repository/PR identity, saved repairs, current-head evidence, cumulative budgets, and the admitted finish intent. Batch resolver intent is `merge`; adopting the existing Fix and Review Loop preset's default `fix_only` would change the requested outcome. That preset also requires fresh automated reviews, while these batch inputs did not; reuse the lifecycle without silently adding a new review policy.

Proof for implementation: extend `tests/unit/test_batch_pr_resolver.py` and the existing merge-automation verdict-routing tests, then exercise `tests/integration/workflows/temporal/workflows/test_merge_automation_full_topology.py`. The acceptance scenario should demonstrate that an external wait releases the provider slot, survives restart, resumes the same candidate, and reaches the admitted merge outcome after current-head checks pass. Changed workflow command sequences also need replay evidence or a controlled migration.

This continuation is proposed. No new owner has accepted these failed runs and no follow-up execution was queued during the audit.

## 2. Separate lightweight GitHub jobs from Unreal runner capacity

**Highest impact Tactics configuration change.** Current main routes preflight, Unreal build/tests, CI Gate, asset-tool tests, and the auto-merge guard to `[self-hosted, Linux]`. Every observed executing job used `asus-laptop`; queued jobs had no assigned runner. The organization runner inventory was inaccessible, so this does not establish that only one runner exists.

Measured delays exceed resolver budgets:

- [#2770 run 36843787515](https://github.com/MoonLadderStudios/Tactics/actions/runs/36843787515): preflight created at 10:14:03 and started at 16:32:35 — **6 hours 18 minutes** in the queue for a job that then ran for 77 seconds.
- [#2774 run 36850406982](https://github.com/MoonLadderStudios/Tactics/actions/runs/36850406982): preflight waited **2 hours 29 minutes**, then Unreal waited another **2 hours 31 minutes** before running.
- [#2771 run 36853794076](https://github.com/MoonLadderStudios/Tactics/actions/runs/36853794076): preflight waited almost two hours, followed by another 42-minute Unreal queue.

A 16:36 observation found 35 queued runs: 12 build/test workflows and 23 Disable Auto-Merge workflows. That is a later queue snapshot, not 35 failures in the audit window. The guard runs after unsuccessful or canceled workflows and performs a short GitHub API operation, so using the Unreal runner for it adds avoidable contention.

Keep every Tactics CI workflow on self-hosted runners, as explicitly required by the operator. A separate lightweight self-hosted runner queue could serve the GitHub-script guard and eligible short jobs while qualified prepared self-hosted runners execute Unreal. Preflight and CI Gate currently use the pinned control-tool image and artifact contract; preserve those requirements when moving them. Measure queue latency and add qualified heavy capacity or bound admission if it remains excessive. Extending an agent's polling timeout alone would retain the slot bottleneck.

Proof: repository workflow/static checks plus CI on a coherent Tactics revision. Verify the normal PR journey starts its short jobs promptly, preserves the control-tool and artifact integrity checks, runs Unreal, and produces the final CI Gate without needing a free Unreal executor for the GitHub-script guard. Do not infer success from a helper test alone.

## 3. Repair GitBook delivery and make the portable readiness evidence coherent

[#2776](https://github.com/MoonLadderStudios/Tactics/pull/2776) demonstrates a distinct blocker: its Unreal job and CI Gate passed before the resolver began, but two current-head GitBook contexts have been pending since **2026-09-29 09:35:16 UTC**, still described as updating content. There were no actionable review comments in the captured resolver snapshot.

There is also a portable classification defect to investigate:

- [`pr_resolve_snapshot.py`](../../.agents/skills/pr-resolver/bin/pr_resolve_snapshot.py) replaces running/failure flags with the REST current-head check-run summary. Legacy commit statuses, including GitBook, are not check-runs. The saved #2776 snapshot consequently reports `ci.isRunning=false`.
- [`normalize_portable_snapshot`](../../pr_resolver_core/normalize.py) treats aggregate `mergeStateStatus=UNSTABLE` as unknown mergeability even when GitHub separately reports `mergeable=MERGEABLE`.
- The classifier therefore emits `external_state_transient` repeatedly, obscuring the stale status contexts actually visible in the PR rollup.

Read current-head check-runs and legacy commit statuses into one coherent readiness observation. Distinguish actual merge conflicts, check readiness, status-delivery failures, and unknown required-check policy. Keep this logic in the existing portable Skill/core; do not add another MoonMind-native PR classifier.

Repair the GitBook integration, or explicitly retire an unused integration and its merge criterion through repository policy. Required checks must remain enforced. Branch-protection/ruleset queries returned 403 and `requiredChecksKnown=false`, so the audit cannot certify whether GitBook is required or advisory and does not recommend silently bypassing it.

Proof: add behavior cases to `tests/unit/test_pr_resolver_tools.py` and `tests/unit/test_pr_resolver_core.py` for current-head legacy statuses, required versus advisory contexts, unavailable policy, and `MERGEABLE` plus `UNSTABLE`. Verify that a stale required context remains blocked with a useful reason and that a confirmed advisory context does not consume the whole repair budget. Exercise the resulting gate through the existing integration topology.

## 4. Make the managed Unreal fallback actually reach test execution

Two managed container jobs attempted during these resolutions failed **before running a test**:

| Target | Container job | Acquisition interval UTC | Observed outcome |
| --- | --- | --- | --- |
| #2770 | `container-job:69cceb93ad8a42a0a2dab76a247f9210` | 09:38:10–09:43:11 | `container_job.acquire_image` ScheduleToClose timeout |
| #2771 | `container-job:683f74be583d4e9fb8aabf9b690b18f2` | 10:58:54–11:03:55 | Same five-minute acquisition timeout |

The first is supporting evidence from before the root-closure window. Both submitted a direct pinned GHCR Unreal base image with no `imageSourceRef`. The configured workload timeout was 900 seconds, but the direct-image acquisition activity has a separate 300-second budget. There is no test exit code and `testExecuted=false`. The captured records do **not** distinguish a slow download, registry access problem, or another acquisition stall.

[`container_job.py`](../../moonmind/workflows/temporal/workflows/container_job.py) already gives deployment-owned image sources a longer bounded acquisition/build budget and deliberately preserves old direct-image command shapes for replay. [`container_job_backend.py`](../../moonmind/workflows/temporal/container_job_backend.py) publishes pull diagnostics after the pull returns, so a timeout can leave only a generic Temporal error.

Continue the existing [Tactics #2777 / issue #2740](https://github.com/MoonLadderStudios/Tactics/pull/2777) owner for the managed validation path. Use the deployment-owned `tactics-unreal` source and supported container-job service rather than inventing host Docker execution inside a managed workflow. Ensure the actual test workspace has its Lore-locked content; retained logs also reported missing `MainMenu.umap` and `VikingDemoDay.umap`. The existing [#2776](https://github.com/MoonLadderStudios/Tactics/pull/2776) owns relevant Lore integration work.

For MoonMind, make image acquisition progress and original errors durable while the pull is running, give legitimate slow pulls a measured bounded budget, and reconcile uncertain pull completion before retrying. Any direct-image timeout change must protect retained Temporal histories. A larger timeout is insufficient evidence that registry authentication or content materialization works.

Proof: targeted container backend/workflow tests for slow acquisition, progress diagnostics, cancellation and uncertain completion; replay protection for changed activity commands; then the existing container-job integration journey plus a real managed Tactics test job with the locked assets present. An image build or wrapper syntax check alone does not prove Unreal validation ran.

## 5. Resolve the actual frontend failures on the saved PRs

The latest CI failures are concrete behavioral failures, not suitable for an infrastructure-only rerun shortcut:

- [#2771 job 110391483470](https://github.com/MoonLadderStudios/Tactics/actions/runs/36853794076/job/110391483470), candidate `be5bbdcb4c09`: at 14:46 UTC, `Tactics.Demo.Integration.HomeLanRoundResetChangeMode` failed with **“Demo LAN route timed out at stage 2.”** The earlier failure had been destination adoption at first planning enablement; the pushed repair did not establish a passing journey on the final candidate.
- [#2774 job 110388617339](https://github.com/MoonLadderStudios/Tactics/actions/runs/36850406982/job/110388617339), candidate `9f500e92c512`: at 16:00 UTC, `Tactics.Integration.Frontend.TypedCoordinatorSession.keeps ordinary launch readiness local without publishing reset starts` failed because the canonical reset start never reached the sink (observed count zero). At 16:29 UTC, the Demo LAN test also failed its assertion about initial adoption completing without a Next Group timeline. The latter event and final job conclusion occurred after the fixed audit window.

#2770, #2771, and #2774 touch overlapping frontend adoption/reset code or tests. Reuse their retained repairs, reproduce the failing boundary in the owning PR, and validate the integrated behavior as main and PR heads change. The current evidence identifies the failed assertions, not a fully established common code-level root cause. Do not weaken the tests or treat all of these failures as quota/platform errors.

Broader verification should use the existing Unreal CI journey on each actual candidate. Focused reproduction should run the named typed-coordinator and Demo LAN reset tests through the supported managed container route once acquisition/content are repaired. Preserve required rendered evidence on the PRs that own it. Existing MoonMind [PR #4645](https://github.com/MoonLadderStudios/MoonMind/pull/4645) addresses infrastructure-only CI failures; avoid duplicating that work or applying it to these behavioral errors.

## 6. Recover semantic stalls and preserve truthful failure evidence

The MoonMind action-menu workflow used Omnigent with the `opencode-go-default` profile. Its `integration.omnigent.profile_bound_execute` activity ran from **06:38:30 to 12:38:30 UTC** and reached the six-hour StartToClose limit. There was no usable checkpoint or verified deliverable in the Step projection.

The persisted journals contain 1,455 entries: **1,437 heartbeats and 18 other events**. All non-heartbeat events occurred by **06:39:31 UTC**. The workflow kept heartbeating as active for almost six hours without further observed semantic progress. A completed response event early in the journal belonged to a different response than the later active turn; it is not proof that the requested work completed. The bridge row was subsequently marked completed without terminal output references, which also does not prove task success.

Extend existing Omnigent reconciliation in [`execute.py`](../../moonmind/omnigent/execute.py) so a stale active snapshot cannot indefinitely postpone a bounded semantic-progress check. Track meaningful progress separately from transport liveness; consult authoritative turn/session evidence before interrupting or replaying; recover through the existing lifecycle with the saved workspace and journal. Expose the original stall, last meaningful event, recovery attempt and unavailable checkpoint in diagnostics. The trace does not identify why the provider stopped progressing, so a model replacement alone is not an evidence-backed fix.

Proof: extend `tests/unit/omnigent/test_execute.py` and the fake-server integration tests with continuing heartbeats, a stale active snapshot, no semantic progress, and bounded reconciliation. Include a slow but progressing tool scenario to avoid falsely interrupting legitimate work, and prove saved work survives recovery.

PR resolver failure receipts need a smaller observability correction too. Some final projections show `push_status=failed` or a failed publication phase even though the saved candidate and remote head confirm a successful earlier push. Preserve confirmed push receipts and report the unresolved CI/status gate as the terminal blocker. A reporting failure must not erase the completed publication phase. The four failed resolver workspaces remained recoverable in this audit.

## Evidence, limits, and next authorized work

Sources were the live execution API, retained Step diagnostics and resolver workspaces, Temporal histories, persisted Omnigent journals, GitHub PR/check/status APIs and job logs, and current repository code/configuration. The execution list covered the window; filtering used timezone-aware UTC timestamps locally. The API response did not narrow when finished-time parameters were supplied, so those parameters were not used as proof of inclusion.

MoonMind checkout and installed revision were `ef04a847bfd809c9e11cc556e270a270a5bc7a60`. The host Tactics checkout was old and dirty, so Tactics configuration evidence came from GitHub current main and the exact candidate revisions. Unrelated local edits and submodule changes were preserved.

During the investigation, read-only replay of the captured portable snapshots reproduced the wait/degraded classifications, including #2776's `external_state_transient`. The investigation made no production changes. Raw journals and private workflow prompts were excluded from this report.

The operator subsequently authorized one unified implementation PR and required **all Tactics CI to remain self-hosted**. The MoonMind change now:

- Submits batch resolutions through the existing qualified PR-adoption preset and MergeAutomation owner, preserving merge intent, requested merge method, portable repair budget, Provider Profile, model and effort. Batch adoption uses existing review evidence without adding a fresh-review requirement.
- Returns admitted external waits to Temporal rather than retaining an agent slot. Standalone portable invocations retain their existing bounded polling behavior.
- Reconciles current-head Actions check-runs and latest legacy statuses together. A subsequent successful branch metadata read explicitly reported Tactics `main` as `protected=false`; this authoritative observation permits advisory GitBook statuses to remain visible without becoming merge blockers. A 403 alone still cannot establish an empty requirement set, and unknown policy remains conservative. Actual CI failures and known required or missing contexts remain blockers.
- Separates image acquisition from workload execution with a bounded 1,800s acquisition / 2,100s total window for fresh workflows, protected by a Temporal patch. Live redacted pull diagnostics survive cancellation and acquisition failure.
- Detects semantic stalls under the existing execution budget, preserves their progress clock and diagnostics, and uses the existing bounded same-session recovery owner only after a confirmed inactive projection. The compiled session-interruption policy governs interruption authority.
- Preserves verified repair-push receipts and archived confirmed publication evidence when PR resolution or reporting remains unsuccessful.

Targeted regression tests cover these producers and consumers, with current and retained container histories replayed. The full MergeAutomation → UserWorkflow → AgentRun test exercises a durable CI wait and provider-slot release before another resolver pass, plus parent-history replay. Broader affected suites will run in CI on the unified PR. Real managed Unreal acquisition, registry authentication, locked-content materialization and hardware validation remain unverified by these credential-free tests.

The remaining Tactics work is to separate short jobs onto **qualified self-hosted capacity**, diagnose GitBook delivery itself, and continue #2771/#2774 from their retained candidates using the failed tests above. Runner inventory/administration was unavailable, so no unprovisioned labels or capacity changes were introduced. Existing #2777/issue #2740 and #2776 remain the owners of managed Unreal validation and Lore integration. These are identified next actions, not scheduled continuations; no production workflow was restarted, deployed or merged by this implementation.
