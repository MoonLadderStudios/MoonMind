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


def test_moonspec_verify_skill_treats_publication_as_downstream_candidate_work() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "moonspec-verify"
        / "SKILL.md"
    )

    text = skill_path.read_text(encoding="utf-8")

    assert "pre-publication verification step" in text
    assert "open issue" in text
    assert "unmerged candidate" in text
    assert "expected inputs" in text
    assert "Do not require downstream commit" in text


def test_moonspec_verify_skill_uses_repository_managed_test_boundary() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "moonspec-verify"
        / "SKILL.md"
    )

    text = skill_path.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "repository's managed-agent test boundary" in normalized
    assert "documented managed-run signal" in normalized
    assert "required delegated test command" in normalized
    assert "route every covered test through it" in normalized
    assert "Do not run an equivalent host-local test command" in normalized
    assert "install a test runner into the agent host" in normalized
    assert "results from a path the repository forbids" in normalized
    assert "documented delegated capability exists" in normalized


def test_moonspec_verify_skill_rebuilds_post_remediation_classifications() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "moonspec-verify"
        / "SKILL.md"
    )

    text = skill_path.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "verification follows a remediation attempt" in normalized
    assert (
        "rebuild every controlling classification from the current repository head"
        in normalized
    )
    assert "are hypotheses and process context only" in normalized
    assert "replace the old status, evidence, line numbers, and notes" in normalized
    assert "Never copy or incrementally edit a previous verifier JSON report" in normalized
    assert "no gap may remain solely because the old artifact said it existed" in normalized


def test_moonspec_verify_skill_defines_target_modes() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "moonspec-verify"
        / "SKILL.md"
    )

    text = skill_path.read_text(encoding="utf-8")

    assert "Treat the user's instructions as a valid verification baseline" in text
    assert "referenced declarative document as a valid verification baseline" in text
    assert "Do not treat ad hoc verifier guidance" in text
    assert "focus on API tests" in text
    assert "In source-direct verification mode" in text
    assert "Do not require a MoonSpec feature directory" in text
    assert "their absence is never by itself a verification gap" in text
    assert "--json --paths-only" in text
    assert "Derived-artifact discovery failure is not itself a verification failure" in text
    assert "ignore stale or unrelated acceptance artifacts" in text
    assert "--json --include-tasks" not in text
    assert "--require-tasks" not in text
    assert "If the user provides issue-brief verification inputs" in text
    assert "use issue-brief verification mode" in text
    assert "issue brief artifact path" in text
    assert "assessment artifact path" in text
    assert "without requiring a MoonSpec feature directory" in text
    assert "Use the issue summary, description, acceptance criteria" in text


def test_moonspec_verify_command_does_not_require_feature_artifacts() -> None:
    command_path = (
        Path(__file__).resolve().parents[3]
        / ".specify"
        / "templates"
        / "commands"
        / "moonspec.verify.md"
    )

    text = command_path.read_text(encoding="utf-8")

    assert "scripts:" not in text
    assert "original instructions or authoritative declarative source" in text
    assert ".specify/scripts/bash/check-prerequisites.sh --json --paths-only" in text
    assert "Do not require `spec.md`, `plan.md`, or `tasks.md`" in text
    assert "--require-tasks" not in text


def test_moonspec_verify_skill_defines_report_and_remaining_work_output() -> None:
    skill_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "moonspec-verify"
        / "SKILL.md"
    )

    text = skill_path.read_text(encoding="utf-8")

    assert "## Report" in text
    assert "Return a Markdown report in the response" in text
    assert "Do not write a file unless the user explicitly asks for one" in text
    assert "**Verdict**: FULLY_IMPLEMENTED | ADDITIONAL_WORK_NEEDED | NO_DETERMINATION | BLOCKED" in text
    assert "## Remaining Work" in text
    assert "Gap Type: implementation | verification | documentation | environment" in text
    assert "Recoverable In Current Runtime: true | false" in text


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
    assert "ADDITIONAL_WORK_NEEDED" in text
    assert "NO_DETERMINATION" in text
    assert "ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION" in text
    assert "Choose exactly one verdict" in text
    assert (
        "Use `ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION` only as a diagnostic value"
        not in text
    )
    assert "Implementation gaps mean required behavior" in text
    assert "Verification gaps mean tests" in text
    assert "include a structured Remaining Work section" in text
