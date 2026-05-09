"""Snapshot test for settings catalog drift detection.

Fails if any registry entry's key, type, scopes, or section changes without
updating the snapshot file intentionally.  Updating the snapshot is the
explicit developer action that replaces a migration entry for test purposes.

MM-652 / specs/330-backend-settings-catalog-registry
"""

import json
from pathlib import Path

from api_service.services.settings_catalog import SettingsCatalogService

_SNAPSHOT_PATH = (
    Path(__file__).parent / "snapshots" / "settings_catalog_snapshot.json"
)


def _build_catalog_shape() -> dict:
    service = SettingsCatalogService(env={})
    catalog = service.catalog()
    return {
        entry.key: {
            "type": entry.type,
            "scopes": sorted(entry.scopes),
            "section": entry.section,
        }
        for category_entries in catalog.categories.values()
        for entry in category_entries
    }


def test_catalog_snapshot_no_drift():
    """Catalog keys, types, scopes, and sections must match the committed snapshot.

    To update the snapshot after an intentional catalog change:
        python - <<'EOF'
        import json
        from tests.unit.services.test_settings_catalog_snapshot import _build_catalog_shape
        from pathlib import Path
        p = Path("tests/unit/services/snapshots/settings_catalog_snapshot.json")
        p.write_text(json.dumps(_build_catalog_shape(), indent=2, sort_keys=True))
        print("snapshot updated")
        EOF
    """
    actual = _build_catalog_shape()
    expected = json.loads(_SNAPSHOT_PATH.read_text())
    added = sorted(set(actual) - set(expected))
    removed = sorted(set(expected) - set(actual))
    changed = sorted(
        k for k in actual if k in expected and actual[k] != expected[k]
    )
    drift_lines = []
    if added:
        drift_lines.append(f"  added keys: {added}")
    if removed:
        drift_lines.append(f"  removed keys: {removed}")
    for k in changed:
        drift_lines.append(f"  changed {k!r}: {expected[k]} -> {actual[k]}")
    assert not drift_lines, (
        "Catalog drift detected — update the snapshot file intentionally if this "
        "change is expected:\n" + "\n".join(drift_lines)
    )
