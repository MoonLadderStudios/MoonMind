"""Versioned, immutable, artifact-backed compiled Omnigent execution intent.

Source issue: MoonLadderStudios/MoonMind#3706
([Omnigent control plane 5/11] Compile typed immutable execution intent and
lifecycle authority before admission).

Authority-critical execution data has historically travelled through generic
nested ``parameters``, ``workspaceSpec``, ``annotations``, retry, timeout, and
approval dictionaries. A valid Pydantic *outer* request is not enough when the
essential semantics stay optional entries inside those generic maps — that is
how #3684 lost ``annotations.remediationLoop`` before Temporal could initialize
the controller, how repository readiness raced submission, and how mutable image
and runtime configuration drifted from immutable policy authority.

This module defines the one strict, versioned contract those authoring paths
compile to *before* ``MoonMind.AgentRun`` or ``MoonMind.OmnigentSession`` begins
any provider, host, lease, or workspace side effect:
:class:`CompiledOmnigentExecutionIntent` (schema
``moonmind.omnigent.compiled-execution-intent/v1``).

The record is:

* **Strict** — every model uses ``extra="forbid"`` so an unmodelled authority
  field cannot silently ride along inside a generic map.
* **Immutable** — every model is ``frozen`` and every digest-governing list is a
  tuple, so a consumer cannot mutate admitted authority.
* **Versioned** — the top-level schema string and every independently evolving
  nested contract carry their own version, and unknown versions follow the
  explicit :func:`classify_execution_intent_schema` policy.
* **Digest-stable** — ``intentDigest`` is computed deterministically over the
  governing authority (independent of ``createdAt``, the digest itself, and the
  derivation ``provenance``), so semantically equal normalized intent always
  produces the same digest and a retry reproduces the same record.

It has no infrastructure imports beyond the standard library, pydantic, and the
typed :class:`~moonmind.schemas.workspace_locator_models.WorkspaceLocator`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from moonmind.schemas.workspace_locator_models import WorkspaceLocator

# ---------------------------------------------------------------------------
# Schema identity and compatibility policy
# ---------------------------------------------------------------------------

#: The canonical schema family for the compiled execution intent.
EXECUTION_INTENT_SCHEMA_FAMILY = "moonmind.omnigent.compiled-execution-intent"
#: The single supported major version and full schema string for a *newly*
#: admitted feature generation.
EXECUTION_INTENT_SCHEMA_VERSION = "v1"
EXECUTION_INTENT_SCHEMA = (
    f"{EXECUTION_INTENT_SCHEMA_FAMILY}/{EXECUTION_INTENT_SCHEMA_VERSION}"
)
#: Producer identity stamped into every compiled record.
EXECUTION_INTENT_PRODUCER_VERSION = "omnigent-execution-intent@1"

#: Full schema strings a runtime may *parse and admit* as first-class authority.
KNOWN_EXECUTION_INTENT_SCHEMAS: frozenset[str] = frozenset({EXECUTION_INTENT_SCHEMA})

#: Hard bound on the serialized authority payload. Authority is compact refs and
#: digests, never large bodies; a document larger than this is rejected so an
#: oversized generic map cannot smuggle unbounded content into admitted
#: authority. Large prompts/logs/bundles live as artifacts referenced by ref.
EXECUTION_INTENT_MAX_PAYLOAD_BYTES = 128 * 1024

# Stable fail-closed error codes.
EXECUTION_INTENT_UNSAFE_INPUT = "EXECUTION_INTENT_UNSAFE_INPUT"
EXECUTION_INTENT_DIGEST_MISMATCH = "EXECUTION_INTENT_DIGEST_MISMATCH"
EXECUTION_INTENT_PAYLOAD_TOO_LARGE = "EXECUTION_INTENT_PAYLOAD_TOO_LARGE"
EXECUTION_INTENT_UNKNOWN_VERSION = "EXECUTION_INTENT_UNKNOWN_VERSION"
EXECUTION_INTENT_INCOMPLETE_AUTHORITY = "EXECUTION_INTENT_INCOMPLETE_AUTHORITY"
EXECUTION_INTENT_CONTRADICTORY_AUTHORITY = "EXECUTION_INTENT_CONTRADICTORY_AUTHORITY"

# Substrings that indicate a raw credential body has leaked into a value.
_CREDENTIAL_VALUE_MARKERS: tuple[str, ...] = (
    "bearer ",
    "token=",
    "password=",
    "secret=",
    "apikey=",
    "api_key=",
    "authorization:",
    "-----begin",
)
# Runtime/host shortcut values that must never substitute for typed authority.
_UNSAFE_VALUE_MARKERS: tuple[str, ...] = (
    "docker.sock",
    "/var/run/docker",
    "unix://",
    "tcp://docker",
)
# Key fragments that indicate a secret was smuggled in by name. Compared after
# normalizing separators/casing away.
_SECRET_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "secret",
    "apikey",
    "accesstoken",
    "refreshtoken",
    "privatekey",
    "clientsecret",
    "authorizationheader",
    "credentialbody",
)
# Nested typed sections whose generic-looking keys grant no authority and must
# not be scanned as if they were free-form authority maps.
_SECRET_SCAN_SKIP_KEYS: frozenset[str] = frozenset({"workspacelocator", "provenance"})


class ExecutionIntentSchemaPolicy(str, Enum):
    """How a runtime must treat a given compiled-execution-intent schema string.

    * ``PARSE`` — the exact supported schema; parse and admit as first-class
      authority.
    * ``HISTORICAL_READ_ONLY`` — a recognized *older* version in the same family;
      an already-admitted history may still be read for evidence/replay, but a
      newly admitted feature generation must not be created at this version.
    * ``FAIL`` — an unknown family, a malformed version, or a *newer* version the
      runtime does not understand; fail closed rather than guess authority.
    """

    PARSE = "parse"
    HISTORICAL_READ_ONLY = "historical_read_only"
    FAIL = "fail"


def _parse_major(version: str) -> int | None:
    candidate = version.strip().lower()
    if not candidate.startswith("v"):
        return None
    try:
        return int(candidate[1:])
    except ValueError:
        return None


def classify_execution_intent_schema(schema: str) -> ExecutionIntentSchemaPolicy:
    """Return the explicit compatibility policy for a schema string.

    The current major version parses; a recognized older major in the same
    family is historical-read-only; any newer major, unknown family, or
    malformed version fails closed. This is the single tested authority for how
    unknown versions are treated (never a silent default).
    """

    normalized = str(schema or "").strip()
    if normalized in KNOWN_EXECUTION_INTENT_SCHEMAS:
        return ExecutionIntentSchemaPolicy.PARSE
    family, _, version = normalized.rpartition("/")
    if family != EXECUTION_INTENT_SCHEMA_FAMILY:
        return ExecutionIntentSchemaPolicy.FAIL
    candidate_major = _parse_major(version)
    current_major = _parse_major(EXECUTION_INTENT_SCHEMA_VERSION)
    if candidate_major is None or current_major is None:
        return ExecutionIntentSchemaPolicy.FAIL
    if candidate_major < current_major:
        return ExecutionIntentSchemaPolicy.HISTORICAL_READ_ONLY
    return ExecutionIntentSchemaPolicy.FAIL


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------


class ExecutionLineageKind(str, Enum):
    """Why this execution was authored."""

    CREATE = "create"
    RERUN = "rerun"
    EDIT = "edit"
    REMEDIATION = "remediation"
    CHECKPOINT = "checkpoint"
    CONTINUATION = "continuation"


class RepositoryOperationClass(str, Enum):
    """Repository authority class the run is admitted for."""

    READ_ONLY = "read_only"
    CONTROLLED_MUTATION = "controlled_mutation"
    PUBLICATION = "publication"
    PULL_REQUEST = "pull_request"


class SessionMode(str, Enum):
    """Fresh session versus authorized reuse of an existing provider session."""

    FRESH = "fresh"
    AUTHORIZED_REUSE = "authorized_reuse"


class ReattachPolicy(str, Enum):
    """Live reattach versus cold restore on continuation."""

    LIVE_REATTACH = "live_reattach"
    COLD_RESTORE = "cold_restore"


class AuthorityProvenance(str, Enum):
    """Where a compiled value's authority came from.

    ``DURABLE`` — resolved from durable existing authority (a persisted record,
    a resolved policy/profile/image digest, a readiness resolution).
    ``LEGACY_DERIVED`` — derived by the migration adapter from an existing
    request shape; a bounded compatibility derivation, not proven full authority.
    """

    DURABLE = "durable"
    LEGACY_DERIVED = "legacy_derived"


# ---------------------------------------------------------------------------
# Base model
# ---------------------------------------------------------------------------


class _IntentModel(BaseModel):
    """Base for every compiled-execution-intent contract object.

    ``extra="forbid"`` rejects unmodelled authority fields, ``frozen`` keeps
    admitted authority immutable, the camelCase alias generator matches the
    repository wire convention, and ``populate_by_name`` still allows snake_case
    construction.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


