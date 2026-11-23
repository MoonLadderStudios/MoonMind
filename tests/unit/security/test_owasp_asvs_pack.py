"""OWASP ASVS 5.0.0 Level 1 as general best practices for applications.

Covers the offline build from the pinned release, the CC BY-SA boundary, the
per-requirement evidence classification, the default-rule objective on real
Opengrep output, not-assessed conclusions and the Top 10:2025 reporting lens
(MoonLadderStudios/MoonMind#5434).
"""

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.schemas.repository_scanner_models import RepositoryScan, ScannerRule
from moonmind.security.assessments import (
    AssessmentScope,
    Comparison,
    Finding,
    compare_observations,
)
from moonmind.security.control_assessments import (
    assess_controls,
    render_control_assessment,
)
from moonmind.security.exploitation_data import VulnerabilityPriority
from moonmind.security.framework_packs import (
    FindingClass,
    FrameworkMapping,
    MappingTarget,
    PackRequirement,
    compile_framework_plan,
    evidence_plan,
    retained_pack_metadata,
)
from moonmind.security.framework_registry import (
    INSTALLED_PACK_IDS,
    installed_digest,
    installed_pack,
    readiness_catalog,
)
from moonmind.security.frameworks import EvidencePeriod
from moonmind.security.reporting_lens import render_reporting_lens, reporting_lenses
from moonmind.security.repository_scanners import (
    NativeResult,
    ScannerExecution,
    ScannerSummary,
    default_rule_ids,
    normalize_native,
)
from tools import build_asvs_pack

ROOT = Path(__file__).resolve().parents[3]
PACK_ID = "owasp-asvs-l1"
DATA = ROOT / "moonmind/security/packs/owasp-asvs"
FIXTURE = ROOT / "tests/fixtures/security_review/asvs/opengrep-eval.json"
PINNED_RELEASE = "8201b20eec2908c3380ac600c91c8ba746346fbb808859366abb232027532311"
PINNED_LICENSE = "28a9529c7d0bb4dc51f4bf5c116a3d16ef247a052f7591466768ddf563fd1cf5"
NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)
# Each deterministic objective names the vulnerable/repaired fixture that shows
# its rule failing and passing. A new objective without one fails this suite.
OBJECTIVE_FIXTURES = {("V1.3.2", "default-eval-rules"): FIXTURE}


def pack():
    return installed_pack(PACK_ID)[1]


def level_one_rows():
    rows = json.loads(build_asvs_pack.SOURCE.read_bytes())["requirements"]
    return [row for row in rows if row["L"] == "1"], rows


def build_inputs():
    return (
        build_asvs_pack.SOURCE.read_bytes(),
        build_asvs_pack.CLASSIFICATION.read_bytes(),
        build_asvs_pack.PROCEDURES.read_bytes(),
    )


def test_offline_rebuild_reproduces_the_installed_pack_and_recorded_digests():
    payload, provenance = build_asvs_pack.build(*build_inputs())
    assert payload == build_asvs_pack.OUTPUT.read_bytes()
    recorded = json.loads(build_asvs_pack.PROVENANCE.read_text())
    assert recorded == provenance
    assert recorded["output"]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert recorded["output"]["sha256"] == installed_digest(PACK_ID)
    upstream = recorded["upstream"]
    assert (upstream["tag"], upstream["commit"], upstream["sha256"]) == (
        "v5.0.0_release",
        "5cf9b032440be53ce345ab3c130fda46ba1ce7a2",
        PINNED_RELEASE,
    )
    assert hashlib.sha256(build_asvs_pack.SOURCE.read_bytes()).hexdigest() == (
        PINNED_RELEASE
    )
    # The referenced procedure catalog is a build input: changing it means a rebuild.
    assert (
        recorded["inputs"][".agents/skills/security-review/references/web-api.json"]
        == hashlib.sha256(build_asvs_pack.PROCEDURES.read_bytes()).hexdigest()
    )
    assert build_asvs_pack.main(["--check"]) == 0


