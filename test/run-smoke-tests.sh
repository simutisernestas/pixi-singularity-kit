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
#   - uses kit-local `bin/pixi-container-build`
#
# Notes:
#   - builder uses `limactl` by default on macOS and local `apptainer` on Linux CI
#   - this smoke runner only verifies SIF generation for both sample cases

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
kit_root="$(cd -- "$script_dir/.." && pwd)"
builder="$kit_root/bin/pixi-container-build"
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

"$builder" --backend "$backend" --lima-instance "$lima_instance" --output "$kit_root/test/package/package-dev.sif" --manifest "$kit_root/test/package/pyproject.toml"
"$builder" --backend "$backend" --lima-instance "$lima_instance" --mode pixi-project --output "$kit_root/test/experiment/experiment-pixi.sif" --manifest "$kit_root/test/experiment/pixi.toml"

echo "built=$kit_root/test/package/package-dev.sif"
echo "built=$kit_root/test/experiment/experiment-pixi.sif"
