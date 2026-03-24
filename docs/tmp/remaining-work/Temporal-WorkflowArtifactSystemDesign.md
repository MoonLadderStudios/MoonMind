# Remaining work: `docs/Temporal/WorkflowArtifactSystemDesign.md`

**Source:** [`docs/Temporal/WorkflowArtifactSystemDesign.md`](../../Temporal/WorkflowArtifactSystemDesign.md)  
**Last synced:** 2026-03-24

## Open items

### §16 Deliverables checklist (all unchecked in source)

- **Deliverable A:** Artifact API contract — endpoints, auth/audit, multipart semantics.
- **Deliverable B:** Versioned `ArtifactRef` JSON schema + required fields + “no presigned URLs in workflow state” enforcement story.
- **Deliverable C:** Retention classes, default mapping by `link_type`, lifecycle manager (idempotent delete + tombstones).

### §17 Open questions

- Cross-execution shared artifacts + ACLs.
- Default retention for manifests/plans in regulated environments.
