"""Container-image, image-archive and SBOM jobs on the scanner job contracts.

Trivy reads a registry image directly (``--image-src remote``), a captured
archive (``--input``) or a captured SBOM; no job mounts a container daemon
socket or runs privileged. Evidence names the resolved target, the feed
snapshot it was matched against, and what the inventory could not cover.
"""

from __future__ import annotations

import json
from typing import Literal
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import Field

from moonmind.schemas.repository_scanner_models import ImageAssessment
from moonmind.security.assessments import Contract, Finding, Items, Text
from moonmind.security.repository_scanners import NativeResult, normalize_native
from moonmind.security.scanner_feeds import FeedEvidence

IMAGE_INPUT, IMAGE_FILE = "image", "image.tar"
SBOM_INPUT, SBOM_FILE = "sbom", "sbom.json"
SOURCE_INPUT = "source"
#: Worker-owned read-only Docker config holding only the target credential.
TARGET_AUTH_TARGET = "/run/moonmind-target-registry"
DATABASE_TARGET = "/opt/moonmind-scanner-db"
#: Docker Hub's normalized name is not the host its registry API answers on.
_REGISTRY_HOSTS = {"docker.io": "registry-1.docker.io"}
#: Known-vulnerability matching explains its own reach in every summary.
INTERPRETATION = (
    "Known-vulnerability matching covers only the packages in the examined "
    "inventory. It does not establish exploitability, malware absence, or "
    "runtime safety."
)
INVENTORY_SCOPE = (
    "Trivy inventories packages it can identify from package-manager and "
    "language metadata; files without such metadata are not inventoried."
)


def build_image_assessment_job(
    assessment: ImageAssessment, *, image_source_ref: str = "repository-security"
) -> dict:
    """Workload only; admission stamps workspace and target authorization."""
    registry = assessment.target == "registry"
    attachments = {
        "registry": [],
        "archive": [
            {
                "name": IMAGE_INPUT,
                "artifactRef": assessment.input_artifact_ref,
                "fileName": IMAGE_FILE,
                "maxBytes": assessment.max_input_bytes,
            }
        ],
        "sbom": [
            {
                "name": SBOM_INPUT,
                "artifactRef": assessment.input_artifact_ref,
                "fileName": SBOM_FILE,
                "maxBytes": assessment.max_sbom_bytes,
            }
        ],
        "source": [
            {
                "name": SOURCE_INPUT,
                "workspacePath": ".",
                "includeGit": False,
                "maxBytes": assessment.max_input_bytes,
            }
        ],
    }[assessment.target]
    return {
        "imageSourceRef": image_source_ref,
        "imageAssessment": assessment.model_dump(mode="json", by_alias=True),
        "inputAttachments": attachments,
        "entrypoint": ["python"],
        "command": [
            "-I",
            "-m",
            "moonmind.security.image_assessment_runner",
            assessment.model_dump_json(by_alias=True),
            f"/outputs/{assessment.output_directory}",
        ],
        "networkMode": "bridge" if registry else "none",
        **({"egressPolicyRef": assessment.egress_policy_ref} if registry else {}),
        "workdir": "/workspace",
        "resources": {"cpuMillis": 2000, "memoryMiB": 4096, "pids": 256},
        "timeoutSeconds": assessment.timeout_seconds + 15,
        "outputs": assessment.outputs(),
        "caches": (
            [
                {
                    "cacheRef": assessment.database_cache_ref,
                    "target": DATABASE_TARGET,
                    "readOnly": True,
                }
            ]
            if assessment.database_cache_ref
            else []
        ),
    }


