# Managed-runtime cleanup is enabled by default

Starting with this release, supported Docker Compose deployments automatically run the managed-runtime workspace janitor every hour. The schedule is enabled and cleanup is destructive after the configured retention window unless an operator has explicitly overridden the janitor settings.

After upgrade, ownership-verified terminal workspaces older than 30 days and eligible unreferenced local managed-runtime artifact directories older than 90 days may be deleted. Every pass remains protected by the existing store-readability, terminal-owner, active-turn, Docker-reference, path, symlink, lock, rescan, quarantine, grace, and deletion-budget gates. Run and session JSON records are still retained indefinitely by default.

To retain all managed-runtime state, set:

```text
MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED=false
```

To inspect candidates without deleting them, set:

```text
MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN=true
```

The default pass deletes at most 100 paths and the hourly schedule uses overlap `skip` and catch-up `last`, allowing a historical backlog to converge over repeated bounded runs.
