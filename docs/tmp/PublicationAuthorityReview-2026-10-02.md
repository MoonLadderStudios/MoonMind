# Publication and tool authority review

Reviewed: 2026-10-02. Scope: current single-operator application and scoped machine
callers. This records local implementation evidence and open work; it does not
claim deployment or live-provider qualification.

Owning contracts: [Workflow Publishing](../Workflows/WorkflowPublishing.md),
[Step Review Gates](../Workflows/StepReviewGateSystem.md),
[PR Merge Automation](../Workflows/PrMergeAutomation.md), and
[existing workflow follow-ups](WorkflowAuthorityFollowups-2026-10-02.md).

## Repaired boundaries

- `caabf49a415c8191a6214345ebcac5d2` and
  `4fb5c08cf19481918567fdf06229dc67`: the managed launcher and runtime planner
  derive source repository and starting branch from the structured target used
  by readiness. New conflicting workflow branch aliases are rejected. Legacy
  requests without a structured target and recorded pinned-candidate semantics
  remain supported. Tests observed incorrect clone sources/Lore identity and API
  acceptance before the fix; 106 selected launcher/planner/API cases passed.
- `66bb2b2bae048191a22c0724a8740593`: title fallback requires the matching
  MoonSpec Skill. Unrelated, unspecified, or generic `auto` Skills cannot become
  remediation merely through their title. Compiler-authored annotations remain
  the normal route, and retained histories keep their recorded interpretation.
- `a86628ad801c8191afdad012c86f94b1`: a manifest-recorded workspace-policy launch
  rejection now obeys `FAIL_FAST`. Explicit `CONTINUE` still permits independent
  work while dependencies remain blocked. This intentionally does not add a
  blanket global stop or discard the failed attempt's evidence.
- `b8dd8927fb008191a3756cb6308a4e85`: a merge conflict no longer skips required
  check/review observation. The existing classifier still treats conflicts as
  resolver-actionable after other gates permit it. Running or unavailable checks
  and pending required reviews remain blocking. Active review-loop and disabled
  gate semantics are retained. 143 adapter/parent-merge and 44 gate cases passed.

All 457 tests in the affected run integration, bounded-loop, and step-ledger
files passed after the changes. Versioned workflow patch boundaries cover the
new title and failure-policy interpretations; this is not a claim that a retained
production history was replayed against a Temporal server.

## Direct tool and Jira-only paths

`9a01fcaa299881919062b0d6360ba831` and
`4d7d8b45e1788191bbc9137235600a03` are directly mitigated by the integrated
canonical dispatch and machine-capability checks. A pinned tool cannot replace
its canonical executor/capability binding. A bounded child-create principal
cannot invoke `moonmind.ops_diagnose_stack` or deployment control by submitting
remediation policy flags. Regression coverage includes both administrative tools,
permitted and denied machine grants, and spoofed remediation context. This is a
specific dispatch result, not proof for every tool's downstream target contract.

`4c90a57201bc8191aaf464c38295ed70` uses a retired Codex queue/allowlist premise.
The supported Temporal planner compiles a Jira-only updater to `publishMode=none`
without a work branch or commit instruction, even when its outer request says
`pr`; the current executable regression covers that behavior. A composed workflow
can still explicitly request repository publication. Reintroducing a Jira Skill
exception/allowlist would contradict that composition contract.

## Open: managed branch publication and reconciliation

Records: `c5ab681706cc81919968de0d1502e511`,
`249b0f03f3888191947d4e7d0fdae9c4`,
`eca6d004d8c88191a6381a92cc59a9ad`.

The surviving `TemporalAgentRuntimeActivities._push_workspace_branch` owner still
accepts a requested head, resets a local branch, derives its lease from an
agent-mutable remote-tracking ref before a live fallback, and can fetch/rebase
following a lease conflict. Surviving consumers include legacy managed result
publication, terminal-checkpoint preservation, and publication recovery. The
shared Omnigent publisher already has live observation and fast-forward checks
for authored branch publication; its guarantees cannot be attributed to the
older helper without tracing the selected runtime.

