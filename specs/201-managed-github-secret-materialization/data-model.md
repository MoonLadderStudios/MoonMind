# Data Model: Managed GitHub Secret Materialization

## ManagedGitHubCredentialSource

Purpose: Enum-like source kind for launch-time GitHub credential resolution.

Values:
- `secret_ref`: Resolve an explicit secret reference.
- `managed_secret`: Resolve from the local-first managed secret fallback slugs.
- `environment`: Resolve from a process environment variable only at the launch boundary.

Validation:
- Unknown source kinds fail fast.
- `secret_ref` requires a non-blank `secretRef`.
- `managed_secret` and `environment` must not carry raw token values.

## ManagedGitHubCredentialDescriptor

Purpose: Non-sensitive launch contract data describing how GitHub auth should be materialized for host git operations.

Fields:
- `source`: ManagedGitHubCredentialSource.
- `secretRef`: Optional secret reference when `source = secret_ref`.
- `envVar`: Optional environment variable name when `source = environment`.
- `required`: Whether missing or unresolvable credentials should fail launch before clone.

Validation:
- Descriptor contains references and selectors only, never token values.
- Descriptor serializes safely into workflow/activity payloads.
- Required descriptors raise actionable, redaction-safe errors if unresolved.

## GitHubCredentialMaterialization

Purpose: Ephemeral in-memory result used only to build host git subprocess environment.

Fields:
- `token`: Resolved token value; never persisted.
- `source`: Source kind used for redaction-safe metadata.
- `required`: Whether unresolved token is fatal.

State transitions:
- `descriptor` -> `resolved materialization` immediately before workspace preparation.
- `resolved materialization` -> `git subprocess env` for clone/fetch/push setup.
- `git subprocess env` discarded after command completion.

## Managed Session Launch Request

Purpose: Existing Codex launch boundary extended with non-sensitive GitHub auth descriptor.

Rules:
- `environment` must not include raw `GITHUB_TOKEN` for managed-session GitHub clone auth.
- Container environment excludes raw GitHub token values.
- `githubCredential` descriptor may be included in activity payload and container launch payload because it is non-sensitive.
