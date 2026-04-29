# Quickstart: Agentic Skill Step Authoring

Coverage: MM-564; FR-001 through FR-007; SC-001 through SC-004; DESIGN-REQ-005 and DESIGN-REQ-015.

1. Open Mission Control Create.
2. Set Step Type to `Skill`.
3. Enter instructions for agentic work, for example `Implement MM-564 from the Jira brief`.
4. Optionally enter a Skill selector such as `moonspec-orchestrate`; leave blank only when `auto` semantics are intended.
5. Enable advanced step options and enter Skill Args as a JSON object, for example `{ "issueKey": "MM-564" }`.
6. Optionally enter required capabilities as CSV, for example `git`.
7. Submit the task.

Expected result: the submitted payload contains an executable `type: skill` step and preserves Skill args/capabilities (SC-001). Invalid Skill Args JSON blocks submission before `/api/executions` is called (SC-002). Existing Tool and Preset authoring checks continue to pass in the same Create-page target (SC-003), and the verification report maps DESIGN-REQ-005 and DESIGN-REQ-015 to implemented evidence (SC-004).
