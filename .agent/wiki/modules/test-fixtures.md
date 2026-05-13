# Test Fixtures

- Files: `test/run-smoke-tests.sh`, `test/package/*`, `test/experiment/*`
- Wiki commit: `f7f2984cf0bae6e69c5f22b8b86d5ab8764ec88a`

## Smoke Runner

`test/run-smoke-tests.sh` builds three images:
- `test/package/package-dev.sif`
- `test/experiment/experiment-pixi.sif`
- `test/experiment/experiment-host-local-pixi.sif`

It uses repo-local builder at `src/bin/pixi-container-build`.

Arguments:
- `--backend local|lima|auto`
- `--lima-instance NAME`

Behavior:
- build logs stay quiet unless failure
- cleanup restores edited support package file and removes temp logs/cache
- local backend additionally verifies host-local-path dependency overlay by editing source from `experiment-host-v1` to `experiment-host-v2` and rerunning container

## `package-dev` Fixture

- manifest: `test/package/pyproject.toml`
- package source: `test/package/src/kit_package_demo/`
- runner: `test/package/print_message.py`

Purpose: validate editable self package flow with Pixi-enabled `pyproject.toml`.

## `pixi-project` Fixture

- manifest: `test/experiment/pixi.toml`
- runner: `test/experiment/run_experiment.py`
- local dependency: `test/experiment/support_pkg/`

Purpose: validate standard Pixi project image and host-local-path dependency mode.
