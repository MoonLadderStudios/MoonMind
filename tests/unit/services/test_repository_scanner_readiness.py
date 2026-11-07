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
