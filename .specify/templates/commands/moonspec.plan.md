---
description: Execute the implementation planning workflow using the plan template to generate design artifacts.
handoffs: 
  - label: Create Tasks
    agent: moonspec.tasks
    prompt: Break the plan into tasks
    send: true
scripts:
  sh: .specify/scripts/bash/setup-plan.sh --json
agent_scripts:
  sh: .specify/scripts/bash/update-agent-context.sh __AGENT__
---

## User Input

```text
$ARGUMENTS
```

Run the resolved `moonspec-plan` skill, passing the user input unchanged. Load its `SKILL.md`; it owns prerequisites, hooks, status precedence, workflow steps, validation, and reporting. Resolve it from `$MOONMIND_ACTIVE_SKILLS_DIR` when exported, otherwise the host's available skill resolver or `.agents/skills/moonspec-plan`.

Apply the skill's status rules before generating or executing work: `unverified` preserves disclosures without tasks; `needs_human` preserves the exact decision, owner, and action without performing it. Testing, implementation, and task-coverage requirements apply only to executable rows. An all-Unverified story needs disclosures and final verification, not new code or tests.

Let the skill execute its hooks once; do not run a second command-level hook lifecycle.
