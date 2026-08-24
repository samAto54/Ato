"""Validate Ato source metadata and, optionally, one built wheel using only the stdlib."""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
import zipfile
from pathlib import Path

PROJECT_NAME = "ato-agent"
REQUIRED_SCRIPTS = {"ato": "ato.main:main", "ato-gui": "ato.ui.desktop:main"}


def source_version(root: Path) -> str:
    module = ast.parse((root / "src" / "ato" / "__init__.py").read_text(encoding="utf-8"))
    for node in module.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise ValueError("src/ato/__init__.py must define a literal __version__.")


def validate_source(root: Path) -> tuple[str, tuple[str, ...]]:
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    if project.get("name") != PROJECT_NAME:
        raise ValueError(f"Project name must be {PROJECT_NAME!r}.")
    version = str(project.get("version", ""))
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Project version must use MAJOR.MINOR.PATCH.")
    if source_version(root) != version:
        raise ValueError("pyproject.toml and ato.__version__ do not match.")
    if project.get("scripts") != REQUIRED_SCRIPTS:
        raise ValueError("Required ato and ato-gui entry points are missing or changed.")
    package_files = tuple(
        path.relative_to(root / "src").as_posix()
        for path in sorted((root / "src" / "ato").rglob("*.py"))
    )
    if not package_files or "ato/__init__.py" not in package_files:
        raise ValueError("The Ato source package is incomplete.")
    return version, package_files


def validate_wheel(wheel: Path, version: str, package_files: tuple[str, ...]) -> None:
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise ValueError("Wheel path must identify one existing .whl file.")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing = sorted(set(package_files) - names)
        if missing:
            raise ValueError(f"Wheel is missing source modules: {', '.join(missing[:5])}")
        metadata_name = next((name for name in names if name.endswith(".dist-info/METADATA")), None)
        entries_name = next(
            (name for name in names if name.endswith(".dist-info/entry_points.txt")), None
        )
        if metadata_name is None or entries_name is None:
            raise ValueError("Wheel metadata or console entry points are missing.")
        metadata = archive.read(metadata_name).decode("utf-8")
        entries = archive.read(entries_name).decode("utf-8")
    if f"Name: {PROJECT_NAME}\n" not in metadata or f"Version: {version}\n" not in metadata:
        raise ValueError("Wheel name or version does not match pyproject.toml.")
    for name, target in REQUIRED_SCRIPTS.items():
        if f"{name} = {target}" not in entries:
            raise ValueError(f"Wheel is missing the {name!r} console entry point.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        version, package_files = validate_source(root)
        if args.wheel is not None:
            validate_wheel(args.wheel.resolve(), version, package_files)
    except (KeyError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Release validation failed: {exc}", file=sys.stderr)
        return 1
    wheel_status = f" and wheel {args.wheel.name}" if args.wheel is not None else ""
    print(f"Ato {version} source metadata{wheel_status} validated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