def validate_image_assessment_job(spec) -> None:
    """Rechecked wherever a spec is decoded: admission, persistence, worker.

    Every mount, network and environment entry must equal the canonical
    workload, so no request can add a daemon socket, host path, privileged
    setting or credential the admission did not name.
    """
    assessment = getattr(spec, "image_assessment", None)
    if assessment is None:
        return
    if (
        spec.repository_scan is not None
        or spec.feed_refresh is not None
        or spec.workload_profile is not None
    ):
        raise ValueError("image assessment is its own workload")
    if spec.image_source_ref is None:
        raise ValueError("image assessment uses a deployment-owned scanner image")
    if spec.input_attachments is None:
        raise ValueError("image assessment never mounts the live workspace")
    canonical = build_image_assessment_job(
        assessment, image_source_ref=spec.image_source_ref
    )
    for field, expected in (
        ("entrypoint", canonical["entrypoint"]),
        ("command", canonical["command"]),
        ("network_mode", canonical["networkMode"]),
        ("egress_policy_ref", canonical.get("egressPolicyRef")),
        ("workdir", "/workspace"),
        ("environment", []),
        ("service_ref", None),
        ("registry_credential_ref", None),
        ("timeout_seconds", canonical["timeoutSeconds"]),
    ):
        if getattr(spec, field) != expected:
            raise ValueError("image assessment workload differs from its admission")
    for field, key in (
        ("input_attachments", "inputAttachments"),
        ("outputs", "outputs"),
        ("caches", "caches"),
    ):
        actual = [v.model_dump(by_alias=True) for v in getattr(spec, field)]
        model = type(getattr(spec, field)[0]) if getattr(spec, field) else None
        expected = [
            model.model_validate(item).model_dump(by_alias=True) if model else item
            for item in canonical[key]
        ]
        if actual != expected:
            raise ValueError("image assessment mounts differ from its admission")


def registry_endpoint(assessment: ImageAssessment) -> tuple[str, int | None]:
    from moonmind.schemas.container_job_models import normalize_image_reference

    registry = normalize_image_reference(assessment.image_reference).registry
    host, _, port = registry.partition(":")
    return _REGISTRY_HOSTS.get(host, host), int(port) if port else None


def image_policy_admits(policy, assessment: ImageAssessment) -> bool:
    """Registry egress reaches the target registry and its declared dependencies.

    Every destination is registry API traffic; none reaches a provider, a live
    target network or anything else the scanner does not need.
    """
    host, port = registry_endpoint(assessment)
    return all(
        d.purpose == "registry-api" and d.target_network_ref is None
        for d in policy.destinations
    ) and any(
        d.host == host and (port is None or d.port == port) for d in policy.destinations
    )


