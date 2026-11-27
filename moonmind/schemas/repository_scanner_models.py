"""Admitted repository-scanner inputs, independent of runtime and workflow models."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.alias_generators import to_camel

from moonmind.security.native_network import NativeNetworkLimits

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
]
Items = Annotated[list[Text], Field(max_length=4096)]
Scanner = Literal["gitleaks", "opengrep", "trivy"]
MAX_REPORT_BYTES = 8 * 1024 * 1024
FeedComponent = Literal["vulnerability", "java", "checks", "kev", "epss"]
#: Freshness policy default; independent of scanner-image maintenance.
DEFAULT_FEED_MAX_AGE_HOURS = 48


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, alias_generator=to_camel
    )


def relative_path(value: str, *, root: bool = False) -> str:
    if root and value == ".":
        return value
    if not re.fullmatch(r"[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)*", value):
        raise ValueError("scanner paths must be normalized relative paths")
    if any(part in {".", "..", ".git"} for part in value.split("/")):
        raise ValueError("scanner paths must stay outside Git metadata")
    return value


class ScannerRule(Contract):
    id: Text
    path: str = Field(pattern=r"^/opt/moonmind-scanner-rules/[A-Za-z0-9_./-]+$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    license: Text
    source: Text | None = Field(None, exclude_if=lambda value: value is None)
    version: Text | None = Field(None, exclude_if=lambda value: value is None)
    notice: Text | None = Field(None, exclude_if=lambda value: value is None)
    languages: Items = Field(default_factory=list, exclude_if=lambda value: not value)
    frameworks: Items = Field(default_factory=list, exclude_if=lambda value: not value)

    @field_validator("path")
    @classmethod
    def trusted_path(cls, value):
        if any(p in {".", "..", ""} for p in value.split("/")[1:]):
            raise ValueError("rules must be admitted image files")
        return value


class RepositoryScan(Contract):
    scanner: Scanner
    input_path: str = "."
    output_directory: str
    rules: list[ScannerRule] | None = Field(None, max_length=32)
    exclusions: Items = Field(default_factory=list)
    allow_inline_suppressions: bool = False
    trivy_scanners: list[Literal["vuln", "misconfig", "secret"]] = Field(
        default_factory=lambda: ["vuln", "misconfig"], min_length=1, max_length=3
    )
    gitleaks_owns_secrets: bool = True
    database_cache_ref: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    database_digest: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    feed_max_age_hours: int = Field(DEFAULT_FEED_MAX_AGE_HOURS, ge=1, le=8760)
    require_fresh_feeds: bool = Field(False, exclude_if=lambda value: not value)
    max_files: int = Field(4096, ge=1, le=4096)
    max_input_bytes: int = Field(128 * 1024 * 1024, ge=1, le=2 * 1024**3)
    max_report_bytes: int = Field(MAX_REPORT_BYTES, ge=128, le=MAX_REPORT_BYTES)
    timeout_seconds: int = Field(300, ge=1, le=3600)

    @field_validator("input_path")
    @classmethod
    def input_relative(cls, value):
        return relative_path(value, root=True)

    @field_validator("output_directory")
    @classmethod
    def output_relative(cls, value):
        return relative_path(value)

    @field_validator("exclusions")
    @classmethod
    def excluded_paths(cls, values):
        return [relative_path(v) for v in values]

    @model_validator(mode="after")
    def admitted_choices(self):
        if self.scanner != "opengrep" and self.rules is None:
            self.rules = []
        if self.scanner == "opengrep" and self.rules == []:
            raise ValueError("Opengrep requires selected licensed image rules")
        if self.scanner == "gitleaks" and len(self.rules or []) > 1:
            raise ValueError("Gitleaks accepts one trusted configuration")
        if len({r.id for r in self.rules or []}) != len(self.rules or []):
            raise ValueError("duplicate selected rule identity")
        if len(set(self.trivy_scanners)) != len(self.trivy_scanners):
            raise ValueError("duplicate Trivy capability")
        if self.scanner == "trivy":
            if self.gitleaks_owns_secrets and "secret" in self.trivy_scanners:
                raise ValueError("Gitleaks already owns secret scanning")
            if "vuln" in self.trivy_scanners and not self.database_cache_ref:
                # Resolve only the deployment's standard cache alias here. The
                # worker still authorizes its mount; the runner selects and
                # verifies the immutable current snapshot before scanning.
                self.database_cache_ref = "scanner-db"
        if self.database_digest and not self.database_cache_ref:
            raise ValueError("a database identity requires its admitted cache")
        if self.input_path != "." and (
            self.input_path == self.output_directory
            or self.input_path.startswith(self.output_directory + "/")
        ):
            raise ValueError("output directory cannot contain the admitted input")
        return self

    @property
    def modes(self) -> list[str]:
        return ["history", "worktree"] if self.scanner == "gitleaks" else ["scan"]

    def outputs(self) -> list[dict]:
        return [
            {"name": name, "relativePath": f"{self.output_directory}/{name}.json"}
            for name in [*self.modes, "execution", "summary"]
        ]


class FeedRefresh(Contract):
    """Source-free acquisition of selected Trivy components into a snapshot.

    Upstream locations are deployment code, not request fields. ``download``
    reaches them through an admitted egress policy; ``import`` validates an
    operator-supplied archive of a Trivy cache directory with no network.
    """

    acquisition: Literal["download", "import"] = "download"
    components: list[FeedComponent] = Field(
        default_factory=lambda: ["vulnerability"], min_length=1, max_length=5
    )
    feed_cache_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    egress_policy_ref: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$")
    archive_artifact_ref: str | None = Field(None, pattern=r"^art_[0-9A-Za-z]{1,64}$")
    output_directory: str = "feed-refresh"
    max_age_hours: int = Field(DEFAULT_FEED_MAX_AGE_HOURS, ge=1, le=8760)
    max_bytes: int = Field(8 * 1024**3, ge=1, le=16 * 1024**3)
    timeout_seconds: int = Field(1800, ge=60, le=3600)

    @field_validator("output_directory")
    @classmethod
    def output_relative(cls, value):
        return relative_path(value)

    @model_validator(mode="after")
    def admitted_choices(self):
        if len(set(self.components)) != len(self.components):
            raise ValueError("duplicate feed component")
        if {"java", "checks"}.intersection(
            self.components
        ) and "vulnerability" not in self.components:
            raise ValueError("Trivy feed snapshots require the vulnerability database")
        download = self.acquisition == "download"
        if download != (self.egress_policy_ref is not None):
            raise ValueError("feed downloads, and only downloads, need egress")
        if download == (self.archive_artifact_ref is not None):
            raise ValueError("feed imports, and only imports, read an archive")
        return self


ImageOperation = Literal["assess", "sbom-generate", "sbom-reassess"]
ImageTarget = Literal["registry", "archive", "sbom", "source"]
#: Which inputs each operation reads. Source inventory is SBOM generation
#: only; source vulnerability scanning stays with ``RepositoryScan``.
IMAGE_TARGETS: dict[str, frozenset[str]] = {
    "assess": frozenset({"registry", "archive"}),
    "sbom-generate": frozenset({"registry", "archive", "source"}),
    "sbom-reassess": frozenset({"sbom"}),
}
MAX_SBOM_BYTES = 64 * 1024 * 1024


class ImageAssessment(Contract):
    """One Trivy operation on an image, image archive, SBOM or source snapshot.

    A typed workload of its own, so target-image coverage is requested and
    reported apart from source scanning. Registry targets are read from the
    registry and archives or SBOMs from captured inputs, never through a
    container daemon. ``targetCredentialRef`` authorizes the target registry
    only; the scanner image is a deployment image source with its own pull
    authority.
    """

    operation: ImageOperation
    target: ImageTarget
    image_reference: str | None = Field(None, min_length=1, max_length=512)
    platform: str | None = Field(
        None,
        pattern=r"^linux/(?:amd64|arm64|arm|386|ppc64le|s390x|riscv64)(?:/v[5-8])?$",
    )
    input_artifact_ref: str | None = Field(None, pattern=r"^art_[0-9A-Za-z]{1,64}$")
    egress_policy_ref: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$")
    target_credential_ref: str | None = Field(None, min_length=1, max_length=1024)
    database_cache_ref: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    database_digest: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    feed_max_age_hours: int = Field(DEFAULT_FEED_MAX_AGE_HOURS, ge=1, le=8760)
    require_fresh_feeds: bool = Field(False, exclude_if=lambda value: not value)
    output_directory: str = "image-assessment"
    max_input_bytes: int = Field(2 * 1024**3, ge=1, le=4 * 1024**3)
    max_sbom_bytes: int = Field(32 * 1024 * 1024, ge=1024, le=MAX_SBOM_BYTES)
    max_report_bytes: int = Field(MAX_REPORT_BYTES, ge=128, le=MAX_REPORT_BYTES)
    timeout_seconds: int = Field(900, ge=1, le=3600)

    @field_validator("output_directory")
    @classmethod
    def output_relative(cls, value):
        return relative_path(value)

    @field_validator("target_credential_ref")
    @classmethod
    def opaque_reference(cls, value):
        if value is not None and "://" not in value:
            raise ValueError("secret references must use an opaque reference URI")
        return value

    @model_validator(mode="after")
    def admitted_choices(self):
        if self.target not in IMAGE_TARGETS[self.operation]:
            raise ValueError("operation does not read this target")
        registry = self.target == "registry"
        if registry != (self.image_reference is not None):
            raise ValueError("registry targets, and only they, name an image")
        if registry != (self.egress_policy_ref is not None):
            raise ValueError("registry targets, and only they, need egress")
        if not registry and (self.platform or self.target_credential_ref):
            raise ValueError("platform and target credentials select registry images")
        if (self.target in {"archive", "sbom"}) != (
            self.input_artifact_ref is not None
        ):
            raise ValueError("archives and SBOMs are read from one input artifact")
        if registry:
            from moonmind.schemas.container_job_models import normalize_image_reference

            if any(c.isspace() for c in self.image_reference) or "://" in (
                self.image_reference
            ):
                raise ValueError("image reference must be a registry reference")
            normalize_image_reference(self.image_reference)
        if bool(self.database_cache_ref) != bool(self.database_digest):
            raise ValueError(
                "database cache and content identity are required together"
            )
        # Generation records inventory only; matching needs admitted data.
        if (self.operation == "sbom-generate") == bool(self.database_digest):
            raise ValueError(
                "vulnerability matching, and only matching, reads an offline database"
            )
        return self

    def outputs(self) -> list[dict]:
        native = "sbom" if self.operation == "sbom-generate" else "report"
        return [
            {"name": name, "relativePath": f"{self.output_directory}/{name}.json"}
            for name in [native, "execution", "summary"]
        ]


EndpointTarget = Literal["rootfs", "image", "results", "host"]
MAX_ENDPOINT_REPORT_BYTES = 32 * 1024 * 1024
#: XCCDF identifiers stay short, printable and separator-free.
XCCDF_ID = r"^xccdf_[A-Za-z0-9.-]+_KIND_[A-Za-z0-9_.-]{1,128}$"
XccdfRuleId = Annotated[
    str, StringConstraints(pattern=XCCDF_ID.replace("KIND", "rule"))
]
#: Request words an evaluation never accepts; it reads, it does not change.
_REMEDIATION_WORDS = ("remediat", "fix", "command", "script", "argv", "args")
#: OpenSSH host key types a live host may be pinned to.
HOST_KEY_TYPES = (
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "ssh-rsa",
)
_DOCKER_REF = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"


def host_key(value: str) -> str:
    """A pinned OpenSSH public host key as ``<type> <base64>``; comments drop.

    The decoded key must name its own type, so a key of one type cannot be
    pinned under another's name.
    """
    import base64
    import binascii

    parts = value.split() if isinstance(value, str) else []
    if len(parts) not in (2, 3) or parts[0] not in HOST_KEY_TYPES:
        raise ValueError("host identity must be an OpenSSH public host key")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("host identity must be an OpenSSH public host key") from None
    size = int.from_bytes(blob[:4], "big") if len(blob) >= 4 else -1
    if not 40 <= len(blob) <= 4096 or blob[4 : 4 + size] != parts[0].encode():
        raise ValueError("host identity must be an OpenSSH public host key")
    return f"{parts[0]} {parts[1]}"


def host_key_digest(value: str) -> str:
    """SHA-256 of the pinned key blob, as hex."""
    import base64
    import hashlib

    return hashlib.sha256(base64.b64decode(value.split()[1])).hexdigest()


def host_key_fingerprint(value: str) -> str:
    """The OpenSSH ``SHA256:`` fingerprint an operator compares on the host."""
    import base64

    digest = bytes.fromhex(host_key_digest(value))
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _live_host_address(value: str) -> str:
    from moonmind.security.native_network import canonical_host

    if "/" in str(value):
        raise ValueError("a live host is one address or DNS name, not a network")
    return canonical_host(value)


class EndpointHost(Contract):
    """One running Linux host, reached over SSH through the native gateway.

    ``address`` and ``port`` are the only endpoint the job's native network
    selection names; ``limits`` are its reserved connection, byte, rate and
    time budgets. ``hostKey`` pins the host's identity: SSH checks it before
    any credential is used, and an unknown or changed key stops the job.
    ``account`` is the login the host-inspection credential belongs to.
    ``elevated`` runs the evaluation through ``sudo -n``; it is never implied.
    Private addresses name the labeled container route that reaches them.
    """

    address: str = Field(max_length=253)
    port: int = Field(22, ge=1, le=65535)
    account: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    host_key: str = Field(max_length=8192)
    elevated: bool = Field(False, exclude_if=lambda value: not value)
    target_network_ref: str | None = Field(
        None, pattern=_DOCKER_REF, exclude_if=lambda value: value is None
    )
    target_container_ref: str | None = Field(
        None, pattern=_DOCKER_REF, exclude_if=lambda value: value is None
    )
    limits: NativeNetworkLimits

    _address = field_validator("address")(_live_host_address)
    # A lambda: in this class body ``host_key`` names the field, not the check.
    _key = field_validator("host_key")(lambda value: host_key(value))

    @model_validator(mode="after")
    def reachable_only_here(self):
        # The derived selection is the job's whole native reach; building it
        # applies the native route and private-address rules.
        self.native_selection()
        return self

    @property
    def authority(self) -> str:
        return f"{self.address}:{self.port}"

    def native_selection(self):
        from moonmind.security.native_network import (
            NativeNetworkSelection,
            NativeNetworkTarget,
        )

        return NativeNetworkSelection(
            targets=(
                NativeNetworkTarget(
                    hosts=(self.address,),
                    ports=(self.port,),
                    purpose="live-target",
                    targetNetworkRef=self.target_network_ref,
                    targetContainerRef=self.target_container_ref,
                ),
            ),
            limits=self.limits,
        )


class EndpointAssessment(Contract):
    """OpenSCAP evaluation of an endpoint, offline or live, or result import.

    ``rootfs`` and ``image`` read a captured root filesystem tar or Docker/OCI
    image archive and evaluate a ComplianceAsCode profile shipped in the
    scanner image, with no network. ``results`` imports ARF or XCCDF results
    the operator produced with OpenSCAP on the endpoint. Neither is MoonMind's
    own observation of the running device. ``host`` evaluates a running host
    inside an admitted assessment: the shipped datastream is evaluated there
    with the host's own ``oscap``, reached only through the admitted native
    gateway with a host-inspection credential. Evaluation is read-only: the
    contract has no remediation, fix-generation or command input.
    """

    target: EndpointTarget
    # The endpoint's stable name in the assessment scope; findings attach to it.
    asset: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
    input_artifact_ref: str | None = Field(None, pattern=r"^art_[0-9A-Za-z]{1,64}$")
    datastream: str | None = Field(None, pattern=r"^ssg-[a-z0-9]{2,32}-ds\.xml$")
    profile: str | None = Field(
        None, pattern=XCCDF_ID.replace("KIND", "profile"), max_length=176
    )
    tailoring_artifact_ref: str | None = Field(None, pattern=r"^art_[0-9A-Za-z]{1,64}$")
    # Rules this assessment must evaluate. Any the profile leaves unselected, or
    # that end without pass or fail, leave the run partial.
    rule_ids: list[XccdfRuleId] = Field(
        default_factory=list, max_length=256, exclude_if=lambda value: not value
    )
    # Live hosts only: the admitted assessment, the host-inspection credential
    # reference (never its value) and the native egress policy that reaches it.
    assessment_ref: str | None = Field(
        None,
        pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$",
        exclude_if=lambda value: value is None,
    )
    credential_ref: str | None = Field(
        None, min_length=4, max_length=1024, exclude_if=lambda value: value is None
    )
    egress_policy_ref: str | None = Field(
        None,
        pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$",
        exclude_if=lambda value: value is None,
    )
    host: EndpointHost | None = Field(None, exclude_if=lambda value: value is None)
    # Rules the evaluation skips (``oscap --skip-rule``); a live host's
    # authorization can require them.
    skip_rule_ids: list[XccdfRuleId] = Field(
        default_factory=list, max_length=256, exclude_if=lambda value: not value
    )
    output_directory: str = "endpoint-assessment"
    max_input_bytes: int = Field(2 * 1024**3, ge=1, le=4 * 1024**3)
    max_report_bytes: int = Field(
        MAX_ENDPOINT_REPORT_BYTES, ge=1024, le=MAX_ENDPOINT_REPORT_BYTES
    )
    timeout_seconds: int = Field(1800, ge=60, le=3600)

    @model_validator(mode="before")
    @classmethod
    def read_only(cls, value):
        if isinstance(value, dict) and any(
            word in str(key).lower() for key in value for word in _REMEDIATION_WORDS
        ):
            raise ValueError(
                "endpoint evaluation is read-only; remediation, fix generation and "
                "commands are refused"
            )
        return value

    @field_validator("output_directory")
    @classmethod
    def output_relative(cls, value):
        return relative_path(value)

    @field_validator("credential_ref")
    @classmethod
    def opaque_reference(cls, value):
        if value is not None and "://" not in value:
            raise ValueError("secret references must use an opaque reference URI")
        return value

    @model_validator(mode="after")
    def admitted_choices(self):
        evaluate = self.target != "results"
        live = self.target == "host"
        if evaluate and (self.datastream is None or self.profile is None):
            raise ValueError(
                "offline evaluation names a shipped datastream and profile"
                if not live
                else "a live host evaluation names a shipped datastream and profile"
            )
        if not evaluate and (self.datastream or self.tailoring_artifact_ref):
            raise ValueError("imported results already name their content")
        if live != (self.host is not None):
            raise ValueError("host targets, and only they, name a live host")
        if live:
            if self.input_artifact_ref or self.tailoring_artifact_ref:
                raise ValueError(
                    "a live host is evaluated in place; it reads no input artifact"
                )
            if not (
                self.assessment_ref and self.credential_ref and self.egress_policy_ref
            ):
                raise ValueError(
                    "a live host needs its assessment, host-inspection credential "
                    "and native egress policy"
                )
            if self.host.limits.max_duration_seconds > self.timeout_seconds:
                raise ValueError("native duration cannot exceed the evaluation timeout")
        else:
            if self.input_artifact_ref is None:
                raise ValueError(
                    "offline evaluation and imports read an input artifact"
                )
            if (
                self.assessment_ref
                or self.credential_ref
                or self.egress_policy_ref
                or self.skip_rule_ids
            ):
                raise ValueError(
                    "only a live host names an assessment, credential, egress "
                    "policy or skipped rules"
                )
        if len(set(self.rule_ids)) != len(self.rule_ids):
            raise ValueError("duplicate required rule")
        if len(set(self.skip_rule_ids)) != len(self.skip_rule_ids):
            raise ValueError("duplicate skipped rule")
        if set(self.rule_ids) & set(self.skip_rule_ids):
            raise ValueError("a required rule cannot be skipped")
        if (
            self.tailoring_artifact_ref
            and self.tailoring_artifact_ref == self.input_artifact_ref
        ):
            raise ValueError("tailoring is its own artifact")
        return self

    def outputs(self) -> list[dict]:
        return [
            {"name": "results", "relativePath": f"{self.output_directory}/results.xml"},
            {
                "name": "execution",
                "relativePath": f"{self.output_directory}/execution.json",
            },
            {
                "name": "summary",
                "relativePath": f"{self.output_directory}/summary.json",
            },
        ]


WebMode = Literal["baseline", "api", "active", "known-issues"]
HttpMethod = Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
#: The scope action class each mode needs. Network permission reaches the
#: target; only these classes authorize what is done to the application.
WEB_ACTION_CLASSES: dict[str, str] = {
    "baseline": "web-passive",
    "api": "web-api",
    "active": "web-active",
    "known-issues": "web-known-issue",
}
_ORIGIN = re.compile(
    r"^https?://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}"
    r"(?::[0-9]{1,5})?$"
)
_SEGMENT = r"[A-Za-z0-9._~!$&'()*+,;=:@%-]+"
# Segments are separated by exactly one slash, so matching is linear.
_ROUTE = re.compile(rf"^/(?:{_SEGMENT}(?:/{_SEGMENT})*/?)?$")
_NAME = r"^[a-z0-9][a-z0-9.-]{0,63}$"
WEB_OUTPUT_FILES = {
    "report": "report.json",
    "messages": "messages.har",
    "passive-report": "passive-report.json",
    "passive-messages": "passive-messages.har",
}


def web_origin(value: str) -> str:
    """``scheme://host[:port]`` with an exact DNS name, nothing else."""
    value = value.lower()
    if not _ORIGIN.fullmatch(value):
        raise ValueError("web origins are scheme://host[:port] with a DNS name")
    port = value.rpartition(":")[2]
    if value.count(":") == 2 and not 1 <= int(port) <= 65535:
        raise ValueError("web origin port is out of range")
    return value


def web_route(value: str) -> str:
    """A normalized absolute path prefix without query, fragment or dot parts."""
    if (
        len(value) > 512
        or not _ROUTE.fullmatch(value)
        or any(part in {".", ".."} for part in value.split("/"))
        or "//" in value
    ):
        raise ValueError("web routes are normalized absolute path prefixes")
    return value.rstrip("/") or "/"


def route_within(path: str, prefix: str) -> bool:
    """Whether ``path`` is ``prefix`` or below it on a segment boundary."""
    return prefix == "/" or path == prefix or path.startswith(prefix + "/")


class WebRoute(Contract):
    path: str
    methods: list[HttpMethod] = Field(
        default_factory=lambda: ["GET"], min_length=1, max_length=7
    )

    _path = field_validator("path")(web_route)

    @field_validator("methods")
    @classmethod
    def unique_methods(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("duplicate route method")
        return value


class WebIdentity(Contract):
    """One admitted test identity; its secret arrives only at launch.

    ``form`` posts ``username`` and the secret to ``loginPath``; ``bearer``
    sends the secret as a bearer token to the admitted origin only. The
    verification route must show ``loggedInMarker`` to the identity, and a
    form identity's route must not show it anonymously, so authentication is
    checked rather than assumed. A bearer token rides on every request, so
    its marker must be specific to the identity, such as its account name.
    """

    name: str = Field(pattern=_NAME)
    method: Literal["form", "bearer"]
    credential_ref: str = Field(min_length=4, max_length=1024)
    verification_path: str
    logged_in_marker: Text = Field(max_length=256)
    login_path: str | None = None
    username: Text | None = Field(None, max_length=256)
    username_field: str = Field("username", pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    password_field: str = Field("password", pattern=r"^[A-Za-z0-9_.-]{1,64}$")

    _paths = field_validator("verification_path", "login_path")(
        lambda value: None if value is None else web_route(value)
    )

    @field_validator("credential_ref")
    @classmethod
    def opaque_reference(cls, value):
        if "://" not in value:
            raise ValueError("secret references must use an opaque reference URI")
        return value

    @model_validator(mode="after")
    def method_fields(self):
        form = self.method == "form"
        if form != (self.login_path is not None and self.username is not None):
            raise ValueError("form identities, and only they, name a login and user")
        return self


class ApiDefinition(Contract):
    """An authorized OpenAPI or GraphQL definition read from one artifact."""

    format: Literal["openapi", "graphql"]
    input_artifact_ref: str = Field(pattern=r"^art_[0-9A-Za-z]{1,64}$")
    endpoint_path: str | None = None
    max_bytes: int = Field(2 * 1024 * 1024, ge=64, le=16 * 1024 * 1024)

    _endpoint = field_validator("endpoint_path")(
        lambda value: None if value is None else web_route(value)
    )

    @model_validator(mode="after")
    def endpoint(self):
        if (self.format == "graphql") != (self.endpoint_path is not None):
            raise ValueError("GraphQL definitions, and only they, name an endpoint")
        return self


class WebAssessment(Contract):
    """One ZAP Automation Framework run against one admitted origin.

    ``baseline`` crawls and checks passively; ``api`` imports an admitted
    definition and checks passively; ``active`` adds the active scanner and
    sends general attack payloads. ``known-issues`` emits only selected fixed
    HTTP recipes without discovery or credentials. One job tests at most one
    identity, so every observation is attributable to it.
    """

    mode: WebMode
    template_ids: list[Literal["nextjs-middleware-bypass"]] = Field(
        default_factory=list, max_length=1
    )
    assessment_ref: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$")
    origin: str
    routes: list[WebRoute] = Field(min_length=1, max_length=32)
    start_paths: list[str] = Field(
        default_factory=lambda: ["/"], min_length=1, max_length=16
    )
    exclusions: list[str] = Field(default_factory=list, max_length=64)
    identity: WebIdentity | None = None
    api_definition: ApiDefinition | None = None
    discovery: list[Literal["spider", "ajax-spider"]] = Field(
        default_factory=lambda: ["spider"], max_length=2
    )
    active_strength: Literal["low", "medium", "high"] | None = None
    egress_policy_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$")
    max_requests_per_second: int = Field(10, ge=1, le=100)
    max_depth: int = Field(5, ge=1, le=20)
    max_children: int = Field(50, ge=1, le=1000)
    output_directory: str = "web-assessment"
    max_report_bytes: int = Field(MAX_REPORT_BYTES, ge=128, le=MAX_REPORT_BYTES)
    timeout_seconds: int = Field(900, ge=300, le=3600)

    @model_serializer(mode="wrap")
    def _retained_scope_shape(self, handler):
        value = handler(self)
        # HTTP/RGS and real-time captures hash this nested contract. An empty
        # selection adds no authority and must retain their pre-template shape.
        if not self.template_ids:
            value.pop("template_ids", None)
            value.pop("templateIds", None)
        return value

    @model_validator(mode="before")
    @classmethod
    def curated_defaults(cls, value):
        if (
            isinstance(value, dict)
            and value.get("mode") == "known-issues"
            and "discovery" not in value
        ):
            return {**value, "discovery": []}
        return value

    _origin = field_validator("origin")(web_origin)

    @field_validator("start_paths", "exclusions")
    @classmethod
    def routes_only(cls, values):
        return [web_route(v) for v in values]

    @field_validator("output_directory")
    @classmethod
    def output_relative(cls, value):
        return relative_path(value)

    @model_validator(mode="after")
    def admitted_choices(self):
        paths = [r.path for r in self.routes]
        if len(set(paths)) != len(paths):
            raise ValueError("duplicate web route")
        if len(set(self.discovery)) != len(self.discovery):
            raise ValueError("duplicate discovery method")
        if "/" in self.exclusions:
            raise ValueError("an exclusion cannot cover the whole origin")

        def admitted(path: str, method: str = "GET") -> bool:
            return not any(route_within(path, e) for e in self.exclusions) and any(
                route_within(path, r.path) and method in r.methods for r in self.routes
            )

        if not all(admitted(p) for p in self.start_paths):
            raise ValueError("start paths must be admitted, unexcluded GET routes")
        if self.identity is not None:
            if not admitted(self.identity.verification_path):
                raise ValueError("identity verification must use an admitted route")
            if self.identity.login_path and not admitted(
                self.identity.login_path, "POST"
            ):
                raise ValueError("form login must POST to an admitted route")
        api = self.api_definition
        if (self.mode == "api") and api is None:
            raise ValueError("api mode reads an admitted API definition")
        if self.mode == "baseline" and api is not None:
            raise ValueError("baseline mode crawls; API definitions select api mode")
        if (
            api is not None
            and api.endpoint_path
            and not admitted(api.endpoint_path, "POST")
        ):
            raise ValueError("GraphQL endpoint must accept admitted POST requests")
        if self.mode == "known-issues":
            if (
                not self.template_ids
                or self.discovery
                or self.identity
                or api is not None
            ):
                raise ValueError(
                    "known-issue templates require explicit IDs, anonymous requests and no discovery or API input"
                )
            if len(set(self.start_paths)) != len(self.start_paths):
                raise ValueError("duplicate known-issue target path")
        elif self.template_ids:
            raise ValueError("template IDs require known-issues mode")
        if self.mode not in {"api", "known-issues"} and not self.discovery:
            raise ValueError("crawling modes need a discovery method")
        # Intensity is chosen explicitly and never inferred or upgraded.
        if (self.mode == "active") != (self.active_strength is not None):
            raise ValueError("active mode, and only it, sets an active strength")
        return self

    @property
    def action_class(self) -> str:
        return WEB_ACTION_CLASSES[self.mode]

    def outputs(self) -> list[dict]:
        # Active mode reports its passive phase first, so a timeout or
        # cancellation during active testing still leaves checked evidence.
        return [
            {"name": name, "relativePath": f"{self.output_directory}/{file}"}
            for name, file in WEB_OUTPUT_FILES.items()
            if self.mode == "active" or not name.startswith("passive-")
        ]


#: Providers with a real adapter. Others are refused at admission with an
#: explicit unsupported result rather than launched without coverage.
CLOUD_PROVIDERS = ("aws",)
#: The scope action class of read-only posture collection. Remediation is a
#: separate operation this workload cannot express.
CLOUD_ACTION_CLASS = "cloud-posture"
CLOUD_REGION = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-[0-9]{1,2}$")
_CHECK = re.compile(r"^[a-z0-9][a-z0-9_]{0,127}$")
_SERVICE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
#: A job command holds 128 argv entries and the rendered Prowler argv spends
#: 18 on the provider, flags and outputs; the rest name regions and checks or
#: services, so an admission never renders a job its command cannot hold.
CLOUD_SELECTION_ARGV = 110


def cloud_regions(values: list[str]) -> list[str]:
    if any(not CLOUD_REGION.fullmatch(v) for v in values):
        raise ValueError("cloud regions are provider region names")
    if len(set(values)) != len(values):
        raise ValueError("duplicate cloud region")
    return values


class CloudAssessment(Contract):
    """One read-only Prowler posture assessment of one admitted cloud account.

    The job selects its regions and either explicit checks or services; it
    never names remediation, export or notification options. The account's
    credential is a ``cloud_inventory`` grant exchanged for a read-only
    session, so one session must cover the whole budget.
    """

    provider: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")
    assessment_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$")
    account: str = Field(pattern=r"^[0-9]{12}$")
    credential_ref: str = Field(min_length=4, max_length=1024)
    regions: list[str] = Field(min_length=1, max_length=32)
    checks: list[str] = Field(default_factory=list, max_length=CLOUD_SELECTION_ARGV - 1)
    services: list[str] = Field(default_factory=list, max_length=64)
    egress_policy_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$")
    max_retry_attempts: int = Field(3, ge=0, le=10)
    output_directory: str = "cloud-assessment"
    max_report_bytes: int = Field(MAX_REPORT_BYTES, ge=1024, le=MAX_REPORT_BYTES)
    timeout_seconds: int = Field(1800, ge=300, le=43200)

    @field_validator("provider")
    @classmethod
    def supported_provider(cls, value):
        if value not in CLOUD_PROVIDERS:
            raise ValueError(
                f"cloud provider {value} is unsupported; supported: "
                + ", ".join(CLOUD_PROVIDERS)
            )
        return value

    _regions = field_validator("regions")(cloud_regions)

    @field_validator("credential_ref")
    @classmethod
    def opaque_reference(cls, value):
        if "://" not in value:
            raise ValueError("secret references must use an opaque reference URI")
        return value

    @field_validator("output_directory")
    @classmethod
    def output_relative(cls, value):
        return relative_path(value)

    @model_validator(mode="after")
    def explicit_selection(self):
        if bool(self.checks) == bool(self.services):
            raise ValueError("select either explicit checks or services")
        for values, pattern in ((self.checks, _CHECK), (self.services, _SERVICE)):
            if any(not pattern.fullmatch(v) for v in values):
                raise ValueError("checks and services are Prowler identifiers")
            if len(set(values)) != len(values):
                raise ValueError("duplicate check or service")
        if len(self.regions) + len(self.checks or self.services) > CLOUD_SELECTION_ARGV:
            raise ValueError(
                f"regions plus checks or services exceed the {CLOUD_SELECTION_ARGV} "
                "argv entries a job command can hold"
            )
        return self

    def outputs(self) -> list[dict]:
        return [
            {"name": name, "relativePath": f"{self.output_directory}/{file}"}
            for name, file in CLOUD_OUTPUT_FILES.items()
        ]


#: Prowler's native JSON-OCSF report and its error log.
CLOUD_OUTPUT_FILES = {"report": "prowler.ocsf.json", "log": "prowler.log"}


TLSProtocol = Literal["TLSv1", "TLSv1.1", "TLSv1.2", "TLSv1.3"]


class TLSAssessment(Contract):
    origin: str
    assessment_ref: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$")
    egress_policy_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$")
    protocols: list[TLSProtocol] = Field(
        default_factory=lambda: ["TLSv1.2", "TLSv1.3"], min_length=1, max_length=4
    )
    ciphers: list[str] = Field(default_factory=list, max_length=16)
    trust_artifact_ref: str | None = Field(None, pattern=r"^art_[0-9A-Za-z]{1,64}$")
    timeout_seconds: int = Field(120, ge=1, le=1800)
    max_requests_per_second: int = Field(1, ge=1, le=10)
    output_directory: str = "tls-assessment"

    @field_validator("origin")
    @classmethod
    def https_origin(cls, value):
        value = web_origin(value)
        if not value.startswith("https://"):
            raise ValueError("TLS inspection requires an admitted HTTPS origin")
        return value

    @field_validator("output_directory")
    @classmethod
    def relative(cls, value):
        return relative_path(value)

    @field_validator("ciphers")
    @classmethod
    def cipher_names(cls, values):
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", v) for v in values):
            raise ValueError("select individual cipher names, not cipher expressions")
        return values

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.protocols)) != len(self.protocols) or len(
            set(self.ciphers)
        ) != len(self.ciphers):
            raise ValueError("duplicate TLS probe")
        return self

    def outputs(self):
        return [
            {"name": name, "relativePath": f"{self.output_directory}/{file}"}
            for name, file in {
                "execution": "execution.json",
                "summary": "summary.json",
            }.items()
        ]
