# DocsReview Revalidation Disposition (MoonLadderStudios/MoonMind#3966)

**Document Class:** Imperative working document
**Status:** Active revalidation record
**Owner:** MoonMind maintainers
**Scope/revision:** Full `docs/` tree revalidated against repository revision
`8504ac360b0e5385419487dfb02106ad8a27ecf9`, plus the working-tree repairs
landed by this issue. The August 11 counts in `docs/DocsReview.md`
(21 broken links, 139 advisory findings) are a frozen snapshot at that
date and **do not apply** to the current revision: the pre-repair full-tree
architecture scan reported 166 advisory findings (154 after this issue's
incremental metadata adoption, exit 0), and the bounded link scan
(`tools/check_documentation_links.py --scope all`) reports 7 advisory
findings after the repairs below — all in `docs/Rag/`, outside the August
repair set.
**Delete/Archive Trigger:** Delete this file when #3966 closes and its
successor consolidation/disposal children (#3961/#3962/#3963) have landed;
its durable content is the per-finding owner pointers, which must be
re-homed before deletion. `docs/DocsReview.md` itself must be preserved
until every still-applicable item below has a durable owning document.

## 1. Broken-link repair set (DocsReview §"Broken-link repair set")

All targets verified against the actual canonical successor (move commits
and owning-document migration notes cited); no tombstone documents created.

| # | Source | Old target | Disposition | Canonical successor / evidence |
|---|---|---|---|---|
| 1 | `docs/ManagedAgents/ManagedAgentArchitecture.md` | `./LiveLogs.md` | **Fixed** | `../Observability/LiveLogs.md` (moved in `03d21c00e`) |
| 2 | `docs/ManagedAgents/ManagedRuntimeCleanup.md` | `./LiveLogs.md` | **Fixed** | same as 1 |
| 3 | `docs/ManagedAgents/OAuthTerminal.md` | `./LiveLogs.md` | **Fixed** | same as 1 |
| 4 | `docs/ManagedAgents/SharedManagedAgentAbstractions.md` (2x) | `./LiveLogs.md` | **Fixed** | same as 1 |
| 5 | `docs/MoonMindArchitecture.md` | `ManagedAgents/LiveLogs.md` | **Fixed** | `Observability/LiveLogs.md` |
| 6 | `docs/ManagedAgents/CodexCliManagedSessions.md` | `../Temporal/ArtifactPresentationContract.md` | **Fixed** | `../Artifacts/ArtifactPresentationContract.md` (owning doc §17.1 declares the Temporal path stale and asks refs to move) |
| 7 | `docs/Observability/LiveLogs.md` (2x) | `./CodexCliManagedSessions.md`, `../Temporal/ArtifactPresentationContract.md` | **Fixed** | `../ManagedAgents/CodexCliManagedSessions.md`, `../Artifacts/ArtifactPresentationContract.md` |
| 8 | `docs/Omnigent/CombinedStackValidationAndRollback.md` | `MoonMindVsOmnigent.md` | **Fixed** | No successor: the file was empty when deleted (`354ac483c`). Dead link removed per "remove references to deleted documents rather than creating new tombstones". |
| 9 | `docs/Temporal/TemporalAgentExecution.md` (3x) | `RemainingWork.md` | **Fixed** | No successor file exists; §5 already states execution-stage items are implemented. Dangling links replaced with the `MM-730` cutover release note and owning-module pointers. |
| 10 | `docs/ManagedAgents/ManagedAgentsAuthentication.md` | `TmateArchitecture.md` | **Fixed** | `OAuthTerminal.md` (which `Replaces: docs/ManagedAgents/TmateArchitecture.md`); Provider Profiles own the rest |
| 11 | `docs/DocumentationArchitecture.md` §10 example `../DocumentationArchitecture.md` | — | **Superseded (not a defect)** | Fenced example for an Implementation Plan, which lives in `docs/tmp/` — from there `../DocumentationArchitecture.md` correctly resolves to `docs/DocumentationArchitecture.md`. Fences are classified separately by the link verifier. |
| 12 | Stale code-span path mentions (`docs/ManagedAgents/LiveLogs.md` in `TemporalArchitecture.md`, `StepLedgerAndProgressModel.md`, `ReportArtifacts.md`, `WorkflowConsoleArchitecture.md`; `docs/Temporal/ArtifactPresentationContract.md` in `TemporalArchitecture.md`, `ReportArtifacts.md`, `ImageSystem.md`) | — | **Fixed** | Same successors as 1 and 6 |
| 13 | `docs/DocsReview.md` internal `Workflows/Workflow%53tatus.md` (URL-encoded) | — | **Unverified / frozen evidence** | Inside the frozen August snapshot; not repaired in place. Excluded from the link verifier scope with this record as justification. |
| 14 | `docs/tmp/historical/LiveWorkflowManagement.md` (5x `../ManagedAgents/LiveLogs.md`) | — | **Superseded / frozen evidence** | Archived historical doc superseded by `docs/Observability/LiveLogs.md`; not rewritten. Excluded from the link verifier scope with this record as justification. |

## 2. Architecture-checker findings (full-tree `--scope all`)

August 139 → pre-repair 166 → **154 post-repair** advisory findings (`missing-document-class`,
`duplicate-claim-id`, `imperative-plan-in-canonical-area`), exit 0
(advisory-only posture preserved; no rule added, removed, or weakened).

- `missing-document-class` (122): incremental adoption continues — this
  change adds correct markers to the 10 substantively edited docs that
  lacked them (Module/System Architecture View and Module Contract
  Specification per §3 viewpoints). No metadata-only sweep, no misleading
  classifications. Remaining debt stays advisory and is owned by future
  substantive edits of each document.
- `duplicate-claim-id` (43, incl. `CONTRACT-001..005` shared by the Codex
  canary and Lore/RepositoryAccess docs): **still applicable, deferred**.
  Renumbering stable IDs outside a substantive edit of the owning docs
  would risk breaking stable claim references merely to make a report
  green; owned by the consolidation children touching those documents.
- `imperative-plan-in-canonical-area` (1,
  `docs/Temporal/WorkflowLanguageHardSwitchPlan.md`): **still applicable,
  owned by #3961/#3962**. Deletion/migration must preserve any live-history
  operational rule with the release note and Temporal deployment docs in
  the same change — not done here.

## 3. Deferred chat-instruction contracts (verified, kept distinct)

Verified at this revision; no route reintroduced, no supported chat deleted:

- No `POST /api/executions/{workflowId}/chat-instructions` route or
  handler exists. The only chat surface in
  `api_service/api/routers/executions.py` is
  `GET /{workflow_id}/chat-binding` (native Omnigent binding resolution)
  plus the terminal-continuation guard that explicitly refuses to route
  intent through the native composer / `SubmitChatInstruction`.
- The native-chat non-goal and promotion/security boundary are preserved
  in their actual owner: `docs/UI/WorkflowChatPanel.md` §12 ("does not
  require implementation of `SubmitChatInstruction`, chat-driven plan
  revision…") and §§10–11 evidence rules.
- `docs/Api/ChatInstructionsApiContract.md` (Deferred reserved contract),
  `docs/Temporal/ChatInstructionTemporalContract.md` (Deferred optional
  extension), and `docs/Workflows/ChatInstructionIntervention.md`
  (Deferred optional extension) each name `WorkflowChatPanel.md` as the
  owner of ordinary chat and require explicit promotion before
  implementation. They reserve distinct layer boundaries (API shape,
  Temporal primitive, product/intervention rules), not unowned
  speculation — so per the issue plan they are **kept**, not consolidated
  here. Any future merge is owned by the consolidation children with the
  boundary intact.

## 4. Consolidation / disposal ownership (not executed here)

Per the issue plan, duplicate-authority reconciliation runs through the
existing children; this change deletes no canonical document and creates
no replacement duplicate authority:

- `Api/ExecutionsApiContract.md` vs `Workflows/WorkflowRunsApi.md`,
  artifact storage/presentation overlap, Skill-semantics duplication →
  #3961/#3962 (preserve API fields, ownership, safety requirements,
  executable Skill semantics, stable claim refs before deleting copies).
- `docs/tmp/` active-plan disposal and Jira-handoff hygiene → #3963
  (honoring the #3951 discovery that startup still reads cutover files:
  `TEMPORAL_USER_WORKFLOW_CUTOVER_RECORD_PATH` /
  `MM-730-hard-switch-cutover.json` stay until configuration, validation,
  and tests migrate atomically).
- UI-effect prose, runbook placement, roadmap ownership → #3964/#3965
  with accessibility, reduced-motion, theme/responsive, and component
  contracts preserved.

## 5. Remaining advisory debt (honest record)

- 7 bounded-link findings remain, all in `docs/Rag/`
  (`LlamaIndexManifestSystem.md` non-ASCII-hyphen anchors,
  `ManifestIngestDesign.md` undefined `[n]` citation refs). Genuine but
  outside the August repair set; owned by future substantive edits of
  those documents.
- `docs/DocsReview.md` stays at its user-requested path as review
  evidence until §1–§4 owners above are durable.
