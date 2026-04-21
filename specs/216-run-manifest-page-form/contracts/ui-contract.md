# UI Contract: Run Manifest Page Form

## Surface

Route: `/tasks/manifests`

Primary user-visible regions:
- `Manifests` page heading
- `Run Manifest` form
- `Recent Runs` list/table

## Controls

- `Source Kind`: select or equivalent accessible source-mode control with `Registry Manifest` and `Inline YAML`.
- `Registry Manifest Name`: required in Registry Manifest mode.
- `Manifest Name`: required in Inline YAML mode.
- `Inline YAML`: required in Inline YAML mode.
- `Action`: required supported action selector.
- `Advanced options`: collapsed by default.
- `Dry Run`, `Force Full`, `Max Docs`: supported advanced options.
- `Run Manifest`: primary submit action, visible without opening advanced options.

## Validation Contract

Before any `PUT /api/manifests/{name}` or `POST /api/manifests/{name}/runs` request:
- Registry mode rejects blank registry manifest names.
- Inline mode rejects blank manifest names.
- Inline mode rejects blank inline YAML.
- Nonblank `Max Docs` rejects non-positive or non-integer values.
- Raw secret-shaped values in manifest content or helper fields are rejected with guidance to use env/Vault references.

## Submission Contract

Registry mode:
- Sends `POST /api/manifests/{registryName}/runs`.
- Does not send `PUT /api/manifests/{registryName}`.

Inline mode:
- Sends `PUT /api/manifests/{manifestName}` with `{ "content": "<inline YAML>" }`.
- Sends `POST /api/manifests/{manifestName}/runs`.

Successful submit:
- Keeps the user on `/tasks/manifests`.
- Displays a success notice.
- Exposes a run detail link when the response includes one.
- Refreshes recent manifest runs.
