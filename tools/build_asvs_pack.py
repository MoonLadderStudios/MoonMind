"""Build the installed OWASP ASVS 5.0.0 Level 1 pack from the pinned release file.

Offline and reproducible: the inputs are the vendored release file (checked
against its pinned SHA-256), MoonMind's classification in
tools/asvs_l1_classification.json and the security-review Skill's web/API
procedure catalog, which the pack references by procedure ID. Requirement IDs,
counts and text come only from the release file.

    python tools/build_asvs_pack.py          # write the pack and PROVENANCE.json
    python tools/build_asvs_pack.py --check  # fail if the installed files differ
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "moonmind/security/packs/owasp-asvs"
SOURCE = DATA / "OWASP_Application_Security_Verification_Standard_5.0.0_en.flat.json"
LICENSE = DATA / "LICENSE.md"
OUTPUT = DATA / "owasp-asvs-l1.json"
PROVENANCE = DATA / "PROVENANCE.json"
CLASSIFICATION = ROOT / "tools/asvs_l1_classification.json"
PROCEDURES = ROOT / ".agents/skills/security-review/references/web-api.json"
LEVEL = "1"
PACK_ID = "owasp-asvs-l1"
REVIEW_METHODS = {"security-review", "security-investigate"}
REPRODUCTION_METHODS = {"zap", "application-access"}


class BuildError(ValueError):
    pass


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _procedure_catalog(raw: bytes, review: dict) -> dict[str, dict]:
    """The web/API procedures by ID, plus the pack's requirement review."""
    catalog = {}
    for requirement in json.loads(raw)["requirements"]:
        for procedure in requirement["procedures"]:
            if procedure["id"] in catalog:
                raise BuildError(f"duplicate procedure {procedure['id']}")
            catalog[procedure["id"]] = procedure
    if review["id"] in catalog:
        raise BuildError("the requirement review shadows a web/API procedure")
    return {**catalog, review["id"]: review}


def _requirement(row: dict, entry: dict, plan: dict, procedures: dict) -> dict:
    req_id = row["req_id"]
    kinds = [k for k in ("procedures", "documentation", "notAssessed") if k in entry]
    if len(kinds) != 1:
        raise BuildError(f"{req_id} needs exactly one evidence class, got {kinds}")
    tag = plan["upstream"]
    value = {
        "id": req_id,
        "title": row["section_name"],
        "summary": row["req_description"],
        "category": f"{row['chapter_id']} {row['chapter_name']}",
        "applicability": entry.get("applicability", plan["defaultApplicability"]),
        "sources": [
            {
                "url": f"{tag['repository']}/tree/{tag['tag']}",
                "section": f"ASVS 5.0.0 {req_id} ({row['section_id']} {row['section_name']})",
                "reviewedOn": plan["reviewedOn"],
            }
        ],
    }
    if "notAssessed" in entry:
        return {
            **value,
            "evidencePrompt": f"MoonMind cannot evidence {req_id}; assess it by other means and retain that result.",
            "objectiveBasis": f"Not assessable here: {entry['notAssessed']}",
            "notAssessedReason": entry["notAssessed"],
        }
    if "documentation" in entry:
        return {
            **value,
            "evidenceTypes": ["organisational"],
            "requiredEvidenceMethods": ["evidence"],
            "evidencePrompt": f"Supply the current approved documentation {req_id} asks for: {entry['documentation']}",
            "objectiveBasis": f"Documentation requirement: {req_id} is evidenced by the organisation's own records, which a reviewer accepts; no MoonMind check applies.",
            "organisationalEvidence": [
                {
                    "id": f"{req_id}.documentation",
                    "category": "policy",
                    "purpose": "design",
                    "description": entry["documentation"],
                }
            ],
        }
    selected = []
    for key in entry["procedures"]:
        if key not in procedures:
            raise BuildError(f"{req_id} names unknown procedure {key}")
        selected.append(procedures[key])
    methods = []
    if entry.get("objectives"):
        methods.append("deterministic_check")
    if any(p["method"] in REVIEW_METHODS for p in selected):
        methods.append("evidence")
    if any(p["method"] in REPRODUCTION_METHODS for p in selected):
        methods.append("observation")
    reviewed = ", ".join(p["id"] for p in selected)
    return {
        **value,
        "requiredEvidenceMethods": methods,
        "evidencePrompt": f"Retain the evidence of {reviewed} for {req_id}: examined paths, interfaces, identities and what stayed unexamined.",
        "objectives": entry.get("objectives", []),
        "objectivesComplete": False,
        "objectiveBasis": entry.get(
            "objectiveBasis",
            f"No installed MoonMind rule checks {req_id}; the listed procedures gather evidence for a reviewer's conclusion.",
        ),
        "procedures": selected,
    }


