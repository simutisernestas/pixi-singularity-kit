# Overview

- Repo type: small build kit, mostly Python + shell
- Wiki commit: `f7f2984deac0dcb2e82026f9703f45a0cc9e0ff9`

## Architecture

Host side builder prepares staged project bundle, computes cache keys, then builds image either:
- locally with `apptainer`
- in Lima guest with `limactl` on macOS or when chosen explicitly

Built image contains:
- Pixi installation under `/opt/pixi`
- staged project bundle under `/opt/pixi-bundle`
- symlinked project root at `/opt/pixi-project`
- one or more baked Pixi envs under `/opt/pixi-project/.pixi/envs/<env>`

Builder-managed cache contains:
- Apptainer download cache
- Apptainer temp dir
- Pixi download cache bound into env builds at `/mnt/pixi-cache`
- cached base-stage SIFs keyed by base template plus architecture
- cached env-stage SIFs keyed by staged bundle hash plus base key

Runtime entrypoint is container `%runscript`, which calls `/opt/run_pixi_container.sh`.

## Main Modes

### `package-dev`

- Requires `pyproject.toml` with both `[project]` and `[tool.pixi]`
- Builder strips self path dependency from staged manifest
- Builder injects `pip`, `editables`, and build-system requirements into staged manifest
- Runtime prepends host project source to `PYTHONPATH`

### `pixi-project`

- Uses `pixi.toml` or pixi-enabled `pyproject.toml`
- Bakes selected envs for running host scripts or commands inside image
- If `pixi.lock` exists, image build uses `pixi install --frozen`
- Optional `--host-local-path-deps` still stages local deps for solve/install, then overlays host source paths at runtime

## Validation

- `apptainer test` runs `src/validate_pixi_container.sh`
- smoke script builds sample images for both modes
- local backend smoke also edits host dependency source and confirms host-local-path overlay changes runtime output
- repeated identical builds print `base_cache=hit` and `env_cache=hit` and reuse stage SIFs

## Sharp Edges

- `--manifest` must point to existing `pyproject.toml` or `pixi.toml`
- unknown `--env` names fail fast
- absolute local path deps outside workspace are rejected
- host-local-path mode depends on host source being discoverable at runtime
- Lima flow copies image into guest `/tmp`, then back out after build
- warm Lima runs still pay VM start/stop cost even when stage cache hits
