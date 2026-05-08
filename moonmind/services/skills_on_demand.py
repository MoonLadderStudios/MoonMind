"""Skills On Demand runtime controls."""

import hashlib

from moonmind.schemas.agent_skill_models import (
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
    RuntimeSkillMaterialization,
    SkillsOnDemandMaterializationSummary,
    SkillCatalogSearchResult,
    SkillsOnDemandQueryRequest,
    SkillsOnDemandQueryResult,
    SkillsOnDemandRequest,
    SkillsOnDemandRequestResult,
)

SKILLS_ON_DEMAND_DISABLED_CODE = "feature_disabled"
SKILLS_ON_DEMAND_DISABLED_MESSAGE = (
    "Skills On Demand is disabled for this deployment."
)
SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_CODE = "enabled_mode_not_implemented"
SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_MESSAGE = (
    "Skills On Demand enabled mode is not implemented for this deployment."
)
SKILLS_ON_DEMAND_INVALID_REQUEST_CODE = "invalid_request"
SKILLS_ON_DEMAND_ALREADY_ACTIVE_CODE = "already_active"
SKILLS_ON_DEMAND_DISABLED_INSTRUCTION = (
    "- Skills On Demand is disabled for this run. Use only the active Skills "
    "already available under the active skill path provided by MoonMind."
)


