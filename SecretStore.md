# Secret Store Design (HashiCorp Vault for Worker Secrets)

## Purpose

This document defines how MoonMind workers should use HashiCorp Vault for sensitive values, especially repository credentials referenced by queue payloads (for example `repoAuthRef`).

It also covers how Vault can improve Codex CLI authentication operations.

## Goals

1. Keep raw secrets out of queue payloads, DB rows, and worker logs.
2. Let jobs reference secrets indirectly by stable IDs (for example `repoAuthRef`).
3. Enforce least privilege per worker and per repository.
4. Support secret rotation without changing queued jobs.

## Non-goals for initial rollout

1. Replacing all existing worker auth in one release.
2. Storing every large runtime artifact in Vault.
3. Rewriting the queue API surface beyond targeted fields.

## High-level model

Producer submits a job with a secret reference, not a token value:

```json
{
  "type": "codex_exec",
  "payload": {
    "repository": "MoonLadderStudios/MoonMind",
    "repoAuthRef": "vault://kv/moonmind/repos/MoonLadderStudios/MoonMind#github_token",
    "instruction": "Implement the assigned task"
  }
}
```

Worker execution flow:

1. Claim job.
2. Resolve `repoAuthRef` from Vault.
3. Configure transient git auth in-process.
4. Clone/fetch/push as needed.
5. Clear in-memory secret material.
6. Complete/fail job with no raw secret exposure.

## Vault architecture

## 1) Secrets engine

Use KV v2 mount (example: `kv/`) for static credential values.

Example paths:

- `kv/data/moonmind/repos/<owner>/<repo>`
- `kv/data/moonmind/workers/<worker-id>/github`
- `kv/data/moonmind/codex/<shard-or-env>`

## 2) Worker authentication to Vault

Recommended in order:

1. Kubernetes auth (if running in k8s).
2. AppRole with short-lived SecretID (if running via Compose/VM).
3. Vault Agent auto-auth and local sink file for token delivery.

Avoid long-lived root or broad-scope Vault tokens in environment variables.

## 3) Token lifecycle

- Worker obtains short-lived Vault token at startup.
- Renew periodically while worker is healthy.
- Re-authenticate on renewal failure.
- Fail secure if Vault is unavailable and required secrets are missing.

## Data contract for `repoAuthRef`

## URI format

Use an explicit URI-like convention:

- `vault://<mount>/<path>#<field>`

Examples:

- `vault://kv/moonmind/repos/MoonLadderStudios/MoonMind#github_token`
- `vault://kv/moonmind/workers/executor-01/github#token`

## Validation rules

1. `mount`, `path`, and `field` are required.
2. Reject path traversal patterns.
3. Allow only approved mounts.
4. Enforce max length.

## Secret shape examples

Repo-scoped PAT:

```json
{
  "github_token": "ghp_xxx",
  "username": "x-access-token",
  "host": "github.com"
}
```

Worker default credential:

```json
{
  "token": "ghp_xxx",
  "host": "github.com"
}
```

## Worker integration details

## 1) Queue payload changes

Add optional fields to `codex_exec` payload:

- `repoAuthRef` (string, optional)
- `publishAuthRef` (string, optional, defaults to `repoAuthRef` if omitted)

Keep existing `repository`, `ref`, `publish` behavior unchanged.

## 2) Runtime behavior

At repository prep time:

1. If `repoAuthRef` exists, resolve secret from Vault.
2. Configure git credential flow without placing token in command args.
3. Run clone/fetch/checkout.
4. If `publish.mode != none`, use `publishAuthRef` or fallback.

## 3) Preferred git credential setup

Preferred:

- Use `gh auth login --with-token` and `gh auth setup-git` with token from Vault.

Alternative:

- Use a custom credential helper script that reads token from a temporary file with strict permissions.

Never:

- Append token to repository URLs.
- Persist token to queue payload, artifacts, or plain logs.

