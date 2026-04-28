# Feature Specification: Secret-Safe Settings and Managed Secrets Workflows

**Feature Branch**: `270-secret-safe-settings-managed-secrets`
**Created**: 2026-04-28
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-540 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-540 MoonSpec Orchestration Input

## Source

- Jira issue: MM-540
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Secret-safe Settings and Managed Secrets workflows
- Labels: moonmind-workflow-mm-285619b3-4c87-4e03-944f-282e648fa000
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-540 from MM project
Summary: Secret-safe Settings and Managed Secrets workflows
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Security/SettingsSystem.md
Source Title: Settings System
Source Sections:
- 2.2 What the Secrets System owns
- 5.3 References Over Secrets
- 7.9 SecretRef Setting
- 10.4 SecretRef Resolution
- 14. Secrets Integration
- 22. Security Requirements
Coverage IDs:
- DESIGN-REQ-002
- DESIGN-REQ-010
- DESIGN-REQ-011
- DESIGN-REQ-018

As a secret manager or workspace admin, I can create and bind managed secrets from Settings while generic settings store only SecretRefs and never reveal plaintext after submission.

Acceptance Criteria
- Generic overrides reject API keys, access tokens, refresh tokens, passwords, private keys, OAuth state, and credential-bearing generated config.
- Secret-like backend fields are hidden unless explicitly represented as SecretRef pickers or managed through the Managed Secrets creation/replacement flow.
- Managed secret create and replace flows accept plaintext only as one-way submissions, then clear browser input and show metadata plus SecretRef.
- Secret validation resolves plaintext only in memory at controlled execution boundaries, discards it, stores redacted metadata, and returns redacted diagnostics.
- Broken, disabled, revoked, or missing SecretRefs are surfaced clearly and prevent affected launches where appropriate.

Requirements
- Settings must not redefine SecretRef semantics, secret storage, encryption at rest, root key custody, backend classes, resolution, rotation, revocation, audit, or plaintext redaction rules.
- SecretRef values are security-relevant metadata and must be access controlled.
- Settings APIs must ignore client-supplied descriptor metadata and enforce session/CSRF protections appropriate to MoonMind auth.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-540 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

## Classification

- Input type: Single-story runtime feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable Settings and Managed Secrets workflow.
- Selected mode: Runtime.
- Source design: `docs/Security/SettingsSystem.md` is treated as runtime source requirements.
- Resume decision: No existing Moon Spec artifacts for `MM-540` were found under `specs/`; specification is the first incomplete stage.
- Multi-spec ordering: Not applicable for `MM-540`.

## User Story - Create and Bind SecretRefs Safely

**Summary**: As a secret manager or workspace admin, I can create and bind managed secrets from Settings while generic settings store only SecretRefs and never reveal plaintext after submission.

**Goal**: MoonMind lets authorized Settings users submit plaintext only through managed-secret create or replacement flows, returns secret metadata and copyable SecretRefs rather than plaintext, validates SecretRefs through controlled backend resolution, and prevents generic settings overrides from persisting raw credentials.

**Independent Test**: Through Settings, create a managed secret with plaintext, verify the response and UI show metadata plus `db://<slug>` but not the plaintext, bind that SecretRef to a generic setting, validate active and disabled SecretRefs, and confirm raw credential-shaped generic overrides are rejected and redacted from responses.

### Acceptance Scenarios

1. **Given** a workspace admin creates a managed secret from Settings, **When** the create request succeeds, **Then** the browser-visible response and list show metadata and `db://<slug>` while never rendering the submitted plaintext.
2. **Given** a user replaces or rotates an existing managed secret value, **When** the operation completes, **Then** plaintext input is cleared and the UI continues to show only status, timestamps, and the SecretRef.
3. **Given** a generic SecretRef setting, **When** a user submits `db://<slug>` for an active managed secret, **Then** the setting stores only the reference and does not resolve or persist plaintext.
4. **Given** a generic setting receives a raw API key, token, password, private key, OAuth state, or credential-bearing config value, **When** the override is submitted, **Then** the backend rejects it with a sanitized validation error and no partial override is stored.
5. **Given** a SecretRef points at a missing, disabled, revoked, deleted, or invalid managed secret, **When** Settings resolves catalog or validation diagnostics, **Then** the UI surfaces a broken SecretRef state and affected launch checks can block rather than silently falling back.

### Edge Cases

