# Contract: Mission Control Visual Tokens

## Scope

The shared Mission Control stylesheet defines the runtime token contract for page atmosphere and elevated chrome.

## Required Token Families

The same token names must exist in `:root` and `.dark`:

- `--mm-atmosphere-violet`
- `--mm-atmosphere-cyan`
- `--mm-atmosphere-warm`
- `--mm-atmosphere-base`
- `--mm-glass-fill`
- `--mm-glass-border`
- `--mm-glass-edge`
- `--mm-input-well`
- `--mm-elevation-panel`
- `--mm-elevation-floating`

## Consumption Rules

- `body` must use the three atmosphere layer tokens and the atmosphere base token.
- `.dark body` must use the same token names; only token values should change by theme.
- `.masthead::before` and `.panel` must consume `--mm-glass-fill` and `--mm-elevation-panel`.
- The Create page floating rail may consume `--mm-glass-*`, `--mm-input-well`, and `--mm-elevation-floating` as an elevated glass surface.
- Text, muted text, status, and focus behavior continue to use the existing semantic token families.

## Non-Goals

- No new runtime theme provider.
- No new build pipeline for tokens.
- No route-specific behavior changes.
- No change to task creation, Jira, GitHub, Temporal, or artifact payloads.
