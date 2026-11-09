from dataclasses import replace

from api_service.services.repository_scanner_readiness import (
    repository_scanner_readiness,
)
from moonmind.config.container_backend_settings import (
    CacheSource,
    RegistryImageSource,
    resolve_container_backend_settings,
)
from moonmind.security.egress_policy import ScopedEgressPolicy


def configured():
    policy = ScopedEgressPolicy.model_validate(
        {
            "policyRef": "feed-downloads",
            "allowedWorkflowIds": ["refresh-scanners"],
            "allowedImageSourceRefs": ["repository-security"],
            "destinations": [
                {
                    "host": host,
                    "protocol": "https",
                    "port": 443,
                    "purpose": "registry-api",
                }
                for host in ("ghcr.io", "pkg-containers.githubusercontent.com")
            ],
        }
    )
    return replace(
        resolve_container_backend_settings({}),
        enabled=True,
        image_sources=(
            RegistryImageSource(
                "repository-security", "registry.example/scanner:custom"
            ),
        ),
        cache_sources=(
            CacheSource("scanner-db", "feeds", "/opt/moonmind-scanner-db", True),
            CacheSource("scanner-feeds", "feeds", "/opt/moonmind-scanner-feeds", False),
        ),
        egress_policies=(policy,),
    )


def test_configured_is_unverified_and_preserves_actual_image():
    result = repository_scanner_readiness(configured())
    assert result["status"] == "unverified"
    assert result["sourceImage"] == "registry.example/scanner:custom"
    assert result["diagnostics"] == []
    assert result["feedSnapshot"] == "unverified"
    assert result["ruleSelection"] == "image-default"
    assert result["refreshPolicies"] == [
        {"policyRef": "feed-downloads", "workflowIds": ["refresh-scanners"]}
    ]


def test_missing_setup_has_actionable_diagnostics():
    result = repository_scanner_readiness(
        replace(configured(), image_sources=(), cache_sources=(), egress_policies=())
    )
    assert {d["code"] for d in result["diagnostics"]} == {
        "missing_image_source",
        "feed_cache_unavailable",
        "refresh_egress_unavailable",
    }
    assert all(d["action"] for d in result["diagnostics"])


def test_cache_aliases_must_share_volume_and_correct_permissions():
    settings = configured()
    for bad in (
        replace(settings.cache_sources[0], read_only=False),
        replace(settings.cache_sources[0], volume_name="other"),
        replace(settings.cache_sources[0], target="/wrong"),
    ):
        result = repository_scanner_readiness(
            replace(settings, cache_sources=(bad, settings.cache_sources[1]))
        )
        assert "feed_cache_unavailable" in [d["code"] for d in result["diagnostics"]]


def test_egress_must_cover_feed_hosts_and_image_not_broad_internet():
    settings = configured()
    policy = settings.egress_policies[0]
    for bad in (
        policy.model_copy(update={"allowed_image_source_refs": ("other",)}),
        policy.model_copy(update={"destinations": policy.destinations[:1]}),
        policy.model_copy(
            update={
                "destinations": (
                    *policy.destinations,
                    policy.destinations[0].model_copy(update={"host": "example.com"}),
                )
            }
        ),
    ):
        result = repository_scanner_readiness(replace(settings, egress_policies=(bad,)))
        assert "refresh_egress_unavailable" in [
            d["code"] for d in result["diagnostics"]
        ]


def test_disabled_backend_is_not_ready():
    result = repository_scanner_readiness(replace(configured(), enabled=False))
    assert "container_backend_unavailable" in [d["code"] for d in result["diagnostics"]]


def test_selected_gitleaks_does_not_require_optional_feeds_or_credentials():
    from api_service.services.repository_scanner_readiness import (
        assessment_input_readiness,
    )

    result = assessment_input_readiness(
        replace(configured(), cache_sources=(), egress_policies=()),
        "repository-scan",
        {"scanner": "gitleaks", "outputDirectory": "scan"},
    )
    assert result["status"] == "unverified"
    assert result["diagnostics"] == []
    assert result["runtimeChecks"] == {
        "scannerImage": "unverified",
        "capturedInput": "unverified",
    }


