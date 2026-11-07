"""Read-only deployment configuration, never a live scanner health claim."""

from moonmind.config.container_backend_settings import ContainerBackendSettings
from moonmind.security.scanner_feeds import (
    FEED_CACHE_TARGET,
    FEED_HOSTS,
    feed_policy_admits,
)


def repository_scanner_readiness(settings: ContainerBackendSettings) -> dict:
    """Project configured defaults without pulling images or inspecting volumes.

    The API does not run on the container worker. Image contents, feed freshness,
    and network reachability remain unverified until execution evidence exists.
    """
    source_ref = "repository-security"
    image = next(
        (s for s in settings.image_sources if s.source_ref == source_ref), None
    )
    caches = {s.cache_ref: s for s in settings.cache_sources}
    reader, writer = caches.get("scanner-db"), caches.get("scanner-feeds")
    cache_configured = bool(
        reader
        and writer
        and reader.read_only
        and not writer.read_only
        and reader.target == "/opt/moonmind-scanner-db"
        and writer.target == FEED_CACHE_TARGET
        and reader.volume_name == writer.volume_name
    )
    policies = [
        {
            "policyRef": policy.policy_ref,
            "workflowIds": list(policy.allowed_workflow_ids),
        }
        for policy in settings.egress_policies
        if source_ref in policy.allowed_image_source_refs
        and feed_policy_admits(policy)
        and FEED_HOSTS <= {d.host for d in policy.destinations}
    ]
    diagnostics = []
    if not settings.enabled or not settings.endpoint:
        diagnostics.append(
            {
                "code": "container_backend_unavailable",
                "action": "Enable the container backend and configure its worker endpoint.",
            }
        )
    if image is None:
        diagnostics.append(
            {
                "code": "missing_image_source",
                "action": "Configure the repository-security image source in the deployment container backend settings.",
            }
        )
    if not cache_configured:
        diagnostics.append(
            {
                "code": "feed_cache_unavailable",
                "action": "Configure scanner-db read-only at /opt/moonmind-scanner-db and scanner-feeds writable at /opt/moonmind-scanner-feeds on the same named volume.",
            }
        )
    if not policies:
        diagnostics.append(
            {
                "code": "refresh_egress_unavailable",
                "action": "Authorize a feed-only HTTPS egress policy for repository-security and an explicit refresh workflow, covering ghcr.io and pkg-containers.githubusercontent.com. Offline feed import remains available.",
            }
        )
    return {
        "status": "unverified",
        "imageSourceRef": source_ref,
        "sourceImage": image.image if image else None,
        "scanners": ["gitleaks", "opengrep", "trivy"],
        "ruleSelection": "image-default",
        "ruleCoverage": "Bundled default: Python eval/shell rules and JavaScript/TypeScript eval rules. Custom images may differ; execution records the installed rules.",
        "databaseCacheRef": "scanner-db",
        "refreshCacheRef": "scanner-feeds",
        "feedCacheConfigured": cache_configured,
        "feedSnapshot": "unverified",
        "refreshPolicies": policies,
        "diagnostics": diagnostics,
        "verificationNote": "Configuration only. Image contents, installed rules, feed snapshot freshness and refresh connectivity are Unverified until actual execution evidence is available. No scan or refresh is started here.",
    }
