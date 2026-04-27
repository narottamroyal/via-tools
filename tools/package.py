# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "cairosvg>=2.9.0",
# ]
# ///

from contextlib import contextmanager
from pathlib import Path
from shutil import make_archive, copy2, rmtree

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
        pass


def main():
    with archive() as root:
        plugins = root / "plugins"
        plugins.mkdir()
        plugin_files = ["plugin.json", "via_tools.py"]
        for file in plugin_files:
            copy2(file, plugins / file)
        icon(plugins / "icon.png", 24)
        requirements(plugins / "requirements.txt")

        resources = root / "resources"
        resources.mkdir()
        icon(resources / "icon.png", 64)

        copy2("metadata.json", root / "metadata.json")


if __name__ == "__main__":
    main()
