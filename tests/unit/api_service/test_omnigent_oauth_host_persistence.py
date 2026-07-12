"""Database contract tests for profile-bound Omnigent OAuth hosts."""

from api_service.db.models import (
    OmnigentOAuthHostBindingRecord,
    OmnigentOAuthHostLeaseRecord,
)


def test_binding_enforces_one_durable_host_configuration_per_profile() -> None:
    table = OmnigentOAuthHostBindingRecord.__table__
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("provider_profile_id",) in unique_columns
    assert table.c.credential_mount_template_json.nullable is False


def test_host_lease_enforces_one_active_record_per_profile_and_provider_lease() -> None:
    table = OmnigentOAuthHostLeaseRecord.__table__
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("provider_profile_id",) not in unique_columns
    assert ("provider_lease_id",) in unique_columns
    assert ("idempotency_key",) in unique_columns
    assert table.c.credential_generation.nullable is False
    assert any(
        index.name == "ix_omnigent_oauth_host_lease_expiry"
        and tuple(column.name for column in index.columns) == ("expires_at",)
        for index in table.indexes
    )
    assert any(
        index.name == "ux_omnigent_oauth_host_lease_active_profile"
        and index.unique
        for index in table.indexes
    )
