# Requirements Traceability: Unify Manifest Route And Navigation

| Requirement | Implementation Target | Validation |
| --- | --- | --- |
| FR-001 | `api_service/templates/_navigation.html` | `test_navigation_exposes_single_manifest_destination` |
| FR-002 | `api_service/api/routers/task_dashboard.py` | `test_legacy_manifest_submit_route_redirects_to_unified_manifests_page` |
| FR-003 | `frontend/src/entrypoints/manifests.tsx` | `Manifests Entrypoint renders manifest submission and recent runs on the same page` |
| FR-004 | `frontend/src/entrypoints/manifests.tsx` | `Manifests Entrypoint upserts inline manifests...`; `runs registry manifests...` |
| FR-005 | `frontend/src/entrypoints/manifests.tsx` | `Manifests Entrypoint upserts inline manifests...refreshes recent runs in place` |
| FR-006 | `frontend/src/entrypoints/manifests.tsx` | Advanced options use a closed `details` element by default |
| FR-007 | `frontend/src/entrypoints/manifests.tsx`; existing manifest APIs | No secret fields are added; focused tests use existing endpoint shapes |
| FR-008 | `api_service/api/routers/task_dashboard.py` | `test_invalid_dashboard_route_returns_404` |
