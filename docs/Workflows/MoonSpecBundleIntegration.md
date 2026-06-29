# MoonSpec Bundle Integration

MoonSpec workflow assets are sourced from the `vendor/moonspec` git submodule.
MoonMind keeps the current runtime paths stable by projecting the pinned bundle
revision into the paths the application already reads.

## Source Of Truth

Edit MoonSpec workflow behavior in the MoonSpec repository, not in projected
MoonMind files. The projected paths are generated from:

- `vendor/moonspec/bundle/skills/` to `.agents/skills/`
- `vendor/moonspec/bundle/templates/` to `.specify/templates/`
- `vendor/moonspec/bundle/scripts/bash/` to `.specify/scripts/bash/`
- `vendor/moonspec/bundle/presets/moonspec-orchestrate.yaml` to
  `api_service/data/presets/moonspec-orchestrate.yaml`
- `vendor/moonspec/bundle/docs/MoonSpecDocumentModel.md` to
  `docs/Workflows/MoonSpecDocumentModel.md`
- `vendor/moonspec/bundle/commands/gemini/` to `.gemini/commands/`

MoonMind owns the submodule pointer, projection script, runtime preset seeding,
workflow scheduling, database/API/UI behavior, and MoonMind-specific tests.

## Local Setup

After cloning MoonMind, initialize submodules:

```bash
git submodule update --init --recursive
```

CI checks out submodules and verifies that `vendor/moonspec/bundle/moonspec.bundle.yaml`
exists.

## Projection

Check committed projections:

```bash
python3 tools/sync_moonspec_submodule.py --check
```

Refresh committed projections after bumping the submodule:

```bash
python3 tools/sync_moonspec_submodule.py --write
```

Projected files include a generated header. Direct edits to those files should
be moved upstream into MoonSpec, then re-projected into MoonMind from the pinned
submodule revision.

## Bumping MoonSpec

1. Update the MoonSpec repository and merge the upstream MoonSpec PR.
2. In MoonMind, update `vendor/moonspec` to the desired MoonSpec commit.
3. Run `python3 tools/sync_moonspec_submodule.py --write`.
4. Run `python3 tools/sync_moonspec_submodule.py --check`.
5. Run the targeted MoonMind tests for projection, preset seeding, scheduling,
   and any touched runtime/API/UI surface.
