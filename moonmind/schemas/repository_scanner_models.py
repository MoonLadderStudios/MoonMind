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
    model_validator,
)
from pydantic.alias_generators import to_camel

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
]
Items = Annotated[list[Text], Field(max_length=4096)]
Scanner = Literal["gitleaks", "opengrep", "trivy"]
MAX_REPORT_BYTES = 8 * 1024 * 1024
FeedComponent = Literal["vulnerability", "java", "checks"]
#: Freshness policy default; independent of scanner-image maintenance.
DEFAULT_FEED_MAX_AGE_HOURS = 72


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
    rules: list[ScannerRule] = Field(default_factory=list, max_length=32)
    exclusions: Items = Field(default_factory=list)
    allow_inline_suppressions: bool = False
    trivy_scanners: list[Literal["vuln", "misconfig", "secret"]] = Field(
        default_factory=lambda: ["vuln", "misconfig"], min_length=1, max_length=3
    )
    gitleaks_owns_secrets: bool = True
    database_cache_ref: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    database_digest: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    feed_max_age_hours: int = Field(DEFAULT_FEED_MAX_AGE_HOURS, ge=1, le=8760)
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
        if self.scanner == "opengrep" and not self.rules:
            raise ValueError("Opengrep requires selected licensed image rules")
        if self.scanner == "gitleaks" and len(self.rules) > 1:
            raise ValueError("Gitleaks accepts one trusted configuration")
        if len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError("duplicate selected rule identity")
        if len(set(self.trivy_scanners)) != len(self.trivy_scanners):
            raise ValueError("duplicate Trivy capability")
        if self.scanner == "trivy":
            if self.gitleaks_owns_secrets and "secret" in self.trivy_scanners:
                raise ValueError("Gitleaks already owns secret scanning")
            if "vuln" in self.trivy_scanners and not (
                self.database_cache_ref and self.database_digest
            ):
                raise ValueError(
                    "Trivy vulnerabilities require an admitted offline database"
                )
        if bool(self.database_cache_ref) != bool(self.database_digest):
            raise ValueError(
                "database cache and content identity are required together"
            )
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
        default_factory=lambda: ["vulnerability"], min_length=1, max_length=3
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
        if "vulnerability" not in self.components:
            raise ValueError("feed snapshots require the vulnerability database")
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


WebMode = Literal["baseline", "api", "active"]
HttpMethod = Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
#: The scope action class each mode needs. Network permission reaches the
#: target; only these classes authorize what is done to the application.
WEB_ACTION_CLASSES: dict[str, str] = {
    "baseline": "web-passive",
    "api": "web-api",
    "active": "web-active",
}
_ORIGIN = re.compile(
    r"^https?://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}"
    r"(?::[0-9]{1,5})?$"
)
_ROUTE = re.compile(r"^/(?:[A-Za-z0-9._~!$&'()*+,;=:@%-]+/?)*$")
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

    _path = field_validator("path")(lambda value: web_route(value))

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
    verification route must answer for the identity and not for anonymous
    use, so authentication is checked rather than assumed.
    """

    name: str = Field(pattern=_NAME)
    method: Literal["form", "bearer"]
    credential_ref: str = Field(min_length=4, max_length=1024)
    verification_path: str
    logged_in_marker: Text | None = Field(None, max_length=256)
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
        if form and self.logged_in_marker is None:
            raise ValueError("form identities need a logged-in marker to verify")
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
    is the only mode that sends attack payloads. One job tests at most one
    identity, so every observation is attributable to it.
    """

    mode: WebMode
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

    _origin = field_validator("origin")(lambda value: web_origin(value))

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
        if self.mode != "api" and not self.discovery:
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
