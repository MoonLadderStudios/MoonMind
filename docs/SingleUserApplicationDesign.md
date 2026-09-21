# Single-User Application Design

**Document Class:** Canonical declarative\
**Viewpoint:** System / Feature Design View\
**Status:** Accepted\
**Owners:** MoonMind Engineering\
**Audience:** Contributors, operators, API and dashboard developers, runtime authors\
**Authority:** Desired single-user application behavior under the application-model invariant in [MoonMind Architecture](MoonMindArchitecture.md#single-user-application-model). Exact machine interfaces remain owned by their subsystem contracts.\
**Related Docs:** [Authentication Contracts](Security/AuthenticationContracts.md), [Settings System](Security/SettingsSystem.md), [Provider Profiles](Security/ProviderProfiles.md), [Secrets System](Security/SecretsSystem.md)

> Accepted describes the intended architecture, not a claim that the account
> system has already been removed. This design does not itself change runtime
> behavior, authorize a production migration, or permit disabling an existing
> access boundary. Implementation sequencing and deployment evidence belong in
> issues, `docs/tmp/`, or run-local artifacts, not in this design.

## 1. Product model

MoonMind is a single-user application. One operator controls an instance, and
application configuration and resources belong to that instance rather than to
application users. Single-user is the only application architecture, not an
optional mode of a multi-user product.

The operator can use multiple browsers and devices, run concurrent workflows,
and operate multiple independent deployments. None of those capabilities
requires multiple MoonMind accounts. The operator is a product concept, not a
replacement database entity for `User`.

The application has no registration, invitations, membership, account switching,
password reset, email verification, account recovery, administrator promotion,
last-administrator protection, human roles, or tenant administration. It has no
application-login identity-provider selection or local/external identity linking.
It does not retain a hidden multi-user feature flag, a permanently seeded default
user, or a singleton principal that every service must resolve.
The application-login role of `AUTH_PROVIDER` disappears rather than gaining
another single-user selector value.

Deployment access, machine authentication, execution policy, and external provider
credentials remain separate responsibilities. Removing application users does
not remove those boundaries.

### Design rationale

A second application user changes the meaning of storage, queries, permissions,
configuration, caches, and UI state throughout the product. That complexity has
no purpose in this application model. The chosen design removes the dependency
rather than making the existing permission system always return an administrator.

Keeping the current disabled-auth path would retain user provisioning and
ownership plumbing. Restricting built-in accounts to one member would retain an
account lifecycle. Renaming `User` to `Operator` or `Tenant` would retain the same
model. None is the target design. Remote access is a deployment concern rather
than a reason to rebuild accounts inside MoonMind.

Each replacement migrates its real callers and removes the account machinery it
makes obsolete. Necessary transitional readers have identified consumers and a
removal condition. A separate final-cleanup issue is not a reason to leave two
live systems indefinitely. Useful changes can be developed and verified at their
own boundaries without waiting for every migration task. This does not permit an
incomplete or unprotected cutover to be labeled the completed single-user release.

## 2. Application and resource boundaries

Operator-facing APIs and services operate on instance resources without a human
owner parameter. Ordinary workflow, artifact, schedule, preset, and settings
operations do not load an account, test `is_superuser`, or filter by a current
user ID. A resource cannot become invisible to the operator because its creator
account was deleted, disabled, or replaced.

| Concern | Desired representation |
| --- | --- |
| Human ownership | No application-user ownership or owner-versus-admin branching |
| Operator preferences and configuration | Instance settings, with local browser preferences where appropriate |
| Workflows, schedules, artifacts, and presets | Instance resources identified by their own stable IDs |
| Machine authority | Credentials or capabilities restricted to permitted operations and resources |
| Execution ownership | Workflow, step, session, job, claim, and lease relationships |
| External credentials | Named provider profiles and managed-secret references |
| Deployment identity | An operational identifier only where coordination or diagnostics needs it |

A database dedicated to one instance does not need `instance_id` on every row as
an automatic replacement for `user_id`. The design introduces no generic
principal, organization, tenant, membership, or authorization framework.

Resource validation remains necessary. An operator request still obeys execution
state, source authority, publish intent, approval requirements, secret-handling
policy, and destructive-action boundaries. Instance-wide access is not blanket
permission to bypass these controls or expose raw secrets.

An execution owning a container, artifact, worktree, or GitHub issue claim is not
human ownership. Those associations remain authoritative for isolation,
continuation, cancellation, and cleanup. Names such as `MoonMind.UserWorkflow`
also do not, by themselves, imply an account dependency or justify a wire rename.

## 3. Operator access without application accounts

### Local and remote access

The default local path opens the dashboard without MoonMind account creation or
login. The operator interface is published on loopback by default. Normal local
operation requires no identity server, mandatory reverse-proxy container, or
application-session database.

Remote use is supported through an operator-approved restricted network or
authenticated ingress. Everyone admitted to the operator interface has the same
instance authority. There is no per-person isolation or viewer role inside
MoonMind, and sharing operator access is equivalent to sharing control of the
instance. A publicly reachable unauthenticated operator interface is unsupported.

An ingress may use its own login, multifactor authentication, or device policy.
MoonMind does not synchronize its users, import its roles, map its subjects to
local accounts, or store a local identity roster. A trusted-access configuration
records the chosen boundary. It does not create a firewall or establish trust
merely by being set.

The operator-facing API has one shared admission boundary, separate from
business logic. Admission establishes permission to use the operator interface,
not a persisted person. Apply it through the existing product routes and shared
dependencies. A parallel demonstration API, echo socket, synthetic artifact, or
in-memory work ledger does not replace the real workflow, artifact, or chat
consumer. Test scaffolding stays in tests rather than becoming another production
surface. This design requires no new access service, selector family, or
prescribed credential format.

### No bypass through the internal network

Every operator surface is protected by that boundary, including HTTP APIs,
artifact downloads, server-sent events, WebSocket handshakes and reconnects, and
chat controls. A reverse proxy alone is insufficient if an untrusted container
can bypass it and reach an unrestricted backend.

Network isolation and any backend admission proof must prevent such bypass.
Caller-controlled identity or forwarding headers, a shared Docker network,
loopback from inside a workload container, and possession of a worker token do
not establish operator access. An invalid machine credential never falls back to
operator access. A failed access dependency produces an actionable denial or
unavailable response, not an account stub or a less-protected route.

The local trust boundary assumes the host and its operator are trusted. It does
not extend that trust to arbitrary web pages, agent-generated content, or
workload containers.

### Browser and transport protections

Host and origin validation, restrictive cross-origin behavior, protection for
browser-initiated mutations, and safe WebSocket origin handling remain required
where applicable. CORS alone is not the admission or cross-site request forgery
boundary. Existing reusable browser-security helpers can survive without their
account lifecycle machinery.

Remote transport protects credentials and content. Long-lived streams use the
existing admitting boundary's bounded revalidation or closure policy. Rechecking
an unchanged cached header does not establish upstream revocation detection.
Where that authority cannot be queried during a stream, bound the connection
lifetime and require fresh admission on reconnect, with the limitation stated
honestly. Do not add a second session authority to hide the distinction. Broad
reusable credentials do not travel in URLs or application logs.

Losing browser access or closing a browser does not cancel already-admitted
workflows. Durable work uses its own recorded intent and scoped machine
authority. Revoking a machine capability has the behavior defined by its owning
contract, independent of a browser session.

## 4. Machine and runtime authority

Workers, MCP clients, container jobs, OAuth runners, callbacks, runtime bridges,
and Omnigent hosts retain their required authentication. Their authority is
restricted by operation, resource, execution binding, and lifetime as applicable.
Machine tokens are not unrestricted operator credentials.

An agent can use only the secrets, workspaces, artifacts, tools, and side effects
authorized for its execution. It does not inherit access to every instance
resource because the person who launched it owns the instance. A runtime bridge
remains bound to the intended runtime session, not an arbitrary upstream proxy.

Existing signing, revocation, and credential-materialization mechanisms remain
where they serve these machine contracts. Removing application login is not a
blanket deletion of `auth` modules, JWT libraries, signing keys, OAuth workflows,
or every field containing `owner` or `session`. Each protocol retains the
restrictions its real trust boundary requires, not every possible lease,
generation, introspection, or registry mechanism. Adapt existing issuers and
validators together rather than introducing a universal principal translator,
second token store, or parallel chat/runtime lifecycle.

The [Docker Backend Service](ManagedAgents/DockerBackendService.md),
[Provider Profiles](Security/ProviderProfiles.md), and
[Secrets System](Security/SecretsSystem.md) retain ownership of their exact
interfaces and security rules. The single-user design removes human identity
from their application-facing composition. It does not broaden agent authority.

## 5. Instance settings, presets, and preferences

### Settings

There is one instance settings experience. Human-user scopes, role checks, and
workspace membership are absent. A workspace that exists only as a tenancy
container is not part of the target model.

Repository, project, provider-profile, or runtime overrides remain only where
they express a real configuration need. Such contexts are not users or tenants.
Bootstrap configuration such as network binding and database connectivity stays
separate from settings intended for ordinary in-application editing.

Typed descriptors, validation, secret references, deterministic precedence,
effective-value explanations, restart requirements, and concurrent-write
protection remain useful. Their implementation does not require a permission
matrix or a human-owner lookup. Browser-local display preferences may stay
browser-local without introducing a profile service or mandatory synchronization.

### Presets and preferences

Presets form one instance-wide catalog. There is no personal-versus-global
permission distinction, sharing ACL, or administrator-only global write path.
Preset identity, version history, composition, and useful provenance remain.
Built-in or managed provenance may govern update behavior without becoming a
human role.

Favorites and recents are instance preferences where persisted by the server.
They do not require an account key. Catalog reconciliation preserves different
preset contents and versions instead of silently overwriting a personal/global
name collision.

## 6. Provider profiles and secrets

A single operator may have multiple provider subscriptions, OAuth enrollments,
API keys, GitHub credentials, registry credentials, and runtime profiles. These
are credentials with external systems, not MoonMind application users.

The existing provider-profile and managed-secret systems remain the canonical
homes for execution credentials and their selection. Credential resolution does
not load or create `UserProfile`, take a human user as an input, or cache secrets
by application-user identity.

Legacy profile-held secret values retain their confidentiality and meaning when
represented as managed secrets. Credential references, encryption or wrapping
requirements, and provider bindings remain valid. Nonsecret profile preferences
become instance settings or appropriate local preferences. The design introduces
no second credential store and does not silently select a different provider,
account, billing context, model, or effort level.

Source-account identities still matter to their external operations. For
example, a GitHub search constrained to the credential's authenticated account
remains meaningful without a MoonMind account or local identity mapping.

## 7. Dashboard and API behavior

The dashboard opens into the instance after deployment admission. It does not
show account onboarding, membership management, owner filters, role-dependent
navigation, or personal/global selectors. Settings describe the instance,
provider profiles, and supported execution controls.

All of the operator's devices see the same server-held resources. Cache keys and
query layers no longer partition by human identity. Reconnect handling, draft
preservation, duplicate-submission protection, and conflicting-edit detection
remain because a single operator can use multiple tabs and concurrent workflows.
Remove obsolete API scope inputs with their actual clients rather than hiding a
personal/global default in the shared client. Reuse existing cache, idempotency,
and transport owners; account removal does not require a new synchronization or
retry framework.

Application errors identify real problems with admission, data availability,
configuration, execution, or machine authority. Routine requests do not fail
because a default user is missing, a profile was not provisioned, or a resource's
creator account is inactive.

Audit and diagnostic records use request, deployment, workflow, step, session,
job, and machine identifiers as appropriate. A simple initiator description such
as operator, schedule, or worker may explain an action without becoming an
account model. Secret redaction and durable execution evidence remain unchanged.

## 8. Independent deployments and concurrency

One operator can run independent MoonMind deployments without a shared user
database, account service, identity synchronization, or direct inter-deployment
connection. Each deployment owns its configuration, data, and access boundary.
This design does not add a centralized control service.

Existing GitHub-based claim, lease, continuation, and publication coordination
remains valid. A claim's execution or deployment owner is distinct from a human
resource owner. Removing user scopes does not remove mutual exclusion,
idempotency, transaction safety, retries, or recovery between competing runs.

The instance may have multiple workers and API processes where the existing
architecture needs them. Single-user does not mean single-threaded, one browser,
one provider profile, one active workflow, or one physical machine. It also does
not require additional replication or distributed services.

Temporal, PostgreSQL, artifact storage, and Omnigent are not replacement targets
of this design. Their simplification is a separate architectural decision.

## 9. Persistence and upgrade invariants

The desired schema has no account, external-person-identity, enrollment,
application-login session, role, membership, or human-ownership structures needed
for normal operation. Obsolete foreign keys and user-scoped uniqueness rules do
not survive behind a permanent default-user mapping.

An eligible single-operator upgrade preserves its admitted workflows, schedules, artifacts,
preset versions, effective settings, preferences, and usable credential
references. Existing resource IDs and relationships remain stable where other
records or external references depend on them. Legacy creator information may
remain as provenance, but never as an operator-access predicate.

Legacy conversion is a versioned migration, not a permanent startup qualification
system. Use existing schema/migration state to select it when needed. Fresh and
successfully converted installations start normally without inventorying removed
account tables, requiring alias declarations again, or creating a default person.
Reuse the existing migration and deployment owners, not independent startup
converters or a new transformation registry requiring no-op plugins.

### Upgrade eligibility and disposition

In-place conversion is supported only when the complete retained data set is
attributable to one operator or to that operator's deployment. The chosen
disposition for a genuinely multi-person database is to **block conversion
without changing the source deployment**. The migration does not choose an
administrator as the new owner, combine people's data, or silently discard the
unselected rows. It does not implement automatic splitting, quarantine, or
export as an alternate migration mode.

| Source state | Conversion disposition |
| --- | --- |
| Fresh database with no retained application data | Initialize the single-user schema without creating an account |
| Retained data attributable to one operator, including proven aliases of that same person | Convert automatically within existing deployment authorization, preserving the resources and effective configuration described below |
| Retained data attributable to multiple people | Block before conversion or access cutover, leaving source data and its existing protections unchanged |
| Missing, conflicting, or incomplete ownership evidence | Block on the same boundary until the source attribution is resolved through authorized evidence or a separately authorized data disposition |

Attribution covers retained resources and dependencies, not just active login
counts or UUID-shaped values. Include disabled/deleted users' remaining data,
profile-held credentials, settings, presets, artifacts, schedules, serialized
ownership, and in-flight references. Unavailable required reads are unknown,
never an empty inventory or proof of eligibility. Existing trusted records may
establish deployment ownership or a same-person alias without new declarations.
An arbitrary UUID list, caller boolean, matching email/display name, admin flag,
or one remaining active account cannot manufacture that authority. Genuinely
unresolved human data remains protected. Multiple provider accounts are not
multiple people, and historical provenance is not automatically a live owner.

The authoritative eligibility read and conversion share the existing migration
serialization and appropriate transaction/locks or bounded writer quiescence.
Acquire that boundary before reading and retain it through coherent commit and
cutover, covering all writers that can change relevant data. Serialize by the
actual deployment/database being converted, not a digest that changes with each
proposal. An earlier report, owner-set hash, or row count does not prove that
values and references stayed unchanged. Prevent the race rather than adding a
larger fingerprint system. Continuous old/new fleet availability is not required.

The block precedes removal of access predicates, credential rebinding, destructive
schema changes, and replacement of the serving application. On refusal, preserve
the source data and access protection, release only owned temporary migration
resources, and resume safely paused work under its original authority. All
supported startup/update paths that could expose the new model use this owner.
Logging conversion failure and continuing ordinary account-free service is not
enforcement. Protected diagnostics and the independent host repair path may
remain available without exposing the unconverted data.

Use ordinary versioned migrations and a small explicit sequence of required
transforms with coherent reference updates. Prefer rollback of incomplete
transactional work. If existing durable progress is needed for an external effect,
reconcile it before retry and preserve the actual operation's authority. A stale
`in_progress` marker cannot permanently prohibit recovery, and an unknown result
cannot default to success. Do not steal an active writer or create another lease,
ledger, or general migration service to repair avoidable bookkeeping complexity.

Blocked conversion reports the reason and redacted evidence through the existing
migration or deployment result. It does not disclose another person's resource
contents or credentials. Resolution is a separate, authorized preparation of a
single-operator data set or correction of the attribution evidence, followed by
the same eligibility check. This design neither authorizes that preparation nor
requires a new export service, identity registry, or permanent multi-user mode.
The old release is a protected source pending a valid cutover, not a second
application architecture supported by the new release. Lack of production
authority does not prevent agents from implementing and testing these outcomes
against isolated fixtures.

### Data preservation within an eligible upgrade

Within a verified single-operator data set, preserve previously effective settings
and real project/runtime contexts through the existing resolver's precedence.
Different raw overrides or independent contexts are not automatically conflicting
effective choices. Preserve absent/null/reset semantics. Block only where genuinely
incompatible effective defaults require an explicit disposition, not because more
than one historical row exists. Do not choose the newest row or an administrator's
profile for convenience, or add a generic scope framework to retain unused modes.

Different same-person presets retain their IDs, contents, and versions. Resolve
actual name collisions deterministically from the original scope and stable ID,
with references preserved or updated transactionally. Unaffected names need no
migration alias or rename. Existing seed synchronization preserves custom edits
and does not reconstruct private/global duplicates. Credentials remain separate
managed-secret references with their original provider and billing bindings.
Identical-value deduplication is not permission to merge different credentials.
Favorites are deduplicated by resource identity and recents retain their latest
recorded use. These rules do not apply across people because such a source is
ineligible for conversion.

### History and deployment continuity

Retained Temporal histories and durable payloads remain executable or have a
controlled cutover under their existing compatibility rules. Historical user
fields may be read for compatibility without becoming present-day authorization.
Histories are not rewritten to assign every event to a new constant principal.
Names and fields unrelated to human accounts are not mechanically removed.

Historical migrations required for database upgrades remain. Runtime account
machinery does not. Any necessary transitional reader has a specific persisted
payload or replay purpose rather than a general legacy-auth mode.

Preserve schedule IDs, cadence, paused state, and explicit execution/publication
choices. Update stored actions only when their actual interface changes; use
compatible existing payload readers where sufficient. Account removal does not
require re-admitting every schedule or freezing deployment image bookkeeping.
Future managed launches follow the installed runtime under the existing deployment
owner, while historical attempts retain their actual artifacts and provider intent.

The [Docker Compose update system](Steps/DockerComposeUpdateSystem.md) owns
in-place recreation and recovery independent of healthy application orchestration.
This design does not require retained fleets, version-promotion canaries, or a
second migration coordinator. Real replay incompatibility still needs a controlled
transition before removing the execution capability that retained work needs.

Deployment changes preserve the operator's configured dashboard URL and a
working, protected access path. They do not silently replace LAN access with
localhost-only access or turn authenticated access into public access. Existing
multifactor or ingress requirements have a verified replacement before account
controls are retired. Old and new writers do not concurrently mutate an
incompatible schema.

Backup, restoration, and rollback preserve data and execution authority. Broad
volume deletion, resetting Temporal, silently weakening access, or discarding
credential encryption material is not an account-removal strategy. Reconcile and
retry committed conversion through the existing owner. Backward rollback is valid
only where schema/data/history compatibility and subsequent writes permit it;
otherwise use forward repair, not a shared database restore that discards newer
work or a bespoke rollback engine for every phase. Verification uses isolated
populated fixtures and applicable replay evidence. Production migration is a
separate authorized operation, not a prerequisite for proving the implementation.

## 10. Observable acceptance behavior

These are product outcomes, not implementation phases, a fixed test count, or a
new runtime gating framework. A representative default-path journey can cover
several outcomes; focused negative cases exercise the relevant owning boundary.
Reuse existing tests and add only missing integration coverage instead of a
cross-product of every client, credential, transport, and historical release mode.

| Scenario | Required outcome |
| --- | --- |
| Fresh local instance | Dashboard and ordinary operator actions work without account setup, login, user seeding, or an identity service |
| Eligible single-operator populated instance | Retained resources, effective settings, preset versions, and credential bindings survive without owner-based visibility, including proven same-person aliases and collision handling |
| Multiple people or unresolved attribution | Conversion is blocked before source mutation or access cutover, without exposing, combining, exporting, or deleting another person's data; the protected source release and admitted work remain recoverable under their existing authority |
| Source changes during conversion | The candidate is not exposed using a stale eligibility decision; inconsistent conversion has no partially published result |
| Approved remote access | The configured operator URL works through its protected boundary without a local user registry |
| Unapproved or bypass access | Direct-backend, forged-header, hostile-origin, and invalid-credential requests cannot acquire operator authority |
| Scoped machine execution | Correctly scoped operations work, while unrelated resources and operator-only actions remain denied |
| Concurrent use and restart | Multiple tabs, workers, schedules, and independent deployments retain concurrency and recovery behavior |
| Retained execution history | In-flight work and historical payloads remain compatible through the established replay or cutover path |
| Structural simplification | Ordinary service calls, queries, caches, and startup no longer depend on human accounts, roles, or a default principal |

A hidden singleton user, an always-allow authorization helper, or a smaller
account UI does not satisfy the last outcome. Conversely, surviving workflow
owners, provider identities, scoped machine credentials, and historical
migrations are not evidence that multi-user support remains.

Evidence must exercise the boundary claimed: sample payload decoding is not
Temporal event-history replay, metadata creation is not a populated PostgreSQL
upgrade, and a policy helper or demonstration endpoint is not browser-to-API
workflow execution. Running-container bypass and actual migration interruption
need tests at those boundaries, not only rendered YAML or fabricated receipts.
Use existing replay machinery only for replay-sensitive changes, not a new
history matrix for unchanged orchestration. Do not retain account fixtures
solely because an old test named them.

A short PR explanation can map outcomes to actual results and remaining gaps.
Do not package this table as production conformance state or add tests for row
counts, issue references, headings, or test filenames. Targeted development tests
and broader current-candidate GitHub Actions results follow AGENTS.md. Missing
sandbox capability or pending CI stays unverified without burning repeated
implementation attempts. No new evidence registry, approval step, or manual
rehearsal prerequisite is required. Every required outcome still needs relevant
evidence before claiming the single-user migration complete.

## 11. Documentation authority and scope

[MoonMind Architecture](MoonMindArchitecture.md#single-user-application-model)
owns the single-user-only application invariant. This design describes its
cross-component realization. Under the
[MoonSpec Document Model](Workflows/MoonSpecDocumentModel.md), execution plans and
backlogs are derived from that desired state and cannot require a contradictory
multi-user product.

The application-account target in
[Authentication Contracts](Security/AuthenticationContracts.md) and its
[Omnigent auth adapter](Security/OmnigentAuthAdapterContract.md) is superseded for
this design. Account enrollment, identity mapping, user ownership, and role
matrices are not prerequisites to complete before adopting the single-user
architecture. Existing implementation guidance remains applicable to releases
that still contain those mechanisms until a protected cutover is implemented.

The human-user and tenancy-scope portions of the
[Settings System](Security/SettingsSystem.md), personal/global preset permissions,
and profile-to-application-user relationships likewise do not define the target
application model. Their useful configuration, catalog, and credential semantics
remain owned by those subsystems within the single-user invariant.

The owning documents carry this classification themselves. The Omnigent
application-auth adapter is a superseded predecessor reference, and the Settings
System explicitly supersedes its human ownership, tenancy, and role requirements
while retaining its configuration and security responsibilities. Account-era
examples in those documents describe existing implementations only, not new
single-user interfaces. Consumers of those scope rules inherit the same
classification. UI navigation, secret materialization, workflow capabilities,
and other independent contracts are not redefined by an old scope example.

This scope does not supersede machine-token wire formats, runtime binding,
credential isolation, publication approval, or Temporal compatibility contracts.
Their owning documents change with their implementations when needed. A design
paragraph is not permission to bypass a currently enforced security boundary.

Each feature updates its own contracts alongside the implementation. Cross-document
reconciliation removes remaining contradictions and repairs links, not another
approval gate or full-tree rewrite for every child. Documentation-only changes
need review and lightweight link checks, not unit tests of wording or structure
or new broad-suite selection. Generated API types, configuration, and CLI behavior
are executable contracts and stay with their implementation tests.

When the design is implemented, its settled behavior belongs in the owning
architecture and subsystem documents under the documentation promotion rule.
This design is then superseded or removed rather than becoming a second
permanent architecture authority. Do not present accepted or partially implemented
behavior as shipped, or recreate an obsolete documentation test as a promotion
prerequisite.
