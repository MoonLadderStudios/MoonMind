# Requirements Traceability: Jira UI Runtime Config

| Source Requirement | Functional Requirements | Planned Implementation Surfaces | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-002, FR-006, FR-009 | Omit Jira blocks when disabled; keep non-Jira Create page config intact; expose only MoonMind-owned endpoint templates when enabled. | Unit tests assert `sources.jira` and `system.jiraIntegration` are absent when disabled and existing runtime config keys remain present. |
| DOC-REQ-002 | FR-004, FR-009 | Extend the existing dashboard runtime config builder rather than adding browser-side config or direct Jira calls. | Unit tests call `build_runtime_config()` and verify endpoint templates are MoonMind API paths. |
| DOC-REQ-003 | FR-003, FR-006, FR-009 | Keep Jira UI rollout independent from backend Jira tool enablement and avoid changes to task submission or Create page editing semantics. | Unit tests assert Jira UI blocks are controlled by the Create-page rollout flag; no tests require Jira credentials or Jira tool enablement. |
| DOC-REQ-004 | FR-001, FR-002, FR-003 | Add a Create-page-specific Jira UI enabled setting with disabled-by-default behavior. | Unit tests cover disabled omission and enabled presence. |
| DOC-REQ-005 | FR-004, FR-005 | Publish the six source entries and four integration settings when enabled. | Unit tests assert the exact endpoint templates and default setting values. |
| DOC-REQ-006 | FR-001, FR-005, FR-009 | Include `system.jiraIntegration.enabled` as the affirmative UI gate and keep all Jira URLs under MoonMind API paths. | Unit tests assert `enabled` is true when present and endpoint strings are the documented MoonMind API templates. |

All `DOC-REQ-*` identifiers from `spec.md` map to at least one functional requirement and at least one validation path.