# ---------------------------------------------------------------------------
# Nested, independently versioned sub-contracts
# ---------------------------------------------------------------------------


class ExecutionIdentity(_IntentModel):
    """Product and execution identity plus immutable input evidence."""

    schema_version: Literal["v1"] = "v1"
    workflow_id: str = Field(..., min_length=1)
    run_id: str | None = None
    logical_step_id: str | None = None
    step_execution_id: str = Field(..., min_length=1)
    agent_run_id: str = Field(..., min_length=1)
    #: Deterministic seed for the canonical session identity.
    canonical_session_seed: str = Field(..., min_length=1)
    lineage_kind: ExecutionLineageKind = ExecutionLineageKind.CREATE
    #: The create/rerun/edit/remediation/checkpoint/continuation source.
    source_execution_ref: str | None = None
    #: Original task-input snapshot ref and digest (never re-authored).
    task_input_snapshot_ref: str = Field(..., min_length=1)
    task_input_snapshot_digest: str = Field(..., min_length=1)
    instruction_ref: str = Field(..., min_length=1)
    instruction_digest: str = Field(..., min_length=1)


class RuntimeProviderSelection(_IntentModel):
    """Runtime and provider selection authority.

    ``agent_kind`` and ``agent_id`` are pinned: this contract is the omnigent
    external runtime only. ``provider`` here is declarative selection authority,
    never a live credential.
    """

    schema_version: Literal["v1"] = "v1"
    agent_kind: Literal["external"] = "external"
    agent_id: Literal["omnigent"] = "omnigent"
    execution_profile_ref: str = Field(..., min_length=1)
    execution_profile_version: str = Field(..., min_length=1)
    agent_profile_ref: str = Field(..., min_length=1)
    agent_profile_digest: str = Field(..., min_length=1)
    provider_profile_ref: str | None = None
    provider_profile_id: str = Field(..., min_length=1)
    #: The credential *generation* the run expects; a monotonic expectation, not
    #: a credential body.
    credential_generation: str = Field(..., min_length=1)
    provider_runtime: str = Field(..., min_length=1)
    provider_harness: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    effort: str | None = None
    #: Immutable compatibility profile identity resolved at admission.
    compatibility_profile: str = Field(..., min_length=1)


