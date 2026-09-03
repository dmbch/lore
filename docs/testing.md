# Testing

The three checks in [CLAUDE.md](../CLAUDE.md) gate every commit. This document
covers the lanes that run outside them: mutation, end-to-end, the golden
archive, and retrieval recall. Standing constraints, not open work; intended
work lives in [TODO.md](../TODO.md), and the record of measurements taken
lives in [measurements.md](measurements.md).

**An instrument must be able to fail.** A metric pinned by its fixture, an
assertion a validator already guarantees, or a scan over a surface that is
always empty reports the same value forever and reads as evidence. Annotating
one as "structural" does not redeem it; it teaches readers to trust the dead
number beside the live ones. Before adding a check, name the input that would
make it fail, and prefer a positive control where the surface can legitimately
be empty. `mise run recall` refuses to print recall at a depth nothing can
fall out of; the leak scan proves its capture is armed before scanning it.

## The math mutation floor

`mise run mutation`, configured in `[tool.mutmut]`. Scope is `src/lore/math/*`
against `tests/math`. Not a commit gate.

The campaign stands at 461 mutants, 447 killed, 14 survived (97%). Those 14 are
inspected equivalent mutants, and the set itself is the threshold: any change
to it, either direction, is the signal. A new name is a test gap. A missing one
is a suppression or a dead line.

| Site | Mutants | Why equivalent |
|---|---|---|
| `compute_oracle_trust` | 86 | defensive clamp, fires only on ~1-ulp noise |
| `to_opinion` | 12, 23, 24 | output-identical scalar sign boundaries |
| `maximize_uncertainty` | 38, 48, 58 | defensive clamps |
| `_acbf_pair` | 1, 16, 19, 20, 21, 22 | routing unreachable via `to_opinion`, or agreeing within noise |
| `fuse` | 12 | dogmatic subsets are pre-partitioned, so the guard is dead by architecture |

Pragma suppression of the 14 was built and reverted on principle: mutmut
registers trailing pragmas at statement level and blocks by line range, so every
available scope also swallowed killable mutants beside the equivalent one (the
clamp line carries the live division; the maximize block took 36 mutants for 3
equivalents). Scope is measured in silenced signal, so a suppression that cannot
isolate the false positive is no suppression at all.

Cached verdicts stand across reruns. Source edits invalidate via function
hashes; test-only edits do not. After changing tests, wipe `mutants/` or rerun
mutants by name.

## The golden archive

`tests/e2e/fixtures/golden.db.gz` stands in for the 28 live seed consults in
`tests/e2e/corpus.py`, and it bakes write-time ledger math. Rebuild triggers:
corpus, prompt, model, or epistemics and trust-math changes.

A rebuild redraws content, not only ids and embeddings. Storage is verbatim,
so wording no longer resamples; decomposition still does. Seeding runs the
real Interpreter, and its splitting is stochastic: the same composite flapped
2 atoms vs 1 across same-day rebuilds (2026-08-10), both passing e2e. The
archive is a sampled artifact used as a fixture. Treat a rebuild as a new
baseline rather than a refresh of the old one, and read `manifest.json` in a
rate dir to tell which archive a number was taken against.

```bash
mise run golden-rebuild
mise run e2e
```

Both are manual, programmer's terminal only. Per-worker copies re-base
attestation timestamps to now, and the bootstrap dimension check fails a stale
fixture loud. Size budget: 1 MiB, enforced.

The knowledge arc keeps live seeding, because the write path is what it tests.
The decay test keeps its one live seed, because it runs different epistemics.

## End-to-end

`mise run e2e` mirrors the flags in the release job; keep the two in sync. The
suite is gemini-coupled: `require_gemini` autouse-skips without a key, and the
golden bakes gemini embeddings, so any other vendor needs a way past both.

Flake posture: the deixis probe is a known stochastic class, and a flake blocks
the release tag until a workflow re-run. `pytest-rerunfailures` was tried and
dropped (2026-08-02); it broke mutmut's runner and masks a genuinely degrading
probe. Re-running the workflow is the accepted remedy.

Rate runs: one green pass on an optional-output behavior proves nothing, since
it passes at base rate. Pin prompt-behavior claims at k >= 5 with `mise run
rate -- <selection>`: k fresh pytest processes, per-test pass rates printed
worst first. Every run is metered live spend, so k = 5 is the default floor;
raise it per claim (`--runs 10`) only when a rate is ambiguous. Runs are
sequential by default; concurrency changes API pressure, so opt in explicitly
with `mise run rate -- -n auto` when that pressure is the point. A candidate
rate alone proves nothing either: measure the delta by running the old prompt
against the same fixtures.