class SkillsOnDemandService:
    """Handle runtime on-demand Skill operations."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        catalog_entries: list[ResolvedSkillEntry] | None = None,
        allow_repo_skills: bool = False,
        allow_local_skills: bool = False,
    ) -> None:
        self._enabled = enabled
        self._catalog_entries = catalog_entries or []
        self._allow_repo_skills = allow_repo_skills
        self._allow_local_skills = allow_local_skills

    async def query(
        self,
        request: SkillsOnDemandQueryRequest,
    ) -> SkillsOnDemandQueryResult:
        if not self._enabled:
            code, message = self._disabled_contract()
            return self._denied_result(code=code, message=message)

        validation_error = self._validate_query_request(request)
        if validation_error is not None:
            return self._denied_result(
                code=SKILLS_ON_DEMAND_INVALID_REQUEST_CODE,
                message=validation_error,
            )

        active_skill_names = {
            skill.skill_name
            for skill in (request.active_snapshot.skills if request.active_snapshot else [])
        }
        query = request.query.strip().lower()
        results: list[SkillCatalogSearchResult] = []
        for entry in sorted(self._catalog_entries, key=lambda item: item.skill_name):
            candidate_text = " ".join(
                part
                for part in [
                    entry.skill_name,
                    entry.version or "",
                    entry.selection_reason or "",
                ]
                if part
            ).lower()
            if query not in candidate_text:
                continue
            results.append(
                self._project_entry(
                    entry,
                    in_current_snapshot=entry.skill_name in active_skill_names,
                )
            )
            if len(results) >= request.max_results:
                break

        return SkillsOnDemandQueryResult(
            status="ok",
            code=None,
            message=self._result_message(len(results)),
            results=results,
            metadata={
                "result_count": len(results),
                "denied": False,
                "query_hash": self._query_hash(request.query),
            },
        )

    async def request(
        self,
        request: SkillsOnDemandRequest,
        *,
        resolved_skillset: ResolvedSkillSet | None = None,
        materialization: RuntimeSkillMaterialization | None = None,
    ) -> SkillsOnDemandRequestResult:
        if not self._enabled:
            code, message = self._disabled_contract()
            return self.denied_request_result(request, code=code, message=message)

        validation_error = self._validate_activation_request(request)
        if validation_error is not None:
            return self.denied_request_result(
                request,
                code=SKILLS_ON_DEMAND_INVALID_REQUEST_CODE,
                message=validation_error,
            )

        requested = self.normalized_requested_skills(request)
        active_snapshot = request.active_snapshot
        assert active_snapshot is not None
        active_entries = {entry.skill_name: entry for entry in active_snapshot.skills}
        if all(
            name in active_entries
            and (version is None or active_entries[name].version == version)
            for name, version in requested
        ):
            requested_names = [name for name, _version in requested]
            return SkillsOnDemandRequestResult(
                status="no_change",
                code=SKILLS_ON_DEMAND_ALREADY_ACTIVE_CODE,
                message="All requested Skills are already active.",
                active_snapshot_id=active_snapshot.snapshot_id,
                parent_snapshot_ref=active_snapshot.snapshot_id,
                snapshot_id=None,
                resolved_skillset_ref=active_snapshot.manifest_ref,
                activation_summary=(
                    "All requested Skills are already active in the current snapshot."
                ),
                metadata={
                    "requested_skills": requested_names,
                    "activated_skills": [],
                    "denied": False,
                },
            )

        if resolved_skillset is None:
            code, message = self._denial_contract()
            return self.denied_request_result(request, code=code, message=message)

        return self.activated_request_result(
            request,
            resolved_skillset=resolved_skillset,
            materialization=materialization,
        )

    def denied_request_result(
        self,
        request: SkillsOnDemandRequest,
        *,
        code: str,
        message: str,
    ) -> SkillsOnDemandRequestResult:
        active_snapshot = request.active_snapshot
        active_snapshot_id = active_snapshot.snapshot_id if active_snapshot else None
        requested_names = [
            skill.name.strip()
            for skill in request.requested_skills
            if skill.name and skill.name.strip()
        ]
        return SkillsOnDemandRequestResult(
            status="denied",
            code=code,
            message=message,
            active_snapshot_id=active_snapshot_id,
            parent_snapshot_ref=request.current_snapshot_ref or active_snapshot_id,
            snapshot_id=None,
            resolved_skillset_ref=None,
            activation_summary=None,
            materialization=None,
            metadata={
                "requested_skills": requested_names,
                "denied": True,
                "denial_code": code,
            },
        )

    def activated_request_result(
        self,
        request: SkillsOnDemandRequest,
        *,
        resolved_skillset: ResolvedSkillSet,
        materialization: RuntimeSkillMaterialization | None = None,
    ) -> SkillsOnDemandRequestResult:
        requested = self.normalized_requested_skills(request)
        requested_names = [name for name, _version in requested]
        active_names = {
            skill.skill_name for skill in (request.active_snapshot.skills if request.active_snapshot else [])
        }
        activated_names = [
            skill.skill_name
            for skill in resolved_skillset.skills
            if skill.skill_name in set(requested_names) and skill.skill_name not in active_names
        ]
        materialization_summary = self._materialization_summary(
            materialization,
            manifest_ref=resolved_skillset.manifest_ref,
        )
        activation_summary = self._activation_summary(activated_names)
        active_snapshot_id = (
            request.active_snapshot.snapshot_id if request.active_snapshot else None
        )
        return SkillsOnDemandRequestResult(
            status="activated",
            code=None,
            message=activation_summary,
            active_snapshot_id=active_snapshot_id,
            parent_snapshot_ref=request.current_snapshot_ref or active_snapshot_id,
            snapshot_id=resolved_skillset.snapshot_id,
            resolved_skillset_ref=resolved_skillset.manifest_ref
            or resolved_skillset.snapshot_id,
            activation_summary=activation_summary,
            materialization=materialization_summary,
            metadata={
                "requested_skills": requested_names,
                "activated_skills": activated_names,
                "created_by": "skills_on_demand",
                "denied": False,
            },
        )

    def normalized_requested_skills(
        self, request: SkillsOnDemandRequest
    ) -> list[tuple[str, str | None]]:
        seen: dict[str, str | None] = {}
        for skill in request.requested_skills:
            name = skill.name.strip()
            version = skill.version.strip() if skill.version is not None else None
            if name not in seen:
                seen[name] = version
        return [(name, version) for name, version in seen.items()]

    def _validate_activation_request(
        self, request: SkillsOnDemandRequest
    ) -> str | None:
        if request.active_snapshot is None:
            return "active_snapshot is required when Skills On Demand is enabled."
        if not request.current_snapshot_ref or not request.current_snapshot_ref.strip():
            return "current_snapshot_ref is required when Skills On Demand is enabled."
        if request.current_snapshot_ref != request.active_snapshot.snapshot_id:
            return "current_snapshot_ref does not match active_snapshot."
        if not request.requested_skills:
            return "requested_skills must contain at least one Skill."
        seen_versions: dict[str, str | None] = {}
        for skill in request.requested_skills:
            if not skill.name.strip():
                return "requested skill name must not be blank."
            version = skill.version.strip() if skill.version is not None else None
            if skill.version is not None and not version:
                return "requested skill version must not be blank when provided."
            name = skill.name.strip()
            if name in seen_versions and seen_versions[name] != version:
                return f"requested skill '{name}' has conflicting versions."
            seen_versions[name] = version
        for field_name in ("reason", "runtime_id", "step_id"):
            value = getattr(request, field_name)
            if value is not None and not value.strip():
                return f"{field_name} must not be blank when provided."
        return None

    def _denial_contract(self) -> tuple[str, str]:
        if self._enabled:
            return (
                SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_CODE,
                SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_MESSAGE,
            )
        return self._disabled_contract()

    def _disabled_contract(self) -> tuple[str, str]:
        return SKILLS_ON_DEMAND_DISABLED_CODE, SKILLS_ON_DEMAND_DISABLED_MESSAGE

    def _denied_result(
        self,
        *,
        code: str,
        message: str,
    ) -> SkillsOnDemandQueryResult:
        return SkillsOnDemandQueryResult(
            status="denied",
            code=code,
            message=message,
            results=[],
            metadata={
                "result_count": 0,
                "denied": True,
                "denial_code": code,
            },
        )

    def _validate_query_request(
        self,
        request: SkillsOnDemandQueryRequest,
    ) -> str | None:
        if not request.query.strip():
            return "Skills On Demand query must not be blank."
        if request.runtime_id is not None and not request.runtime_id.strip():
            return "runtime_id must not be blank when provided."
        if (
            request.current_snapshot_ref is not None
            and not request.current_snapshot_ref.strip()
        ):
            return "current_snapshot_ref must not be blank when provided."
        if (
            request.active_snapshot is not None
            and request.current_snapshot_ref
            and request.current_snapshot_ref != request.active_snapshot.snapshot_id
        ):
            return "current_snapshot_ref does not match active_snapshot."
        return None

    def _project_entry(
        self,
        entry: ResolvedSkillEntry,
        *,
        in_current_snapshot: bool,
    ) -> SkillCatalogSearchResult:
        eligible, summary = self._eligibility_for(entry)
        return SkillCatalogSearchResult(
            name=entry.skill_name,
            latest_version=entry.version,
            source_kind=entry.provenance.source_kind,
            eligible=eligible,
            in_current_snapshot=in_current_snapshot,
            eligibility_summary=summary,
        )

    def _eligibility_for(self, entry: ResolvedSkillEntry) -> tuple[bool, str]:
        source_kind = entry.provenance.source_kind
        if source_kind == AgentSkillSourceKind.REPO and not self._allow_repo_skills:
            return (
                False,
                "Blocked because repo Skill sources are disabled for this query.",
            )
        if source_kind == AgentSkillSourceKind.LOCAL and not self._allow_local_skills:
            return (
                False,
                "Blocked because local Skill sources are disabled for this query.",
            )
        return True, "Eligible for this runtime and deployment policy."

    def _result_message(self, result_count: int) -> str:
        if result_count == 1:
            return "Returned 1 Skill metadata result."
        return f"Returned {result_count} Skill metadata results."

    def _query_hash(self, query: str) -> str:
        return hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()

    def _activation_summary(self, activated_names: list[str]) -> str:
        if not activated_names:
            return "Skills On Demand activated the requested Skills."
        if len(activated_names) == 1:
            return (
                "Skills On Demand activated 1 requested Skill. Newly active Skills: "
                f"{activated_names[0]}."
            )
        return (
            f"Skills On Demand activated {len(activated_names)} requested Skills. "
            f"Newly active Skills: {', '.join(activated_names)}."
        )

    def _materialization_summary(
        self,
        materialization: RuntimeSkillMaterialization | None,
        *,
        manifest_ref: str | None,
    ) -> SkillsOnDemandMaterializationSummary | None:
        if materialization is None:
            return None
        visible_path = materialization.metadata.get("visiblePath")
        if visible_path is None and materialization.workspace_paths:
            visible_path = materialization.workspace_paths[0]
        return SkillsOnDemandMaterializationSummary(
            mode=materialization.materialization_mode,
            visible_path=visible_path,
            manifest_ref=manifest_ref
            or materialization.metadata.get("manifestRef")
            or materialization.metadata.get("manifestPath"),
        )


def skills_on_demand_disabled_instruction(*, enabled: bool) -> str:
    """Return runtime guidance when on-demand Skill commands cannot be hidden."""

    return "" if enabled else SKILLS_ON_DEMAND_DISABLED_INSTRUCTION
