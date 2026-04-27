import subprocess

from cairosvg import svg2png
from pathlib import Path


def icon(output: Path, size: int):
    svg2png(
        url="resources/icon.svg",
        write_to=str(output),
        output_width=size,
        output_height=size,
    )


def requirements(output: Path):
    result = subprocess.run(
        ["uv", "export", "--format", "requirements.txt"],
        check=True,
        capture_output=True,
        text=True,
    )
    output.write_text(result.stdout)
