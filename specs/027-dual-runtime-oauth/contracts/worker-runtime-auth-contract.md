# Contract: Worker Runtime Auth and Default Runtime Fallback

## 1) Operator setup commands

Both commands are first-class and independent:

```bash
./tools/auth-codex-volume.sh
./tools/auth-claude-volume.sh
```

## 2) Runtime preflight matrix

| `MOONMIND_WORKER_RUNTIME` | Required preflight auth checks |
| --- | --- |
| `codex` | `codex login status` |
| `claude` | `claude auth status` (or configured equivalent) |
| `universal` | both Codex and Claude auth status checks |
| `gemini` | no Codex/Claude auth requirement change |

## 3) Default runtime fallback

When queue job `type="task"` omits both `payload.targetRuntime` and
`payload.task.runtime.mode`:

- Resolver uses `MOONMIND_DEFAULT_TASK_RUNTIME` if configured.
- Otherwise falls back to `codex`.

When runtime is explicitly provided in payload:

- Explicit runtime is preserved and used for normalization + capability derivation.

## 4) Backward compatibility requirements

- Existing codex-only deployments remain valid with no Claude env variables set.
- Existing explicit runtime payloads are unchanged after normalization.
- Existing queue worker token auth flow remains unchanged.
