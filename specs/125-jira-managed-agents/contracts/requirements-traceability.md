# Requirements Traceability: Jira Tools for Managed Agents

| DOC-REQ | FR Mapping | Implemented Surface | Validation Evidence |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001 | `moonmind/integrations/jira/auth.py`, `moonmind/integrations/jira/tool.py`, `moonmind/mcp/jira_tool_registry.py`, `api_service/api/routers/mcp_tools.py` | `tests/unit/integrations/test_jira_auth.py`, `tests/unit/integrations/test_jira_tool_service.py`, `tests/unit/api/test_mcp_tools_router.py` |
| DOC-REQ-002 | FR-002, FR-003 | `moonmind/config/settings.py`, `moonmind/integrations/jira/auth.py` | `tests/config/test_atlassian_settings.py`, `tests/unit/integrations/test_jira_auth.py` |
| DOC-REQ-003 | FR-004, FR-005 | `moonmind/integrations/jira/auth.py`, `moonmind/integrations/jira/client.py`, `moonmind/integrations/jira/errors.py` | `tests/unit/integrations/test_jira_auth.py`, `tests/unit/integrations/test_jira_client.py` |
| DOC-REQ-004 | FR-006 | `moonmind/mcp/jira_tool_registry.py`, `moonmind/mcp/__init__.py`, `api_service/api/routers/mcp_tools.py` | `tests/unit/mcp/test_jira_tool_registry.py`, `tests/unit/api/test_mcp_tools_router.py` |
| DOC-REQ-005 | FR-008 | `moonmind/integrations/jira/client.py` | `tests/unit/integrations/test_jira_client.py` |
| DOC-REQ-006 | FR-009, FR-010 | `moonmind/config/settings.py`, `moonmind/integrations/jira/auth.py`, `moonmind/integrations/jira/client.py` | `tests/config/test_atlassian_settings.py`, `tests/unit/integrations/test_jira_auth.py`, `tests/unit/integrations/test_jira_client.py` |
| DOC-REQ-007 | FR-006, FR-007, FR-013, FR-015 | `moonmind/integrations/jira/models.py`, `moonmind/integrations/jira/tool.py`, `moonmind/mcp/jira_tool_registry.py` | `tests/unit/integrations/test_jira_tool_service.py`, `tests/unit/mcp/test_jira_tool_registry.py`, `tests/unit/api/test_mcp_tools_router.py` |
| DOC-REQ-008 | FR-011, FR-012 | `moonmind/config/settings.py`, `moonmind/integrations/jira/models.py`, `moonmind/integrations/jira/tool.py` | `tests/config/test_atlassian_settings.py`, `tests/unit/integrations/test_jira_tool_service.py`, `tests/unit/mcp/test_jira_tool_registry.py`, `tests/unit/api/test_mcp_tools_router.py` |
| DOC-REQ-009 | FR-013, FR-014, FR-015, FR-016 | `moonmind/integrations/jira/adf.py`, `moonmind/integrations/jira/tool.py` | `tests/unit/integrations/test_jira_tool_service.py` |
| DOC-REQ-010 | FR-005, FR-009, FR-010, FR-017 | `moonmind/integrations/jira/client.py`, `moonmind/integrations/jira/errors.py` | `tests/unit/integrations/test_jira_client.py` |
| DOC-REQ-011 | FR-018, FR-019 | `moonmind/integrations/jira/auth.py`, `moonmind/integrations/jira/tool.py`, `moonmind/config/settings.py` | `tests/unit/integrations/test_jira_auth.py`, `tests/unit/integrations/test_jira_tool_service.py`, `tests/config/test_atlassian_settings.py` |
| DOC-REQ-012 | FR-020 | `tests/config/test_atlassian_settings.py`, `tests/unit/integrations/test_jira_auth.py`, `tests/unit/integrations/test_jira_client.py`, `tests/unit/integrations/test_jira_tool_service.py`, `tests/unit/mcp/test_jira_tool_registry.py`, `tests/unit/api/test_mcp_tools_router.py` | Focused run: `./tools/test_unit.sh tests/config/test_atlassian_settings.py tests/unit/integrations/test_jira_auth.py tests/unit/integrations/test_jira_client.py tests/unit/integrations/test_jira_tool_service.py tests/unit/mcp/test_jira_tool_registry.py tests/unit/api/test_mcp_tools_router.py` -> `28 passed`; full run: `./tools/test_unit.sh` -> unrelated failures in `tests/unit/api/routers/test_task_dashboard.py` |

Implementation-scope validation:

- `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` -> passed
- `bash ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` -> blocked in this environment because the script requires `mapfile`, which is unavailable in the installed Bash
