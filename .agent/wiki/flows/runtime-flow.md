# Runtime Flow

- Primary source: `src/run_pixi_container.sh`
- Wiki commit: `f7f2984cf0bae6e69c5f22b8b86d5ab8764ec88a`

## Sequence

1. Load container metadata from `/opt/pixi-bundle/.pixi-container/metadata.env`.
2. Parse runtime args like `--pixi-env` and `--project`.
3. Resolve baked env path under `/opt/pixi-project/.pixi/envs/<env>`.
4. If mode needs host source paths, resolve host project directory.
5. Export runtime env vars: `PATH`, `CONDA_PREFIX`, `PIXI_ENV`.
6. Prepend host source paths to `PYTHONPATH` depending on mode.
7. Execute:
   - `*.py` with baked env Python
   - `python` with baked env Python
   - anything else directly

## Mode Differences

### `package-dev`

- host project required
- host project root and `src/` injected into `PYTHONPATH`
- recorded local path deps also injected into `PYTHONPATH`

### `pixi-project` without host-local deps

- no host project resolution required
- current working directory and `src/` may be added when present

### `pixi-project` with `--host-local-path-deps`

- host project required
- only recorded local path deps overlay host source; main project stays baked unless command uses host files directly
