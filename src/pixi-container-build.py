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
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
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
CACHE_DIR_NAME = "pixi-container-kit"
BASE_TEMPLATE_NAME = "pixi-container-base.def"
ENV_TEMPLATE_NAME = "pixi-container-env.def"
PIXI_CACHE_BIND_TARGET = "/mnt/pixi-cache"


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
    parser.add_argument(
        "--host-local-path-deps",
        action="store_true",
        help="Do not bake local path pypi-dependencies; load them from the host at runtime via PYTHONPATH.",
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


def build_manifest_copy(mode: str, manifest_path: Path, build_root: Path, host_local_path_deps: bool) -> Path:
    if mode != "package-dev":
        return manifest_path
    sanitized = copy.deepcopy(load_toml(manifest_path))
    workspace_root = manifest_path.parent.resolve()
    if mode == "package-dev":
        strip_self_path_dependencies(sanitized, workspace_root)
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


def run_host_command(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    command_env = None
    if env:
        command_env = os.environ.copy()
        command_env.update({key: str(value) for key, value in env.items()})
    return subprocess.run(
        command,
        check=True,
        env=command_env,
        capture_output=capture_output,
        text=capture_output,
    )


def ensure_local_apptainer_available() -> None:
    if shutil.which("apptainer") is None:
        raise SystemExit("could not find `apptainer` in PATH for local backend")


def run_guest_command(
    instance: str,
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    guest_command = command
    if env:
        guest_command = ["env", *[f"{key}={value}" for key, value in env.items()], *command]
    return subprocess.run(
        ["limactl", "shell", instance, *guest_command],
        check=True,
        capture_output=capture_output,
        text=capture_output,
    )


def copy_to_guest(instance: str, local_path: Path, guest_path: str) -> None:
    subprocess.run(["limactl", "copy", str(local_path), f"{instance}:{guest_path}"], check=True)


def copy_from_guest(instance: str, guest_path: str, local_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="pixi-container-copy-") as tmpdir:
        temp_path = Path(tmpdir) / local_path.name
        subprocess.run(["limactl", "copy", f"{instance}:{guest_path}", str(temp_path)], check=True)
        shutil.move(str(temp_path), str(local_path))


def ensure_lima_started(instance: str) -> None:
    run_host_command(["limactl", "start", instance])


def guest_file_exists(instance: str, path: Path) -> bool:
    return subprocess.run(["limactl", "shell", instance, "test", "-f", str(path)], check=False).returncode == 0


def guest_home_dir(instance: str) -> Path:
    result = run_guest_command(instance, ["sh", "-lc", 'printf %s "$HOME"'], capture_output=True)
    return Path(result.stdout.strip())


def target_architecture(backend: str, lima_instance: str) -> str:
    if backend == "lima":
        return run_guest_command(lima_instance, ["uname", "-m"], capture_output=True).stdout.strip()
    return os.uname().machine


def resolve_cache_dirs(backend: str, lima_instance: str) -> dict[str, Path]:
    if backend == "lima":
        root = guest_home_dir(lima_instance) / ".cache" / CACHE_DIR_NAME / backend
        run_guest_command(
            lima_instance,
            [
                "mkdir",
                "-p",
                str(root / "apptainer"),
                str(root / "tmp"),
                str(root / "pixi"),
                str(root / "stages" / "base"),
                str(root / "stages" / "env"),
                str(root / "meta"),
            ],
        )
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / CACHE_DIR_NAME / backend
        for path in [
            root / "apptainer",
            root / "tmp",
            root / "pixi",
            root / "stages" / "base",
            root / "stages" / "env",
            root / "meta",
        ]:
            path.mkdir(parents=True, exist_ok=True)
    return {
        "root": root,
        "apptainer": root / "apptainer",
        "tmp": root / "tmp",
        "pixi": root / "pixi",
        "base": root / "stages" / "base",
        "env": root / "stages" / "env",
        "meta": root / "meta",
    }


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            digest.update(b"L\0")
            digest.update(rel)
            digest.update(b"\0")
            digest.update(os.readlink(path).encode("utf-8"))
            digest.update(b"\0")
            continue
        if path.is_dir():
            digest.update(b"D\0")
            digest.update(rel)
            digest.update(b"\0")
            continue
        digest.update(b"F\0")
        digest.update(rel)
        digest.update(b"\0")
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def compute_cache_key(payload: dict[str, object]) -> str:
    return sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def stage_metadata_payload(key: str, extra: dict[str, object]) -> str:
    payload = {
        "key": key,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **extra,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_guest_text_file(instance: str, guest_path: Path, content: str) -> None:
    with tempfile.TemporaryDirectory(prefix="pixi-container-meta-") as tmpdir:
        local_path = Path(tmpdir) / guest_path.name
        local_path.write_text(content, encoding="utf-8")
        copy_to_guest(instance, local_path, str(guest_path))


def build_base_key(script_dir: Path, arch: str) -> tuple[str, dict[str, object]]:
    template_path = script_dir / BASE_TEMPLATE_NAME
    inputs = {
        "arch": arch,
        "template": BASE_TEMPLATE_NAME,
        "template_sha256": sha256_bytes(template_path.read_bytes()),
    }
    return compute_cache_key(inputs), inputs


def build_env_key(script_dir: Path, arch: str, base_key: str, bundle_hash: str) -> tuple[str, dict[str, object]]:
    template_path = script_dir / ENV_TEMPLATE_NAME
    inputs = {
        "arch": arch,
        "base_key": base_key,
        "bundle_sha256": bundle_hash,
        "pixi_cache_bind_target": PIXI_CACHE_BIND_TARGET,
        "template": ENV_TEMPLATE_NAME,
        "template_sha256": sha256_bytes(template_path.read_bytes()),
    }
    return compute_cache_key(inputs), inputs


def build_apptainer_env(cache_dirs: dict[str, Path]) -> dict[str, str]:
    return {
        "APPTAINER_CACHEDIR": str(cache_dirs["apptainer"]),
        "APPTAINER_TMPDIR": str(cache_dirs["tmp"]),
    }


def render_definition(template_path: Path, replacements: dict[str, str], output_path: Path) -> None:
    content = template_path.read_text(encoding="utf-8")
    for needle, replacement in replacements.items():
        content = content.replace(needle, replacement)
    output_path.write_text(content, encoding="utf-8")


def apptainer_build_command(output_path: Path, recipe_path: Path, binds: list[str]) -> list[str]:
    command = ["apptainer", "build"]
    for bind in binds:
        command.extend(["--bind", bind])
    command.extend([str(output_path), str(recipe_path)])
    return command


def build_local_stage(output_path: Path, recipe_path: Path, cache_dirs: dict[str, Path], binds: list[str] | None = None) -> None:
    if output_path.exists():
        output_path.unlink()
    run_host_command(
        apptainer_build_command(output_path, recipe_path, binds or []),
        env=build_apptainer_env(cache_dirs),
    )


def build_guest_stage(
    instance: str,
    output_path: Path,
    recipe_path: Path,
    cache_dirs: dict[str, Path],
    binds: list[str] | None = None,
) -> None:
    run_guest_command(instance, ["rm", "-f", str(output_path)])
    run_guest_command(
        instance,
        ["sudo", "-E", *apptainer_build_command(output_path, recipe_path, binds or [])],
        env=build_apptainer_env(cache_dirs),
    )


def test_local_image(image_path: Path) -> None:
    run_host_command(["apptainer", "test", str(image_path)])


def test_guest_image(instance: str, image_path: Path) -> None:
    run_guest_command(instance, ["apptainer", "test", str(image_path)])


def copy_cached_image(source_path: Path, output_path: Path) -> None:
    if source_path.resolve() == output_path.resolve():
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    shutil.copy2(source_path, output_path)


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


def write_metadata(
    staging_root: Path,
    manifest_rel: Path,
    mode: str,
    source_manifest_name: str,
    use_frozen: bool,
    host_local_path_deps: bool,
) -> None:
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
        f"HOST_LOCAL_PATH_DEPS={shlex.quote('1' if host_local_path_deps else '0')}",
    ]
    (meta_dir / "metadata.env").write_text("\n".join(metadata_lines) + "\n", encoding="utf-8")


def stage_bundle(
    mode: str,
    manifest_path: Path,
    manifest_copy: Path,
    selected_envs: list[str],
    output_bundle: Path,
    host_local_path_deps: bool,
) -> str:
    workspace_root = manifest_path.parent.resolve()
    local_roots = collect_local_path_roots(manifest_path, skip_workspace_root=mode == "package-dev")
    host_local_paths = [os.path.relpath(path, workspace_root) for path in local_roots] if host_local_path_deps else []
    selected_paths = [*local_roots]
    conda_pypi_map = load_pixi_config(manifest_path).get("workspace", {}).get("conda-pypi-map")
    if isinstance(conda_pypi_map, str):
        conda_pypi_map = {"default": conda_pypi_map}
    if isinstance(conda_pypi_map, dict):
        for map_value in conda_pypi_map.values():
            if not isinstance(map_value, str):
                continue
            map_path = Path(map_value)
            resolved_map = (workspace_root / map_path).resolve() if not map_path.is_absolute() else map_path.resolve()
            if resolved_map.exists():
                selected_paths.append(resolved_map)
    lock_path = workspace_root / "pixi.lock"
    if lock_path.exists() and mode != "package-dev":
        selected_paths.append(lock_path)
    selected_paths = normalize_paths(selected_paths)
    common_root = workspace_root.parent if mode == "package-dev" else Path(
        os.path.commonpath([str(path.resolve()) for path in [workspace_root, *selected_paths]])
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
            host_local_path_deps=host_local_path_deps,
        )
        (staging_root / ".pixi-container" / "expected-envs.txt").write_text(
            "\n".join(selected_envs) + "\n",
            encoding="utf-8",
        )
        (staging_root / ".pixi-container" / "host-local-paths.txt").write_text(
            "\n".join(host_local_paths) + ("\n" if host_local_paths else ""),
            encoding="utf-8",
        )
        bundle_hash = hash_tree(staging_root)

        output_bundle.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(output_bundle, "w:gz") as archive:
            archive.add(staging_root, arcname="")
    return bundle_hash


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
        ensure_lima_started(args.lima_instance)
    else:
        ensure_local_apptainer_available()

    tmpdir_obj: tempfile.TemporaryDirectory[str] | None = None
    if args.keep_build_dir:
        build_root = Path(tempfile.mkdtemp(prefix="pixi-container-build-"))
    else:
        tmpdir_obj = tempfile.TemporaryDirectory(prefix="pixi-container-build-")
        build_root = Path(tmpdir_obj.name)

    try:
        manifest_copy = build_manifest_copy(mode, manifest_path, build_root, args.host_local_path_deps)
        cache_dirs = resolve_cache_dirs(backend, args.lima_instance)
        arch = target_architecture(backend, args.lima_instance)
        bundle_hash = stage_bundle(
            mode,
            manifest_path,
            manifest_copy,
            selected_envs,
            build_root / "pixi-bundle.tar.gz",
            args.host_local_path_deps,
        )
        base_key, base_inputs = build_base_key(script_dir, arch)
        env_key, env_inputs = build_env_key(script_dir, arch, base_key, bundle_hash)
        base_stage_path = cache_dirs["base"] / f"{base_key}.sif"
        env_stage_path = cache_dirs["env"] / f"{env_key}.sif"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"mode={mode}")
        print(f"manifest={manifest_path}")
        print(f"envs={','.join(selected_envs)}")
        print(f"backend={backend}")
        print(f"arch={arch}")
        print(f"host_local_path_deps={int(args.host_local_path_deps)}")
        print(f"lima_instance={args.lima_instance}")
        print(f"output={output_path}")
        print(f"build_root={build_root}")
        print(f"cache_root={cache_dirs['root']}")
        print(f"bundle_hash={bundle_hash[:12]}")
        print(f"base_key={base_key[:12]}")
        print(f"env_key={env_key[:12]}")
        if backend == "lima":
            guest_root = Path(f"/tmp/pixi-container-build-{uuid4().hex[:12]}")
            guest_bundle = guest_root / "pixi-bundle.tar.gz"
            guest_runscript = guest_root / "run_pixi_container.sh"
            guest_validator = guest_root / "validate_pixi_container.sh"
            guest_base_recipe = guest_root / BASE_TEMPLATE_NAME
            guest_env_recipe = guest_root / ENV_TEMPLATE_NAME
            local_base_recipe = build_root / BASE_TEMPLATE_NAME
            local_env_recipe = build_root / ENV_TEMPLATE_NAME

            run_guest_command(args.lima_instance, ["rm", "-rf", str(guest_root)])
            run_guest_command(args.lima_instance, ["mkdir", "-p", str(guest_root)])
            copy_to_guest(args.lima_instance, build_root / "pixi-bundle.tar.gz", str(guest_bundle))
            copy_to_guest(args.lima_instance, script_dir / "run_pixi_container.sh", str(guest_runscript))
            copy_to_guest(args.lima_instance, script_dir / "validate_pixi_container.sh", str(guest_validator))
            render_definition(script_dir / BASE_TEMPLATE_NAME, {}, local_base_recipe)
            render_definition(
                script_dir / ENV_TEMPLATE_NAME,
                {
                    "__BASE_IMAGE__": str(base_stage_path),
                    "__BUNDLE_TAR__": str(guest_bundle),
                    "__RUNSCRIPT__": str(guest_runscript),
                    "__VALIDATOR__": str(guest_validator),
                },
                local_env_recipe,
            )
            copy_to_guest(args.lima_instance, local_base_recipe, str(guest_base_recipe))
            copy_to_guest(args.lima_instance, local_env_recipe, str(guest_env_recipe))
            print(f"guest_root={guest_root}")
            try:
                base_hit = guest_file_exists(args.lima_instance, base_stage_path)
                env_hit = guest_file_exists(args.lima_instance, env_stage_path)
                print(f"base_cache={'hit' if base_hit else 'miss'}")
                print(f"base_stage={base_stage_path}")
                print(f"env_cache={'hit' if env_hit else 'miss'}")
                print(f"env_stage={env_stage_path}")
                if not base_hit:
                    build_guest_stage(args.lima_instance, base_stage_path, guest_base_recipe, cache_dirs)
                    write_guest_text_file(
                        args.lima_instance,
                        base_stage_path.with_suffix(".json"),
                        stage_metadata_payload(base_key, {**base_inputs, "stage": "base"}),
                    )
                if not env_hit:
                    build_guest_stage(
                        args.lima_instance,
                        env_stage_path,
                        guest_env_recipe,
                        cache_dirs,
                        [f"{cache_dirs['pixi']}:{PIXI_CACHE_BIND_TARGET}"],
                    )
                    write_guest_text_file(
                        args.lima_instance,
                        env_stage_path.with_suffix(".json"),
                        stage_metadata_payload(env_key, {**env_inputs, "stage": "env"}),
                    )
                test_guest_image(args.lima_instance, env_stage_path)
                if output_path.exists():
                    output_path.unlink()
                copy_from_guest(args.lima_instance, str(env_stage_path), output_path)
            finally:
                if not args.keep_guest_dir:
                    cleaned = subprocess.run(
                        ["limactl", "shell", args.lima_instance, "rm", "-rf", str(guest_root)],
                        check=False,
                    )
                    if cleaned.returncode != 0:
                        print(f"warning: failed to remove guest build dir {guest_root}", file=sys.stderr)
        else:
            base_recipe_path = build_root / BASE_TEMPLATE_NAME
            env_recipe_path = build_root / ENV_TEMPLATE_NAME
            render_definition(script_dir / BASE_TEMPLATE_NAME, {}, base_recipe_path)
            render_definition(
                script_dir / ENV_TEMPLATE_NAME,
                {
                    "__BASE_IMAGE__": str(base_stage_path),
                    "__BUNDLE_TAR__": str(build_root / "pixi-bundle.tar.gz"),
                    "__RUNSCRIPT__": str(script_dir / "run_pixi_container.sh"),
                    "__VALIDATOR__": str(script_dir / "validate_pixi_container.sh"),
                },
                env_recipe_path,
            )
            base_hit = base_stage_path.exists()
            env_hit = env_stage_path.exists()
            print(f"base_cache={'hit' if base_hit else 'miss'}")
            print(f"base_stage={base_stage_path}")
            print(f"env_cache={'hit' if env_hit else 'miss'}")
            print(f"env_stage={env_stage_path}")
            if not base_hit:
                build_local_stage(base_stage_path, base_recipe_path, cache_dirs)
                base_stage_path.with_suffix(".json").write_text(
                    stage_metadata_payload(base_key, {**base_inputs, "stage": "base"}),
                    encoding="utf-8",
                )
            if not env_hit:
                build_local_stage(
                    env_stage_path,
                    env_recipe_path,
                    cache_dirs,
                    [f"{cache_dirs['pixi']}:{PIXI_CACHE_BIND_TARGET}"],
                )
                env_stage_path.with_suffix(".json").write_text(
                    stage_metadata_payload(env_key, {**env_inputs, "stage": "env"}),
                    encoding="utf-8",
                )
            test_local_image(env_stage_path)
            copy_cached_image(env_stage_path, output_path)
    finally:
        if backend == "lima":
            stopped = subprocess.run(["limactl", "stop", args.lima_instance], check=False)
            if stopped.returncode != 0:
                print(f"warning: failed to stop lima instance {args.lima_instance}", file=sys.stderr)
        if tmpdir_obj is not None:
            tmpdir_obj.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
