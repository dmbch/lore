# Testing

The three checks in [CLAUDE.md](../CLAUDE.md) gate every commit. This document
covers the lanes that run outside them: mutation, end-to-end, the golden
archive, and retrieval recall. Standing constraints, not open work; intended
work lives in [TODO.md](../TODO.md).

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

`tests/e2e/fixtures/golden.db.gz` stands in for eleven live seed consults in the
aggregation suite, and it bakes write-time ledger math. Rebuild triggers: corpus,
prompt, model, or epistemics and trust-math changes.

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
the rate log at `rate.jsonl` and one stage trace per run at
`trace-run<n>.jsonl`, the consult debug events (`consult.interpret.result`,
`consult.reason.result`, note contents) joined per consult by
`correlation_id`. They land in a fresh `lore-rate-*` tempdir whose path the
runner prints; `--artifacts DIR` places them deliberately instead. Trace
files carry hypothesis texts, so mind which machine that tempdir is on. The
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
pass truncates to the limit. And recall@limit discriminates only once the
archive outgrows that limit; on the current golden corpus every pool holds
the whole archive, so the per-lane ranks carry the signal and recall@limit
is structurally 1.0.

Every recall run is paid; its artifacts are the receipts and always persist:
one JSONL row per query at `recall.jsonl`, in a fresh `lore-recall-*` tempdir
whose path the runner prints; `--artifacts DIR` places them deliberately
instead. A rerun into the same dir replaces the receipt; runs never
concatenate. Each row carries the interpreted keywords and propositions
alongside the per-expected ranks, so the receipt doubles as surface-form
evidence for prompt deltas.

The delta protocol: same frozen archive, two runs. Extract the old prompt,
point the first run at it, then run the candidate without `--prompt`:

```bash
git show <pre-change-ref>:src/lore/prompts/interpreter.md > /tmp/interpreter-old.md
mise run recall -- --prompt /tmp/interpreter-old.md --artifacts <dir-old>
mise run recall -- --artifacts <dir-new>
```

Compare per-lane ranks between the runs; the regression channel is
authority-lane rank loss on keyword-rich queries.

## Model aliases

Both gemini roles ride `-latest`. Frontier dogfooding is itself eval signal, and
the other vendors have no rolling aliases anyway.

After any alias flip, run e2e deliberately and re-probe tuning. The 2026-07-21
flip to Gemini 3 made the inherited fast-role `temperature = 0.0` pin toxic and
produced interpreter grounding failures; vendor files own temperature now.
Flash also declines to split very large compound hypotheses, which is resolution
loss rather than corruption.
