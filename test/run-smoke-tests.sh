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

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
kit_root="$(cd -- "$script_dir/.." && pwd)"
builder="$kit_root/src/bin/pixi-container-build"
package_image="$kit_root/test/package/package-dev.sif"
experiment_image="$kit_root/test/experiment/experiment-pixi.sif"
experiment_host_local_image="$kit_root/test/experiment/experiment-host-local-pixi.sif"
support_init="$kit_root/test/experiment/support_pkg/src/experiment_support/__init__.py"
lima_instance="apptainer-x86"
backend="auto"

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

"$builder" --backend "$backend" --lima-instance "$lima_instance" --output "$package_image" --manifest "$kit_root/test/package/pyproject.toml"
"$builder" --backend "$backend" --lima-instance "$lima_instance" --mode pixi-project --output "$experiment_image" --manifest "$kit_root/test/experiment/pixi.toml"
"$builder" --backend "$backend" --lima-instance "$lima_instance" --mode pixi-project --host-local-path-deps --output "$experiment_host_local_image" --manifest "$kit_root/test/experiment/pixi.toml"

if [[ "$backend" == "local" ]]; then
    before="$(apptainer run --pwd "$kit_root/test/experiment" "$experiment_host_local_image" run_experiment.py)"
    if [[ "$before" != "experiment-host-v1" ]]; then
        echo "unexpected host-local output before edit: $before" >&2
        exit 1
    fi

    original_support="$(mktemp)"
    cp "$support_init" "$original_support"
    trap 'cp "$original_support" "$support_init"; rm -f "$original_support"' EXIT
    perl -0pi -e 's/experiment-host-v1/experiment-host-v2/' "$support_init"

    after="$(apptainer run --pwd "$kit_root/test/experiment" "$experiment_host_local_image" run_experiment.py)"
    if [[ "$after" != "experiment-host-v2" ]]; then
        echo "unexpected host-local output after edit: $after" >&2
        exit 1
    fi
fi

echo "built=$package_image"
echo "built=$experiment_image"
echo "built=$experiment_host_local_image"
