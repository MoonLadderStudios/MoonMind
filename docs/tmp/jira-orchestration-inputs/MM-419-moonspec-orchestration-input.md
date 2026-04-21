# MM-419 MoonSpec Orchestration Input

## Source

- Jira issue: MM-419
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Run registry or inline manifests from the Manifests page
- Labels: moonmind-workflow-mm-f78d0769-fb1e-4712-a140-47c34e83cc3f
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-419 from MM project
Summary: Run registry or inline manifests from the Manifests page
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-419 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-419: Run registry or inline manifests from the Manifests page

Source Reference
- Source Document: docs/UI/ManifestsPage.md
- Source Title: Manifests Page
- Source Sections:
  - Page structure
  - Run Manifest card
  - Source type toggle
  - Fields
  - Validation rules
  - Responsive behavior
  - Accessibility
  - Why inline form is preferred over a drawer/modal
- Coverage IDs:
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-005
  - DESIGN-REQ-006
  - DESIGN-REQ-007
  - DESIGN-REQ-013
  - DESIGN-REQ-014
  - DESIGN-REQ-016
  - DESIGN-REQ-018

User Story
As a dashboard user, I want a compact Run Manifest form on the Manifests page that supports registry names and inline YAML so I can start either kind of manifest run from the same context.

Acceptance Criteria
- Given /tasks/manifests loads, then a Run Manifest card is visible above Recent Runs and advanced options are collapsed by default.
- Given Registry Manifest mode is active, then manifest name and action are required and inline YAML is not required.
- Given Inline YAML mode is active, then non-empty YAML and action are required and registry manifest name is not required.
- Given a user switches between source modes, then values entered in each mode are preserved independently for the current session.
- Given max docs is provided, then non-positive or non-integer values are rejected before submit.
- Given submitted content or helper fields contain raw secret-style entries, then the UI rejects them or requires env/Vault references instead.
- Given the page is used with keyboard or assistive technology, then the source toggle, validation messages, YAML fallback, and primary action have accessible labels and field associations.
- Given the viewport narrows, then form controls stack in a clear form-first flow without hiding the primary Run Manifest action behind advanced options.

Requirements
- The Run Manifest card must be available directly on /tasks/manifests.
- Registry Manifest mode must accept a required manifest name and existing supported action values.
- Inline YAML mode must accept required YAML content and existing supported action values.
- Advanced options must include supported dry run, force full sync, max docs, and priority controls where those controls already exist or are backend-supported.
- The primary action label must be Run Manifest and must disable while submission is in progress.
- The implementation must not introduce raw secret entry into the UI.
- Autocomplete from /api/manifests may be added only as progressive enhancement with free-text fallback.

Implementation Notes
- This story is about adding compact Manifests-page authoring for manifest runs, not replacing the existing manifest execution contract.
- The UI must support both registry manifest names and inline YAML as separate source modes.
- Mode switching must preserve the entered values independently for the current session.
- Validation must happen before submit for required source fields, supported actions, max docs, and raw secret-style values.
- Preserve MM-419 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-419 is blocked by MM-420, whose embedded status is Done.
- Trusted Jira link metadata also shows MM-419 blocks MM-418, which is not a blocker for MM-419 and is ignored for dependency gating.
