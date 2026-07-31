"""Compare the three uv pins: pyproject required-version, mise.toml, Dockerfile.

uv enforces required-version at runtime, but the Dockerfile stage only hits it
during the image build on the release path, after the e2e spend. This static
compare moves that failure to PR CI and `mise run check`.
"""

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def pyproject_pin() -> str:
    specifier: str = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"][
        "required-version"
    ]
    if not specifier.startswith("=="):
        sys.exit(f"pyproject.toml [tool.uv] required-version is not an exact pin: {specifier!r}")
    return specifier.removeprefix("==")


def mise_pin() -> str:
    pin: str = tomllib.loads((ROOT / "mise.toml").read_text())["tools"]["uv"]
    return pin


def dockerfile_pin() -> str:
    match = re.search(
        r"^FROM ghcr\.io/astral-sh/uv:([^@\s]+)",
        (ROOT / "Dockerfile").read_text(),
        flags=re.MULTILINE,
    )
    if match is None:
        sys.exit("Dockerfile has no ghcr.io/astral-sh/uv stage")
    return match.group(1)


def main() -> None:
    pins = {
        "pyproject.toml": pyproject_pin(),
        "mise.toml": mise_pin(),
        "Dockerfile": dockerfile_pin(),
    }
    if len(set(pins.values())) != 1:
        drift = ", ".join(f"{source} {pin}" for source, pin in pins.items())
        sys.exit(f"uv pins drifted: {drift}")


if __name__ == "__main__":
    main()