class LaunchDeploymentAuthority(_IntentModel):
    """Launch and deployment authority: policies, image digests, capabilities."""

    schema_version: Literal["v1"] = "v1"
    launch_policy_ref: str = Field(..., min_length=1)
    launch_policy_digest: str = Field(..., min_length=1)
    effective_launch_snapshot_ref: str = Field(..., min_length=1)
    effective_launch_snapshot_digest: str = Field(..., min_length=1)
    host_mode: str = Field(..., min_length=1)
    #: Exact image-digest expectations; a running deployment that drifts from
    #: these is rejected rather than silently accepted (image-drift incident).
    server_image_digest: str = Field(..., min_length=1)
    ui_image_digest: str = Field(..., min_length=1)
    host_image_digest: str = Field(..., min_length=1)
    network_policy_ref: str = Field(..., min_length=1)
    egress_policy_ref: str = Field(..., min_length=1)
    #: Runtime capability requirements (http, sse, websocket, mounted tools,
    #: repository capabilities). Normalized and deduplicated for a stable digest.
    runtime_capabilities: tuple[str, ...] = Field(default_factory=tuple)
    compatibility_manifest_ref: str = Field(..., min_length=1)
    build_manifest_ref: str = Field(..., min_length=1)

    @field_validator("runtime_capabilities", mode="before")
    @classmethod
    def _normalize_capabilities(cls, value: Any) -> tuple[str, ...]:
        return _normalized_tuple(value, lower=True)


