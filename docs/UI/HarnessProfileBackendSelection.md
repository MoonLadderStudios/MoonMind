# Harness, Provider Profile, and Backend Selection

**Status:** Accepted
**Implementation and deployment:** Tracked separately; acceptance of this design does not certify shipped behavior.
**Document Class:** System / Feature Design View
**Owners:** MoonMind Product and Platform
**Updated:** 2026-10-01
**Authority:** Product terminology, ordinary agent selection, advanced Backend disclosure, and identity presentation across authoring and workflow views. Existing admission, credential, persistence, and lifecycle owners remain authoritative for their mechanisms.
**Related:** [Create Page](CreatePage.md), [Workflows List](WorkflowsListPage.md), [Provider Profiles](../Security/ProviderProfiles.md), [Execution Configurations](../Omnigent/AgentProfiles.md), [Primary Backend Strategy](../Omnigent/PrimaryRuntimeProviderStrategy.md), [Selection and Transition](../Omnigent/RuntimeProviderRollout.md), [Model and Effort Tiers](../Security/ProviderProfileModelEffortTiers.md).

## Decision and scope

Ordinary agent authoring uses **Harness** and one **Provider Profile**. **Backend** identifies the system that launches and manages the harness, normally Omnigent. Backend is not an ordinary required choice. The Workflows list replaces its Runtime column and mobile field with **Provider Profile**, optionally showing Harness as secondary text in the same cell.

This adopts and replaces the deferred Harness-first authoring proposal. It does not certify that the UI has shipped, promote an unsupported execution path, or retire an active launcher merely by renaming it. Implementation follows the existing owners and proportional automated tests, not another rollout framework or manual approval gate.

## Vocabulary

| Product term | Meaning | Example | Presentation |
| --- | --- | --- | --- |
| Harness | Agent software performing the work. | Codex, Claude Code, OpenCode | Ordinary authoring, Provider Profile setup, workflow details. |
| Provider | Upstream model service. | OpenAI, Anthropic, OpenRouter | Provider Profile setup and relevant diagnostics. |
| Provider Profile | The existing configured harness/provider connection, credential references, model policy, and capacity identity. | A friendly account/profile name | Ordinary selection and primary workflow-list identity. |
| Backend | System that launches and manages the harness. | Omnigent | Advanced only when a meaningful supported alternative exists, otherwise execution details. |
| Host | The actual machine or host service used for execution. | An acquired Omnigent host | Authorized diagnostics. |
| Container | An actual execution container, image, or container identifier. | Recorded container name and image | Authorized infrastructure diagnostics. |
| Execution configuration | Existing reusable behavior and restrictions represented internally by an Agent Profile. | A selected immutable configuration | Automatically resolved, with exceptional configuration in Settings. |

Use **Backend**, not **Execution backend**, as the UI label. In architecture prose, **agent backend** distinguishes this concept from the **MoonMind API**. Container, Runner, Engine, and Host are not alternate product labels for Omnigent. Use **Claude Code**, not Claude, for the harness.

Provider Profile remains the current object. This change does not create a generic multi-harness account object, duplicate account enrollment, or another profile family. Profiles retain their actual harness compatibility even when several harnesses share the same upstream provider.

### Existing serialized names are not UI terminology

There is no global `Runtime` search-and-replace. Classify each occurrence by its actual meaning:

- Provider Profile `runtime_id` values such as `codex_cli`, `claude_code`, and `opencode` represent harness ownership for this product vocabulary. They are not renamed to `omnigent`.
- An Omnigent execution target represents the Backend, with its existing nested harness identity. Preserve `agentKind=external`, `agentId=omnigent`, and existing supported nested identifiers.
- `task.runtime`, model/effort runtime-intent envelopes, language runtimes, worker runtime modules, container-job backends, historical errors, and recorded target identifiers retain their existing meanings unless their actual schema owner makes a necessary change.

Use the existing registry, serializers, and ingress normalization. Improve internal names at touched boundaries where useful, but do not require a database-wide rename, new compatibility fingerprint, alias registry, or parallel resolver to ship the UI. Necessary retained aliases need identified consumers and a removal condition. Original history and digests remain unchanged.

## Ordinary authoring

Illustrative layout, not actual configured accounts or a hardcoded support catalog:

```text
Harness             OpenCode
Provider Profile    OpenRouter · Work
Tier                2
Model               <selected tier value or Custom input>
Effort              <selected tier value or Custom input>
```

The default Provider Profile and compatible execution configuration establish the initial Harness. There is one consistent selection, not two independent sources of truth. The Harness control filters compatible profiles through the existing backend projection. A profile selected through a deep link, saved draft, preset, or another supported entrypoint establishes its compatible harness without being replaced by a global default.

A genuinely new unconfigured draft may display an authorized default or sole compatible profile. After a deliberate Harness change, retain the selected profile only when compatible. Otherwise show that a compatible Provider Profile must be selected and preserve recoverable draft values. Do not silently select a different account, credential route, or billing policy to repair the mismatch.

