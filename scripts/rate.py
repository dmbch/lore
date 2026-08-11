"""Drive k fresh-process pytest runs over the stochastic e2e suites and report
per-test pass rates. One green proves nothing at base rate; the prompt suites'
measurement protocol is a rate at k >= 5 (docs/testing.md).

Each run gets a fresh process, so the session-scoped system fixtures cannot
leak one run's writes into the next. The runs feed one shared log via the
LORE_RATE_LOG hook in tests/conftest.py.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple


class Outcome(NamedTuple):
    test: str
    passed: int
    failed: int
    skipped: int


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
    parser = argparse.ArgumentParser(
        description="Measure per-test pass rates over k fresh pytest processes."
    )
    # No short -k: the runner's contract is "everything else is pytest argv",
    # and pytest's own -k is the selection flag people reach for first.
    parser.add_argument("--runs", type=_positive_int, default=5, help="pytest processes to drive")
    args, passthrough = parser.parse_known_args()
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "rate.jsonl"
        # -m e2e and --no-cov undo the repo addopts: -m 'not e2e' would
        # deselect the suites this instrument exists for, and the coverage
        # gate fails every subset run (same reasoning as [tool.mutmut]).
        for _ in range(args.runs):
            result = subprocess.run(  # noqa: S603 - argv is built in-process, no shell, no untrusted input
                [sys.executable, "-m", "pytest", "-m", "e2e", "--no-cov", "-q", *passthrough],
                env=os.environ | {"LORE_RATE_LOG": str(log)},
                check=False,
            )
            # 0 and 1 are both expected: a rate run measures failures. Anything
            # else (usage error, interrupted, no tests collected) is not a
            # measurement and must not read as one.
            if result.returncode not in (0, 1):
                sys.exit(result.returncode)
        # A collect-only run leaves no log at all; a keyless run leaves only
        # skips. Neither is a measurement, and exit 0 must not claim one.
        outcomes = tally(log) if log.exists() else []
        print(format_table(outcomes))
        if not any(o.passed + o.failed for o in outcomes):
            sys.exit("rate: no attempts recorded; is GEMINI_API_KEY set?")


if __name__ == "__main__":
    main()
