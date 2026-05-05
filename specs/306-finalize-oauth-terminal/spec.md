# Feature Specification: Finalize OAuth from Provider Terminal

**Feature Branch**: `306-finalize-oauth-terminal`
**Created**: 2026-05-05
**Status**: Draft
**Input**: User description: """
Update finalize OAuth functionality to match the design in docs/ManagedAgents/OAuthTerminal.md

Additional constraints:


MoonSpec Orchestrate always runs as a runtime implementation workflow.
If the request points at a document path, treat the document as runtime source requirements.
Source design path (optional): .

Before running moonspec-specify, validate that the input is already exactly one independently testable story or an active feature directory with an existing one-story spec.md.
If the input is a broad design, names multiple stories/features, asks to split or implement all stories, or otherwise cannot be bounded to one independently testable story without selection, stop immediately and report that MoonSpec Orchestrate requires a preselected single story and that the input must be routed through moonspec-breakdown or another upstream selector first.
Run moonspec-specify unless an active spec.md already passes the specify gate.
Do not classify this input into a different workflow and do not run moonspec-breakdown from this preset; any story splitting must already be complete before MoonSpec Orchestrate starts.
Preserve the original request or source design in spec.md so final verification can compare against it.
"""

## Classification

Input classification: single-story runtime feature request. The request selects the finalize OAuth behavior from `docs/ManagedAgents/OAuthTerminal.md`: an operator who completes provider login in the provider terminal page can finalize the same OAuth session there, while Settings remains an alternate caller of the same finalization flow. The broader source design includes auth runner, terminal bridge, volume targeting, managed-session launch, and workload-container boundaries; those are source context but not independently implemented by this story.

## User Story - Provider Terminal OAuth Finalization

**Summary**: As an operator completing provider OAuth in the terminal page, I want to finalize the Provider Profile from that same page so that credential enrollment can finish without returning to Settings.

**Goal**: The provider terminal page gives the authenticated operator a safe session projection, shows finalization only when the OAuth session is eligible, requests the existing finalization operation, renders the verification and profile-registration states, and finishes with a safe registered-profile summary while preserving Settings as an equivalent caller.

**Independent Test**: Start from a prepared OAuth session that represents a completed provider login, render the provider terminal completion surface and Settings surface against the same session, trigger finalization from the terminal page, and verify state transitions, duplicate-request safety, profile-registration output, query refresh behavior, and safe failure handling without exposing credential material or mutable session identity fields.

**Acceptance Scenarios**:

1. **Given** an authenticated operator opens a provider terminal page for an OAuth session, **When** the session projection loads, **Then** the page shows the selected profile label, runtime, provider, session status, expiry, and sanitized failure or success summary.
2. **Given** the OAuth session is terminal-attachable, **When** the provider terminal page renders, **Then** the page offers terminal attachment through the allowed session transport without exposing ordinary task-run terminal access.
3. **Given** provider login has completed or the OAuth session is otherwise eligible for verification, **When** the provider terminal page renders, **Then** it shows a `Finalize Provider Profile` action.
4. **Given** the operator activates `Finalize Provider Profile`, **When** finalization starts, **Then** the same finalization operation used by Settings moves the session into `verifying` while durable auth material is validated.
5. **Given** durable auth verification succeeds, **When** profile registration begins, **Then** the session enters `registering_profile` and registers or updates the Provider Profile selected by the original OAuth session.
6. **Given** finalization succeeds, **When** the terminal page renders the completed session, **Then** it shows a safe registered-provider-profile summary and treats returning to Settings or managing the profile as optional convenience actions.
7. **Given** Settings is open for the same provider profile, **When** finalization succeeds from the terminal page, **Then** Settings-side profile views refresh or invalidate stale profile data without requiring Settings to initiate finalization.
8. **Given** finalization is already `verifying`, `registering_profile`, or `succeeded`, **When** another finalize request is made, **Then** no duplicate Provider Profile is created and no different profile is mutated.
9. **Given** the session is cancelled, expired, superseded, or unauthorized for the actor, **When** finalization is requested, **Then** finalization fails safely with a sanitized recoverable outcome and no credential material is exposed.

