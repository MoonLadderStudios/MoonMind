# Secret Store Design (HashiCorp Vault for Managed Agents)

**Implementation tracking:** [`docs/tmp/remaining-work/ManagedAgents-SecretStore.md`](../tmp/remaining-work/ManagedAgents-SecretStore.md)

Status: Proposed  
Owners: MoonMind Engineering  
Last Updated: 2026-03-14

## 1. Purpose

Define how MoonMind Managed Agents and Temporal Workflows use HashiCorp Vault for sensitive values while executing automated tasks.

## 2. Goals

1. Keep raw secrets out of Temporal Workflow execution history, payloads, DB rows, and logs.
2. Let workflows reference secrets indirectly by stable IDs (for example `repoAuthRef`).
3. Enforce least privilege per worker and per repository.
4. Support rotation without changing inflight workflows.

## 3. Non-Goals (Initial Rollout)

1. Replacing all existing worker auth in one release.
2. Storing large runtime artifacts in Vault.
3. Reworking Temporal data converters beyond targeted auth reference fields.

## 4. High-Level Model

Producer submits a Temporal Workflow Execution with a secret reference (not a token) in the workflow input:

```json
{
  "repository": "MoonLadderStudios/MoonMind",
  "requiredCapabilities": ["git"],
  "auth": {
    "repoAuthRef": "vault://kv/moonmind/repos/MoonLadderStudios/MoonMind#github_token",
    "publishAuthRef": null
  },
  "task": {
    "instructions": "Implement the assigned task"
  }
}
```

Managed Agent flow:

1. Temporal Server schedules the `PrepareWorkspaceActivity`.
2. Worker leases the Activity and resolves `auth.repoAuthRef` from Vault.
3. Configure transient git auth inside the `temporal-worker-sandbox` workspace.
4. Run task stages (LLM agent loop).
5. Clear in-memory secret material.
6. Complete/fail the Activity and Workflow with non-secret summaries.

## 5. Vault Architecture

### 5.1 Secrets engine

Use KV v2 mount (example: `kv/`) for static credentials.

Example paths:

- `kv/data/moonmind/repos/<owner>/<repo>`
- `kv/data/moonmind/workers/<worker-id>/github`

### 5.2 Worker auth to Vault

Recommended order for the `temporal-worker-sandbox` deployment:

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

## 7. Managed Agent Integration Details

### 7.1 Workflow Input extension

The canonical Workflow Input supports the optional `auth` extension:

- `auth.repoAuthRef` (string, optional)
- `auth.publishAuthRef` (string, optional, defaults to `repoAuthRef`)

### 7.2 Runtime behavior

At prepare/publish boundary within Temporal Activities:

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
- persist token strings to Temporal history, events, or artifacts

## 8. Authorization Alignment

### 8.1 Routing policy

Temporal Task Queues and worker capabilities should enforce:

- allowed repositories
- allowed workflow types
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

1. Temporal Task Queue eligibility for repository routing
2. Vault read permission for `repoAuthRef`

If either fails, fail the Activity with a non-secret reason string so the workflow can safely terminate or alert operators.

## 9. Rollout expectations

Production secret handling converges on **Vault KV v2** with audited access, worker auth, least-privilege policies, **`repoAuthRef` / workflow `auth` objects** resolved in Activities (never in workflow history), logging redaction, **metrics and correlation** for resolution outcomes, and **removal of long-lived API-injected `GITHUB_TOKEN`** except local/break-glass. Phased completion is tracked in [`docs/tmp/remaining-work/ManagedAgents-SecretStore.md`](../tmp/remaining-work/ManagedAgents-SecretStore.md).

## 10. Operational Runbook

When `repoAuthRef` resolution fails:

1. validate worker Vault auth
2. validate policy for target path
3. validate referenced field exists
4. retry workflow/activity from the Temporal UI after remediation

When Vault is degraded:

1. Temporal gracefully retries Activities failing with Vault timeout errors, blocking workflow progress until Vault is restored.
2. alert operators with non-secret failure metadata.

## 11. Security Checklist

1. no raw PAT/OAuth material in Temporal history/events/artifacts
2. no token-in-URL clone style
3. short-lived renewable Vault tokens
4. Vault audit logs enabled
5. log redaction enabled and tested
6. rotation playbook validated

## 12. Summary

Use optional `auth.repoAuthRef`/`auth.publishAuthRef` in canonical Temporal Workflows to decouple orchestration from secret material. Keep env-token auth (`docs/ManagedAgents/WorkerGitAuth.md`) as temporary bridge, then migrate private repo credentials to Vault-backed references with aligned Temporal queue/Vault policy.
