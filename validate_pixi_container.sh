#!/usr/bin/env bash
set -euo pipefail

container_root="${PIXI_CONTAINER_ROOT:-/opt/pixi-bundle}"
project_dir="${PIXI_BAKED_PROJECT:-/opt/pixi-project}"
env_root="$project_dir/.pixi/envs"
metadata_file="$container_root/.pixi-container/metadata.env"
expected_file="$container_root/.pixi-container/expected-envs.txt"

. "$metadata_file"

if [[ ! -d "$env_root" ]]; then
    echo "missing baked env root: $env_root" >&2
    exit 1
fi

echo "mode=$MODE"
echo "pixi=$(pixi --version | awk '{print $2}')"

mapfile -t actual_envs < <(find "$env_root" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort)
mapfile -t expected_envs < <(sort "$expected_file")

if [[ "${actual_envs[*]}" != "${expected_envs[*]}" ]]; then
    echo "expected baked envs: ${expected_envs[*]}" >&2
    echo "actual baked envs: ${actual_envs[*]}" >&2
    exit 1
fi

for env_name in "${expected_envs[@]}"; do
    env_prefix="$env_root/$env_name"
    python_version="$($env_prefix/bin/python -c 'import sys; print(sys.version.split()[0])')"
    echo "env=$env_name python=$python_version"
done
