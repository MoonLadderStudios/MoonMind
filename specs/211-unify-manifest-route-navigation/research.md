# Research: Unify Manifest Route And Navigation

## Decision 1: Keep `/tasks/manifests` As The Canonical Route

**Decision**: Use `/tasks/manifests` as the single dashboard route for manifest operations.

**Rationale**: `docs/UI/ManifestsPage.md` defines `/tasks/manifests` as the canonical page and explicitly removes the separate `Manifest Submit` navigation item.

**Alternatives considered**:
- Keep `/tasks/manifests/new` as a second active page: rejected because it preserves the split workflow the story removes.
- Move manifest submission into `/tasks/new`: rejected because manifest operations already have a dedicated manifest-focused monitoring page.

## Decision 2: Redirect The Legacy Submit Route

**Decision**: Keep `/tasks/manifests/new` as an explicit HTTP 307 redirect to `/tasks/manifests`.

**Rationale**: Existing links or bookmarks should land on the unified manifest experience, while the main navigation and 404 guidance should advertise only the canonical route.

## Decision 3: Refresh Runs In Place After Submit

**Decision**: Invalidate the manifest run query after a successful submit and show the started workflow id on the current page.

**Rationale**: The canonical UI doc calls for keeping launch and monitoring together. Refreshing the same query preserves the existing list contract and avoids introducing optimistic state shape drift.

## Decision 4: Reuse Existing Manifest APIs

**Decision**: Continue to use `PUT /api/manifests/{name}` for inline YAML and `POST /api/manifests/{name}/runs` for both inline and registry runs.

**Rationale**: The story is route and navigation unification, not a manifest execution contract change.
