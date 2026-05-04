from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from contextlib import contextmanager
from pathlib import Path
from shutil import make_archive, copy2, rmtree

from kipy.packaging.validate import validate

from generate import icon, metadata, requirements, version


def validate_plugin(path: Path):
    """Validate plugin archive (treat warnings as errors)"""
    report = validate(path)
    if not report.ok or report.errors or report.warnings:
        print("Plugin validation failed:")
        for error in report.errors:
            print(f"[error] {error.message}")
        for warning in report.warnings:
            print(f"[warning] {warning.message}")
        raise SystemExit(2)

    print("Plugin validated")


@contextmanager
def archive(path: Path, zip: bool):
    if path.exists():
        rmtree(path)
    path.mkdir(parents=True)

    zip_file = path.with_suffix(".zip")
    if zip_file.exists():
        zip_file.unlink()

    try:
        yield path
        print("Plugin generated")
    finally:
        validate_plugin(path)

    if zip:
        make_archive(zip_file.with_suffix(""), zip_file.suffix[1:], path)
        rmtree(path)


def generate(path: Path, zip: bool):
    with archive(path, zip) as root:
        plugins = root / "plugins"
        plugins.mkdir()
        icon(plugins / "icon.ico")
        icon(plugins / "icon.png", 24)
        icon(plugins / "icon_large.png", 32)
        requirements(plugins / "requirements.txt")
        copy2("resources/plugin.json", plugins)
        for path in Path("src/via_tools").rglob("*"):
            if "__pycache__" in path.parts:
                continue
            elif path.is_file():
                dest = plugins / path.relative_to("src")
                dest.parent.mkdir(parents=True, exist_ok=True)
                copy2(path, dest)
        version(plugins / "via_tools/_version.py")

        resources = root / "resources"
        resources.mkdir()
        icon(resources / "icon.png", 64)

        metadata(root / "metadata.json")


def main():
    parser = ArgumentParser(
        prog="package",
        usage="uv run scripts/package.py",
        description="KiCad Plugin Packager",
        formatter_class=ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--output", type=Path, default=Path.cwd(), help="output path")
    parser.add_argument("--name", default="via-tools", help="package name")
    parser.add_argument(
        "--dev", action="store_true", help="do not zip package directory"
    )
    args = parser.parse_args()
    generate(args.output / args.name, not args.dev)


if __name__ == "__main__":
    main()
