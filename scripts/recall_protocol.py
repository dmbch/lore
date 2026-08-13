"""Drive the retrieval-recall protocol: rebuild, recall runs, receipt comparison.

One command for a measurement session. ``--rebuild`` reseeds the golden
archive first, so the archive and the query pipeline share a prompt; a
baseline against an archive seeded under another prompt measures a mixed
system. The candidate run always happens; ``-k N`` repeats it and prints the
cells that moved between identical runs, the interpreter's noise floor: a
delta within the floor is noise, not evidence. ``--old-ref REF`` adds an
old-prompt run against the same archive (the prompt extracted from git) and
prints per-lane rank movements between the two receipts, regressions first.
The comparison reads the JSONL receipts, never live state: the receipt is
the contract.

The movement semantics, formatting, and layout are pure and unit-tested; the
subprocess driver stays untested, like rate.py's main.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from scripts.recall import ExpectedEntry, ReceiptRow

_PROMPT_PATH = "src/lore/prompts/interpreter.md"
_REPO_ROOT = Path(__file__).resolve().parent.parent


class LanePair(NamedTuple):
    old: int | None
    new: int | None


class EntryMovement(NamedTuple):
    query_id: str
    correlation_id: str
    hypothesis_id: str
    composite: LanePair
    proximity: LanePair
    authority: LanePair


class ProtocolLayout(NamedTuple):
    candidate_dir: Path
    old_dir: Path
    old_prompt: Path


def protocol_layout(root: Path) -> ProtocolLayout:
    return ProtocolLayout(
        candidate_dir=root / "candidate",
        old_dir=root / "old",
        old_prompt=root / "interpreter-old.md",
    )


def candidate_dirs(layout: ProtocolLayout, *, k: int) -> list[Path]:
    """candidate, candidate-2, ... candidate-k: one dir per repeat run."""
    repeats = [layout.candidate_dir.with_name(f"candidate-{i}") for i in range(2, k + 1)]
    return [layout.candidate_dir, *repeats]


def regressed(pair: LanePair) -> bool:
    # None is "the lane never matched": losing a rank to it is the sharpest
    # regression there is; gaining one from it is pure improvement.
    if pair.old is None:
        return False
    return pair.new is None or pair.new > pair.old


def _changed(pair: LanePair) -> bool:
    return pair.old != pair.new


def _index(rows: Sequence[ReceiptRow]) -> dict[tuple[str, str, str], ExpectedEntry]:
    entries: dict[tuple[str, str, str], ExpectedEntry] = {}
    for row in rows:
        for entry in row.expected:
            key = (row.query_id, entry.correlation_id, entry.hypothesis_id)
            if key in entries:
                # A last-wins join would read a receipt that concatenated two
                # runs as one clean measurement.
                msg = f"duplicate receipt entry {key}: the receipt mixes runs; re-run the recall"
                raise ValueError(msg)
            entries[key] = entry
    return entries


def compare_receipts(
    *, old: Sequence[ReceiptRow], candidate: Sequence[ReceiptRow]
) -> list[EntryMovement]:
    """Join two receipt sets on (query, correlation label, hypothesis).

    The comparison is defined over two runs of the same query set against the
    same frozen archive; a partial join would read entries only one run saw
    as measurements. Disagreeing keys raise instead.
    """
    old_entries = _index(old)
    candidate_entries = _index(candidate)
    if old_entries.keys() != candidate_entries.keys():
        stray = sorted(old_entries.keys() ^ candidate_entries.keys())
        msg = (
            f"receipts disagree on expected entries: {stray};"
            " compare runs of the same query set against the same archive"
        )
        raise ValueError(msg)
    movements: list[EntryMovement] = []
    for key in sorted(old_entries):
        before, after = old_entries[key], candidate_entries[key]
        query_id, correlation_id, hypothesis_id = key
        movements.append(
            EntryMovement(
                query_id=query_id,
                correlation_id=correlation_id,
                hypothesis_id=hypothesis_id,
                composite=LanePair(old=before.composite, new=after.composite),
                proximity=LanePair(old=before.proximity, new=after.proximity),
                authority=LanePair(old=before.authority, new=after.authority),
            )
        )
    return movements


def _lanes(movement: EntryMovement) -> tuple[LanePair, LanePair, LanePair]:
    return (movement.composite, movement.proximity, movement.authority)


def _cell(pair: LanePair) -> str:
    old = "-" if pair.old is None else str(pair.old)
    new = "-" if pair.new is None else str(pair.new)
    return old if old == new else f"{old}>{new}"


def format_movements(movements: Sequence[EntryMovement]) -> str:
    def key(movement: EntryMovement) -> tuple[int, int, str]:
        lanes = _lanes(movement)
        return (
            -sum(regressed(pair) for pair in lanes),
            -sum(_changed(pair) for pair in lanes),
            movement.query_id,
        )

    rows: list[str] = []
    for movement in sorted(movements, key=key):
        cells = (
            f"c {_cell(movement.composite)}  p {_cell(movement.proximity)}"
            f"  a {_cell(movement.authority)}"
        )
        flag = "  regressed" if any(regressed(pair) for pair in _lanes(movement)) else ""
        rows.append(f"{movement.query_id}  {movement.correlation_id}  {cells}{flag}")
    regressions = sum(any(regressed(pair) for pair in _lanes(m)) for m in movements)
    rows.append(f"{regressions}/{len(movements)} entries regressed")
    return "\n".join(rows)


def noise_floor(runs: Sequence[Sequence[ReceiptRow]]) -> list[EntryMovement]:
    """Cells that move between repeat runs of the same prompt and archive.

    Every repeat is compared against the first run; only entries with at
    least one moved lane are kept. Empty means every cell held: the one
    licence for reading a k=1 delta as signal.
    """
    if not runs:
        return []
    first, *repeats = runs
    return [
        movement
        for repeat in repeats
        for movement in compare_receipts(old=first, candidate=repeat)
        if any(_changed(pair) for pair in _lanes(movement))
    ]


def format_noise(movements: Sequence[EntryMovement], *, runs: int) -> str:
    cells = sum(sum(_changed(pair) for pair in _lanes(m)) for m in movements)
    rows = [
        f"{m.query_id}  {m.correlation_id}  c {_cell(m.composite)}"
        f"  p {_cell(m.proximity)}  a {_cell(m.authority)}"
        for m in movements
    ]
    footer = f"noise: {cells} unstable cells across {runs} candidate runs"
    if cells:
        footer += "; a delta within the floor is not evidence"
    return "\n".join([*rows, footer])


def load_receipts(log: Path) -> list[ReceiptRow]:
    from scripts.recall import ReceiptRow

    lines = log.read_text(encoding="utf-8").splitlines()
    return [ReceiptRow.model_validate_json(line) for line in lines if line]


def _run(argv: Sequence[str]) -> None:
    result = subprocess.run(list(argv), cwd=_REPO_ROOT, check=False)  # noqa: S603 - argv is built in-process, no shell, no untrusted input
    if result.returncode != 0:
        sys.exit(result.returncode)


def _extract_old_prompt(*, ref: str, target: Path) -> None:
    git = shutil.which("git")
    if git is None:
        sys.exit("recall-protocol: git not found")
    result = subprocess.run(  # noqa: S603 - argv is built in-process, no shell, no untrusted input
        [git, "show", f"{ref}:{_PROMPT_PATH}"],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        sys.exit(f"recall-protocol: git show {ref}:{_PROMPT_PATH} failed: {detail}")
    target.write_bytes(result.stdout)


def main() -> None:
    # The whole protocol is metered spend; a keyless run must exit here,
    # before any composition, filesystem work, or subprocess.
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("recall-protocol: GEMINI_API_KEY not set; every step drives live model calls")
    # `python scripts/recall_protocol.py` puts scripts/ (not the repo root)
    # on sys.path, and the receipt schema lives in scripts.recall. A no-op
    # under pytest, which already resolves the package from the rootdir.
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    parser = argparse.ArgumentParser(
        description="Drive the recall protocol: optional rebuild, candidate run, old-prompt delta."
    )
    parser.add_argument(
        "--artifacts", type=Path, default=None, help="protocol root for all receipts"
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="golden-rebuild first (rewrites tests/e2e/fixtures/golden.db.gz)",
    )
    parser.add_argument(
        "--old-ref", default=None, help="git ref of the old interpreter prompt; enables the delta"
    )
    parser.add_argument(
        "-k",
        type=int,
        default=1,
        help="candidate runs; each repeat is a full paid run, k>1 prices the noise floor",
    )
    args = parser.parse_args()
    if args.k < 1:
        parser.error("-k must be >= 1")
    if args.artifacts is None:
        root = Path(tempfile.mkdtemp(prefix="lore-recall-protocol-"))
    else:
        root = args.artifacts
        root.mkdir(parents=True, exist_ok=True)
    # Deferred like the sys.path bootstrap above: scripts.recall resolves
    # only once the repo root is importable.
    from scripts.recall import artifact_layout

    layout = protocol_layout(root)
    recall = [sys.executable, str(_REPO_ROOT / "scripts" / "recall.py")]

    if args.rebuild:
        _run([sys.executable, "-m", "tests.e2e.fixtures.rebuild_golden"])
    runs = candidate_dirs(layout, k=args.k)
    for run_dir in runs:
        _run([*recall, "--artifacts", str(run_dir)])
    if args.k > 1:
        receipts = [load_receipts(artifact_layout(run_dir).recall_log) for run_dir in runs]
        print(format_noise(noise_floor(receipts), runs=args.k))
    if args.old_ref is not None:
        _extract_old_prompt(ref=args.old_ref, target=layout.old_prompt)
        _run([*recall, "--artifacts", str(layout.old_dir), "--prompt", str(layout.old_prompt)])
        old_rows = load_receipts(artifact_layout(layout.old_dir).recall_log)
        candidate_rows = load_receipts(artifact_layout(layout.candidate_dir).recall_log)
        print(format_movements(compare_receipts(old=old_rows, candidate=candidate_rows)))
    print(f"artifacts: {root}")


if __name__ == "__main__":
    main()
