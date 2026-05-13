# Pixi Singularity Kit

Reusable Apptainer/Singularity build kit for two cases:

- `package-dev`: current project has `pyproject.toml` with pixi config and host package should stay editable outside image
- `pixi-project`: current project has `pixi.toml` or pixi-enabled `pyproject.toml` and host scripts should run against baked pixi envs

Files:

- `src/bin/pixi-container-build` - executable host-side build command for `PATH`
- `src/pixi-container-build.py` - implementation resolved by `src/bin/pixi-container-build`
- `src/pixi-container-base.def` - cached Apptainer base stage template
- `src/pixi-container-env.def` - cached Apptainer env stage template
- `src/run_pixi_container.sh` - runtime wrapper inside image
- `src/validate_pixi_container.sh` - image self-test
- `install.sh` - installs kit usage files into `/opt/pixi-singularity-kit`
- `test/run-smoke-tests.sh` - smoke runner that builds both sample `.sif` files

Host setup:

Shared requirements:

- host needs `bash`
- host needs `python3` 3.11+ because builder imports stdlib `tomllib`
- host needs outbound network during image builds for Ubuntu packages and Pixi install

macOS:

```bash
brew install lima
limactl start --yes --name=apptainer-x86 template:apptainer
limactl shell apptainer-x86 apptainer --version
python3 --version
```

- builder defaults to `--backend lima` on macOS
- create Lima Apptainer instance once before first build; later builds only start and stop it by name
- default instance name is `apptainer-x86`; if you use different name, pass `--lima-instance <name>`
- macOS host does not need local `apptainer`; builder runs it inside Lima guest

Ubuntu/Debian:

- use release where `python3 --version` reports 3.11+; Ubuntu 24.04 and Debian 12 match this

```bash
python3 --version
sudo apt-get update
sudo apt-get install -y wget squashfs-tools uidmap fuse2fs fakeroot fuse-overlayfs libseccomp-dev cryptsetup-bin runc
wget -q https://github.com/apptainer/apptainer/releases/download/v1.2.5/apptainer_1.2.5_amd64.deb
wget -q https://github.com/apptainer/apptainer/releases/download/v1.2.5/apptainer-suid_1.2.5_amd64.deb
sudo apt-get install -y ./apptainer_1.2.5_amd64.deb ./apptainer-suid_1.2.5_amd64.deb
apptainer --version
```

- builder defaults to `--backend local` on Linux when `apptainer` is in `PATH`
- smoke tests use same backend as CI: `sudo ./test/run-smoke-tests.sh --backend local`
- example `.deb` names above are `amd64`, matching CI; swap release artifacts for other architectures

Install to writable path or `/opt`:

```bash
./install.sh "$HOME/.local/pixi-singularity-kit"
./install.sh /opt/pixi-singularity-kit
```

- installer prints exact `export PATH=...` line for chosen target
- `/opt` usually needs `sudo`

Common usage:

```bash
export PATH="/opt/pixi-singularity-kit/bin:$PATH" # adjust if installed elsewhere
pixi-container-build
pixi-container-build --backend local
pixi-container-build --env default --env format
pixi-container-build --mode pixi-project --output /tmp/my-job.sif
pixi-container-build --mode pixi-project --host-local-path-deps --output /tmp/my-job.sif
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
- `--host-local-path-deps` still stages local path packages during image build so pixi can resolve and install their dependencies, then prepends host source paths at runtime through `PYTHONPATH`
- host-local path deps still rely on host source at runtime; compiled extensions and console scripts come from baked install, not live host source
- builder uses `limactl start`, `limactl copy`, `limactl shell`, guest-local `/tmp` build paths, and `limactl stop` on macOS by default
- builder can use local `apptainer` directly with `--backend local`, which is how CI smoke tests run
- builder keeps reusable caches under `~/.cache/pixi-container-kit/<backend>/` for Apptainer downloads, Pixi downloads, and cached base/env stage SIFs
- base stage rebuilds when `src/pixi-container-base.def` changes or target architecture changes; env stage rebuilds when staged manifest inputs, selected envs, base key, or `src/pixi-container-env.def` change
- sample fixtures live in `test/package/` and `test/experiment/`
