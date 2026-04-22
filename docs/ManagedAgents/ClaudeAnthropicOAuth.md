# MoonMind Design: `claude_anthropic` Settings Authentication (Repo-Backed)

Status: Proposed design based on current MoonMind repo state  
Scope: Mission Control Settings → Providers & Secrets  
Target profile: `claude_anthropic`

## 1. Why this design is needed

MoonMind already has the right architectural home for this feature:

- `Settings` → `Providers & Secrets` is the canonical home for provider profiles, secret-health, OAuth-backed lifecycle entry points, and provider credential readiness feedback.
- `Provider Profiles` already distinguish between `oauth_volume` and `secret_ref` credential sources, and already explicitly model both `Claude Code + Anthropic OAuth` and `Claude Code + Anthropic API key` as valid shapes.
- The current OAuth terminal design is explicitly Codex-focused, even though Claude can already have auth volumes and provider profiles.

The repo also already contains a Claude helper script, `tools/auth-claude-volume.sh`, that assumes a volume-backed Claude auth flow and registers the default profile as:

- `profile_id = claude_anthropic`
- `credential_source = oauth_volume`
- `runtime_materialization_mode = oauth_home`
- `volume_ref = claude_auth_volume`
- `volume_mount_path = /home/app/.claude`

That means the repo already contains partial Claude planning and implementation. However, that current path is still shaped around a Claude home directory / auth-volume model. It is not yet a good match for the workflow described here, where the user follows a link externally and then pastes a returned token back into MoonMind.

## 2. Current repo state

### 2.1 Settings surface

The Settings architecture already supports this feature cleanly.

`docs/UI/SettingsTab.md` makes `Providers & Secrets` the primary configuration surface for:

- Provider Profiles
- Managed Secrets
- secret bindings
- OAuth-backed lifecycle entry points when applicable
- provider credential health, readiness, and validation feedback

This is exactly where `claude_anthropic` auth should live.

### 2.2 Provider profile architecture

`docs/Security/ProviderProfiles.md` already supports the semantics needed for the new flow:

- multiple profiles per runtime
- multiple providers per runtime
- multiple credential source classes per provider
- `oauth_volume`, `secret_ref`, and `none`
- `oauth_home`, `api_key_env`, `env_bundle`, `config_bundle`, and `composite`
- no raw secrets in workflow payloads or provider profile rows

It also already shows two relevant Claude examples:

1. Claude Code + Anthropic OAuth → `oauth_volume` + `oauth_home`
2. Claude Code + Anthropic API key → `secret_ref` + `api_key_env`

That means MoonMind does **not** need a new provider profile concept to support the Anthropic paste-back-token workflow. The data model is already compatible.

### 2.3 Current Settings UI implementation

The current `ProviderProfilesManager.tsx` implementation does **not** yet treat Claude as auth-capable in the inline Settings flow. The helper `isCodexOAuthCapable(profile)` currently returns `true` only when `profile.runtime_id === 'codex_cli'`.

So today:

- `codex_default` gets the inline `Auth` button and OAuth session lifecycle
- `claude_anthropic` does not

This is the first concrete gap that must be closed.

### 2.4 Current OAuth session backend

The current `/api/v1/oauth-sessions` backend is also shaped around the Codex-style flow:

- `create_oauth_session` requires `volume_ref` and `volume_mount_path`
- `finalize_oauth_session` verifies credentials by checking files in a mounted Docker volume
- successful finalization always registers the resulting provider profile as:
  - `credential_source = oauth_volume`
  - `runtime_materialization_mode = oauth_home`

That is a good fit for `codex_default`, but not for a workflow where the real credential handoff is a token pasted back into MoonMind.

### 2.5 Current Claude helper path

`tools/auth-claude-volume.sh` confirms the repo currently treats Claude auth as volume-backed. It supports:

- `--sync` from host `~/.claude`
- `--login` via `claude login` inside a container
- `--check` by looking for `credentials.json`
- `--register` as an `oauth_volume` / `oauth_home` provider profile

This script is useful as proof that Claude auth was already being thought about, but it should not dictate the new Settings flow if Anthropic’s real operator UX is “open link externally, then paste token back.”

## 3. Design decision

