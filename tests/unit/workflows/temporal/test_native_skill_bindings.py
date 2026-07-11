from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    SkillImplementationContract,
)
from moonmind.workflows.temporal.native_skill_bindings import (
    evaluate_pr_resolver_native_binding,
)


def _entry(source: AgentSkillSourceKind) -> ResolvedSkillEntry:
    return ResolvedSkillEntry(
        skill_name="pr-resolver",
        content_ref="art_skill",
        content_digest="sha256:abc",
        provenance=AgentSkillProvenance(source_kind=source),
        implementation=SkillImplementationContract(
            contract="pr-resolver-core/v1",
            supportedHosts=["cli", "temporal"],
            nativeHostEligible=True,
            nativeHostPolicy="moonmind_trusted",
        ),
    )


def test_trusted_built_in_pr_resolver_is_native_eligible() -> None:
    result = evaluate_pr_resolver_native_binding(_entry(AgentSkillSourceKind.BUILT_IN))
    assert result.eligible is True
    assert result.host == "temporal"


def test_repo_or_local_name_collision_uses_observable_portable_fallback() -> None:
    for source in (AgentSkillSourceKind.REPO, AgentSkillSourceKind.LOCAL):
        result = evaluate_pr_resolver_native_binding(_entry(source))
        assert result.eligible is False
        assert result.host == "cli"
        assert result.reason_code == "untrusted_skill_source"


def test_missing_immutable_content_evidence_rejects_native_host() -> None:
    entry = _entry(AgentSkillSourceKind.BUILT_IN).model_copy(
        update={"content_digest": None}
    )
    result = evaluate_pr_resolver_native_binding(entry)
    assert result.eligible is False
    assert result.reason_code == "immutable_content_evidence_missing"
