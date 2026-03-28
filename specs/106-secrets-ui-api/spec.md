# Feature Specification: Secrets UI and API

**Feature Branch**: `106-secrets-ui-api`  
**Created**: 2026-03-28  
**Status**: Draft  
**Input**: User description: "Implement Phase 4 of docs/tmp/010-SecretsSystemPlan.md: UI and API Surfaces"

## Source Document Requirements

- **DOC-REQ-001**: Add API endpoints for create or update managed secret, list metadata, rotate, disable, delete, validate bindings or usage references.
- **DOC-REQ-002**: Add UI screens or forms showing metadata and usage status without ever re-displaying secret values.
- **DOC-REQ-003**: Make the first-run path obvious: after compose startup, the user can add a provider API key and GitHub PAT through the UI and reach a runnable state without manual secret-manager setup.
- **DOC-REQ-004**: Add operator-safe status surfaces showing whether a reference is healthy, missing, or broken.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Add First-Run Secrets (Priority: P1)

As a new MoonMind operator, I want to be prompted to add my Provider API Key and GitHub PAT through the UI immediately after startup, so that I can reach a runnable state without a manual secret-manager setup.

**Why this priority**: It is the baseline requirement for MoonMind to be useful upon installation.

**Independent Test**: Can be fully tested by starting a fresh instance without any secrets and ensuring the UI prompts for the necessary API keys.

**Acceptance Scenarios**:

1. **Given** no secrets exist, **When** I load the frontend app, **Then** I am shown a prominent banner prompting to add the essential secrets.
2. **Given** I am entering a new secret, **When** I save it, **Then** the secret is persisted securely and the prompt disappears if all essential secrets are configured.

---

### User Story 2 - Manage Existing Secrets (Priority: P2)

As a MoonMind operator, I want to view a list of configured secrets, their metadata, and their status, and have the ability to rotate, disable, or delete them safely through the UI.

**Why this priority**: Routine maintenance and security requirements demand a way to manage secrets over time.

**Independent Test**: Can be tested by verifying the UI accurately reflects backend secret metadata, without exposing the `ciphertext`.

**Acceptance Scenarios**:

1. **Given** I am on the secrets dashboard, **When** I view the list, **Then** I see the slug, status, and last updated time, but not the actual secret value.
2. **Given** an existing secret, **When** I trigger a rotation and submit a new value, **Then** the secret is updated securely and logged as rotated.

### Edge Cases

- What happens when a user attempts to update a secret with an empty value? (Validation should reject it).
- How does the system handle an API request seeking the `ciphertext` of a secret? (The API schemas ensure `ciphertext` is never serialized in the response).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a `POST /api/v1/secrets` endpoint for creating new secrets. (Maps to DOC-REQ-001)
- **FR-002**: System MUST provide a `GET /api/v1/secrets` endpoint that returns secret metadata (slug, status, updated_at) but strictly omits ciphertext values. (Maps to DOC-REQ-001)
- **FR-003**: System MUST provide `PUT` and `POST` endpoints for updating, rotating, disabling, and deleting secrets. (Maps to DOC-REQ-001)
- **FR-004**: System MUST present a UI rendering the list of managed secrets and their metadata without displaying the actual secret values. (Maps to DOC-REQ-002)
- **FR-005**: System MUST detect when essential Provider/GitHub secrets are missing and display a first-run prompt in the UI. (Maps to DOC-REQ-003)
- **FR-006**: System MUST indicate in the UI if a secret reference mapped in a Provider Profile is missing or broken. (Maps to DOC-REQ-004)

### Key Entities

- **ManagedSecret**: Represents the secure data entity containing a slug, encrypted ciphertext, status, and audit metadata.
- **ProviderProfile**: An entity that depends on ManagedSecrets to operate correctly.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `GET /api/v1/secrets` response payload size strictly limits data to metadata, returning 0 bytes of sensitive `ciphertext` per secret.
- **SC-002**: An operator can complete the first-run configuration of API keys solely via the Web UI in under 1 minute.
- **SC-003**: Rotating a secret propagates correctly, resulting in a successful test execution from the temporal worker within 5 seconds.
