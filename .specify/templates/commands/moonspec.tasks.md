---
description: Generate a one-story, TDD-first MoonSpec tasks.md with unit tests, integration tests, and source-design traceability.
handoffs:
  - label: Align Artifacts
    agent: moonspec.align
    prompt: Align MoonSpec artifacts before implementation
    send: true
  - label: Implement Project
    agent: moonspec.implement
    prompt: Start the implementation in phases
    send: true
  - label: Verify Final Implementation
    agent: moonspec.verify
    prompt: Verify the completed implementation against the original feature request
scripts:
  sh: .specify/scripts/bash/check-prerequisites.sh --json
---

## User Input

```text
$ARGUMENTS
```

Run the resolved `moonspec-tasks` skill, passing the user input unchanged. Load its `SKILL.md`; it owns prerequisites, hooks, status precedence, workflow steps, validation, and reporting. Resolve it from `$MOONMIND_ACTIVE_SKILLS_DIR` when exported, otherwise the host's available skill resolver or `.agents/skills/moonspec-tasks`.

Apply the skill's status rules before generating or executing work: `unverified` preserves disclosures without tasks; `needs_human` preserves the exact decision, owner, and action without performing it. Testing, implementation, and task-coverage requirements apply only to executable rows. An all-Unverified story needs disclosures and final verification, not new code or tests.

Let the skill execute its hooks once; do not run a second command-level hook lifecycle.
