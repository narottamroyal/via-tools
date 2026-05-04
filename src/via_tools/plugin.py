def main() -> None:
    # Delay import to ensure package is added to path if necessary
    from .engine import ViaTools
    from .gui import GUI

    engine = ViaTools()
    app = GUI(engine)
    app.run()


if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Add package to path (necessary when running from KiCad)
    file_path = Path(__file__).resolve().parent
    __package__ = file_path.name
    package_path = str(file_path.parent)
    if package_path not in sys.path:
        sys.path.insert(0, package_path)

    main()
