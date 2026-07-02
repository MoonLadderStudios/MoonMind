# MoonSpec Bundle Integration

MoonSpec workflow assets are sourced from the root-level `moonspec` git submodule.
MoonMind keeps the current runtime paths stable by linking files from the pinned
bundle revision into the paths the application already reads.

## Source Of Truth

Edit MoonSpec workflow behavior through the `moonspec` submodule, not by
treating projected MoonMind paths as independent copies. The projected paths are
file-level symbolic links from:

- `moonspec/bundle/skills/` to `.agents/skills/`
- `moonspec/bundle/templates/` to `.specify/templates/`
- `moonspec/bundle/scripts/bash/` to `.specify/scripts/bash/`
- `moonspec/bundle/presets/moonspec-orchestrate.yaml` to
  `api_service/data/presets/moonspec-orchestrate.yaml`
- `moonspec/bundle/docs/MoonSpecDocumentModel.md` to
  `docs/Workflows/MoonSpecDocumentModel.md`
- `moonspec/bundle/commands/gemini/` to `.gemini/commands/`

MoonMind owns the submodule pointer, projection script, runtime preset seeding,
workflow scheduling, database/API/UI behavior, and MoonMind-specific tests.

## Local Setup

After cloning MoonMind, initialize submodules:

```bash
git submodule update --init --recursive
```

CI checks out submodules and verifies that `moonspec/bundle/moonspec.bundle.yaml`
exists.

## Projection

Check the symlink projection:

```bash
python3 tools/link_moonspec_submodule.py --check --prune
```

Refresh symlinks after bumping or initializing the submodule:

```bash
python3 tools/link_moonspec_submodule.py --write --prune
```

When converting an older checkout that still contains generated MoonSpec copies,
replace only those generated files explicitly:

```bash
python3 tools/link_moonspec_submodule.py --write --replace-generated --prune
```

Projected files should be tracked as individual symlinks that resolve under
`moonspec/bundle/`. They should not contain MoonMind-injected generated headers
unless that text exists in the MoonSpec source file itself. The previous
generated-copy projector, `tools/sync_moonspec_submodule.py`, is deprecated and
is not the canonical projection mechanism.

## Bumping MoonSpec

1. Update the MoonSpec repository and merge the upstream MoonSpec PR.
2. In MoonMind, update `moonspec` to the desired MoonSpec commit.
3. Run `python3 tools/link_moonspec_submodule.py --write --prune`.
4. Run `python3 tools/link_moonspec_submodule.py --check --prune`.
5. Run the targeted MoonMind tests for projection, preset seeding, scheduling,
   and any touched runtime/API/UI surface.
