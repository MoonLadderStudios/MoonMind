# Static host startup paths

`RETIREMENT_INVENTORY` in `moonmind/omnigent/legacy_retirement.py` records active
provider-specific scripts, Compose services and profiles, and the legacy image
variable. Remove a surface from that inventory when its code is removed, while
retaining rows for consumers that still exist.

| Path | Current consumer and responsibility |
|---|---|
| `start-codex-oauth-host.sh` / `start-claude-oauth-host.sh` | `OmnigentOAuthHostRuntime` snapshots and invokes these wrappers for on-demand launches. Each selects its runtime pack and delegates to `start-omnigent-host.sh`. Keep them while those callers remain. |
| `start-omnigent-host.sh` | Cold bootstrap and standalone native use of the optional Compose roles. Planned workflow executions use the shared host launcher and its runtime script projection. |
| `check-omnigent-host.sh` | Shared readiness check for both dedicated Compose hosts; selects the probe from the configured runtime pack. |
| `init-oauth-host.sh` | Both Compose initializers and on-demand OAuth hosts. Accepts `/home/app/.codex` or `/home/app/.claude`, validates fixed state/artifact/cache paths, and checks OAuth ownership without repairing the OAuth home. |
| `OMNIGENT_HOST_IMAGE_REF` | Fallback in `static_hosts.resolve_static_host_image_ref` when `OMNIGENT_SHARED_HOST_IMAGE_REF` is unset. Retain while deployments still use the alias. |

## Rollback and retained work

The optional Compose roles keep their service names and persistent host state.
Planned workflow executions attach them through the generic lifecycle; retained
profile-bound histories and credential maintenance still use the old adapter.
Keep credential volumes, lease history and immutable plan identities intact
until those recorded consumers have drained. An explicit
`OMNIGENT_SHARED_HOST_IMAGE_REF` selects a prior image without changing topology;
it does not restore scripts mounted from the checkout. Revert the corresponding
checkout change when restoring startup scripts.
