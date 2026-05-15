# Contract: Runtime Command Preview

## Boot Payload Contract

The Create page dashboard config exposes browser-safe preview metadata.

```json
{
  "system": {
    "runtimeCommandPreview": {
      "capabilityVersion": "2026-05-13",
      "hintCatalogVersion": "2026-05-13",
      "runtimes": {
        "codex_cli": {
          "slashCommandPassthrough": true,
          "renderMode": "prompt_prefix",
          "commandHintsRef": "codex_cli"
        },
        "claude_code": {
          "slashCommandPassthrough": true,
          "renderMode": "prompt_prefix",
          "commandHintsRef": "claude_code"
        },
        "gemini_cli": {
          "slashCommandPassthrough": false,
          "renderMode": "plain_prompt"
        }
      },
      "knownRuntimeCommandHints": {
        "review": {
          "label": "Review",
          "aliases": ["/review"],
          "description": "Ask the selected runtime to review the current task or code state.",
          "argumentPolicy": { "allowed": true, "required": false },
          "bodyPolicy": { "allowed": true, "required": false }
        },
        "simplify": {
          "label": "Simplify",
          "aliases": ["/simplify"],
          "description": "Ask the selected runtime to simplify the implementation.",
          "argumentPolicy": { "allowed": true, "required": false },
          "bodyPolicy": { "allowed": true, "required": false }
        }
      }
    }
  }
}
```

Contract rules:

- The payload must not expose secrets, provider credentials, or executable command markup.
- `knownRuntimeCommandHints` is optional enrichment, not an allowlist.
- Missing runtime entries are treated as `slashCommandPassthrough: false`.
- The Create page may cache this metadata for the current page load.

## Preview Interaction Contract

### Known command on slash-capable runtime

Input:

```text
/review
Check this branch.
```

Expected preview:

- Status: runtime command.
- Label includes `/review` or the known hint label.
- Description may use known hint text.
- No warning.
- Authored instructions remain unchanged.

### Unknown command on slash-capable runtime

Input:

```text
/foo
Use provider behavior.
```

Expected preview:

- Status: pass-through runtime command.
- Hint status: opaque.
- No warning or error caused by missing hint.
- Authored instructions remain unchanged.

### Runtime without slash-command pass-through

Input:

```text
/review
Check this.
```

Expected preview:

- Status: unsupported or warning.
- Message tells the user to choose a slash-command capable runtime or escape the slash for literal text.
- Authored instructions remain unchanged.

### Escaped literal

Input:

```text
\/review
Treat as text.
```

Expected preview:

- Status: literal text intent.
- No executable command chip.
- Authored instructions remain unchanged.

### Edit mode with stored metadata

Input:

- Authoritative task input snapshot has authored instructions and `runtimeCommand` metadata.

Expected preview:

- Stored metadata is used for preview when it still corresponds to the restored instructions.
- Re-detection is allowed only when stored metadata is absent or no longer corresponds to the current instructions.
- Historical authored instructions are not rewritten.