## Use a provider-profile-backed **manual token enrollment flow** for `claude_anthropic`, not the current volume-first OAuth session flow.

That means:

- keep the existing Settings / Provider Profiles architecture
- do **not** force Anthropic paste-back auth through the existing `/oauth-sessions` finalize path
- store the returned Anthropic token in the Secrets System
- bind the `claude_anthropic` provider profile to that secret through `secret_ref`
- materialize the credential for launch using `api_key_env` (or a narrowly-scoped Claude token env mode if the runtime eventually needs a distinct name)

This is the cleanest fit with MoonMind’s own architecture.

## 4. Desired profile shape

The repo’s existing desired-state Provider Profiles document already gives the right shape to target.

The recommended resulting `claude_anthropic` profile should look like this conceptually:

```yaml
profile_id: claude_anthropic
runtime_id: claude_code
provider_id: anthropic
provider_label: "Anthropic"

credential_source: secret_ref
runtime_materialization_mode: api_key_env

enabled: true
account_label: "Claude Anthropic"
tags: ["default", "anthropic", "manual-token"]
priority: 100

secret_refs:
  anthropic_api_key: db://claude_anthropic_token

clear_env_keys:
  - ANTHROPIC_AUTH_TOKEN
  - ANTHROPIC_BASE_URL
  - OPENAI_API_KEY

env_template:
  ANTHROPIC_API_KEY:
    from_secret_ref: anthropic_api_key
```

Notes:

- The token itself belongs in the Secrets System, not in the provider profile row.
- The profile row stores only the binding.
- This matches the provider-profile rule that raw credentials must not live in profile rows.

## 5. Settings UX design

### 5.1 Placement

Keep this inside:

- `Settings`
- `Providers & Secrets`
- `Provider Profiles`

Do not create a separate Claude auth page.

### 5.2 Row-level action model

For a `claude_anthropic` profile, the current generic `Auth` action should be replaced by a Claude-specific flow.

Recommended button label:

- `Connect Claude`

Secondary variants after enrollment:

- `Replace token`
- `Validate`
- `Disconnect`

Do **not** reuse the Codex label if the flow is not actually the same.

### 5.3 Modal / drawer flow

When the operator clicks `Connect Claude`, Mission Control should open a focused enrollment drawer or modal with these steps:

1. Explain that Claude Anthropic enrollment is completed externally.
2. Provide the link or command/instructions needed to start the Anthropic flow.
3. Let the operator open that flow in a new tab/window.
4. Provide a secure paste field for the returned token.
5. Validate the token.
6. Save/update the secret.
7. Save/update the provider profile binding.
8. Show readiness / failure state.

Suggested UX states:

- `not_connected`
- `awaiting_external_step`
- `awaiting_token_paste`
- `validating_token`
- `saving_secret`
- `updating_profile`
- `ready`
- `failed`

These states should be shown in the profile row’s Status column the same way Codex currently shows OAuth session status, but they should not pretend to be terminal OAuth states.

### 5.4 Validation feedback

The row should show:

- connected / not connected
- last validated timestamp
- failure reason if validation failed
- whether the backing secret exists
- whether the provider profile is launch-ready

This directly matches the Settings doc’s requirement that provider credential health, readiness, and validation feedback live in this Settings subsection.

## 6. Backend design

### 6.1 Do not reuse `/api/v1/oauth-sessions` as-is

The existing backend is too volume-shaped for this use case.

Today it assumes all of the following:

- a Docker auth volume exists
- the runtime can be finalized by verifying files in that volume
- the resulting profile is `oauth_volume` + `oauth_home`

That is exactly wrong for a token pasted into MoonMind.

### 6.2 Add a separate manual-auth path

Add a dedicated provider-profile auth API for manual token enrollment.

Recommended shape:

- `POST /api/v1/provider-profiles/{profile_id}/manual-auth/start`
- `POST /api/v1/provider-profiles/{profile_id}/manual-auth/validate`
- `POST /api/v1/provider-profiles/{profile_id}/manual-auth/commit`
- `DELETE /api/v1/provider-profiles/{profile_id}/manual-auth`

Or a simpler collapsed API:

- `POST /api/v1/provider-profiles/{profile_id}/connect-claude-anthropic`

with payload:

