"""Group a pack's requirement results and scan findings by a reporting lens.

A lens is a pack mapping target marked ``reportingLens``, such as the OWASP Top
10:2025 categories. Its mappings are directional and partial: grouping never
adds coverage, and no category is reported as passing.
"""

from __future__ import annotations

from collections import Counter

from moonmind.security.framework_packs import FrameworkMapping, MappingTarget

LENS_STATEMENT = (
    "Reporting lens only: mappings are directional and partial, grouping grants "
    "no coverage, and no category is reported as passing."
)
MAX_LISTED_FINDINGS = 32


def _finding(row, mapping_ids=()) -> dict:
    value = {
        "findingId": row.finding_id,
        "source": row.source,
        "tool": row.tool,
        "ruleId": row.rule_id,
        "asset": row.asset,
    }
    if mapping_ids:
        value["mappingIds"] = list(mapping_ids)
    if row.priority is not None:
        value["priority"] = row.priority.model_dump(mode="json", by_alias=True)
    return value


def _attribution(mapping: FrameworkMapping) -> dict:
    value = {
        "id": mapping.id,
        "basis": mapping.basis,
        "relation": mapping.relation,
        "source": mapping.source.model_dump(mode="json", by_alias=True),
    }
    if mapping.basis == "evaluated":
        value["reviewedBy"] = mapping.reviewed_by
        value["reviewedOn"] = mapping.reviewed_on.isoformat()
    return value


def reporting_lenses(pack: dict, results, findings) -> list[dict]:
    """Lens records for one pack's retained metadata.

    ``results`` are control requirement results of the whole assessment and
    ``findings`` its current native findings. Only redistributable metadata
    carries mappings, so a restricted pack has no lens.
    """
    targets = [
        target
        for target in map(MappingTarget.model_validate, pack.get("mappingTargets", []))
        if target.reporting_lens
    ]
    if not targets:
        return []
    mappings = [FrameworkMapping.model_validate(m) for m in pack.get("mappings", [])]
    own = {
        row.requirement.id: row
        for row in results
        if (row.requirement.framework_id, row.requirement.framework_edition)
        == (pack["id"], pack["edition"])
    }
    lenses = []
    for target in targets:
        rows = [m for m in mappings if m.target_id == target.id]
        grouped = set()
        categories = []
        for category in target.requirement_ids:
            related = [m for m in rows if category in m.to_requirement_ids]
            requirements = [
                {
                    "id": key,
                    "conclusion": own[key].conclusion,
                    "coverage": own[key].coverage,
                }
                for key in dict.fromkeys(
                    key for m in related for key in m.from_requirement_ids
                )
                if key in own
            ]
            matched = []
            for row in findings:
                ids = [
                    m.id
                    for m in related
                    if any(item.matches(row) for item in m.from_findings)
                ]
                if ids:
                    matched.append(_finding(row, ids))
                    grouped.add(row.finding_id)
            assessed = [r for r in requirements if r["conclusion"] != "not_assessed"]
            status = (
                "unmapped"
                if not related
                else "assessed" if assessed else "not_assessed"
            )
            categories.append(
                {
                    "id": category,
                    "title": target.titles.get(category, category),
                    "status": status,
                    "conclusionCounts": dict(
                        sorted(Counter(r["conclusion"] for r in assessed).items())
                    ),
                    "requirements": requirements,
                    "findingCount": len(matched),
                    "findings": matched[:MAX_LISTED_FINDINGS],
                    "mappings": [_attribution(m) for m in related],
                }
            )
        unmapped = [_finding(row) for row in findings if row.finding_id not in grouped]
        lenses.append(
            {
                "targetId": target.id,
                "frameworkId": target.framework_id,
                "edition": target.edition,
                "statement": LENS_STATEMENT,
                "categories": categories,
                "unmappedFindingCount": len(unmapped),
                "unmappedFindings": unmapped[:MAX_LISTED_FINDINGS],
            }
        )
    return lenses


def _mapping_text(mapping: dict, clean) -> str:
    source = mapping["source"]
    where = clean(source.get("section") or source.get("url") or "")
    if mapping["basis"] == "upstream":
        return f"{clean(mapping['id'])} (upstream, {mapping['relation']}; {where})"
    return (
        f"{clean(mapping['id'])} (evaluated, {mapping['relation']}; reviewed by "
        f"{clean(mapping['reviewedBy'])} on {mapping['reviewedOn']}; {where})"
    )


def render_reporting_lens(lens: dict, clean) -> list[str]:
    lines = [
        "",
        f"#### Reporting lens: {clean(lens['frameworkId'])} {clean(lens['edition'])}",
        clean(lens["statement"]),
    ]
    for category in lens["categories"]:
        name = f"{clean(category['id'])} {clean(category['title'])}"
        if category["status"] == "unmapped":
            lines.append(
                f"- {name}: no mapping in this pack; not assessed, not a pass."
            )
            continue
        if category["status"] == "assessed":
            counts = ", ".join(
                f"{key} {value}" for key, value in category["conclusionCounts"].items()
            )
            requirements = ", ".join(
                f"{clean(r['id'])} ({r['conclusion']})"
                for r in category["requirements"]
                if r["conclusion"] != "not_assessed"
            )
            state = f"assessed requirements: {counts} — {requirements}"
        else:
            state = "no assessed requirement maps here; not a pass"
        unassessed = [
            clean(r["id"])
            for r in category["requirements"]
            if r["conclusion"] == "not_assessed"
        ]
        if unassessed:
            state += f"; not assessed: {', '.join(unassessed)}"
        findings = category["findingCount"]
        listed = ", ".join(
            f"{clean(f['ruleId'])} on {clean(f['asset'])}"
            for f in category["findings"][:8]
        )
        lines.append(
            f"- {name}: {state}. Findings: {findings}"
            + (f" ({listed}{', …' if findings > 8 else ''})" if listed else "")
            + ". Mappings: "
            + "; ".join(_mapping_text(m, clean) for m in category["mappings"])
            + "."
        )
    if lens["unmappedFindingCount"]:
        listed = ", ".join(
            f"{clean(f['source'])}/{clean(f['tool'])} {clean(f['ruleId'])}"
            for f in lens["unmappedFindings"][:8]
        )
        lines.append(
            f"- Findings with no mapped category: {lens['unmappedFindingCount']} ({listed})."
        )
    return lines
