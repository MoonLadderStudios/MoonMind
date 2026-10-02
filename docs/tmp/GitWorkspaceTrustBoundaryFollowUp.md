# Host Git trust boundary follow-up

**Status:** Open implementation scope, 2026-10-02. The narrow safeguards below
are implemented; isolation of live-workspace Git is not.

## Implemented safeguards

- PublishService, the retained Codex worker, and Temporal's managed publisher
  terminate fetch option parsing before supplying remote/ref operands. Their
  per-file outbound scans disable both external diff and text conversion.
- Alternates normalization/recovery reads bounded regular files without following
  any symlink component, writes through fresh-inode atomic replacement, and never
  logs alternate-file content as a missing path. Existing valid object-store
  recovery remains supported.
- Managed publication uses the existing in-memory, GitHub-host-scoped credential
  helper. It no longer creates or executes workspace-owned authentication shims.
- Automatic legacy repository verification receives only its basic toolchain
  environment, not publisher tokens or arbitrary worker service secrets.

Behavioral evidence: `tests/unit/security/test_git_publication_guards.py`, plus
existing publication, object-store recovery, identity, retry, and verification
coverage. These checks do not establish isolation from worker-readable files.

## Remaining boundary

Managed and generic publishers, checkpoint capture, and reused-workspace
preparation still invoke Git on agent-writable metadata. Repository hooks,
includes, filters, helpers, and fsmonitor can execute programs. `safe.directory`,
output redaction, environment filtering, and the guards above are not a sandbox.
A first-party Omnigent UI does not make files produced by repository content or
agent execution safe to execute on the trusted worker.

Do not close the host-execution or hook/token findings based on this patch.
Repository verification also still needs the existing isolated execution route;
a reduced environment alone cannot prevent reads of worker-mounted files.

## Bounded implementation direction

1. Reuse `moonmind/publish/saved_candidate.py` for publication from an admitted
   saved result into a fresh trusted candidate repository. Do not import agent
   `.git/config`, hooks, includes, alternate paths, or sequencer instructions.
   Preserve admitted repository/branch authority and existing exact-head checks.
2. Run operations that genuinely need live workload Git through the already
   admitted runtime or Container Job boundary, with its workspace-only mounts,
   bounded lifetime, and role-specific credentials. No new runtime, scheduler,
   or second publication owner is needed.
3. Leave necessary host bookkeeping limited to trusted metadata or bounded data
   reads. Do not maintain a custom replica/transaction engine for Git refs,
   index, logs, packs, and configuration on every command.

## Concrete integration scope

- `OmnigentWorkspacePublicationService.publish_workspace` currently passes a
  host `run_command` closure to `PublishService.publish`. Use the existing
  `prepare_saved_candidate`, `push_candidate`, `observe_destination_branch`, and
  `publish_pull_request` methods for already-admitted saved content instead.
  Carry the saved-work digest, destination authority, base/head expectations,
  fixed commit identity, and persisted candidate SHA through this existing
  service contract; do not reacquire authority from workspace Git config.
- That is not a drop-in substitution: `SavedPublicationAdmission` currently
  requires distinct head/base branches and builds a commit from saved files.
  Same-branch Branch publication and `publish_existing_commits=True` must retain
  their semantics. Extend the existing candidate owner only as needed to admit
  the exact selected-history bundle and preserve its head/ancestry; never
  silently squash history or change an already verified candidate SHA.
- Live-workspace calls in `ManagedRuntimeLauncher._prepare_workspace_path`,
  `DockerCodexManagedSessionController._ensure_target_branch`,
  `TemporalSandboxActivities`, `TemporalAgentRuntimeActivities` checkpoint and
  publication helpers, `runtime/checkpoint_history.py`, and the retained Codex
  worker need a lifecycle-bound command runner. Reuse the existing
  `CommandRunner` result/timeout contract, adding the admitted workspace locator
  and execution binding at its owning service injection point rather than
  trusting an arbitrary host cwd. Omnigent `run_runtime_command` alone has no
  workspace authority and cannot decide which isolated runtime to use.
- Use an existing admitted runtime while it is alive; after termination, use the
  existing `ContainerJobSubmitRequest`/`DockerContainerJobBackend` path with
  admitted source correlation and workspace reference. Run a bounded capture or
  preparation operation per job, not a new container per individual Git read.
  Keep cleanup ordered after saved evidence/candidate retention. This requires
  lifecycle wiring and boundary integration tests, not just extra Git flags.
- The repository test auto-run in the retained Codex worker should use that same
  isolated job owner, with existing verification report/artifact collection.
  Publisher credentials belong only to the fresh trusted candidate's network
  operation; workspace inspection/test jobs receive no publication credential.

## Required acceptance

- Ordinary clone, existing checkout, branch switch, status, checkpoint,
  staged/unstaged changes, commit, fetch, push, and permitted local object-store
  recovery retain their supported behavior.
- A workload hook/filter/helper cannot execute on the worker or read publication
  credentials. Imported checkpoints and changed configuration exercise the same
  production boundaries, including replacement between validation and use.
- Failed or interrupted publication preserves the only saved candidate. A retry
  reconciles remote effects before repeating a push or PR operation. Concurrent
  branch changes use Git's existing exact-ref checks and never overwrite newer
  work or rewrite the admitted source/target intent.
- Large repositories have bounded command/output budgets without copying their
  complete Git metadata for every status or history read.
- Tests exercise these journeys through existing runtime and publication owners;
  a passing helper-only unit test is insufficient. Broader CI and credentialed
  runtime qualification must be reported separately from local regressions.