def test_selected_trivy_requires_only_reader_and_keeps_freshness_unverified():
    from api_service.services.repository_scanner_readiness import (
        assessment_input_readiness,
    )

    result = assessment_input_readiness(
        replace(
            configured(),
            cache_sources=configured().cache_sources[:1],
            egress_policies=(),
        ),
        "repository-scan",
        {"scanner": "trivy", "outputDirectory": "scan"},
    )
    assert result["diagnostics"] == []
    assert result["runtimeChecks"]["feedFreshness"] == "unverified"
    missing = assessment_input_readiness(
        replace(configured(), cache_sources=()),
        "repository-scan",
        {"scanner": "trivy", "outputDirectory": "scan"},
    )
    assert missing["status"] == "blocked"
    assert missing["diagnostics"][0]["field"] == "assessment.databaseCacheRef"


def test_selected_opengrep_keeps_image_default_rules_unverified():
    from api_service.services.repository_scanner_readiness import (
        assessment_input_readiness,
    )

    result = assessment_input_readiness(
        configured(),
        "repository-scan",
        {
            "scanner": "opengrep",
            "outputDirectory": "scan",
        },
    )
    assert result["diagnostics"] == []
    assert result["runtimeChecks"]["licensedRules"] == "unverified"


def test_invalid_selected_inputs_never_echo_user_values():
    from api_service.services.repository_scanner_readiness import (
        assessment_input_readiness,
    )

    result = assessment_input_readiness(
        configured(),
        "cloud-assessment",
        {
            "provider": "secret-private-provider",
            "credentialRef": "secret-private-key",
        },
    )
    assert result["status"] == "blocked"
    assert result["diagnostics"][0]["code"] == "invalid_assessment_input"
    assert "secret-private" not in str(result)


def test_selected_web_anonymous_mode_has_no_credential_requirement():
    from api_service.services.repository_scanner_readiness import (
        assessment_input_readiness,
    )

    settings = configured()
    settings = replace(
        settings,
        image_sources=(
            RegistryImageSource("web-assessment", "registry.example/zap:custom"),
        ),
        egress_policies=(
            settings.egress_policies[0].model_copy(
                update={
                    "policy_ref": "web-target",
                    "allowed_image_source_refs": ("web-assessment",),
                }
            ),
        ),
    )
    result = assessment_input_readiness(
        settings,
        "web-assessment",
        {
            "mode": "baseline",
            "origin": "https://app.example.test",
            "routes": [{"path": "/", "methods": ["GET"]}],
            "egressPolicyRef": "web-target",
        },
    )
    assert result["status"] == "unverified"
    assert result["diagnostics"] == []
    assert result["runtimeChecks"] == {
        "scannerImage": "unverified",
        "targetAccess": "unverified",
    }


def test_cloud_account_and_credentials_are_never_inferred_ready():
    from api_service.services.repository_scanner_readiness import (
        assessment_input_readiness,
    )

    value = {
        "provider": "aws",
        "assessmentRef": "fixture",
        "account": "123456789012",
        "credentialRef": "secret://fixture",
        "regions": ["us-east-1"],
        "checks": ["s3_bucket_public_access"],
        "egressPolicyRef": "aws-apis",
    }
    result = assessment_input_readiness(configured(), "cloud-assessment", value)
    assert result["status"] == "blocked"
    assert {item["code"] for item in result["diagnostics"]} == {
        "missing_image_source",
        "egress_policy_unavailable",
    }
    assert result["runtimeChecks"]["accountCredential"] == "unverified"
    assert "secret://fixture" not in str(result)
    del value["account"]
    missing = assessment_input_readiness(configured(), "cloud-assessment", value)
    assert missing["diagnostics"][0]["field"] == "assessment.account"


def test_sbom_generation_does_not_require_vulnerability_feed():
    from api_service.services.repository_scanner_readiness import (
        assessment_input_readiness,
    )

    result = assessment_input_readiness(
        replace(configured(), cache_sources=()),
        "image-assessment",
        {
            "operation": "sbom-generate",
            "target": "source",
        },
    )
    assert result["status"] == "unverified"
    assert result["diagnostics"] == []
    assert "feedFreshness" not in result["runtimeChecks"]