```json
{
  "token": "...",
  "account_label": "optional label override"
}
```

and server behavior:

1. validate caller permission
2. validate token format
3. perform a safe upstream validation probe
4. write/update the managed secret
5. create/update the provider profile using `secret_ref` + `api_key_env`
6. sync the `ProviderProfileManager`
7. return a secret-free readiness summary

### 6.3 Optional audit/session record

If MoonMind wants parity with the current session-history UX, it can still create an auth-session record for this flow, but it should not be forced into the `oauth_terminal` shape.

If you want auditability, add an auth session kind such as:

- `auth_kind = oauth_terminal`
- `auth_kind = manual_token`

Then the Claude flow can still show a history timeline without reusing PTY/terminal assumptions.

## 7. Secrets handling

This flow should lean into the repo’s Provider Profiles and Secrets architecture.

### 7.1 Required rules

- token is never stored in the provider profile row
- token is never returned to the browser after submission
- token is never placed in workflow payloads
- token is redacted from logs, notices, validation failures, and artifacts

### 7.2 Secret ownership

The secret should be managed under the authenticated operator or workspace scope already used by the Secrets System.

Recommended binding example:

```json
{
  "secret_refs": {
    "anthropic_api_key": "db://claude_anthropic_token"
  }
}
```

## 8. Runtime launch behavior

No new runtime-selection concept is required.

The launcher already supports profile-driven materialization. For `claude_anthropic`, this design simply changes the auth source from volume-backed to secret-backed.

At launch:

1. resolve provider profile
2. apply `clear_env_keys`
3. resolve `secret_refs`
4. inject `ANTHROPIC_API_KEY`
5. launch `claude_code`

This remains fully aligned with the materialization pipeline in `ProviderProfiles.md`.

## 9. Why this is better than extending the current Claude volume flow

The repo’s existing Claude path is:

- sync host `~/.claude`
- or run `claude login`
- verify volume files
- register `oauth_volume` profile

That is still useful for local/operator tooling, but it is the wrong primary Settings experience when the intended auth ceremony is:

- open external link
- complete auth externally
- paste returned token into MoonMind

If MoonMind forced that pasted token back into a generated Claude home directory just to preserve `oauth_volume`, it would:

- add format-coupling to Claude’s private file layout
- add ambiguity about which files are authoritative
- preserve a misleading “OAuth session” abstraction even though MoonMind never truly completed the auth inside its own terminal bridge

Using `secret_ref` is cleaner, more explicit, and more faithful to the real operator workflow.

## 10. Concrete implementation changes

### 10.1 Frontend

Update `frontend/src/components/settings/ProviderProfilesManager.tsx` to:

- stop hardcoding auth capability to Codex only
- add per-profile auth action dispatch by runtime + provider + credential strategy
- add a `claude_anthropic` enrollment modal/drawer
- show `Connect Claude` / `Replace token` instead of generic `Auth`
- surface validation/readiness state in the Status column

### 10.2 Backend

Add a non-volume-based manual auth endpoint and service path that:

- validates token
- writes managed secret
- updates provider profile
- syncs `ProviderProfileManager`
- returns secret-free readiness metadata

### 10.3 Optional schema/session work

If you want audit/history parity, extend auth sessions to support multiple auth kinds rather than only volume-based OAuth-terminal flows.

### 10.4 Docs

Update these docs:

- `docs/UI/SettingsTab.md`
- `docs/Security/ProviderProfiles.md`
- `docs/ManagedAgents/OAuthTerminal.md`

And likely add a focused doc such as:

- `docs/ManagedAgents/ClaudeAnthropicSettingsAuth.md`

## 11. Final recommendation

MoonMind should treat `claude_anthropic` Settings auth as a **provider-profile-backed manual token enrollment flow**.

It should:

- live in `Settings` → `Providers & Secrets`
- reuse the Provider Profiles shell and readiness UX
- store the returned Anthropic token in Managed Secrets
- bind that secret to `claude_anthropic` using `secret_ref`
- launch Claude using `api_key_env`
- avoid forcing this flow through the current volume-based `/oauth-sessions` pipeline

That approach fits MoonMind’s existing architecture, explains the current repo state honestly, and cleanly separates the Codex terminal OAuth flow from Anthropic’s paste-back token flow.
