---
description: Verify the final implementation against original instructions, a declarative document, an issue brief, or optional MoonSpec artifacts, plus repo guidance and required tests.
---

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Verification

Use the resolved `moonspec-verify` Skill to verify the original instructions or authoritative declarative source. Load its `SKILL.md` and `references/acceptance-policy.md`; the Skill owns scope resolution, required checks, evidence binding, verdicts, and continuation decisions. This command supplies the user input; the Skill owns acceptance and pre/post-verification hooks.

Resolve the Skill from `$MOONMIND_ACTIVE_SKILLS_DIR` when exported, otherwise the host's available Skill resolver or `.agents/skills/moonspec-verify`. Pass `$ARGUMENTS` unchanged, including the original issue/source, constraints, candidate, completion target, and evidence references. Do not require `spec.md`, `plan.md`, or `tasks.md` when another usable original baseline exists.

Run the Skill read-only and preserve its complete report and objective evidence. Let the Skill execute its hooks once; do not run a second command-level hook lifecycle.
