# Contract: Create Page Repository Options

## Runtime Boot Payload

The Create page runtime config exposes repository suggestions under:

```ts
interface DashboardConfig {
  system?: {
    repositoryOptions?: {
      items: RepositoryOption[];
      error?: string | null;
    };
  };
}

interface RepositoryOption {
  value: string;
  label: string;
  source: "default" | "configured" | "github";
}
```

Rules:

- `value` is the exact owner/repo string that may be submitted as the task repository.
- `label` is display-only and must not contain credential material.
- `source` is non-secret provenance for operator-visible debugging and test assertions.
- `error` is optional, sanitized, and must not block manual entry.
- The browser treats this as suggestions, not as the exhaustive allowed repository set.

## Create Page Interaction

Rules:

- The repository field remains an editable text input.
- When `repositoryOptions.items` is non-empty, the field is associated with a datalist.
- Choosing an option updates the existing `repository` draft value.
- Submitting uses the selected or typed repository value in the existing execution create payload.
- Existing owner/repo validation remains authoritative at submit time.
