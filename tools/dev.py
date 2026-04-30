from generate import icon, requirements
from pathlib import Path


def main():
    icon(Path("icon.ico"))
    icon(Path("icon.png"), 24)
    requirements(Path("requirements.txt"))


if __name__ == "__main__":
    main()
