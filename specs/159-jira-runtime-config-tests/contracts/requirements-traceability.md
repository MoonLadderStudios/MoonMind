# Requirements Traceability: Jira Runtime Config Tests

| Source Requirement | Functional Requirements | Planned Implementation Surfaces | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-002, FR-003, FR-006, FR-007 | Omit Jira blocks when disabled; keep non-Jira Create page config intact; expose only MoonMind-owned endpoint templates when enabled. | Unit tests assert `sources.jira` and `system.jiraIntegration` are absent when disabled, backend Jira tooling does not control browser discovery, and existing runtime config keys remain present. |
| DOC-REQ-002 | FR-001, FR-006 | Extend the existing dashboard runtime config builder rather than adding browser-side config or direct Jira calls. | Unit tests call runtime config generation and verify Jira discovery appears only through the boot payload shape. |
| DOC-REQ-003 | FR-001, FR-002, FR-003 | Add or preserve a Create-page-specific Jira UI enabled setting with disabled-by-default behavior. | Unit tests cover disabled omission, enabled presence, and separation from backend Jira tool enablement. |
| DOC-REQ-004 | FR-004, FR-005 | Publish the six source entries and four integration settings when enabled. | Unit tests assert endpoint templates and default setting values. |
| DOC-REQ-005 | FR-001, FR-003, FR-004, FR-005, FR-007 | Include `system.jiraIntegration.enabled` as the affirmative UI gate and keep all Jira URLs under MoonMind API paths. | Unit tests assert `enabled` is true when present and endpoint strings are the documented MoonMind API templates. |

All `DOC-REQ-*` identifiers from `spec.md` map to at least one functional requirement and at least one validation path.
