#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./install.sh
#   ./install.sh /opt/pixi-singularity-kit
#
# What it installs:
#   - bin/pixi-container-build
#   - pixi-container-build
#   - pixi-container.def
#   - run_pixi_container.sh
#   - validate_pixi_container.sh
#   - README.md
#
# What it does not install:
#   - .git/
#   - test/
#   - build artifacts and caches
#
# After install it prints exact line to add to ~/.zshrc.

target_root="${1:-/opt/pixi-singularity-kit}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$target_root/bin"

install -m 0755 "$script_dir/bin/pixi-container-build" "$target_root/bin/pixi-container-build"
install -m 0755 "$script_dir/pixi-container-build" "$target_root/pixi-container-build"
install -m 0644 "$script_dir/pixi-container.def" "$target_root/pixi-container.def"
install -m 0755 "$script_dir/run_pixi_container.sh" "$target_root/run_pixi_container.sh"
install -m 0755 "$script_dir/validate_pixi_container.sh" "$target_root/validate_pixi_container.sh"
install -m 0644 "$script_dir/README.md" "$target_root/README.md"

cat <<EOF
Installed Pixi Singularity Kit to:
  $target_root

Add this line to ~/.zshrc:
  export PATH="$target_root/bin:\$PATH"
EOF
