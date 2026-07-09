# MoonSpec Bundle Integration

MoonSpec workflow assets are sourced from the root-level `moonspec` git
submodule and vendored into the repository as real files. The submodule
gitlink pins the exact bundle revision the vendored files came from; the
vendored copies are what every runtime path reads, so no runtime, image
build, or agent workspace depends on submodule checkout state or filesystem
symlink support.

## Source Of Truth

Edit MoonSpec workflow behavior in the MoonSpec repository, not by editing
the vendored copies. The projection recipe lives in the bundle
(`moonspec/bundle/projections/moonmind.yaml`) and maps:

- `moonspec/bundle/skills/` to `.agents/skills/`
- `moonspec/bundle/templates/` to `.specify/templates/`
- `moonspec/bundle/commands/markdown/` to `.specify/templates/commands/`
- `moonspec/bundle/scripts/bash/` to `.specify/scripts/bash/`
- `moonspec/bundle/presets/moonspec-orchestrate.yaml` to
  `api_service/data/presets/moonspec-orchestrate.yaml`
- `moonspec/bundle/docs/MoonSpecDocumentModel.md` to
  `docs/Workflows/MoonSpecDocumentModel.md`
- `moonspec/bundle/commands/gemini/` to `.gemini/commands/`

Vendored copies are byte-identical to their bundle sources — no injected
headers — so hand edits are detected as drift rather than silently diverging.

MoonMind owns the submodule pointer, the sync tool, runtime preset seeding,
workflow scheduling, database/API/UI behavior, and MoonMind-specific tests.

## Sync Tool

`tools/sync_moonspec.py` is the canonical projector:

```bash
python3 tools/sync_moonspec.py --check   # report drift, exit 1 if any
python3 tools/sync_moonspec.py --write   # vendor files and prune stale ones
```

`--write` copies every mapped bundle file into the repository, replaces any
legacy symlinks with real files, and prunes stale projection-managed files:
`moonspec-*` skill directories, `moonspec.*` command files, everything under
the MoonSpec-owned `.specify` directories, and the recipe's
`unexpectedLegacy` patterns. Repo-native skills and non-MoonSpec command
files are never touched. The tool refuses source or target path escapes and
fails fast when a projection recipe adds a directory mapping whose target
ownership has not been classified in `DIRECTORY_OWNERSHIP`.

The sync tool requires the submodule only when it runs:

```bash
git submodule update --init moonspec
```

Two guards keep the vendored copies honest, both requiring a submodule
checkout at the pinned commit:

- the `moonspec-projection` CI job runs `tools/sync_moonspec.py --check`;
- `tests/unit/tools/test_sync_moonspec.py` asserts the committed projection
  matches the pinned bundle.

## Bumping MoonSpec

1. Update the MoonSpec repository and merge the upstream MoonSpec PR.
2. In MoonMind, update `moonspec` to the desired MoonSpec commit.
3. Run `python3 tools/sync_moonspec.py --write`.
4. Commit the submodule bump together with the vendored file changes.
5. Run the targeted MoonMind tests for projection, preset seeding,
   scheduling, and any touched runtime/API/UI surface.
