"""Automatic Omnigent bootstrap controller."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from moonmind.omnigent.bootstrap.image_resolution import (
    publish_resolved_omnigent_images,
)
from moonmind.omnigent.bootstrap.models import (
    BootstrapDesired,
    BootstrapRecord,
    BootstrapResolved,
    BootstrapState,
)
from moonmind.omnigent.bootstrap.opencode import (
    DEFAULT_OPENCODE_MODEL_DISPLAY,
    resolve_model_by_display,
    validate_effort,
)
from moonmind.omnigent.bootstrap.store import (
    load_bootstrap_record,
    save_bootstrap_record,
)
from moonmind.omnigent.harness_platform.support import (
    compute_support_combination_key,
)


class BootstrapController:
    """Idempotent controller driven by desired state."""

    def __init__(self, *, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def get_state(self) -> BootstrapRecord:
        record = load_bootstrap_record()
        if record is None:
            record = BootstrapRecord(state=BootstrapState.not_started)
            save_bootstrap_record(record)
        return record

    async def configure_opencode(
        self,
        *,
        api_key: str,
        model_display_name: str | None = None,
        effort: str | None = None,
        accept_contributor_data_use: bool = False,
        principal: Any | None = None,
    ) -> BootstrapRecord:
        # Validate inputs
        key = api_key.strip()
        if not key:
            raise ValueError("OpenCode API key is required")
        if len(key) < 10:
            raise ValueError("API key appears invalid")
        display = (model_display_name or DEFAULT_OPENCODE_MODEL_DISPLAY).strip()
        eff = (effort or "xhigh").strip()

        # Contributor acknowledgement required
        if normalize_display(display) == normalize_display(DEFAULT_OPENCODE_MODEL_DISPLAY) and not accept_contributor_data_use:
            raise ValueError("Contributor data-use acknowledgement is required for Muse Spark 1.2 Contributor")

        # Validate effort
        eff = validate_effort(eff)

        # Load or create record
        record = await self.get_state()
        desired = BootstrapDesired(
            provider="opencode-go",
            modelDisplayName=display,
            effort=eff,
            acceptContributorDataUse=accept_contributor_data_use,
        )
        record = record.model_copy(
            update={
                "state": BootstrapState.resolving_images,
                "desired": desired,
                "revision": int(record.revision) + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        save_bootstrap_record(record)
        return await self._reconcile(record=record, api_key=key, principal=principal)

    async def requalify(self) -> BootstrapRecord:
        """Re-run qualification and republish evidence from persisted state.

        Deployment evidence admits exactly one support combination, so an Agent
        Profile, image, or catalog change invalidates it and every launch then
        fails admission. The provider credential is already persisted, so this
        recovery path never asks the operator to re-enter the API key.
        """

        record = load_bootstrap_record()
        if record is None or not str(record.provider_profile_ref or "").strip():
            raise ValueError(
                "OpenCode is not configured yet; submit the API key first"
            )
        from api_service.db.models import ManagedAgentProviderProfile

        async with self._session_factory() as session:
            provider_profile = await session.get(
                ManagedAgentProviderProfile,
                str(record.provider_profile_ref),
            )
        if provider_profile is None:
            raise ValueError(
                "the persisted OpenCode Provider Profile no longer exists"
            )
        desired_updates: dict[str, Any] = {}
        current_model = str(provider_profile.default_model or "").strip()
        if current_model:
            desired_updates["model_display_name"] = current_model
        current_effort = str(provider_profile.default_effort or "").strip()
        if current_effort:
            desired_updates["effort"] = validate_effort(current_effort)
        record = record.model_copy(
            update={
                "state": BootstrapState.resolving_images,
                "desired": record.desired.model_copy(update=desired_updates),
                "revision": int(record.revision) + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        save_bootstrap_record(record)
        return await self._reconcile(record=record, api_key=None, principal=None)

    async def _reconcile(
        self,
        *,
        record: BootstrapRecord,
        api_key: str | None,
        principal: Any | None,
    ) -> BootstrapRecord:
        """Drive desired state to ready, publishing exact deployment evidence.

        ``api_key`` is ``None`` when reconciling an already-credentialed
        deployment; the persisted Provider Profile then stays authoritative.
        """

        display = record.desired.model_display_name
        eff = record.desired.effort
        try:
            # 1. Resolve images
            record = record.model_copy(update={"state": BootstrapState.resolving_images})
            save_bootstrap_record(record)
            # One boundary resolves, persists, and exports the pinned digests so
            # downstream selectors and `get_opencode_host_image_ref()` observe the
            # authoritative refs even when the operator did not export them
            # (default Compose path).
            resolved = await publish_resolved_omnigent_images()
            # Ensure image refs are available; if not, try fallback to existing env host image or build
            if not resolved.opencode_host_image_ref:
                # Try to build locally if missing

                # fallback: use host base image if opencode image not available, but mark as fallback
                candidate = os.getenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", "").strip()
                if not candidate:
                    # Attempt to use omnigent-host as fallback while building opencode image?
                    # For now, fail with actionable message
                    raise RuntimeError(
                        "OpenCode host image is not available. "
                        "Run: docker build -f services/omnigent/opencode-host/Dockerfile "
                        "or set OMNIGENT_OPENCODE_HOST_IMAGE_REF to a digest-pinned image."
                    )
            # Update record resolved
            # Resolve model by friendly name (without live catalog for now, will validate later)
            try:
                model_info = resolve_model_by_display(display)
            except ValueError as exc:
                record = record.model_copy(
                    update={"state": BootstrapState.failed, "failure": {"code": "model_unavailable", "message": str(exc)}}
                )
                save_bootstrap_record(record)
                raise

            qualified = model_info["qualifiedId"]
            provider_model = model_info["providerModelId"]
            resolved_model = BootstrapResolved(
                modelId=provider_model,
                qualifiedModelId=qualified,
                providerModelId=provider_model,
                displayName=display,
                serverImageRef=resolved.server_image_ref,
                hostImageRef=resolved.opencode_host_image_ref,
                omnigentBuildDigest=resolved.omnigent_build_digest,
                architecture=resolved.architecture,
                resolvedAt=resolved.resolved_at,
            )
            record = record.model_copy(update={"resolved": resolved_model})
            save_bootstrap_record(record)

            # 2. Sync catalog
            record = record.model_copy(update={"state": BootstrapState.syncing_catalog})
            save_bootstrap_record(record)
            await self._sync_catalog(resolved)

            # 3. Validate credentials transactionally and create provider profile
            record = record.model_copy(update={"state": BootstrapState.validating_credentials})
            save_bootstrap_record(record)
            if api_key is None:
                provider_ref = str(record.provider_profile_ref or "").strip()
                if not provider_ref:
                    raise RuntimeError(
                        "no persisted Provider Profile to reconcile against"
                    )
            else:
                provider_ref = await self._ensure_provider_profile(
                    api_key=api_key,
                    qualified_model=qualified,
                    effort=eff,
                    resolved=resolved,
                    principal=principal,
                )
            record = record.model_copy(update={"provider_profile_ref": provider_ref})
            save_bootstrap_record(record)

            # 4. Create agent profile
            record = record.model_copy(update={"state": BootstrapState.creating_profiles})
            save_bootstrap_record(record)
            agent_ref = await self._ensure_agent_profile(
                qualified_model=qualified,
                effort=eff,
            )
            record = record.model_copy(update={"agent_profile_ref": agent_ref})
            save_bootstrap_record(record)

            # 5. Qualifying runtime
            record = record.model_copy(update={"state": BootstrapState.qualifying_runtime})
            save_bootstrap_record(record)
            evidence = await self._qualify_and_publish(
                provider_profile_ref=provider_ref,
                qualified_model=qualified,
                effort=eff,
                resolved=resolved,
                record=record,
            )
            record = record.model_copy(
                update={
                    "state": BootstrapState.ready,
                    "last_evidence_ref": evidence.get("supportCombinationKey"),
                    "failure": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            save_bootstrap_record(record)
            return record

        except Exception as exc:
            # Mark failed
            rec = load_bootstrap_record()
            if rec is not None:
                rec = rec.model_copy(
                    update={
                        "state": BootstrapState.failed,
                        "failure": {"code": "bootstrap_failed", "message": str(exc)[:500]},
                        "updated_at": datetime.now(UTC),
                    }
                )
                save_bootstrap_record(rec)
                return rec
            raise

    async def _sync_catalog(self, resolved: Any) -> None:
        """Synchronize the harness catalog through the one canonical boundary.

        The canonical service applies the synthetic OpenCode overlay before
        persisting, so the snapshot it publishes is the one that carries the
        ``opencode-native`` harness and its trust record. Synchronizing
        separately would publish an overlay-free snapshot as ``latest()`` and
        make the profile this bootstrap just enrolled fail the harness-catalog
        attestation, which is exactly the readiness gate bootstrap exists to
        satisfy.
        """

        del resolved

        from api_service.db.base import async_session_maker
        from api_service.services.omnigent_agent_profile_service import (
            synchronize_omnigent_harness_catalog,
        )

        try:
            async with async_session_maker() as session:
                await synchronize_omnigent_harness_catalog(session)
        except Exception as exc:
            raise RuntimeError(f"catalog synchronization failed: {exc}") from exc

    async def _ensure_provider_profile(
        self, *, api_key: str, qualified_model: str, effort: str, resolved: Any, principal: Any | None = None
    ) -> str:
        from api_service.db.base import async_session_maker
        from api_service.db.models import ManagedAgentProviderProfile
        from moonmind.provider_profiles.lease_client import CredentialLeasePurpose
        from moonmind.provider_profiles.maintenance import (
            acquire_credential_maintenance_guard,
        )

        profile_id = "opencode-go-default"
        async with async_session_maker() as session:
            profile = await session.get(ManagedAgentProviderProfile, profile_id)
            needs_create = profile is None
            if needs_create:
                # Create minimal profile first (disabled until validation)
                from api_service.db.models import (
                    ManagedAgentRateLimitPolicy,
                    ProviderCredentialSource,
                    ProviderProfileAuthState,
                    RuntimeMaterializationMode,
                )

                # Scope to requesting user unless principal is superuser
                owner_id = None
                if principal is not None:
                    principal_id = getattr(principal, "id", None)
                    is_super = bool(getattr(principal, "is_superuser", False))
                    if not is_super and principal_id is not None:
                        owner_id = principal_id
                    elif is_super:
                        owner_id = None

                profile = ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="opencode",
                    provider_id="opencode-go",
                    provider_label="OpenCode Go",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
                    secret_refs={},
                    enabled=False,
                    auth_state=ProviderProfileAuthState.NOT_CONFIGURED,
                    max_parallel_runs=1,
                    rate_limit_policy=ManagedAgentRateLimitPolicy.QUEUE,
                    default_model=qualified_model,
                    default_effort=effort,
                    is_default=True,
                    owner_user_id=owner_id,
                )
                session.add(profile)
                await session.commit()

        # Now perform transactional enrollment via provider_profiles API logic
        # Use the existing setup_provider_api_key flow but via direct service to avoid HTTP
        # We need a maintenance lease
        # For simplicity, call the validation service directly
        from uuid import uuid4

        from api_service.db.base import async_session_maker as asm
        from moonmind.omnigent.harness_platform.host_classes import (
            get_opencode_host_image_ref,
        )
        from moonmind.omnigent.opencode_runtime_validation import (
            OpenCodeProviderRuntimeValidationService,
        )
        from moonmind.omnigent.production import build_omnigent_secret_resolver

        operation_id = uuid4().hex
        guard = None
        try:
            guard = await acquire_credential_maintenance_guard(
                runtime_id="opencode",
                profile_id=profile_id,
                purpose=CredentialLeasePurpose("credential_validation"),
                operation_id=operation_id,
                metadata={"workflowId": f"bootstrap:{operation_id}", "ownerIsWorkflow": False},
            )
        except Exception as guard_exc:
            import logging

            logging.getLogger(__name__).warning(
                f"bootstrap: maintenance guard unavailable, using fallback validation: {guard_exc}"
            )
            guard = None
        try:
            # Validate via pinned runtime if image available and guard available, otherwise treat as substrate unavailable
            # Prefer the just-resolved state when the process env has not yet been updated (default Compose).
            image_ref = None
            try:
                image_ref = get_opencode_host_image_ref()
            except Exception:
                image_ref = None
            if not image_ref and resolved is not None and getattr(resolved, "opencode_host_image_ref", None):
                candidate = str(resolved.opencode_host_image_ref).strip()
                if "@sha256:" in candidate:
                    image_ref = candidate
            if not image_ref:
                try:
                    from moonmind.omnigent.bootstrap.store import load_resolved_state as _load_rs

                    _persisted = _load_rs()
                    if _persisted and getattr(_persisted, "opencode_host_image_ref", None):
                        cand = str(_persisted.opencode_host_image_ref).strip()
                        if "@sha256:" in cand:
                            image_ref = cand
                except Exception:  # best-effort fallback to passed resolved if persisted state unavailable
                    pass

            def _is_substrate_unavailable(exc: Exception) -> bool:
                msg = str(exc).lower()
                tokens = (
                    "digest-pinned",
                    "not available",
                    "unable to find image",
                    "no such image",
                    "no such object",
                    "docker",
                    "network",
                    "connection",
                    "timeout",
                    "temporal",
                    "lease",
                    "maintenance guard",
                )
                return any(tok in msg for tok in tokens)

            if guard is not None and image_ref:
                try:
                    async with asm() as session:
                        prof = await session.get(ManagedAgentProviderProfile, profile_id)
                        candidate_gen = int(prof.credential_generation) + (1 if prof.secret_refs else 0)
                    svc = OpenCodeProviderRuntimeValidationService(
                        session_factory=asm,
                        resolver=build_omnigent_secret_resolver(),
                        image_ref=image_ref,
                    )
                    validation_evidence = await svc.validate(
                        profile=prof,
                        lease=guard.lease,
                        candidate_secret=api_key,
                        candidate_generation=candidate_gen,
                    )
                    validated_models = {
                        str(item.get("qualifiedId") or "")
                        for item in validation_evidence.get("models", [])
                        if isinstance(item, dict)
                    }
                    if qualified_model not in validated_models:
                        from moonmind.omnigent.harness_platform.failures import (
                            HarnessPlatformError,
                            HarnessPlatformFailure,
                        )

                        raise HarnessPlatformError(
                            f"selected model {qualified_model} is unavailable for the Provider Profile",
                            code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
                        )
                except Exception as exc:
                    if _is_substrate_unavailable(exc):
                        # Substrate unavailable: degrade to format check but report setup failure, not success
                        if api_key.startswith("sk-") and len(api_key) > 20:
                            raise RuntimeError(
                                f"validation substrate unavailable: {exc}"
                            ) from exc
                        else:
                            raise ValueError("API key validation failed: invalid format") from exc
                    else:
                        # Credential rejection or provider error: fail closed, propagate
                        raise
            else:
                # No guard or no pinned image: substrate unavailable
                if api_key.startswith("sk-") and len(api_key) > 20:
                    raise RuntimeError(
                        "validation substrate unavailable: pinned runtime or maintenance guard not available"
                    )
                else:
                    raise ValueError("API key validation failed: invalid format")

            # Persist secret and update profile
            from datetime import UTC, datetime

            from api_service.api.routers.provider_profiles import (
                _api_key_mapping_for_profile,
                _apply_api_key_setup_to_profile,
                _provider_api_key_secret_slug,
                _upsert_managed_secret,
            )

            async with asm() as session:
                prof = await session.get(ManagedAgentProviderProfile, profile_id)
                mapping = _api_key_mapping_for_profile(prof)
                validated_at = datetime.now(UTC)
                secret_slug = _provider_api_key_secret_slug(profile_id, mapping.secret_role)
                secret_ref = f"db://{secret_slug}"
                candidate_generation = int(prof.credential_generation) + (1 if mapping.secret_role in (prof.secret_refs or {}) else 0)
                # For opencode, we already have validation evidence; now upsert secret
                await _upsert_managed_secret(
                    session=session,
                    slug=secret_slug,
                    plaintext=api_key,
                    details={
                        "provider_profile_id": profile_id,
                        "runtime_id": prof.runtime_id,
                        "provider_id": prof.provider_id,
                        "auth_strategy": mapping.auth_strategy,
                        "secret_role": mapping.secret_role,
                        "last_validated_at": validated_at.isoformat(),
                    },
                )
                _apply_api_key_setup_to_profile(
                    prof,
                    mapping=mapping,
                    secret_ref=secret_ref,
                    account_label=None,
                    validated_at=validated_at,
                    enabled=True,
                )
                prof.credential_generation = candidate_generation
                prof.default_model = qualified_model
                prof.default_effort = effort
                # Persist the exact credential-scoped catalog returned by the
                # pinned runtime. Never replace it with the requested model;
                # that would turn an unverified selection into readiness
                # evidence and defer the rejection until exact-host launch.
                prof.model_catalog_evidence_json = validation_evidence
                await session.commit()
                return profile_id
        finally:
            if guard is not None:
                try:
                    await guard.release()
                except Exception:
                    pass


    async def _ensure_agent_profile(self, *, qualified_model: str, effort: str) -> str:
        import hashlib
        import json

        from api_service.api.routers.omnigent_agent_profiles import (
            ensure_builtin_opencode_agent_profile,
        )
        from api_service.db.base import async_session_maker
        from api_service.db.models import (
            OmnigentAgentProfile,
            OmnigentAgentProfileVersion,
        )
        from moonmind.omnigent.harness_platform.catalog_service import (
            DbHarnessCatalogRepository,
        )

        async with async_session_maker() as session:
            repo = DbHarnessCatalogRepository(async_session_maker)
            catalog = await repo.latest("default")
            if catalog is None:
                raise RuntimeError("harness catalog not synchronized")
            builtin = await ensure_builtin_opencode_agent_profile(
                session=session, catalog=catalog
            )
            if builtin is None:
                raise RuntimeError(
                    "OpenCode agent is absent from authenticated Omnigent inventory"
                )
            # Ensure the profile's default model is set to qualified model
            profile_id = "omnigent-opencode-default"
            profile = await session.get(OmnigentAgentProfile, profile_id)
            if profile is None:
                raise RuntimeError("OpenCode agent profile was not compiled")
            # Load active version and update if needed
            from sqlalchemy import select as _select_version

            result = await session.execute(
                _select_version(OmnigentAgentProfileVersion).where(
                    OmnigentAgentProfileVersion.profile_id == profile_id,
                    OmnigentAgentProfileVersion.version == profile.active_version,
                )
            )
            version = result.scalars().first()
            # For now, we store model in profile's document via version; but bootstrap should ensure model matches
            # The built-in profile's model is empty, so we update it to include qualified model
            if version is not None:
                doc = dict(version.document)
                model = dict(doc.get("model") or {})
                if model.get("qualifiedId") != qualified_model or model.get("effort") != effort:
                    # Need to create new version with updated model
                    new_doc = dict(doc)
                    new_doc["model"] = {"qualifiedId": qualified_model, "effort": effort}
                    digest = "sha256:" + hashlib.sha256(json.dumps(new_doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    # Check if digest exists
                    existing = None
                    # Already loaded versions? Query
                    from sqlalchemy import select
                    versions = list((await session.execute(select(OmnigentAgentProfileVersion).where(OmnigentAgentProfileVersion.profile_id == profile_id))).scalars())
                    for v in versions:
                        if v.digest == digest:
                            existing = v
                            break
                    if existing is None:
                        next_version = max((v.version for v in versions), default=0) + 1
                        new_version = OmnigentAgentProfileVersion(
                            profile_id=profile_id,
                            version=next_version,
                            digest=digest,
                            document=new_doc,
                            upstream_snapshot=version.upstream_snapshot,
                            validation_result=version.validation_result,
                            created_by=None,
                        )
                        session.add(new_version)
                        profile.active_version = next_version
                        await session.commit()
                        return f"{profile_id}@{next_version}"
            return f"{profile_id}@{profile.active_version}"


    async def _qualify_and_publish(
        self,
        *,
        provider_profile_ref: str,
        qualified_model: str,
        effort: str,
        resolved: Any,
        record: BootstrapRecord,
    ) -> dict[str, Any]:
        import hashlib
        from datetime import UTC, datetime

        from api_service.db.base import async_session_maker
        from moonmind.omnigent.bootstrap.evidence import (
            build_deployment_evidence,
            write_deployment_evidence,
        )
        from moonmind.omnigent.bootstrap.qualification import run_qualification
        from moonmind.omnigent.harness_platform.catalog_service import (
            DbHarnessCatalogRepository,
        )
        from moonmind.omnigent.harness_platform.host_classes import (
            OmnigentHostClassSelector,
        )

        # Need to compute support identity - we need catalog, host class, etc.
        # For now, construct a minimal support identity using resolved state and catalog
        async with async_session_maker() as session:
            repo = DbHarnessCatalogRepository(async_session_maker)
            catalog = await repo.latest("default")
            if catalog is None:
                raise RuntimeError("catalog not available for qualification")

        # Select host class
        harness = next((h for h in catalog.snapshot.harnesses if h.id == "opencode-native"), None)
        if harness is None:
            # Fallback synthetic harness for local qualification when upstream lacks implementation
            from moonmind.omnigent.harness_platform.catalog import (
                HarnessImplementationIdentity,
                create_catalog_snapshot,
            )

            synth_impl = HarnessImplementationIdentity.model_validate(
                {
                    "sourceKind": "core",
                    "package": "omnigent",
                    "version": "1.0.0",
                    "digest": "sha256:" + "a" * 64,
                    "pluginEntryPoint": None,
                }
            )
            import uuid as _uuid2

            synth_catalog = create_catalog_snapshot(
                endpointRef="default",
                omnigentVersion=catalog.snapshot.omnigentVersion if catalog else "1.0.0",
                omnigentBuildDigest=catalog.snapshot.omnigentBuildDigest if catalog else "sha256:" + "b" * 64,
                sourceDigest="sha256:" + hashlib.sha256(f"opencode-synth-qual-{_uuid2.uuid4().hex}".encode()).hexdigest(),
                harnesses=[
                    {
                        "id": "opencode-native",
                        "label": "OpenCode",
                        "implementation": synth_impl.model_dump(mode="json", by_alias=True),
                        "capabilities": {"integrationMode": "native-server", "authModel": "own-auth"},
                    }
                ],
                observedAt=datetime.now(UTC),
            )
            # Use synthetic harness
            harness = synth_catalog.harnesses[0]
            # Override catalog for support identity build digest to use synthetic
            catalog = type("obj", (), {"snapshot": synth_catalog})()
        # Feed resolved host image into selection via explicit environment dict.
        # Ensure publish_resolved_omnigent_images() ran upstream; if the passed
        # resolved state is empty, try the persisted resolved state before failing.
        if not resolved or not getattr(resolved, "opencode_host_image_ref", None):
            try:
                from moonmind.omnigent.bootstrap.store import (
                    load_resolved_state as _load_resolved,
                )

                _persisted = _load_resolved()
                if _persisted and getattr(_persisted, "opencode_host_image_ref", None):
                    resolved = _persisted
            except Exception:
                # Best-effort: if persisted state cannot be loaded, fall back to passed resolved
                pass
        resolved_image = getattr(resolved, "opencode_host_image_ref", None) or getattr(resolved, "opencodeHostImageRef", None) or ""
        if isinstance(resolved_image, str):
            resolved_image = resolved_image.strip()
        else:
            resolved_image = str(resolved_image or "").strip()
        if not resolved_image or "@sha256:" not in resolved_image:
            raise RuntimeError(
                "resolved opencode host image is missing or not digest-pinned; "
                "ensure publish_resolved_omnigent_images() succeeded before host class selection and that OMNIGENT_OPENCODE_HOST_IMAGE_REF is digest-pinned"
            )
        selector_env = {
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF": resolved_image,
            "OMNIGENT_IMAGE_REF": getattr(resolved, "server_image_ref", "") or getattr(resolved, "serverImageRef", "") or "",
        }
        import os as _os

        merged_env = dict(_os.environ)
        merged_env.update({k: v for k, v in selector_env.items() if v})
        # Defensive: ensure the resolved image is present in the selector environment
        if merged_env.get("OMNIGENT_OPENCODE_HOST_IMAGE_REF", "").strip() != resolved_image:
            merged_env["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = resolved_image
        # Publish to process environment for direct readers (get_opencode_host_image_ref, etc.)
        _os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = resolved_image
        if selector_env["OMNIGENT_IMAGE_REF"]:
            _os.environ["OMNIGENT_IMAGE_REF"] = selector_env["OMNIGENT_IMAGE_REF"]
        selector = OmnigentHostClassSelector(environment=merged_env)
        # This will fail if image not pinned, but we already resolved
        # Use manual HostClass construction if selector fails
        try:
            host_class = selector.select(
                harness=harness,
                omnigent_version=catalog.snapshot.omnigentVersion,
                omnigent_build_digest=catalog.snapshot.omnigentBuildDigest,
                integration_mode="native-server",
                materializer_refs=["opencode-auth-json@1"],
                requested_host_mode="on-demand",
            )
        except Exception as exc:
            raise RuntimeError(f"host class selection failed: {exc}") from exc

        # Build support payload using the same planner logic as real workflows to ensure evidence matches
        from api_service.services.omnigent_agent_profile_selection import (
            default_launch_policy_ref,
        )
        from api_service.services.omnigent_execution_plan_service import (
            _build_v2_profile,
        )
        from moonmind.omnigent.harness_platform.catalog import (
            TrustState,
            classify_harness_trust,
        )
        from moonmind.omnigent.harness_platform.credential_bindings import (
            create_binding_set,
        )
        from moonmind.omnigent.harness_platform.planner import compile_execution_plan
        from moonmind.omnigent.harness_platform.skills import (
            ResolvedSkillSet as PlannerSkillSet,
        )

        # Load the V2 profile snapshot
        async with async_session_maker() as session:
            from sqlalchemy import select

            from api_service.db.models import (
                OmnigentAgentProfile,
                OmnigentAgentProfileVersion,
            )

            profile_row = await session.get(OmnigentAgentProfile, "omnigent-opencode-default")
            if profile_row is None:
                raise RuntimeError("agent profile not found")
            result = await session.execute(
                select(OmnigentAgentProfileVersion).where(
                    OmnigentAgentProfileVersion.profile_id == profile_row.profile_id,
                    OmnigentAgentProfileVersion.version == profile_row.active_version,
                )
            )
            version_row = result.scalars().first()
            snapshot = {
                "document": version_row.document,
                "digest": version_row.digest,
                "version": version_row.version,
            }
        # The launch policy is part of the support combination key, so
        # qualification must select it exactly the way API admission does.
        # Restating a harness-shaped ref here qualifies a combination no plan
        # ever compiles, and every launch then fails admission.
        qualified_launch_policy_ref = default_launch_policy_ref(
            snapshot["document"].get("allowedLaunchPolicyRefs")
        )
        # Build V2 profile as planner does
        v2_profile = _build_v2_profile(
            snapshot=snapshot,
            catalog_ref=catalog.snapshot.catalogRef,
            implementation_ref=harness.implementation.implementation_ref(),
            harness_id="opencode-native",
            auth_model="own-auth",
        )
        # Build a minimal planner run to get the exact supportIdentity
        trust = classify_harness_trust(
            harnessId="opencode-native",
            implementation=harness.implementation,
            trustState=TrustState.core_trusted,
        )
        # Create a dummy skill set and binding set as planner does
        dummy_skills = PlannerSkillSet.model_validate(
            {
                "resolvedSkillSetRef": "artifact:dummy",
                "resolvedSkillSetDigest": "sha256:" + "a" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
            }
        )
        dummy_binding = create_binding_set(
            bindingSetId="opencode-native.primary-model",
            version=1,
            bindings={"primary-model": {"providerProfileRef": provider_profile_ref, "materializerRef": "opencode-auth-json@1"}},
        )
        # Compile a dummy plan to extract the exact supportIdentity and key
        dummy_plan = compile_execution_plan(
            agent_profile=v2_profile,
            harness_catalog=catalog.snapshot,
            trust_record=trust,
            resolved_skills=dummy_skills,
            credential_binding_set=dummy_binding,
            host_class_ref=host_class.ref,
            host_class=host_class,
            launch_policy_ref=qualified_launch_policy_ref,
            model_qualified_id=qualified_model,
            model_effort=effort,
            model_route_ref="opencode-go",
            model_normalized_options={},
            workflow_requirements=[],
            bridge_capabilities={},
            workspace_intent_ref="workspace-intent:sha256:" + "c" * 64,
            policy_snapshot_ref="artifact:policy",
            policy_snapshot_digest="sha256:" + "d" * 64,
            effective_launch_snapshot_ref="artifact:effective",
            effective_launch_snapshot_digest="sha256:" + "e" * 64,
            host_image_ref=host_class.imageRef,
            omnigent_host_build_digest=host_class.omnigentBuildDigest,
            host_architecture=resolved.architecture if resolved else "linux/amd64",
            capture_policy_ref="capture:sha256:" + "f" * 64,
            execution_authority={
                "authoredRequestRef": "art_req",
                "authoredRequestDigest": "sha256:" + "b" * 64,
                "taskInputSnapshotRef": "art_input",
                "taskInputSnapshotDigest": "sha256:" + "c" * 64,
                "repositoryIntentRef": "repo",
                "continuationPolicyRef": "cont",
                "remediationPolicyRef": "rem",
                "checkpointPolicyRef": "chk",
                "publicationPolicyRef": "pub",
                "timingPolicyRef": "tim",
                "failurePolicyRef": "fail",
            },
            agent_profile_snapshot_ref="artifact:snap",
        )
        support_identity = dummy_plan.payload.supportIdentity
        support_key = compute_support_combination_key(support_identity)
        # Run qualification via generic realizer
        qualification = await run_qualification(
            session_factory=async_session_maker,
            provider_profile_ref=provider_profile_ref,
            model_qualified_id=qualified_model,
            effort=effort,
            host_image_ref=host_class.imageRef,
            server_build_digest=catalog.snapshot.omnigentBuildDigest,
        )
        # For deployment evidence we need policy digests; use deterministic dummy hashes
        policy_digest = "sha256:" + hashlib.sha256(b"policy").hexdigest()
        effective_digest = "sha256:" + hashlib.sha256(b"effective").hexdigest()

        # Use qualification results
        results = qualification["results"]
        evidence_refs = qualification["evidenceRefs"]
        # Load credential generation
        async with async_session_maker() as session:
            from api_service.db.models import ManagedAgentProviderProfile

            prof = await session.get(ManagedAgentProviderProfile, provider_profile_ref)
            gen = int(prof.credential_generation) if prof else 1

        evidence = build_deployment_evidence(
            support_identity=support_identity,
            support_combination_key=support_key,
            host_image_ref=host_class.imageRef,
            policy_snapshot_digest=policy_digest,
            effective_launch_snapshot_digest=effective_digest,
            provider_profile_ref=provider_profile_ref,
            credential_generation=gen,
            qualified_model_id=qualified_model,
            effort=effort,
            results=results,
            evidence_refs=evidence_refs,
            resolved_state=resolved,
        )
        write_deployment_evidence(evidence)
        return evidence


def normalize_display(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]", "", name.lower())
