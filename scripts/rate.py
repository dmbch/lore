"""Drive k fresh-process pytest runs over the stochastic e2e suites and report
per-test pass rates. One green proves nothing at base rate; the prompt suites'
measurement protocol is a rate at k >= 5 (docs/testing.md).

Each run gets a fresh process, so the session-scoped system fixtures cannot
leak one run's writes into the next. The runs feed one shared log via the
LORE_RATE_LOG hook in tests/conftest.py.

Artifacts persist every run: the rate log at rate.jsonl, one stage trace
per run at trace-run<n>.jsonl, and manifest.json naming what produced them,
in a fresh lore-rate-* tempdir (path printed) unless --artifacts DIR places
them deliberately.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tomllib
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple, cast

# Every input whose drift makes two receipts incomparable. docs/measurements.md
# compares numbers only within one entry precisely because these move between
# sessions; fingerprinting them is what lets a later delta check rather than
# trust that it is comparing like with like.
FINGERPRINTED = (
    "src/lore/prompts/*.md",
    "src/lore/config/*.toml",
    "src/lore/config/vendors/*.toml",
    "tests/e2e/fixtures/golden.db.gz",
)


class Outcome(NamedTuple):
    test: str
    passed: int
    failed: int
    skipped: int


class Manifest(NamedTuple):
    """What produced a set of receipts.

    ``git_head`` is None outside a checkout (an exported tree still measures,
    it just cannot cite a commit); ``dirty`` carries nothing in that case.
    ``selection`` is the pytest argv verbatim, flags included, since a
    ``-n auto`` changes what was measured as surely as a path does.
    """

    git_head: str | None
    dirty: bool
    runs: int
    selection: list[str]
    models: dict[str, str]
    fingerprints: dict[str, str]


class ArtifactLayout(NamedTuple):
    rate_log: Path
    manifest: Path
    traces: list[Path]


def artifact_layout(root: Path, *, runs: int) -> ArtifactLayout:
    return ArtifactLayout(
        rate_log=root / "rate.jsonl",
        manifest=root / "manifest.json",
        traces=[root / f"trace-run{run}.jsonl" for run in range(1, runs + 1)],
    )


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no untrusted input
            ["git", "-C", str(root), *args],  # noqa: S607 - git from PATH, as everywhere else here
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _model_of(section: object) -> str | None:
    """The model pin in one vendor-config section, if that section names one."""
    if not isinstance(section, dict):
        return None
    # tomllib hands back genuinely dynamic data; the cast is that boundary,
    # and the value is re-checked before it reaches the manifest.
    model = cast("dict[str, object]", section).get("model")
    return model if isinstance(model, str) else None


def _model_pins(root: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for path in sorted((root / "src/lore/config/vendors").glob("*.toml")):
        sections: dict[str, object] = tomllib.loads(path.read_text(encoding="utf-8"))
        for role, section in sections.items():
            model = _model_of(section)
            if model is not None:
                pins[f"{path.stem}.{role}"] = model
    return pins


def _fingerprints(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for pattern in FINGERPRINTED:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                found[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()[:16]
    return found


def build_manifest(root: Path, *, runs: int, selection: Sequence[str]) -> Manifest:
    return Manifest(
        git_head=_git(root, "rev-parse", "HEAD"),
        dirty=bool(_git(root, "status", "--porcelain")),
        runs=runs,
        selection=list(selection),
        models=_model_pins(root),
        fingerprints=_fingerprints(root),
    )


def refuse_reused_receipts(rate_log: Path) -> None:
    """Exit rather than blend two measurements into one set of receipts.

    The rate log appends per run within one invocation; across invocations
    an existing log would pool incomparable measurements while the manifest
    is rewritten to describe only the newest. Receipts never concatenate.
    """
    if rate_log.exists():
        sys.exit(f"rate: {rate_log} already holds receipts; use a fresh --artifacts dir")


def tally(log: Path) -> list[Outcome]:
    counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for line in log.read_text(encoding="utf-8").splitlines():
        record: dict[str, str] = json.loads(line)
        counts[record["id"]][record["outcome"]] += 1
    return [
        Outcome(
            test=test,
            passed=outcomes["passed"],
            failed=outcomes["failed"],
            skipped=outcomes["skipped"],
        )
        for test, outcomes in counts.items()
    ]


def _rate(outcome: Outcome) -> float:
    # Zero attempts (a fully skipped test) carries no evidence of
    # unreliability; rate 1.0 sinks it below every test that actually failed.
    attempted = outcome.passed + outcome.failed
    return outcome.passed / attempted if attempted else 1.0


def format_table(outcomes: Sequence[Outcome]) -> str:
    rows: list[str] = []
    for outcome in sorted(outcomes, key=lambda o: (_rate(o), o.test)):
        attempted = outcome.passed + outcome.failed
        percent = f"  {100 * outcome.passed / attempted:3.0f}%" if attempted else ""
        skips = f"  skipped {outcome.skipped}" if outcome.skipped else ""
        rows.append(f"{outcome.passed}/{attempted}{percent}  {outcome.test}{skips}")
    return "\n".join(rows)


def _positive_int(text: str) -> int:
    runs = int(text)
    if runs < 1:
        message = "runs must be >= 1"
        raise argparse.ArgumentTypeError(message)
    return runs


def main() -> None:
    # `python scripts/rate.py` puts scripts/ (not the repo root) on
    # sys.path; the shared driver shell resolves from the root. A no-op
    # under pytest, which already resolves the package from the rootdir.
    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from scripts._metered import artifacts_root

    parser = argparse.ArgumentParser(
        description="Measure per-test pass rates over k fresh pytest processes."
    )
    # No short -k: the runner's contract is "everything else is pytest argv",
    # and pytest's own -k is the selection flag people reach for first.
    parser.add_argument("--runs", type=_positive_int, default=5, help="pytest processes to drive")
    parser.add_argument(
        "--artifacts", type=Path, default=None, help="persist the rate log and per-run traces here"
    )
    args, passthrough = parser.parse_known_args()
    root = artifacts_root(args.artifacts, prefix="lore-rate-")
    layout = artifact_layout(root, runs=args.runs)
    refuse_reused_receipts(layout.rate_log)
    # Written before the first run: a measurement interrupted halfway still
    # leaves receipts, and receipts that cannot say what produced them are
    # the failure mode this file exists to prevent.
    manifest = build_manifest(Path(repo_root), runs=args.runs, selection=passthrough)
    layout.manifest.write_text(json.dumps(manifest._asdict(), indent=2) + "\n", encoding="utf-8")
    # -m e2e and --no-cov undo the repo addopts: -m 'not e2e' would
    # deselect the suites this instrument exists for, and the coverage
    # gate fails every subset run (same reasoning as [tool.mutmut]).
    for run in range(args.runs):
        result = subprocess.run(  # noqa: S603 - argv is built in-process, no shell, no untrusted input
            [sys.executable, "-m", "pytest", "-m", "e2e", "--no-cov", "-q", *passthrough],
            env=os.environ
            | {"LORE_RATE_LOG": str(layout.rate_log), "LORE_TRACE_LOG": str(layout.traces[run])},
            check=False,
        )
        # 0 and 1 are both expected: a rate run measures failures. Anything
        # else (usage error, interrupted, no tests collected) is not a
        # measurement and must not read as one.
        if result.returncode not in (0, 1):
            sys.exit(result.returncode)
    # A collect-only run leaves no log at all; a keyless run leaves only
    # skips. Neither is a measurement, and exit 0 must not claim one.
    outcomes = tally(layout.rate_log) if layout.rate_log.exists() else []
    print(format_table(outcomes))
    print(f"artifacts: {root}")
    if not any(o.passed + o.failed for o in outcomes):
        sys.exit("rate: no attempts recorded; is GEMINI_API_KEY set?")


if __name__ == "__main__":
    main()
