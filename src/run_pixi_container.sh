#!/usr/bin/env bash
set -euo pipefail

container_root="${PIXI_CONTAINER_ROOT:-/opt/pixi-bundle}"
project_dir="${PIXI_BAKED_PROJECT:-/opt/pixi-project}"
metadata_file="$container_root/.pixi-container/metadata.env"
env_name="${PIXI_ENV:-default}"
host_project="${PIXI_HOST_PROJECT:-}"

. "$metadata_file"

print_help() {
    cat <<'EOF'
Usage:
  singularity run image.sif [--pixi-env NAME] [--project DIR] <script.py> [args...]
  singularity run image.sif [--pixi-env NAME] [--project DIR] python <args...>
  singularity run image.sif [--pixi-env NAME] [--project DIR] <command> [args...]

Options:
  --pixi-env NAME     Select baked pixi environment.
  --project DIR       Host project root. Needed for package-dev mode if auto-detect misses.
  --help, -h          Show this help.

Behavior:
  - `--pixi-env` defaults to `default`.
  - `script.py` and `python ...` use baked interpreter from selected env.
  - `package-dev` mode imports host package through `PYTHONPATH` only.
  - host-local path deps import host source through `PYTHONPATH` only.
  - `$PWD` and optional `$PWD/src` are prepended to `PYTHONPATH`.
EOF
}

available_envs() {
    local env_root="$project_dir/.pixi/envs"
    local first=1
    local env_dir
    for env_dir in "$env_root"/*; do
        if [[ -d "$env_dir" ]]; then
            if [[ $first -eq 0 ]]; then
                printf ', '
            fi
            printf '%s' "$(basename "$env_dir")"
            first=0
        fi
    done
    printf '\n'
}

search_up_for_manifest() {
    local start_dir="$1"
    local current="$start_dir"
    while [[ -n "$current" && -d "$current" ]]; do
        if [[ -f "$current/$SOURCE_MANIFEST_NAME" ]]; then
            printf '%s\n' "$current"
            return 0
        fi
        if [[ "$current" == "/" ]]; then
            break
        fi
        current="$(dirname "$current")"
    done
    return 1
}

prepend_python_path() {
    local path="$1"
    if [[ -d "$path" ]]; then
        export PYTHONPATH="$path${PYTHONPATH:+:$PYTHONPATH}"
    fi
}

prepend_host_source_path() {
    local source_path="$1"
    prepend_python_path "$source_path"
    prepend_python_path "$source_path/src"
}

apply_host_local_path_deps() {
    local paths_file="$container_root/.pixi-container/host-local-paths.txt"
    local rel_path
    [[ -f "$paths_file" ]] || return 0
    while IFS= read -r rel_path; do
        [[ -n "$rel_path" ]] || continue
        prepend_host_source_path "$host_project/$rel_path"
    done < "$paths_file"
}

container_image_dir() {
    local image_path="${APPTAINER_CONTAINER:-${SINGULARITY_CONTAINER:-}}"
    if [[ -n "$image_path" ]]; then
        dirname "$image_path"
    fi
}

resolve_host_project() {
    local candidate=""
    if [[ -n "$host_project" ]]; then
        candidate="$host_project"
    else
        candidate="$(search_up_for_manifest "$PWD" || true)"
        if [[ -z "$candidate" ]]; then
            candidate="$(container_image_dir || true)"
            if [[ -n "$candidate" ]]; then
                candidate="$(search_up_for_manifest "$candidate" || true)"
            fi
        fi
    fi

    if [[ -n "$candidate" && -f "$candidate/$SOURCE_MANIFEST_NAME" ]]; then
        printf '%s\n' "$candidate"
        return 0
    fi

    cat >&2 <<EOF
Cannot locate host project for runtime host source paths.
Run from project root or subdir, place image next to project, or pass --project /path/to/project.
Expected manifest: $SOURCE_MANIFEST_NAME
EOF
    return 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pixi-env)
            [[ $# -ge 2 ]] || { echo "missing value for --pixi-env" >&2; exit 2; }
            env_name="$2"
            shift 2
            ;;
        --project)
            [[ $# -ge 2 ]] || { echo "missing value for --project" >&2; exit 2; }
            host_project="$2"
            shift 2
            ;;
        --help|-h)
            print_help
            echo "Mode: $MODE"
            echo "Available baked environments: $(available_envs)"
            exit 0
            ;;
        --)
            shift
            break
            ;;
        *)
            break
            ;;
    esac
done

env_prefix="$project_dir/.pixi/envs/$env_name"
if [[ ! -x "$env_prefix/bin/python" ]]; then
    echo "baked pixi environment '$env_name' not found" >&2
    echo "Available baked environments: $(available_envs)" >&2
    exit 1
fi

if [[ $# -eq 0 ]]; then
    print_help
    echo "Mode: $MODE"
    echo "Available baked environments: $(available_envs)"
    exit 2
fi

if [[ "$MODE" == "package-dev" || "${HOST_LOCAL_PATH_DEPS:-0}" == "1" ]]; then
    host_project="$(resolve_host_project)"
fi

export PATH="$env_prefix/bin:$PATH"
export CONDA_PREFIX="$env_prefix"
export PIXI_ENV="$env_name"

if [[ "$MODE" == "package-dev" ]]; then
    export PIXI_HOST_PROJECT="$host_project"
    export PIXI_CONTAINER_RUNTIME_MODE="package-dev-pythonpath"
    prepend_host_source_path "$host_project"
    apply_host_local_path_deps
elif [[ "${HOST_LOCAL_PATH_DEPS:-0}" == "1" ]]; then
    export PIXI_HOST_PROJECT="$host_project"
    export PIXI_CONTAINER_RUNTIME_MODE="pixi-project-host-local-path-deps"
    apply_host_local_path_deps
elif [[ -d "$PWD" ]]; then
    prepend_host_source_path "$PWD"
fi

cmd="$1"
shift
case "$cmd" in
    *.py)
        exec "$env_prefix/bin/python" "$cmd" "$@"
        ;;
    python)
        exec "$env_prefix/bin/python" "$@"
        ;;
    *)
        exec "$cmd" "$@"
        ;;
esac
