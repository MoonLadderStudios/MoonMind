# Runtime-Provider Rollout Policy

**Status:** Desired-state simplification with an implemented legacy contract still in use  
**Document Class:** Module Contract Specification  
**Owners:** MoonMind Platform  
**Last updated:** 2026-09-21  
**Authority:** Runtime selection and bounded transition under the primary-runtime strategy; not a permanent promotion platform

## Related documents

[Primary runtime strategy](PrimaryRuntimeProviderStrategy.md), [Omnigent module entrypoint](README.md), [Contract Ownership](ContractOwnership.md), [Harness Platform](OmnigentHarnessPlatformDesign.md), [Canonical Turn Command Boundary](CanonicalTurnCommandBoundary.md), [Provider Profiles](../Security/ProviderProfiles.md), [Single-User Application Design](../SingleUserApplicationDesign.md), and [Docker Compose updates](../Steps/DockerComposeUpdateSystem.md).

## Advance organizer

Use one existing selection/admission boundary and the deployment's installed managed runtime. Preserve the operator's meaningful harness, Profile, model/cost/privacy, source, and publication choices. Do not maintain parallel Codex phases, qualification toggles, per-combination canaries, rollback controls, and independent schedule pins as permanent authorities for the same choice.

`moonmind/omnigent/runtime_provider_rollout.py` still implements the earlier policy at reviewed main `6fdaab848e8f9fd9c5279ea36186482cab05733d`. Its fields, states, and existing consumers must be migrated coherently rather than ignored. This document revises the target and retains enough compatibility context to interpret that implementation. It does not change executable behavior or authorize deployment. The [earlier complete contract](https://github.com/MoonLadderStudios/MoonMind/blob/6fdaab848e8f9fd9c5279ea36186482cab05733d/docs/Omnigent/RuntimeProviderRollout.md) remains available for precise historical questions.

## 1. Identity: exact combinations

The old `RuntimeProviderCombination` contains exact harness, implementation, Agent Profile compatibility, provider runtime/class, Host Class, runtime pack, materializer, launch policy, host mode, architecture, model class, realizer, and path-class values. Its combination digest and explicit `not-applicable` values retain their original meaning in recorded data.

That hash is not the desired permanent compatibility policy. Actual required interfaces, isolation, credential handling, architecture, and requested capabilities determine whether an operation can run. A SHA, image digest, or patch change alone is not incompatibility. Do not replace exact build matching with a different all-fields fingerprint or broad major/minor equality rule.

Keep artifact integrity and source-control concurrency checks. Historical observations must still identify exactly what ran. Removing a runtime-selection fingerprint does not authorize changing the selected account, ignoring a genuinely incompatible payload, or treating a corrupted artifact as valid.

### Authoring and diagnostic presentation

Preserve the existing Runtime and one Profile authoring boundary. The Profile's account and meaningful execution choices determine its compatible resolved configuration. Internal target, harness descriptor, Host Class, and realizer are not additional mandatory controls. Displayed/submitted expectations must agree with actual admission, without forcing manual reconfiguration for every compatible installed-image update.

## 2. Rollout states

The earlier seven-state vocabulary remains readable while existing consumers require it:

| Recorded state | Earlier interpretation, not a new lifecycle to implement |
| --- | --- |
| `disabled` | No execution through that row. |
| `retired_for_new_work` | Not offered for new authoring; recorded work may still use its supported execution path. |
| `direct_compatibility_only` | Explicit labeled compatibility choice. |
| `explicit_only` | Explicit choice, not an automatic default. |
| `canary` | Explicit choice subject to the existing cohort rules. |
| `preferred`, `new_work_default` | Eligible to be offered as a default under the existing policy. |

State names do not grant access, attest a host, or prove current support. Preserve original decode/decision semantics for retained histories. Do not add another set of replacement states just to simplify this table. New implementation should remove overlapping policy decisions through the existing resolver and retain only actual availability, explicit choice, and necessary compatibility behavior.

## 3. Rules and matching

The legacy resolver matches exact declared dimensions, uses `*` as its selector wildcard, and applies its existing specificity/order rules. An unmatched combination becomes `explicit_only` with `combination_not_registered`; that is a default-selection result, not independent execution authorization. Preserve the distinction while it has live consumers.

The target does not require operator-authored rollout JSON to make ordinary supported work usable. Resolve the installed runtime and required capabilities through existing trusted inputs. Explicit unknown or incompatible choices remain actionable errors before effects, not opportunities to guess another runtime. Registration, temporary availability, and policy permission remain separate facts without another rule engine.

## 4. Built-in rows

Existing rows distinguish generic Codex/Claude/OpenCode from legacy profile-bound and direct compatibility. They are implementation history, not six product choices or six permanent independent rollout programs.

Known qualification/default fields in current code must be reconciled with their actual consumers when removed. A release does not become usable merely by setting every legacy gate true. Conversely, a shipped compatible path should not require several redundant manual toggles or fresh unrelated live evidence because its patch-level artifact changed.

No current or historical path is relabeled generic by editing a document. The selected realizer and recorded evidence remain accurate. Retain a direct/static/profile-bound path only for an actual supported consumer during its bounded transition.

## 5. Fail-closed readiness

Required operator/resource authority, credential use, safe workspace preparation, and actual runtime capability are enforced at their owning boundary. Missing security or execution prerequisites cannot become a pass. Avoid repeating the same checks in several controllers or mistaking a generic failure for a version mismatch.

Separate a supported-but-busy Profile from an unsupported configuration and an unavailable observation. Advisory catalog/probe refresh failure should preserve valid choices and drafts, not erase configuration or invent denial. Actual controlled actions still perform their required current checks. Bounded waits/retries stay with the existing owner and cannot change identity or billing policy.

The legacy readiness path can demote a promoted row to `explicit_only`. That state must not be mistaken for complete qualification or for a default that applies to every caller. Simplification removes redundant promotion checks, not meaningful authentication, supported-interface, or resource constraints.

## 6. Canary cohorts

Exact canary allowlists are an existing migration feature, not a steady-state requirement for a single-operator installation. Do not extend their dimensions, build a cohort dashboard, or require a canary for unrelated removal of dead configuration.

When a real rollout risk warrants a targeted rehearsal, use the existing CI or authorized deployment operation and record its actual outcome. Evidence for a different credential/protocol boundary does not automatically qualify another. Shared production code can share representative evidence without a permanent all-combination matrix.

## 7. Rollback controls

The earlier controls stop generic Codex, generic Claude, OpenCode shared-image, native chat, or all Omnigent admissions, or explicitly restore a declared legacy/direct default. They affect future decisions and do not rewrite an active session. Existing explicitly set safety stops must not disappear during migration or silently re-enable work.

The target reuses actual pause/cancel/operation controls and one deployment repair path instead of six migration-specific switches plus a second rollback engine. A stop does not authorize selecting another account or runtime. Rollback to an older installation is a separately authorized operation subject to real data/schema/history compatibility, not automatic restoration of broad fallback behavior.

## 8. Frozen execution authority

The existing v1 field `OmnigentExecutionPlanPayload.runtimeProviderRollout` records the earlier admission decision. Its representative shape remains:

```json
{
  "policyVersion": "moonmind.omnigent-runtime-provider-rollout/v1",
  "policyGeneration": 1,
  "combinationKey": "omnigent-runtime-provider-combination:sha256:...",
  "targetId": "codex.generic-omnigent",
  "pathClass": "generic_omnigent",
  "state": "new_work_default",
  "ruleGeneration": 1,
  "reasonCode": "rollout_new_work_default"
}
```

This is historical/transition context, not a new request schema or an instruction to keep writing migration fields forever. Original omission and hashing rules remain with the actual loader. Do not retroactively add fields or recompute old hashes. Remove new-write dependence on obsolete metadata only with its real producers/readers, using the existing versioned boundary where necessary rather than a duplicate plan type.

Already-started attempts retain the identity, meaning, and evidence of what they ran. A fresh admitted recovery can use a compatible installed runtime while preserving meaningful choices and saved work. Restored content never restores old leases, credentials, or approval to repeat effects. Actual replay-sensitive command changes still require appropriate evidence or a controlled transition.

## 9. Shared selection and admission boundary

`resolve_runtime_target_selection` in `runtime_target_selection.py` remains the integration point for Create, presets, schedules, edits/reruns, fresh retries, Checkpoint Branches, remediation, continuation, API/MCP, worker normalization, and dashboard projections. Remove redundant providers/defaults there rather than build another selector.

### Recorded authority

Existing recorded-target and explicit-upgrade behavior must remain interpretable for its real consumers. A stale display response cannot override a selected Profile. When a current path is genuinely unavailable or incompatible, report the affected choice and supported correction. Do not silently switch realizer, account, source, model, host-mode constraint, or publication intent.

Preserving historical truth does not require permanently pinning incidental image or rollout generations for new work. Identify which fields express an operator choice and which merely record the old installed implementation before migrating them.

### Schedules

New occurrences follow installed managed runtime selection with their authored harness/Profile, model/cost/privacy, source, and publication intent preserved. Schedules do not have an independent runtime-provider target or image authority. The earlier `runtimeProviderTargetUpdatePolicy` is not restored. Historical target metadata describes past resolution, not permission to ignore current admission.

Keep schedule identity, cadence, paused state, and other explicit choices. Do not recreate or reapprove every schedule on a patch update. Already-started occurrences keep their recorded inputs. A genuine incompatible configuration requires the existing bounded migration/correction, not an unbounded catch-up burst or automatic intent change.

## 10. Operator-visible migration status

The existing `/api/omnigent/runtime-provider-migration` projection is a transition consumer. Preserve any real caller while migrating it, then remove or fold redundant presentation into existing Settings/diagnostics. Do not add another dashboard or require every internal rollout field to remain a product feature.

Useful diagnostics show the selected path, required capability/setup problem, actual operation state, original error, and available evidence. Operator admission and scoped machine/resource restrictions apply without a human role matrix. Metadata access never confers launch authority, and raw credentials or private runtime state must not enter ordinary reports.

## 11. Migration telemetry

Existing bounded telemetry is observational. Its failure must not change selection, execution, save outcome, or cleanup. Keep useful existing measurements and remove obsolete migration families when their consumers disappear. Eleven metric families, exact label inventories, and independent per-combination dashboards are not acceptance requirements.

Use existing logs and operation records for basic diagnosis. No LLM, metrics exporter, or perfectly current projection is required to read the original failure or recover through the independent deployment owner. Do not build a new telemetry system before deleting unused code.

## 12. Configuration

The reviewed deployment still exposes the legacy Codex phase/deployed-phase fields and the runtime-provider rollout, rollback, cohort, and qualified/enabled inputs. These are active implementation inputs until coherently migrated, not the desired first-run form.

#3941 and the runtime-transition owner remove duplication from actual Compose, shell, and application consumers. Keep bootstrap values at the deployment boundary, Preferences in the existing Settings resolver, and Secrets/Profiles separate. Do not move database connectivity into the database or invent a required value the installation can derive.

An unambiguous obsolete value may be migrated once or reported as an unused warning. A conflicting security-sensitive setting must not silently disappear. Preserve explicit stops and real operator choices. No permanent startup census, blanket failure on harmless aliases, or fallback to a different credential source is required.

## 13. Rollback runbook

The supported operational owner is the [portable deployment controller](../Steps/DockerComposeUpdateSystem.md), with local durable intent/progress and recovery independent of healthy MoonMind orchestration. A bounded maintenance window can stop incompatible new writers while actual old work is completed or drained. This document does not introduce another runbook engine or authorize any live operation.

Retirement evidence is proportional to the component: actual active session/credential/cleanup uses, necessary replay/reset support, and required data preservation must be resolved. Missing visibility is unknown, not permission to delete. A dead alias or unused reader does not require every live-provider criterion or nine separate removal stages.

Use existing records, observations, and tests for the affected deployment. Independent installations are observed separately, not treated as a shared fleet. Remove redundant code, controls, and obsolete assertions with their consumers. Delete the empty retirement framework when it no longer has a real job. Do not delete histories, profile-owned OAuth homes, or the only recoverable workspace to make a gate pass.

## 14. Non-goals

No new policy/retirement registry, compatibility fingerprint, event/state service, permanent candidate/retained fleet, account model, or per-harness lifecycle. No universal manual approval or live-canary prerequisite for all cleanup. No silent authority substitution or weakening of required security and history behavior.

Implementation changes retain focused behavior/replay tests and use existing broader GitHub Actions. Prose, headings, phase counts, and registry-row inventories are not unit-test targets. Report current implementation, desired behavior, candidate CI, and any pending live observations distinctly. A documentation PR does not certify that the transition has shipped.
