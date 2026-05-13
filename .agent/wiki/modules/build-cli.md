# Build CLI

- File: `src/pixi-container-build.py`
- Entrypoint shim: `src/bin/pixi-container-build`
- Wiki commit: `f7f2984deac0dcb2e82026f9703f45a0cc9e0ff9`

## Role

Single host-side orchestrator. Detect manifest and mode, stage bundle, resolve caches, choose backend, reuse or build cached stages, run container self-test, clean temp dirs.

## Important Functions

- `parse_args()`: CLI flags for mode, manifest, env selection, output, backend, Lima instance, debug temp retention, host-local deps
- `find_manifest()`: walks upward from cwd to locate valid Pixi manifest
- `is_package_dev_manifest()`: recognizes `package-dev` projects
- `list_environments()` and `normalize_selected_envs()`: select baked envs
- `collect_local_path_roots()`: gathers local path `pypi-dependencies`
- `strip_self_path_dependencies()`: removes self editable dep for `package-dev` staging
- `add_build_requirements()`: adds `pip`, `editables`, build-system deps to staged manifest
- `stage_bundle()`: copies manifest, selected local deps, optional lock/map files, metadata, expected env list into tarball
- `resolve_cache_dirs()`: creates stable cache layout per backend
- `hash_tree()`: hashes canonical staged bundle tree for env cache keying
- `build_base_key()` / `build_env_key()`: derive deterministic stage keys
- `render_definition()`: fills template placeholders for stage recipes
- `build_local_stage()` / `build_guest_stage()`: run `apptainer build` with persistent cache env and optional binds
- `test_local_image()` / `test_guest_image()`: run `apptainer test` on cached env stage
- `main()`: top-level flow and backend branching

## CLI Surface

```text
--mode {auto,package-dev,pixi-project}
--manifest PATH
--env NAME             # repeatable, comma-separated accepted
--output PATH
--lima-instance NAME   # default apptainer-x86
--backend {auto,lima,local}
--keep-build-dir
--keep-guest-dir
--host-local-path-deps
```

## Backend Rules

- `auto` on macOS -> `lima`
- `auto` on non-macOS with `apptainer` in `PATH` -> `local`
- else if `limactl` exists -> `lima`
- else fail

## Cache Layout

- root: `~/.cache/pixi-container-kit/<backend>/`
- Lima uses same path shape inside guest home directory
- `apptainer/`: `APPTAINER_CACHEDIR`
- `tmp/`: `APPTAINER_TMPDIR`
- `pixi/`: persistent Pixi download cache bound during env builds
- `stages/base/`: cached base SIFs plus JSON metadata
- `stages/env/`: cached env SIFs plus JSON metadata

## Cache Invalidation

- base key: architecture plus `src/pixi-container-base.def` content hash
- env key: architecture, base key, staged bundle tree hash, bind target path, and `src/pixi-container-env.def` content hash
- identical inputs print `base_cache=hit` and `env_cache=hit`

## Inputs Carried Into Bundle

- selected manifest
- local path dependencies from Pixi config
- optional `workspace.conda-pypi-map` files if present
- `pixi.lock` for non-`package-dev` mode
- `.pixi-container/metadata.env`
- `.pixi-container/expected-envs.txt`
- `.pixi-container/host-local-paths.txt`

## Exclusions

Staging skips names like `.git`, `.pixi`, `.venv`, `.agent`, caches, `node_modules`, and artifacts matching `*.sif`, `*.sqsh`, `*.img`, `*.pyc`, `*.pyo`.
