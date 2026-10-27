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
