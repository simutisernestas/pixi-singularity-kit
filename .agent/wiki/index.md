# Repo Wiki

- Repo: `pixi-container-kit`
- Wiki commit: `f7f2984deac0dcb2e82026f9703f45a0cc9e0ff9`

## Purpose

Build reusable Apptainer/Singularity images for Pixi projects.

Supported modes:
- `package-dev`: bake Pixi envs, keep host package editable through runtime `PYTHONPATH`
- `pixi-project`: bake Pixi envs for running host scripts against container-managed envs

## Main Files

- `src/bin/pixi-container-build`: host CLI shim
- `src/pixi-container-build.py`: main builder implementation
- `src/pixi-container-base.def`: cached Apptainer base stage template
- `src/pixi-container-env.def`: cached Apptainer env stage template
- `src/run_pixi_container.sh`: runtime wrapper inside image
- `src/validate_pixi_container.sh`: image self-test
- `install.sh`: installs kit files into target prefix
- `test/run-smoke-tests.sh`: builds and validates sample images
- `.github/workflows/smoke-tests.yml`: CI smoke job on Ubuntu

## Repo Layout

- `src/`: builder, recipe, runtime scripts
- `test/package/`: `package-dev` sample fixture
- `test/experiment/`: `pixi-project` sample fixture, plus host-local-path-deps scenario
- `.github/workflows/`: CI workflow

## Common Commands

Install kit:

```bash
./install.sh "$HOME/.local/pixi-singularity-kit"
./install.sh /opt/pixi-singularity-kit
```

Use builder:

```bash
export PATH="/opt/pixi-singularity-kit/bin:$PATH"
pixi-container-build
pixi-container-build --backend local
pixi-container-build --env default --env format
pixi-container-build --mode pixi-project --output /tmp/my-job.sif
pixi-container-build --mode pixi-project --host-local-path-deps --output /tmp/my-job.sif
```

Run smoke tests:

```bash
./test/run-smoke-tests.sh
./test/run-smoke-tests.sh --backend local
./test/run-smoke-tests.sh --lima-instance apptainer-x86
```

## Platform Rules

- Host needs `bash`
- Host needs `python3` 3.11+ because builder uses `tomllib`
- Image build needs outbound network for Ubuntu packages and Pixi install
- macOS defaults to Lima backend
- Linux defaults to local `apptainer` when available
- Reusable build cache lives under `~/.cache/pixi-container-kit/<backend>/`; for Lima this path is inside guest home filesystem

## Pages

- `overview.md`
- `modules/build-cli.md`
- `modules/runtime-scripts.md`
- `modules/test-fixtures.md`
- `flows/build-flow.md`
- `flows/runtime-flow.md`

## Gaps

- No separate packaging manifest for repo itself at root
- No unit-test suite; validation centered on smoke builds and container self-test
