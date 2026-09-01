"""Check the toolchain pins agree.

uv: mise.toml, the Dockerfile, and every workflow setup-uv step carry the
same exact version, and that version satisfies pyproject's required-version
range. The setup-uv scan expects `version:` as the first with: key of each
step; a step in any other shape fails the count assertion instead of
slipping past unpinned. python: the Dockerfile
base image is authoritative; .python-version, workflow container images,
requires-python, ruff's target-version, and pyright's pythonVersion must all
agree with its tag. The workflow scan recognizes bare `python:` refs and YAML
anchor definitions (`image: &name python:...`); YAML aliases (`image: *name`)
are invisible to it, which is safe because an alias cannot differ from its
anchor. Registry-qualified refs (docker.io/library/python) would slip past.

uv enforces required-version at runtime, but the Dockerfile stage only hits it
during the image build on the release path, after the e2e spend. This static
check moves that failure to PR CI and `mise run check`. The range (not an
exact pin) exists so Dependabot's older bundled uv can still resolve the
lockfile.

Dependabot only ever bumps the Dockerfile, the one ecosystem it watches, so
mise.toml, the workflow setup-uv pins, and the workflow python image ref all
drift out from under it every time. `--fix` treats the Dockerfile as
authoritative and rewrites the other three back into agreement. It does not
touch the Dockerfile, and for uv it does not preflight whether the target
version has reached mise's install backend or setup-uv's checksum manifest:
astral publishes the docker image, GitHub release, and setup-uv manifest
from the same pipeline, and Dependabot's own scan cadence already trails
that by days in practice. The extraordinary case where an upstream channel
still lags is left to fail loudly at `mise install` or the CI setup-uv step,
same as any other pin drift.

For python, `--fix` only ever rewrites the workflow image ref to match the
Dockerfile's tag and digest exactly, a digest-only rebuild (a Debian
security patch, same python version) is always safe to sync mechanically.
It never touches .python-version, requires-python, ruff's target-version, or
pyright's pythonVersion: a real version bump changes those deliberately, and
that is a human call, not a mechanical one. If the Dockerfile's version
number itself moved, the self-check after `--fix` still fails and names
exactly which of those pins is now behind.
"""

import argparse
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
        r"^FROM ghcr\.io/astral-sh/uv:(\S+)",
        (ROOT / "Dockerfile").read_text(),
        flags=re.MULTILINE,
    )
    if match is None:
        sys.exit("Dockerfile has no ghcr.io/astral-sh/uv stage")
    version, _, digest = match.group(1).partition("@")
    if not digest.startswith("sha256:"):
        sys.exit(f"Dockerfile uv ref carries no digest: {match.group(1)}")
    return version


def workflow_uv_pins() -> list[tuple[str, str]]:
    pins: list[tuple[str, str]] = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        text = workflow.read_text()
        found: list[str] = re.findall(
            r'astral-sh/setup-uv@[^\n]*\n\s*with:\n\s*version: "([^"]+)"',
            text,
        )
        steps = text.count("astral-sh/setup-uv@")
        if len(found) != steps:
            sys.exit(
                f"{workflow.name}: {steps} setup-uv steps, {len(found)} pinned; "
                'every setup-uv step pins uv with version: "<exact>" as the first with: key'
            )
        pins.extend((workflow.name, pin) for pin in found)
    return pins


def dockerfile_python_ref() -> str:
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
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        found: list[str] = re.findall(
            r"^\s*image:\s*(?:&\S+\s+)?(python:\S+)",
            workflow.read_text(),
            flags=re.MULTILINE,
        )
        refs.extend((workflow.name, ref) for ref in found)
    return refs


def ref_version(ref: str) -> str:
    tag = ref.partition("@")[0]
    return tag.partition("-")[0]


def check_uv_pins() -> None:
    mise, docker = mise_pin(), dockerfile_pin()
    if mise != docker:
        sys.exit(f"uv pins drifted: mise.toml {mise}, Dockerfile {docker}")
    workflow_pins = workflow_uv_pins()
    if not workflow_pins:
        sys.exit("no workflow provisions uv via setup-uv; the CI pin scan has nothing to police")
    for workflow, pin in workflow_pins:
        if pin != mise:
            sys.exit(f"uv pins drifted: {workflow} setup-uv {pin}, mise.toml {mise}")
    allowed = pyproject_range()
    if not allowed.contains(mise):
        sys.exit(f"uv pin {mise} is outside pyproject required-version {allowed}")


def fix_uv_pins() -> None:
    docker = dockerfile_pin()
    allowed = pyproject_range()
    if not allowed.contains(docker):
        sys.exit(
            f"Dockerfile uv pin {docker} is outside pyproject required-version {allowed}; "
            "refusing to propagate it"
        )

    mise_path = ROOT / "mise.toml"
    fixed_mise, count = re.subn(
        r'^uv = "[^"]+"$', f'uv = "{docker}"', mise_path.read_text(), count=1, flags=re.MULTILINE
    )
    if count == 0:
        sys.exit('mise.toml has no uv = "..." line to fix')
    mise_path.write_text(fixed_mise)

    pattern = re.compile(r'(astral-sh/setup-uv@[^\n]*\n\s*with:\n\s*version: )"[^"]+"')
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        text = workflow.read_text()
        fixed, changed = pattern.subn(rf'\1"{docker}"', text)
        if changed:
            workflow.write_text(fixed)

    check_uv_pins()
    print(f"uv pins synced to {docker}")


def fix_python_pins() -> None:
    docker = dockerfile_python_ref()
    expected_image = f"python:{docker}"

    pattern = re.compile(r"^(\s*image:\s*(?:&\S+\s+)?)python:\S+", flags=re.MULTILINE)
    changed: list[str] = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        text = workflow.read_text()
        fixed, count = pattern.subn(rf"\1{expected_image}", text)
        if count:
            workflow.write_text(fixed)
            changed.append(workflow.name)
    if not changed:
        sys.exit("no workflow carries a python container image; nothing to fix")

    check_python_pins()
    print(f"python image pins synced to {expected_image}")


def check_python_pins() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    docker = dockerfile_python_ref()
    expected_image = f"python:{docker}"
    workflow_refs = workflow_python_refs()
    if not workflow_refs:
        sys.exit("no workflow carries a python container image; tests must run in the image base")
    for workflow, image in workflow_refs:
        if image != expected_image:
            sys.exit(
                f"python pins drifted: {workflow} has {image}, Dockerfile has {expected_image}"
            )
    version = ref_version(docker)
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit(f"Dockerfile python tag is not a patch-level pin: {version}")
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
    parser = argparse.ArgumentParser(
        description="Check the toolchain pins agree, or sync them with --fix."
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help=(
            "rewrite mise.toml, workflow setup-uv pins, and the workflow python image "
            "ref to match the Dockerfile"
        ),
    )
    args = parser.parse_args()
    if args.fix:
        fix_uv_pins()
        fix_python_pins()
        return
    check_uv_pins()
    check_python_pins()


if __name__ == "__main__":
    main()
