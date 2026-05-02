from .gui import GUI
from .engine import ViaTools


def main() -> None:
    engine = ViaTools()
    app = GUI(engine)
    app.run()


if __name__ == "__main__":
    main()