def test_rebuild_refuses_a_changed_release_or_incomplete_classification():
    source, classification, procedures = build_inputs()
    changed = source.replace(b"eval()", b"evaluate()", 1)
    with pytest.raises(build_asvs_pack.BuildError, match="pinned SHA-256"):
        build_asvs_pack.build(changed, classification, procedures)
    plan = json.loads(classification)
    del plan["requirements"]["V6.2.1"]
    with pytest.raises(build_asvs_pack.BuildError, match=r"missing \['V6.2.1'\]"):
        build_asvs_pack.build(source, json.dumps(plan).encode(), procedures)
    plan = json.loads(classification)
    plan["requirements"]["V6.2.1"]["notAssessed"] = "Ambiguous classification."
    with pytest.raises(build_asvs_pack.BuildError, match="exactly one evidence class"):
        build_asvs_pack.build(source, json.dumps(plan).encode(), procedures)


def test_pack_lists_every_level_one_requirement_under_its_upstream_identifier():
    level, rows = level_one_rows()
    installed = pack()
    assert (installed.id, installed.edition, installed.revision) == (
        PACK_ID,
        "5.0.0",
        "1",
    )
    assert [row.id for row in installed.requirements] == [r["req_id"] for r in level]
    assert len(installed.requirements) == len(level) == 70
    by_id = {row.id: row for row in installed.requirements}
    for upstream in level:
        assert by_id[upstream["req_id"]].summary == upstream["req_description"]
    # Levels 2 and 3 are not offered: no other row, pack or catalog entry.
    others = {r["req_id"] for r in rows if r["L"] != "1"}
    assert not others & set(by_id)
    assert [key for key in INSTALLED_PACK_IDS if "asvs" in key] == [PACK_ID]
    assert installed.distribution == "redistributable"
    assert installed.publication_status == "published"


def test_requirement_text_is_cc_by_sa_data_apart_from_apache_source():
    license_text = (DATA / "LICENSE.md").read_bytes()
    assert hashlib.sha256(license_text).hexdigest() == PINNED_LICENSE
    assert license_text.startswith(b"Attribution-ShareAlike 4.0 International")
    notice = (DATA / "NOTICE").read_text()
    for phrase in (
        "Creative Commons Attribution-ShareAlike 4.0",
        "not part\nof MoonMind's Apache-2.0 source",
        "Share-alike",
        "OWASP Application Security Verification Standard (ASVS) 5.0.0",
        "OWASP Top 10:2025",
    ):
        assert phrase in notice
    # Only data, license and notice files: no Apache-2.0 code lives here.
    assert {path.suffix for path in DATA.iterdir()} <= {".json", ".md", ""}
    assert "moonmind/security/packs/owasp-asvs" in (ROOT / "NOTICE").read_text()
    installed = pack()
    assert installed.license.startswith("CC-BY-SA-4.0")
    assert "CC BY-SA 4.0" in installed.attribution
    assert "OWASP Application Security Verification Standard 5.0.0" in (
        installed.attribution
    )
    metadata = retained_pack_metadata(
        installed, artifact_ref="art_pack", sha256="a" * 64, now=NOW
    )
    # Portable metadata reproduces requirement text, so it carries attribution.
    assert metadata["requirements"][0]["summary"]
    assert metadata["attribution"] == installed.attribution


def test_every_requirement_declares_how_moonmind_can_evidence_it():
    installed = pack()
    web_api = {
        p["id"]: p
        for row in json.loads(build_asvs_pack.PROCEDURES.read_text())["requirements"]
        for p in row["procedures"]
    }
    classes = {"check": 0, "review": 0, "documentation": 0, "not_assessed": 0}
    for row in installed.requirements:
        plan = evidence_plan(installed, row)
        assert not row.objectives_complete
        assert row.objective_basis
        if row.not_assessed_reason:
            classes["not_assessed"] += 1
            assert plan.not_assessed_reason == row.not_assessed_reason
            assert not (plan.methods or plan.procedures or plan.checks)
            continue
        assert plan.methods, row.id
        if row.organisational_evidence:
            classes["documentation"] += 1
            assert [(e.category, e.purpose) for e in row.organisational_evidence] == [
                ("policy", "design")
            ]
            continue
        assert row.procedures, row.id
        classes["check" if row.objectives else "review"] += 1
        for procedure in row.procedures:
            if procedure.id != "requirement-review":
                # One owner: the web/API catalog, embedded unchanged by ID.
                assert procedure.model_dump(mode="json", by_alias=True) == (
                    web_api[procedure.id]
                )
        if any(p.method in {"zap", "application-access"} for p in row.procedures):
            assert "observation" in plan.methods
    assert classes == {"check": 1, "review": 64, "documentation": 4, "not_assessed": 1}
    # Each of the six web/API procedure groups is reused for what it addresses.
    used = {p.id for row in installed.requirements for p in row.procedures}
    assert set(web_api) <= used
    assert any("mostly not deterministic" in text for text in installed.limitations)