class RepositoryWorkspaceAuthority(_IntentModel):
    """Repository and workspace authority (typed locator, resolved target)."""

    schema_version: Literal["v1"] = "v1"
    repository_provider: str = Field(..., min_length=1)
    #: Canonical repository source resolved at admission (never re-resolved).
    repository: str | None = Field(None, max_length=2000)
    connection_ref: str | None = None
    base_branch: str = Field(..., min_length=1, max_length=400)
    target_branch: str | None = Field(None, max_length=400)
    #: Resolved checkout commit; readiness is resolved before admission so
    #: planning and submission cannot disagree about repository authority.
    checkout_commit: str | None = Field(None, max_length=200)
    operation_class: RepositoryOperationClass = RepositoryOperationClass.READ_ONLY
    #: Canonical typed workspace identity — never a bind path or volume name.
    workspace_locator: WorkspaceLocator
    workspace_authority_class: str = Field(..., min_length=1)
    attachment_refs: tuple[str, ...] = Field(default_factory=tuple)
    checkpoint_ref: str | None = None
    checkpoint_digest: str | None = None
    restore_ref: str | None = None
    restore_digest: str | None = None
    publication_policy: str = Field("none", min_length=1, max_length=100)
    no_commit_policy: bool = False

    @field_validator("attachment_refs", mode="before")
    @classmethod
    def _normalize_attachments(cls, value: Any) -> tuple[str, ...]:
        return _normalized_tuple(value)

    @model_validator(mode="after")
    def _validate_operation_consistency(self) -> "RepositoryWorkspaceAuthority":
        if (self.checkpoint_ref is None) != (self.checkpoint_digest is None):
            raise ValueError(
                f"{EXECUTION_INTENT_INCOMPLETE_AUTHORITY}: checkpoint ref and "
                "digest must be supplied together"
            )
        if (self.restore_ref is None) != (self.restore_digest is None):
            raise ValueError(
                f"{EXECUTION_INTENT_INCOMPLETE_AUTHORITY}: restore ref and digest "
                "must be supplied together"
            )
        publishing = self.operation_class in {
            RepositoryOperationClass.PUBLICATION,
            RepositoryOperationClass.PULL_REQUEST,
        }
        if publishing and self.no_commit_policy:
            raise ValueError(
                f"{EXECUTION_INTENT_CONTRADICTORY_AUTHORITY}: a publishing "
                "operation class cannot also declare a no-commit policy"
            )
        if publishing and self.publication_policy in {"", "none"}:
            raise ValueError(
                f"{EXECUTION_INTENT_CONTRADICTORY_AUTHORITY}: a publishing "
                "operation class requires a publication policy"
            )
        return self


class SessionContinuationPolicy(_IntentModel):
    """Session, turn, and continuation policy plus terminal-evidence contract."""

    schema_version: Literal["v1"] = "v1"
    session_mode: SessionMode = SessionMode.FRESH
    initial_turn_attempt_id: str = Field(..., min_length=1)
    first_message_digest: str = Field(..., min_length=1)
    first_message_marker_policy: str = Field(..., min_length=1)
    allowed_continuation_kinds: tuple[str, ...] = Field(default_factory=tuple)
    provider_session_epoch: int = Field(0, ge=0)
    chat_binding_policy: str = Field(..., min_length=1)
    terminal_evidence_contract: str = Field(..., min_length=1)
    cleanup_policy: str = Field(..., min_length=1)
    historical_read_policy: str = Field(..., min_length=1)

    @field_validator("allowed_continuation_kinds", mode="before")
    @classmethod
    def _normalize_kinds(cls, value: Any) -> tuple[str, ...]:
        return _normalized_tuple(value, lower=True)

    @model_validator(mode="after")
    def _validate_reuse(self) -> "SessionContinuationPolicy":
        if (
            self.session_mode is SessionMode.AUTHORIZED_REUSE
            and not self.allowed_continuation_kinds
        ):
            raise ValueError(
                f"{EXECUTION_INTENT_CONTRADICTORY_AUTHORITY}: authorized session "
                "reuse requires at least one allowed continuation kind"
            )
        return self


