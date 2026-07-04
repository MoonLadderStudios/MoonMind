import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

from temporalio import activity

from moonmind.schemas.agent_skill_models import (
    AgentSkillFormat,
    ResolvedSkillSet,
    SkillSelector,
    SkillSelectorEntry,
    SkillsOnDemandQueryRequest,
    SkillsOnDemandQueryResult,
    SkillsOnDemandRequest,
    SkillsOnDemandRequestResult,
    RuntimeSkillMaterialization,
    RuntimeMaterializationMode,
)
from moonmind.config.settings import settings
from moonmind.services.skills_on_demand import SkillsOnDemandService
from moonmind.services.skill_resolution import (
    AgentSkillResolver,
    SkillResolutionContext,
)
from moonmind.services.skill_materialization import AgentSkillMaterializer


class AgentSkillsActivities:
    """Temporal activities for managing agent skill resolution and materialization."""

    def __init__(
        self,
        artifact_service: Any | None = None,
        async_session_maker: Any | None = None,
    ) -> None:
        self._artifact_service = artifact_service
        self._async_session_maker = async_session_maker

    @activity.defn(name="agent_skill.resolve")
    async def resolve_skills(
        self,
        selector: SkillSelector,
        run_id: str | None = None,
        workspace_root: str | None = None,
        allow_local_skills: bool = False,
        allow_repo_skills: bool = False,
    ) -> ResolvedSkillSet:
        """Resolve a SkillSelector intent into a canonical ResolvedSkillSet."""

        # Instantiate the proper context bounds
        info = activity.info()
        snapshot_id = f"skillset_{info.workflow_id}_{info.activity_id}"

        context = SkillResolutionContext(
            snapshot_id=snapshot_id,
            deployment_id=run_id,
            workspace_root=workspace_root,
            allow_repo_skills=allow_repo_skills,
            allow_local_skills=allow_local_skills,
            async_session_maker=getattr(self, "_async_session_maker", None),
        )

        resolver = AgentSkillResolver()
        resolved_set = await resolver.resolve(selector, context)

        if self._artifact_service:
            resolved_set = await self._persist_file_backed_skill_content(
                resolved_set=resolved_set,
                activity_info=info,
            )
            resolved_set = await self._persist_resolved_skillset_manifest(
                resolved_set=resolved_set,
                snapshot_id=snapshot_id,
                activity_info=info,
            )

        return resolved_set

    async def _persist_file_backed_skill_content(
        self,
        *,
        resolved_set: ResolvedSkillSet,
        activity_info: Any,
    ) -> ResolvedSkillSet:
        from moonmind.workflows.temporal.artifacts import ExecutionRef
        from moonmind.core.artifacts import (
            TemporalArtifactRedactionLevel,
            TemporalArtifactRetentionClass,
        )

        updated_skills = []
        for skill in resolved_set.skills:
            if skill.content_ref or not skill.provenance.source_path:
                updated_skills.append(skill)
                continue

            skill_dir = Path(skill.provenance.source_path)
            try:
                payload = self._build_skill_bundle_payload(skill_dir)
            except OSError as exc:
                raise RuntimeError(
                    f"failed to persist selected skill '{skill.skill_name}' from {skill_dir}: {exc}"
                ) from exc

            digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            link = ExecutionRef(
                namespace=activity_info.namespace,
                workflow_id=activity_info.workflow_id,
                run_id=activity_info.workflow_run_id,
                link_type="input.agent_skill_body",
            )
            artifact, _ = await self._artifact_service.create(
                principal="agent_workflow",
                content_type="application/gzip",
                metadata_json={
                    "contentDigest": digest,
                    "contentFormat": AgentSkillFormat.BUNDLE.value,
                    "producer": "agent_skill.resolve",
                    "skillName": skill.skill_name,
                    "sourceKind": skill.provenance.source_kind.value,
                },
                link=link,
                retention_class=TemporalArtifactRetentionClass.LONG,
                redaction_level=TemporalArtifactRedactionLevel.NONE,
            )
            await self._artifact_service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="agent_workflow",
                payload=payload,
                content_type="application/gzip",
            )
            updated_skills.append(
                skill.model_copy(
                    update={
                        "content_digest": digest,
                        "content_ref": artifact.artifact_id,
                        "format": AgentSkillFormat.BUNDLE,
                    }
                )
            )

        return resolved_set.model_copy(update={"skills": updated_skills})

    @staticmethod
    def _build_skill_bundle_payload(skill_dir: Path) -> bytes:
        if not skill_dir.is_dir():
            raise OSError(f"skill source path is not a directory: {skill_dir}")
        if not (skill_dir / "SKILL.md").is_file():
            raise OSError(f"skill source path is missing SKILL.md: {skill_dir}")

        allowed_root = AgentSkillsActivities._skill_bundle_allowed_root(skill_dir)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for path in sorted(skill_dir.rglob("*")):
                if not path.is_file():
                    continue
                source_path = path
                if path.is_symlink():
                    source_path = path.resolve(strict=True)
                    try:
                        source_path.relative_to(allowed_root)
                    except ValueError as exc:
                        raise OSError(
                            "skill bundle symlink escapes allowed root: "
                            f"{path} -> {source_path}"
                        ) from exc
                archive.add(source_path, arcname=str(path.relative_to(skill_dir)))
        return buffer.getvalue()

    @staticmethod
    def _skill_bundle_allowed_root(skill_dir: Path) -> Path:
        resolved = skill_dir.resolve(strict=False)
        for candidate in (resolved, *resolved.parents):
            if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
                return candidate.resolve(strict=False)
        return resolved

    async def _persist_resolved_skillset_manifest(
        self,
        *,
        resolved_set: ResolvedSkillSet,
        snapshot_id: str,
        activity_info: Any,
    ) -> ResolvedSkillSet:
        from moonmind.workflows.temporal.artifacts import ExecutionRef
        from moonmind.core.artifacts import (
            TemporalArtifactRedactionLevel,
            TemporalArtifactRetentionClass,
        )

        link = ExecutionRef(
            namespace=activity_info.namespace,
            workflow_id=activity_info.workflow_id,
            run_id=activity_info.workflow_run_id,
            link_type="input.skill_snapshot",
        )
        payload = resolved_set.model_dump(mode="json")
        artifact, _ = await self._artifact_service.create(
            principal="agent_workflow",
            content_type="application/json",
            metadata_json={
                "producer": "agent_skill.resolve",
                "snapshot_id": snapshot_id,
            },
            link=link,
            retention_class=TemporalArtifactRetentionClass.LONG,
            redaction_level=TemporalArtifactRedactionLevel.NONE,
        )
        await self._artifact_service.write_complete(
            artifact_id=artifact.artifact_id,
            principal="agent_workflow",
            payload=json.dumps(payload, sort_keys=True, indent=2).encode("utf-8"),
            content_type="application/json",
        )
        return resolved_set.model_copy(update={"manifest_ref": artifact.artifact_id})

    @activity.defn(name="agent_skill.query_on_demand")
    async def query_on_demand(
        self,
        request: SkillsOnDemandQueryRequest,
    ) -> SkillsOnDemandQueryResult:
        """Query on-demand Skills through the runtime control gate."""

        catalog_entries = []
        if settings.workflow.skills_on_demand_enabled:
            info = activity.info()
            context = SkillResolutionContext(
                snapshot_id=(
                    request.current_snapshot_ref
                    or f"skillquery_{info.workflow_id}_{info.activity_id}"
                ),
                workspace_root=settings.workflow.repo_root,
                async_session_maker=getattr(self, "_async_session_maker", None),
            )
            catalog_entries = await AgentSkillResolver().query_catalog(
                SkillSelector(),
                context,
            )

        service = SkillsOnDemandService(
            enabled=settings.workflow.skills_on_demand_enabled,
            catalog_entries=catalog_entries,
        )
        result = await service.query(request)
        return await self._with_persisted_activity_audit_context(result)

    @activity.defn(name="agent_skill.request_on_demand")
    async def request_on_demand(
        self,
        request: SkillsOnDemandRequest,
    ) -> SkillsOnDemandRequestResult:
        """Request additional runtime Skills through the runtime control gate."""

        service = SkillsOnDemandService(
            enabled=settings.workflow.skills_on_demand_enabled
        )
        initial_result = await service.request(request)
        if (
            initial_result.status != "denied"
            or initial_result.code != "enabled_mode_not_implemented"
        ):
            return await self._with_persisted_activity_audit_context(initial_result)

        requested = service.normalized_requested_skills(request)
        active_snapshot = request.active_snapshot
        if active_snapshot is None:
            return await self._with_persisted_activity_audit_context(initial_result)

        try:
            info = activity.info()
            active_names = {skill.skill_name for skill in active_snapshot.skills}
            addition_selector = SkillSelector(
                include=[
                    SkillSelectorEntry(name=name)
                    for name in requested
                    if name not in active_names
                ]
            )
            context = SkillResolutionContext(
                snapshot_id=f"skillset_{info.workflow_id}_{info.activity_id}_derived",
                deployment_id=active_snapshot.deployment_id,
                workspace_root=settings.workflow.repo_root,
                async_session_maker=getattr(self, "_async_session_maker", None),
            )
            resolved_additions = await AgentSkillResolver().resolve(
                addition_selector,
                context,
                base_entries=list(active_snapshot.skills),
            )
            derived_set = self._build_on_demand_derived_skillset(
                active_snapshot=active_snapshot,
                resolved_additions=resolved_additions,
                request=request,
                snapshot_id=context.snapshot_id,
            )
            if self._artifact_service:
                derived_set = await self._persist_file_backed_skill_content(
                    resolved_set=derived_set,
                    activity_info=info,
                )
            materializer = AgentSkillMaterializer(
                workspace_root=settings.workflow.repo_root,
                artifact_service=self._artifact_service,
                backing_root=settings.workflow.skills_cache_root,
                source_preservation_root=settings.workflow.skills_workspace_root,
            )
            materialization = await materializer.materialize(
                derived_set,
                request.runtime_id or "managed-runtime",
                RuntimeMaterializationMode.WORKSPACE_MOUNTED,
            )
            if materialization.metadata.get("runtimeRefreshFailed"):
                message = (
                    materialization.metadata.get("runtimeRefreshMessage")
                    or "Skills On Demand runtime refresh failed."
                )
                return await self._with_persisted_activity_audit_context(
                    service.denied_request_result(
                        request,
                        code="runtime_refresh_failed",
                        message=self._safe_skills_on_demand_message(str(message)),
                    )
                )
            if self._artifact_service:
                derived_set = await self._persist_resolved_skillset_manifest(
                    resolved_set=derived_set,
                    snapshot_id=derived_set.snapshot_id,
                    activity_info=info,
                )
        except ValueError as exc:
            return await self._with_persisted_activity_audit_context(
                service.denied_request_result(
                    request,
                    code=self._skills_on_demand_resolution_code(str(exc)),
                    message=self._safe_skills_on_demand_message(str(exc)),
                )
            )
        except RuntimeError as exc:
            return await self._with_persisted_activity_audit_context(
                service.denied_request_result(
                    request,
                    code=self._skills_on_demand_runtime_code(str(exc)),
                    message=self._safe_skills_on_demand_message(str(exc)),
                )
            )

        result = await service.request(
            request,
            resolved_skillset=derived_set,
            materialization=materialization,
        )
        return await self._with_persisted_activity_audit_context(result)

    async def _with_persisted_activity_audit_context(self, result):
        enriched_result = self._with_activity_audit_context(result)
        if self._artifact_service is None or not getattr(
            enriched_result, "audit_events", None
        ):
            return enriched_result
        try:
            info = activity.info()
        except Exception:
            return enriched_result

        from moonmind.workflows.temporal.artifacts import ExecutionRef
        from moonmind.core.artifacts import (
            TemporalArtifactRedactionLevel,
            TemporalArtifactRetentionClass,
        )

        events = [
            event.model_dump(mode="json") for event in enriched_result.audit_events
        ]
        payload = {
            "producer": "agent_skill.skills_on_demand",
            "workflow_id": getattr(info, "workflow_id", None),
            "run_id": getattr(info, "workflow_run_id", None),
            "activity_id": getattr(info, "activity_id", None),
            "status": getattr(enriched_result, "status", None),
            "code": getattr(enriched_result, "code", None),
            "events": events,
        }
        link = ExecutionRef(
            namespace=info.namespace,
            workflow_id=info.workflow_id,
            run_id=info.workflow_run_id,
            link_type="audit.skills_on_demand",
        )
        artifact, _ = await self._artifact_service.create(
            principal="agent_workflow",
            content_type="application/json",
            metadata_json={
                "producer": "agent_skill.skills_on_demand",
                "eventTypes": sorted(
                    {
                        str(event.get("event_type"))
                        for event in events
                        if event.get("event_type")
                    }
                ),
                "status": getattr(enriched_result, "status", None),
                "code": getattr(enriched_result, "code", None),
            },
            link=link,
            retention_class=TemporalArtifactRetentionClass.LONG,
            redaction_level=TemporalArtifactRedactionLevel.NONE,
        )
        await self._artifact_service.write_complete(
            artifact_id=artifact.artifact_id,
            principal="agent_workflow",
            payload=json.dumps(payload, sort_keys=True, indent=2).encode("utf-8"),
            content_type="application/json",
        )
        metadata = dict(getattr(enriched_result, "metadata", {}) or {})
        metadata["audit_ref"] = artifact.artifact_id
        metadata["audit_event_count"] = len(events)
        return enriched_result.model_copy(update={"metadata": metadata})

    def _with_activity_audit_context(self, result):
        if not getattr(result, "audit_events", None):
            return result
        try:
            info = activity.info()
        except Exception:
            return result
        workflow_id = getattr(info, "workflow_id", None)
        run_id = getattr(info, "workflow_run_id", None)
        enriched = [
            event.model_copy(
                update={
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                }
            )
            for event in result.audit_events
        ]
        return result.model_copy(update={"audit_events": enriched})

    def _build_on_demand_derived_skillset(
        self,
        *,
        active_snapshot: ResolvedSkillSet,
        resolved_additions: ResolvedSkillSet,
        request: SkillsOnDemandRequest,
        snapshot_id: str,
    ) -> ResolvedSkillSet:
        active_by_name = {skill.skill_name: skill for skill in active_snapshot.skills}
        merged = dict(active_by_name)
        requested_names = set()
        for requested_skill in request.requested_skills:
            name = requested_skill.name.strip()
            if name:
                requested_names.add(name)
        for skill in resolved_additions.skills:
            active_skill = active_by_name.get(skill.skill_name)
            if (
                active_skill is not None
                and skill.skill_name not in requested_names
            ):
                continue
            merged[skill.skill_name] = skill.model_copy(
                update={"selection_reason": "skills_on_demand"}
            )
        requested_names = [
            skill.name.strip()
            for skill in request.requested_skills
            if skill.name and skill.name.strip()
        ]
        source_trace = dict(active_snapshot.source_trace)
        source_trace["skillsOnDemandLineage"] = {
            "parentSnapshotId": active_snapshot.snapshot_id,
            "parentManifestRef": active_snapshot.manifest_ref,
            "createdBy": "skills_on_demand",
            "requestedBy": "managed_agent",
            "requestReason": request.reason,
            "requestedSkills": requested_names,
            "runtimeId": request.runtime_id,
            "stepId": request.step_id,
        }
        return active_snapshot.model_copy(
            update={
                "snapshot_id": snapshot_id,
                "resolved_at": resolved_additions.resolved_at,
                "skills": sorted(merged.values(), key=lambda skill: skill.skill_name),
                "manifest_ref": resolved_additions.manifest_ref,
                "resolution_inputs": {
                    "skills_on_demand": {
                        "current_snapshot_ref": request.current_snapshot_ref,
                        "requested_skills": requested_names,
                        "runtime_id": request.runtime_id,
                        "step_id": request.step_id,
                    }
                },
                "source_trace": source_trace,
            }
        )

    def _skills_on_demand_resolution_code(self, message: str) -> str:
        lowered = message.lower()
        if "runtime" in lowered:
            return "runtime_incompatible"
        if "policy" in lowered or "forbidden" in lowered:
            return "policy_denied"
        return "skill_not_found"

    def _skills_on_demand_runtime_code(self, message: str) -> str:
        lowered = message.lower()
        if "checksum" in lowered:
            return "materialization_failed"
        if "artifact" in lowered or "content_ref" in lowered:
            return "artifact_unavailable"
        if "materializ" in lowered:
            return "materialization_failed"
        return "runtime_refresh_failed"

    def _safe_skills_on_demand_message(self, message: str) -> str:
        if not message:
            return "Skills On Demand request failed."
        return message.split("\n", 1)[0]

    @activity.defn(name="agent_skill.build_prompt_index")
    async def build_prompt_index(
        self, resolved_skillset: ResolvedSkillSet
    ) -> str:
        """Render a ResolvedSkillSet into a prompt-injectable payload."""

        lines = []
        lines.append(f"# Active Agent Skills (Snapshot: {resolved_skillset.snapshot_id})")
        lines.append(f"Resolved At: {resolved_skillset.resolved_at.isoformat()}")
        lines.append("---")

        if not resolved_skillset.skills:
            lines.append("No specialized agent skills active.")
            return "\n".join(lines)

        for skill in resolved_skillset.skills:
            lines.append(f"## Skill: {skill.skill_name}")
            if skill.provenance.source_kind:
                lines.append(f"Source: {skill.provenance.source_kind.value}")
                
            # Usually we'd fetch the content via content_ref if we are injecting the body directly.
            # Depending on mode, it assumes the task has content mounted via workspace or we inject here.
            # In purely prompt_bundled, we would read artifacts here.
            # For now, we mock basic metadata to fulfill the API promise and tests.
            if skill.content_ref:
                lines.append(f"[Content available at canonical ref: {skill.content_ref}]")
            else:
                lines.append("[No specific prompt bundle ref available]")
                
            lines.append("")

        return "\n".join(lines)

    @activity.defn(name="agent_skill.materialize")
    async def materialize(
        self,
        resolved_skillset: ResolvedSkillSet,
        runtime_id: str,
        mode: RuntimeMaterializationMode,
        workspace_root: str,
    ) -> RuntimeSkillMaterialization:
        """Materialize the immutable skill snapshot for a given runtime."""
        materializer = AgentSkillMaterializer(
            workspace_root=workspace_root,
            artifact_service=self._artifact_service,
        )
        return await materializer.materialize(
            resolved_skillset=resolved_skillset,
            runtime_id=runtime_id,
            mode=mode,
        )