Do not remove recovery, require all branches to use a new prefix, or silently
change an explicitly selected destination. Extend/consolidate the existing
publisher around its admitted repository/head/base and original remote
expectation. Prove rewritten-history rejection, exact branch preservation,
remote movement/deletion, existing-head reconciliation after lost acknowledgement,
and retry of the same preserved candidate. A retry may reconcile authorized
concurrency; an unreviewed changed candidate cannot inherit the prior acceptance.
These records remain open; this pass did not execute a remote mutation.

## Open: accepted publication evidence and reviewed repair

`98930532f4988191968df2e0bc071ea2` shares the existing same-repository publication
provenance gap documented in WorkflowAuthorityFollowups. Free-form diagnostics can
still supply a PR-shaped URL. Exact repository merge checks stop cross-repository
confusion, but do not attest that a same-repository PR belongs to this task.
Consume the existing publisher's accepted atomic target/candidate evidence while
preserving provider-native and explicitly adopted existing PRs.

`ba3dda037e6881918f78a452d0ad0e7f` remains open. The one-shot publish repair calls
`MoonMind.AgentRun` directly, accepts `COMPLETED`, and returns its changed result
without the source step's review loop. It must reuse the existing gate owner and
source acceptance policy, retained candidate, evidence identity, and consumed
budgets. Do not simply disable agentic repair or add a second approval system.
Automated acceptance must cover passing, non-passing, malformed, unavailable, and
lost-ack review outcomes, along with explicit disabled policy and retained
histories. No claim of end-to-end repaired publication is made here.

## Open: compilation and machine identity propagation

`5546656f75a88191b38032dc2a89edc5`: normal UI/workflow-envelope authoring enriches
publish metadata from the deployment catalog, and fan-out cannot use direct
execution-shaped requests. The raw operator/legacy contract still accepts
self-declared Skill auto-publish metadata. Its existing unit test demonstrates
that contract behavior, but it does not establish a scoped-machine exploit.
Unify new admission through the existing trusted compiler while preserving pinned
custom Skill definitions and retained histories. Do not replace it with a
built-in-name-only allowlist.

`548bfe75c40081918eac239bf88c281e`,
`d673f15f61748191a05885eb68d2c4db`, and
`e35e7173b7648191bc1303871fba8706`: the old cross-human SYSTEM-owner premise no
longer establishes escalation: new operator work intentionally defaults to
instance ownership. The native story/Jira child creators still read legacy owner
fields and do not carry the transport-admitted `executionPrincipal` through their
shared creator. Keep the separate machine-lineage question open. The next proof
must traverse an admitted bounded machine workflow, native child creation and
preset expansion, then an attempted privileged grandchild tool. If reproduced,
carry/attenuate the existing principal through the shared creator without adding
human roles, a new ownership framework, or denying normal operator fan-out.

`3b32f47b6558819199674866eb868388`: likewise, reading an instance-owned target is
not a cross-human leak in the current product. The envelope admits remediation
metadata and the existing target check is owner-based, so separately verify a
scoped machine's authority over the target and evidence before closing this
record. Reuse current execution visibility and remediation admission, prove an
unrelated-run denial and legitimate parent/child observation, and retain existing
context/artifact producer checks. This pass did not claim an end-to-end machine
log disclosure.

## Open: repository story imports

`909334857e4081919ea4d4ccb308ccda`: artifact-backed handoff has an existing safe
preferred route, but ordinary story import still supports repository/ref/path
fetching. This pass repairs the default fetcher's missing repository argument so
the existing credential resolver applies repository-scoped selection. Tests cover
authenticated and public import transport without making network requests.
Task-bound target/path authorization remains a separate open question; this
narrow repair does not close the whole record. Preserve intentionally authored
operator imports and do not impose a new hard-coded `docs/tmp` path restriction.
