# Agent Backend Selection and Rollout Controls

**Status:** Implemented controls; simplification follows the backend strategy  
**Document Class:** Module Contract Specification  
**Owners:** MoonMind Platform  
**Last updated:** 2026-10-16  
**Authority:** Selection and rollout controls in `moonmind/omnigent/runtime_provider_rollout.py`; not a permanent promotion platform

## Related documents

[Backend strategy](PrimaryRuntimeProviderStrategy.md), [Harness, Provider Profile, and Backend Selection](../UI/HarnessProfileBackendSelection.md), [Omnigent module entrypoint](README.md), [Contract Ownership](ContractOwnership.md), [Provider Profiles](../Security/ProviderProfiles.md), and [Docker Compose updates](../Steps/DockerComposeUpdateSystem.md).

## Advance organizer

`runtime_provider_rollout.py` owns one versioned per-combination policy that answers whether a combination may be a new-work default, an explicit-only choice, a labeled direct compatibility path, or nothing. Ordinary supported work must not need operator-authored rollout JSON: the built-in policy is derived from the shipped qualification switches. These controls are transition tools. Remove or fold them together with their actual consumers; never simplify by setting every gate true or adding another policy facade.

## 1. Authoring and identity

Ordinary authoring uses **Harness and one Provider Profile**. **Backend** is Omnigent and is not an ordinary selector; the direct compatibility Backends appear only under Advanced ([selection design](../UI/HarnessProfileBackendSelection.md)). The Profile's account, harness compatibility, and meaningful execution choices determine its resolved configuration. Internal target, harness descriptor, Host Class, and realizer are not additional controls. Profile `runtime_id` remains a harness-ownership field, not Backend identity.

`RuntimeProviderCombination` records exact harness, implementation, Agent Profile compatibility, provider runtime/class, Host Class, runtime pack, materializer, launch policy, host mode, architecture, model class, realizer, and path class. Its digest and explicit `not-applicable` values keep their meaning in recorded data, but the hash is not a compatibility policy. Required interfaces, isolation, credential handling, architecture, and requested capabilities decide whether an operation can run; a changed SHA, image digest, or patch version alone is not incompatibility. Artifact integrity and source-control concurrency checks remain.

## 2. Rollout states

| State | Meaning |
| --- | --- |
| `disabled` | No execution through that row. |
| `retired_for_new_work` | Not offered for new authoring; recorded work keeps executing on its recorded realizer. |
| `direct_compatibility_only` | Explicit labeled compatibility choice. |
| `explicit_only` | Explicit choice, not an automatic default. |
| `canary` | Explicit choice for the row's exact cohort allowlist; outside it the row is `explicit_only`. |
| `preferred`, `new_work_default` | Eligible to be offered as a default. |

State names do not grant access, attest a host, or prove current support. Preserve their decode semantics for retained histories, and do not add a replacement state set.

## 3. Rules and built-in rows

Rules match exact declared dimensions, use `*` as the selector wildcard, and apply specificity/order. An unmatched combination becomes `explicit_only` with `combination_not_registered`: a default-selection result, not execution authorization. Explicit unknown or incompatible choices fail before effects instead of guessing another runtime.

| Target | Built-in state |
| --- | --- |
| `codex.generic-omnigent` | `new_work_default` by default; explicit `MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED=false` disables it; explicitly configured linked-evidence constraints may keep it `explicit_only` |
| `codex.legacy-profile-bound-omnigent` | `retired_for_new_work`; recorded plans, retries and cleanup only |
| `claude.generic-omnigent` | `explicit_only` (reached by choosing an Anthropic Profile); `disabled` when `MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED=false` |
| `opencode.generic-omnigent` | `new_work_default`; `disabled` when `MOONMIND_OMNIGENT_OPENCODE_ENABLED=false` |
| `codex.direct`, `claude.direct` | `direct_compatibility_only` (`codex_cli`, `claude_code`) |

