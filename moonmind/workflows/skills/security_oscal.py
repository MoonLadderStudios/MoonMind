"""Publish optional exchange documents through the assessment's artifact owner."""

from __future__ import annotations

import json

from pydantic import ValidationError

from moonmind.security.assessments import Comparison, content_key
from moonmind.security.frameworks import load_framework_pack
from moonmind.security.oscal_export import (
    LIMITATIONS,
    MODELS,
    OSCAL_VERSION,
    OscalAdapter,
    OscalContext,
    UnsupportedExport,
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


async def export_assessment(
    execution, *, request, comparison, comparison_ref, framework
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
        if framework is None:
            raise UnsupportedExport(
                "Select a native framework assessment before requesting OSCAL export.",
                "missing_framework",
            )
        context = None
        ssp = None
        refs = [comparison_ref, framework[0]]
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
        for root in (comparison_ref, framework[0]):
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
            pack=load_framework_pack(request.framework.pack_id),
            framework_report=framework[1],
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