class SbomError(ValueError):
    """A bounded diagnostic code; never carries document text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SbomInventory(Contract):
    """What a standard SBOM declares, as checked structure, not trusted claims."""

    format: Literal["cyclonedx", "spdx"]
    spec_version: Text
    generator: Text | None = None
    generator_version: Text | None = None
    # Package components only: an operating-system component or an
    # identifier-less application that merely groups packages is context.
    components: int = Field(ge=0)
    components_without_identifiers: int = Field(ge=0)
    # The document's own statement of completeness, when it makes one.
    declared_complete: bool | None = None
    # Unverified: what the document says it describes, and any embedded
    # signature or attestation material. Neither is treated as attestation.
    declared_subject: Text | None = None
    embedded_claims: Items = Field(default_factory=list)


_CYCLONEDX_VERSIONS = {"1.2", "1.3", "1.4", "1.5", "1.6", "1.7"}
_SPDX_VERSIONS = {"SPDX-2.2", "SPDX-2.3"}
_MAX_COMPONENTS = 200_000


def _text(value) -> str | None:
    return value[:1024] if isinstance(value, str) and value else None


def inspect_sbom(body: bytes, limit: int) -> tuple[SbomInventory, list[str]]:
    """Validate a CycloneDX or SPDX JSON document and report its coverage."""
    if len(body) > limit:
        raise SbomError("sbom_exceeds_bound")
    try:
        document = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise SbomError("sbom_malformed") from exc
    if not isinstance(document, dict):
        raise SbomError("sbom_malformed")
    claims = set()
    try:
        if document.get("bomFormat") == "CycloneDX":
            inventory = _cyclonedx(document, claims)
        elif "spdxVersion" in document:
            inventory = _spdx(document, claims)
        else:
            raise SbomError("sbom_format_unsupported")
    except (KeyError, TypeError, AttributeError, RecursionError) as exc:
        raise SbomError("sbom_malformed") from exc
    limitations = []
    if not inventory.components:
        limitations.append("sbom_inventory_empty")
    if inventory.components_without_identifiers:
        limitations.append("sbom_packages_without_identifiers")
    if inventory.declared_complete is False:
        limitations.append("sbom_declares_incomplete_inventory")
    return inventory, limitations


def _cyclonedx(document: dict, claims: set) -> SbomInventory:
    if document.get("specVersion") not in _CYCLONEDX_VERSIONS:
        raise SbomError("sbom_format_unsupported")
    metadata = document.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise SbomError("sbom_malformed")
    tools = metadata.get("tools") or []
    if isinstance(tools, dict):  # CycloneDX 1.5+ groups tools by kind
        tools = tools.get("components") or []
    tool = next((t for t in tools if isinstance(t, dict) and t.get("name")), {})
    # Generators such as Trivy add the detected OS and one application per
    # lock file or binary, linked to their packages through dependencies.
    # Those have no package identifier by design; counting them would mark
    # every real inventory as partial.
    groups = {
        entry.get("ref")
        for entry in document.get("dependencies") or []
        if entry.get("dependsOn")
    }
    examined = total = unidentified = 0
    pending = list(document.get("components") or [])
    while pending:
        component = pending.pop()
        if not isinstance(component, dict) or not isinstance(
            component.get("name"), str
        ):
            raise SbomError("sbom_malformed")
        examined += 1
        if examined > _MAX_COMPONENTS:
            raise SbomError("sbom_exceeds_bound")
        nested = component.get("components") or []
        if not isinstance(nested, list):
            raise SbomError("sbom_malformed")
        pending.extend(nested)
        identified = bool(component.get("purl") or component.get("cpe"))
        kind = component.get("type")
        if kind == "operating-system" or (
            kind == "application"
            and not identified
            and (nested or component.get("bom-ref") in groups)
        ):
            continue
        total += 1
        unidentified += not identified
    complete = None
    for composition in document.get("compositions") or []:
        aggregate = composition.get("aggregate")
        if aggregate == "complete" and complete is None:
            complete = True
        elif isinstance(aggregate, str) and aggregate != "complete":
            complete = False
    if "signature" in document:
        claims.add("signature")
    if "declarations" in document or "formulation" in document:
        claims.add("attestation")
    subject = metadata.get("component") or {}
    for item in [
        *(metadata.get("properties") or []),
        *(subject.get("properties") or []),
    ]:
        name = str(item.get("name", "")).lower()
        if any(word in name for word in ("attestation", "provenance", "signature")):
            claims.add("provenance-property")
    return SbomInventory(
        format="cyclonedx",
        specVersion=document["specVersion"],
        generator=_text(tool.get("name")),
        generatorVersion=_text(tool.get("version")),
        components=total,
        componentsWithoutIdentifiers=unidentified,
        declaredComplete=complete,
        declaredSubject=_text(subject.get("purl")) or _text(subject.get("name")),
        embeddedClaims=sorted(claims),
    )


def _spdx(document: dict, claims: set) -> SbomInventory:
    if document.get("spdxVersion") not in _SPDX_VERSIONS:
        raise SbomError("sbom_format_unsupported")
    packages = document.get("packages") or []
    if not isinstance(packages, list) or len(packages) > _MAX_COMPONENTS:
        raise SbomError("sbom_exceeds_bound" if packages else "sbom_malformed")
    unidentified = 0
    for package in packages:
        if not isinstance(package, dict) or not isinstance(package.get("name"), str):
            raise SbomError("sbom_malformed")
        references = package.get("externalRefs") or []
        if not any(
            r.get("referenceType") in {"purl", "cpe23Type", "cpe22Type"}
            for r in references
        ):
            unidentified += 1
    generator = version = None
    for creator in (document.get("creationInfo") or {}).get("creators") or []:
        if isinstance(creator, str) and creator.startswith("Tool:"):
            name, _, version = creator.removeprefix("Tool:").strip().rpartition("-")
            generator = name or version
            version = version if name else None
            break
    if document.get("annotations"):
        claims.add("annotation")
    return SbomInventory(
        format="spdx",
        specVersion=document["spdxVersion"],
        generator=_text(generator),
        generatorVersion=_text(version),
        components=len(packages),
        componentsWithoutIdentifiers=unidentified,
        declaredSubject=_text(document.get("name")),
        embeddedClaims=sorted(claims),
    )


class ImageSubject(Contract):
    """The target actually examined. ``asset`` names it in findings and reports.

    A registry asset carries the resolved manifest digest, so a moving tag
    never stands in for the content it pointed to. Archive, SBOM and source
    assets carry the digest of the captured bytes that were examined.
    """

    kind: Literal["registry", "archive", "sbom", "source"]
    asset: Text
    requested_reference: Text | None = None
    registry: Text | None = None
    repository: Text | None = None
    resolved_digest: str | None = Field(None, pattern=r"^sha256:[0-9a-f]{64}$")
    image_id: str | None = Field(None, pattern=r"^sha256:[0-9a-f]{64}$")
    os: Text | None = None
    architecture: Text | None = None
    variant: Text | None = None
    input_digest: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")


def _digest(value) -> str | None:
    if isinstance(value, str):
        value = value.rpartition("@")[2] if "@" in value else value
        value = unquote(value)
        if value.startswith("sha256:") and len(value) == 71:
            if all(c in "0123456789abcdef" for c in value[7:]):
                return value
    return None


def _matching_digest(values, assessment: ImageAssessment) -> str | None:
    """A repository digest counts only for the admitted registry/repository."""
    from moonmind.schemas.container_job_models import normalize_image_reference

    wanted = normalize_image_reference(assessment.image_reference)
    for value in values:
        if not isinstance(value, str) or "@" not in value:
            continue
        try:
            named = normalize_image_reference(value)
        except ValueError:
            continue
        if (named.registry, named.repository) == (wanted.registry, wanted.repository):
            return _digest(value)
    return None


def subject_from_report(
    assessment: ImageAssessment, report: dict, input_digest: str | None
) -> ImageSubject:
    """Identity from a Trivy JSON report's ``Metadata``."""
    metadata = report.get("Metadata") or {}
    config = metadata.get("ImageConfig") or {}
    return _subject(
        assessment,
        input_digest,
        resolved=(
            _matching_digest(metadata.get("RepoDigests") or [], assessment)
            if assessment.target == "registry"
            else None
        ),
        image_id=_digest(metadata.get("ImageID")),
        os=_text(config.get("os")),
        architecture=_text(config.get("architecture")),
        variant=_text(config.get("variant")),
    )


