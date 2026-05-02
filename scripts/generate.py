import json
import subprocess

from importlib.metadata import version
from io import BytesIO
from pathlib import Path

from cairosvg import svg2png
from PIL import Image


def icon(output: Path, size: int | None = None):
    if size is None:
        size = 256
    png = svg2png(
        url="resources/icon.svg",
        output_width=size,
        output_height=size,
    )
    match output.suffix.lower():
        case ".ico":
            img = Image.open(BytesIO(png))
            img.save(
                output, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)]
            )
        case ".png":
            output.write_bytes(png)
        case str(suffix):
            raise ValueError(f"Unsupported file type: {suffix}")


def metadata(output: Path):
    data = json.loads(Path("resources/metadata.json").read_text())
    data["versions"][0]["version"] = version("via-tools")
    output.write_text(json.dumps(data, indent=2))


def requirements(output: Path):
    result = subprocess.run(
        [
            "uv",
            "export",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements.txt",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    output.write_text(result.stdout)
