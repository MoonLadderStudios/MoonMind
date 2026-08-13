#!/usr/bin/env python3
"""Build deployment-local release evidence for MoonLadderStudios/MoonMind#3626."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.remediation_matrix_conformance import (  # noqa: E402
    build_remediation_release_evidence,
)


def _local_ref(ref: str, *, base: Path) -> Path:
    parsed = urlparse(ref)
    if parsed.scheme == "file" and parsed.netloc in {"", "localhost"}:
        return Path(unquote(parsed.path)).resolve()
    if parsed.scheme:
        raise ValueError(f"release evidence ref must be deployment-local: {ref}")
    return (base / ref).resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--artifact", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    release = json.loads(args.release.read_text(encoding="utf-8"))
    if not isinstance(release, dict):
        raise ValueError("release configuration must be an object")
    evidence_dir = args.output.parent / "remediation-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for index, artifact in enumerate(args.artifact):
        target = evidence_dir / f"{index:02d}-{artifact.name}"
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
            raise ValueError(f"invalid remediation evidence artifact: {artifact}")
        for row_index, row in enumerate(payload["rows"]):
            if not isinstance(row, dict):
                raise ValueError(f"invalid remediation evidence row: {artifact}")
            ref_rewrites: dict[str, str] = {}
            staged_records: list[tuple[dict[str, object], Path, bytes]] = []
            for record_index, record in enumerate(row.get("evidenceManifest", [])):
                if not isinstance(record, dict) or not isinstance(record.get("ref"), str):
                    raise ValueError(f"invalid source evidence record: {artifact}")
                old_ref = record["ref"]
                source = _local_ref(old_ref, base=artifact.resolve().parent)
                record_dir = evidence_dir / "source-records"
                record_dir.mkdir(parents=True, exist_ok=True)
                destination = record_dir / (
                    f"{index:02d}-{row_index:03d}-{record_index:02d}.json"
                )
                new_ref = str(destination.relative_to(evidence_dir))
                ref_rewrites[old_ref] = new_ref
                record["ref"] = new_ref
                staged_records.append((record, destination, source.read_bytes()))
            lineage = row.get("lineage")
            if not isinstance(lineage, dict):
                raise ValueError(f"invalid remediation evidence lineage: {artifact}")
            for field, value in list(lineage.items()):
                if isinstance(value, str) and value in ref_rewrites:
                    lineage[field] = ref_rewrites[value]
            for record, destination, raw in staged_records:
                source_payload = json.loads(raw)
                if not isinstance(source_payload, dict):
                    raise ValueError(f"invalid source evidence payload: {artifact}")
                source_lineage = source_payload.get("lineage")
                if isinstance(source_lineage, dict):
                    for field, value in list(source_lineage.items()):
                        if isinstance(value, str) and value in ref_rewrites:
                            source_lineage[field] = ref_rewrites[value]
                content = (
                    json.dumps(source_payload, indent=2, sort_keys=True) + "\n"
                ).encode()
                destination.write_bytes(content)
                record["sha256"] = hashlib.sha256(content).hexdigest()
                record["sizeBytes"] = len(content)
            secret_scan = row.get("secretScan")
            if isinstance(secret_scan, dict):
                for channel, scan in secret_scan.items():
                    if not isinstance(scan, dict) or not isinstance(
                        scan.get("evidenceRef"), str
                    ):
                        continue
                    source = _local_ref(
                        scan["evidenceRef"], base=artifact.resolve().parent
                    )
                    scan_dir = evidence_dir / "secret-scans"
                    scan_dir.mkdir(parents=True, exist_ok=True)
                    destination = scan_dir / f"{index:02d}-{row_index:03d}-{channel}.json"
                    destination.write_bytes(source.read_bytes())
                    scan["evidenceRef"] = str(destination.relative_to(evidence_dir))
        target.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        staged.append(target)
    document = build_remediation_release_evidence(
        release=release,
        artifact_paths=staged,
    )
    for item in document["evidenceManifest"]:
        item["ref"] = f"remediation-evidence/{Path(item['ref']).name}"
    document["evidenceRefs"] = [
        item["ref"] for item in document["evidenceManifest"]
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