def subject_from_cyclonedx(
    assessment: ImageAssessment, document: dict, input_digest: str | None
) -> ImageSubject:
    """Identity from Trivy's CycloneDX ``metadata.component``."""
    component = (document.get("metadata") or {}).get("component") or {}
    properties = {
        str(p.get("name")): p.get("value")
        for p in component.get("properties") or []
        if isinstance(p, dict)
    }
    qualifiers, purl_digest = {}, None
    purl = component.get("purl")
    if isinstance(purl, str) and purl.startswith("pkg:oci/"):
        parts = urlsplit(purl)
        qualifiers = {k: v[0] for k, v in parse_qs(parts.query).items()}
        purl_digest = _digest(parts.path)
    resolved = None
    if assessment.target == "registry":
        named = [
            v for k, v in properties.items() if k == "aquasecurity:trivy:RepoDigest"
        ]
        if purl_digest and qualifiers.get("repository_url"):
            named.append(f"{qualifiers['repository_url']}@{purl_digest}")
        resolved = _matching_digest(named, assessment)
    return _subject(
        assessment,
        input_digest,
        resolved=resolved,
        image_id=_digest(properties.get("aquasecurity:trivy:ImageID")),
        os=None,
        architecture=_text(qualifiers.get("arch")),
        variant=None,
    )


def _subject(assessment, input_digest, *, resolved, **identity) -> ImageSubject:
    if assessment.target == "registry":
        from moonmind.schemas.container_job_models import normalize_image_reference

        named = normalize_image_reference(assessment.image_reference)
        location = f"{named.registry}/{named.repository}"
        return ImageSubject(
            kind="registry",
            asset=f"{location}@{resolved}" if resolved else assessment.image_reference,
            requestedReference=assessment.image_reference,
            registry=named.registry,
            repository=named.repository,
            resolvedDigest=resolved,
            inputDigest=None,
            **identity,
        )
    prefix = {"archive": "image-archive", "sbom": "sbom", "source": "source"}
    return ImageSubject(
        kind=assessment.target,
        asset=f"{prefix[assessment.target]}@sha256:{input_digest}",
        inputDigest=input_digest,
        **identity,
    )