Every rate run is paid; its artifacts are the receipts and always persist:
the rate log at `rate.jsonl`, one stage trace per run at
`trace-run<n>.jsonl`, the consult debug events (`consult.interpret.result`,
`consult.reason.result`, note contents) joined per consult by
`correlation_id`, and `manifest.json`. The manifest is what makes a receipt
self-describing: commit, working-tree cleanliness, model pin per vendor role,
and a digest of every prompt, config, and the golden archive. It is written
before the first run, so an interrupted measurement still says what produced
it. Two artifact dirs whose fingerprints differ are not a delta on the thing
you changed; they are a delta on everything that moved. They land in a fresh `lore-rate-*` tempdir whose path the
runner prints; `--artifacts DIR` places them deliberately instead. Trace
files carry hypothesis texts and the answers built from them, so mind which
machine that tempdir is on. The split is by level, not by field: INFO
counts and hashes content (`resolution.contribute`), DEBUG carries it, and
the trace sink runs at DEBUG. The
`/rate-analyze` skill is the consumer: the offline sniff test over atom
counts, classification kinds, and divergent outputs across runs.

## Retrieval recall

`mise run recall` scores the two-lane search against the labeled query set in
`tests/e2e/queries.py`, over a fresh copy of the golden archive. Per query it
runs one fast interpret call, one embedding batch, and three search passes
(composite plus each lane isolated); the archivist is never invoked and
nothing is written. Settings load from the ambient config, so a local
`lore.toml`'s retrieval weights flow into the measurement: the eval scores
the config you'd run. Manual and metered, never CI: programmer's terminal
only.

A `-` rank means the lane never surfaced the hypothesis within the result
limit: zero-score pool filler is dropped before ranking, and each search
pass truncates to the limit. Every lane can show one once the archive
outgrows what a lane fetches (limit times fan-out).

Recall is the guard the eval exists for: retrieval bounds paraphrase
detection, and a paraphrase the Archivist never sees becomes a false novel
on an append-only ledger. It is reported at a pool depth the archive can
overflow: the configured limit once the corpus outgrows it, and a third of
the archive until then, marked `(squeezed)`. The squeeze is a fallback for
a corpus too small to contest the pool, not the intended state; the corpus
carries distractors precisely so the real limit binds. Reporting 1.000 at a
depth nothing can fall out of promises nothing at all.

Alongside it, `interlopers` counts non-expected hypotheses ranked above a
query's worst expected hit: finer-grained than recall, and the only signal
that catches a non-expected hypothesis wedged *between* two expected ones.
MRR is live but saturated: it can fall, never rise.

Every recall run is paid; its artifacts are the receipts and always persist:
one JSONL row per query at `recall.jsonl`, in a fresh `lore-recall-*` tempdir
whose path the runner prints; `--artifacts DIR` places them deliberately
instead. A rerun into the same dir replaces the receipt; runs never
concatenate. Each row carries the interpreted keywords and propositions
alongside the per-expected ranks, so the receipt doubles as surface-form
evidence for prompt deltas.

`mise run recall-protocol` drives a whole measurement session:

```bash
mise run recall-protocol -- --rebuild --old-ref <pre-change-ref> --artifacts <dir>
```

`--rebuild` reseeds the golden archive first. A prompt change is a rebuild
trigger, and a baseline against an archive seeded under another prompt
measures a mixed system; rebuilding first puts the archive and the query
pipeline on the same prompt. Omit it when the committed archive already
matches the working tree's prompt.

`-k N` repeats the candidate run; every repeat is a full paid run. Cells
that move between identical runs are the interpreter's noise floor, printed
before the delta: a movement within the floor is noise, not evidence. The
one licence for reading a k=1 delta as signal is a floor already measured
at zero.

Both recall runs then share that frozen archive: the candidate prompt from
the working tree and, with `--old-ref`, the old prompt extracted from git.
The driver joins the two receipts and prints per-lane rank movements,
regressions first; the regression channel is authority-lane rank loss on
keyword-rich queries. Without `--old-ref` the protocol is a plain
re-baseline.

## Model versions

Both gemini roles pin a stable version in `src/lore/config/vendors/gemini.toml`. They
rode `-latest` until 2026-08-15, when the alias flipped to Gemini 3.7 flash
mid-acceptance: live behavior shifted and the deixis shapes probe went 0/5,
back to 5/5 under the pin. A rolling alias runs someone else's release
schedule against a calibrated system; frontier dogfooding was not worth
that. Model adoption is deliberate: bump the pin on a branch, run
`recall-protocol -- --rebuild --old-ref`, e2e, and the shapes and
decomposition rates, and re-probe the model tuning facts before landing.

After any alias flip, run e2e deliberately and re-probe tuning. The 2026-07-21
flip to Gemini 3 made the inherited fast-role `temperature = 0.0` pin toxic and
produced interpreter grounding failures; vendor files own temperature now.
Flash also declines to split very large compound hypotheses, which is resolution
loss rather than corruption.