## Authorization and policy alignment

Vault access must align with existing worker authorization controls.

## 1) Queue policy (already present)

- Worker tokens can enforce allowed repositories and allowed job types.

## 2) Vault policy (new)

- Worker identity should only read paths for repositories it is allowed to process.

Example policy (conceptual):

```hcl
path "kv/data/moonmind/repos/MoonLadderStudios/MoonMind" {
  capabilities = ["read"]
}
path "kv/data/moonmind/repos/MoonLadderStudios/*" {
  capabilities = ["list"]
}
```

## 3) Defense in depth

Require both:

1. Queue claim eligibility for repository.
2. Vault read permission for `repoAuthRef`.

If either fails, fail the job with a clear non-secret error.

## Codex CLI OAuth improvements with Vault

Current pattern uses persistent OAuth volumes for Codex CLI login. Vault can improve this in two practical ways.

## Option A (recommended first): protect OAuth operational data, not raw session state

1. Keep the current persistent `.codex` volume model for runtime compatibility.
2. Store metadata in Vault:
- shard ownership
- last preflight status
- credential refresh timestamps
3. Encrypt/backup `.codex` volume snapshots using Vault Transit keys before off-host storage.

Benefits:

- Better disaster recovery and auditability.
- No need to force Codex CLI into an unsupported auth path.

## Option B (if supported by your Codex CLI mode): Vault-injected API key auth

1. Store `CODEX_API_KEY` in Vault.
2. Inject short-lived key material at startup via Vault Agent template or runtime fetch.
3. Eliminate long-lived OAuth session artifacts where feasible.

Benefits:

- Easier rotation.
- Lower risk of stale OAuth cache state.

Tradeoff:

- Depends on CLI auth mode support and feature parity.

## What to avoid for Codex auth

1. Writing OAuth refresh tokens directly into queue payloads.
2. Logging Codex auth files or their contents.
3. Sharing a single Codex auth volume between multiple workers.

## Implementation plan

## Phase 1: Vault foundation

1. Deploy Vault and enable KV v2 + audit logs.
2. Configure worker auth method (Kubernetes auth or AppRole).
3. Create least-privilege policies per worker identity.

## Phase 2: `repoAuthRef` support

1. Extend `codex_exec` payload schema with `repoAuthRef`.
2. Add Vault client abstraction in worker runtime.
3. Resolve and apply credentials during repository preparation.
4. Add redaction for any secret-like strings in exception/log pipelines.

## Phase 3: policy and observability hardening

1. Add metrics for secret resolution success/failure and latency.
2. Correlate queue job ID with Vault request IDs (without exposing secret values).
3. Add integration tests for:
- valid ref resolution
- denied path access
- expired Vault token
- secret rotation while jobs are queued

## Phase 4: Codex auth improvements

1. Add Vault-backed metadata and backup encryption for Codex auth volumes.
2. Evaluate API key mode migration where available.

## Operational runbook

When `repoAuthRef` resolution fails:

1. Check Vault auth status for the worker identity.
2. Verify policy allows `read` on the target path.
3. Verify field exists in the secret document.
4. Re-run job after remediation.

When Vault is degraded:

1. Stop claiming new jobs requiring secret resolution.
2. Keep processing jobs that do not require Vault-resolved credentials.
3. Alert operators with job IDs and non-secret failure reasons.

## Security checklist

1. No raw PAT/OAuth material in job payloads.
2. No token-in-URL clone style.
3. Vault tokens are short-lived and renewable.
4. Vault audit logs enabled.
5. Worker logs redact known secret patterns.
6. Secret rotation playbook tested.

## Summary

Use `repoAuthRef` + Vault to decouple job orchestration from secret material. Keep the current fast PAT path only as a temporary bridge (`WorkerGitAuth.md`), then migrate private repo credentials to Vault-backed references and align Vault policies with existing worker repository authorization rules.
