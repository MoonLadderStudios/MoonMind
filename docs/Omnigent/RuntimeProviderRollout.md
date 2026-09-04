# Runtime-Provider Rollout Policy

**Status:** Implemented
**Document Class:** Module Contract Specification
**Owners:** MoonMind Platform
**Last updated:** 2026-09-04
**Authority:** Per-combination rollout state, default selection, canary and rollback controls, migration status, and migration telemetry for the Omnigent primary-runtime program

## Related documents

- [`docs/Omnigent/PrimaryRuntimeProviderStrategy.md`](./PrimaryRuntimeProviderStrategy.md)
- [`docs/Omnigent/SharedHostImage.md`](./SharedHostImage.md)
- [`docs/Omnigent/CanonicalTurnCommandBoundary.md`](./CanonicalTurnCommandBoundary.md)
- [`docs/Omnigent/OmnigentHarnessPlatformDesign.md`](./OmnigentHarnessPlatformDesign.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)

## Advance organizer

**One sentence:** One deployment-owned, versioned rollout policy decides — per exact runtime-provider combination — whether a target is a default for new work, an explicit-only choice, a labeled compatibility path, or unavailable, and every authoring and follow-up surface reads that one decision.

**One paragraph:** `moonmind/omnigent/runtime_provider_rollout.py` owns the policy: a set of versioned rules that match a combination by field equality over thirteen exact compatibility dimensions plus its path class, and resolve one of seven rollout states. `moonmind/workflows/executions/runtime_target_selection.py` is the single selection and admission boundary every surface uses — Workflow Create, presets, schedules, edit, rerun, retry as a fresh execution, Checkpoint Branch, remediation, linked continuation, and any API or MCP submission. The trusted planner freezes the resolved decision into the immutable execution plan, so changing the live policy afterwards can never reinterpret an admitted execution or a Temporal history. Denial is always explicit: a missing, stale, unqualified, or rolled-back target names its reason and never silently becomes another harness, realizer, Provider Profile, host mode, or model.

## 1. Identity: exact combinations

A rollout decision is scoped to one **exact runtime-provider combination**. Every dimension is a required, non-empty, exact identity; a path that does not own a dimension records the explicit `not-applicable` sentinel rather than an empty string.

```text
harnessId
harnessImplementationRef
agentProfileCompatibilityClass
providerRuntimeId
providerClass
hostClassRef
runtimePackRef
credentialMaterializerRef
launchPolicyRef
hostMode
architecture
modelConfigurationClass
executionRealizerRef
pathClass
```

`compute_runtime_provider_combination_key` digests all fourteen values into

```text
omnigent-runtime-provider-combination:sha256:<digest>
```

Changing any single dimension produces a different combination key. There is no display-name, substring, or prefix routing anywhere in the resolver: `RolloutRule.matches` is field equality only.

### Path classes

```text
generic_omnigent
legacy_profile_bound_omnigent
direct_compatibility
```

The path class is what makes "Codex via generic Omnigent", "Codex via legacy profile-bound Omnigent", and "Direct Codex compatibility" three distinct, independently governed rows even though the first two submit the same canonical `external/omnigent` identity.

## 2. Rollout states

| State | Offered as a new-work default | Offered as an explicit choice | Executes recorded authority |
| --- | --- | --- | --- |
| `disabled` | no | no | no |
| `retired_for_new_work` | no | no | yes |
| `direct_compatibility_only` | no | yes (labeled compatibility) | yes |
| `explicit_only` | no | yes | yes |
| `canary` | no | yes (cohort-gated) | yes |
| `preferred` | yes | yes | yes |
| `new_work_default` | yes | yes | yes |

Two predicates express the difference, and callers use exactly one of them:

- `state_admits_new_authoring` — may an authoring surface *offer* this row?
- `state_admits_execution` — may a plan still compile and run on this row?

`retired_for_new_work` differs on purpose: it is never offered for new authoring, but recorded authority stays executable so replay, rerun, cleanup, and active executions keep their recorded realizer.

## 3. Rules and matching

A `RolloutRule` carries a stable `targetId`, an operator-visible `label`, a `selector`, a `state`, a `generation`, optional support-evidence freshness requirements, an exact canary cohort, and whether the row is restorable as a legacy or direct default.

A selector pins only the dimensions the deployment has qualified on; every other dimension carries the explicit `*` wildcard. Matching is deterministic: the rule with the most pinned dimensions wins, and declaration order breaks a tie. A rule may not declare a dimension outside the closed list above.

### Unregistered combinations

A combination no rule matches resolves to `explicit_only` with reason `combination_not_registered`. That is the fail-closed direction for a *default-selection* policy: a missing support row leaves the relevant path explicit rather than promoting it, and a newly registered harness stays launchable without a rollout edit while promotion remains policy-owned.

## 4. Built-in rows

