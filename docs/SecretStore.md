# Secret Store Design (HashiCorp Vault for Task Workers)

Status: Proposed  
Owners: MoonMind Engineering  
Last Updated: 2026-02-16

## 1. Purpose

Define how MoonMind workers use HashiCorp Vault for sensitive values while executing canonical Task queue jobs (`type="task"`).

## 2. Goals

1. Keep raw secrets out of queue payloads, DB rows, and logs.
2. Let jobs reference secrets indirectly by stable IDs (for example `repoAuthRef`).
3. Enforce least privilege per worker and per repository.
4. Support rotation without changing queued jobs.

## 3. Non-Goals (Initial Rollout)

1. Replacing all existing worker auth in one release.
2. Storing large runtime artifacts in Vault.
3. Reworking queue APIs beyond targeted auth reference fields.

## 4. High-Level Model

Producer submits a Task job with a secret reference (not a token):

```json
{
  "type": "task",
  "payload": {
    "repository": "MoonLadderStudios/MoonMind",
    "requiredCapabilities": ["git", "codex"],
    "targetRuntime": "codex",
    "auth": {
      "repoAuthRef": "vault://kv/moonmind/repos/MoonLadderStudios/MoonMind#github_token",
      "publishAuthRef": null
    },
    "task": {
      "instructions": "Implement the assigned task",
      "skill": { "id": "auto", "args": {} },
      "runtime": { "mode": "codex", "model": null, "effort": null },
      "git": { "startingBranch": null, "newBranch": null },
      "publish": { "mode": "branch", "prBaseBranch": null, "commitMessage": null, "prTitle": null, "prBody": null }
    }
  }
}
```

Worker flow:

1. Claim job.
2. Resolve `auth.repoAuthRef` from Vault when present.
3. Configure transient git auth.
4. Run task stages.
5. Clear in-memory secret material.
6. Complete/fail with non-secret summaries.

## 5. Vault Architecture

### 5.1 Secrets engine

Use KV v2 mount (example: `kv/`) for static credentials.

Example paths:

- `kv/data/moonmind/repos/<owner>/<repo>`
- `kv/data/moonmind/workers/<worker-id>/github`
- `kv/data/moonmind/runtime/<env>/<runtime>`

### 5.2 Worker auth to Vault

Recommended order:

1. Kubernetes auth (k8s deployment)
2. AppRole with short-lived SecretID (Compose/VM)
3. Vault Agent auto-auth with local sink token file

Avoid long-lived broad-scope Vault tokens in environment variables.

### 5.3 Token lifecycle

- obtain short-lived token at startup
- renew while healthy
- re-authenticate on renewal failure
- fail secure when required secret cannot be resolved

## 6. Secret Reference Contract

### 6.1 URI format

Use:

- `vault://<mount>/<path>#<field>`

Examples:

- `vault://kv/moonmind/repos/MoonLadderStudios/MoonMind#github_token`
- `vault://kv/moonmind/workers/executor-01/github#token`

### 6.2 Validation rules

1. `mount`, `path`, and `field` are required.
2. Reject traversal patterns.
3. Allow only approved mounts.
4. Enforce max length and character constraints.

### 6.3 Secret shape examples

Repo token:

```json
{
  "github_token": "<github-token>",
  "username": "x-access-token",
  "host": "github.com"
}
```

Worker default token:

```json
{
  "token": "<github-token>",
  "host": "github.com"
}
```

## 7. Worker Integration Details

### 7.1 Payload extension

Canonical Task payload remains unchanged except optional `auth` extension:

- `auth.repoAuthRef` (string, optional)
- `auth.publishAuthRef` (string, optional, defaults to `repoAuthRef`)

### 7.2 Runtime behavior

At prepare/publish boundary:

1. If `auth.repoAuthRef` exists, resolve it before clone/fetch.
2. If publish enabled, resolve `auth.publishAuthRef` or fallback.
3. Configure `gh auth`/git credentials without token-in-URL patterns.

### 7.3 Credential setup preference

Preferred:

- `gh auth login --with-token`
- `gh auth setup-git`

Alternative:

- strict-permission temporary credential helper file

Never:

- place tokens in repository URLs
- persist token strings to queue payloads/events/artifacts

## 8. Authorization Alignment

### 8.1 Queue policy

Worker token policy should enforce:

- allowed repositories
- allowed job types
- capability scope

### 8.2 Vault policy

Worker identity should only read allowed repository paths.

Conceptual policy:

```hcl
path "kv/data/moonmind/repos/MoonLadderStudios/MoonMind" {
  capabilities = ["read"]
}
```

### 8.3 Defense in depth

Require both:

1. queue claim eligibility for repository
2. Vault read permission for `repoAuthRef`

If either fails, fail job with non-secret reason.

## 9. Implementation Plan

### Phase 1: Vault foundation

1. deploy Vault with KV v2 and audit logs
2. configure worker auth method
3. create least-privilege policies

### Phase 2: Task auth refs

1. extend Task payload model with optional `auth` object
2. add Vault client abstraction in worker runtime
3. resolve/apply credentials during prepare/publish stages
4. redact secret-like strings in logging paths

### Phase 3: Observability and resilience

1. add metrics for secret resolution success/failure/latency
2. correlate queue job ID with Vault request IDs (without secret values)
3. add integration tests for denied/expired/rotated credentials

### Phase 4: Remove temporary bridge

1. migrate private repo jobs away from plain `GITHUB_TOKEN`
2. keep env-token path only for local fallback and break-glass operations

## 10. Operational Runbook

When `repoAuthRef` resolution fails:

1. validate worker Vault auth
2. validate policy for target path
3. validate referenced field exists
4. retry job after remediation

When Vault is degraded:

1. stop claiming jobs that require Vault refs
2. continue jobs that do not need Vault refs
3. alert operators with non-secret failure metadata

## 11. Security Checklist

1. no raw PAT/OAuth material in payloads/events/artifacts
2. no token-in-URL clone style
3. short-lived renewable Vault tokens
4. Vault audit logs enabled
5. log redaction enabled and tested
6. rotation playbook validated

## 12. Summary

Use optional `auth.repoAuthRef`/`auth.publishAuthRef` in canonical Task jobs to decouple orchestration from secret material. Keep env-token auth (`docs/WorkerGitAuth.md`) as temporary bridge, then migrate private repo credentials to Vault-backed references with aligned queue/Vault policy.