### Edge Cases

- A double click or concurrent Settings and terminal-page finalize request must converge on one selected Provider Profile update.
- A stale terminal page must not allow changing `profile_id`, `volume_ref`, `volume_mount_path`, runtime, provider, or provider identity for the active OAuth session.
- A failed verification can show a sanitized failure reason and retry option only when the current session state permits retry.
- Cancel, retry, or reconnect actions appear only when the current session state allows them.
- A session transport may be unavailable; finalization behavior must still depend on OAuth session state rather than on legacy external terminal URLs.

## Assumptions

- The OAuth session has already been created by Settings, Mission Control, or an equivalent authorized entry point; this story does not create a new OAuth-session start flow.
- The provider terminal page already exists or is part of the selected OAuth terminal surface; this story owns finalization behavior on that surface, not the full terminal bridge implementation.
- The canonical source design remains Codex-focused for the current target; Claude and Gemini task-scoped managed-session parity is outside this story.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | `docs/ManagedAgents/OAuthTerminal.md` lines 242-267, section 5.4 | The provider terminal page lets the operator finish profile setup where provider auth completed, shows safe session metadata, exposes terminal attachment when attachable, shows `Finalize Provider Profile` when eligible, renders verification and registration states, shows a safe success summary, and exposes allowed Cancel, Retry, or Reconnect actions. | In scope | FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-011 |
| DESIGN-REQ-002 | `docs/ManagedAgents/OAuthTerminal.md` lines 269-276, section 5.4 | Settings and the terminal page call the same finalization flow and observe the same state transitions; finalization owns the move from eligible post-login state through `verifying`, `registering_profile`, and terminal success or failure. | In scope | FR-004, FR-005, FR-007, FR-008 |
| DESIGN-REQ-003 | `docs/ManagedAgents/OAuthTerminal.md` lines 278-285, section 5.4 | Terminal-page finalization is duplicate-click and race safe, cannot duplicate Provider Profiles or mutate a different profile, fails safely for cancelled, expired, or superseded sessions, and cannot change session identity or credential-reference fields. | In scope | FR-008, FR-009, FR-010 |
| DESIGN-REQ-004 | `docs/ManagedAgents/OAuthTerminal.md` lines 287-311, section 6 | Successful OAuth verification registers or updates a Provider Profile using the existing OAuth session metadata and preserves the selected Codex OAuth provider-profile shape and slot policy. | In scope | FR-005, FR-006, FR-010 |
| DESIGN-REQ-005 | `docs/ManagedAgents/OAuthTerminal.md` lines 342-354, section 8 | OAuth/Profile boundary verification validates durable auth material before Provider Profile registration and must not expose credential contents through workflow payloads, artifacts, logs, or UI responses. | In scope | FR-004, FR-012 |
| DESIGN-REQ-006 | `docs/ManagedAgents/OAuthTerminal.md` lines 356-374, section 9 | Only authenticated users with provider-profile management permission can finalize OAuth sessions; browser-visible data is limited to session status, terminal I/O, timestamps, failure reason, and registered profile summary, never raw credential material. | In scope | FR-001, FR-009, FR-012 |
| DESIGN-REQ-007 | `docs/ManagedAgents/OAuthTerminal.md` lines 376-396, section 10 | The operator flow starts an OAuth session, completes provider login in the terminal, finalizes from the terminal, observes verification and registration, refreshes Settings views, and later targets the registered profile for managed Codex sessions. | In scope for finalization and profile-refresh behavior; managed-session launch is out of scope for this story because it is independently testable from task execution. | FR-003, FR-004, FR-005, FR-006, FR-007 |
| DESIGN-REQ-008 | `docs/ManagedAgents/OAuthTerminal.md` lines 38-58, 191-240, 313-340, and 402-414 | Auth runner, terminal bridge transport, managed-session volume targeting, Codex App Server startup, and workload-container boundaries define surrounding desired state. | Out of scope except where they constrain finalization UI and security; implementing these would be separate independently testable stories. | Not mapped |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The provider terminal page MUST show a safe OAuth session projection containing the selected profile label, runtime, provider, session status, expiry, and sanitized failure or success summary for authenticated users authorized to manage provider profiles.
- **FR-002**: The provider terminal page MUST offer terminal attachment only when the OAuth session is terminal-attachable through an allowed MoonMind-owned transport.
- **FR-003**: The provider terminal page MUST show `Finalize Provider Profile` only when the session status is eligible for verification or finalization.
- **FR-004**: Activating `Finalize Provider Profile` from the terminal page MUST request the same OAuth-session finalization operation available to Settings.
- **FR-005**: Finalization MUST transition an eligible session through `verifying` while durable auth material is checked and through `registering_profile` while the Provider Profile is registered or updated.
- **FR-006**: Successful finalization MUST register or update the Provider Profile selected by the OAuth session and show a safe registered-profile summary.
- **FR-007**: Finalization from the terminal page MUST refresh or invalidate any open Settings-side provider-profile data for the affected profile.
- **FR-008**: Duplicate or concurrent finalization requests for a session already in `verifying`, `registering_profile`, or `succeeded` MUST NOT create duplicate Provider Profiles or mutate a different profile.
- **FR-009**: Finalization MUST fail safely for unauthorized, cancelled, expired, or superseded sessions without registering or mutating a Provider Profile.
- **FR-010**: The provider terminal page MUST NOT allow changing `profile_id`, `volume_ref`, `volume_mount_path`, runtime, provider, or provider identity for the active OAuth session.
- **FR-011**: Cancel, Retry, and Reconnect actions MUST appear only when the current OAuth session state allows the action.
- **FR-012**: UI responses, logs, workflow-visible outputs, and artifacts produced by this flow MUST NOT expose credential files, token values, environment dumps, raw auth-volume listings, or raw credential contents.

