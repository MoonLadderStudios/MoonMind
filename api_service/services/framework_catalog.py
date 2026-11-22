"""Read-only catalog over installed packs and exact saved registrations."""

from datetime import UTC, datetime
from hashlib import sha256


from api_service.services.settings_catalog import SettingsCatalogService
from moonmind.security.assessment_projects import SETTINGS_KEY, AssessmentProjects
from moonmind.security.framework_packs import (
    MAX_PACK_BYTES,
    load_declared_pack,
    parse_declared_pack,
    validate_pack_effectivity,
)
from moonmind.security.framework_registry import readiness_catalog, subject_availability
from moonmind.security.frameworks import FrameworkPackIdentity, FrameworkPackSelection
from moonmind.security.web_api_methodology import MethodologyInput
from moonmind.workflows.skills.resolver import resolve_skill_markdown_path
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)


def _versioned_entry(source, pack_id, schema, **identity):
    return {
        "source": source, "id": pack_id, "title": pack_id, "edition": None,
        "revision": None, "amendments": [], "publicationStatus": None,
        "publishedOn": None, "effectiveFrom": None, "effectiveUntil": None,
        "license": None, "distribution": None, "subjects": subject_availability(),
        "jurisdiction": None, "sector": None, "requirementCount": None,
        "evidenceKinds": [], "externalEvidence": [], "conclusions": None,
        "inputSchema": schema, "available": False, "reason": None, **identity,
    }


def _describe(entry, pack, now):
    skill = entry["source"] == "skill"
    external_roles = {"laboratory", "qsa", "auditor", "regulator"}
    evidence, external = set(), set()
    for row in pack.requirements:
        types = {value.casefold() for value in row.evidence_types}
        if row.objectives or "deterministic_check" in row.required_evidence_methods:
            evidence.add("deterministic_check")
        if types.intersection({"organizational", "organisational", "documentary"}) or set(row.required_evidence_methods) - {"deterministic_check"}:
            evidence.add("organisational_evidence")
        if any(p.method in {"security-review", "security-investigate"} for p in row.procedures):
            evidence.add("agent_review")
        external.update(types & external_roles)
    if external:
        evidence.add("external_only")
    entry.update(
        edition=pack.edition, revision=pack.revision, amendments=pack.amendments,
        publicationStatus=pack.publication_status, publishedOn=pack.published_on,
        effectiveFrom=pack.effective_from, effectiveUntil=pack.effective_until,
        distribution=pack.distribution, requirementCount=len(pack.requirements),
        evidenceKinds=sorted(evidence), externalEvidence=sorted(external),
        conclusions=(
            "Coverage of the selected web/API procedures requires a retained assessment plan. "
            "Independent assurance remains external." if skill else
            "Evaluates scoped control objectives against admitted evidence. External assurance remains separate."
        ),
    )
    if pack.distribution == "redistributable":
        entry.update(title=pack.title, license=pack.license)
    else:
        entry["license"] = "See the authorized retained pack for license terms."
    try:
        validate_pack_effectivity(pack, now)
    except ValueError as error:
        entry["reason"] = f"{error}. Register an operative edition."
    else:
        entry["available"] = True
        entry["subjects"] = subject_availability("application", "endpoint") if skill else subject_availability("application")
    return entry


def _skill_entry(now):
    entry = _versioned_entry("skill", "web-api-methodology", MethodologyInput.model_json_schema())
    skill = resolve_skill_markdown_path("security-review")
    try:
        if skill is None:
            raise FileNotFoundError
        raw = (skill.parent / "references" / "web-api.json").read_bytes()
        pack = parse_declared_pack(raw)
        if pack.id != entry["id"]:
            raise ValueError
    except (OSError, ValueError, RecursionError):
        entry["reason"] = "Restore the installed security-review Skill's web/API pack."
        return entry
    entry["sha256"] = sha256(raw).hexdigest()
    return _describe(entry, pack, now)


async def _registered_entry(identity, artifacts, access, now, schema):
    entry = _versioned_entry(
        "declared", identity.pack_id, schema, edition=identity.edition,
        revision=identity.revision, artifactRef=identity.artifact_ref, sha256=identity.sha256,
    )
    try:
        artifact, _, _, policy = await artifacts.get_metadata(artifact_id=identity.artifact_ref, access=access)
        if not policy.raw_access_allowed:
            entry["reason"] = "Register a pack with permission to read its original bytes."
            return entry
        if artifact.size_bytes is None or artifact.size_bytes > MAX_PACK_BYTES:
            entry["reason"] = "Register a retained pack with a verified size of at most 2 MiB."
            return entry
        _, raw = await artifacts.read(artifact_id=identity.artifact_ref, access=access)
    except TemporalArtifactError:
        entry["reason"] = "Restore access to the retained pack artifact or register a readable copy."
        return entry
    if sha256(raw).hexdigest() != identity.sha256:
        entry["reason"] = "SHA-256 mismatch. Check the registered digest and artifact reference."
        return entry
    try:
        pack = load_declared_pack(raw, pack_id=identity.pack_id, edition=identity.edition, revision=identity.revision)
    except (ValueError, RecursionError):
        entry["reason"] = "Check the retained pack format, ID, edition and revision."
        return entry
    return _describe(entry, pack, now)


async def framework_catalog(session, access):
    current = await SettingsCatalogService(session=session).effective_value_async(SETTINGS_KEY)
    projects = AssessmentProjects.model_validate(current.value)
    registrations = {}
    for project in projects.projects:
        for environment in project.environments:
            for identity in environment.frameworks:
                if isinstance(identity, FrameworkPackIdentity):
                    key = (identity.artifact_ref, identity.pack_id, identity.edition, identity.revision, identity.sha256)
                    registrations[key] = identity
    now = datetime.now(UTC)
    items = [*readiness_catalog(), _skill_entry(now)]
    artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
    schema = FrameworkPackSelection.model_json_schema()
    for identity in registrations.values():
        items.append(await _registered_entry(identity, artifacts, access, now, schema))
    return {"items": items}
