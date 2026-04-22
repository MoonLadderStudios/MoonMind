# Claude Anthropic OAuth in Settings

**Status:** Desired state  
**Scope:** Mission Control Settings -> Providers & Secrets -> Provider Profiles  
**Target OAuth profile:** `claude_anthropic`

This document defines the OAuth-backed Claude Code authentication path for
Anthropic accounts. It covers the case where the operator authenticates Claude
Code through Claude's own interactive login flow in a MoonMind browser terminal,
and MoonMind persists the resulting Claude home credentials in an auth volume.

This document does **not** define Anthropic API-key enrollment. API-key auth is a
separate credential method that should remain easy to choose from the same
Settings surface.

## 1. Product Intent

Operators should be able to configure Claude Code credentials from:

- `Settings`
- `Providers & Secrets`
- `Provider Profiles`

For Claude Code with Anthropic, the operator should be able to choose between
two first-class credential methods:

1. **Connect with Claude OAuth**: open a MoonMind browser terminal, run Claude's
   interactive account login flow, and store the resulting Claude home files in a
   durable auth volume.
2. **Use Anthropic API key**: store an Anthropic API key in Managed Secrets and
   materialize it as `ANTHROPIC_API_KEY` at launch.

The OAuth path is the subject of this document. The API-key path is mentioned
only to clarify that OAuth is not the only valid Claude Anthropic credential
method.

## 2. OAuth Profile Shape

The OAuth-backed `claude_anthropic` provider profile uses a volume-backed Claude
home:

```yaml
profile_id: claude_anthropic
runtime_id: claude_code
provider_id: anthropic
provider_label: "Anthropic"

credential_source: oauth_volume
runtime_materialization_mode: oauth_home

volume_ref: claude_auth_volume
volume_mount_path: /home/app/.claude

enabled: true
account_label: "Claude Anthropic OAuth"
tags: ["default", "anthropic", "oauth"]

clear_env_keys:
  - ANTHROPIC_API_KEY
  - CLAUDE_API_KEY
  - OPENAI_API_KEY
```

The auth volume contains Claude's reusable account-auth material. Raw credential
files are never copied into workflow payloads, logs, artifacts, browser
responses, or provider profile rows.

## 3. Settings UX

### 3.1 Placement

Claude Anthropic OAuth enrollment stays inside the existing Provider Profiles
table. MoonMind should not create a separate Claude auth page.

### 3.2 Row Actions

For a Claude Anthropic profile, the row should expose auth actions that make the
credential method clear:

- `Connect with Claude OAuth` for OAuth volume enrollment or repair.
- `Use Anthropic API key` for the separate API-key enrollment path.
- `Validate OAuth` when an OAuth volume is present and can be checked.
- `Disconnect OAuth` when supported by the provider-profile lifecycle policy.

Claude rows must not reuse Codex-specific labels where the behavior is Claude
specific. Codex OAuth behavior remains available for `codex_default`.

### 3.3 OAuth Terminal Flow

When the operator chooses `Connect with Claude OAuth`, Mission Control opens an
OAuth terminal session anchored to the provider profile row:

```text
Settings Provider Profile row
  -> OAuth Session API
  -> MoonMind.OAuthSession workflow
  -> short-lived Claude auth-runner container
  -> MoonMind PTY/WebSocket bridge
  -> browser terminal
  -> claude login
  -> claude_auth_volume mounted as CLAUDE_HOME
  -> volume verification
  -> provider profile registration/update
```

The terminal is for credential enrollment and repair only. It is not the normal
task execution surface for Claude runs.

### 3.4 Claude Sign-In Ceremony

Claude OAuth uses the same browser-terminal infrastructure as Codex OAuth, but
the operator ceremony is different.

Codex uses device-code auth. Claude's interactive flow prints or opens a Claude
authentication URL, then expects the operator to paste the returned auth token or
authorization code back into the terminal.

The Claude OAuth ceremony is:

1. Operator selects `Connect with Claude OAuth` from the Settings provider
   profile row.
2. MoonMind creates an OAuth session for `runtime_id = "claude_code"` and opens
   the in-browser terminal view.
3. The terminal attaches to a short-lived auth-runner container with
   `claude_auth_volume` mounted as `CLAUDE_HOME`.
4. MoonMind starts Claude's interactive login command in that PTY.
5. Claude prints an authentication URL in the terminal.
6. Operator opens the URL in a normal browser tab or window and signs in with
   Anthropic.
7. Claude/Anthropic returns an auth token or authorization code to the operator.
8. Operator pastes that token or code back into the MoonMind browser terminal.
9. The PTY bridge forwards the pasted input to the Claude CLI process.
10. Claude CLI writes the resulting account-auth material into the mounted
    Claude home.