def _mapping(row: dict, plan: dict) -> dict:
    top10 = plan["top10"]
    page = top10["pages"][row["to"]][0]
    value = {
        "id": row["id"],
        **(
            {"fromRequirementIds": row["from"]}
            if "from" in row
            else {"fromFindings": row["findings"]}
        ),
        "targetId": "owasp-top10-2025",
        "toRequirementIds": [row["to"]],
        "relation": "partial",
        "rationale": row["rationale"],
        "source": {
            "url": f"{top10['repository']}/blob/{top10['commit']}/2025/docs/en/{page}",
            "section": row["section"],
            "reviewedOn": plan["reviewedOn"],
        },
        "basis": row["basis"],
    }
    if row["basis"] == "evaluated":
        value.update(reviewedBy=plan["reviewedBy"], reviewedOn=plan["reviewedOn"])
    return value


def build(
    source: bytes, classification: bytes, procedures: bytes
) -> tuple[bytes, dict]:
    """The pack bytes and their provenance record, from the three inputs only."""
    plan = json.loads(classification)
    upstream = plan["upstream"]
    if _sha256(source) != upstream["sha256"]:
        raise BuildError("the release file differs from the pinned SHA-256")
    rows = json.loads(source)["requirements"]
    level = [row for row in rows if row["L"] == LEVEL]
    ids = [row["req_id"] for row in level]
    if len(ids) != len(set(ids)):
        raise BuildError("duplicate upstream requirement identity")
    if set(ids) != set(plan["requirements"]):
        missing = sorted(set(ids) - set(plan["requirements"]))
        extra = sorted(set(plan["requirements"]) - set(ids))
        raise BuildError(
            f"classification differs from Level 1: missing {missing}, extra {extra}"
        )
    catalog = _procedure_catalog(procedures, plan["reviewProcedure"])
    requirements = [
        _requirement(row, plan["requirements"][row["req_id"]], plan, catalog)
        for row in level
    ]
    counts = {
        "deterministic": sum(bool(r.get("objectives")) for r in requirements),
        "review": sum(bool(r.get("procedures")) for r in requirements),
        "documentation": sum("organisationalEvidence" in r for r in requirements),
        "not_assessed": sum("notAssessedReason" in r for r in requirements),
    }
    top10 = plan["top10"]
    mapped = {row["to"] for row in plan["mappings"]}
    unmapped = [key for key in top10["pages"] if key not in mapped]
    pack = {
        "schemaVersion": "declared-framework-pack/v1",
        "id": PACK_ID,
        "edition": "5.0.0",
        "revision": "1",
        "title": "OWASP ASVS 5.0.0 Level 1",
        "source": {
            "url": f"{upstream['repository']}/tree/{upstream['tag']}",
            "section": f"{upstream['path']}: the Level {LEVEL} rows",
            "reviewedOn": plan["reviewedOn"],
        },
        "reviewedBy": plan["reviewedBy"],
        "reviewedOn": plan["reviewedOn"],
        "publicationStatus": "published",
        "publishedOn": upstream["publishedOn"],
        "effectiveFrom": upstream["publishedOn"],
        "provenance": (
            f"Generated offline by tools/build_asvs_pack.py from OWASP/ASVS tag {upstream['tag']} "
            f"(commit {upstream['commit']}), {upstream['path']}, SHA-256 {upstream['sha256']}: "
            f"the {len(level)} Level {LEVEL} rows of {len(rows)}, with upstream identifiers and text unchanged, "
            "annotated with MoonMind's evidence classification and OWASP Top 10:2025 mappings. "
            "A later edition or level is a new pack, never an in-place edit."
        ),
        "license": (
            "CC-BY-SA-4.0. An adaptation of the OWASP Application Security Verification Standard 5.0.0, "
            "distributed with its license and notice in moonmind/security/packs/owasp-asvs, apart from "
            "MoonMind's Apache-2.0 source."
        ),
        "attribution": (
            "Requirement text: OWASP Application Security Verification Standard 5.0.0, "
            f"{upstream['repository']}/tree/{upstream['tag']}, and category names: OWASP Top 10:2025, "
            "both by the OWASP Foundation under CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/). "
            "MoonMind selected the Level 1 rows and added evidence classification and mappings; "
            "the adaptation is licensed CC BY-SA 4.0. OWASP does not endorse MoonMind or certify conformance."
        ),
        "distribution": "redistributable",
        "conclusions": (
            "Per-requirement OWASP ASVS 5.0.0 Level 1 conclusions from scoped evidence. A clean scan satisfies "
            "no requirement that needs review; MoonMind makes no ASVS conformance claim."
        ),
        "limitations": [
            f"Level {LEVEL} only: {len(level)} of the release's {len(rows)} requirements. Levels 2 and 3 can be "
            "built from the same file but are not offered until their evidence paths are reviewed.",
            f"Coverage is mostly not deterministic. Of {len(level)} requirements: {counts['deterministic']} "
            f"partial installed-rule check plus review; {counts['review'] - counts['deterministic']} agent review "
            f"or reproduction only; {counts['documentation']} organisational documentation; "
            f"{counts['not_assessed']} not assessable here. No requirement declares complete objectives, so a "
            "clean scan satisfies none.",
            "Web and API procedures are the security-review Skill's web-api-methodology catalog, referenced by "
            "ID; a retained methodology plan and results report their coverage as tested, partially tested, "
            "blocked or untested.",
            "Known-vulnerability (with KEV and EPSS priority where feed data exists), secret, misconfiguration, "
            "dynamic-code and TLS findings are grouped by OWASP Top 10:2025 category. That lens is directional "
            "and partial, grants no coverage and never reports a category as passing.",
            f"{', '.join(unmapped) or 'No category'} cite ASVS V16, which has no Level {LEVEL} requirement, so this "
            "pack maps nothing to them.",
        ],
        "requirements": requirements,
        "mappingTargets": [
            {
                "id": "owasp-top10-2025",
                "frameworkId": "owasp-top-10",
                "edition": top10["edition"],
                "requirementIds": list(top10["pages"]),
                "titles": {key: page[1] for key, page in top10["pages"].items()},
                "reportingLens": True,
            }
        ],
        "mappings": [_mapping(row, plan) for row in plan["mappings"]],
    }
    payload = (json.dumps(pack, indent=2, ensure_ascii=False) + "\n").encode()
    # The installed loader is the contract; refuse output it would refuse.
    sys.path.insert(0, str(ROOT))
    from moonmind.security.framework_packs import parse_declared_pack

    parse_declared_pack(payload)
    provenance = {
        "schemaVersion": "asvs-pack-provenance/v1",
        "builder": "tools/build_asvs_pack.py",
        "upstream": {
            "repository": upstream["repository"],
            "tag": upstream["tag"],
            "commit": upstream["commit"],
            "path": upstream["path"],
            "file": _rel(SOURCE),
            "sha256": upstream["sha256"],
            "license": "CC-BY-SA-4.0",
            "licenseFile": _rel(LICENSE),
            "requirementCount": len(rows),
        },
        "level": int(LEVEL),
        "requirementCount": len(level),
        "inputs": {
            _rel(CLASSIFICATION): _sha256(classification),
            _rel(PROCEDURES): _sha256(procedures),
        },
        "output": {"file": _rel(OUTPUT), "sha256": _sha256(payload)},
    }
    return payload, provenance


def _provenance_bytes(provenance: dict) -> bytes:
    return (json.dumps(provenance, indent=2) + "\n").encode()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    payload, provenance = build(
        SOURCE.read_bytes(), CLASSIFICATION.read_bytes(), PROCEDURES.read_bytes()
    )
    record = _provenance_bytes(provenance)
    if args.check:
        stale = [
            _rel(path)
            for path, expected in ((OUTPUT, payload), (PROVENANCE, record))
            if not path.exists() or path.read_bytes() != expected
        ]
        if stale:
            print(
                "Rebuild differs from installed files: " + ", ".join(stale),
                file=sys.stderr,
            )
            return 1
        print(f"{_rel(OUTPUT)} reproduces sha256 {provenance['output']['sha256']}")
        return 0
    OUTPUT.write_bytes(payload)
    PROVENANCE.write_bytes(record)
    print(f"wrote {_rel(OUTPUT)} sha256 {provenance['output']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
