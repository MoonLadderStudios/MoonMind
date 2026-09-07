# Harness-First Workflow Authoring Design

**Status:** Proposed  
**Document Class:** Canonical declarative  
**Viewpoint:** System / Feature Design View  
**Implementation posture:** Deferred; preserve for implementation when the system is ready  
**Owners:** MoonMind Product and Platform  
**Updated:** 2026-09-07  
**Authority:** Candidate product design only. Current providing contracts remain authoritative until this proposal is explicitly adopted and those contracts are reconciled.

> Recording or merging this proposal does not change the UI, API, defaults, runtime identities, support policy, or issue status. It does not authorize implementation or default promotion. In particular, it does not override today's requirement for a visible Runtime selector.

## Advance organizer

**One sentence:** Ordinary users choose a Harness such as Codex, Claude Code, or OpenCode and one Profile, while MoonMind uses Omnigent as the normal runtime and exposes runtime configuration only under Advanced.

**One paragraph:** Harness identifies the agent that performs the work. Profile identifies the existing account, credential route, model policy, and capacity configuration. Runtime identifies the execution backend, normally Omnigent. Execution configurations, rollout targets, Host Classes, and realizer versions remain subordinate resolved details. Hiding normal runtime configuration simplifies presentation without weakening explicit intent, qualification, immutable execution plans, credential ownership, or recovery. A nondefault runtime override remains noticeable even when Advanced is collapsed.

## 1. Product intent and scope

MoonMind is intended to become optimized for Codex, Claude Code, and OpenCode through its shared Omnigent execution plane. Given that direction, the common choice should be which agent to use, not which host implementation to assemble around it.

The normal experience does not require understanding or displaying Omnigent in every selector, workflow title, or execution summary. Technical details remain inspectable, and runtime-specific errors identify the relevant backend. This is progressive disclosure, not removal of provenance or upstream attribution.

The proposal replaces ordinary Runtime plus Profile authoring with Harness plus Profile. It does not add Harness beside an unchanged collection of Runtime, Profile, and Target controls. It applies to agent-execution choices across Create, presets, schedules, edit/rerun, fresh retries, checkpoint branches, remediation, linked continuations, and their API/MCP consumers. It does not force non-agent tools or container jobs to invent a harness or model Profile.

## 2. Terminology

| Term | Meaning | Normal presentation |
| --- | --- | --- |
| Harness | Agent implementation, such as Codex, Claude Code, or OpenCode. | Primary selector. |
| Profile | Existing Provider Profile owning account, credential route, model tiers/policy, and capacity. | One primary selector, scoped by compatibility. |
| Runtime | Execution backend, normally Omnigent. | Advanced configuration. |
| Execution Configuration | Reusable versioned behavior and restrictions currently represented internally by an Agent Profile. | Resolved automatically; exceptional configuration belongs in Settings. |
| Host | A particular acquired container or machine used by the runtime. | Authorized diagnostics. |
| Technical target | Resolved deployment/rollout route, not another account or independent ordinary selection. | Internal admission and diagnostics. |

Use Claude Code rather than Claude for the harness label to distinguish the agent from a provider or model. Omnigent is the runtime; an Omnigent host is an instance used for execution.

These product terms do not mandate a wholesale serialized-field or database rename. Existing Provider Profile `runtime_id` values such as `codex_cli`, `claude_code`, and `opencode` retain their underlying-runtime compatibility meaning until their actual owners deliberately change them. Omnigent execution continues to use `agentKind=external`, `agentId=omnigent`, with nested harness identity. Do not introduce top-level identities such as `omnigent_codex` to implement this presentation.

## 3. Normal and advanced experience

An illustrative normal form is:

```text
Harness       Claude Code
Profile       Personal Claude account

> Advanced
```

The primary harness choices are Codex, Claude Code, OpenCode, and other approved integrations. These are illustrative product labels, not a hardcoded support list or a claim that every combination is qualified.

Opening Advanced reveals the resolved backend:

```text
Runtime       Omnigent · Default
```

When only one runtime is supported for the selected harness and Profile, this may be read-only information. An editable runtime choice appears only for a genuine supported alternative and the caller's permitted configuration. Host Class, materializer, realizer, rollout target, and Agent Profile do not become a mandatory advanced selection chain.

A selected alternative remains visible outside the collapsed section:

```text
Harness       Codex
Profile       Personal Codex account
Execution     Direct runtime override
```

