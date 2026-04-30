#!/usr/bin/env python3
"""Build reusable pixi-backed Apptainer/Singularity image from current project.

Usage:
  pixi-container-build
  pixi-container-build --env default --env format
  pixi-container-build --mode package-dev --output ./my-package-dev.sif
  pixi-container-build --mode pixi-project --manifest /path/to/pixi.toml

Behavior:
  - Auto-detects nearest pixi-enabled project from current directory upward.
  - `package-dev` mode expects `pyproject.toml` with `[project]` and `[tool.pixi]`.
    Image bakes pixi environments but keeps host package editable outside image.
  - `pixi-project` mode bakes pixi environments for running host scripts/experiments.
  - `--env` may be passed multiple times or as comma-separated names.
    If omitted, all pixi environments are baked.
  - Uses `limactl` and guest-local `/tmp` staging by default on macOS.
  - Can use local `apptainer` directly on Linux for CI.
  - Output defaults to `<project>-dev.sif` or `<project>-pixi.sif` in current directory.
  - Keep only `bin/` on `PATH`; wrapper resolves sibling files inside this kit.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
import shlex
import tomllib
from uuid import uuid4


EXCLUDED_NAMES = {
    ".agent",
    ".git",
    ".mypy_cache",
    ".pixi",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}
EXCLUDED_PATTERNS = ["*.img", "*.pyc", "*.pyo", "*.sif", "*.sqsh"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "package-dev", "pixi-project"],
        default="auto",
        help="Container mode. Default: auto.",
    )
    parser.add_argument(
        "--manifest",
        help="Path to pyproject.toml or pixi.toml. Default: auto-detect from current directory upward.",
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        help="Pixi environment to bake. Repeat or pass comma-separated names. Default: all environments.",
    )
    parser.add_argument(
        "--output",
        help="Output .sif path. Default: ./<project>-dev.sif or ./<project>-pixi.sif.",
    )
    parser.add_argument(
        "--lima-instance",
        default="apptainer-x86",
        help="Lima instance name used for build/test. Default: apptainer-x86.",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "lima", "local"],
        default="auto",
        help="Build backend. Default: auto.",
    )
    parser.add_argument(
        "--keep-build-dir",
        action="store_true",
        help="Keep temporary host build directory for debugging.",
    )
    parser.add_argument(
        "--keep-guest-dir",
        action="store_true",
        help="Keep guest /tmp build directory for debugging.",
    )
    return parser.parse_args()


def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_pixi_config(manifest_path: Path) -> dict:
    data = load_toml(manifest_path)
    if manifest_path.name == "pyproject.toml":
        return data.get("tool", {}).get("pixi", {})
    return data


def is_package_dev_manifest(manifest_path: Path) -> bool:
    if manifest_path.name != "pyproject.toml":
        return False
    data = load_toml(manifest_path)
    return bool(data.get("project")) and bool(data.get("tool", {}).get("pixi"))


def find_manifest(start_dir: Path, mode: str) -> tuple[Path, str]:
    current = start_dir.resolve()
    while True:
        pyproject = current / "pyproject.toml"
        pixi_toml = current / "pixi.toml"
        if mode in {"auto", "package-dev"} and pyproject.exists() and is_package_dev_manifest(pyproject):
            return pyproject, "package-dev"
        if mode in {"auto", "pixi-project"}:
            if pixi_toml.exists():
                return pixi_toml, "pixi-project"
            if pyproject.exists() and load_pixi_config(pyproject):
                return pyproject, "pixi-project"
        if current.parent == current:
            break
        current = current.parent
    if mode == "package-dev":
        raise SystemExit("could not find pyproject.toml with [project] and [tool.pixi] from current directory upward")
    raise SystemExit("could not find pixi.toml or pixi-enabled pyproject.toml from current directory upward")


def normalize_selected_envs(raw_values: list[str], available_envs: list[str]) -> list[str]:
    if not raw_values:
        return available_envs
    selected: list[str] = []
    for value in raw_values:
        for item in value.split(","):
            name = item.strip()
            if name and name not in selected:
                selected.append(name)
    unknown = [name for name in selected if name not in available_envs]
    if unknown:
        raise SystemExit(
            f"unknown pixi environment(s): {', '.join(unknown)}; available: {', '.join(available_envs)}"
        )
    return selected


def list_environments(manifest_path: Path) -> list[str]:
    pixi_config = load_pixi_config(manifest_path)
    envs = pixi_config.get("environments", {})
    names = ["default"]
    for name in envs:
        if name != "default":
            names.append(name)
    return names


def iter_pypi_dependency_tables(node: object):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "pypi-dependencies" and isinstance(value, dict):
                yield value
            yield from iter_pypi_dependency_tables(value)


def collect_local_path_roots(manifest_path: Path, skip_workspace_root: bool) -> list[Path]:
    workspace_root = manifest_path.parent.resolve()
    roots: list[Path] = []
    for table in iter_pypi_dependency_tables(load_pixi_config(manifest_path)):
        for spec in table.values():
            if not isinstance(spec, dict) or "path" not in spec:
                continue
            dep_path = Path(spec["path"])
            resolved = (workspace_root / dep_path).resolve() if not dep_path.is_absolute() else dep_path.resolve()
            if skip_workspace_root and resolved == workspace_root:
                continue
            if dep_path.is_absolute() and workspace_root not in resolved.parents and resolved != workspace_root:
                raise SystemExit(
                    f"absolute local path dependency outside workspace is not supported: {dep_path}"
                )
            roots.append(resolved)
    return roots


def normalize_paths(paths: list[Path]) -> list[Path]:
    unique = sorted({path.resolve() for path in paths}, key=lambda path: (len(path.parts), str(path)))
    kept: list[Path] = []
    for path in unique:
        if any(parent == path or parent in path.parents for parent in kept):
            continue
        kept.append(path)
    return kept


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        dirs_exist_ok=True,
        symlinks=True,
        ignore=shutil.ignore_patterns(*sorted(EXCLUDED_NAMES), *EXCLUDED_PATTERNS),
    )


def copy_path(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        copy_tree(src, dst)
    else:
        shutil.copy2(src, dst)


def add_build_requirements(sanitized: dict, manifest_path: Path) -> None:
    if manifest_path.name != "pyproject.toml":
        return
    data = load_toml(manifest_path)
    build_requires = data.get("build-system", {}).get("requires", [])
    sanitized.setdefault("tool", {}).setdefault("pixi", {}).setdefault("dependencies", {})
    sanitized["tool"]["pixi"]["dependencies"].setdefault("pip", "*")
    sanitized["tool"]["pixi"]["dependencies"].setdefault("editables", "*")
    pypi_dependencies = sanitized["tool"]["pixi"].setdefault("pypi-dependencies", {})
    pypi_dependencies.setdefault("editables", "*")
    for requirement in build_requires:
        match = re.match(r"^([A-Za-z0-9_.-]+)", requirement)
        if match and match.group(1) not in pypi_dependencies:
            version_spec = requirement[len(match.group(1)):].strip()
            pypi_dependencies[match.group(1)] = version_spec or "*"


def strip_self_path_dependencies(sanitized: dict, workspace_root: Path) -> None:
    for table in iter_pypi_dependency_tables(sanitized):
        for name, spec in list(table.items()):
            if not isinstance(spec, dict) or "path" not in spec:
                continue
            dep_path = Path(spec["path"])
            resolved = (workspace_root / dep_path).resolve() if not dep_path.is_absolute() else dep_path.resolve()
            if resolved == workspace_root:
                del table[name]


def format_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(format_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{key} = {format_value(item)}" for key, item in value.items()) + " }"
    raise SystemExit(f"unsupported value in generated TOML: {value!r}")


def write_toml(node: dict, output_path: Path) -> None:
    lines: list[str] = []

    def visit(table: dict, path: list[str]) -> None:
        scalars: list[tuple[str, object]] = []
        children: list[tuple[str, dict]] = []
        for key, value in table.items():
            if isinstance(value, dict):
                children.append((key, value))
            else:
                scalars.append((key, value))
        if path:
            lines.append(f"[{'.'.join(path)}]")
        for key, value in scalars:
            lines.append(f"{key} = {format_value(value)}")
        if path and (scalars or children):
            lines.append("")
        for index, (key, value) in enumerate(children):
            visit(value, [*path, key])
            if index != len(children) - 1:
                lines.append("")

    visit(node, [])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def build_manifest_copy(mode: str, manifest_path: Path, build_root: Path) -> Path:
    if mode != "package-dev":
        return manifest_path
    sanitized = copy.deepcopy(load_toml(manifest_path))
    strip_self_path_dependencies(sanitized, manifest_path.parent.resolve())
    add_build_requirements(sanitized, manifest_path)
    temp_manifest = build_root / manifest_path.name
    write_toml(sanitized, temp_manifest)
    return temp_manifest


def build_output_path(args: argparse.Namespace, project_name: str, mode: str) -> Path:
    if args.output:
        return Path(args.output).resolve()
    suffix = "dev" if mode == "package-dev" else "pixi"
    return (Path.cwd() / f"{project_name}-{suffix}.sif").resolve()


def ensure_limactl_available() -> None:
    if shutil.which("limactl") is None:
        raise SystemExit("could not find `limactl` in PATH")


def run_host_command(command: list[str]) -> None:
    subprocess.run(command, check=True)


def ensure_local_apptainer_available() -> None:
    if shutil.which("apptainer") is None:
        raise SystemExit("could not find `apptainer` in PATH for local backend")


def run_guest_command(instance: str, command: list[str]) -> None:
    subprocess.run(["limactl", "shell", instance, *command], check=True)


def copy_to_guest(instance: str, local_path: Path, guest_path: str) -> None:
    subprocess.run(["limactl", "copy", str(local_path), f"{instance}:{guest_path}"], check=True)


def copy_from_guest(instance: str, guest_path: str, local_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="pixi-container-copy-") as tmpdir:
        temp_path = Path(tmpdir) / local_path.name
        subprocess.run(["limactl", "copy", f"{instance}:{guest_path}", str(temp_path)], check=True)
        shutil.move(str(temp_path), str(local_path))


def ensure_lima_started(instance: str) -> None:
    run_host_command(["limactl", "start", instance])


def detect_backend(name: str) -> str:
    if name != "auto":
        return name
    if os.uname().sysname == "Darwin":
        return "lima"
    if shutil.which("apptainer") is not None:
        return "local"
    if shutil.which("limactl") is not None:
        return "lima"
    raise SystemExit("could not auto-detect backend: need `apptainer` or `limactl`")


def write_metadata(staging_root: Path, manifest_rel: Path, mode: str, source_manifest_name: str, use_frozen: bool) -> None:
    meta_dir = staging_root / ".pixi-container"
    meta_dir.mkdir(parents=True, exist_ok=True)
    project_package_name = ""
    if source_manifest_name == "pyproject.toml":
        pyproject_path = staging_root / manifest_rel
        if pyproject_path.exists():
            project_package_name = load_toml(pyproject_path).get("project", {}).get("name", "")
    metadata_lines = [
        f"MODE={shlex.quote(mode)}",
        f"MANIFEST_REL={shlex.quote(manifest_rel.as_posix())}",
        f"SOURCE_MANIFEST_NAME={shlex.quote(source_manifest_name)}",
        f"PROJECT_PACKAGE_NAME={shlex.quote(project_package_name)}",
        f"USE_FROZEN={shlex.quote('1' if use_frozen else '0')}",
    ]
    (meta_dir / "metadata.env").write_text("\n".join(metadata_lines) + "\n", encoding="utf-8")


def stage_bundle(
    mode: str,
    manifest_path: Path,
    manifest_copy: Path,
    selected_envs: list[str],
    output_bundle: Path,
) -> None:
    workspace_root = manifest_path.parent.resolve()
    local_roots = collect_local_path_roots(manifest_path, skip_workspace_root=mode == "package-dev")
    selected_paths = [*local_roots]
    lock_path = workspace_root / "pixi.lock"
    if lock_path.exists() and mode != "package-dev":
        selected_paths.append(lock_path)
    selected_paths = normalize_paths(selected_paths)
    common_root = workspace_root.parent if mode == "package-dev" else Path(
        os.path.commonpath([str(path.resolve()) for path in [manifest_path, *selected_paths]])
    )

    with tempfile.TemporaryDirectory(prefix="pixi-bundle-") as tmpdir:
        staging_root = Path(tmpdir) / "bundle"
        staging_root.mkdir(parents=True, exist_ok=True)

        for src in selected_paths:
            rel = src.relative_to(common_root)
            copy_path(src, staging_root / rel)

        manifest_rel = manifest_path.relative_to(common_root)
        copy_path(manifest_copy, staging_root / manifest_rel)
        write_metadata(
            staging_root,
            manifest_rel,
            mode,
            manifest_path.name,
            use_frozen=mode == "pixi-project" and lock_path.exists(),
        )
        (staging_root / ".pixi-container" / "expected-envs.txt").write_text(
            "\n".join(selected_envs) + "\n",
            encoding="utf-8",
        )

        output_bundle.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(output_bundle, "w:gz") as archive:
            archive.add(staging_root, arcname="")


def render_definition(template_path: Path, bundle_path: Path, runscript_path: Path, validator_path: Path, output_path: Path) -> None:
    content = template_path.read_text(encoding="utf-8")
    content = content.replace("__BUNDLE_TAR__", str(bundle_path))
    content = content.replace("__RUNSCRIPT__", str(runscript_path))
    content = content.replace("__VALIDATOR__", str(validator_path))
    output_path.write_text(content, encoding="utf-8")


def stage_guest_inputs(instance: str, script_dir: Path, build_root: Path) -> tuple[str, str, str]:
    guest_root = f"/tmp/pixi-container-build-{uuid4().hex[:12]}"
    guest_bundle = f"{guest_root}/pixi-bundle.tar.gz"
    guest_runscript = f"{guest_root}/run_pixi_container.sh"
    guest_validator = f"{guest_root}/validate_pixi_container.sh"
    guest_recipe = f"{guest_root}/pixi-container.def"
    local_recipe = build_root / "pixi-container.def"

    run_guest_command(instance, ["rm", "-rf", guest_root])
    run_guest_command(instance, ["mkdir", "-p", guest_root])
    copy_to_guest(instance, build_root / "pixi-bundle.tar.gz", guest_bundle)
    copy_to_guest(instance, script_dir / "run_pixi_container.sh", guest_runscript)
    copy_to_guest(instance, script_dir / "validate_pixi_container.sh", guest_validator)
    render_definition(
        script_dir / "pixi-container.def",
        Path(guest_bundle),
        Path(guest_runscript),
        Path(guest_validator),
        local_recipe,
    )
    copy_to_guest(instance, local_recipe, guest_recipe)
    return guest_root, guest_recipe, f"{guest_root}/image.sif"


def build_local_image(script_dir: Path, build_root: Path, output_path: Path) -> None:
    recipe_path = build_root / "pixi-container.def"
    render_definition(
        script_dir / "pixi-container.def",
        build_root / "pixi-bundle.tar.gz",
        script_dir / "run_pixi_container.sh",
        script_dir / "validate_pixi_container.sh",
        recipe_path,
    )
    run_host_command(["apptainer", "build", str(output_path), str(recipe_path)])
    run_host_command(["apptainer", "test", str(output_path)])


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    if args.manifest:
        manifest_path = Path(args.manifest).resolve()
        if not manifest_path.exists() or manifest_path.name not in {"pyproject.toml", "pixi.toml"}:
            raise SystemExit("--manifest must point to existing pyproject.toml or pixi.toml")
        if args.mode == "auto":
            mode = "package-dev" if is_package_dev_manifest(manifest_path) else "pixi-project"
        else:
            mode = args.mode
    else:
        manifest_path, mode = find_manifest(Path.cwd(), args.mode)

    if mode == "package-dev" and manifest_path.name != "pyproject.toml":
        raise SystemExit("package-dev mode requires pyproject.toml")

    pixi_config = load_pixi_config(manifest_path)
    workspace_name = pixi_config.get("workspace", {}).get("name")
    project_name = workspace_name or manifest_path.parent.name
    available_envs = list_environments(manifest_path)
    selected_envs = normalize_selected_envs(args.env, available_envs)
    output_path = build_output_path(args, project_name, mode)

    backend = detect_backend(args.backend)
    if backend == "lima":
        ensure_limactl_available()
    else:
        ensure_local_apptainer_available()

    tmpdir_obj: tempfile.TemporaryDirectory[str] | None = None
    if args.keep_build_dir:
        build_root = Path(tempfile.mkdtemp(prefix="pixi-container-build-"))
    else:
        tmpdir_obj = tempfile.TemporaryDirectory(prefix="pixi-container-build-")
        build_root = Path(tmpdir_obj.name)

    manifest_copy = build_manifest_copy(mode, manifest_path, build_root)
    stage_bundle(mode, manifest_path, manifest_copy, selected_envs, build_root / "pixi-bundle.tar.gz")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"mode={mode}")
    print(f"manifest={manifest_path}")
    print(f"envs={','.join(selected_envs)}")
    print(f"backend={backend}")
    print(f"lima_instance={args.lima_instance}")
    print(f"output={output_path}")
    print(f"build_root={build_root}")
    if backend == "lima":
        ensure_lima_started(args.lima_instance)
        guest_root, guest_recipe, guest_image = stage_guest_inputs(args.lima_instance, script_dir, build_root)
        print(f"guest_root={guest_root}")
        run_guest_command(args.lima_instance, ["rm", "-f", guest_image])
        run_guest_command(args.lima_instance, ["sudo", "apptainer", "build", guest_image, guest_recipe])
        run_guest_command(args.lima_instance, ["apptainer", "test", guest_image])
        if output_path.exists():
            output_path.unlink()
        copy_from_guest(args.lima_instance, guest_image, output_path)
        if not args.keep_guest_dir:
            run_guest_command(args.lima_instance, ["rm", "-rf", guest_root])
    else:
        build_local_image(script_dir, build_root, output_path)

    if tmpdir_obj is not None:
        tmpdir_obj.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