def default_rules():
    rules = []
    for name, languages in (
        ("python", ["python"]),
        ("javascript", ["javascript", "typescript"]),
    ):
        raw = (ROOT / f"services/repository-security/rules/{name}.yml").read_bytes()
        rules.append(
            ScannerRule(
                id=f"moonmind-default-{name}",
                path=f"/opt/moonmind-scanner-rules/default/{name}.yml",
                sha256=hashlib.sha256(raw).hexdigest(),
                license="Apache-2.0",
                source="https://github.com/MoonLadderStudios/MoonMind",
                notice="MoonMind default Opengrep rules",
                languages=languages,
            )
        )
    return rules


def opengrep_summary(case, *, native_ref="art_opengrep", admitted=False):
    """The retained summary an Opengrep job writes for a fixture's native report.

    By default the admission omits rules and the runner records the image
    defaults it verified, as for an operator who keeps the default rules.
    """
    fixture = json.loads(FIXTURE.read_text())["cases"][case]
    result = normalize_native(
        "opengrep",
        json.dumps(fixture["report"]).encode(),
        exit_code=0,
        terminal_state="succeeded",
        native_ref=native_ref,
    )
    rules = default_rules()
    return ScannerSummary(
        scanner="opengrep",
        admission=RepositoryScan(
            scanner="opengrep",
            outputDirectory="artifacts/opengrep",
            rules=rules if admitted else None,
        ),
        execution=ScannerExecution(
            inputDigest="a" * 64,
            assets=sorted(fixture["sources"]),
            version="2.0.0",
            exits={"scan": 0},
            states={"scan": "succeeded"},
            effectiveRules=rules,
        ),
        results={"scan": result},
        nativeRefs={"scan": native_ref},
        coverage="complete",
        limitations=[],
    )


def trivy_dependency_summary(*, native_ref="art_trivy", vulnerable=True):
    """A complete Trivy repository scan, optionally with one dependency CVE."""
    findings = (
        [
            Finding(
                source="repository",
                tool="trivy",
                ruleId="CVE-2026-0001",
                asset="requirements.txt",
                nativeId="requirements.txt:CVE-2026-0001",
                evidenceRefs=[native_ref],
                priority=VulnerabilityPriority(),
            )
        ]
        if vulnerable
        else []
    )
    return ScannerSummary(
        scanner="trivy",
        admission=RepositoryScan(
            scanner="trivy", outputDirectory="artifacts/trivy", trivyScanners=["vuln"]
        ),
        execution=ScannerExecution(
            inputDigest="b" * 64,
            assets=["requirements.txt"],
            version="0.58.0",
            exits={"scan": 0},
            states={"scan": "succeeded"},
        ),
        results={
            "scan": NativeResult(
                execution="succeeded",
                coverage="complete",
                examinedAssets=["requirements.txt"],
                findings=findings,
            )
        },
        nativeRefs={"scan": native_ref},
        coverage="complete",
        limitations=[],
    )


def assess(observation, choices, *, assets):
    plan, _ = compile_framework_plan(
        pack(),
        scope=AssessmentScope(assets=assets),
        period=EvidencePeriod(start=NOW.date(), end=NOW.date()),
        choices=choices,
        now=NOW,
    )
    compared = Comparison.model_validate(
        compare_observations(observation, None, now=NOW)
    )
    window = {
        "observedAt": observation.observed_at.isoformat(),
        "expiresAt": observation.expires_at.isoformat(),
    }
    return assess_controls(
        plan,
        compared,
        scan_ref="art_scan",
        comparison_ref="art_comparison",
        coverage_windows=[window] * len(observation.coverage),
        now=NOW,
        evidence_availability={},
    )


def applicable(*ids, systems=None):
    return {
        key: {
            "applicability": "applicable",
            "applicabilityReason": "Controlled fixture application in scope.",
            **({"systems": systems} if systems else {}),
        }
        for key in ids
    }


