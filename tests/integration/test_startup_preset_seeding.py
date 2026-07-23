from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload, sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base, Preset, PresetScopeType
from api_service.main import startup_event
from api_service.services.presets.catalog import PresetCatalogService

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

@pytest.mark.asyncio
async def test_startup_seeds_default_task_templates(disabled_env_keys, tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    with (
        patch("api_service.main._initialize_embedding_model"),
        patch("api_service.main._initialize_vector_store"),
        patch("api_service.main._initialize_contexts"),
        patch("api_service.main._load_or_create_vector_index"),
        patch("api_service.main._initialize_oidc_provider"),
    ):
        await startup_event()

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(Preset)
            .where(
                Preset.slug == "moonspec-orchestrate",
                Preset.scope_type == PresetScopeType.GLOBAL,
                Preset.scope_ref.is_(None),
            )
            
        )
        template = result.scalar_one_or_none()
        assert template is not None
        assert template.release_status.value == "active"
        seeded_skill_ids = [
            step["skill"]["id"] for step in template.steps
        ]
        assert seeded_skill_ids == [
            "moonspec-specify",
            "moonspec-assess",
            "moonspec-plan",
            "moonspec-tasks",
            "moonspec-align",
            "moonspec-implement",
            "moonspec-verify",
            "moonspec-doc-reconcile",
        ]
        seeded_step_titles = [step["title"] for step in template.steps]
        assert "Classify request and resume point" not in seeded_step_titles
        assert "Split broad designs when needed" not in seeded_step_titles
        assert seeded_step_titles[0] == "Create or select MoonSpec"
        assert "moonspec-breakdown" not in seeded_skill_ids
        assert "speckit-analyze" not in seeded_skill_ids
        specify_step = template.steps[0]
        assert "Before running moonspec-specify, validate" in specify_step[
            "instructions"
        ]
        assert "stop immediately" in specify_step["instructions"]
        assert "routed through moonspec-breakdown" in specify_step["instructions"]
        assert "source-acceptance.json" in specify_step["instructions"]
        assess_step = next(
            (
                step
                for step in template.steps
                if step["title"] == "Assess source acceptance coverage"
            ),
            None,
        )
        assert (
            assess_step is not None
        ), "Step 'Assess source acceptance coverage' not found in template steps"
        assert assess_step["skill"]["id"] == "moonspec-assess"
        assert "acceptance-assessment.json" in assess_step["instructions"]
        assert "bounded backlog" in assess_step["instructions"]
        tasks_step = next(
            step
            for step in template.steps
            if step["title"] == "Generate TDD task breakdown"
        )
        assert "/moonspec.verify" in tasks_step["instructions"]
        assert "/speckit.verify" not in tasks_step["instructions"]

        result = await session.execute(
            select(Preset)
            .where(
                Preset.slug == "jira-breakdown",
                Preset.scope_type == PresetScopeType.GLOBAL,
                Preset.scope_ref.is_(None),
            )
            
        )
        jira_template = result.scalar_one_or_none()
        assert jira_template is not None
        assert jira_template.title == "Breakdown and Jira Create"
        assert "jira_board_id" not in {
            item["name"] for item in jira_template.inputs_schema
        }
        assert [
            (step.get("skill") or step.get("tool"))["id"]
            for step in jira_template.steps
        ] == [
            "jira.load_preset_brief",
            "moonspec-breakdown",
            "story.create_jira_issues",
        ]
        jira_create_step = jira_template.steps[2]
        assert "Selected Jira board" not in jira_create_step["instructions"]
        assert "boardId" not in jira_create_step["storyOutput"]["jira"]

        result = await session.execute(
            select(Preset)
            .where(
                Preset.slug == "jira-orchestrate",
                Preset.scope_type == PresetScopeType.GLOBAL,
                Preset.scope_ref.is_(None),
            )
            
        )
        jira_orchestrate_template = result.scalar_one_or_none()
        assert jira_orchestrate_template is not None
        annotations = jira_orchestrate_template.annotations or {}
        assert annotations["inputSchema"]["required"] == ["jira_issue"]
        assert annotations["inputSchema"]["properties"]["jira_issue"]["required"] == [
            "key"
        ]
        assert (
            annotations["inputSchema"]["properties"]["run_verify"]["type"]
            == "boolean"
        )
        assert (
            annotations["inputSchema"]["properties"]["run_verify"]["default"]
            is True
        )
        assert annotations["defaults"]["run_verify"] is True
        assert (
            annotations["uiSchema"]["jira_issue"]["widget"]
            == "jira.issue-picker"
        )
        assert annotations["uiSchema"]["jira_issue"]["allowManualKeyEntry"] is True
        jira_orchestrate_steps = [
            (step.get("skill") or step.get("tool"))["id"]
            for step in jira_orchestrate_template.steps
        ]
        assert jira_orchestrate_steps[0] == "jira.check_blockers"
        assert jira_orchestrate_steps[1] == "jira.load_preset_brief"
        assert jira_orchestrate_steps[2] == "auto"
        assert jira_orchestrate_steps[3] == "jira.update_issue_status"
        assert "moonspec-implement" in jira_orchestrate_steps
        assert "moonspec-verify" in jira_orchestrate_steps
        assert jira_orchestrate_steps[-1] == "jira-issue-updater"
        assert "moonspec-assess" in jira_orchestrate_steps
        assert len(jira_orchestrate_steps) == 26
        assert jira_orchestrate_steps.count("moonspec-implement") == 7
        assert jira_orchestrate_steps.count("moonspec-verify") == 7
        assert jira_orchestrate_steps.count("moonspec-doc-reconcile") == 1
        jira_orchestrate_titles = [
            step["title"] for step in jira_orchestrate_template.steps
        ]
        assert "Split broad designs when needed" not in jira_orchestrate_titles
        classify_step = next(
            step
            for step in jira_orchestrate_template.steps
            if step["title"] == "Classify request and resume point"
        )
        assert "upstream breakdown/selector workflow" in classify_step[
            "instructions"
        ]
        specify_step = next(
            step
            for step in jira_orchestrate_template.steps
            if step["title"] == "Create or select MoonSpec"
        )
        assert "Do not run moonspec-breakdown from this preset" in specify_step[
            "instructions"
        ]
        blocker_step = jira_orchestrate_template.steps[0]
        assert blocker_step["title"] == "Check Jira blockers before implementation"
        assert blocker_step["type"] == "tool"
        assert blocker_step["tool"]["id"] == "jira.check_blockers"
        assert "deterministic trusted Jira blocker preflight" in blocker_step["instructions"]
        assert "other issue as outwardIssue" in blocker_step["instructions"]
        assert "other issue as inwardIssue" in blocker_step["instructions"]
        assert "Done" in blocker_step["instructions"]
        assert "non-blocker" in blocker_step["instructions"]
        assert "status cannot be determined" in blocker_step["instructions"]
        assert "stop the orchestration immediately" in blocker_step["instructions"]
        brief_step = jira_orchestrate_template.steps[1]
        assert brief_step["title"] == "Load Jira preset brief"
        assert brief_step["type"] == "tool"
        assert brief_step["tool"]["id"] == "jira.load_preset_brief"
        remediation_step = next(
            step
            for step in jira_orchestrate_template.steps
            if step["title"] == "Remediate verification gaps — attempt 1 of 6"
        )
        assert remediation_step["skill"]["id"] == "moonspec-implement"
        assert "ADDITIONAL_WORK_NEEDED" in remediation_step["instructions"]
        assert "verification report's gaps" in remediation_step["instructions"]
        remediation_verify_step = next(
            step
            for step in jira_orchestrate_template.steps
            if step["title"] == "Verify remediation attempt 6 of 6"
        )
        assert remediation_verify_step["skill"]["id"] == "moonspec-verify"
        assert remediation_verify_step["skill"]["args"]["verify_artifact_path"] == (
            "var/artifacts/moonspec-verify/jira-orchestrate.json"
        )
        assert "controlling verification gate" in remediation_verify_step["instructions"]
        doc_reconcile_step = next(
            step
            for step in jira_orchestrate_template.steps
            if step["title"] == "Reconcile declarative docs"
        )
        assert doc_reconcile_step["skill"]["id"] == "moonspec-doc-reconcile"
        assert doc_reconcile_step["annotations"] == {
            "jiraOrchestrateRole": "doc-reconciliation"
        }
        assert "FULLY_IMPLEMENTED" in doc_reconcile_step["instructions"]
        assert "starting authority candidate" in doc_reconcile_step["instructions"]
        assert "authority ladder and module-owned contract policy" in doc_reconcile_step[
            "instructions"
        ]
        assert "owning canonical doc may be different" in doc_reconcile_step[
            "instructions"
        ]
        assert "ownership is ambiguous" in doc_reconcile_step["instructions"]
        assert "updated, noUpdateRequired, escalated" in doc_reconcile_step[
            "instructions"
        ]
        assert "artifacts/jira-orchestrate-doc-reconcile.json" in doc_reconcile_step[
            "instructions"
        ]
        doc_reconcile_index = jira_orchestrate_template.steps.index(
            doc_reconcile_step
        )
        pr_step = next(
            step
            for step in jira_orchestrate_template.steps
            if step["title"] == "Create pull request"
        )
        assert (
            jira_orchestrate_template.steps.index(pr_step)
            == doc_reconcile_index + 1
        )
        assert "pull request" in pr_step["instructions"]
        assert "doc reconciliation outcome" in pr_step["instructions"]
        assert "post-remediation moonspec-verify" in pr_step["instructions"]
        assert "parent workflow must use the pull request URL" in pr_step["instructions"]
        assert "explicit PR-publication step" in pr_step["instructions"]
        assert "controlling instruction for this step only" in pr_step["instructions"]
        assert "merge automation" in pr_step["instructions"]
        assert "Documentation Conformance section" in pr_step["instructions"]
        assert "canonical sources" in pr_step["instructions"]
        assert "temporary artifacts consulted" in pr_step["instructions"]
        assert "claim coverage" in pr_step["instructions"]
        assert "reconciliation outcomes" in pr_step["instructions"]
        assert "artifacts/jira-orchestrate-pr.json" in pr_step["instructions"]
        code_review_step = next(
            step
            for step in jira_orchestrate_template.steps
            if step["title"] == "Move Jira issue to Review"
        )
        assert code_review_step == jira_orchestrate_template.steps[-1]
        assert code_review_step["annotations"] == {
            "jiraOrchestrateRole": "code-review-handoff"
        }
        assert "status Review" in code_review_step["instructions"]
        assert "pull_request_url" in code_review_step["instructions"]

        expanded_orchestrate_without_verify = await PresetCatalogService(
            session
        ).expand_template(
            slug="jira-orchestrate",
            scope="global",
            scope_ref=None,
            inputs={
                "jira_issue": {"key": "MM-999"},
                "constraints": "",
                "run_verify": False,
            },
            context={"repository": "MoonLadderStudios/MoonMind"},
        )
        no_verify_orchestrate_titles = [
            step["title"] for step in expanded_orchestrate_without_verify["steps"]
        ]
        no_verify_orchestrate_skill_ids = [
            (step.get("skill") or step.get("tool"))["id"]
            for step in expanded_orchestrate_without_verify["steps"]
        ]
        assert "Verify completion" not in no_verify_orchestrate_titles
        assert "Verify remediation attempt 6 of 6" not in no_verify_orchestrate_titles
        assert "Reconcile declarative docs" not in no_verify_orchestrate_titles
        assert "moonspec-verify" not in no_verify_orchestrate_skill_ids
        assert "Implement the task breakdown" in no_verify_orchestrate_titles
        assert "Create pull request" in no_verify_orchestrate_titles
        no_verify_orchestrate_pr_step = next(
            step
            for step in expanded_orchestrate_without_verify["steps"]
            if step["title"] == "Create pull request"
        )
        assert "Verification was disabled" in no_verify_orchestrate_pr_step[
            "instructions"
        ]
        assert "confirm the verdict is FULLY_IMPLEMENTED" not in (
            no_verify_orchestrate_pr_step["instructions"]
        )

        result = await session.execute(
            select(Preset)
            .where(
                Preset.slug == "jira-implement",
                Preset.scope_type == PresetScopeType.GLOBAL,
                Preset.scope_ref.is_(None),
            )
            
        )
        jira_implement_template = result.scalar_one_or_none()
        assert jira_implement_template is not None
        implement_annotations = (
            jira_implement_template.annotations or {}
        )
        assert implement_annotations["inputSchema"]["required"] == ["jira_issue"]
        assert implement_annotations["inputSchema"]["properties"]["jira_issue"][
            "required"
        ] == ["key"]
        assert (
            implement_annotations["uiSchema"]["jira_issue"]["widget"]
            == "jira.issue-picker"
        )
        assert (
            implement_annotations["uiSchema"]["jira_issue"]["allowManualKeyEntry"]
            is True
        )
        assert (
            implement_annotations["inputSchema"]["properties"]["run_verify"]["type"]
            == "boolean"
        )
        assert (
            implement_annotations["inputSchema"]["properties"]["run_verify"]["default"]
            is True
        )
        assert implement_annotations["defaults"]["run_verify"] is True
        assert (
            implement_annotations.get("postMergeJiraCompletion") == "done_category"
        )
        raw_jira_implement_steps = jira_implement_template.steps
        assert raw_jira_implement_steps[1]["kind"] == "include"
        assert raw_jira_implement_steps[1]["slug"] == "issue-implement-assessment"
        assert raw_jira_implement_steps[4]["kind"] == "include"
        assert raw_jira_implement_steps[4]["slug"] == "issue-implement-work-pr"

        expanded_implement = await PresetCatalogService(session).expand_template(
            slug="jira-implement",
            scope="global",
            scope_ref=None,
            inputs={"jira_issue": {"key": "MM-999"}, "constraints": ""},
            context={"repository": "MoonLadderStudios/MoonMind"},
        )
        expanded_steps = expanded_implement["steps"]
        jira_implement_steps = [
            (step.get("skill") or step.get("tool"))["id"]
            for step in expanded_steps
        ]
        assert jira_implement_steps[0] == "jira.load_preset_brief"
        assert jira_implement_steps[1] == "auto"
        assert jira_implement_steps[2] == "jira.check_blockers"
        assert jira_implement_steps[3] == "jira.update_issue_status"
        assert jira_implement_steps[-1] == "jira-issue-updater"
        assert len(jira_implement_steps) == 9
        implement_step_titles = [step["title"] for step in expanded_steps]
        assert implement_step_titles[0] == "Load Jira preset brief"
        assert implement_step_titles[1] == "Assess existing implementation state"
        assert implement_step_titles[2] == "Check Jira blockers before implementation"
        assert implement_step_titles[3] == "Move Jira issue to In Progress"
        assert "Implement the issue" in implement_step_titles
        assert "Verify implementation" in implement_step_titles
        assert "Remediation loop controller" in implement_step_titles
        assert "Create pull request" in implement_step_titles
        assert implement_step_titles[-1] == "Finalize Jira status"
        implement_brief_step = expanded_steps[0]
        assert implement_brief_step["type"] == "tool"
        assert implement_brief_step["tool"]["id"] == "jira.load_preset_brief"
        assert implement_brief_step["tool"]["inputs"] == {
            "issueKey": "MM-999",
            "artifactPath": "artifacts/jira-implement-brief.json",
        }
        implement_assessment_step = expanded_steps[1]
        assert implement_assessment_step["title"] == "Assess existing implementation state"
        implement_blocker_step = expanded_steps[2]
        assert implement_blocker_step["type"] == "tool"
        assert implement_blocker_step["tool"]["id"] == "jira.check_blockers"
        assert implement_blocker_step["tool"]["inputs"] == {
            "targetIssueKey": "MM-999",
            "assessmentArtifactPath": "artifacts/jira-implement-assessment.json",
            "blockerPreflight": {
                "targetIssueKey": "MM-999",
                "linkType": "Blocks",
            },
        }
        assert (
            "deterministic trusted Jira blocker preflight"
            in implement_blocker_step["instructions"]
        )
        assert (
            "FULLY_IMPLEMENTED" in implement_blocker_step["instructions"]
        )
        assert implement_assessment_step["skill"]["id"] == "auto"
        assert (
            "FULLY_IMPLEMENTED" in implement_assessment_step["instructions"]
        )
        assert (
            "PARTIALLY_IMPLEMENTED" in implement_assessment_step["instructions"]
        )
        assert (
            "NOT_IMPLEMENTED" in implement_assessment_step["instructions"]
        )
        assert (
            "artifacts/jira-implement-assessment.json"
            in implement_assessment_step["instructions"]
        )
        assert (
            "artifacts/jira-implement-brief.json"
            in implement_assessment_step["instructions"]
        )
        implement_in_progress_step = expanded_steps[3]
        assert implement_in_progress_step["title"] == "Move Jira issue to In Progress"
        assert implement_in_progress_step["type"] == "tool"
        assert implement_in_progress_step["tool"]["id"] == "jira.update_issue_status"
        assert implement_in_progress_step["tool"]["inputs"] == {
            "issueKey": "MM-999",
            "targetStatus": "In Progress",
            "assessmentArtifactPath": "artifacts/jira-implement-assessment.json",
        }
        assert (
            "FULLY_IMPLEMENTED" in implement_in_progress_step["instructions"]
        )
        assert (
            "skip the In Progress transition"
            in implement_in_progress_step["instructions"]
        )
        implement_step = next(
            step
            for step in expanded_steps
            if step["title"] == "Implement the issue"
        )
        assert "FULLY_IMPLEMENTED" in implement_step["instructions"]
        assert "PARTIALLY_IMPLEMENTED" in implement_step["instructions"]
        assert (
            "artifacts/jira-implement-assessment.json"
            in implement_step["instructions"]
        )
        implement_pr_step = next(
            step
            for step in expanded_steps
            if step["title"] == "Create pull request"
        )
        verify_step = next(
            step
            for step in expanded_steps
            if step["title"] == "Verify implementation"
        )
        assert verify_step["skill"]["id"] == "moonspec-verify"
        assert verify_step["skill"]["args"] == {
            "verification_target": "issue_brief",
            "issue_provider": "jira",
            "issue_ref": "MM-999",
            "brief_artifact_path": "artifacts/jira-implement-brief.json",
            "assessment_artifact_path": "artifacts/jira-implement-assessment.json",
            "verify_artifact_path": "artifacts/jira-implement-verify.json",
        }
        assert "verification target issue_brief" in verify_step["instructions"]
        assert "artifacts/jira-implement-verify.json" in verify_step["instructions"]
        remediation_step = next(
            step
            for step in expanded_steps
            if step["title"] == "Remediation loop controller"
        )
        assert remediation_step["annotations"]["issueImplementRole"] == (
            "moonspec-remediation-loop"
        )
        remediation_loop = remediation_step["annotations"]["remediationLoop"]
        assert remediation_loop["kind"] == "remediation_loop"
        assert remediation_loop["budgets"]["hardMaxAttempts"] == "6"
        assert remediation_loop["workspacePolicy"] == "continue_from_loop_head"
        assert remediation_loop["verificationTool"]["inputs"][
            "verify_artifact_path"
        ] == "artifacts/jira-implement-verify.json"
        assert "controlling post-remediation moonspec-verify verdict is FULLY_IMPLEMENTED" in (
            implement_pr_step["instructions"]
        )
        assert "artifacts/jira-implement-verify.json" in implement_pr_step[
            "instructions"
        ]
        assert "merge automation" in implement_pr_step["instructions"]
        assert "Done automatically" in implement_pr_step["instructions"]
        assert "artifacts/jira-implement-pr.json" in implement_pr_step["instructions"]
        assert "FULLY_IMPLEMENTED" in implement_pr_step["instructions"]
        implement_finalize_step = expanded_steps[-1]
        assert implement_finalize_step["title"] == "Finalize Jira status"
        assert implement_finalize_step["skill"]["id"] == "jira-issue-updater"
        assert "Done" in implement_finalize_step["instructions"]
        assert "status Review" in implement_finalize_step["instructions"]
        assert "pull_request_url" in implement_finalize_step["instructions"]
        assert "artifacts/jira-implement-verify.json" in (
            implement_finalize_step["instructions"]
        )
        assert (
            "FULLY_IMPLEMENTED" in implement_finalize_step["instructions"]
        )

        expanded_without_verify = await PresetCatalogService(session).expand_template(
            slug="jira-implement",
            scope="global",
            scope_ref=None,
            inputs={
                "jira_issue": {"key": "MM-999"},
                "constraints": "",
                "run_verify": False,
            },
            context={"repository": "MoonLadderStudios/MoonMind"},
        )
        no_verify_titles = [
            step["title"] for step in expanded_without_verify["steps"]
        ]
        no_verify_skill_ids = [
            (step.get("skill") or step.get("tool"))["id"]
            for step in expanded_without_verify["steps"]
        ]
        assert "Verify implementation" not in no_verify_titles
        assert "Verify remediation attempt 6 of 6" not in no_verify_titles
        assert "Remediate verification gaps — attempt 1 of 6" not in no_verify_titles
        assert "moonspec-verify" not in no_verify_skill_ids
        assert "Implement the issue" in no_verify_titles
        assert "Create pull request" in no_verify_titles
        no_verify_pr_step = next(
            step
            for step in expanded_without_verify["steps"]
            if step["title"] == "Create pull request"
        )
        assert "Verification was disabled" in no_verify_pr_step["instructions"]
        assert "moonspec-verify verdict is FULLY_IMPLEMENTED in" not in (
            no_verify_pr_step["instructions"]
        )
        no_verify_finalize_step = expanded_without_verify["steps"][-1]
        assert no_verify_finalize_step["title"] == "Finalize Jira status"
        assert "Verification was disabled" in no_verify_finalize_step["instructions"]
        assert "verifier result" not in no_verify_finalize_step["instructions"]

        result = await session.execute(
            select(Preset)
            .where(
                Preset.slug == "jira-breakdown-orchestrate",
                Preset.scope_type == PresetScopeType.GLOBAL,
                Preset.scope_ref.is_(None),
            )
            
        )
        composite_template = result.scalar_one_or_none()
        assert composite_template is not None
        assert composite_template.title == "Breakdown and Jira Orchestrate"
        assert "jira_board_id" not in {
            item["name"] for item in composite_template.inputs_schema
        }
        assert "run_verify" in {
            item["name"] for item in composite_template.inputs_schema
        }
        assert [
            (step.get("skill") or step.get("tool"))["id"]
            for step in composite_template.steps
        ] == [
            "jira.load_preset_brief",
            "moonspec-breakdown",
            "story-reconcile-implementation",
            "story.create_jira_issues",
            "story.create_jira_orchestrate_tasks",
        ]
        reconcile_step = composite_template.steps[2]
        assert "fully implemented stories" in reconcile_step["instructions"]
        composite_jira_step = composite_template.steps[3]
        assert "Selected Jira board" not in composite_jira_step["instructions"]
        assert "boardId" not in composite_jira_step["storyOutput"]["jira"]
        downstream_step = composite_template.steps[4]
        assert "trusted Jira story output" in downstream_step["instructions"]
        assert "dependsOn" in downstream_step["instructions"]
        assert "orchestrationMode" not in downstream_step["jiraOrchestration"]["task"]
        assert downstream_step["jiraOrchestration"]["task"]["publish"] == {
            "mode": "pr",
            "mergeAutomation": {
                "enabled": "{{ inputs.publish_mode == 'pr_with_merge_automation' }}"
            },
        }
        assert downstream_step["jiraOrchestration"]["task"]["inputs"] == {
            "run_verify": "{{ inputs.run_verify }}"
        }

        result = await session.execute(
            select(Preset)
            .where(
                Preset.slug == "jira-breakdown-implement",
                Preset.scope_type == PresetScopeType.GLOBAL,
                Preset.scope_ref.is_(None),
            )
            
        )
        implement_composite_template = result.scalar_one_or_none()
        assert implement_composite_template is not None
        assert implement_composite_template.title == "Breakdown and Jira Implement"
        assert "jira_board_id" not in {
            item["name"] for item in implement_composite_template.inputs_schema
        }
        assert "run_verify" in {
            item["name"] for item in implement_composite_template.inputs_schema
        }
        assert [
            (step.get("skill") or step.get("tool"))["id"]
            for step in implement_composite_template.steps
        ] == [
            "jira.load_preset_brief",
            "moonspec-breakdown",
            "story-reconcile-implementation",
            "story.create_jira_issues",
            "story.create_jira_implement_tasks",
        ]
        implement_downstream_step = (
            implement_composite_template.steps[4]
        )
        implement_jira_step = implement_composite_template.steps[3]
        assert "Selected Jira board ID" not in implement_jira_step["instructions"]
        assert "boardId" not in implement_jira_step["storyOutput"]["jira"]
        assert (
            "Create one Jira Implement workflow execution"
            in implement_downstream_step["instructions"]
        )
        assert "dependsOn" in implement_downstream_step["instructions"]
        assert implement_downstream_step["jiraOrchestration"]["task"]["publish"] == {
            "mode": "pr",
            "mergeAutomation": {
                "enabled": "{{ inputs.publish_mode == 'pr_with_merge_automation' }}"
            },
        }
        assert implement_downstream_step["jiraOrchestration"]["task"]["inputs"] == {
            "run_verify": "{{ inputs.run_verify }}"
        }

        for slug, title, downstream_skill in (
            (
                "github-issue-breakdown-implement",
                "Breakdown and GitHub Issue Implement",
                "story.create_github_issue_implement_workflows",
            ),
            (
                "github-issue-breakdown-orchestrate",
                "Breakdown and GitHub Issue Orchestrate",
                "story.create_github_issue_orchestrate_workflows",
            ),
        ):
            result = await session.execute(
                select(Preset)
                .where(
                    Preset.slug == slug,
                    Preset.scope_type == PresetScopeType.GLOBAL,
                    Preset.scope_ref.is_(None),
                )
            )
            github_breakdown_template = result.scalar_one_or_none()
            assert github_breakdown_template is not None
            assert github_breakdown_template.title == title
            assert "run_verify" in {
                item["name"] for item in github_breakdown_template.inputs_schema
            }
            assert [
                (step.get("skill") or step.get("tool"))["id"]
                for step in github_breakdown_template.steps
            ] == [
                "moonspec-breakdown",
                "story-reconcile-implementation",
                "story.create_github_issues",
                downstream_skill,
            ]
            github_create_step = github_breakdown_template.steps[2]
            assert github_create_step["storyOutput"]["github"] == {
                "repository": "{{ inputs.github_repository }}",
                "sourceIssueKey": "{{ inputs.source_issue_key }}",
            }
            github_downstream_step = github_breakdown_template.steps[3]
            assert "workflow execution" in github_downstream_step["instructions"]
            assert "Create workflow dependencies with dependsOn" in (
                github_downstream_step["instructions"]
            )
            assert github_downstream_step["githubOrchestration"]["task"]["publish"] == {
                "mode": "{{ inputs.publish_mode }}",
            }
            assert github_downstream_step["githubOrchestration"]["task"]["inputs"] == {
                "run_verify": "{{ inputs.run_verify }}"
            }
            assert github_downstream_step["githubOrchestration"]["traceability"] == {
                "sourceIssueKey": "{{ inputs.source_issue_key }}"
            }

        result = await session.execute(
            select(Preset)
            .where(
                Preset.slug == "github-issue-orchestrate",
                Preset.scope_type == PresetScopeType.GLOBAL,
                Preset.scope_ref.is_(None),
            )
        )
        github_orchestrate_template = result.scalar_one_or_none()
        assert github_orchestrate_template is not None
        final_status_step = github_orchestrate_template.steps[-1]
        assert final_status_step["tool"]["id"] == "github.update_issue_status"
        assert final_status_step["tool"]["inputs"]["verificationArtifactPath"] == (
            "var/artifacts/moonspec-verify/github-issue-orchestrate.json"
        )
        assert final_status_step["tool"]["inputs"]["requireVerification"] == (
            "{{ inputs.run_verify }}"
        )

        result = await session.execute(
            select(Preset)
            .where(
                Preset.slug == "document-update-orchestrate",
                Preset.scope_type == PresetScopeType.GLOBAL,
                Preset.scope_ref.is_(None),
            )
            
        )
        doc_template = result.scalar_one_or_none()
        assert doc_template is not None
        doc_steps = doc_template.steps
        assert len(doc_steps) == 2
        assert doc_steps[0]["type"] == "tool"
        assert doc_steps[0]["tool"]["id"] == "document.discover"
        assert doc_steps[0]["tool"]["inputs"]["repository"] == "{{ context.repository }}"
        assert "{{ inputs.document_directory }}" in doc_steps[0]["instructions"]
        assert doc_steps[1]["type"] == "tool"
        assert doc_steps[1]["tool"]["id"] == "story.create_document_update_tasks"
        assert "documentUpdateOrchestration" in doc_steps[1]
        assert doc_steps[1]["documentUpdateOrchestration"]["task"]["publish"] == {
            "mode": "pr",
            "mergeAutomation": {
                "enabled": "{{ inputs.publish_mode == 'pr_with_merge_automation' }}"
            },
        }
        assert doc_steps[1]["documentUpdateOrchestration"]["traceability"][
            "sourceDirectory"
        ] == "{{ inputs.document_directory }}"

        result = await session.execute(
            select(Preset)
            .where(
                Preset.slug == "document-author",
                Preset.scope_type == PresetScopeType.GLOBAL,
                Preset.scope_ref.is_(None),
            )

        )
        doc_author_template = result.scalar_one_or_none()
        assert doc_author_template is not None
        assert doc_author_template.annotations["sourceIssueKey"] == "MM-931"
        assert doc_author_template.annotations["sourceReference"] == "MM-927"
        assert len(doc_author_template.steps) == 1
        assert doc_author_template.steps[0]["type"] == "skill"
        assert doc_author_template.steps[0]["skill"]["id"] == "document-author"
        assert "Do not create spec.md" in doc_author_template.steps[0]["instructions"]
        assert "docs/tmp/" in doc_author_template.steps[0]["instructions"]

        result = await session.execute(
            select(Preset)
            .where(
                Preset.slug == "batch-workflows",
                Preset.scope_type == PresetScopeType.GLOBAL,
                Preset.scope_ref.is_(None),
            )
            
        )
        batch_template = result.scalar_one_or_none()
        assert batch_template is not None
        assert batch_template.title == "Batch Jira Workflows"
        batch_annotations = batch_template.annotations or {}
        assert batch_annotations["runtimeInheritance"] == "caller"
        assert batch_annotations["workflowPublish"] == {"mode": "none"}
        batch_schema = batch_annotations["inputSchema"]
        assert batch_schema["required"] == [
            "jira_project_key",
            "jira_status",
            "run_ref",
        ]
        assert batch_schema["properties"]["run_ref"]["enum"] == [
            "skill:jira-verify",
            "preset:jira-implement",
            "preset:jira-orchestrate",
        ]
        assert batch_schema["properties"]["run_verify"]["default"] is True
        assert "source_kind" not in batch_schema["properties"]
        assert "target_preset_slug" not in batch_schema["properties"]
        assert "target_preset_version" not in batch_schema["properties"]
        assert batch_annotations["uiSchema"]["run_ref"]["widget"] == "select"
        assert batch_annotations["uiSchema"]["constraints"]["widget"] == "textarea"
        assert batch_annotations["uiSchema"]["run_verify"]["widget"] == "checkbox"
        assert batch_annotations["bindings"]["skill:jira-verify"][
            "jira_issue_key"
        ] == "{{ target.jiraIssue.key }}"
        assert batch_annotations["bindings"]["preset:jira-implement"][
            "jira_issue_key"
        ] == "{{ target.jiraIssue.key }}"
        assert batch_annotations["bindings"]["preset:jira-implement"][
            "run_verify"
        ] == "{{ shared.run_verify }}"
        assert batch_annotations["bindings"]["preset:jira-orchestrate"][
            "jira_issue_key"
        ] == "{{ target.jiraIssue.key }}"
        assert batch_annotations["bindings"]["preset:jira-orchestrate"][
            "run_verify"
        ] == "{{ shared.run_verify }}"
        assert all("github" not in key for key in batch_annotations["bindings"])
        batch_steps = batch_template.steps
        assert len(batch_steps) == 1
        assert batch_steps[0]["skill"]["id"] == "batch-workflows"
        assert "publish" not in batch_steps[0]
        assert batch_steps[0]["batchOrchestration"]["runtime"]["inherit"] == "caller"
        assert batch_steps[0]["batchOrchestration"]["source"]["kind"] == "jira_status"
        assert (
            batch_steps[0]["batchOrchestration"]["publish"]["mode"]
            == "{{ inputs.publish_mode }}"
        )
        assert (
            batch_steps[0]["batchOrchestration"]["target"]["runRef"]
            == "{{ inputs.run_ref }}"
        )
        assert (
            batch_steps[0]["batchOrchestration"]["sharedInputs"]["run_verify"]
            == "{{ inputs.run_verify }}"
        )

@pytest.mark.asyncio
async def test_startup_deactivates_legacy_speckit_orchestrate_template(
    disabled_env_keys, tmp_path
):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with db_base.async_session_maker() as session:
        legacy_template = Preset(
            slug="speckit-orchestrate",
            scope_type=PresetScopeType.GLOBAL,
            scope_ref=None,
            title="SpecKit Orchestrate",
            description="Legacy preset",
            tags=[],
            required_capabilities=[],
            is_active=True,
        )
        session.add(legacy_template)
        await session.commit()

    with (
        patch("api_service.main._initialize_embedding_model"),
        patch("api_service.main._initialize_vector_store"),
        patch("api_service.main._initialize_contexts"),
        patch("api_service.main._load_or_create_vector_index"),
        patch("api_service.main._initialize_oidc_provider"),
    ):
        await startup_event()

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(Preset).where(
                Preset.slug == "speckit-orchestrate",
                Preset.scope_type == PresetScopeType.GLOBAL,
                Preset.scope_ref.is_(None),
            )
        )
        template = result.scalar_one_or_none()
        assert template is not None
        assert template.is_active is False