Use supported catalog data, not page-local enum lists or combined primary options such as Omnigent Codex and Direct Codex. Registration alone is not launch permission. Settings may display disabled or disconnected profiles with their setup state. Unavailable capacity, failed advisory discovery, and revoked credentials have different meanings and cannot silently select a different identity.

### Model controls remain one existing owner

The selected profile supplies its configured tier choices and actual default tier. The approved Tier/Custom interaction is owned by the model-selection and tier contracts, not a second policy inside the Harness control: numbered Tier selection populates Model and Effort, editing either field selects Custom while preserving the other field, and selecting Custom directly leaves both fields unchanged. Do not restore the Tier fallback selector, including under Advanced.

Saved, preset, inherited, and Custom choices survive reconstruction and compatible profile/harness changes. A genuinely incompatible explicit value remains visible for correction. Stale asynchronous responses cannot overwrite newer input or convert derived previews into authored overrides. The companion Tier/Custom design is recorded in [documentation PR #4635](https://github.com/MoonLadderStudios/MoonMind/pull/4635); its separate implementation must be reused rather than duplicated here.

Steps inherit workflow selection unless explicitly specialized through the existing supported override boundary. Viewing or saving an inherited value does not pin it as a new override. Tools and container jobs without an agent do not invent a Harness or Provider Profile.

## Backend disclosure

Omnigent is the normal backend policy for new agent authoring. Merely hiding the current Runtime dropdown and dropping its value is insufficient if that reactivates an old direct-path fallback. Resolve omitted and documented default-equivalent input at the existing shared admission boundary.

Offer a Backend selector under Advanced only while the selected combination has a genuine supported alternative. An explicitly selected nondefault backend remains visible in the collapsed summary and workflow details. Opening or closing Advanced never changes its value. An explicitly configured alternative deployment default is also disclosed rather than presented as invisible Omnigent execution.

When Omnigent is the only supported backend, remove the selector rather than showing a one-option dropdown or retaining dead launch paths behind a hidden flag. Recorded Backend remains inspectable in execution details. Host Classes, images, launch policies, materializers, realizer versions, and execution configurations do not become a mandatory selection chain.

Direct means an existing supported integration without Omnigent, not necessarily execution on the operator's computer. Do not introduce Direct as a second implementation merely to populate this control. Retirement of actual alternative launchers remains with the existing consolidation owner and must preserve active work.

## One server-side selection contract

Extend the existing composition of `runtime_target_selection.py`, `profile_execution_selection.py`, Provider Profile ownership validation, and the executions admission boundary. They already own target/default resolution, compatible configuration selection, and the final authoritative handoff. This product change does not create another owner.

For every supported new-work producer, reconcile authored Harness, Provider Profile, defaulted or explicit Backend, model intent, and compatible execution configuration before credential acquisition or host effects. A forged or contradictory combination fails at the API boundary even when the UI would never offer it. Preserve explicit pins, account, source, privacy/cost, and publication intent.

Keep authored/defaulted/inherited intent distinct from resolved provenance. Use the existing expectation mechanism for consequential concurrent changes to the selected account, harness, backend, or pinned configuration. Do not add a stale-preview requirement or image/digest equality gate for harmless metadata refresh, display-name changes, compatible installed-image updates, or advisory model discovery.

Create, presets, schedules, edits/reruns, fresh retries, checkpoint branches, remediation, continuations, API/CLI/MCP, and worker consumers use the same surviving selection owners. Adapters preserve intent rather than choosing their own defaults. Fresh schedule occurrences use the installed backend with authored choices intact. Already-started attempts retain their original identity and evidence. Reconnect/redelivery is not a fresh admission, and fresh admission is not permission to rewrite the source attempt.

A supported-but-busy route waits through existing capacity handling. Missing setup, revoked authority, or an actual unsupported interface blocks the affected action with an actionable reason. An unavailable optional observation is not a fabricated pass or a blanket denial. No silent account, harness, backend, model, or paid-route fallback is introduced.

## Provider Profile in the Workflows list

[Workflows List](WorkflowsListPage.md) owns table interaction. This section owns the identity shown there.

Replace the ordinary desktop Runtime column and mobile Runtime field with **Provider Profile**. Show its friendly recorded name, with Harness as optional secondary text in the same cell. Do not add ordinary Provider, Harness, Backend, Host, and Container columns alongside it. Execution details retain authorized provenance.

The list summarizes the workflow's recorded selection and supported explicit step-profile variations. It is not a live “current agent” field. A retry using another implementation artifact does not change the selected profile summary. New attempt identity and actual use remain inspectable at their existing detail boundaries.

| Recorded information | Presentation |
| --- | --- |
| One known profile | Recorded display name, or stable profile ID when no name was recorded. Harness may appear beneath it. |
| Selection still unresolved | Pending selection. Do not substitute today's deployment default. |
| Historical row without a recoverable profile association | Not recorded. Do not infer the profile from a runtime, provider, model, or today's profile inventory. |
| No agent profile applies | Not applicable. Do not create a dummy profile. |
| More than one recorded applicable profile | Multiple profiles, with a bounded summary and the individual step/attempt associations in details. |

A known authored profile may be displayed before launch without implying successful acquisition. Distinguish authored selection from confirmed use in details. A disabled, disconnected, renamed, or removed live profile does not erase its historical identity. Capture an inexpensive display-name snapshot with existing admitted selection metadata where needed. When old evidence contains only the ID, show the ID rather than inventing the past name. Do not introduce a separate profile-history registry.

Project this compact summary through the existing execution list/read model. No per-row detail, profile, step-ledger, or Temporal history requests and no browser enumeration of every page. Use existing persistence and batching. Exclude credentials, OAuth paths, raw provider payloads, and infrastructure handles from the ordinary list payload.

### Filtering, sorting, and saved list state

Provider Profile filters use stable profile IDs, not display labels or `targetRuntime`. Extend the existing list/facet query owner with the required profile summary and membership data. Desktop headers, mobile filters, active chips, URL/query serialization, saved column preferences, and list-to-detail return context all use the same semantics.

For a multiple-profile workflow, an include filter matches any recorded applicable member and an exclude filter rejects any listed member. Unresolved, historical-unknown, and not-applicable states remain distinguishable in display and queries through `providerProfileStateIn` / `providerProfileStateNotIn` values `pending`, `not_recorded`, and `not_applicable`. [Workflows List sections 7.2, 9.3, 12.1, and 13.2](WorkflowsListPage.md#93-provider-profile-filter) own the row state, ID/state union, aggregate blank shortcut, and state facet/count contract. Do not imply a missing projection proves a known absence.

Facets describe authorized workflow records, not just currently enabled profiles or the currently loaded page. Retain recorded profiles that are no longer launchable. Deduplicate by stable ID and show a short ID when equal labels require disambiguation. Count and pagination semantics must agree with the list. Facet failure preserves the table and selected filter, with truthful partial/unavailable coverage.

Keep the current-page-only sort notice until real server sorting exists. Do not fetch all rows to simulate global ordering. Profile label sorting, where supported, uses the same display projection with a deterministic ID tie-breaker.

Old runtime filter URLs and saved views cannot be relabeled as profile filters. Preserve them with an explicitly labeled legacy runtime constraint while supported, or show an actionable unsupported-filter message before changing the query. Never silently broaden the result set or translate one runtime into today's profile inventory. The old Runtime column preference may become the replacement column preference without changing a query's meaning.

## Consistency across other surfaces

Provider setup, profile tables and filters, schedules, workflow details, step overrides, edit/rerun, remediation, help, and user-facing validation use the same vocabulary. Provider Profile stays distinct from the upstream Provider and internal execution configuration. Profile setup/filtering is Harness-scoped even while stored fields are still named `runtime_id`.

Show the selected Harness, Provider Profile, and recorded Backend together in execution details. Container names/images and actual hosts belong in diagnostics. Errors identify the affected choice, for example a profile incompatible with the selected Harness or an unavailable Backend, without exposing secret material. Stable machine error codes can remain compatible while their readable messages improve.

## Preservation and implementation limits

Reuse existing OAuth enrollment, credential-home ownership, provider capacity, native-chat authorization, immutable plans, live bindings, and saved-work/recovery owners. Backend terminology does not move those responsibilities or permit copying OAuth homes, a second login, concurrent unauthorized credential writers, an unrestricted chat proxy, or credentialless-to-keyed fallback.

Protect saved work, active attempts, historical decoders, and required Temporal replay when changing serialized producers/consumers. Resolve only demonstrated compatibility needs at existing boundaries. Do not build a migration dashboard, universal validation service, new lifecycle, cross-deployment registry, permanent legacy fleet, or new quota UI.

## Automated acceptance

Use focused failing executable tests followed by the smallest complete implementation and broader existing GitHub Actions. Documentation wording is not a unit-test target and manual visual signoff is not an acceptance dependency.

Required evidence covers:

- Production authoring/profile components and API admission agree on Harness, Provider Profile, defaulted/explicit Backend, and inherited versus authored model intent.
- New omitted/default requests resolve consistently across form and representative non-form callers. Explicit mismatch, unavailable setup, delayed responses, and capacity waits preserve intent and useful errors.
- Backend has no ordinary selector, appears in Advanced only for a real alternative, remains disclosed when exceptional, and is unchanged by disclosure toggles.
- Existing OAuth and keyed/credentialless boundaries retain their actual credential and capacity guarantees through the surviving path.
- Real list/read-model/facet integration displays and filters stable profile associations, including multiple, renamed/removed, unknown, pending, and no-agent records, without per-row fetches or guessed backfills.
- Desktop/mobile controls, long labels, equal-name profiles, keyboard focus, filter chips, query round trips, saved preferences, and list/detail return state preserve the same meaning.
- Historical payloads and active attempts retain their original IDs, interpretation, digests, and recorded evidence. Compatible installed-image changes do not become identity conflicts.

Share tests for common behavior and add focused cases for genuine credential/protocol differences. An external-provider substitute proves only the stated integration boundary. Live support is separately observed or honestly unverified, never a universal prerequisite for a terminology or list change. Implementation tracking belongs in GitHub issues and the PR, not another checklist document.
