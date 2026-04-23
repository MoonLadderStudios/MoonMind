# Contract: Claude Secret-Ref Launch

## Boundary

The managed runtime launcher accepts a selected `ManagedRuntimeProfile` for `claude_anthropic` and prepares the process environment for Claude Code.

## Input Profile Shape

```json
{
  "profile_id": "claude_anthropic",
  "runtime_id": "claude_code",
  "provider_id": "anthropic",
  "credential_source": "secret_ref",
  "runtime_materialization_mode": "api_key_env",
  "secret_refs": {
    "anthropic_api_key": "db://claude_anthropic_token"
  },
  "clear_env_keys": [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY"
  ],
  "env_template": {
    "ANTHROPIC_API_KEY": {
      "from_secret_ref": "anthropic_api_key"
    }
  },
  "command_template": ["claude"]
}
```

## Required Behavior

- Resolve `secret_refs.anthropic_api_key` through the trusted secret-reference resolver.
- Remove every configured `clear_env_keys` key from the inherited environment before applying rendered profile values.
- Render `env_template.ANTHROPIC_API_KEY` from `anthropic_api_key`.
- Start Claude Code with `ANTHROPIC_API_KEY` in the child environment.
- Do not pass `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, or `OPENAI_API_KEY` through when they are configured for clearing.
- Do not write the resolved secret value to durable payloads, logs, diagnostics, artifacts, or failure summaries.
- If the secret reference cannot be resolved, fail before process start with a secret-free actionable error.

## Out Of Scope

- Real Anthropic API validation.
- UI enrollment behavior.
- Changing runtime selector semantics.
- Changing non-Claude provider-profile materialization semantics.
