# Measurements

The record layer: what was measured, when, with which command, and what it
means. [testing.md](testing.md) owns the method; the artifact dirs each run
prints own the raw receipts; this file owns the interpreted result. Newest
first. Numbers compare only within one entry unless the entry says
otherwise: archives, metrics, and prompts drift between sessions.

## 2026-08-21: the audit-residual prompt batch

`mise run rate -- tests/e2e/test_interpreter_decomposition.py
tests/e2e/test_consult_shapes.py`, after `golden-rebuild` and `e2e` both came
back green: 27 tests, 5 runs, 135 executions, zero failures. Every probe 5/5,
so per-test detail is uniform. k=5 meets the protocol floor. Six prompt
commits rode it, the `feat(prompts)` set from "close the audit residual's
prompt-facing wording" through "name the oracle count where the register
implies a crowd", against the last pre-batch prompt revision, "docs: close
the audit residual's wording and ownership fixes". Subjects, not hashes:
this repo lands by rebase-and-merge, so hashes do not survive landing.

What it settles: nothing in the batch regressed decomposition or write shapes.
Across five runs the four traced consults agreed on atom counts, classification
kinds, corroborates targets, and contradicts lists, and enrichment was
identical throughout (`c_herd` 0.175, `oracle_count` 1 on every target). Two
text divergences, both benign. Run 4 dropped an "also" from a balsamic atom.
Run 3 left "HTTP" unnormalized where the other four expanded it: a real miss
of the Interpreter's acronym rule at 1-in-5, absorbed whole by retrieval
because the keyword lane carried both surface forms. It slipped on a fixture
that does not assert normalization; the one that does passed 5/5. The
`shapes-neg-paraphrase` note count moved `[0,0,1,0,1]`, both notes recording
the same most-exact-match tiebreak. Notes are discretionary, so that is a
discretion swing, not a classification flip.

What it cannot settle, which is the batch's hole and not the run's: this
selection cannot see an answer. The Scribe changes execute client-side and are
unattestable in-repo. The answer-register change is observable in principle,
but neither rate suite reads an answer, and the wider e2e lane judges answers
only for acknowledgement and conflict, never for cardinality. The register edit
therefore ships unobserved, and the instrument that would see it does not exist
yet.

No delta was run. A candidate rate alone proves nothing, and that stands; what
makes it acceptable here is that no behavioral claim is being made. The edits
are doctrinal and this run is a regression check, so a delta would price a
baseline for metrics nobody says moved.

The archive moved under this run. `golden-rebuild` produced a corpus differing
from the committed one in a single row of 28, "Hypertext Transfer Protocol ...
Transport Layer Security" resampled to "HTTP ... TLS", so recall figures do not
compare across it. Rebuilds redraw content, not just ids; the archive is a
sampled artifact and the entry that says so is in TODO.md.

Two fixes landed off the back of it: the answer now rides
`consult.reason.result`, and every rate dir now carries `manifest.json`.
Both postdate this run, so this is the last entry resting on the
operator's account of what changed rather than on fingerprints.

## 2026-08-17: recall after the litellm bump

`mise run recall` on the rebased branch, an hour after the baseline below
and with the production litellm bump from main in place: no provider error,
**recall@10 = 1.000**, interlopers 5 (was 4), MRR 1.000.

Cells moved, and this run cannot say why. `abbrev-cap-composite` sent the
scen3 hypothesis from composite rank 1 to 5 while its sibling took the top
slot; `planetary-orbit-direction` lost the Mars seed from the authority lane
(rank 4 to `-`); the two scen3 hypotheses swapped in
`abbrev-bridge-hypothesis`. Either the bump changed something or the
interpreter reshuffled near-ties on its own. The evidence leans to noise:
authority ranks follow the interpreter's keywords, and that same Mars
authority cell already flapped `-` to 2 between 08-13 and 08-16.

The floor that would settle it is stale. Zero unstable cells across three
runs was measured against the 10-hypothesis archive; at 28 there are far
more near-ties for a keyword change to flip, so rank stability is a property
of the archive and does not transfer across a corpus change. Re-price with
`mise run recall-protocol -- -k 3` before reading any future single-run
delta on this archive as signal.

## 2026-08-17: changed probes under the crowded archive

`mise run rate -- tests/e2e/test_interpreter_decomposition.py
tests/e2e/test_archivist_aggregation.py`: 31 tests, 4 runs, 124 executions,
zero failures. Every probe 4/4, so per-test detail is uniform. The run was
interrupted before the fifth pass and its artifact directory did not
survive, which is why this entry cites stdout rather than a receipt; k=4
falls one short of the k>=5 protocol.

What it settles: tightening the two archivist probes from "at most one"
attestation per seed to exactly one did not buy flakiness, so the model
reliably corroborates the paraphrase and contradicts both seeds. The
metric-notation rule generalizes to held-out notation (p95 in a payments
hypothesis), so it is witnessed rather than witnessed by its own example.
The keyword cap of 8 held on every run, which makes the retrieval slice at
10 the pure backstop it was meant to be. All of it against the archive
grown to 28 hypotheses, where these suites had only ever seen 10.

## 2026-08-17: first contested recall baseline

The corpus grew from 11 seeds to 28 (17 distractors sharing the labeled
targets' entities and sentence shapes), and `mise run golden-rebuild`
stored every one as an orthogonal-novel: 28 hypotheses, 28 attestations,
no paraphrase collapse, 363 KB compressed against the 1 MiB budget. No
distractor corroborated into a labeled target, so no target's epistemic
state moved, and `mise run e2e` came back 45/45 on the denser archive.

`mise run recall`: **recall@10 = 1.000** with no squeeze marker, the first
run where that number is evidence: 28 hypotheses contest 10 pool slots, so
18 could have been pushed out. **interlopers = 4**, the live headline.
MRR = 1.000 remains saturated (every query lands a target at rank 1) and is
now the least informative of the three.

The margin, which is what makes the pass meaningful: `keyword-rich-composite`
holds its fourth expected hypothesis at rank 7 with three crowders above it,
so it sits three slots from a genuine recall failure. The crowders are the
distractors working as designed, each sharing an entity with the target it
competes against: "The Meridian Tower in Harborview has forty-two floors" at
rank 3, "The Valletta maritime academy admits sixty cadets each year" at 5,
"Cedarbrook Health operates four regional clinics" at 6. On
`planetary-orbit-direction`, the ecliptic distractor takes rank 2 between the
two expected seeds.

Baseline reset, not a continuation: the scen3 pair stored as two hypotheses
this rebuild where it collapsed to one before, moving the denominator from
17 expected entries to 18, and the archive itself is new. Nothing here
compares to an entry above.

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