Direct means MoonMind's direct integration without Omnigent, not necessarily execution on the user's local computer. If an operator deliberately configures a different deployment default, the form must still disclose that exceptional backend rather than present it as the invisible normal Omnigent path. Opening or closing Advanced never changes the selection.

Workflow lists and details lead with harness and Profile. Authorized execution details expose recorded runtime/configuration provenance. Runtime-specific failures surface actionable diagnostics. The design does not require an Omnigent prefix on ordinary labels, and does not replace the default with combined options such as Omnigent Codex and Direct Codex in the primary selector.

## 4. One shared selection boundary

The existing backend resolver remains the selection owner. Extend it to treat the selected harness as an explicit constraint, alongside the selected Profile and effective runtime. Do not introduce a second frontend resolver, a new Profile type, a new runtime registry, or another rollout service.

```text
Selected Harness + selected Profile + defaulted or explicit Runtime intent
    -> compatible immutable execution configuration
    -> permitted technical route and exact support checks
    -> committed execution plan
    -> separately acquired live runtime binding
```

A compatible Profile pin remains binding. Without a pin, an authorized compatible default or sole compatible configuration may resolve automatically. These choices must also satisfy the selected harness and runtime. Neither editable field may silently override the other. Genuine ambiguity has a focused Settings remedy rather than a required extra workflow selector.

The harness/Profile projection must come from existing trusted registration, compatibility, and readiness owners. Registration is not support, a shared provider name is not credential compatibility, and a UI-filtered option is not launch authorization. The final API boundary independently verifies the complete selection and caller permission.

### 4.1 Profile and model transitions

| Situation | Proposed behavior |
| --- | --- |
| Genuinely unselected draft | An authorized default or sole compatible Profile may be preselected, with the resulting account clearly displayed. |
| Harness changes | Retain the current Profile only if compatible. Otherwise require a compatible selection and preserve recoverable draft values without submitting stale authority. |
| Runtime changes | Retain harness, Profile, model, and effort where compatible. An incompatible pin or override requires explicit review, not a substituted account or configuration. |
| Explicit model or effort becomes incompatible | Show the conflict and retain the requested value for correction. Do not silently normalize it to another model or paid route. |
| Delayed response, reordered catalog, or metadata refresh | Update only the matching selection's metadata. Do not overwrite newer input, clear an explicit choice, or install a stale default. |
| Missing credentials or no compatible Profile | Show the setup state and an authorized Settings action, not a hidden credential search. |

Keep explicit model/effort overrides distinct from derived tier defaults. Immediate submission must use the current draft, not a debounced or previously resolved selection. Profile inventory and saved drafts remain inspectable under transient discovery or capacity failure, subject to normal authorization.

### 4.2 Hidden Runtime is not unspecified Runtime

The current Profile resolver can identify a direct route when a Profile has no pin and no compatible Omnigent configuration. It deliberately avoids using an unvalidated compatible Omnigent configuration as permission for direct fallback. Consequently, hiding the current dropdown and simply omitting Runtime is not sufficient to implement this proposal.

New harness-first authoring must express a defined default-runtime policy and resolve it through the existing shared boundary. For the normal path that policy selects Omnigent. Preserve whether runtime intent was defaulted, explicitly selected, or inherited, together with the exact resolved runtime/configuration. Do not pretend that a hidden default was an explicit user override.

The preview and submission must agree. Carry an expectation for the relevant resolved identity using the existing configuration-expectation mechanism, extended only where necessary. If a concurrent default, Profile pin, or configuration change would alter that identity, return an actionable review conflict before execution rather than silently taking the new route. Final wire fields belong to the existing schema owner; this proposal does not establish a parallel request format.

Omitted and documented default-equivalent values in the new contract use the same resolver. Older clients and persisted payloads retain their actual versioned semantics through the repository's compatibility policy, not an unconditional reinterpretation of omission. Use only the narrowly required historical reader or cutover mechanism and remove superseded new-write paths with their consumers.

### 4.3 Failure and history semantics

Qualified but busy provider, host, or worker capacity produces durable waiting, not a different runtime or account. Missing qualification, incompatible configuration, revoked credentials, or forbidden policy produces an actionable blocked/setup state. Unknown observations remain unknown. None of these conditions authorizes silent harness, runtime, Profile, model, billing, or security-policy substitution.

