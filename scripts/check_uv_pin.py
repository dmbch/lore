"""Check the uv pins agree: mise.toml and the Dockerfile carry the same exact
version, and that version satisfies pyproject's required-version range.

uv enforces required-version at runtime, but the Dockerfile stage only hits it
during the image build on the release path, after the e2e spend. This static
check moves that failure to PR CI and `mise run check`. The range (not an exact
pin) exists so Dependabot's older bundled uv can still resolve the lockfile.
"""

import re
import sys
import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parent.parent


def pyproject_range() -> SpecifierSet:
    specifier: str = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"][
        "required-version"
    ]
    return SpecifierSet(specifier)


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
    mise, docker = mise_pin(), dockerfile_pin()
    if mise != docker:
        sys.exit(f"uv pins drifted: mise.toml {mise}, Dockerfile {docker}")
    allowed = pyproject_range()
    if not allowed.contains(mise):
        sys.exit(f"uv pin {mise} is outside pyproject required-version {allowed}")


if __name__ == "__main__":
    main()
