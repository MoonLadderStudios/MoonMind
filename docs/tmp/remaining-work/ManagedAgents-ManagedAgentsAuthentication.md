# Remaining work: `docs/ManagedAgents/ManagedAgentsAuthentication.md`

**Source:** [`docs/ManagedAgents/ManagedAgentsAuthentication.md`](../../ManagedAgents/ManagedAgentsAuthentication.md)  
**Last synced:** 2026-03-24

## Open items

### §8 Migration path

- **Phase 1:** Single default profile (minimal) — verify still accurate vs code.
- **Phase 2:** `managed_agent_auth_profiles` table + CRUD API + script `--check`/`--list`; `execution_profile_ref` into `AgentRun`.
- **Phase 3:** Multi-volume support and profile selector in `AgentRun`.
- **Phase 4:** Queuing, cooldown, 429 rotation observability.
- **Phase 5:** Dedicated `mm.activity.agent_runtime` fleet migration vs sandbox.