11. Operator or UI finalization triggers volume verification.
12. MoonMind registers or updates the `claude_anthropic` provider profile.

During this ceremony, the OAuth session should remain in an operator-waiting
state such as `awaiting_user` while the operator completes the external
Anthropic sign-in step. The terminal must remain attached long enough for the
operator to paste the returned token or code.

The pasted token or code is transient terminal input. MoonMind must not store it
as a Managed Secret, return it through API responses, write it to artifacts, or
persist it in provider profile rows. The durable credential output of the flow
is the Claude auth material written by the Claude CLI into the auth volume.

## 4. OAuth Session Backend

Claude OAuth should use the same OAuth Session API and workflow family as other
volume-backed CLI OAuth runtimes:

- `POST /api/v1/oauth-sessions`
- `POST /api/v1/oauth-sessions/{session_id}/terminal/attach`
- WebSocket terminal attach
- `POST /api/v1/oauth-sessions/{session_id}/finalize`
- cancel, retry, and history endpoints where available

For `runtime_id = "claude_code"`, the provider registry should define:

```yaml
runtime_id: claude_code
auth_mode: oauth
session_transport: moonmind_pty_ws
default_volume_name: claude_auth_volume
default_mount_path: /home/app/.claude
provider_id: anthropic
provider_label: Anthropic
bootstrap_command:
  - claude
  - login
success_check: claude_config_exists
```

The auth runner must set Claude-specific home environment variables so the
interactive login writes into the mounted auth volume:

- `HOME=/home/app`
- `CLAUDE_HOME=/home/app/.claude`
- `CLAUDE_VOLUME_PATH=/home/app/.claude`

The runner should clear competing API-key environment variables during OAuth
enrollment so the login state comes from the account-auth flow, not an ambient
key:

- `ANTHROPIC_API_KEY`
- `CLAUDE_API_KEY`

## 5. Verification

Finalization verifies the auth volume before registering or updating the
provider profile.

For Claude OAuth, verification should check for Claude account-auth material
under the mounted Claude home. The verifier may accept known Claude credential
artifacts such as:

- `credentials.json`
- `settings.json` when it contains evidence that Claude completed account setup
- any other stable Claude CLI account-auth artifact explicitly documented by the
runtime adapter

Verification must return only secret-free metadata, such as:

- `verified`
- `status`
- `reason`
- credential artifact counts
- timestamps

It must not return credential file contents, tokens, environment dumps, or raw
directory listings that could expose secrets.

## 6. Profile Registration

After verification succeeds, MoonMind registers or updates the OAuth-backed
provider profile with:

```yaml
credential_source: oauth_volume
runtime_materialization_mode: oauth_home
volume_ref: claude_auth_volume
volume_mount_path: /home/app/.claude
```

The Provider Profile Manager is then synced for `runtime_id = "claude_code"` so
new runs can select the updated profile.

## 7. Runtime Launch Behavior

At Claude task launch, MoonMind resolves the selected provider profile and
materializes the OAuth home into the runtime container according to the
provider-profile materialization contract:

1. Resolve `claude_anthropic`.
2. Apply `clear_env_keys`.
3. Mount or project `claude_auth_volume` at the configured Claude home path.
4. Set Claude home environment variables consistently for the runtime.
5. Launch `claude_code`.

The launch path must not place raw credential file contents in workflow history,
logs, or artifacts.

## 8. API-Key Auth Is Separate

The same Settings surface should also make API-key auth easy to choose. That
path uses a different provider profile shape:

```yaml
credential_source: secret_ref
runtime_materialization_mode: api_key_env
env_template:
  ANTHROPIC_API_KEY:
    from_secret_ref: anthropic_api_key
```

API-key enrollment stores the key in Managed Secrets and binds the provider
profile to that secret. It is not an OAuth session and should not run through
the browser terminal.

## 9. Security Requirements

- Only authorized operators can start, attach to, cancel, finalize, or repair
  Claude OAuth sessions.
- Browser terminal attach tokens are short-lived and single-use.
- Raw credentials are never returned to the browser after login.
- Terminal output, failure reasons, logs, and artifacts are redacted for
  secret-like values.
- Provider profile rows store refs and metadata only, never credential file
  contents.
- OAuth auth volumes are credential stores, not task workspaces or audit
  artifacts.

## 10. Related Documents

- `docs/ManagedAgents/OAuthTerminal.md`
- `docs/Security/ProviderProfiles.md`
- `docs/UI/SettingsTab.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
