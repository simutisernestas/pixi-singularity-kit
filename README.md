# Pixi Singularity Kit

Reusable Apptainer/Singularity build kit for two cases:

- `package-dev`: current project has `pyproject.toml` with pixi config and host package should stay editable outside image
- `pixi-project`: current project has `pixi.toml` or pixi-enabled `pyproject.toml` and host scripts should run against baked pixi envs

Files:

- `bin/pixi-container-build` - executable host-side build command for `PATH`
- `pixi-container-build` - implementation resolved by `bin/pixi-container-build`
- `pixi-container.def` - generic Apptainer recipe template
- `run_pixi_container.sh` - runtime wrapper inside image
- `validate_pixi_container.sh` - image self-test
- `install.sh` - installs kit usage files into `/opt/pixi-singularity-kit`
- `test/run-smoke-tests.sh` - smoke runner that builds both sample `.sif` files

Common usage:

```bash
export PATH="/opt/pixi-singularity-kit/bin:$PATH"
pixi-container-build
pixi-container-build --backend local
pixi-container-build --env default --env format
pixi-container-build --mode pixi-project --output /tmp/my-job.sif
```

Install to `/opt`:

```bash
./install.sh
./install.sh /opt/pixi-singularity-kit
```

Runtime examples:

```bash
singularity run my-project-dev.sif python -m pytest
singularity run my-project-pixi.sif run_experiment.py
singularity run my-project-pixi.sif --pixi-env cuda python run_experiment.py
```

Smoke tests:

```bash
./test/run-smoke-tests.sh
./test/run-smoke-tests.sh --backend local
./test/run-smoke-tests.sh --lima-instance apptainer-x86
```

Notes:

- default build mode is auto-detect from current directory or nearest parent with manifest
- if no `--env` is passed, all pixi environments are baked
- builder uses `limactl start`, `limactl copy`, `limactl shell`, and guest-local `/tmp` build paths on macOS by default
- builder can use local `apptainer` directly with `--backend local`, which is how CI smoke tests run
- Pixi Singularity Kit is self-sufficient inside `container/pixi-container-kit/`; pixwake source is not used by kit itself
- sample fixtures live in `test/package/` and `test/experiment/`
- smoke runner generates `test/package/package-dev.sif` and `test/experiment/experiment-pixi.sif`
- Lima shared mount in this repo was stale for post-build host edits during verification; host file edits were not immediately visible from guest view until copied/synced into guest. Built image behavior itself validated using guest-local copied `.sif`.
