"""MoonLadderStudios/MoonMind#3712 migration inventory classification tests."""

from __future__ import annotations

from moonmind.omnigent.session_migration_inventory import (
    InventoryClass,
    RecordInventoryView,
    build_inventory_report,
    classify_record,
)


def _view(record_id: str = "b-1", **overrides: object) -> RecordInventoryView:
    base: dict[str, object] = {
        "record_id": record_id,
        "immutable_evidence_complete": True,
    }
    base.update(overrides)
    return RecordInventoryView(**base)


def test_new_model_ready() -> None:
    view = _view(has_canonical_session=True)
    assert classify_record(view) is InventoryClass.NEW_MODEL_READY


def test_legacy_active_session_kept_on_legacy_owner() -> None:
    # Even with provable authority, an active session is never canonicalized.
    view = _view(is_active=True, authority_provable=True)
    assert classify_record(view) is InventoryClass.LEGACY_ACTIVE


def test_terminal_provable_is_canonicalizable() -> None:
    view = _view(is_terminal=True, authority_provable=True)
    assert classify_record(view) is InventoryClass.CANONICALIZABLE


def test_terminal_unprovable_is_readable() -> None:
    view = _view(is_terminal=True, authority_provable=False)
    assert classify_record(view) is InventoryClass.LEGACY_TERMINAL_READABLE


def test_alias_required() -> None:
    view = _view(is_terminal=True, requires_chat_alias=True)
    assert classify_record(view) is InventoryClass.ALIAS_REQUIRED


def test_cleanup_required() -> None:
    view = _view(is_terminal=True, cleanup_pending=True)
    assert classify_record(view) is InventoryClass.CLEANUP_REQUIRED


def test_conflicting_authority_quarantined_not_recency() -> None:
    view = _view(is_terminal=True, authority_provable=True, conflicting_authority=True)
    assert classify_record(view) is InventoryClass.AMBIGUOUS_AUTHORITY


def test_duplicate_group_without_provable_authority_is_ambiguous() -> None:
    view = _view(is_terminal=True, duplicate_group=True, authority_provable=False)
    assert classify_record(view) is InventoryClass.AMBIGUOUS_AUTHORITY


def test_duplicate_group_with_provable_authority_can_canonicalize() -> None:
    view = _view(is_terminal=True, duplicate_group=True, authority_provable=True)
    assert classify_record(view) is InventoryClass.CANONICALIZABLE


def test_corrupt_or_incomplete_evidence_is_unsupported() -> None:
    assert classify_record(_view(corrupt=True)) is (
        InventoryClass.UNSUPPORTED_OR_CORRUPT
    )
    bad = RecordInventoryView(record_id="b-x", immutable_evidence_complete=False)
    assert classify_record(bad) is InventoryClass.UNSUPPORTED_OR_CORRUPT


def test_duplicate_seven_binding_group_all_quarantined() -> None:
    views = [
        _view(f"dup-{i}", is_terminal=True, duplicate_group=True, conflicting_authority=True)
        for i in range(7)
    ]
    report = build_inventory_report(views)
    assert report.total == 7
    assert report.count_for(InventoryClass.AMBIGUOUS_AUTHORITY) == 7
    assert report.quarantined == 7


def test_report_is_bounded_and_safe() -> None:
    views = [
        _view(f"t-{i}", is_terminal=True, authority_provable=True, diagnostic_ref=f"art://d/{i}")
        for i in range(50)
    ]
    report = build_inventory_report(views, sample_limit=5)
    entry = next(
        e for e in report.counts if e.inventory_class is InventoryClass.CANONICALIZABLE
    )
    assert entry.count == 50
    # Diagnostic-ref sample is bounded regardless of input size.
    assert len(entry.diagnostic_refs) == 5
    # Only operator-safe refs appear; the view has no provider-session field at
    # all, so a report can never carry one.
    assert "recordId" not in report.as_dict()
    assert all(ref.startswith("art://") for ref in entry.diagnostic_refs)


def test_view_forbids_unknown_sensitive_fields() -> None:
    # extra="forbid" means a caller cannot smuggle a raw provider session id or
    # credential into the safe view.
    import pydantic

    try:
        RecordInventoryView(record_id="b", provider_session_id="secret")  # type: ignore[call-arg]
    except pydantic.ValidationError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("sensitive extra field must be rejected")


def test_representative_mixed_database() -> None:
    views = [
        _view("n1", has_canonical_session=True),
        _view("a1", is_active=True),
        _view("t1", is_terminal=True, authority_provable=True),
        _view("t2", is_terminal=True),
        _view("al1", is_terminal=True, requires_chat_alias=True),
        _view("q1", is_terminal=True, conflicting_authority=True),
        _view("c1", is_terminal=True, cleanup_pending=True),
        RecordInventoryView(record_id="x1", immutable_evidence_complete=False),
    ]
    report = build_inventory_report(views)
    assert report.total == 8
    assert report.count_for(InventoryClass.NEW_MODEL_READY) == 1
    assert report.count_for(InventoryClass.LEGACY_ACTIVE) == 1
    assert report.count_for(InventoryClass.CANONICALIZABLE) == 1
    assert report.count_for(InventoryClass.LEGACY_TERMINAL_READABLE) == 1
    assert report.count_for(InventoryClass.ALIAS_REQUIRED) == 1
    assert report.count_for(InventoryClass.AMBIGUOUS_AUTHORITY) == 1
    assert report.count_for(InventoryClass.CLEANUP_REQUIRED) == 1
    assert report.count_for(InventoryClass.UNSUPPORTED_OR_CORRUPT) == 1
