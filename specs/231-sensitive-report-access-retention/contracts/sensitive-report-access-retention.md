# Contract: Apply Report Access and Lifecycle Policy

## Artifact Service Boundary

The story is implemented through existing artifact service calls and HTTP routes:

- Create artifact: `TemporalArtifactService.create(...)` and `POST /api/artifacts`
- Complete artifact: `write_complete(...)` and `PUT /api/artifacts/{artifact_id}/content`
- Metadata/read policy: `get_metadata(...)` and `GET /api/artifacts/{artifact_id}`
- Raw download: `presign_download(...)`, `read(...)`, and download routes
- Pin: `pin(...)` and `POST /api/artifacts/{artifact_id}/pin`
- Unpin: `unpin(...)` and `DELETE /api/artifacts/{artifact_id}/pin`
- Delete: `soft_delete(...)`, lifecycle sweep, and `DELETE /api/artifacts/{artifact_id}`

## Expected Behavior

- A restricted report artifact with a generated preview returns metadata with:
  - `raw_access_allowed=false` for callers without raw access.
  - `preview_artifact_ref` set when a preview exists.
  - `default_read_ref` pointing at the preview artifact.
- Raw read and presign calls fail for callers without restricted raw access.
- Report metadata validation rejects unsupported keys, secret-like values, cookies, session tokens, raw access grants, and oversized inline payloads.
- Report retention defaults:
  - `report.primary` -> `long`
  - `report.summary` -> `long`
  - `report.structured` -> `standard` unless explicitly overridden
  - `report.evidence` -> `standard` unless explicitly overridden
- Pinning sets retention to `pinned`.
- Unpinning restores report-derived retention based on existing report links.
- Deleting one report artifact does not mutate unrelated observability artifacts linked to the same execution.

## Compatibility Notes

MoonMind is pre-release. This change updates internal artifact policy behavior directly and does not add compatibility aliases or alternate link semantics.