@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.parametrize(
    "case, outcome, conclusion",
    [("vulnerable", "failed", "not_satisfied"), ("repaired", "passed", "inconclusive")],
)
def test_each_deterministic_objective_fails_on_vulnerable_and_passes_on_repaired_input(
    admitted, case, outcome, conclusion
):
    from moonmind.workflows.skills.security_assessment import _scanner_observation

    objectives = {
        (row.id, objective.id)
        for row in pack().requirements
        for objective in row.objectives
    }
    assert objectives == set(OBJECTIVE_FIXTURES)
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["scanner"]["ruleSha256"] == {
        rule.path.rsplit("/", 1)[1]: rule.sha256 for rule in default_rules()
    }
    assets = sorted(fixture["cases"][case]["sources"])
    request = SimpleNamespace(scope=AssessmentScope(assets=assets))
    observation = _scanner_observation(
        request, opengrep_summary(case, admitted=admitted), "art_summary", NOW
    )
    for requirement_id, objective_id in OBJECTIVE_FIXTURES:
        report = assess(observation, applicable(requirement_id), assets=assets)
        row = next(r for r in report.requirements if r.requirement.id == requirement_id)
        result = next(o for o in row.objectives if o.id == objective_id)
        assert (result.outcome, row.conclusion) == (outcome, conclusion)
        assert result.checked_cells == result.required_cells == 2 * len(assets)
        # A partial rule never completes the requirement.
        assert row.coverage == "partial" and row.review_state == "review_required"


def test_default_rule_binding_never_turns_a_finding_into_a_pass():
    rules = default_rules()
    prefixed = "opt.moonmind-scanner-rules.default."
    assert default_rule_ids(rules) == sorted(
        prefixed + rule
        for rule in (
            "moonmind-python-dynamic-eval",
            "moonmind-python-shell-command",
            "moonmind-javascript-dynamic-eval",
        )
    )
    # A finding whose rule ID carries another prefix replaces the guess, so the
    # objective's rule stays unchecked rather than passing beside a finding.
    other = SimpleNamespace(rule_id="workspace.rules.moonmind-python-dynamic-eval")
    ids = default_rule_ids(rules, [other])
    assert other.rule_id in ids
    assert prefixed + "moonmind-python-dynamic-eval" not in ids
    tampered = [rules[0].model_copy(update={"sha256": "0" * 64})]
    assert default_rule_ids(tampered) == []


def test_clean_scan_satisfies_no_requirement_and_unassessable_ones_say_why():
    from moonmind.workflows.skills.security_assessment import _scanner_observation

    assets = ["app.js", "app.py"]
    observation = _scanner_observation(
        SimpleNamespace(scope=AssessmentScope(assets=assets)),
        opengrep_summary("repaired"),
        "art_summary",
        NOW,
    )
    ids = [row.id for row in pack().requirements]
    report = assess(observation, applicable(*ids), assets=assets)
    rows = {row.requirement.id: row for row in report.requirements}
    assert len(rows) == 70
    assert not [key for key, row in rows.items() if row.conclusion == "satisfied"]
    unassessed = rows["V14.3.1"]
    assert (unassessed.conclusion, unassessed.coverage) == (
        "not_assessed",
        "not_assessed",
    )
    assert unassessed.limitations[0].startswith("Not assessed here: Needs the browser")
    documentation = rows["V8.1.1"]
    assert documentation.organisational_evidence[0].state == "requested"
    assert documentation.conclusion == "inconclusive"
    text = render_control_assessment(report)
    assert "not assessed here: Needs the browser" in text
    assert "procedures: ownership-review, ownership-cases" in text


def lens_report(*findings, choices=None):
    installed = pack()
    observation_findings = list(findings)
    metadata = retained_pack_metadata(
        installed, artifact_ref="art_pack", sha256="a" * 64, now=NOW
    )
    from moonmind.security.assessments import Observation

    observed = Observation.model_validate(
        {
            "scope": {"assets": ["app"]},
            "observedAt": NOW.isoformat(),
            "expiresAt": (NOW + timedelta(days=1)).isoformat(),
            "coverage": [
                {
                    "source": source,
                    "tool": tool,
                    "assets": ["app"],
                    "ruleIds": [],
                    "allRules": True,
                    "configRef": f"{tool}:fixture",
                    "status": "complete",
                    "evidenceMethod": "deterministic_check",
                }
                for source, tool in sorted(
                    {(f.source, f.tool) for f in findings} or {("repository", "trivy")}
                )
            ],
            "findings": [f.model_dump(mode="json", by_alias=True) for f in findings],
            "provenance": {"fixture": "controlled"},
        }
    )
    report = assess(observed, choices or {}, assets=["app"])
    return metadata, reporting_lenses(
        metadata, report.requirements, observation_findings
    )


