# Claude Code via MiniMax

## 1. Overview

MiniMax provides an Anthropic-compatible API endpoint that lets you run Claude Code against the **MiniMax-M2.7** model using a MiniMax API key. Because Claude Code speaks the Anthropic wire protocol, it works out of the box — the only configuration needed is pointing the `ANTHROPIC_BASE_URL` at `https://api.minimax.io/anthropic` and supplying your MiniMax key.

MoonMind integrates this as a dedicated auth profile (`claude_minimax`) layered on the existing `claude_code` runtime. When `MINIMAX_API_KEY` is set in the worker environment, the profile is auto-seeded on first startup alongside the default profiles.

---

## 2. Prerequisites

| Item | Details |
|------|---------|
| **MiniMax account** | Sign up at [minimax.io](https://www.minimax.io) and generate an API key. |
| **MoonMind worker** | A running `mm.activity.agent_runtime` (or sandbox) worker with `claude` CLI installed. |
| **No Anthropic key required** | The MiniMax key replaces the Anthropic key entirely for this profile. |

---

## 3. Quick Setup (Auto-Seed via `.env`)

The simplest path: add one line to your `.env` and `docker compose up`.

1. Add `MINIMAX_API_KEY` to the project `.env` file at the repo root:

   ```bash
   # .env
   MINIMAX_API_KEY=your-minimax-api-key-here
   ```

   Both the `api` and worker services (`temporal-worker-sandbox`, `temporal-worker-agent-runtime`) include `env_file: .env` in `docker-compose.yaml`, so the key is automatically available in every container that needs it.

2. Run `docker compose up` (or restart if already running). On startup, the API service calls `_auto_seed_auth_profiles()` which checks for `MINIMAX_API_KEY` in the environment. If present, it seeds a `claude_minimax` profile alongside the default profiles:

   ```
   profile_id:           claude_minimax
   runtime_id:           claude_code
   auth_mode:            api_key
   api_key_ref:          MINIMAX_API_KEY
   api_key_env_var:      ANTHROPIC_AUTH_TOKEN
   ```

3. The auto-seeded profile also injects these runtime environment overrides into every Claude Code subprocess:

   | Variable | Value |
   |----------|-------|
   | `ANTHROPIC_BASE_URL` | `https://api.minimax.io/anthropic` |
   | `ANTHROPIC_MODEL` | `MiniMax-M2.7` |
   | `ANTHROPIC_SMALL_FAST_MODEL` | `MiniMax-M2.7` |
   | `ANTHROPIC_DEFAULT_SONNET_MODEL` | `MiniMax-M2.7` |
   | `ANTHROPIC_DEFAULT_OPUS_MODEL` | `MiniMax-M2.7` |
   | `ANTHROPIC_DEFAULT_HAIKU_MODEL` | `MiniMax-M2.7` |
   | `API_TIMEOUT_MS` | `3000000` (50 minutes) |
   | `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | `1` |

> [!NOTE]
> Auto-seeding checks each profile individually.  If `MINIMAX_API_KEY` is set
> but the `claude_minimax` profile does not yet exist, it is inserted on the
> next startup regardless of whether other profiles are already present.

---

## 4. Manual Profile Setup (via Dashboard or API)

If auto-seeding has already run, create the profile through the Task Dashboard or the REST API.

### Dashboard

1. Open the Task Dashboard and navigate to **Settings → Auth Profiles → Create Profile**.
2. Fill in:
   - **Profile ID**: `claude_minimax`
   - **Runtime**: `claude_code`
   - **Auth Mode**: `api_key`
   - **API Key Ref**: `MINIMAX_API_KEY` — name of the worker env var holding your key
   - **API Key Env Var (target)**: `ANTHROPIC_AUTH_TOKEN`
   - **Runtime Env Overrides** (JSON):
     ```json
     {
       "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
       "ANTHROPIC_MODEL": "MiniMax-M2.7",
       "API_TIMEOUT_MS": "3000000",
       "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
     }
     ```

### REST API

```bash
curl -X POST http://localhost:8000/api/v1/auth-profiles \
  -H 'Content-Type: application/json' \
  -d '{
    "profile_id": "claude_minimax",
    "runtime_id": "claude_code",
    "auth_mode": "api_key",
    "api_key_ref": "MINIMAX_API_KEY",
    "api_key_env_var": "ANTHROPIC_AUTH_TOKEN",
    "runtime_env_overrides": {
      "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
      "ANTHROPIC_MODEL": "MiniMax-M2.7",
      "ANTHROPIC_SMALL_FAST_MODEL": "MiniMax-M2.7",
      "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M2.7",
      "ANTHROPIC_DEFAULT_OPUS_MODEL": "MiniMax-M2.7",
      "ANTHROPIC_DEFAULT_HAIKU_MODEL": "MiniMax-M2.7",
      "API_TIMEOUT_MS": "3000000",
      "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
    },
    "max_parallel_runs": 1,
    "enabled": true
  }'
