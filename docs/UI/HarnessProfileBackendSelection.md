# Harness, Provider Profile, and Backend Selection

**Status:** Current
**Document Class:** System / Feature Design View
**Owners:** MoonMind Product and Platform
**Updated:** 2026-10-16
**Authority:** Product terminology, ordinary agent selection, advanced Backend disclosure, and identity presentation across authoring and workflow views. Existing admission, credential, persistence, and lifecycle owners remain authoritative for their mechanisms.
**Related:** [Create Page](CreatePage.md), [Workflows List](WorkflowsListPage.md), [Workflow Model Selection](WorkflowModelSelection.md), [Provider Profiles](../Security/ProviderProfiles.md), [Execution Configurations](../Omnigent/AgentProfiles.md), [Primary Backend Strategy](../Omnigent/PrimaryRuntimeProviderStrategy.md), [Selection and Transition](../Omnigent/RuntimeProviderRollout.md), [Model and Effort Tiers](../Security/ProviderProfileModelEffortTiers.md).

## Decision and scope

Ordinary agent authoring uses **Harness** and one **Provider Profile**. **Backend** identifies the system that launches and manages the harness. Omnigent is the single normal Backend, so ordinary authoring neither asks for it nor describes it. The Workflows list shows **Provider Profile** where it once showed Runtime, optionally with Harness as secondary text in the same cell.

## Vocabulary

| Product term | Meaning | Example | Presentation |
| --- | --- | --- | --- |
| Harness | Agent software performing the work. | Codex, Claude Code, OpenCode | Ordinary authoring, Provider Profile setup, workflow details. |
| Provider | Upstream model service. | OpenAI, Anthropic, OpenRouter | Provider Profile setup and relevant diagnostics. |
| Provider Profile | The existing configured harness/provider connection, credential references, model policy, and capacity identity. | A friendly account/profile name | Ordinary selection and primary workflow-list identity. |
| Backend | System that launches and manages the harness. | Omnigent | Advanced only when a supported alternative exists; otherwise execution details. |
| Host | The actual machine or host service used for execution. | An acquired Omnigent host | Authorized diagnostics. |
| Container | An actual execution container, image, or container identifier. | Recorded container name and image | Authorized infrastructure diagnostics. |
| Execution configuration | Reusable behavior and restrictions represented internally by an Agent Profile. | A selected immutable configuration | Automatically resolved, with exceptional configuration in Settings. |

Use **Backend**, not **Execution backend**, as the UI label. In architecture prose, **agent backend** distinguishes this concept from the **MoonMind API**. Container, Runner, Engine, and Host are not alternate product labels for Omnigent. Use **Claude Code**, not Claude, for the harness.

Provider Profile is the existing object, not a generic multi-harness account or another profile family. Profiles keep their actual harness compatibility even when several harnesses share one upstream provider.

### Serialized names are not UI terminology

There is no global `Runtime` rename. Classify each occurrence by its actual meaning:

- Provider Profile `runtime_id` values such as `codex_cli`, `claude_code`, and `opencode` represent harness ownership. They are not renamed to `omnigent`.
- An Omnigent execution target represents the Backend, with its nested harness identity. `agentKind=external`, `agentId=omnigent`, and the nested identifiers keep their meaning.
- `task.runtime`, model/effort runtime-intent envelopes, language runtimes, worker runtime modules, container-job backends, historical errors, and recorded target identifiers keep their existing meanings.

Use the existing registry, serializers, and ingress normalization. A retained alias needs identified consumers and a removal condition. Original history and digests remain unchanged.

## Ordinary authoring

Illustrative layout, not actual configured accounts or a hardcoded support catalog:

```text
Harness             OpenCode
Provider Profile    OpenRouter · Work
Tier                2
Model               <selected tier value or Custom input>
Effort              <selected tier value or Custom input>
```

The saved default Provider Profile and compatible execution configuration establish the initial Harness. UI, API/CLI Create (including raw execution requests), and both recurring creation endpoints share user/workspace settings. Each new submission snapshots the saved Profile and workspace runtime fallback together before preset expansion; neither becomes an authored Backend override. Accepted retries, schedule updates and refresh retain recorded authority. Saving a workspace runtime applies to the next submission without a process restart. Direct tool-only plans and native review-only finishing ignore the saved agent-account default while retaining the workspace runtime fallback; enabled fix-only or merge finishing still resolves it for the implicit PR resolver child. Unattended submissions retain missing, disabled, or unready saved accounts rather than silently choosing another account. The form may omit unavailable suggestions. Explicit Provider Profile and Agent Profile account pins, and authored profile selectors, take precedence. Each step inherits omitted account intent; a child pin or pool replaces the parent’s account criteria, while an authored pin with its own selector keeps both constraints. Empty criteria retain inheritance. The Harness control filters compatible profiles through the existing backend projection. A profile arriving from a deep link, saved draft, preset, or other entrypoint establishes its compatible harness without being replaced by a global default.

A new unconfigured draft may show an authorized default or sole compatible profile. After a deliberate Harness change, the selected profile stays only when compatible; otherwise the form asks for a compatible Provider Profile and keeps recoverable draft values. It never silently selects a different account, credential route, or billing policy to repair the mismatch.

Choices come from supported catalog data, not page-local enums or combined options such as Omnigent Codex and Direct Codex. Registration alone is not launch permission. Settings may show disabled or disconnected profiles with their setup state. Unavailable capacity, failed advisory discovery, and revoked credentials mean different things and never select a different identity.

