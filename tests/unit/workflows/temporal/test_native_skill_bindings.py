from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    SkillImplementationContract,
)
from moonmind.workflows.temporal.native_skill_bindings import (
    evaluate_pr_resolver_native_binding,
    require_skill_owned_pr_resolver_execution,
)


def _entry(source: AgentSkillSourceKind) -> ResolvedSkillEntry:
    return ResolvedSkillEntry(
        skill_name="pr-resolver",
        content_ref="art_skill",
        content_digest="sha256:abc",
        provenance=AgentSkillProvenance(source_kind=source),
        implementation=SkillImplementationContract(
            contract="pr-resolver-core/v1",
            supportedHosts=["cli"],
            nativeHostEligible=False,
        ),
    )


def test_built_in_pr_resolver_is_not_native_eligible() -> None:
    result = evaluate_pr_resolver_native_binding(_entry(AgentSkillSourceKind.BUILT_IN))
    assert result.eligible is False
    assert result.host == "cli"
    assert result.reason_code == "temporal_host_not_supported"


def test_repo_or_local_pr_resolver_uses_skill_owned_execution() -> None:
    for source in (AgentSkillSourceKind.REPO, AgentSkillSourceKind.LOCAL):
        result = evaluate_pr_resolver_native_binding(_entry(source))
        assert result.eligible is False
        assert result.host == "cli"
        assert result.reason_code == "temporal_host_not_supported"


def test_missing_immutable_content_evidence_rejects_native_host() -> None:
    entry = _entry(AgentSkillSourceKind.BUILT_IN).model_copy(
        update={
            "content_digest": None,
            "implementation": SkillImplementationContract(
                contract="pr-resolver-core/v1",
                supportedHosts=["cli", "temporal"],
                nativeHostEligible=True,
                nativeHostPolicy="moonmind_trusted",
            ),
        }
    )
    result = evaluate_pr_resolver_native_binding(entry)
    assert result.eligible is False
    assert result.reason_code == "immutable_content_evidence_missing"


def test_skill_owned_cutover_rejects_even_legacy_native_eligible_content() -> None:
    entry = _entry(AgentSkillSourceKind.BUILT_IN).model_copy(
        update={
            "implementation": SkillImplementationContract(
                contract="pr-resolver-core/v1",
                supportedHosts=["cli", "temporal"],
                nativeHostEligible=True,
                nativeHostPolicy="moonmind_trusted",
            )
        }
    )

    result = require_skill_owned_pr_resolver_execution(entry)

    assert result.eligible is False
    assert result.host == "cli"
    assert result.reason_code == "skill_owned_execution_required"
    assert result.identity["contentDigest"] == "sha256:abc"