class RemediationCheckpointPolicy(_IntentModel):
    """Typed remediation-loop controller intent and checkpoint policy.

    This is the typed replacement for the free-form ``annotations.remediationLoop``
    that #3684 silently stripped: lifecycle ownership lives here, in admitted
    authority, and cannot be dropped by a dashboard transform.
    """

    schema_version: Literal["v1"] = "v1"
    remediation_loop_enabled: bool = False
    verifier_owner: str | None = None
    remediator_owner: str | None = None
    max_attempts: int = Field(1, gt=0)
    max_branches: int = Field(1, gt=0)
    gate_result_ref: str | None = None
    remaining_work_ref: str | None = None
    checkpoint_branch_behavior: str = Field(..., min_length=1)
    reattach_policy: ReattachPolicy = ReattachPolicy.COLD_RESTORE
    #: Dimensions whose change requires a new branch rather than in-place reuse.
    immutable_dimensions: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("immutable_dimensions", mode="before")
    @classmethod
    def _normalize_dimensions(cls, value: Any) -> tuple[str, ...]:
        return _normalized_tuple(value)

    @model_validator(mode="after")
    def _validate_ownership(self) -> "RemediationCheckpointPolicy":
        if self.remediation_loop_enabled and not (
            self.verifier_owner and self.remediator_owner
        ):
            raise ValueError(
                f"{EXECUTION_INTENT_INCOMPLETE_AUTHORITY}: an enabled remediation "
                "loop requires both a verifier and remediator owner"
            )
        return self


class TimingFailurePolicy(_IntentModel):
    """Timing and failure policy: deadlines, cadence, retries, cleanup order."""

    schema_version: Literal["v1"] = "v1"
    execution_deadline_seconds: int = Field(..., gt=0)
    no_progress_timeout_seconds: int = Field(..., gt=0)
    observation_cadence_seconds: int = Field(..., gt=0)
    reconcile_cadence_seconds: int = Field(..., gt=0)
    retry_classes: tuple[str, ...] = Field(default_factory=tuple)
    max_attempts: int = Field(1, gt=0)
    cancellation_policy: str = Field(..., min_length=1)
    required_evidence: tuple[str, ...] = Field(default_factory=tuple)
    fail_closed: bool = True
    #: Ordered cleanup / lease-release steps, e.g. ("cleanup", "lease_release").
    cleanup_lease_release_order: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("retry_classes", "required_evidence", mode="before")
    @classmethod
    def _normalize_string_tuples(cls, value: Any) -> tuple[str, ...]:
        return _normalized_tuple(value, lower=True)

    @field_validator("cleanup_lease_release_order", mode="before")
    @classmethod
    def _normalize_order(cls, value: Any) -> tuple[str, ...]:
        # Order is significant: dedupe while preserving first-seen order.
        return _normalized_tuple(value, lower=True)


class ExecutionIntentProvenance(_IntentModel):
    """Records which compiled sections came from durable vs legacy authority.

    This is *metadata about derivation*, not authority itself, so it is excluded
    from the digest: two intents with identical authority are the same intent
    whether one was resolved durably and the other derived by the legacy adapter.
    ``claims_full_authority`` is false whenever any required value could only be
    legacy-derived, so a consumer can refuse to treat an unproven intent as full
    v1 authority.
    """

    schema_version: Literal["v1"] = "v1"
    identity: AuthorityProvenance = AuthorityProvenance.DURABLE
    runtime: AuthorityProvenance = AuthorityProvenance.DURABLE
    launch: AuthorityProvenance = AuthorityProvenance.DURABLE
    repository: AuthorityProvenance = AuthorityProvenance.DURABLE
    session: AuthorityProvenance = AuthorityProvenance.DURABLE
    remediation: AuthorityProvenance = AuthorityProvenance.DURABLE
    timing: AuthorityProvenance = AuthorityProvenance.DURABLE
    claims_full_authority: bool = True


# ---------------------------------------------------------------------------
# Top-level compiled execution intent
# ---------------------------------------------------------------------------


