from pathlib import Path


def test_moonspec_verify_skill_includes_projection_contamination_preflight() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "moonspec-verify"
        / "SKILL.md"
    )

    text = skill_path.read_text(encoding="utf-8")

    assert "## Workspace Projection Preflight" in text
    assert "test ! -L .agents/skills" in text
    assert "test ! -L .gemini/skills" in text
    assert "git status --porcelain -- .agents/skills .gemini/skills skills_active" in text
    assert "ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION" in text
    assert "verdict `BLOCKED`" in text
    assert "`recoverableInCurrentRuntime: false`" in text
    assert "`recommendedNextAction: blocked`" in text
    assert "stop with verdict `ADDITIONAL_WORK_NEEDED`" not in text


def test_moonspec_verify_skill_reports_canonical_claim_coverage() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "moonspec-verify"
        / "SKILL.md"
    )

    text = skill_path.read_text(encoding="utf-8")

    assert "## Canonical Claim Coverage" in text
    assert "Code Evidence" in text
    assert "Test Evidence" in text
    assert "Artifact Evidence" in text
    assert "Implementation Status" in text
    assert "Verification Status" in text
    assert "Drift Status" in text
    assert "Gap Reason" in text
    assert "Implementation gaps mean" in text
    assert "Verification gaps mean" in text
    assert "Doc drift means" in text
    assert "Doc drift alone does not block `FULLY_IMPLEMENTED`" in text
    assert "Use durable evidence references" in text
    assert "structured drift in Source Document Drift" in text


def test_moonspec_verify_skill_supports_issue_brief_mode() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "moonspec-verify"
        / "SKILL.md"
    )

    text = skill_path.read_text(encoding="utf-8")

    assert "issue-brief verification mode" in text
    assert "without requiring a MoonSpec feature directory" in text
    assert "issue brief artifact path" in text
    assert "assessment artifact path" in text
    assert "PARTIALLY_IMPLEMENTED" in text
    assert "unmet and partially-met requirements" in text
    assert "Do not require `spec.md`, `plan.md`, `tasks.md`" in text
    assert "Gap Type: implementation | verification | documentation | environment" in text
    assert "Recoverable In Current Runtime: true | false" in text


def test_moonspec_verify_skill_defines_target_modes() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "moonspec-verify"
        / "SKILL.md"
    )

    text = skill_path.read_text(encoding="utf-8")

    assert (
        "Supported target modes are `moonspec_feature`, `issue_brief`, and `auto`"
        in text
    )
    assert "`target_mode`, `targetMode`, `verification_target`" in text
    assert "auto -> [resolved mode]" in text
    assert "require an issue brief artifact path" in text
    assert "Treat MoonSpec feature files as optional context" in text
    assert "issue title, issue body or description, acceptance criteria, labels" in text


def test_moonspec_verify_skill_defines_structured_artifact_output() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "moonspec-verify"
        / "SKILL.md"
    )

    text = skill_path.read_text(encoding="utf-8")

    assert "## Structured Verification Artifact" in text
    assert (
        "`verification_artifact_path`, `verificationArtifactPath`, "
        "`verify_artifact_path`, or `verifyArtifactPath`"
    ) in text
    assert '"schemaVersion": 1' in text
    assert '"targetMode": "moonspec_feature | issue_brief"' in text
    assert '"remainingWork": [' in text
    assert (
        '"gapType": "implementation | verification | documentation | environment"'
        in text
    )
    assert (
        '"recommendedNextAction": "advance | reattempt_current_step | blocked"'
        in text
    )
    assert "still return the Markdown report" in text


def test_moonspec_verify_skill_documents_workflow_consumed_verdicts() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "moonspec-verify"
        / "SKILL.md"
    )

    text = skill_path.read_text(encoding="utf-8")

    assert "BLOCKED" in text
    assert "FAILED_UNRECOVERABLE" in text
    assert "ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION" in text
    assert "Workflow consumers treat `FULLY_IMPLEMENTED` as passing" in text
    assert (
        "Use `ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION` only as a diagnostic value"
        in text
    )
    assert "Implementation gaps mean required behavior" in text
    assert "Verification gaps mean tests" in text
    assert "Emit concrete `remainingWork`" in text
