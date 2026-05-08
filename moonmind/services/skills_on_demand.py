"""Skills On Demand runtime controls."""

import hashlib

from moonmind.schemas.agent_skill_models import (
    AgentSkillSourceKind,
    ResolvedSkillEntry,
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
    ) -> SkillsOnDemandRequestResult:
        code, message = self._denial_contract()
        active_snapshot = request.active_snapshot
        return SkillsOnDemandRequestResult(
            status="denied",
            code=code,
            message=message,
            active_snapshot_id=active_snapshot.snapshot_id if active_snapshot else None,
            snapshot_id=None,
            resolved_skillset_ref=None,
        )

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


def skills_on_demand_disabled_instruction(*, enabled: bool) -> str:
    """Return runtime guidance when on-demand Skill commands cannot be hidden."""

    return "" if enabled else SKILLS_ON_DEMAND_DISABLED_INSTRUCTION
