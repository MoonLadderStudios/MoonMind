---
description: Verify the final implementation against original instructions, a declarative document, an issue brief, or optional MoonSpec artifacts, plus repo guidance and required tests.
---

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

**Check for extension hooks (before final verification)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.before_verify` key
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
- For each executable hook, output the following based on its `optional` flag:
  - **Optional hook** (`optional: true`):
    ```
    ## Extension Hooks

    **Optional Pre-Hook**: {extension}
    Command: `/{command}`
    Description: {description}

    Prompt: {prompt}
    To execute: `/{command}`
    ```
  - **Mandatory hook** (`optional: false`):
    ```
    ## Extension Hooks

    **Automatic Pre-Hook**: {extension}
    Executing: `/{command}`
    EXECUTE_COMMAND: {command}

    Wait for the result of the hook command before proceeding to the Outline.
    ```
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

## Verification

Use the resolved `moonspec-verify` Skill to verify the original instructions or authoritative declarative source. Load its `SKILL.md` and `references/acceptance-policy.md`; the Skill owns scope resolution, required checks, evidence binding, verdicts, and continuation decisions. This command supplies the user input and runs the extension hooks; it does not define a separate acceptance policy.

Resolve the Skill from `$MOONMIND_ACTIVE_SKILLS_DIR` when exported, otherwise the host's available Skill resolver or `.agents/skills/moonspec-verify`. Pass `$ARGUMENTS` unchanged, including the original issue/source, constraints, candidate, completion target, and evidence references. Do not require `spec.md`, `plan.md`, or `tasks.md` when another usable original baseline exists.

Run the Skill read-only and preserve its complete report and objective evidence. Perform post-verification hooks only after that result is available.

## Post-Execution Checks

**Check for extension hooks (after final verification)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.after_verify` key
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
- For executable hooks:
  - Optional hooks: report the hook command and prompt.
  - Mandatory hooks: output `EXECUTE_COMMAND: {command}` and execute or delegate it.
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently
