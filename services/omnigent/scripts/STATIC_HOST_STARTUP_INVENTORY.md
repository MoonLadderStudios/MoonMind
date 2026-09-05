# Static host startup path inventory (MoonLadderStudios/MoonMind#3834)

Every old static-host startup path, its current state after consolidation,
and the condition that retires it. Legacy execution realizers and historical
compatibility are out of scope for removal in #3834 and stay.

| Old path | State after #3834 | Retirement condition |
|---|---|---|
| `services/omnigent/scripts/start-codex-oauth-host.sh` | Thin wrapper: exports `MOONMIND_OMNIGENT_RUNTIME_PACK_REF=codex-native-pack@1`, execs `start-omnigent-host.sh`. No lifecycle logic. | Remove when no deployment or replay-visible path references the name **and** the generic Codex static row passes exact-image + lifecycle gates. |
| `services/omnigent/scripts/start-claude-oauth-host.sh` | Thin wrapper: exports `MOONMIND_OMNIGENT_RUNTIME_PACK_REF=claude-native-pack@1`, execs `start-omnigent-host.sh`. No lifecycle logic. | Remove when no deployment or replay-visible path references the name **and** the generic Claude static row passes exact-image + lifecycle gates. |
| `services/omnigent/scripts/check-codex-oauth-host.sh` | Thin wrapper: exports the Codex pack ref, execs `check-omnigent-host.sh`. | Same as the Codex start wrapper. |
| `services/omnigent/scripts/check-claude-oauth-host.sh` | Thin wrapper: exports the Claude pack ref, execs `check-omnigent-host.sh`. | Same as the Claude start wrapper. |
| `services/omnigent/scripts/start-omnigent-host.sh` (new) | Generic entrypoint owning shared preparation + `omnigent host --server ... --non-interactive`. | Canonical; not retired. |
| `services/omnigent/scripts/check-omnigent-host.sh` (new) | Generic health/readiness contract. | Canonical; not retired. |
| `services/omnigent/scripts/init-codex-oauth-host.sh` | Still active: Codex init-container ownership staging. | Converge into `init-oauth-host.sh` dispatch once init-contract tests cover the Codex layout through the generic init. Not in #3834 scope. |
| `services/omnigent/scripts/init-oauth-host.sh` | Generic OAuth init (accepts `/home/app/.codex` or `/home/app/.claude`). | Canonical; not retired. |
| `docker-compose.yaml` static `image:` (legacy `OMNIGENT_HOST_IMAGE_REF` construction) | Replaced by the shared-image expression; the legacy variable survives only as a bounded alias in `static_hosts.resolve_static_host_image_ref` when `OMNIGENT_SHARED_HOST_IMAGE_REF` is unset. | Remove the alias after the rollback window closes and no operator relies on the legacy variable (grep deployments for `OMNIGENT_HOST_IMAGE_REF`). |
| `omnigent-host-codex` split `entrypoint: [/usr/bin/env -u ...]` + `command: [start-codex-oauth-host.sh]` | Removed; the single generic entrypoint fails closed on ambient selectors instead of scrubbing them. | Already removed in #3834; rollback is `git revert` or setting `OMNIGENT_SHARED_HOST_IMAGE_REF` to the prior digest. |
| Legacy execution realizers (`codex-profile-bound@1`, direct runtimes) | Untouched. | Explicit non-goal of #3834; retired only by #3835 after replay/rollback guards pass. |

## Rollback

Until both generic static rows pass:

- keep the `omnigent-host-codex` / `omnigent-host-claude` Compose profile names;
- keep explicit static host policy selection;
- set `OMNIGENT_SHARED_HOST_IMAGE_REF` to the prior pinned digest to roll the
  image back without touching service topology;
- never silently redirect an old immutable plan to a new Host Class or script:
  plan identity (`executionRealizerRef`, `hostClassRef`) still comes from the
  trusted planner, not from these wrappers.
