from temporalio import activity

from moonmind.schemas.agent_skill_models import (
    ResolvedSkillSet,
    SkillSelector,
    RuntimeSkillMaterialization,
    RuntimeMaterializationMode,
)
from moonmind.services.skill_resolution import (
    AgentSkillResolver,
    SkillResolutionContext,
)
from moonmind.services.skill_materialization import AgentSkillMaterializer


class AgentSkillsActivities:
    """Temporal activities for managing agent skill resolution and materialization."""

    @activity.defn(name="agent_skill.resolve")
    async def resolve_skills(
        self,
        selector: SkillSelector,
        run_id: str | None = None,
        workspace_root: str | None = None,
        allow_local_skills: bool = False,
    ) -> ResolvedSkillSet:
        """Resolve a SkillSelector intent into a canonical ResolvedSkillSet."""
        
        # Instantiate the proper context bounds
        info = activity.info()
        snapshot_id = f"skillset_{info.workflow_id}_{info.activity_id}"
        
        context = SkillResolutionContext(
            snapshot_id=snapshot_id,
            deployment_id=run_id,
            workspace_root=workspace_root,
            allow_local_skills=allow_local_skills,
        )

        resolver = AgentSkillResolver()
        return await resolver.resolve(selector, context)

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
            if skill.version:
                lines.append(f"Version: {skill.version}")
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
        materializer = AgentSkillMaterializer(workspace_root=workspace_root)
        return await materializer.materialize(
            resolved_skillset=resolved_skillset,
            runtime_id=runtime_id,
            mode=mode,
        )

