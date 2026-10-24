# Operator and machine access

MoonMind has one operator per instance. The operator interface uses local
transport or the deployment's approved ingress. It does not provision a person,
resolve a profile, issue application login sessions, or import upstream roles.
Provider OAuth, workflow/session owners, machine capabilities, and signing keys
remain independent authorities.

## Operator admission

`moonmind/security/operator_admission.py` owns transport and ingress validation.
`api_service/operator_admission.py` adapts it to HTTP and WebSocket requests.
Shared product routers use `api_service/auth_providers.py`; admission also requires
certified completion of the guarded data conversion. An incomplete conversion
returns `503 conversion_required`, and an unreadable completion returns
`503 unavailable`. Neither falls back to an account or a synthetic administrator.

Each admission checks current pending recovery work through an indexed owner
classification, rather than scanning retained artifact history or caching a
successful readiness result. Restored human/ambiguous owners and revoked grants
are visible immediately. The reserved disabled-mode scope is a separate index
class, so changing that deployment configuration does not require a data rewrite.

| Deployment | Admission |
| --- | --- |
| Local, with no public URL or an explicit loopback URL | Connecting loopback peer and loopback Host. Compose also recognizes its single kernel default gateway as the host publication peer when configured to publish loopback. Other bridge peers and forwarding claims confer no authority. |
| Approved non-loopback publication | `MOONMIND_TRUSTED_INGRESS=1` approves the installed publication. Host and mutation-origin checks still apply. Direct peers on the API container's own networks, other than its default gateway, do not qualify. An explicit loopback URL keeps local rules; a remote URL with configured trusted proxies keeps remote rules. |
| Remote public URL with trusted proxies | The connecting peer must match `MOONMIND_TRUSTED_PROXIES`, ingress must be approved, and the configured proxy identity header must carry one valid assertion. This is boolean admission; no local account, subject, membership, or role is imported. |

The Compose entrypoint disables Uvicorn forwarding-header rewriting so admission
sees the actual connecting peer. Gateway discovery requires the container marker
and deployment/publication configuration. Missing or ambiguous routing fails
closed. Remote publication needs a valid HTTPS public URL and its installed
access protection before predecessor account access is retired.

Ingress must strip caller assertions and inject its own. `X-Forwarded-*`,
`X-Real-IP`, untrusted identity headers, worker credentials, and URL parameters
never confer operator access. Presented machine/bearer credentials cannot fall
back to local operator admission. Machine protocols authenticate at their own
scoped endpoints.

`Host` must agree with the configured operator URL. A presented foreign
`Origin` or `Referer` on a mutation is denied independently of admission. Explicit
trusted browser origins belong in `MOONMIND_CORS_ALLOWED_ORIGINS`; the public URL
is included automatically. Wildcard credentialed CORS is refused. CORS alone is
not access control.

Application-account routes, including `/me`, `/api/v1/accounts/*`, and application
OIDC login/callback/logout, are retired. `AUTH_PROVIDER` does not select a product
login mode. Existing operator URLs and deployment-owned ingress settings remain
the access path. Omnigent's authentication configuration belongs to its runtime;
provider OAuth remains available through provider-profile/session APIs.

## Streams and durable work

HTTP, artifact access, SSE, native chat, WebSocket handshakes and reconnects use
the same operator boundary. URL credentials are ignored. Stream admission is
rechecked or expires within five minutes, including busy and idle live-log and
bridge event streams. A new request obtains a fresh ingress assertion.
Local revalidation detects configuration changes and does not pretend
to query an upstream identity provider's revocation state.

Loss of browser admission closes the stream without canceling already-admitted
Temporal work. That work retains its recorded inputs and independently scoped
machine authority. Demonstration routers live under tests and are not mounted as
a separate operator product.

## Scoped machine and provider authority

A workflow fan-out marker only selects its execution-capability verifier. It
cannot authorize artifact recovery or mint operator permission. Parent/child,
workflow/session, tool, job-owner, expiry, and action restrictions remain at the
owning endpoint. A bare service principal is not a generic artifact-read grant.

An admitted operator can inherit runtime selections from any existing instance
workflow by naming its parent, without task identity headers. A machine needs
the matching verified parent and both child-create and runtime-inheritance
scopes. Recorded person ownership and retired administrator flags grant neither
permission; missing parents remain unavailable. Headerless inheritance records
the selected parent as lineage without granting workflow-scoped machine authority.

Historical execution and job owner values retain their provenance and idempotency
namespaces. The guarded converter grants operator access per eligible retained
job; service-owned jobs and machine session restrictions remain separate.
Operator reads, exact replay, and cancel preserve the recorded owner, including
its evidence namespace. Ambiguous replay keys fail closed. Terminal replay returns
the saved result without starting work again. New person-attribution stays null.
Confirmed cancel signals retain their idempotency key and state across requests
and restarts.
Restricted raw artifacts, quarantine, redaction, linked-execution checks, and
protected conversion recovery keep their own gates.

Provider credentials resolve through provider profiles and managed-secret
references. They are never obtained by looking up an application user. Secret
values are encrypted and must not appear in responses, logs, workflow payloads,
or diagnostic artifacts. `JWT_SECRET_KEY` continues to sign scoped machine
capabilities. Omitted material is generated atomically in the existing
API/worker secrets volume; concurrent starters and restarts reuse the same key.
Explicit keys must contain at least 32 bytes of non-placeholder material.

## Retained data and upgrades

The existing guarded converter and `single_user_conversion_runs` ledger own
eligibility, subsystem transforms, and publication. Multiple people, unresolved
attribution, ambiguous data, stale evidence, and concurrent conversion refuse
before publication. No preferred-account heuristic merges identities.

Schema migration detaches person foreign keys from retained resource attribution
without changing IDs or values. After credential, artifact, preset, and settings
transforms and retained-job grants succeed, the same transaction renames account-era tables
to `retired_*`. These inert archives preserve source records and encrypted bytes;
they are outside live model metadata and have no account routes or resolver.
Source descriptions used by conversion are isolated from live models, keeping
historical Alembic revisions and retained-data inspection usable.

An interrupted transform or archive rolls back with its publication record.
Retry reconciles the existing ledger; a certified restart avoids repeating
completed transformations. Conflicting live/archive source tables fail closed.
The converter may inspect these archives when new retained data needs
attribution, so cleanup must preserve that evidence. Downgrade after
retirement is refused because restoring person constraints would invalidate
supported account-free writes; repair forward while preserving archives.

Isolated PostgreSQL conversion and mounted-request tests cover repository
behavior. An installation's actual ingress and operator access path require
that installation's evidence. Deployment and independent host repair remain
owned by the [deployment controller](../Steps/DockerComposeUpdateSystem.md).
