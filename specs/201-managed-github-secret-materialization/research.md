# Research: Managed GitHub Secret Materialization

## Durable Launch Contract

Decision: Add compact, non-sensitive GitHub credential descriptor data to the managed-session launch request and avoid adding raw `GITHUB_TOKEN` to durable launch payloads.

Rationale: `docs/Security/SecretsSystem.md` requires durable contracts to carry references, not raw values. A typed descriptor lets workflow/activity payloads remain inspectable while giving the runtime launch boundary enough information to resolve the credential late.

Alternatives considered: Continuing to inject `GITHUB_TOKEN` into `LaunchCodexManagedSessionRequest.environment` was rejected because it can leak into durable payloads and container environment. Embedding an opaque untyped dict was rejected because the Temporal boundary is compatibility-sensitive and needs schema coverage.

## Resolution Boundary

Decision: Resolve GitHub credentials in the activity/controller launch path immediately before host git workspace preparation.

Rationale: Host git clone/fetch occurs before the managed session container starts, so that is the narrowest boundary that can support private repository workspace preparation without worker-local `gh auth setup-git`.

Alternatives considered: Resolving in workflow code was rejected because workflow history must remain free of raw secrets. Resolving inside the managed Codex container was rejected because clone happens on the host before container startup.

## Materialization Mechanism

Decision: Keep the existing environment-scoped git credential helper for host subprocesses, but drive it from a launch-scoped resolved value that is not stored on the request.

Rationale: The helper already limits materialization to git subprocess environment and avoids persistent worker git config. Reusing it minimizes behavioral blast radius while fixing the durable contract.

Alternatives considered: Running `gh auth setup-git` was rejected because it mutates worker-user state. Embedding credentials in repository URLs was rejected because URLs are often logged and persisted.

## Local-First Fallback

Decision: Preserve the existing precedence for explicit secret refs and well-known managed secret slugs `GITHUB_TOKEN` and `GITHUB_PAT`, but represent fallback use through a non-sensitive source kind.

Rationale: MM-320 explicitly requires existing local-first behavior to continue. The descriptor can express fallback intent without carrying the resolved token.

Alternatives considered: Requiring every launch to specify a secret ref was rejected because it would break the current UI-managed local-first flow.

## Testing Strategy

Decision: Cover schema serialization, activity launch shape, controller host git environment, docker/container omission, and redaction failure behavior with focused pytest tests.

Rationale: The risk is at the serialized Temporal/activity/controller boundary. Focused unit and boundary-style tests run inside managed-agent containers and do not require Docker availability.

Alternatives considered: A live private GitHub integration test is useful but not reliable in hermetic CI because it requires credentials and external network access.
