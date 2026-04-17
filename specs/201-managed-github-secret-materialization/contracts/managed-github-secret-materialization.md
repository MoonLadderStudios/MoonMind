# Contract: Managed GitHub Secret Materialization

## Launch Request Shape

`LaunchCodexManagedSessionRequest` accepts a non-sensitive optional `githubCredential` field:

```json
{
  "githubCredential": {
    "source": "secret_ref",
    "secretRef": "db://managed-secrets/GITHUB_TOKEN",
    "required": true
  }
}
```

Fallback descriptor:

```json
{
  "githubCredential": {
    "source": "managed_secret",
    "required": false
  }
}
```

Environment descriptor:

```json
{
  "githubCredential": {
    "source": "environment",
    "envVar": "GITHUB_TOKEN",
    "required": false
  }
}
```

## Runtime Rules

- `githubCredential` is durable and non-sensitive.
- Raw token values must not appear in `environment`, workflow history, activity payloads, docker run arguments, container environment, artifacts, logs, or diagnostics.
- The activity/controller boundary resolves the descriptor only when host git workspace preparation needs GitHub auth.
- Host git commands receive `GITHUB_TOKEN`, `GIT_TERMINAL_PROMPT=0`, and credential-helper config in process environment only.
- Missing required credentials fail before clone/fetch/push with redaction-safe text.
- Existing owner/repo, URL, and local path repository inputs remain unchanged.

## Compatibility

- Existing requests without `githubCredential` remain valid.
- Legacy raw `environment.GITHUB_TOKEN` is treated as launch-boundary input only and is scrubbed from downstream container environment and payloads.
- Unsupported descriptor values fail through normal schema validation.
