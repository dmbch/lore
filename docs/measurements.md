# Measurements

The record layer: what was measured, when, with which command, and what it
means. [testing.md](testing.md) owns the method; the artifact dirs each run
prints own the raw receipts; this file owns the interpreted result. Newest
first. Numbers compare only within one entry unless the entry says
otherwise: archives, metrics, and prompts drift between sessions.

## 2026-08-16: recall noise floor

`mise run recall-protocol -- -k 3` on the committed archive: 0 unstable
cells across three candidate runs (17 entries, all lanes), recall@limit
1.000 and MRR 1.000 in each. Within a session the instrument is stable and
k=1 deltas are licensed. Across sessions it is not: the Mars seed's
authority cell read `-` on 08-13 and 2 in every 08-16 run, so day-to-day
interpreter drift is real. Compare receipts only within one protocol
session, the only comparison the driver performs anyway.

## 2026-08-16: decomposition suite under the tightened probe

`mise run rate -- tests/e2e/test_interpreter_decomposition.py`: 22/22 tests
at 5/5. The surface-form probe now requires the short form in a keyword
free of the expansion (a merged phrase would defeat both FTS lookups) and
passes clean; the judge's illustrative-lists clause left every neighbor
stable.

## 2026-08-15: alias flip adjudication

The gemini `-latest` alias flipped to 3.7 flash mid-acceptance. The deixis
shapes probe went 0/5 with identical reasoning each run, back to 5/5 under
the `gemini-3.6-flash` pin, confirming causation; e2e ran 43/44 with the
single failure adjudicated to the flip. Doctrine follow-up in TODO.md
("Gemini 3.7 and the err-toward-novel boundary").

## 2026-08-13: surface-form re-baseline

`mise run recall-protocol -- --rebuild --old-ref main`: archive reseeded
under the candidate prompt (10 hypotheses, the scen3 collapse repeated),
then old vs candidate on that frozen copy: 0/17 entries regressed, every
cell identical. recall@limit 1.000 (structural: every pool holds the whole
archive), MRR 1.000 (textbook: each query's best hit at rank 1). The
zero-score filter surfaced its first honest lane miss (the Mars seed missed
the authority lane that session); `abbrev-cap-composite` resolved 3/3 at
ranks 1-3 with surface-form pairs in the receipts and no eviction.

## 2026-08-13: surface-form delta, pre-redefinition

Delta doctrine, k=5 per rate run:

- Probe, old vs candidate: 0/5 to 5/5. Every old-prompt failure showed the
  predicted mechanism: keywords carried "electrocardiogram", never "ECG".
- Neighbors: no attributable regression. One 4/5 per run on different tests
  (old: colloquial-question; candidate: paragraph-deixis), each 5/5 in the
  counterpart run. Judge noise, not prompt effect.
- Recall delta on the then-frozen archive: identical aggregates both runs;
  `keyword-rich-composite` authority ranks 1-4 in both; receipts show the
  candidate emitting surface-form pairs ("remote procedure call" + "RPC")
  with no eviction of seed-critical terms. The identical-both-runs
  conclusion carries forward. The aggregates (recall@limit 1.000, MRR
  0.827) do not: MRR was later redefined to the textbook per-query form,
  zero-score pool filler was dropped from lane ranks,
  `abbrev-cap-composite` was added, and the re-baseline reseeded the
  archive. Numbers across that line are incommensurable, not a trend.