```

---

## 5. How It Works

### API Key Resolution

When the `ManagedAgentAdapter` launches a Claude Code subprocess using the `claude_minimax` profile:

1. **`api_key_ref`** (`MINIMAX_API_KEY`) is resolved to the actual key value via `resolve_managed_api_key_reference()`. This looks up the worker-side env var `MINIMAX_API_KEY` (or a `vault://` reference).
2. The resolved key is injected into the subprocess as `ANTHROPIC_AUTH_TOKEN` (the **`api_key_env_var`** target).
3. All `runtime_env_overrides` are merged into the subprocess environment, pointing the Claude CLI at the MiniMax endpoint.

### Why `ANTHROPIC_AUTH_TOKEN` instead of `ANTHROPIC_API_KEY`?

Claude Code treats `ANTHROPIC_AUTH_TOKEN` as a bearer-style credential for third-party API providers — it is accepted by the CLI for non-Anthropic endpoints. Using this variable avoids conflicting with a real `ANTHROPIC_API_KEY` that may be set elsewhere on the worker for the default `claude_default` profile.

### Auth Profile Manager

The `ProviderProfileManager` Temporal workflow for `claude_code` automatically manages concurrency slots across both the default Claude profile and the MiniMax profile. When a task requests `claude_code`, the manager selects an available profile based on slot availability and cooldown state.

---

## 6. Running a Task with MiniMax

When creating a task in the dashboard:

1. Select **Claude Code** as the runtime.
2. Set **Execution Profile** to `claude_minimax`.
3. The task will use MiniMax-M2.7 through the MiniMax API endpoint.

If no profile is explicitly selected, the `ProviderProfileManager` picks the next available `claude_code` profile, which may be any of `claude_anthropic` or `claude_minimax` depending on slot availability.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Profile not auto-seeded | `MINIMAX_API_KEY` not in worker env **or** profiles table already populated | Set the env var and either clear the table or create manually (§4) |
| `Unable to resolve MANAGED_API_KEY_REF` | `MINIMAX_API_KEY` env var missing on the agent runtime worker | Ensure the env var is set on the **worker** process, not just the API service |
| `401 Unauthorized` from MiniMax | Invalid or expired API key | Regenerate key at minimax.io and update the worker env var |
| Model errors / unexpected behavior | Wrong `ANTHROPIC_BASE_URL` | Verify the profile's runtime env overrides point to `https://api.minimax.io/anthropic` |
| Claude CLI ignores MiniMax config | `ANTHROPIC_API_KEY` takes precedence | Ensure the default Claude profile uses a separate volume/OAuth and does not leak `ANTHROPIC_API_KEY` into the MiniMax subprocess |

---

## 8. Related Documentation

- [ProviderProfiles](../Security/ProviderProfiles.md) — provider profile registry, volume system, and slot management
- [ManagedAndExternalAgentExecutionModel](../Temporal/ManagedAndExternalAgentExecutionModel.md) — execution model for managed agent runtimes
- [SecretStore](./SecretStore.md) — vault-based secret resolution for `api_key_ref`