def test_step_readiness_revalidates_exact_skill_selection_and_blocks_missing_config():
    import copy

    import pytest

    from api_service.services.repository_scanner_readiness import (
        require_assessment_step_readiness,
    )

    selection = {"scanner": "gitleaks", "outputDirectory": "scan"}
    workflow = {
        "steps": [
            {
                "skill": {"id": "security-review", "args": {"assessment": selection}},
                "annotations": {
                    "securityAssessment": {
                        "kind": "repository-scan",
                        "admission": selection,
                        "phase": "collect",
                    }
                },
            }
        ]
    }
    require_assessment_step_readiness(workflow, settings=configured())
    changed = copy.deepcopy(workflow)
    changed["steps"][0]["skill"]["args"]["assessment"]["scanner"] = "opengrep"
    # deepcopy preserves aliases; replace admission to independently differ.
    changed["steps"][0]["annotations"]["securityAssessment"]["admission"] = dict(
        selection
    )
    with pytest.raises(ValueError, match="steps.0.assessment"):
        require_assessment_step_readiness(changed, settings=configured())
    with pytest.raises(ValueError, match="missing_image_source"):
        require_assessment_step_readiness(
            workflow, settings=replace(configured(), image_sources=())
        )


def test_live_readiness_needs_scope_and_native_authorization():
    import pytest

    from api_service.services.repository_scanner_readiness import (
        require_assessment_step_readiness,
    )

    settings = configured()
    settings = replace(
        settings,
        image_sources=(
            RegistryImageSource("web-assessment", "registry.example/zap:custom"),
        ),
        egress_policies=(
            settings.egress_policies[0].model_copy(
                update={
                    "policy_ref": "web-target",
                    "allowed_image_source_refs": ("web-assessment",),
                }
            ),
        ),
    )
    selection = {
        "mode": "baseline",
        "assessmentRef": "web-1",
        "origin": "https://app.example.test",
        "routes": [{"path": "/", "methods": ["GET"]}],
        "egressPolicyRef": "web-target",
    }
    workflow = {
        "steps": [
            {
                "skill": {"id": "security-review", "args": {"assessment": selection}},
                "annotations": {
                    "securityAssessment": {
                        "kind": "web-assessment",
                        "admission": selection,
                        "phase": "collect",
                    }
                },
            }
        ]
    }
    with pytest.raises(ValueError, match="Review and admit"):
        require_assessment_step_readiness(workflow, settings=settings)
    scope = {
        "assessmentRef": "web-1",
        "profileRefs": ["web-assessment"],
        "actionClasses": ["web-passive"],
        "targets": [
            {"webOrigin": selection["origin"], "webRoutes": selection["routes"]}
        ],
        "networkRefs": ["web-target"],
        "maxJobs": 1,
        "maxJobSeconds": 3600,
        "evidenceIntent": "fixture",
    }
    assert (
        require_assessment_step_readiness(
            workflow, settings=settings, assessment_scope=scope
        )[0]["status"]
        == "unverified"
    )
    scope["networkRefs"] = []
    with pytest.raises(ValueError, match="credential grants"):
        require_assessment_step_readiness(
            workflow, settings=settings, assessment_scope=scope
        )


def test_repository_readiness_does_not_borrow_live_scope():
    import pytest

    from api_service.services.repository_scanner_readiness import (
        require_assessment_step_readiness,
    )

    selection = {"scanner": "gitleaks", "outputDirectory": "scan"}
    workflow = {
        "steps": [
            {
                "skill": {"id": "security-review", "args": {"assessment": selection}},
                "annotations": {
                    "securityAssessment": {
                        "kind": "repository-scan",
                        "admission": selection,
                        "phase": "collect",
                    }
                },
            }
        ]
    }
    with pytest.raises(ValueError, match="separate unscoped workflow"):
        require_assessment_step_readiness(
            workflow,
            settings=configured(),
            assessment_scope={"assessmentRef": "fixture"},
        )
