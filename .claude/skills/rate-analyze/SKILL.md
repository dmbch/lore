# /rate-analyze

Offline sniff test over rate-run artifacts: pass rates per probe, stage stability across runs, divergent outputs side by side.

---
context: fork
allowed-tools: Read, Glob, Grep, Bash
---

## Instructions

Given "$ARGUMENTS" (`<artifacts-dir> [<baseline-dir>]`), produced by `mise run rate` (the runner prints the artifacts dir; `--artifacts` places it):

1. **Tally the rate log.** `rate.jsonl` holds one `{"id", "outcome"}` line per test per run, all k runs appended. Report pass rate per test (`passed / (passed + failed)`), worst first; note skips separately. A missing or all-skip log means the runs never attempted anything; say so and stop.

2. **Group the traces.** Each run wrote its own `trace-run<n>.jsonl`: Lore's structlog events at debug level. Group lines by `correlation_id` within each file; each group is one consult. The stages that matter:
   - `consult.interpret.result`: normalized `question`, atomic `propositions`, retrieval `keywords`.
   - `consult.reason.result`: the Archivist's `reasoning` and `resolutions` (each names `corroborates`, `contributes`, or both null, plus a `contradicts` list).
   - `consult.note_contents`: the Archivist's `notes` on ambiguous propositions.
   - `consult.enrich.result`: the retrieved neighborhood (`id`, `c_herd`, `oracle_count`, `score`), context for judging target choices.

   Correlation ids are per call, so they do not align across runs. Align consults across run files by their question and hypothesis content; when the mapping to rate-log test ids is unclear, read the test source the selection names (e.g. `tests/e2e/test_consult_shapes.py`) and match probes by the texts they submit.

3. **Report per probe, across runs.** Atom count (length of `propositions`), classification kinds per resolution (`corroborates` / `contributes` / `contradicts` targets), and note count. Flag instability: atom-count divergence, corroborates-target flaps, classification flips (corroborates in one run, contributes in another), note-count swings. For every flag, show the divergent outputs side by side: the atom texts, the chosen target ids with their `c_herd` and scores, the notes. Counts are mechanical; whether two different splits are the same claims is the semantic judgment this skill exists for; say which divergences are benign rephrasings and which change meaning.

4. **Baseline delta.** With a second directory, run the same tally there and report the delta per probe: pass-rate movement and stage-stability changes. A candidate rate alone proves nothing; the delta is the measurement (docs/testing.md).

5. **Never trigger runs.** Analysis is offline; rate runs are metered live spend and the programmer's alone (`.claude/rules/llm-spend.md`). At k = 5 a one-run difference is noise: when a rate is ambiguous, recommend the programmer re-measure with `--runs 10`, and stop there.

## Reference

`references/example-artifacts/` is the offline test bed: two runs, three probes, with a planted atom-split divergence (2 atoms vs 3) and a planted corroborates-target flap. A correct analysis flags exactly those two and calls the third probe stable.
