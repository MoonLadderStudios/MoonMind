# Data Model: Run Manifest Page Form

## Entities

### Manifest Run Draft

Represents the transient form state for one in-browser `/tasks/manifests` session.

Fields:
- `sourceKind`: `registry` or `inline`.
- `registryName`: registry-backed manifest name.
- `manifestName`: inline manifest name.
- `manifestContent`: inline YAML content.
- `action`: supported manifest action, currently `run` or `plan`.
- `dryRun`: optional boolean advanced option.
- `forceFull`: optional boolean advanced option.
- `maxDocs`: optional positive integer advanced option entered as text until validated.

Validation rules:
- Registry mode requires nonblank `registryName`.
- Inline mode requires nonblank `manifestName` and nonblank `manifestContent`.
- `maxDocs`, when provided, must be a positive integer.
- Raw secret-shaped values in submitted content or helper fields are rejected unless they are represented as env/Vault references.

### Manifest Run Submission

Represents the request emitted after validation.

Fields:
- `action`: selected manifest action.
- `title`: selected registry name or inline manifest name.
- `options`: omitted when empty; may contain `dryRun`, `forceFull`, and `maxDocs`.

State transitions:
- Draft -> validation error: user remains on `/tasks/manifests`, draft values are preserved, no manifest API request is sent.
- Draft -> inline upsert -> run request: inline mode saves content before creating the run.
- Draft -> run request: registry mode creates a run without re-uploading manifest body.
- Run request success -> recent runs refresh and success notice appears.
- Run request failure -> error notice appears and draft values are preserved.