def finding(source, tool, rule, *, vulnerability=False):
    return Finding(
        source=source,
        tool=tool,
        ruleId=rule,
        asset="app",
        nativeId=f"{tool}:{rule}",
        evidenceRefs=["art_native"],
        priority=VulnerabilityPriority() if vulnerability else None,
    )


def test_top10_lens_groups_results_and_findings_without_granting_coverage():
    cve = finding("repository", "trivy", "CVE-2026-0001", vulnerability=True)
    secret = finding("repository", "gitleaks", "generic-api-key")
    misconfig = finding("repository", "trivy", "DS002")
    zap = finding("web", "zap", "10021")
    _, lenses = lens_report(
        cve, secret, misconfig, zap, choices=applicable("V8.2.2", "V14.3.1", "V15.2.1")
    )
    (lens,) = lenses
    assert (lens["frameworkId"], lens["edition"]) == ("owasp-top-10", "2025")
    categories = {row["id"]: row for row in lens["categories"]}
    assert list(categories) == [f"A{n:02d}:2025" for n in range(1, 11)]
    assert not {row["status"] for row in categories.values()} - {
        "assessed",
        "not_assessed",
        "unmapped",
    }
    supply = categories["A03:2025"]
    assert supply["title"] == "Software Supply Chain Failures"
    assert [f["ruleId"] for f in supply["findings"]] == ["CVE-2026-0001"]
    # Known-vulnerability priority travels with the finding (empty without feeds).
    assert supply["findings"][0]["priority"] == {"kev": None, "epss": None}
    assert {r["id"]: r["conclusion"] for r in supply["requirements"]}["V15.2.1"] == (
        "inconclusive"
    )
    assert supply["status"] == "assessed"
    bases = {m["id"]: m for m in supply["mappings"]}
    assert bases["a03-asvs-v15"]["basis"] == "upstream"
    assert bases["a03-known-vulnerabilities"]["reviewedBy"] == "MoonMind maintainers"
    assert [f["ruleId"] for f in categories["A07:2025"]["findings"]] == [
        "generic-api-key"
    ]
    assert [f["ruleId"] for f in categories["A02:2025"]["findings"]] == ["DS002"]
    # A CVE-less Trivy configuration finding is not a supply-chain vulnerability.
    assert "DS002" not in {f["ruleId"] for f in supply["findings"]}
    for key in ("A09:2025", "A10:2025"):
        assert categories[key]["status"] == "unmapped"
    # V14.3.1 is not assessable, but A04's other rows were not selected either.
    assert categories["A04:2025"]["status"] == "not_assessed"
    assert categories["A01:2025"]["status"] == "assessed"
    assert [f["ruleId"] for f in lens["unmappedFindings"]] == ["10021"]
    text = "\n".join(render_reporting_lens(lens, str))
    assert "grants no coverage" in text
    assert (
        "A09:2025 Security Logging and Alerting Failures: no mapping in this pack; not assessed, not a pass."
        in text
    )
    assert (
        "A04:2025 Cryptographic Failures: no assessed requirement maps here; not a pass"
        in text
    )
    assert "CVE-2026-0001 on app" in text
    assert "a03-asvs-v15 (upstream, partial;" in text
    assert "reviewed by MoonMind maintainers on 2026-11-23" in text
    assert "Findings with no mapped category: 1 (web/zap 10021)." in text
    assert "pass" not in text.replace("not a pass", "").replace("passing", "")


def test_restricted_or_lensless_packs_add_no_lens():
    metadata, _ = lens_report()
    assert reporting_lenses({**metadata, "mappingTargets": []}, [], []) == []
    restricted = {key: value for key, value in metadata.items() if key != "mappings"}
    restricted.pop("mappingTargets")
    assert reporting_lenses(restricted, [], []) == []


