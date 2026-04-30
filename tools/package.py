from contextlib import contextmanager
from pathlib import Path
from shutil import make_archive, copy2, rmtree

from kipy.packaging.validate import validate

from generate import icon, requirements


@contextmanager
def archive():
    directory = Path("archive")
    if directory.exists():
        rmtree(directory)
    directory.mkdir()
    try:
        yield directory
    finally:
        make_archive("via-tools", "zip", directory)
        rmtree(directory)
        print("Plugin generated")


def generate_plugin():
    with archive() as root:
        plugins = root / "plugins"
        plugins.mkdir()
        plugin_files = ["plugin.json", "via_tools.py"]
        for file in plugin_files:
            copy2(file, plugins / file)
        icon(plugins / "icon.ico")
        icon(plugins / "icon.png", 24)
        requirements(plugins / "requirements.txt")

        resources = root / "resources"
        resources.mkdir()
        icon(resources / "icon.png", 64)
        copy2("metadata.json", root / "metadata.json")


def validate_plugin():
    """Validate plugin archive (treat warnings as errors)"""
    report = validate("via-tools.zip")
    if not report.ok or report.errors or report.warnings:
        print("Plugin validation failed:")
        for error in report.errors:
            print(f"[error] {error.message}")
        for warning in report.warnings:
            print(f"[warning] {warning.message}")
        raise SystemExit(2)

    print("Plugin validated")


def main():
    generate_plugin()
    validate_plugin()


if __name__ == "__main__":
    main()