Retries within an existing execution reconcile its recorded plan and binding. Fresh execution, restore, or an explicitly reviewed runtime change follows the existing re-admission owner. Historical detail displays recorded identity rather than today's default. Saved schedules and inherited/child work preserve their admitted scope and declared pin/default-following policy. Unknown historical intent requires review rather than guessed migration. Workspace restoration does not restore credentials, leases, approvals, or permission to repeat external effects.

## 5. Resilience and authority that must remain intact

The proposal changes which execution choice is prominent, not the security or durability model. Preserve immutable plans separately from acquired runtime bindings, exact support checks, current revocation checks, generation fencing, idempotent session/turn ownership, and the existing bounded retry and cleanup mechanisms.

Existing Codex and Claude OAuth Profiles remain the account, enrollment, credential-home, generation, and capacity owners for their corresponding Omnigent harnesses. Do not duplicate accounts, copy OAuth homes, repeat login, or allow concurrent mutable-home consumers merely because Runtime moved under Advanced. Keyed OpenCode and credentialless OpenCode remain distinct routes. Credentialless execution receives no dummy secret or implicit keyed fallback.

Provider quota, credential exclusivity, and host/worker capacity remain separate constraints under their existing owners. Physical consumer teardown and durable capacity release must remain correctly ordered. Required checkpoint/artifact preservation and saved-work retention remain governed by their providing contracts. This proposal neither changes what qualifies as a recovery checkpoint nor authorizes destroying useful work to make cleanup appear successful.

Portable Skills keep their semantic authority. No harness-first UI or resolver may reproduce Skill behavior, create a second agent lifecycle, or bypass MoonMind's native-chat authorization boundary.

## 6. Readiness for adoption

Implementation and enablement are deferred. The following are evidence requirements for adopting the normal experience, not a claim that they are already met or a new implementation checklist.

| Readiness condition | Required evidence or decision |
| --- | --- |
| Qualified normal execution | Each advertised default combination completes ordinary authoring, admission, on-demand host realization, exact-host verification, execution, and terminal evidence through the intended generic path. |
| Credential reuse and isolation | Existing Codex OAuth and Claude OAuth work without duplicate enrollment. Keyed and credentialless OpenCode are separately verified wherever advertised. |
| Interaction and recovery | Applicable native Workflow Chat, supported retry/continuation/restore, checkpoint/artifact access, cancellation, and ownership-aware cleanup work across their real handoffs. |
| Shared selection behavior | Harness constraints, compatible Profile/configuration resolution, resolved-runtime expectations, and no-fallback behavior are enforced at actual entrypoints, not only in UI mocks. |
| Safe adoption of saved intent | Existing drafts, schedules, historical runs, and supported alternative runtimes have an explicit disposition that preserves known intent and flags ambiguity. |
| Explicit product adoption | The responsible maintainers adopt this presentation and reconcile the affected current contracts and tests before enabling it. |

Readiness is evaluated per advertised combination and capability. Do not infer it from a registered harness, installed image, passing helper, or closed issue. Distinguish inspected source, hermetic tests, exact-artifact verification, protected-live qualification, and default promotion. Reuse valid evidence from existing owners.

This proposal does not require every possible harness, optional static host, legacy-path removal, or unrelated repository/App project to finish before a bounded implementation can proceed. Equally, partial readiness cannot be hidden by presenting an unqualified route as the normal default. Preserve actionable availability/setup information for unavailable choices. There is no automatic activation date or feature flag introduced by this document.

## 7. Current-contract differences and existing owners

The source baseline reviewed for this proposal is `49fca8528f39b38ddb1f09d580c061d8df03140f` on 2026-09-07. It is context for future reinspection, not permanent evidence of deployment behavior.

The following current owners remain authoritative while this proposal is deferred:

| Existing owner | Reconciliation needed when adopted |
| --- | --- |
| [Primary Runtime Provider Strategy](../Omnigent/PrimaryRuntimeProviderStrategy.md) | Revise the visible-Runtime product outcome to Harness plus Profile with advanced Runtime, while retaining the other primary-runtime outcomes. |
| [Omnigent Execution Configurations](../Omnigent/AgentProfiles.md) | Replace the ordinary Runtime-first presentation rule and integrate an explicit harness constraint without adding another account or required configuration choice. |
| [Create Page](../UI/CreatePage.md) | Make Harness the primary execution control and define advanced Runtime, exceptional-backend disclosure, errors, and reconstruction consistently. |
| [Provider Profiles](../Security/ProviderProfiles.md) | Clarify user-facing terminology and default-runtime intent without changing credential ownership or silently reinterpreting existing Profile fields. |
| [Profile execution selection](../../api_service/services/profile_execution_selection.py) | Extend the existing compatible configuration and expectation boundary instead of duplicating selection policy. |
| [Shared runtime-target selection](../../moonmind/workflows/executions/runtime_target_selection.py) | Preserve centralized defaults, recorded authority, policy restrictions, and exact runtime resolution across callers. |

