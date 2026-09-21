# Omnigent Conformance and Live Smoke

**Document Class:** Canonical declarative  
**Status:** Desired verification policy with existing executable consumers still requiring migration  
**Updated:** 2026-09-21  
**Authority:** Verification scope and evidence for the shared Omnigent runtime, native interaction, and recovery. Runtime selection and action authority stay with their providing modules.

Related: [AGENTS.md](../../AGENTS.md), [Primary Runtime Provider Strategy](PrimaryRuntimeProviderStrategy.md), [runtime selection and transition](RuntimeProviderRollout.md), [Workflow Remediation](../Workflows/WorkflowRemediation.md), [Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md), [review and continuation](../Workflows/StepReviewGateSystem.md), and [deployment updates](../Steps/DockerComposeUpdateSystem.md).

Verification must demonstrate the behavior claimed without becoming a second orchestration platform or an unrelated source of production outages. Reuse existing tests, runners, reports, and actual module owners. A useful small fix does not wait for every harness, optional host mode, provider account, or full release matrix.

This document revises the earlier blanket exact-combination and protected-runner obligations. It does not disable current enforcement or claim that the revised runtime behavior is implemented. The [earlier full contract](https://github.com/MoonLadderStudios/MoonMind/blob/6fdaab848e8f9fd9c5279ea36186482cab05733d/docs/Omnigent/ConformanceAndLiveSmoke.md) remains available for exact historical case IDs, report fields, and operational details while their real consumers still need them. Change those consumers coherently, preserving meaningful security and recorded evidence.

## Existing owners and current baseline

At reviewed main `6fdaab848e8f9fd9c5279ea36186482cab05733d`, the shared-host conformance module explicitly contains pure inventory/validation logic and pending live rows. The remediation matrix distinguishes action delivery from repair and keeps autonomous mutation closed. Those are implemented foundations, not full product verification or an instruction to create more catalogs.

| Responsibility | Existing owner to reuse |
| --- | --- |
| Focused bridge, API, and frontend behavior | Existing unit, integration, browser, and conformance fixtures. |
| Deployable-image behavior | `tools/run_omnigent_exact_artifact_conformance.py` and its current CI job. |
| Provider-controlled live observations | `tools/run_omnigent_live_conformance.py` and the protected workflow/action-adapter boundary. |
| Existing aggregate report format | `tools/build_omnigent_conformance_report.py` and the owning schema/validator. |
| Useful generic runtime verification | #3832, consuming the actual providing feature tests. |
| Recovery/action verification | #3626 with #3510, #3621, #3622, and #3624. |
| Shared-provider accounting and release | Existing ProviderProfileManager/lease owners, #3882 and #1089. |
| Save, restore, and publication | #4014–#4018 and the existing artifact/workspace/publisher owners. |
| Removal of overlapping selection/retirement gates | #3833, #3931, #3932, and #3925. |

These links are not a serial approval pipeline. Completed feature tests can supply evidence to several consumers. A coordinator should not run another copy of every child suite or build a permanent ledger of claim completion.

## Terminal evidence

A result must identify what ran, what it observed, and what remains unknown. Reuse the current result schema and artifact references. Keep the tested source/artifact, relevant harness/credential/protocol boundary, scenario/input, observed outcome, and required saved-content references. Record the actual immutable image used, not a mutable selector or another artifact's identity.

Evidence may establish different facts: a parser accepted a payload, a real process started, a served browser used the admitted API, a saved workspace restored after host removal, or an authorized live provider answered. Do not promote one into another. A self-asserted success field, fake host identifier, helper result, or installed CLI is not a complete journey.

### Reuse without relabeling

Use current-candidate CI for affected code. Previously collected evidence can support an unchanged boundary only with a clear explanation of its scope and relationship to the candidate. Do not rewrite its source SHA, image, timestamp, or subject to make it appear new. Evidence from a different credential or session protocol does not establish the changed protocol.

Shared mechanisms can share representative integration tests. A new alias or prose edit does not require the same provider/browser/host matrix as a changed OAuth materializer. A substantive new credential, storage, or network boundary needs its relevant tests. This is not permission to drop a still-required behavior or rename an unfinished required capability as unsupported.

### Honest partial results

Missing mandatory evidence, a failed case, cancellation, unexpected skip, truncated output, or unavailable service remains explicit. A partial report is not a full qualification pass. Use existing supported runner modes for a narrower observation and state their scope. Do not use `--allow-partial`, edited expected-case lists, or removed assertions to turn an incomplete requested journey into success.

Current consumers of `tests/fixtures/omnigent/conformance-v4.json` and its report schema still enforce their actual version/case rules until changed together. Retain original decoding for stored reports. The old full inventory is not an obligation to run every case for each small change or to add another report/schema family. A concise summary can link existing evidence rather than duplicate it.

### Failure preservation and confidentiality

Preserve confirmed compute, saved content, and remote effects when later verification or reporting fails. Retry only the unfinished evidence/implementation phase through its existing owner. A repaired report must not erase recognized failures or invent observations. Missing local tooling calls for an authorized existing CI/container route, not routine human signoff or another implementation attempt.

Bound and redact evidence before ordinary storage/publication. Keep raw credentials, cookies, private configuration, and sensitive source out of public logs and reports. Use existing artifact access policy for legitimately required restricted material. Hashing a secret-bearing file does not make publishing its contents safe. Scan the final publication tree after the last report/cleanup output is added, without introducing another scanning engine.

## Live-run boundaries

The normal product journey starts at the real served Create/Workflow Detail and public admission path. It carries the selected Runtime/Profile, actual model/cost/privacy policy, authorized source, and publication intent into session execution. A lower-level adapter test remains useful but must not be called that whole journey.

Browser coverage belongs at the real interaction boundary. It proves rendered native conversation, scoped requests, relevant follow-up/control, visible errors, and readable results. A loaded iframe or a route-shaped string is not enough. It does not follow that each low-level validation or permission-denial test needs a separate complete browser/provider run.

### Provider and host scope

Credentialed live runs use an already authorized environment and selected Profile. Do not enroll another account, copy an OAuth home, use untrusted-fork secrets, or switch billing routes to get a green result. Credentialless services are still external dependencies, with availability and data-use constraints that must be reported honestly.

On-demand shared-host behavior is the primary path. Optional static operation is tested only where it remains a supported claim or has actual retained consumers. Images may be shared across harnesses. Record what each scenario actually used; do not require separate image names or independent promotion infrastructure merely to preserve an old report layout.

The existing live runner uses `MOONMIND_OMNIGENT_ACTION_COMMAND` where its contract calls for an external action adapter. The adapter must execute real authorized actions and provide independently resolvable observations, not generated success payloads. Keep existing subject/digest/reference verification at this boundary. Do not build another orchestration framework solely to populate a matrix. An unavailable adapter means the live observation has not run, not that all repository implementation is defective.

### Recovery and cleanup

Use existing real storage and runtime journeys to verify interrupted work, lost acknowledgments, restoration, and publication recovery. Demonstrate preserved bytes and invocation counts. A C0-to-C1-to-C2 recovery claim needs actual cumulative content, with the old source removed only after C1 is durably saved. A repeated successful agent run is not restoration.

Keep session reattachment distinct from workspace restoration. Restored files do not revive old leases, credentials, approvals, or permission to repeat remote effects. A failed source remains failed even when a corrective branch produces a verified result.

Stop actual credential consumers and record the safe release decision before reusing their authority. Release of model capacity need not wait for unrelated non-sensitive retention or optional reporting after consumers have stopped. Do not enforce a ceremonial rule that release must be the final loggable event. A static host that still consumes an OAuth home is not safely released merely because no turn is queued.

Cleanup is bounded and limited to positively owned test resources. Preserve Profile-owned credential homes, unrelated volumes, and the only recoverable workspace. A failed capture cannot authorize destructive teardown. Hard process loss may prevent a final upload; report missing evidence rather than fabricate it. No global Docker prune or production cleanup is part of verification.

### Remediation and shared capacity

Operator-initiated remediation is an authority mode, not a requirement for a human to execute the tests. Automated browser/API tests can drive the supported normal flow. Reuse the existing action, approval, exact-result verification, and saved-work owners. Pending verification survives browser closure and does not resend the original action.

The existing `operator-remediation-support-matrix/v1` and its readers still need coherent migration where they impose unrelated all-matrix conditions. Keep the distinction between accepted delivery, confirmed operational effect, and verified coding objective. A low-level action does not need a new record database or every evidence channel for every row if existing authoritative records prove its relevant postconditions.

Read-only diagnosis and existing historical evidence should remain usable under normal admission even when unrelated live certification is unavailable. Mutating operations require their actual execution, verification, approval, and security prerequisites. Unsupported actions remain unavailable. Autonomous mutation stays separately closed unless explicitly authorized and implemented through its providing policy. Passing operator-initiated evidence never grants `admin_auto`.

Shared-capacity scenarios use the existing manager and durable lease accounting. Verify the actual admitted scope, purpose, duplicate throttle effects, and release boundaries. Do not create a provider-health service or a second concurrency ledger inside conformance. A cooldown expiry permits only the configured bounded retry policy, not a claim that the provider has fully recovered.

## Verification tiers

The existing tier names describe evidence scope, not a required promotion state machine.

| Tier | What it can establish | Limits |
| --- | --- | --- |
| Required hermetic and exact-artifact checks | Actual selected code, packaged entrypoints, browser/API, database/Temporal, process/mount, and owned cleanup behavior with controlled external dependencies. | A substituted provider does not establish live-provider availability. |
| Protected live-provider observation | The selected account, provider, model, and relevant real external interaction in the authorized environment. | Not an implicit paid call or a universal prerequisite for unrelated PRs. |
| Post-deployment synthetic | A bounded observed journey on that installation and installed artifacts. | Does not certify another independent deployment. |
| Scheduled soak/failure work | Longer behavior or changed failure boundaries not already covered by relevant tests. | An unrelated outage does not redefine completed lower-tier evidence or ordinary runtime compatibility. |

Reuse existing GitHub Actions selection and required aggregation. Selected required jobs cannot pass by being skipped, canceled, or absent. Do not add a new mandatory conformance workflow, duplicate image build, or blanket local suite. Narrow tests run locally for development; broader affected suites run in CI.

Packaged-artifact verification must use the actual built image, with probes mounted separately rather than replacing application code. Keep real migrations, restart, native transport, and required poller checks where those boundaries change. A database-locking claim needs the supported database, not SQLite alone. A Workflow command change needs appropriate retained-history replay or a controlled transition, not helper equivalence.

Choose evidence at the right boundary. The exact-artifact job does not by itself prove provider execution or host-loss restoration when those actions did not occur. Reuse the existing reliability and saved-work journeys for those claims instead of expanding every image probe into another whole-product test.

### Deployed release-support evidence

**Runtime requirements and release certification are different decisions.** The default path should validate the actual installed interfaces, selected authority, required capability, and safe resource state. It should not depend on the continued health of an unrelated protected CI runner or on a fresh report for every harmless SHA/patch change.

Current source/settings still expose the existing `deployment`, `protected`, and `either` evidence policies; the reviewed Compose/template default is `either`. The older protected document chain and the `tools/materialize_omnigent_evidence.py` path are still real consumers. Do not describe all deployments as requiring only the old protected chain, and do not bypass their actual validation because this document changes the target.

#3832 and #3833 coordinate separating incidental certification/readiness coupling at the existing admission/evidence boundary, with #3931 owning redundant runtime-gate removal. Preserve explicitly chosen stricter certification policy and current security stops during migration. Do not introduce another compatibility hash, ready-flag service, or automatic policy downgrade.

Evidence freshness is meaningful for the claim it qualifies. Expired live evidence cannot be advertised as current certification, but does not by itself prove an installed interface incompatible. Revoked credentials, an unsupported interface, known unsafe behavior, or missing required security enforcement still blocks the affected action. A report outage is neither permission to ignore those conditions nor proof they exist.

Original report bytes, hashes, and historical results remain unchanged. New compatible work follows installed runtime selection with meaningful operator choices intact. Future schedule occurrences do not require independent image/report pins or recreation after ordinary updates. A genuinely incompatible persisted plan uses the existing migration/recovery path.

## Credentialed CI publication

The existing `.github/workflows/omnigent-live-conformance.yml` and protected runner remain the live publication path while their consumers need them. Inspect the actual workflow and runner arguments for the intended scenario. Source documentation is not a promise that an environment, adapter, credential, or trigger is available.

Publish only what actually passed for its declared subject and scope. Retain failure/partial observations safely. Reuse the existing report builder and scan/access checks, keeping full aggregate publication non-passing when its mandatory cases are missing. To simplify an obsolete aggregate, change the real producer and its consumers together rather than silently edit reports or mark required rows unsupported.

Do not couple every narrow fix, basic diagnosis, or default execution to the full protected aggregate. Reuse one valid common journey and add only the adapter-specific evidence the claim needs. Static and experimental topology do not automatically block on-demand support. A new actual security boundary still requires its own enforcement proof.

A report can identify the exact image and source that ran without imposing exact equality across all components. No new promotion dashboard, permanent retirement ledger, all-row matrix, or duplicate live-verification service is required. Keep useful diagnostic records and necessary historical readers as obsolete machinery retires.

## Completion and delivery

#3832 owns missing generic-runtime evidence, #3626 recovery qualification, and each implementation issue its own behavior. This document does not close their remaining requirements, authorize production operations, or enable autonomous remediation. Required live observations remain visibly pending until actually collected.

Document-only changes need review, not unit tests of wording, headings, row counts, or metadata. Preserve tests of actual executable schema, parser, transport, permission, and generator behavior. Update providing documentation with code changes rather than invent a semantic-prose validator.

An incomplete handoff keeps the same candidate, accepted work, concrete missing proof, original error, consumed budget, and next authorized automated action through the existing continuation owner. Distinguish report repair, evidence collection, implementation repair, and genuine user decisions. Do not promise a scheduled continuation until its owner accepted it. Verification should recover useful work, not require rebuilding the machinery that blocked it.