Generic Codex promotion is decided in `codex_cutover_drain.py`: with no linked qualification declared, the switch alone permits it; a declared linked qualification must match the selected dimensions exactly, or the row stays explicit-only. Editing a document never relabels a path as generic; the selected realizer and recorded evidence stay accurate.

## 4. Readiness

Readiness fails closed for promoted rows only. A `preferred`, `new_work_default`, or `canary` row whose launch readiness, model qualification, architecture, host mode, or Provider Profile availability is missing is demoted to `explicit_only` with the exact reason. Support-evidence freshness demotes a row only under explicit strict certification (`MOONMIND_OMNIGENT_EVIDENCE_POLICY=protected` or `deployment`); ordinary installations keep it as an advisory observation. An `explicit_only` result is neither full qualification nor a default for every caller.

Separate a supported-but-busy Profile from an unsupported configuration and an unavailable observation. An advisory catalog/probe refresh failure preserves valid choices and drafts. Bounded waits and retries stay with their owner and cannot change identity or billing policy.

## 5. Canary cohorts and rollback controls

`MOONMIND_OMNIGENT_RUNTIME_PROVIDER_CANARY_COHORTS` lists this deployment's cohort membership, matched against a rule's exact allowlists. Cohorts are not a steady-state requirement for a single-operator installation; do not extend their dimensions or require a canary to remove dead configuration.

`MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK` takes a comma-separated set of controls; unknown values fail fast:

- `stop_new_generic_codex_admission`, `stop_new_generic_claude_admission`, `stop_new_opencode_shared_image_admission`: disable that harness's generic rows.
- `stop_all_new_omnigent_work`: disable every Omnigent row.
- `disable_native_interactive_chat`: reported as `nativeInteractiveChatAllowed=false` in the migration status.
- `restore_legacy_or_direct_default`: demote default generic rows to `explicit_only` and make still-supported direct rows that declare themselves restorable the default. Recorded profile-bound Codex plans cannot become a new-work default.

Every control affects future admission only. None rewrites an active session or recorded plan, and a stop never substitutes another account or runtime. Explicitly set safety stops must not disappear or silently re-enable work when these controls are later simplified. Rolling back to an older installation is a separate operation subject to real data/schema/history compatibility.

## 6. Recorded plan authority

`OmnigentExecutionPlanPayload.runtimeProviderRollout` records the admission decision:

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

Original omission and hashing rules stay with the loader; never add fields to old records or recompute their hashes. Already-started attempts keep the identity and evidence of what they ran. A fresh admitted recovery can use a compatible installed runtime while preserving meaningful choices and saved work, but restored content never restores old leases, credentials, or approval to repeat effects.

## 7. Shared selection boundary

`resolve_runtime_target_selection` in `moonmind/workflows/executions/runtime_target_selection.py` is the integration point for Create, presets, schedules, edits/reruns, fresh retries, Checkpoint Branches, remediation, continuation, API/MCP, worker normalization, and dashboard projections. `new_work_runtime.py` resolves Create and schedule admission through it. Admission composes it with Provider Profile/configuration selection.

Omitted or default-equivalent Backend intent resolves to Omnigent through this boundary, so hiding a selector cannot reactivate a direct default. Defaulted, explicit, and inherited input stay distinct from the resolved backend and configuration. Contradictory harness/profile/backend combinations fail before credential or host effects. A stale display response cannot override a selected Profile; a genuinely unavailable path reports the affected choice and its correction rather than switching realizer, account, source, model, host mode, or publication intent.

### Schedules

New occurrences follow installed runtime selection with their authored harness/Profile, model/cost/privacy, source, and publication intent. A schedule has no target pin of its own: `runtimeProviderTarget` records the last resolution as provenance, and adopting a new target advances the schedule revision while keeping identity, cadence, paused state, and choices. A stored, retired `runtimeProviderTargetUpdatePolicy` is ignored and dropped when the schedule's authority next advances. Patch updates never require recreating or reapproving schedules.

## 8. Migration status and telemetry

