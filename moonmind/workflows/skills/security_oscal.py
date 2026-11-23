"""Publish optional exchange documents through the assessment's artifact owner."""

from __future__ import annotations

import json

from pydantic import ValidationError

from moonmind.security.assessments import Comparison, content_key
from moonmind.security.framework_packs import parse_declared_pack
from moonmind.security.oscal_export import (
    LIMITATIONS,
    MODELS,
    OSCAL_VERSION,
    OscalAdapter,
    OscalContext,
    UnsupportedExport,
    readiness_report,
)
from moonmind.workflows.temporal.artifacts import ArtifactDependency


def _models(selected):
    needed = set(selected)
    if needed & {"assessment-results", "poam"}:
        needed.add("assessment-plan")
    if "assessment-plan" in needed:
        needed.add("profile")
    if "profile" in needed:
        needed.add("catalog")
    return [m for m in MODELS if m in needed]


async def _selected_framework(execution, controls):
    """The exported pack, its native rows and the sealed report that holds them."""
    packs = controls[1].pack_metadata if controls else []
    if len(packs) != 1 or "readiness" not in packs[0]:
        raise UnsupportedExport(
            "Select a native framework assessment before requesting OSCAL export.",
            "missing_framework",
        )
    _, raw = await execution.read(packs[0]["artifactRef"])
    pack = parse_declared_pack(raw)
    if pack.distribution != "redistributable":
        # Catalogs copy requirement text, which restricted packs keep in their
        # authorized artifact and out of every portable report.
        raise UnsupportedExport(
            "A restricted-distribution framework pack is not exported to OSCAL; its licensed text stays in the authorized pack artifact.",
            "restricted_distribution",
        )
    return pack, readiness_report(controls[1], packs[0]), controls[0]


async def export_assessment(
    execution, *, request, comparison, comparison_ref, controls=None
):
    result = {
        "status": "unsupported",
        "oscalVersion": OSCAL_VERSION,
        "artifacts": {},
        "diagnostics": [],
        "limitations": list(LIMITATIONS),
    }
    selection = request.oscal_export
    try:
        if len(request.selected_packs) > 1:
            raise UnsupportedExport(
                "OSCAL export supports one framework pack per assessment. This assessment selected multiple packs.",
                "multiple_framework_packs",
            )
        pack, framework_report, framework_ref = await _selected_framework(
            execution, controls
        )
        context = None
        ssp = None
        refs = [comparison_ref, framework_ref]
        if selection.context_ref:
            _, raw = await execution.read(selection.context_ref)
            context = OscalContext.model_validate_json(raw)
            refs.extend([selection.context_ref, *context.evidence_refs()])
            if context.system:
                refs.append(context.system.ssp_ref)
                _, raw = await execution.read(context.system.ssp_ref)
                ssp = json.loads(raw)
                if not isinstance(ssp, dict):
                    raise UnsupportedExport(
                        "system.sspRef must contain a system-security-plan object.",
                        "missing_ssp",
                    )
        # Read every required dependency under current access, before exporting
        # it. A historical seal is retention evidence, never a new read grant.
        for root in (comparison_ref, framework_ref):
            proof = await execution.service.dependency_proof(
                root_artifact_id=root, access=execution.access
            )
            if proof is None:
                raise UnsupportedExport(
                    "The native report needs a verified evidence dependency seal before export.",
                    "unsealed_evidence",
                )
            refs.extend(e.artifact_id for e in proof if e.required)
        refs = list(dict.fromkeys(refs))
        for ref in refs:
            await execution.read(ref)
        adapter = OscalAdapter(
            comparison=Comparison.model_validate(comparison),
            pack=pack,
            framework_report=framework_report,
            context=context,
            ssp=ssp,
        )
    except UnsupportedExport as exc:
        result["diagnostics"].append({"code": exc.code, "message": str(exc)})
        return result
    except (ValidationError, ValueError, TypeError, KeyError):
        result["diagnostics"].append(
            {
                "code": "invalid_context",
                "message": "Supply oscal-export-context/v1 data with supported states and relationships; authority and unknown fields are not accepted.",
            }
        )
        return result
    except Exception as exc:
        result["diagnostics"].append(
            {
                "code": "inaccessible_evidence",
                "error": type(exc).__name__,
                "message": "Export requires readable native, context, SSP and cited evidence artifacts under current access. Check their access and retention; native reports remain available.",
            }
        )
        return result
    artifacts = result["artifacts"]
    for model in _models(selection.models):
        try:
            if (
                model == "profile"
                and "catalog" not in artifacts
                or model == "assessment-plan"
                and "profile" not in artifacts
                or model in {"assessment-results", "poam"}
                and "assessment-plan" not in artifacts
            ):
                raise UnsupportedExport(
                    "The required preceding export is unavailable; resolve its diagnostic first.",
                    "missing_export_dependency",
                )
            document = adapter.build(model, artifacts)
        except UnsupportedExport as exc:
            result["diagnostics"].append(
                {"model": model, "code": exc.code, "message": str(exc)}
            )
            continue
        except Exception as exc:
            result["status"] = "failed"
            result["diagnostics"].append(
                {
                    "model": model,
                    "code": "export_mapping_failed",
                    "error": type(exc).__name__,
                    "message": "The optional export could not be mapped; retain the native reports and inspect the supplied context.",
                }
            )
            return result
        try:
            payload = json.dumps(
                document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
            key = content_key([model, document])
            saved = await execution.saved(kind="security.oscal", key=key)
            root = (
                saved[0]
                if saved
                else await execution.write(payload, kind="security.oscal", key=key)
            )
            dependencies = [*refs, *artifacts.values()]

            async def seal_dependencies():
                return [
                    ArtifactDependency(
                        ref,
                        await execution.digest(ref),
                        True,
                        "security.oscal-evidence",
                    )
                    for ref in dict.fromkeys(dependencies)
                ]

            await execution.seal(root, seal_dependencies)
            await execution.service.link_artifact(
                artifact_id=root,
                principal=execution.principal,
                execution_ref={**execution.execution, "link_type": "report.export"},
            )
            artifacts[model] = root
        except Exception as exc:
            result["status"] = "failed"
            result["diagnostics"].append(
                {
                    "model": model,
                    "code": "artifact_publication_failed",
                    "error": type(exc).__name__,
                    "message": "Retry this export; completed native work and sealed exports are retained for reuse.",
                }
            )
            return result
    result["status"] = (
        "partial"
        if result["diagnostics"] and artifacts
        else "unsupported" if result["diagnostics"] else "exported"
    )
    return result