- Empty plaintext create and replacement submissions are rejected before persistence.
- SecretRef metadata is treated as security-relevant and never used to grant authorization.
- Secret validation failures do not echo the submitted plaintext or decrypted secret.
- Existing provider-profile secret flows remain owned by Provider Profiles and are not redefined by generic Settings.

## Requirements

### Functional Requirements

- **FR-001**: Managed secret create, replace, and rotate flows MUST accept plaintext only as one-way submissions and clear browser-held plaintext after completion.
- **FR-002**: Managed secret list and mutation responses MUST expose metadata and a canonical `db://<slug>` SecretRef while never exposing ciphertext or plaintext.
- **FR-003**: The Settings UI MUST provide a copyable SecretRef affordance for managed secrets without exposing the stored secret value.
- **FR-004**: Generic settings overrides MUST reject raw API keys, access tokens, refresh tokens, passwords, private keys, OAuth state, and credential-bearing generated config.
- **FR-005**: Generic SecretRef settings MUST store and return only SecretRef strings, not resolved plaintext.
- **FR-006**: Secret validation MUST resolve plaintext only in controlled backend code, discard it before responding, and return redacted diagnostics including status and timestamp.
- **FR-007**: Missing, disabled, rotated, deleted, invalid, or otherwise unresolved `db://` SecretRefs MUST produce explicit diagnostics in Settings catalog/effective responses.
- **FR-008**: Secret-like backend fields MUST be hidden from generic settings unless represented as SecretRef settings or handled by Managed Secrets / Provider Profile specialized flows.
- **FR-009**: Settings APIs MUST ignore client-supplied descriptor metadata and enforce existing session/auth boundaries for secret-related settings.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-540` and this canonical Jira preset brief.

### Key Entities

- **ManagedSecret**: A durable encrypted secret record with slug, lifecycle status, metadata, and timestamps. Browser-visible surfaces expose only metadata plus a derived SecretRef.
- **SecretRef**: A security-relevant reference string, such as `db://github-pat-main`, stored in settings or provider bindings instead of plaintext.
- **SettingsOverride**: A scoped setting value that may store allowed JSON values and SecretRefs but must not store raw credentials.
- **SecretValidationDiagnostic**: A redacted validation result for a SecretRef or managed secret, including status, timestamp, and non-secret diagnostics.

## Assumptions

- Existing Secrets System encryption, resolver, and provider-profile semantics remain authoritative.
- This story extends existing Settings and Managed Secrets surfaces rather than introducing a new secret backend.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-002 | `docs/Security/SettingsSystem.md` section 2.2 | Settings may expose secret workflows and SecretRef pickers but must not redefine storage, decryption, resolution, rotation, revocation, audit, or plaintext redaction semantics owned by the Secrets System. | In scope | FR-001, FR-006, FR-008 |
| DESIGN-REQ-010 | `docs/Security/SettingsSystem.md` sections 5.3 and 7.9 | Sensitive settings must store references, not raw values; SecretRef settings store reference strings while the referenced value remains outside the setting override. | In scope | FR-004, FR-005, FR-007 |
| DESIGN-REQ-011 | `docs/Security/SettingsSystem.md` sections 10.4 and 14 | Plaintext resolution happens only at controlled execution or validation boundaries; Settings shows metadata, usage, validation, and broken-reference states without plaintext readback. | In scope | FR-002, FR-003, FR-006, FR-007 |
| DESIGN-REQ-018 | `docs/Security/SettingsSystem.md` section 22 | Security requirements forbid raw secrets in generic overrides, browser plaintext readback, unmanaged secret-like fields, client-supplied descriptor trust, and unredacted validation diagnostics. | In scope | FR-001, FR-002, FR-004, FR-006, FR-008, FR-009 |

## Success Criteria

- **SC-001**: Secret create, update, rotate, list, and validate responses contain zero instances of submitted plaintext or decrypted secret values in tests.
- **SC-002**: The Managed Secrets UI shows a canonical `db://<slug>` reference for each secret and copy action tests prove the copied value is the reference only.
- **SC-003**: Settings API tests prove raw credential-shaped generic overrides fail and do not persist partial data.
- **SC-004**: Settings catalog/effective responses report explicit diagnostics for missing and inactive `db://` SecretRefs.
- **SC-005**: Traceability evidence preserves `MM-540`, DESIGN-REQ-002, DESIGN-REQ-010, DESIGN-REQ-011, and DESIGN-REQ-018 across MoonSpec artifacts and final verification.