def test_mapping_contract_accepts_finding_classes_and_rejects_ambiguity():
    source = {"url": "https://example.test", "section": "s", "reviewedOn": "2026-11-23"}
    base = {
        "id": "m",
        "targetId": "t",
        "toRequirementIds": ["A03:2025"],
        "relation": "partial",
        "rationale": "r",
        "source": source,
        "basis": "evaluated",
        "reviewedBy": "reviewer",
        "reviewedOn": "2026-11-23",
    }
    classes = [{"source": "repository", "tool": "trivy", "rulePatterns": ["*"]}]
    FrameworkMapping.model_validate({**base, "fromFindings": classes})
    for bad in (
        {},
        {"fromFindings": classes, "fromRequirementIds": ["V1.2.1"]},
    ):
        with pytest.raises(ValueError, match="source requirements or finding classes"):
            FrameworkMapping.model_validate({**base, **bad})
    # Existing requirement mappings serialize exactly as before.
    mapping = FrameworkMapping.model_validate({**base, "fromRequirementIds": ["V1"]})
    assert "fromFindings" not in mapping.model_dump(by_alias=True)
    # So do existing mapping targets, which keeps their mappings digest.
    target = {
        "id": "t",
        "frameworkId": "f",
        "edition": "2025",
        "requirementIds": ["A01:2025"],
    }
    dumped = MappingTarget.model_validate(target).model_dump(mode="json", by_alias=True)
    assert dumped == target
    with pytest.raises(ValueError, match="titles name unknown requirements"):
        MappingTarget.model_validate(
            {
                "id": "t",
                "frameworkId": "f",
                "edition": "2025",
                "requirementIds": ["A01:2025"],
                "titles": {"A02:2025": "x"},
            }
        )
    vulnerable_only = FindingClass(
        source="repository", tool="trivy", rulePatterns=["*"], vulnerabilities=True
    )
    assert vulnerable_only.matches(finding("repository", "trivy", "CVE-2025-1234"))
    assert vulnerable_only.matches(
        finding("repository", "trivy", "GHSA-xxxx", vulnerability=True)
    )
    assert not vulnerable_only.matches(finding("repository", "trivy", "DS002"))
    assert not vulnerable_only.matches(finding("image", "trivy", "CVE-2025-1234"))


def test_a_not_assessed_requirement_declares_no_evidence_path():
    row = next(r for r in pack().requirements if r.not_assessed_reason)
    data = row.model_dump(mode="json", by_alias=True)
    for extra in (
        {"requiredEvidenceMethods": ["evidence"]},
        {
            "procedures": [
                pack().requirements[0].procedures[0].model_dump(by_alias=True)
            ]
        },
    ):
        with pytest.raises(ValueError, match="declares no evidence path"):
            PackRequirement.model_validate({**copy.deepcopy(data), **extra})


def test_catalog_offers_this_pack_as_the_general_best_practices_choice():
    entries = [entry for entry in readiness_catalog() if entry["choice"]]
    assert [(e["id"], e["edition"], e["choice"]) for e in entries] == [
        (PACK_ID, "5.0.0", "general-best-practices")
    ]
    (entry,) = entries
    checks = {check["id"]: check for check in entry["companionChecks"]}
    assert set(checks) == {"known-vulnerabilities", "secrets", "misconfiguration"}
    assert "KEV" in checks["known-vulnerabilities"]["title"]
    assert "EPSS" in checks["known-vulnerabilities"]["title"]
    assert checks["secrets"]["presets"] == ["security-code-supply-chain"]
    for check in checks.values():
        for slug in check["presets"]:
            assert (ROOT / f"api_service/data/presets/{slug}.yaml").exists()
    assert entry["subjects"]["application"]["available"]
    assert entry["sha256"] == installed_digest(PACK_ID)


def test_oscal_catalog_export_of_requirement_text_carries_the_attribution():
    from moonmind.security.assessments import Observation
    from moonmind.security.oscal_export import OscalAdapter

    installed = pack()
    observed = Observation.model_validate(
        {
            "scope": {"assets": ["app"]},
            "observedAt": NOW.isoformat(),
            "expiresAt": (NOW + timedelta(days=1)).isoformat(),
            "coverage": [
                {
                    "source": "repository",
                    "tool": "trivy",
                    "assets": ["app"],
                    "ruleIds": [],
                    "allRules": True,
                    "configRef": "trivy:fixture",
                    "status": "complete",
                    "evidenceMethod": "deterministic_check",
                }
            ],
            "findings": [],
            "provenance": {"fixture": "controlled"},
        }
    )
    adapter = OscalAdapter(
        comparison=Comparison.model_validate(
            compare_observations(observed, None, now=NOW)
        ),
        pack=installed,
        framework_report={
            "requirements": [
                {"id": row.id, "status": "applicable"} for row in installed.requirements
            ]
        },
    )
    catalog = adapter.build("catalog", {})["catalog"]
    assert catalog["controls"][0]["parts"][0]["prose"] == (
        installed.requirements[0].summary
    )
    assert installed.attribution in catalog["metadata"]["remarks"]
