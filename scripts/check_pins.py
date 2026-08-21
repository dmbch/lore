"""Check the toolchain pins agree.

uv: mise.toml and the Dockerfile carry the same exact version, and that
version satisfies pyproject's required-version range. python: the Dockerfile
base image is authoritative; .python-version, workflow container images,
requires-python, ruff's target-version, and pyright's pythonVersion must all
agree with its tag.

uv enforces required-version at runtime, but the Dockerfile stage only hits it
during the image build on the release path, after the e2e spend. This static
check moves that failure to PR CI and `mise run check`. The range (not an
exact pin) exists so Dependabot's older bundled uv can still resolve the
lockfile.
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


def dockerfile_python_refs() -> str:
    refs: list[str] = re.findall(
        r"^FROM python:(\S+)",
        (ROOT / "Dockerfile").read_text(),
        flags=re.MULTILINE,
    )
    if not refs:
        sys.exit("Dockerfile has no python base image")
    if len(set(refs)) != 1:
        sys.exit(f"Dockerfile python refs differ: {sorted(set(refs))}")
    if "@sha256:" not in refs[0]:
        sys.exit(f"Dockerfile python ref carries no digest: {refs[0]}")
    return refs[0]


def workflow_python_refs() -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        found: list[str] = re.findall(r"image:\s*(python:\S+)", workflow.read_text())
        refs.extend((workflow.name, ref) for ref in found)
    return refs


def ref_version(ref: str) -> str:
    tag = ref.partition("@")[0]
    return tag.partition("-")[0]


def check_uv_pins() -> None:
    mise, docker = mise_pin(), dockerfile_pin()
    if mise != docker:
        sys.exit(f"uv pins drifted: mise.toml {mise}, Dockerfile {docker}")
    allowed = pyproject_range()
    if not allowed.contains(mise):
        sys.exit(f"uv pin {mise} is outside pyproject required-version {allowed}")


def check_python_pins() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    docker = dockerfile_python_refs()
    expected_image = f"python:{docker}"
    for workflow, image in workflow_python_refs():
        if image != expected_image:
            sys.exit(
                f"python pins drifted: {workflow} has {image}, Dockerfile has {expected_image}"
            )
    version = ref_version(docker)
    pinned = (ROOT / ".python-version").read_text().strip()
    if version != pinned:
        sys.exit(f"python pins drifted: Dockerfile tag {version}, .python-version {pinned}")
    required: str = pyproject["project"]["requires-python"]
    allowed = SpecifierSet(required)
    if not allowed.contains(version):
        sys.exit(f"python pin {version} is outside pyproject requires-python {allowed}")
    major, minor = version.split(".")[:2]
    ruff_target: str = pyproject["tool"]["ruff"]["target-version"]
    if ruff_target != f"py{major}{minor}":
        sys.exit(f"python pins drifted: ruff target-version {ruff_target}, Dockerfile {version}")
    pyright_version: str = pyproject["tool"]["pyright"]["pythonVersion"]
    if pyright_version != f"{major}.{minor}":
        sys.exit(
            f"python pins drifted: pyright pythonVersion {pyright_version}, Dockerfile {version}"
        )


def main() -> None:
    check_uv_pins()
    check_python_pins()


if __name__ == "__main__":
    main()
