# Research: Jira Tools for Managed Agents

## Decision 1: Reuse the existing MCP router instead of creating a Jira-specific API

- **Decision**: Add Jira discovery/call support to `/mcp/tools` and `/mcp/tools/call`.
- **Rationale**: Managed agents already consume the MCP surface for tool capability, and the source design is explicitly tool-oriented.
- **Alternatives considered**:
  - Create dedicated `/jira/*` mutation endpoints: rejected because it splits agent tool discovery from the existing tool system.
  - Add Jira support to the queue-stub registry directly: rejected because a dedicated Jira registry is clearer and keeps Jira logic out of the queue compatibility stub.

## Decision 2: Keep Jira credentials at the trusted tool boundary

- **Decision**: Resolve Jira SecretRefs only in the Jira auth/tool path, with optional raw environment fallback for explicit local-development configuration.
- **Rationale**: This matches the source design and MoonMind’s existing SecretRef resolution model.
- **Alternatives considered**:
  - Inject Jira credentials into managed runtime env: rejected because the design explicitly forbids it.
  - Materialize Jira credentials into workspace files: rejected because the design forbids writing `.jira`/`.env` token files into agent workspaces.

## Decision 3: Use a dedicated Jira REST client built on `httpx`

- **Decision**: Implement a small low-level Jira client on top of `httpx`.
- **Rationale**: The repo already depends on `httpx`, and the design explicitly prefers direct Jira REST calls over Forge MCP or Jira CLI tooling.
- **Alternatives considered**:
  - Use `atlassian-python-api` for the managed-agent tool path: rejected because explicit request/response shaping, retry control, and redaction are easier to guarantee with a thin owned client.
  - Use Forge MCP: rejected by the source document.

## Decision 4: Use strict request models and high-level service enforcement

- **Decision**: Model each Jira tool action with strict Pydantic request schemas and keep project/action allowlists plus transition validation in a high-level Jira tool service.
- **Rationale**: It cleanly separates schema validation, policy enforcement, and transport behavior.
- **Alternatives considered**:
  - One generic Jira request payload: rejected because the design calls for narrow, explicit actions and strict validation.
  - Put policy checks in the low-level client: rejected because allowlists are a tool policy concern, not a transport concern.

## Decision 5: Convert plain text to ADF only at mutation boundaries

- **Decision**: Convert plain-text descriptions and comments into Atlassian Document Format when a Jira mutation action requires rich-text payloads.
- **Rationale**: The design calls for plain-text convenience inputs while still producing valid Jira payloads.
- **Alternatives considered**:
  - Require callers to submit raw ADF only: rejected because it makes agent usage harder and encourages malformed payloads.
  - Convert all text fields blindly: rejected because only the rich-text Jira fields need ADF conversion.

## Decision 6: Bound retry behavior and honor `Retry-After`

- **Decision**: Retry only `429`, `502`, `503`, and `504` with a maximum of 3 attempts, honoring `Retry-After` when present.
- **Rationale**: This matches the source document and keeps failure behavior predictable.
- **Alternatives considered**:
  - Retry all 5xx responses indiscriminately: rejected because the design is explicit about the retry set and bounded behavior.
  - No retries: rejected because the design requires bounded retry behavior for transient Jira failures.
