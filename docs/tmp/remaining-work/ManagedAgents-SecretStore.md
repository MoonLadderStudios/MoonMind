# Remaining work: `docs/ManagedAgents/SecretStore.md`

**Source:** [`docs/ManagedAgents/SecretStore.md`](../../ManagedAgents/SecretStore.md)  
**Last synced:** 2026-03-24

## Open items

### §9 Implementation plan

- **Phase 1 — Vault foundation:** Deploy Vault KV v2, audit, worker auth, least-privilege policies.
- **Phase 2 — Workflow auth refs:** Workflow input `auth` object, Vault client in worker, resolve in prepare/publish activities, log redaction.
- **Phase 3 — Observability:** Metrics, correlation IDs, integration tests for denied/expired/rotated creds.
- **Phase 4 — Remove bridge:** Migrate off plain `GITHUB_TOKEN` from API server for private repo flows; keep env token for local/break-glass only.
