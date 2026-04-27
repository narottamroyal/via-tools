# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "cairosvg>=2.9.0",
# ]
# ///


from generate import icon, requirements
from pathlib import Path


def main():
    icon(Path("icon.png"), 24)
    requirements(Path("requirements.txt"))


if __name__ == "__main__":
    main()
