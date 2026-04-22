# Contract: Provider Profile Auth Actions

## Scope

This contract defines the Settings row-level behavior for provider profile auth actions. It is a UI interaction contract, not a new HTTP API contract.

## Inputs

`ProviderProfilesManager` receives `ProviderProfile[]`.

Relevant profile fields:

```ts
type ProviderProfile = {
  profile_id: string;
  runtime_id: string;
  provider_id: string;
  credential_source: string;
  runtime_materialization_mode: string;
  secret_refs: Record<string, string>;
  command_behavior?: Record<string, unknown> | null;
  enabled: boolean;
};
```

Optional Claude metadata in `command_behavior`:

```json
{
  "auth_strategy": "claude_manual_token",
  "auth_state": "connected",
  "auth_actions": ["replace_token", "validate", "disconnect"],
  "auth_status_label": "Claude token ready"
}
```

## Required Rendering

- Codex OAuth-capable rows keep the existing `Auth`, `Cancel OAuth`, `Finalize`, and `Retry` behavior tied to OAuth session state.
- Disconnected Claude Anthropic rows with supported metadata render a row action labeled `Connect Claude`.
- Connected Claude Anthropic rows render only supported lifecycle labels:
  - `Replace token`
  - `Validate`
  - `Disconnect`
- Claude rows must not render the generic Codex `Auth` label.
- Unsupported rows render no Claude auth action.
- Optional Claude auth status text renders in the Status cell when metadata provides a secret-free `auth_status_label`.

## Non-Goals

- No standalone Claude auth page.
- No raw token handling in this story.
- No new provider profile persistence schema.
- No changes to Codex OAuth request payloads or endpoints.

## Verification

- UI tests assert labels, omitted unsupported labels, status text, and preserved Codex OAuth behavior.
- Final verification confirms MM-445 and source design mappings are preserved.