Affected Settings, workflow-detail, preset/schedule, generated API/type, and test documentation must be reconciled with their actual owners in the adoption change. Do not broadly rename internal fields or weaken existing tests while only recording this proposal.

Coordinate with the existing primary-runtime program [#3825](https://github.com/MoonLadderStudios/MoonMind/issues/3825), exact qualification [#3832](https://github.com/MoonLadderStudios/MoonMind/issues/3832), authoring/default rollout [#3833](https://github.com/MoonLadderStudios/MoonMind/issues/3833), and convergence/retirement [#3925](https://github.com/MoonLadderStudios/MoonMind/issues/3925). These are ownership references, not assertions about current completion or additional prerequisites. Recheck source and active work before implementation. Saving this proposal does not create a new epic or change those issues.

## 8. Observable acceptance requirements

The eventual implementation must demonstrate the following through the existing production form, public API, resolver, and execution boundaries. These requirements do not report tests run for this documentation proposal.

| Scenario | Required result |
| --- | --- |
| Ordinary Codex, Claude Code, or OpenCode workflow | Harness and one Profile are prominent. Omnigent is inspectable under Advanced without an ordinary Runtime/Target/configuration selection chain. |
| Advanced disclosure | Opening and closing it changes no request identity. A nondefault backend remains visible when collapsed. |
| Harness/Profile/runtime mismatch | The public API rejects the conflict before credential or host side effects, including forged client selections. |
| Rapid selection changes and immediate submission | Delayed/out-of-order results cannot overwrite or validate a different draft. Submitted and admitted identities match the current selection. |
| Concurrent default or pinned-configuration change | Relevant stale expectations produce a review conflict, not a silently changed execution. |
| Busy, disabled, unknown, or unqualified route | Inventory, setup, support, and temporary capacity remain distinguishable. No automatic runtime, account, model, or billing substitution occurs. |
| Existing OAuth and OpenCode credential classes | Account reuse, generation/capacity ownership, keyed isolation, and credentialless no-secret behavior remain correct. |
| Presets, schedules, branches, remediation, and API/MCP | All supported consumers use the same selection contract or the expressly retained historical contract. No per-surface default map is introduced. |
| Retry, continuation, rerun, and historical display | Recorded authority remains truthful. Fresh work re-admits as required, and source-workspace restoration never restores old live authority. |
| Worker/host loss, cancellation, and cleanup | Applicable recovery and evidence guarantees remain intact. Capacity release cannot precede the required verified teardown. |
| New harness registration | Existing projections can expose its permitted setup/support state without a new core lifecycle branch or mandatory selector. Registration alone cannot authorize execution. |
| Access and presentation | Unauthorized Profiles and diagnostics remain protected. Keyboard/focus, narrow layouts, and production-served assets preserve the same behavior. |

Required CI must select the affected regressions, including negative cases that deliberately violate identity agreement or reintroduce hidden fallback. Hermetic checks do not replace any required exact-artifact or protected-live evidence. Link evidence through existing qualification and test owners rather than creating another test framework.

## 9. Rationale and alternatives

Keeping Runtime plus Profile visible remains the current contract and can be a useful transitional experience, but it foregrounds infrastructure for the intended common Omnigent path. Harness plus Profile better expresses which agent and account the user selected.

A combined primary selector with Omnigent Codex and Direct Codex options was considered and is not the proposed default. It repeats the normally implicit backend and gives migration alternatives equal prominence. Supported alternatives instead remain advanced and explicitly disclosed when selected.

Collapsing everything into one universal Profile would remove useful distinctions between account/credential lifecycle and reusable execution behavior. This proposal retains those internal distinctions while simplifying their presentation.

The intended simplification is therefore one prominent agent choice, one account/configuration choice, and one shared resolution boundary. It is not a second runtime architecture, broader fallback policy, permanent promise to retain direct paths, or immediate implementation request.
