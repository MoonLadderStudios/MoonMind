# Remaining work: `docs/Temporal/LiveTaskManagement.md`

**Source:** [`docs/Temporal/LiveTaskManagement.md`](../../Temporal/LiveTaskManagement.md)  
**Last synced:** 2026-03-24

## Open items

### Rollout (§11)

- **Phase 1 — Live log tailing:** `web_ro` for managed runs, Live Output panel, tmate RO embed, session lifecycle UX.
- **Phase 2 — Live terminal handoff:** Live Session Card, pause/resume, RW grant/revoke TTL, operator messages.
- **Phase 3 — Post-session artifacts:** `transcript.log` artifact + detail page link.

### Open questions (§12)

- iframe vs xterm-style widget for `web_ro`.
- Default-on log tailing vs provisioned-only.
- Static terminal snapshot artifact on completed detail.