def subject_limitations(
    assessment: ImageAssessment, subject: ImageSubject
) -> list[str]:
    limitations = []
    if assessment.target == "registry":
        if subject.resolved_digest is None:
            limitations.append("target_digest_unresolved")
        else:
            from moonmind.schemas.container_job_models import normalize_image_reference

            pinned = normalize_image_reference(assessment.image_reference).digest
            if pinned and pinned != subject.resolved_digest:
                limitations.append("target_digest_mismatch")
        if (
            assessment.platform
            and subject.architecture
            and (
                [subject.os or "linux", subject.architecture]
                != assessment.platform.split("/")[:2]
            )
        ):
            limitations.append("target_platform_mismatch")
    if assessment.target in {"registry", "archive"} and subject.architecture is None:
        limitations.append("target_platform_unrecorded")
    return limitations


def normalize_image_report(
    body: bytes,
    *,
    subject_asset: str,
    exit_code: int | None,
    terminal_state: str,
    native_ref: str,
) -> NativeResult:
    """Trivy's native result rows, attributed to the one examined subject.

    Native identity keeps each package and location distinct; the asset is the
    resolved image, archive or SBOM rather than a path inside it.
    """
    try:
        report = json.loads(body)
        for result in report.get("Results") or []:
            target = result.get("Target")
            if isinstance(target, str):
                for name in (IMAGE_INPUT, SBOM_INPUT, SOURCE_INPUT):
                    target = target.removeprefix(f"/inputs/{name}/")
                result["Target"] = target.lstrip("/") or "."
        body = json.dumps(report).encode()
    except (ValueError, TypeError, AttributeError, RecursionError):
        pass  # normalize_native records the malformed report.
    native = normalize_native(
        "trivy",
        body,
        exit_code=exit_code,
        terminal_state=terminal_state,
        native_ref=native_ref,
    )
    findings = {
        f.finding_id: f
        for f in (
            Finding.model_validate({**row.model_dump(), "asset": subject_asset})
            for row in native.findings
        )
    }
    return native.model_copy(
        update={
            "findings": list(findings.values()),
            "examined_assets": [subject_asset] if native.examined_assets else [],
            "unexamined_assets": [subject_asset] if native.unexamined_assets else [],
        }
    )


class Provenance(Contract):
    """Upstream claims are retained as claims; MoonMind verifies none of them."""

    verification: Literal["not_performed"] = "not_performed"
    declared_subject: Text | None = None
    embedded_claims: Items = Field(default_factory=list)


class ImageExecution(Contract):
    operation: Literal["assess", "sbom-generate", "sbom-reassess"]
    target: Literal["registry", "archive", "sbom", "source"]
    subject: ImageSubject | None = None
    version: str = Field(
        pattern=r"^(?:[0-9]+\.){1,3}[0-9]+(?:[-+][A-Za-z0-9.-]+)?$|^unavailable$"
    )
    exit_code: int | None = None
    state: Literal["succeeded", "failed", "timed_out", "canceled"]
    limitations: list[str] = Field(default_factory=list, max_length=128)
    feed: FeedEvidence | None = None
    database_digest: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    # Never the value: whether a worker-resolved target credential was used.
    target_credential: Literal["none", "delivered", "unavailable"] = "none"
    sbom: SbomInventory | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class RetainedSbom(Contract):
    artifact_ref: Text
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    inventory: SbomInventory


class ImageAssessmentSummary(Contract):
    schema_version: Literal["image-assessment/v1"] = "image-assessment/v1"
    tool: Literal["trivy"] = "trivy"
    admission: ImageAssessment
    execution: ImageExecution | None
    subject: ImageSubject | None
    result: NativeResult | None = None
    native_ref: Text | None = None
    sbom: RetainedSbom | None = None
    coverage: Literal["complete", "partial"]
    limitations: Items
    inventory_scope: Text = INVENTORY_SCOPE
    interpretation: Text = INTERPRETATION
    verification: Literal["not_performed"] = "not_performed"
