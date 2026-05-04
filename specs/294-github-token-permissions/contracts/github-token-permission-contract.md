# Contract: GitHub Token Permission Improvements

## Canonical Credential Resolution

All GitHub repository operations use a shared resolver with this precedence:

1. Explicit token supplied by the caller.
2. Direct token environment sources: `GITHUB_TOKEN`, `GH_TOKEN`, `WORKFLOW_GITHUB_TOKEN`.
3. Secret reference environment sources: `GITHUB_TOKEN_SECRET_REF`, `WORKFLOW_GITHUB_TOKEN_SECRET_REF`.
4. Settings token reference: `MOONMIND_GITHUB_TOKEN_REF` / `integrations.github.token_ref`.

Contract:
- Successful resolution returns the token only to the immediate caller and returns redaction-safe source metadata separately.
- Failed resolution returns a redaction-safe diagnostic with missing or unresolvable source detail.
- Callers must not write raw tokens into workflow payloads, logs, artifacts, PR bodies, or durable metadata.

## Publish Contract

Branch publishing:
- Pushes the work branch using the resolved credential.
- Disables interactive credential prompts.
- Redacts token-like output from subprocess errors and logs.

PR publishing:
- Creates pull requests using the resolved credential.
- Prefer the existing REST GitHub service for deterministic error diagnostics.
- If a CLI path remains, it must receive `GH_TOKEN` and `GITHUB_TOKEN` in process environment and must not rely on existing `gh auth` state.

Failure output:
- Missing credential: explain which supported sources were considered.
- Unsupported remote/protocol: explain that personal access tokens require token-aware HTTPS Git operations.
- Missing permission: include a sanitized GitHub permission diagnostic when provider metadata is available.

## Permission Profiles

Indexing profile:
- Repository access: selected target repository.
- Required: Contents read.

Publish PR profile:
- Repository access: selected target repository.
- Required: Contents write, Pull requests write.
- Conditional: Workflows write when workflow files may be modified.

Readiness profile:
- Repository access: selected target repository.
- Required: Pull requests read.
- Required when checks are enabled: Commit statuses read, Checks read.
- Required when reaction fallback is enabled: Issues read.

Full PR automation profile:
- Contents read and write.
- Pull requests read and write.
- Commit statuses read.
- Checks read.
- Issues read.
- Workflows write when workflow files may be modified.

## GitHub Permission Diagnostic Shape

```json
{
  "operation": "create_pull_request",
  "httpStatus": 403,
  "message": "Resource not accessible by personal access token",
  "documentationUrl": "https://docs.github.com/...",
  "acceptedPermissions": "pull_requests=write",
  "requiredPermission": "Pull requests: write",
  "retryable": false
}
```

Rules:
- `message`, `documentationUrl`, and `acceptedPermissions` are included only when present and sanitized.
- Token-like strings must be replaced with `[REDACTED]`.
- The diagnostic is suitable for workflow summaries, Mission Control, and artifacts.

## Token Probe Contract

Input:

```json
{
  "repo": "owner/repo",
  "mode": "publish",
  "baseBranch": "main"
}
```

Output:

```json
{
  "repo": "owner/repo",
  "mode": "publish",
  "credentialSource": {
    "sourceKind": "settings_token_ref",
    "sourceName": "MOONMIND_GITHUB_TOKEN_REF",
    "resolved": true
  },
  "repositoryAccessible": true,
  "defaultBranchAccessible": true,
  "pullRequestAccessible": true,
  "permissionChecklist": [
    {"permission": "Contents", "level": "write", "required": true, "status": "passed"},
    {"permission": "Pull requests", "level": "write", "required": true, "status": "passed"},
    {"permission": "Workflows", "level": "write", "required": false, "status": "not_checked"}
  ],
  "diagnostics": [],
  "limitations": []
}
```

Rules:
- The probe checks the exact `owner/repo`.
- The probe must not validate by global repository list or classic OAuth scope headers.
- Missing permissions map to profile-specific checklist entries.
- Known limitations such as wrong resource owner, pending organization approval, multi-organization automation, or outside-collaborator limits are returned as explanatory notes when inferred from provider responses or operator guidance.

## Readiness Evidence Contract

Optional evidence permission failure:

```json
{
  "kind": "readiness_evidence_unavailable",
  "source": "github",
  "evidenceSource": "issue_reactions",
  "missingPermission": "Issues: read",
  "summary": "Reaction evidence unavailable; grant Issues read or disable reaction fallback.",
  "retryable": false
}
```

Rules:
- Missing optional permission does not imply the credential is globally invalid.
- Required evidence failures remain blockers.
- Other available evidence continues to be evaluated when policy allows.