`GET /api/omnigent/runtime-provider-migration` projects each row's `targetId`, `rolloutState`, `rolloutGeneration`, `defaultStatus`, `applicableRollbackControls`, and the policy's `activeRollbackControls`, with bounded migration counters from `moonmind/omnigent/control_plane/metrics.py`. It is an operator diagnostic used by the rollback runbook; fold it into Settings/diagnostics when its controls go, rather than adding a dashboard. Metadata access never confers launch authority, and raw credentials or private runtime state never enter it.

Telemetry is observational: its failure must not change selection, execution, save outcome, or cleanup. Basic diagnosis uses logs and operation records and needs no LLM or metrics exporter.

## 9. Configuration

Codex uses the shared realizer for new plans. Explicit false disables new shared Codex admission; it no longer creates profile-bound work. Custom policy and stop controls remain authoritative and fail closed when the shared row is unavailable. Recorded profile-bound plans, planless/reference-only histories, and their cancellation, saving and cleanup keep their existing owners. The retained realizer, OAuth maintenance and janitor adapters can be removed after those recorded consumers can no longer execute or retry; historical readers remain until retention or lossless migration permits removal.

The rollout, rollback, cohort, and qualified/enabled inputs are read by `runtime_provider_rollout.py` and shipped in `.env-template`. `MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLOUT` is normally empty, selecting the built-in policy; a complete JSON policy document overrides it, and invalid configuration fails fast. Clear a stale upgraded value to follow the installed default. Ordinary versus strict certification is selected by `MOONMIND_OMNIGENT_EVIDENCE_POLICY`: omitted, blank, or `either` is certificate independent; explicit `protected` or `deployment` keeps strict validation ([Conformance and Live Smoke](ConformanceAndLiveSmoke.md#deployed-release-support-evidence)).

`MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE` and `MOONMIND_CODEX_OMNIGENT_DEPLOYED_PHASE` are not new-work inputs and are absent from `.env-template` and `docker-compose.yaml`; `new_work_runtime.py` never reads them. They still feed the `cutover` projection of `GET /api/omnigent/codex-catalog-readiness` and the evidence built by `tools/build_codex_omnigent_cutover_evidence.py`, and stored phase observations keep their meaning. [Codex Support and Cutover](CodexSupportAndCutover.md#versioned-rollout) owns them; they are not a second default-selection gate.

Keep bootstrap values at the deployment boundary, Preferences in the Settings resolver, and Secrets/Profiles separate. An unambiguous obsolete value may be migrated once or reported as unused; a conflicting security-sensitive setting must not silently disappear.

## 10. Rollback runbook

The [portable deployment controller](../Steps/DockerComposeUpdateSystem.md) owns general deployment repair. For a harness-specific incident:

1. Read `GET /api/omnigent/runtime-provider-migration` and confirm the affected row's `targetId`, `rolloutState`, `rolloutGeneration`, and `applicableRollbackControls`.
2. Set `MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK` to the narrowest control that covers the incident. Prefer a per-harness control over `stop_all_new_omnigent_work`.
3. For a still-supported direct compatibility default, use `restore_legacy_or_direct_default` explicitly. It cannot reopen profile-bound Codex admission. Stopping shared admission fails closed; it does not substitute another lifecycle.
4. Restart the API and worker services so the new policy is read.
5. Confirm in the migration status that `activeRollbackControls` lists the control and the affected rows report the expected `defaultStatus`.
6. Active executions keep running under their recorded plan and realizer. Historical reads, replay, artifacts, and cleanup are unaffected in every mode.

When retiring a component, resolve its actual active session, credential, cleanup, replay/reset, and data-preservation uses first. Missing visibility is unknown, not permission to delete; a dead alias or unused reader needs caller evidence, not live-provider criteria. Never delete histories, profile-owned OAuth homes, or the only recoverable workspace to make a gate pass.

## 11. Non-goals

No new policy/retirement registry, compatibility fingerprint, event/state service, permanent candidate/retained fleet, account model, or per-harness lifecycle. No universal manual approval or live-canary prerequisite for cleanup. No silent authority substitution or weakening of required security and history behavior.
