# Research: Author Governed Tool Steps

## FR-001 Tool Picker Source

Decision: Use the existing trusted `/api/mcp/tools` listing as the metadata source for Create-page Tool choices.
Evidence: `api_service/api/routers/mcp_tools.py` exposes `GET /tools`, and `moonmind/mcp/jira_tool_registry.py` registers Jira tool names with Pydantic JSON schemas.
Rationale: This keeps Tool selection tied to the same policy-checked trusted surface used for execution and avoids a separate catalog source.
Alternatives considered: Hardcoding Jira/GitHub tools in the frontend was rejected because it would drift from configured trusted tools and policy.
Test implications: Frontend integration test with mocked `/api/mcp/tools` response.

## FR-001/DESIGN-REQ-008 Grouping And Search

Decision: Derive display grouping from the tool id prefix before the first dot, with readable labels, and filter by id, description, group, and schema field names.
Evidence: Registered tools use ids such as `jira.get_issue` and `jira.transition_issue`; the current Tool input is free text in `frontend/src/entrypoints/task-create.tsx`.
Rationale: Prefix grouping matches current trusted tool naming and supports integration/domain grouping without introducing new backend metadata.
Alternatives considered: Adding a new group field to every tool definition was deferred because existing ids already encode the integration and no backend schema change is required for the first governed picker.
Test implications: Frontend test for grouped list and search filtering.

## FR-003 Schema-Guided Inputs

Decision: Generate schema guidance and field-aware JSON object editing from the selected tool input schema while keeping the submitted `tool.inputs` object contract.
Evidence: MCP tool metadata returns `inputSchema`; existing manual Tool authoring already submits `tool.inputs` as an object.
Rationale: This satisfies schema-driven authoring without requiring a full form generator in the first slice.
Alternatives considered: Full generated forms for every JSON Schema feature were rejected for this story because the existing Create page has a JSON-object Tool input model and the story can be validated with required-field guidance and object submission.
Test implications: Frontend test confirms selected schema fields are visible and submitted inputs remain object-shaped.

## FR-004 Dynamic Jira Options

Decision: Support the Jira transition target-status example by calling trusted `jira.get_transitions` only after a selected transition Tool has an issue key in inputs, then render target status choices into the governed Tool authoring surface.
Evidence: Trusted Jira tools include `jira.get_transitions`; the MM-576 brief names Jira target statuses as the dynamic option example.
Rationale: This provides one concrete dynamic option provider through MoonMind-owned trusted surfaces and fails closed when the option lookup fails.
Alternatives considered: Free-text target status entry was rejected because the acceptance criteria require dynamic providers to populate fields such as Jira target statuses.
Test implications: Frontend test mocks successful and failed transition lookups.

## FR-005/SC-004 Shell Guardrail

Decision: Preserve existing task contract shell-like forbidden key rejection and add regression coverage only if implementation touches that boundary.
Evidence: `moonmind/workflows/tasks/task_contract.py` rejects `command`, `cmd`, `script`, `shell`, and `bash` step keys; MM-563 tests already cover related behavior.
Rationale: MM-576 builds on the existing guardrail rather than changing execution semantics.
Alternatives considered: UI-only blocking was rejected in prior MM-563 work and remains insufficient.
Test implications: Final verification must cite existing unit coverage or rerun relevant tests.

## FR-006 Fail-Closed Validation

Decision: Block submission when the selected Tool id is not in trusted metadata, required schema fields are missing, dynamic option provider lookup failed, or selected dynamic value is outside returned options.
Evidence: Current Create page only checks missing Tool id and JSON object syntax.
Rationale: Governed authoring must not silently degrade to unchecked text entry when metadata or dynamic options are unavailable.
Alternatives considered: Allowing unknown tool ids for advanced users was rejected because it conflicts with governed typed Tool authoring.
Test implications: Frontend integration test for unknown tool and failed option provider.

## FR-007 Terminology

Decision: Keep the Step Type label as `Tool` and avoid Script, Activity, or worker-placement labels in the governed picker.
Evidence: Existing Create-page tests already assert Tool/Skill/Preset choices; `docs/Steps/StepTypes.md` section 10.1 keeps Tool as the user-facing term.
Rationale: This preserves the product vocabulary while adding richer Tool selection.
Alternatives considered: Renaming to Executable or Script was rejected by the source design.
Test implications: Existing terminology assertions stay active; new picker tests check no Script label is introduced.