class CompiledOmnigentExecutionIntent(_IntentModel):
    """The one immutable, artifact-backed execution authority for a run.

    Compiled and validated at the API admission boundary *before* any provider,
    host, lease, or workspace side effect. ``MoonMind.AgentRun``,
    ``MoonMind.OmnigentSession``, activities, reconciliation, chat capability
    resolution, remediation, and cleanup all consume this same document by
    ``intentDigest``; runtime code may derive a compact view (see
    :meth:`compact_runtime_view`) but must never silently re-resolve or broaden
    the admitted authority.
    """

    schema_id: Literal["moonmind.omnigent.compiled-execution-intent/v1"] = Field(
        EXECUTION_INTENT_SCHEMA, alias="schema"
    )
    producer_version: str = Field(EXECUTION_INTENT_PRODUCER_VERSION, min_length=1)
    intent_digest: str | None = None
    created_at: datetime

    identity: ExecutionIdentity
    runtime: RuntimeProviderSelection
    launch: LaunchDeploymentAuthority
    repository: RepositoryWorkspaceAuthority
    session: SessionContinuationPolicy
    remediation: RemediationCheckpointPolicy
    timing: TimingFailurePolicy
    provenance: ExecutionIntentProvenance = Field(
        default_factory=ExecutionIntentProvenance
    )

    @model_validator(mode="after")
    def _finalize(self) -> "CompiledOmnigentExecutionIntent":
        if self.session.first_message_digest != self.identity.instruction_digest:
            raise ValueError(
                f"{EXECUTION_INTENT_CONTRADICTORY_AUTHORITY}: first-message and "
                "instruction digests must match"
            )
        dumped = self.model_dump(by_alias=True, mode="json", exclude_none=True)

        # 1. Payload bounds — admitted authority is compact refs and digests.
        encoded_len = len(
            json.dumps(dumped, separators=(",", ":"), allow_nan=False).encode("utf-8")
        )
        if encoded_len > EXECUTION_INTENT_MAX_PAYLOAD_BYTES:
            raise ValueError(
                f"{EXECUTION_INTENT_PAYLOAD_TOO_LARGE}: compiled intent is "
                f"{encoded_len} bytes, over the "
                f"{EXECUTION_INTENT_MAX_PAYLOAD_BYTES}-byte bound; large content "
                "must be an artifact ref, not inline authority"
            )

        # 2. Reject smuggled secrets — by value and by key.
        _assert_no_secret_keys(dumped)
        for leaf in _string_leaves(dumped):
            lowered = leaf.lower()
            if any(marker in lowered for marker in _CREDENTIAL_VALUE_MARKERS):
                raise ValueError(
                    f"{EXECUTION_INTENT_UNSAFE_INPUT}: compiled intent must not "
                    "carry a raw credential body"
                )
            if any(marker in lowered for marker in _UNSAFE_VALUE_MARKERS):
                raise ValueError(
                    f"{EXECUTION_INTENT_UNSAFE_INPUT}: compiled intent must not "
                    "carry host-daemon authority or socket transports"
                )

        # 3. Deterministic digest over governing authority.
        computed = self.compute_digest()
        if self.intent_digest is None:
            object.__setattr__(self, "intent_digest", computed)
        elif self.intent_digest != computed:
            raise ValueError(
                f"{EXECUTION_INTENT_DIGEST_MISMATCH}: intentDigest does not match "
                "the governing execution-intent authority"
            )
        return self

    def _fingerprint_payload(self) -> dict[str, Any]:
        """Identity-independent authority used to derive the digest.

        ``createdAt``, ``intentDigest``, and ``provenance`` are excluded so
        semantically equal normalized intent — including a retry — always
        produces the same digest regardless of when it was built or whether a
        value was resolved durably or legacy-derived.
        """

        dumped = self.model_dump(by_alias=True, mode="json", exclude_none=False)
        dumped.pop("intentDigest", None)
        dumped.pop("createdAt", None)
        dumped.pop("provenance", None)
        return dumped

    def compute_digest(self) -> str:
        """Return the deterministic ``sha256:`` digest of governing authority."""

        encoded = json.dumps(
            self._fingerprint_payload(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def compact_runtime_view(self) -> dict[str, Any]:
        """Compact view runtime code may derive without re-resolving authority.

        Carries only the ref+digest correlation and the identities a consumer
        needs to bind to the same admitted authority. A consumer that needs a
        specific field reads the full document by ``intentDigest``; it never
        reconstructs an overlapping decision from the environment.
        """

        return {
            "schema": self.schema_id,
            "intentDigest": self.intent_digest,
            "workflowId": self.identity.workflow_id,
            "stepExecutionId": self.identity.step_execution_id,
            "agentRunId": self.identity.agent_run_id,
            "canonicalSessionSeed": self.identity.canonical_session_seed,
            "taskInputSnapshotRef": self.identity.task_input_snapshot_ref,
            "taskInputSnapshotDigest": self.identity.task_input_snapshot_digest,
            "agentKind": self.runtime.agent_kind,
            "agentId": self.runtime.agent_id,
            "executionProfileRef": self.runtime.execution_profile_ref,
            "executionProfileVersion": self.runtime.execution_profile_version,
            "agentProfileRef": self.runtime.agent_profile_ref,
            "agentProfileDigest": self.runtime.agent_profile_digest,
            "providerProfileId": self.runtime.provider_profile_id,
            "credentialGeneration": self.runtime.credential_generation,
            "model": self.runtime.model,
            "effort": self.runtime.effort,
            "launchPolicyRef": self.launch.launch_policy_ref,
            "launchPolicyDigest": self.launch.launch_policy_digest,
            "effectiveLaunchSnapshotRef": self.launch.effective_launch_snapshot_ref,
            "effectiveLaunchSnapshotDigest": (
                self.launch.effective_launch_snapshot_digest
            ),
            "hostImageDigest": self.launch.host_image_digest,
            "serverImageDigest": self.launch.server_image_digest,
            "operationClass": self.repository.operation_class.value,
            "baseBranch": self.repository.base_branch,
            "targetBranch": self.repository.target_branch,
            "checkoutCommit": self.repository.checkout_commit,
            "remediationLoopEnabled": self.remediation.remediation_loop_enabled,
            "sessionMode": self.session.session_mode.value,
            "claimsFullAuthority": self.provenance.claims_full_authority,
        }

    def reconciler_view(self) -> Any:
        """Derive the pure lifecycle reconciler's compact input from this authority."""

        from moonmind.omnigent.reconciler.contracts import CompiledSessionIntent

        prompt_digest = (
            self.session.first_message_digest or self.identity.instruction_digest
        )
        if not prompt_digest:
            raise ValueError(
                f"{EXECUTION_INTENT_INCOMPLETE_AUTHORITY}: reconciler intent "
                "requires a first-message digest"
            )
        return CompiledSessionIntent(
            sessionId=self.identity.canonical_session_seed,
            provider=self.runtime.provider_runtime,
            requiresProfileLease=True,
            requiresHost=True,
            requiresCleanup=(
                self.session.cleanup_policy.strip().lower()
                not in {"none", "disabled"}
            ),
            maxTurnAttempts=self.timing.max_attempts,
            reconcileIntervalSeconds=self.timing.reconcile_cadence_seconds,
            turnPromptDigest=prompt_digest,
        )

    def evidence(self) -> dict[str, Any]:
        """Bounded, credential-free, path-safe compilation evidence.

        Suitable for a durable lifecycle event and Workflow Detail. Exposes only
        safe refs and digests, never raw secrets or host paths.
        """

        repository_evidence: str | None
        if self.repository.repository_provider == "local":
            repository_evidence = "[local-source]"
        else:
            repository_evidence = self.repository.repository
        return {
            "schema": self.schema_id,
            "producerVersion": self.producer_version,
            "intentDigest": self.intent_digest,
            "workflowId": self.identity.workflow_id,
            "stepExecutionId": self.identity.step_execution_id,
            "agentRunId": self.identity.agent_run_id,
            "lineageKind": self.identity.lineage_kind.value,
            "taskInputSnapshotDigest": self.identity.task_input_snapshot_digest,
            "instructionDigest": self.identity.instruction_digest,
            "executionProfileRef": self.runtime.execution_profile_ref,
            "executionProfileVersion": self.runtime.execution_profile_version,
            "agentProfileDigest": self.runtime.agent_profile_digest,
            "providerProfileId": self.runtime.provider_profile_id,
            "credentialGeneration": self.runtime.credential_generation,
            "model": self.runtime.model,
            "effort": self.runtime.effort,
            "compatibilityProfile": self.runtime.compatibility_profile,
            "launchPolicyDigest": self.launch.launch_policy_digest,
            "effectiveLaunchSnapshotDigest": (
                self.launch.effective_launch_snapshot_digest
            ),
            "hostImageDigest": self.launch.host_image_digest,
            "serverImageDigest": self.launch.server_image_digest,
            "uiImageDigest": self.launch.ui_image_digest,
            "runtimeCapabilities": list(self.launch.runtime_capabilities),
            "repositoryProvider": self.repository.repository_provider,
            "repository": repository_evidence,
            "operationClass": self.repository.operation_class.value,
            "baseBranch": self.repository.base_branch,
            "targetBranch": self.repository.target_branch,
            "checkoutCommit": self.repository.checkout_commit,
            "workspaceLocatorKind": self.repository.workspace_locator.kind,
            "publicationPolicy": self.repository.publication_policy,
            "noCommitPolicy": self.repository.no_commit_policy,
            "sessionMode": self.session.session_mode.value,
            "allowedContinuationKinds": list(
                self.session.allowed_continuation_kinds
            ),
            "remediationLoopEnabled": self.remediation.remediation_loop_enabled,
            "reattachPolicy": self.remediation.reattach_policy.value,
            "immutableDimensions": list(self.remediation.immutable_dimensions),
            "executionDeadlineSeconds": self.timing.execution_deadline_seconds,
            "cleanupLeaseReleaseOrder": list(
                self.timing.cleanup_lease_release_order
            ),
            "claimsFullAuthority": self.provenance.claims_full_authority,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalized_key(key: Any) -> str:
    return "".join(ch for ch in str(key).strip().lower() if ch.isalnum())


def _normalized_tuple(value: Any, *, lower: bool = False) -> tuple[str, ...]:
    """Dedupe (first-seen order) non-empty string values into a tuple."""

    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    seen: dict[str, None] = {}
    for item in value:
        candidate = str(item).strip()
        if lower:
            candidate = candidate.lower()
        if candidate:
            seen.setdefault(candidate, None)
    return tuple(seen)


def _string_leaves(value: Any) -> list[str]:
    leaves: list[str] = []
    if isinstance(value, dict):
        for nested in value.values():
            leaves.extend(_string_leaves(nested))
    elif isinstance(value, (list, tuple)):
        for item in value:
            leaves.extend(_string_leaves(item))
    elif isinstance(value, str):
        leaves.append(value)
    return leaves


def _assert_no_secret_keys(payload: Any) -> None:
    """Fail closed if a key names a secret (a smuggled credential by name)."""

    if isinstance(payload, dict):
        for key, nested in payload.items():
            normalized = _normalized_key(key)
            if any(fragment in normalized for fragment in _SECRET_KEY_FRAGMENTS):
                raise ValueError(
                    f"{EXECUTION_INTENT_UNSAFE_INPUT}: compiled intent must not "
                    f"carry a secret-shaped key {key!r}"
                )
            if normalized not in _SECRET_SCAN_SKIP_KEYS:
                _assert_no_secret_keys(nested)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _assert_no_secret_keys(item)


__all__ = [
    "EXECUTION_INTENT_SCHEMA",
    "EXECUTION_INTENT_SCHEMA_FAMILY",
    "EXECUTION_INTENT_SCHEMA_VERSION",
    "EXECUTION_INTENT_PRODUCER_VERSION",
    "EXECUTION_INTENT_MAX_PAYLOAD_BYTES",
    "KNOWN_EXECUTION_INTENT_SCHEMAS",
    "EXECUTION_INTENT_UNSAFE_INPUT",
    "EXECUTION_INTENT_DIGEST_MISMATCH",
    "EXECUTION_INTENT_PAYLOAD_TOO_LARGE",
    "EXECUTION_INTENT_UNKNOWN_VERSION",
    "EXECUTION_INTENT_INCOMPLETE_AUTHORITY",
    "EXECUTION_INTENT_CONTRADICTORY_AUTHORITY",
    "ExecutionIntentSchemaPolicy",
    "classify_execution_intent_schema",
    "ExecutionLineageKind",
    "RepositoryOperationClass",
    "SessionMode",
    "ReattachPolicy",
    "AuthorityProvenance",
    "ExecutionIdentity",
    "RuntimeProviderSelection",
    "LaunchDeploymentAuthority",
    "RepositoryWorkspaceAuthority",
    "SessionContinuationPolicy",
    "RemediationCheckpointPolicy",
    "TimingFailurePolicy",
    "ExecutionIntentProvenance",
    "CompiledOmnigentExecutionIntent",
]
