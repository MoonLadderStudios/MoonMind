# Remaining work: `docs/Tasks/ImageSystem.md`

**Source:** [`docs/Tasks/ImageSystem.md`](../../Tasks/ImageSystem.md)  
**Last synced:** 2026-03-24

## Open items

### Rollout & migration (§)

- **Phase 1:** `POST /artifacts` accepts image MIME types and yields valid Temporal `ArtifactRef`s in workflow variables.
- **Phase 2:** `vision.generate_context` activity + consistent injection into `mm.skill.execute` preparation.
- **Phase 3:** Remove legacy `with-attachments` queue ingest endpoint.