The built-in policy expresses this deployment's current qualification gates as one versioned document instead of scattered boolean checks.

| `targetId` | Label | Path class | State |
| --- | --- | --- | --- |
| `codex.generic-omnigent` | Codex via generic Omnigent | `generic_omnigent` | `new_work_default` when `MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED`, else `disabled` |
| `codex.legacy-profile-bound-omnigent` | Codex via legacy profile-bound Omnigent | `legacy_profile_bound_omnigent` | `retired_for_new_work` once generic Codex is qualified, else `new_work_default` |
| `claude.generic-omnigent` | Claude Code via generic Omnigent | `generic_omnigent` | `new_work_default` when `MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED`, else `disabled` |
| `opencode.generic-omnigent` | OpenCode via generic Omnigent | `generic_omnigent` | `new_work_default` when `MOONMIND_OMNIGENT_OPENCODE_ENABLED`, else `disabled` |
| `codex.direct` | Direct Codex compatibility | `direct_compatibility` | `direct_compatibility_only` |
| `claude.direct` | Direct Claude compatibility | `direct_compatibility` | `direct_compatibility_only` |

Those six labels are the target identities the UI and API distinguish. A friendly label never becomes a new top-level runtime id: the canonical submitted identity for the first four rows stays `omnigent`, and for the last two it stays the direct provider runtime id.

## 5. Fail-closed readiness

A `preferred`, `new_work_default`, or `canary` state is demoted to `explicit_only` with an exact reason when any required input is missing:

```text
support_evidence_missing
support_evidence_stale
target_not_launch_ready
model_not_qualified
architecture_unsupported
host_mode_unavailable
provider_profile_unavailable
rollout_canary_cohort_excluded
```

A deployment-authored rule requires support evidence by default (`requiresSupportEvidence: true`), so an authored promotion without evidence still fails closed. Every denial reason is drawn from the closed `RolloutReason` vocabulary, and the UI shows it verbatim next to only the explicitly valid alternatives.

## 6. Canary cohorts

`RolloutCohort` carries an exact allowlist per dimension:

```text
ownerCohorts
agentProfileRefs
providerProfileRefs
harnessImplementationRefs
hostClassRefs
launchPolicyRefs
models
architectures
hostModes
```

An empty tuple means the dimension is unrestricted. A non-empty tuple admits only the listed exact values; anything else — including a missing observation — is excluded with `rollout_canary_cohort_excluded`. There is no partial or fuzzy cohort match.

## 7. Rollback controls

Six independently-operable controls change **future admission only**. None transfers ownership of an active execution, rewrites recorded plan authority, or substitutes another runtime for a denied selection.

| Control | Effect |
| --- | --- |
| `stop_new_generic_codex_admission` | Disables the `codex-native` × `generic_omnigent` row |
| `stop_new_generic_claude_admission` | Disables the `claude-native` × `generic_omnigent` row |
| `stop_new_opencode_shared_image_admission` | Disables the `opencode-native` × `generic_omnigent` row |
| `restore_legacy_or_direct_default` | Demotes promoted generic rows to `explicit_only` and promotes every explicitly supported legacy/direct row (`legacyDefaultRestorable`) to `new_work_default` |
| `disable_native_interactive_chat` | Blocks new interactive native chat; historical reads, diagnostics, and evidence are untouched |
| `stop_all_new_omnigent_work` | Disables every Omnigent-backed row (generic and legacy) without promoting any direct row |

Control matching is by exact harness identity and path class — never by display name or runtime substring. Stopping generic admission does **not** implicitly restore a legacy default: that is the separate, explicit `restore_legacy_or_direct_default` control, and `restore_legacy_or_direct_default` promotes a row only when that row is declared restorable. Unknown control names fail fast.

## 8. Frozen execution authority

`compile_execution_plan` resolves the decision for the exact combination it realizes and freezes the compact record into `OmnigentExecutionPlanPayload.runtimeProviderRollout`:

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

The field is optional for replay compatibility with plans admitted before this contract existed, and it is dropped from the canonical payload bytes when absent, so a historical plan keeps its original digest. New admissions always populate it.

The plan's recorded `executionRealizerRef` and its recorded rollout row always agree, so Workflow Detail and audit evidence show one truthful selected path.

## 9. Shared selection and admission boundary

`resolve_runtime_target_selection` is the one entry point. Its `AuthoringSurface` vocabulary is closed:

```text
workflow_create        preset_expansion       schedule
schedule_occurrence    edit                   rerun
retry_as_fresh_execution                      checkpoint_branch
remediation            linked_continuation    api_submission
mcp_submission         worker_normalization   dashboard_config
```

A source-kind difference changes policy and evidence. It never creates a second default resolver, and the boundary reads no environment variables of its own.

### Recorded authority

