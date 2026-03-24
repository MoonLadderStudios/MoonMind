# Remaining work: `docs/ManagedAgents/CursorCli.md`

**Source:** [`docs/ManagedAgents/CursorCli.md`](../../ManagedAgents/CursorCli.md)  
**Last synced:** 2026-03-24

## Open items

### §13 Implementation Plan (all unchecked in source)

**Phase 1 — Binary integration:** Dockerfile, `tools/auth-cursor-volume.sh`, verify `agent` CLI in container, `.env.example`.

**Phase 2 — Adapter wiring:** `resolve_volume_mount_env`, `CURSOR_API_KEY` scrubbing, `_build_cursor_command`, NDJSON parser, `activity_catalog` + worker capabilities.

**Phase 3 — Auth profile:** DB migration seed, `AuthProfileManager` on API startup, optional compose volume.

**Phase 4 — Permissions:** `.cursor/cli.json` from approval policies, `.cursor/rules/moonmind-task.mdc`, optional MCP wiring.

**Phase 5 — Testing:** Unit tests, Docker integration test, 429/cancel paths, dashboard visibility.

### §14 Open questions

Billing, auto-update pinning, cloud agent handoff, multi-model routing, `CURSOR_CONFIG_DIR` — resolve when scoping product behavior.