The selected profile supplies its configured tiers and actual default tier. [Workflow Model Selection](WorkflowModelSelection.md) owns the Tier/Model/Effort interaction; the Harness control adds no second model policy. Steps inherit the workflow selection unless explicitly specialized; a step's account pin or nonempty selector replaces the inherited account without changing omitted siblings. Viewing or saving an inherited value does not pin it. Tools and container jobs without an agent get no Harness or Provider Profile.

## Backend disclosure

Omitted and default-equivalent input resolve to Omnigent at the shared admission boundary, never to a direct-path fallback. Because Omnigent is the expected route, the page shows no selector or summary line for it.

The Advanced Backend selector appears only while the selected Harness has a supported alternative published by the runtime target catalog, such as the direct Codex and Claude Code compatibility Backends (`codex_cli`, `claude_code`), or while a restored Backend is no longer allowed and needs a replacement. Another account of the same Harness never adds a route. A nondefault Backend, including an explicitly configured alternative deployment default, stays visible as `Backend: <name>` when it is not being edited under Advanced, and in workflow details. Opening or closing Advanced never changes its value.

When Omnigent is the only supported backend for a Harness, there is no selector and no hidden flag keeping a dead launch path. Recorded Backend remains inspectable in execution details. Host Classes, images, launch policies, materializers, realizer versions, and execution configurations are not a mandatory selection chain.

Direct means an existing supported integration without Omnigent, not necessarily execution on the operator's computer. Retiring a direct launcher belongs to its consolidation owner and must preserve active work.

## One server-side selection contract

`runtime_target_selection.py`, `profile_execution_selection.py`, Provider Profile ownership validation, and the executions admission boundary own target/default resolution, compatible configuration selection, and the final handoff. Product changes extend them rather than adding another owner.

For every new-work producer, authored Harness, Provider Profile, defaulted or explicit Backend, model intent, and compatible execution configuration are reconciled before credential acquisition or host effects. A forged or contradictory combination fails at the API boundary even when the UI would never offer it. Explicit pins, account, source, privacy/cost, and publication intent are preserved.

Authored, defaulted, and inherited intent stay distinct from resolved provenance. Consequential concurrent changes to the selected account, harness, backend, or pinned configuration use the existing expectation mechanism. Harmless metadata refresh, display-name changes, compatible installed-image updates, and advisory model discovery do not create a stale-preview or digest-equality gate.

Create, presets, schedules, edits/reruns, fresh retries, checkpoint branches, remediation, continuations, API/CLI/MCP, and worker consumers use the same selection owners; adapters preserve intent rather than choosing their own defaults. Fresh schedule occurrences use the installed backend with authored choices intact. Already-started attempts keep their original identity and evidence. Reconnect or redelivery is not a fresh admission, and fresh admission is not permission to rewrite the source attempt.

A supported-but-busy route waits through existing capacity handling. Missing setup, revoked authority, or an unsupported interface blocks the affected action with an actionable reason. An unavailable optional observation is neither a fabricated pass nor a blanket denial. There is no silent account, harness, backend, model, or paid-route fallback.

## Provider Profile in the Workflows list

[Workflows List](WorkflowsListPage.md) owns the column, projection, filters, facets, and URL state (sections 7, 9.3, 12, and 13.1). The identity rules it implements are:

- The column summarizes the workflow's recorded selection and explicit step-profile variations, not a live "current agent". A retry on another implementation artifact does not change it.
- Unresolved, historical-unknown, and not-applicable states (`pending`, `not_recorded`, `not_applicable`) stay distinct from each other and from a recorded profile. A missing projection is unavailable information, not proof of absence.
- The profile is never inferred from runtime, provider, model, or today's profile inventory, because that would misreport which account ran the work. A renamed or removed profile keeps its recorded identity; an old record with only an ID shows the ID.
- Filters use stable profile IDs, not labels or `targetRuntime`. An old runtime filter stays a separately labeled legacy constraint and is never translated into profile filters.
- The list payload carries only the compact summary: no per-row detail fetches, credentials, OAuth paths, provider payloads, or infrastructure handles.

## Consistency across other surfaces

Provider setup, profile tables and filters, schedules, workflow details, step overrides, edit/rerun, remediation, help, and validation messages use the same vocabulary. Provider Profile stays distinct from the upstream Provider and the internal execution configuration. Profile setup and filtering are Harness-scoped even though the stored field is `runtime_id`.

Execution details show the selected Harness, Provider Profile, and recorded Backend together. Container names, images, and hosts belong in diagnostics. Errors name the affected choice, such as a profile incompatible with the selected Harness or an unavailable Backend, without exposing secrets. Stable machine error codes can stay compatible while readable messages improve.

## Preservation limits

Backend terminology does not move OAuth enrollment, credential-home ownership, provider capacity, native-chat authorization, immutable plans, live bindings, or saved-work and recovery ownership. It never permits copying OAuth homes, a second login, concurrent unauthorized credential writers, an unrestricted chat proxy, or credentialless-to-keyed fallback.

Changes to serialized producers or consumers protect saved work, active attempts, historical decoders, and required Temporal replay at the existing boundaries, without a migration dashboard, universal validation service, cross-deployment registry, or permanent legacy fleet.