`schedule_occurrence`, `edit`, `rerun`, and `linked_continuation` preserve their recorded target unless the caller passes `upgrade_to_qualified_target=True`. A recorded target that is no longer registered or no longer authorable stays visible with `available=False` and `replacement_required=True`; it never silently becomes a different harness, profile, model, policy, Host Class, runtime pack, materializer, or realizer. `retry_as_fresh_execution` is new work and takes the promoted target.

### Schedules

A schedule pins its runtime-provider target in `target.runtimeProviderTarget` and declares
`target.runtimeProviderTargetUpdatePolicy`:

- `pinned` (default) — advancing time-limited admission evidence must not move the schedule onto another rollout row. A target change is rejected with an actionable error and the schedule keeps its recorded authority until an operator revises it explicitly.
- `follow_qualified_default` — a newly qualified target is adopted, and that default change advances the schedule revision (`definition.version`) alongside the new plan authority.

## 10. Operator-visible migration status

`GET /api/omnigent/runtime-provider-migration` (permission `settings.catalog.read`) returns, per combination: rollout state and generation, current default status, exact Agent Profile compatibility class, Host Class, runtime pack, materializer, launch policy, host mode, architectures, and realizer; the newest deployment-qualified and protected-live evidence with its age and expiry; the last successful protected (canary) run; bounded recent outcome counters; applicable and active rollback controls; and compatibility-path status.

The projection deliberately excludes credentials, provider-session ids, raw host paths, host image digests, and internal endpoint authority. A migration status reader needs support state, not launch authority.

## 11. Migration telemetry

Eleven bounded families live in the one Omnigent metric registry (`moonmind/omnigent/control_plane/metrics.py`):

```text
omnigent_migration_selected_path                    harness_class, realizer_class, selection_source
omnigent_migration_rollout_state                    harness_class, realizer_class, rollout_state
omnigent_migration_launch_readiness                 harness_class, readiness
omnigent_migration_support_evidence_denial          harness_class, denial_reason
omnigent_migration_provider_profile_wait_seconds    harness_class
omnigent_migration_host_latency_seconds             harness_class
omnigent_migration_first_turn_latency_seconds       harness_class
omnigent_migration_followup_availability            harness_class, followup_kind, availability
omnigent_migration_cleanup_outcome                  harness_class, cleanup_outcome
omnigent_migration_fallback_denied                  harness_class, denial_reason
omnigent_migration_rollback_activation              rollback_control
```

Every label value is drawn from a closed vocabulary; an out-of-vocabulary value collapses to `other` and a missing one to `unknown`. No user, workflow, run, session, binding, provider-session, host, runner, profile, credential, repository, or workspace identity may appear — the registry rejects those keys at registration and at record time. `harness_class` is an exact-id lookup, so a new harness collapses to `unregistered` rather than joining another class.

The shared selection boundary is what records `selected_path`, `rollout_state`, `fallback_denied`, and `rollback_activation`, so every authoring surface reports the migration identically. Telemetry is never authority: a recording failure cannot change which target was selected.

## 12. Configuration

| Variable | Purpose |
| --- | --- |
| `MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLOUT` | Complete deployment-owned policy document (JSON). Invalid configuration fails fast rather than silently reverting to the built-in policy. |
| `MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK` | Comma-separated rollback control list. Unknown values fail fast. |
| `MOONMIND_OMNIGENT_RUNTIME_PROVIDER_CANARY_COHORTS` | Comma-separated cohort membership for this deployment. |
| `MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED` | Promotes the built-in generic Codex row. |
| `MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED` | Promotes the built-in generic Claude row. |
| `MOONMIND_OMNIGENT_OPENCODE_ENABLED` | Keeps the built-in generic OpenCode row promoted. |

Omitting every variable exercises the same production path as setting its documented default: the built-in policy is the default policy, and the default runtime id resolves through the same boundary Workflow Create uses.

## 13. Rollback runbook

1. Read `GET /api/omnigent/runtime-provider-migration` and confirm the affected combination's `targetId`, `rolloutState`, `rolloutGeneration`, and `applicableRollbackControls`.
2. Set `MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK` to the narrowest control that covers the incident. Prefer one per-harness control over `stop_all_new_omnigent_work`.
3. If new work must keep flowing on a compatibility path, add `restore_legacy_or_direct_default` explicitly. Stopping generic admission alone fails closed by design.
4. Restart the API and worker services so the new policy is read.
5. Confirm in the migration status view that `activeRollbackControls` lists the control and the affected rows report the expected `defaultStatus`.
6. Active executions keep running under their recorded plan and realizer. Historical reads, replay, artifacts, and cleanup are unaffected in every mode.

## 14. Non-goals

- Removing direct or legacy implementation code. Retirement is separately gated.
- Transferring an active session between realizers.
- Making unsupported combinations available for consistency.
- Turning Provider Profiles into multi-runtime objects.
- Treating an installed binary or image build as support evidence.
- Reimplementing the canonical turn path owned by the canonical turn-command boundary.
