from moonmind.workflows.skills.native_binding import pr_resolver_native_binding


def _entry(source="built_in", **overrides):
    value = {
        "skillName": "pr-resolver",
        "contentDigest": "sha256:abc",
        "provenance": {"sourceKind": source},
        "implementation": {
            "contract": "pr-resolver-core/v1",
            "coreVersion": "1",
            "supportedHosts": ["cli", "temporal"],
            "nativeHostAllowed": True,
        },
    }
    value.update(overrides)
    return value


def test_trusted_resolved_contract_enables_native_host():
    assert pr_resolver_native_binding(_entry()).eligible


def test_name_alone_never_enables_native_host():
    decision = pr_resolver_native_binding({"skillName": "pr-resolver"})
    assert not decision.eligible
    assert decision.reason_code == "immutable_content_evidence_missing"


def test_repo_override_requires_its_own_explicit_compatible_contract():
    assert not pr_resolver_native_binding(
        _entry(source="repo", implementation=None)
    ).eligible
    assert pr_resolver_native_binding(_entry(source="repo")).eligible
