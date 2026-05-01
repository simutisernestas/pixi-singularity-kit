#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./test/run-smoke-tests.sh
#   ./test/run-smoke-tests.sh --lima-instance apptainer-x86
#   ./test/run-smoke-tests.sh --backend local
#
# What it does:
#   - builds package-dev sample image at `test/package/package-dev.sif`
#   - builds pixi-project sample image at `test/experiment/experiment-pixi.sif`
#   - builds host-local path dependency sample image at `test/experiment/experiment-host-local-pixi.sif`
#   - uses kit-local `src/bin/pixi-container-build`
#
# Notes:
#   - builder uses `limactl` by default on macOS and local `apptainer` on Linux CI
#   - local backend verifies host-local path dependencies use live host source
#   - build logs are printed only on failure

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
kit_root="$(cd -- "$script_dir/.." && pwd)"
builder="$kit_root/src/bin/pixi-container-build"
package_image="$kit_root/test/package/package-dev.sif"
experiment_image="$kit_root/test/experiment/experiment-pixi.sif"
experiment_host_local_image="$kit_root/test/experiment/experiment-host-local-pixi.sif"
support_init="$kit_root/test/experiment/support_pkg/src/experiment_support/__init__.py"
support_cache="$kit_root/test/experiment/support_pkg/src/experiment_support/__pycache__"
lima_instance="apptainer-x86"
backend="auto"
log_dir="$(mktemp -d)"
original_support=""

cleanup() {
    if [[ -n "$original_support" && -f "$original_support" ]]; then
        cp "$original_support" "$support_init"
        rm -f "$original_support"
    fi
    rm -rf "$support_cache"
    rm -rf "$log_dir"
}
trap cleanup EXIT

run_quiet() {
    local label="$1"
    shift
    local log_file="$log_dir/${label//[^A-Za-z0-9_.-]/_}.log"

    printf 'running=%s\n' "$label"
    if "$@" >"$log_file" 2>&1; then
        printf 'ok=%s\n' "$label"
        return 0
    fi

    echo "failed=$label" >&2
    echo "log_tail=$log_file" >&2
    tail -n 200 "$log_file" >&2
    return 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --lima-instance)
            [[ $# -ge 2 ]] || { echo "missing value for --lima-instance" >&2; exit 2; }
            lima_instance="$2"
            shift 2
            ;;
        --backend)
            [[ $# -ge 2 ]] || { echo "missing value for --backend" >&2; exit 2; }
            backend="$2"
            shift 2
            ;;
        --help|-h)
            sed -n '3,13p' "$0"
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

rm -f "$package_image" "$experiment_image" "$experiment_host_local_image"

run_quiet package-dev-build "$builder" --backend "$backend" --lima-instance "$lima_instance" --output "$package_image" --manifest "$kit_root/test/package/pyproject.toml"
run_quiet pixi-project-build "$builder" --backend "$backend" --lima-instance "$lima_instance" --mode pixi-project --output "$experiment_image" --manifest "$kit_root/test/experiment/pixi.toml"
run_quiet host-local-path-deps-build "$builder" --backend "$backend" --lima-instance "$lima_instance" --mode pixi-project --host-local-path-deps --output "$experiment_host_local_image" --manifest "$kit_root/test/experiment/pixi.toml"

if [[ "$backend" == "local" ]]; then
    rm -rf "$support_cache"
    before="$(apptainer run --pwd "$kit_root/test/experiment" "$experiment_host_local_image" run_experiment.py)"
    if [[ "$before" != "experiment-host-v1" ]]; then
        echo "unexpected host-local output before edit: $before" >&2
        exit 1
    fi

    original_support="$(mktemp)"
    cp "$support_init" "$original_support"
    perl -0pi -e 's/experiment-host-v1/experiment-host-v2/' "$support_init"
    rm -rf "$support_cache"

    after="$(apptainer run --pwd "$kit_root/test/experiment" "$experiment_host_local_image" run_experiment.py)"
    if [[ "$after" != "experiment-host-v2" ]]; then
        echo "unexpected host-local output after edit: $after" >&2
        exit 1
    fi
fi

echo "built=$package_image"
echo "built=$experiment_image"
echo "built=$experiment_host_local_image"
