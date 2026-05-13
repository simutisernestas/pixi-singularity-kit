# Build Flow

- Primary source: `src/pixi-container-build.py`
- Wiki commit: `f7f2984deac0dcb2e82026f9703f45a0cc9e0ff9`

## Sequence

1. Parse CLI args.
2. Resolve manifest and final mode.
3. Load Pixi config, project name, available envs, selected envs.
4. Choose backend: local `apptainer` or Lima guest.
5. Resolve stable cache dirs for `apptainer`, `tmp`, `pixi`, `stages/base`, `stages/env`.
6. For `package-dev`, generate sanitized manifest copy.
7. Stage bundle tarball with manifest, local path deps, metadata, expected env list, optional lock/map files.
8. Hash staged bundle tree and compute base/env cache keys.
9. Reuse or build cached base stage.
10. Reuse or build cached env stage from cached base stage.
11. Run `apptainer test` on cached env stage.
12. Copy cached env SIF to requested output path.
13. Clean temp dirs unless debug keep flags set.

## Local Backend

1. Set `APPTAINER_CACHEDIR` and `APPTAINER_TMPDIR` from stable cache root.
2. Build base stage on miss from `src/pixi-container-base.def`.
3. Build env stage on miss from `src/pixi-container-env.def`, binding Pixi cache dir to `/mnt/pixi-cache`.
4. Run `apptainer test <cached-env-stage>`.
5. Copy cached env stage to final output path.

## Lima Backend

1. Ensure Lima instance started.
2. Create guest temp dir under `/tmp`.
3. Resolve cache dirs inside guest home filesystem.
4. Copy bundle and scripts into guest.
5. Render base/env recipes with guest paths, copy recipes into guest.
6. Build base stage on miss with `sudo -E apptainer build`.
7. Build env stage on miss with `sudo -E apptainer build --bind <pixi-cache>:/mnt/pixi-cache`.
8. Run `apptainer test` on cached env stage in guest.
9. Copy cached env stage back to host output path.
10. Remove guest temp dir unless keep flag set.
11. Stop Lima instance.
