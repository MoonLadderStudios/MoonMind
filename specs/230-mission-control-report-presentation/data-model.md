# Data Model: Surface Canonical Reports in Mission Control

## Report Presentation

- Source: Latest execution artifact query for `link_type=report.primary&latest_only=true`.
- Fields:
  - primary artifact identity and status
  - display title/name from artifact metadata
  - report type/scope from artifact metadata when present
  - open target derived from `default_read_ref`, explicit download URL, or artifact ID
  - viewer label derived from `render_hint`, `content_type`, `metadata.name`, and `metadata.title`
- Validation:
  - exists only when server data returns a canonical `report.primary`
  - no local status or canonical identity is fabricated from generic artifacts

## Related Report Content

- Source: Existing execution artifact list rows with report links.
- Fields:
  - artifact identity
  - related report kind: `report.summary`, `report.structured`, or `report.evidence`
  - label/title from link label or metadata title/name
  - content type and size/status metadata
  - open target derived from artifact presentation fields
- Validation:
  - each row remains individually openable where access permits
  - related report content does not replace the generic artifact list

## Artifact Viewer Selection

- Source fields:
  - `default_read_ref`
  - `download_url`
  - `render_hint`
  - `content_type`
  - `metadata.name`
  - `metadata.title`
- Viewer categories:
  - markdown
  - JSON
  - text
  - diff
  - image
  - PDF/binary metadata or download/open
- Validation:
  - default read ref takes precedence for report open targets
  - raw download remains available only through existing artifact access routes

## State Transitions

- No new persisted state transitions are introduced.
- UI state transitions:
  - latest `report.primary` loading -> report panel shown when present
  - latest `report.primary` loading -> no report panel when absent
  - related artifact rows are derived from existing artifact list query results
