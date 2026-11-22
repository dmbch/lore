"""The locked pytest and pytest-xdist pair honors --dist loadgroup.

scripts/e2e.sh relies on it to keep the knowledge arc on one worker: the
arc's steps share one archive, and each worker owns its own, so a split arc
reads an empty archive and fails in a metered run. pytest 9.2.0 with
pytest-xdist 3.8.0 dropped the grouping without a warning
(pytest-dev/pytest-xdist#1407); this keyless run catches that class of
breakage in the commit gate instead. It does not check that e2e.sh passes
the flag or that the arc carries the mark.
"""

import subprocess
import sys
from pathlib import Path

GROUPED_TESTS = 6

_GROUPED_MODULE = f"""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.xdist_group("arc")


@pytest.mark.parametrize("step", range({GROUPED_TESTS}))
def test_step(step: int) -> None:
    with Path("workers.log").open("a") as log:
        log.write(os.environ["PYTEST_XDIST_WORKER"] + "\\n")
"""


def test_loadgroup_runs_an_xdist_group_on_a_single_worker(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_arc.py").write_text(_GROUPED_MODULE)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-n2", "--dist", "loadgroup"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    workers = (tmp_path / "workers.log").read_text().split()
    assert len(workers) == GROUPED_TESTS
    assert len(set(workers)) == 1, f"arc split across workers: {sorted(set(workers))}"
