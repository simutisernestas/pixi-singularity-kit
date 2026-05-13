# Runtime Scripts

- Files: `src/run_pixi_container.sh`, `src/validate_pixi_container.sh`, `src/pixi-container-base.def`, `src/pixi-container-env.def`
- Wiki commit: `f7f2984deac0dcb2e82026f9703f45a0cc9e0ff9`

## `run_pixi_container.sh`

Role:
- parse runtime flags
- pick baked env
- resolve host project when runtime needs host source paths
- set `PATH`, `CONDA_PREFIX`, `PIXI_ENV`
- execute Python script, `python`, or arbitrary command

Runtime options:
- `--pixi-env NAME`
- `--project DIR`
- `--help`

Host source overlay rules:
- `package-dev`: prepend host project root and `src/` to `PYTHONPATH`
- `--host-local-path-deps`: prepend each recorded host local dependency path and `src/`
- fallback non-overlay mode: prepend `$PWD` and `$PWD/src` when present

Host project discovery order:
- explicit `--project`
- search upward from current working directory for source manifest name
- search near container image path
- fail with guidance if still unresolved

## `validate_pixi_container.sh`

Checks:
- baked env root exists
- actual env directories exactly match expected env list
- `pixi --version` available
- each baked env has working Python executable

This script is image `%test` target and runs during `apptainer test`.

## `pixi-container-base.def`

Recipe behavior:
- base image `ubuntu:24.04`
- install minimal OS packages plus Pixi
- create `/mnt/pixi-cache` bind target for later env builds

## `pixi-container-env.def`

Recipe behavior:
- unpack staged bundle into `/opt/pixi-bundle`
- symlink project dir to `/opt/pixi-project`
- set `PIXI_CACHE_DIR=/mnt/pixi-cache`
- run `pixi install` once per expected env
- use `--frozen` only when metadata says lockfile-backed `pixi-project`
- install runscript and validator into `/opt`

Container entrypoints:
- `%runscript` -> `/opt/run_pixi_container.sh`
- `%test` -> `/opt/validate_pixi_container.sh`
