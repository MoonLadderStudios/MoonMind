"""Saved-work materialization for publication (MoonLadderStudios/MoonMind#4018).

A real managed capture produces the saved-work manifest, snapshot archive, and
recorded delta. Materialization reads only those artifacts, after the original
workspace is removed, and fails closed when any object differs from the
digest chain the manifest committed.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from moonmind.config.settings import settings
from moonmind.publish.saved_candidate import SavedPublicationError
from moonmind.publish.saved_work_source import (
    materialize_saved_work,
    saved_work_artifact_id,
)
from tests.support.saved_work_capture import CapturedSavedWork, capture_saved_work


@pytest.fixture(autouse=True)
def _default_security(monkeypatch):
    monkeypatch.setattr(settings.security, "high_security_mode", False)


def _mutate(repo: Path) -> None:
    (repo / "src" / "app.py").write_text("print('saved')\n")
    (repo / "gone.txt").unlink()
    (repo / "run.sh").chmod(0o755)
    (repo / "notes" / "new.md").parent.mkdir(parents=True, exist_ok=True)
    (repo / "notes" / "new.md").write_text("added by the run\n")
    (repo / "latest").symlink_to("notes/new.md")
    (repo / ".env").write_text("TOKEN=never-exported\n")


async def _captured(tmp_path: Path) -> CapturedSavedWork:
    return await capture_saved_work(
        tmp_path,
        {
            "src/app.py": "print('base')\n",
            "gone.txt": "removed by the run\n",
            "run.sh": "#!/bin/sh\necho run\n",
        },
        _mutate,
    )


@pytest.mark.asyncio
async def test_materializes_verified_snapshot_without_the_source(
    tmp_path: Path,
) -> None:
    saved = await _captured(tmp_path)
    saved.remove_source()
    root = tmp_path / "publication" / "content"

    content = await materialize_saved_work(
        read=saved.read,
        saved_work_ref=saved.saved_work_ref,
        saved_work_digest=saved.saved_work_digest,
        root=root,
    )

    entries = {entry.path: entry for entry in content.entries}
    assert set(entries) == {"src/app.py", "run.sh", "notes/new.md", "latest"}
    assert entries["run.sh"].executable is True
    assert entries["src/app.py"].executable is False
    assert entries["latest"].kind == "symlink"
    assert os.readlink(root / "latest") == "notes/new.md"
    assert (root / "src" / "app.py").read_text() == "print('saved')\n"
    assert content.digest == saved.saved_work_digest
    assert content.baseline_commit == saved.baseline_commit
    assert content.recorded_deletions == ("gone.txt",)
    assert ".env" in content.excluded_paths
    assert not (root / ".env").exists()
    manifest = saved.manifest()
    snapshot = next(o for o in manifest["outputs"] if o["format"] == "full_snapshot")
    # Only the committed saved-work objects are read; nothing else is looked up.
    assert saved.reads == [
        saved.saved_work_ref,
        snapshot["ref"],
        manifest["git"]["deltaRef"],
    ]


@pytest.mark.parametrize("tampered", ["manifest", "snapshot", "delta"])
@pytest.mark.asyncio
async def test_changed_saved_object_fails_closed(tmp_path: Path, tampered: str) -> None:
    saved = await _captured(tmp_path)
    manifest = saved.manifest()
    ref = {
        "manifest": saved.saved_work_ref,
        "snapshot": next(
            o for o in manifest["outputs"] if o["format"] == "full_snapshot"
        )["ref"],
        "delta": manifest["git"]["deltaRef"],
    }[tampered]
    payload, content_type = saved.objects[ref]
    saved.objects[ref] = (payload + b"\n", content_type)

    with pytest.raises(SavedPublicationError) as exc:
        await materialize_saved_work(
            read=saved.read,
            saved_work_ref=saved.saved_work_ref,
            saved_work_digest=saved.saved_work_digest,
            root=tmp_path / "content",
        )

    assert exc.value.code == "PUBLICATION_CONTENT_MISMATCH"


def _rewrite_manifest(saved: CapturedSavedWork, **changes: object) -> str:
    manifest = {**saved.manifest(), **changes}
    payload = json.dumps(manifest, sort_keys=True).encode()
    ref = "artifact://rewritten-manifest"
    saved.objects[ref] = (payload, saved.objects[saved.saved_work_ref][1])
    saved.saved_work_ref = ref
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
async def test_materialization_keeps_recorded_delta_outside_output_summary(
    tmp_path: Path,
) -> None:
    saved = await _captured(tmp_path)
    manifest = saved.manifest()
    assert "exact_baseline_delta" not in manifest["requiredFormats"]
    digest = _rewrite_manifest(
        saved,
        outputs=[
            output
            for output in manifest["outputs"]
            if output["format"] != "exact_baseline_delta"
        ],
    )
    saved.remove_source()

    content = await materialize_saved_work(
        read=saved.read,
        saved_work_ref=saved.saved_work_ref,
        saved_work_digest=digest,
        root=tmp_path / "publication" / "content",
    )

    assert content.recorded_deletions == ("gone.txt",)
    assert manifest["git"]["deltaRef"] in saved.reads


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"scan": {"disposition": "blocked"}}, "PUBLICATION_CONTENT_UNSAFE"),
        (
            {"schemaVersion": "saved-work-manifest/v0"},
            "PUBLICATION_SAVED_WORK_UNSUPPORTED",
        ),
        ({"outputs": []}, "PUBLICATION_SAVED_WORK_UNSUPPORTED"),
        ({"fileManifestDigest": "sha256:" + "0" * 64}, "PUBLICATION_CONTENT_MISMATCH"),
    ],
)
@pytest.mark.asyncio
async def test_unsafe_or_unsupported_saved_work_is_not_published(
    tmp_path: Path, changes: dict, code: str
) -> None:
    saved = await _captured(tmp_path)
    digest = _rewrite_manifest(saved, **changes)

    with pytest.raises(SavedPublicationError) as exc:
        await materialize_saved_work(
            read=saved.read,
            saved_work_ref=saved.saved_work_ref,
            saved_work_digest=digest,
            root=tmp_path / "content",
        )

    assert exc.value.code == code


@pytest.mark.asyncio
async def test_materialization_requires_a_fresh_owned_directory(tmp_path: Path) -> None:
    saved = await _captured(tmp_path)
    root = tmp_path / "content"
    root.mkdir()
    (root / "leftover").write_text("not ours\n")

    with pytest.raises(SavedPublicationError) as exc:
        await materialize_saved_work(
            read=saved.read,
            saved_work_ref=saved.saved_work_ref,
            saved_work_digest=saved.saved_work_digest,
            root=root,
        )

    assert exc.value.code == "PUBLICATION_WORKSPACE_NOT_FRESH"
    assert (root / "leftover").read_text() == "not ours\n"


@pytest.mark.parametrize(
    ("ref", "artifact_id"),
    [("artifact://art_1", "art_1"), ("artifact:art_2", "art_2"), ("art_3", "art_3")],
)
def test_saved_work_artifact_id_accepts_compact_refs(
    ref: str, artifact_id: str
) -> None:
    assert saved_work_artifact_id(ref) == artifact_id
