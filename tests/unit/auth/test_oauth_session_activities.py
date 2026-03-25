"""Tests for OAuth session Temporal activities and catalog registration."""

from __future__ import annotations

from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.workers import REGISTERED_TEMPORAL_WORKFLOW_TYPES


class TestOAuthSessionCatalogRegistration:
    """Verify OAuth session activities are registered in the catalog."""

    def test_ensure_volume_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.ensure_volume")
        assert route.activity_type == "oauth_session.ensure_volume"
        assert route.fleet == "artifacts"
        assert route.capability_class == "artifacts"

    def test_update_status_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.update_status")
        assert route.activity_type == "oauth_session.update_status"
        assert route.fleet == "artifacts"

    def test_mark_failed_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.mark_failed")
        assert route.activity_type == "oauth_session.mark_failed"
        assert route.fleet == "artifacts"

    def test_ensure_volume_timeouts(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.ensure_volume")
        assert route.timeouts.start_to_close_seconds == 60
        assert route.timeouts.schedule_to_close_seconds == 120

    def test_update_status_timeouts(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.update_status")
        assert route.timeouts.start_to_close_seconds == 15
        assert route.timeouts.schedule_to_close_seconds == 30

    def test_update_session_urls_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.update_session_urls")
        assert route.activity_type == "oauth_session.update_session_urls"
        assert route.fleet == "artifacts"
        assert route.timeouts.start_to_close_seconds == 15
        assert route.timeouts.schedule_to_close_seconds == 30

    def test_verify_volume_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.verify_volume")
        assert route.activity_type == "oauth_session.verify_volume"
        assert route.fleet == "artifacts"
        assert route.timeouts.start_to_close_seconds == 60
        assert route.timeouts.schedule_to_close_seconds == 120

    def test_register_profile_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.register_profile")
        assert route.activity_type == "oauth_session.register_profile"
        assert route.fleet == "artifacts"
        assert route.timeouts.start_to_close_seconds == 30
        assert route.timeouts.schedule_to_close_seconds == 60


class TestOAuthSessionWorkflowRegistration:
    """Verify the OAuth session workflow is registered."""

    def test_workflow_type_registered(self) -> None:
        assert "MoonMind.OAuthSession" in REGISTERED_TEMPORAL_WORKFLOW_TYPES

    def test_cleanup_stale_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.cleanup_stale")
        assert route.activity_type == "oauth_session.cleanup_stale"
        assert route.fleet == "artifacts"
        assert route.timeouts.start_to_close_seconds == 60