### Key Entities

- **OAuth Session**: The operator-owned provider enrollment session, including selected Provider Profile identity, runtime/provider identity, status, expiry, allowed actions, transport attachment eligibility, and sanitized result or failure summary.
- **Provider Profile**: The managed credential profile registered or updated after OAuth verification succeeds, including provider identity, credential source, credential-reference metadata, and profile slot policy without raw credentials.
- **Finalization Result**: The observable outcome of a finalize request, including state transition, safe profile summary on success, sanitized failure reason on failure, and any retry or recovery eligibility.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated UI coverage verifies the provider terminal page shows the safe session projection and `Finalize Provider Profile` only for eligible sessions.
- **SC-002**: Boundary coverage verifies terminal-page and Settings finalization use the same session finalization operation and produce the same `verifying`, `registering_profile`, and terminal success or failure states.
- **SC-003**: Automated coverage verifies duplicate or concurrent finalize requests do not create more than one Provider Profile for the selected OAuth session and do not mutate a different profile.
- **SC-004**: Automated coverage verifies cancelled, expired, superseded, and unauthorized sessions fail safely without Provider Profile mutation.
- **SC-005**: Automated coverage verifies successful terminal-page finalization refreshes or invalidates Settings-side provider-profile data.
- **SC-006**: Security review or automated assertions verify browser-visible responses, logs, workflow-visible outputs, and artifacts contain no credential files, token values, environment dumps, raw auth-volume listings, or raw credential contents.
- **SC-007**: Traceability review confirms this spec preserves the original request, `docs/ManagedAgents/OAuthTerminal.md` as runtime source requirements, and DESIGN-REQ-001 through DESIGN-REQ-008 mappings.
