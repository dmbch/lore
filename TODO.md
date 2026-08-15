# TODO

Work we intend to do. Each entry: what, why it matters, options, status.

Standing constraints (the mutation floor, the golden gate, flake posture, alias
re-probes) live in [docs/testing.md](docs/testing.md). Landed work lives in git.

---

## Evaluation harness: retrieval recall

**Found:** 2026-07-19, TODO sweep; carried from the prompt-audit (2026-06-21)
and authority-lane (2026-07-03) entries, both otherwise landed. Narrowed
2026-08-11: the rate runner (`mise run rate`, k >= 5) and the SCR-12 corpus
probes (`tests/e2e/test_consult_shapes.py`) landed; prompt behavior is
measured by the e2e fixture suites plus rate runs. Rate runs now persist
per-run stage traces and `/rate-analyze` delivers the cross-run sniff test
(landed 2026-08-11). Narrowed 2026-08-12: the recall eval landed as a manual
mise task (`mise run recall`): labeled queries in `tests/e2e/queries.py`,
lane-isolated scoring and JSONL receipts in `scripts/recall.py`, delta
protocol in docs/testing.md. Retrieval is measured; weight changes no longer
fly blind.

**What remains.** The short-form-archive edge. The surface-form keyword rule
protects archives that store abbreviations verbatim, and the golden corpus
cannot pin one deterministically: seeding runs the same normalizer that
expands them. Coverage waits for organic corpus growth; revisit the query
labels when a live archive stores short forms.

**Fixture candidates** for growing the prompt suites, each a behavior already
observed and none yet pinned:

- Notes emission on the hedged gRPC composite. Pass rate dropped to ~75-80%
  when Step 0's refusal imperatives landed, caught by one CI red plus rate runs
  on both prompt versions, fixed by coupling err-toward-novel to its note at
  both statement sites.
- Decomposition consistency. One identical hedged consult stored 4 nodes in CI
  and 1 locally; the same composite flapped 2 vs 1 across same-day golden
  rebuilds (2026-08-10), both passing e2e. Candidate interpreter tightening: a
  hedged clause (may, might) is not asserted outright and never becomes its own
  atom.
- True-contradiction recall. The newest worked examples model
  contradiction-free resolutions.
- The composite-collapse rule. Example 9's refusal case could over-generalize.
- Entailment (audit F-2). The old Example 1 shape, a bound corroborated onto a
  point value, as a Step 1 classification case: assert that a strictly weaker
  or stronger proposition contributes with the near-miss noted. The prompt now
  carries the rule; the fixture keeps it.
**Scope boundary.** The Scribe representation rules (no-soften, no-sharpen,
most-recent-wins) execute client-side and are structurally unattestable
in-repo. The harness covers Interpreter and Archivist only; that limit is
accepted rather than unstated.

**Status:** harness landed 2026-08-12; the short-form-archive edge and the
fixture candidates stay open.

---

## Interpreter: emit both surface forms for abbreviation keywords

**Found:** 2026-08-11, k=5 trace analysis of the SCR-12 probes via
`/rate-analyze`. Landed 2026-08-12: step 6 emits both surface forms when
normalization expanded an abbreviation (the pair counts toward the cap of 8,
generic terms drop before either form); Examples 2, 7, and 8 obey the rule;
`test_abbreviation_keywords_carry_both_surface_forms` (ECG, held-out domain)
pins it directly, expected red under the pre-change prompt.

**Measurement** (delta doctrine; measured 2026-08-13, k=5 per rate run):

- Probe, old vs candidate: 0/5 to 5/5. Every old-prompt failure showed the
  predicted mechanism: keywords carried "electrocardiogram", never "ECG".
- Neighbors: no attributable regression. One 4/5 per run on different tests
  (old: colloquial-question, judge rejected "recently" for "lately";
  candidate: paragraph-deixis, judge read a criterion's example list as
  exhaustive); each test's counterpart run was 5/5. Judge noise, not prompt
  effect.
- Recall delta on the frozen archive: identical aggregates both runs.
  `keyword-rich-composite` authority ranks 1-4 in both (a 2-3 swap in
  middle ranks); receipts show the candidate emitting surface-form pairs
  ("remote procedure call" + "RPC") with no eviction of seed-critical
  terms. The identical-both-runs conclusion is what carries forward. The
  aggregates themselves (recall@limit 1.000, MRR 0.827) do not: the review
  batch redefined MRR to the textbook per-query form, dropped zero-score
  pool filler from lane ranks, and added `abbrev-cap-composite`, and the
  re-baseline below reseeded the archive. Numbers across that line are
  incommensurable, not a trend; the 1.000 was structural anyway, since
  every pool holds the whole 10-hypothesis archive.
- After acceptance: `mise run golden-rebuild`, `mise run e2e`, rate the
  shapes suite (prompt change = rebuild trigger; prompt edits have shifted
  neighbors before).
- Re-baselined 2026-08-13, `mise run recall-protocol -- --rebuild
  --old-ref main`: archive reseeded under the candidate prompt (10
  hypotheses, the scen3 collapse repeated), then old vs candidate on that
  frozen copy: 0/17 entries regressed, every cell identical. recall@limit
  1.000 (structural), MRR 1.000 (textbook: each query's best hit at rank
  1). The zero-score filter surfaced its first honest lane miss (the Mars
  seed missed the authority lane on the planetary query that session), and
  `abbrev-cap-composite` resolved 3/3 at ranks 1-3 with surface-form pairs
  in the receipts and no eviction.
- Noise floor priced 2026-08-16, `mise run recall-protocol -- -k 3` on the
  committed archive: 0 unstable cells across three candidate runs (17
  entries, all lanes), recall@limit 1.000 and MRR 1.000 in each. Within a
  session the instrument is stable and k=1 deltas are licensed. Across
  sessions it is not: the Mars seed's authority cell read `-` on 08-13 and
  2 in every 08-16 run, so day-to-day interpreter drift is real. Compare
  receipts only within one protocol session, which is the only comparison
  the driver performs anyway.

**The p99 boundary, closed 2026-08-13.** Step 2 normalizes "p99" to "99th
percentile" while Example 8 omits "p99 latency" from its keywords; the
jargon-vs-abbreviation distinction was taught by omission only. Review found
the rule/example contradiction; step 6 now states it: metric notation is
jargon, not an abbreviation, only the expanded form earns a slot.

**Status:** landed 2026-08-12; measured green 2026-08-13; golden-rebuilt,
re-baselined, and fixture committed 2026-08-13. e2e ran 43/44 on 2026-08-15
with the one failure adjudicated as the `-latest` alias flip (see the
Gemini 3.7 entry); the shapes rate is 5/5 across the file under the 3.6
pin. Remaining: one clean `mise run e2e` under the pin to confirm the
committed fixture.

---

## Gemini 3.7 and the err-toward-novel boundary

**Found:** 2026-08-15. The `-latest` alias flipped to Gemini 3.7 flash
(released 2026-08-13) mid-acceptance: live behavior shifted and the deixis
shapes probe went 0/5 with identical reasoning each run. Pinned both roles
to `gemini-3.6-flash`; the probe returned to 5/5. Contained, not resolved.

**What.** Two coupled questions, doctrine first.

3.7 applies archivist.md's err-toward-novel rule more faithfully than 3.6.
Adjudicated on the deixis case: "Database B is built on PostgreSQL" and
"Database B uses PostgreSQL as its storage engine" are distinct claims
(Aurora: built on PostgreSQL, custom storage engine), so 3.7's
novel-with-note was correct under Step 1 and 3.6's corroboration was lax.
The doctrine was priced under a lax model. A rule-faithful one splits more
evidence: uncertainty stays higher, the herd converges slower, near-misses
accumulate as separate nodes rejoined only by retrieval. Is err-toward-novel
still right when the model stops erring? The asymmetry argument stands (a
false paraphrase writes onto the wrong claim irreversibly; a false novel
splits evidence retrieval can rejoin), but the fragmentation cost is now
real. Options: keep the rule and accept fragmentation; soften the boundary
(same-subject role refinements corroborate); or restate it two-sided,
naming both error costs.

Then 3.7 adoption, blocked on the doctrine call: bump the pin,
`recall-protocol -- --rebuild --old-ref`, e2e, shapes and decomposition
rates, and re-probe the Gemini 3 tuning facts (temperature, thinking,
compound splitting). The deixis test example rides the boundary and passes
only under 3.6; whichever way doctrine lands, re-choose the example (a true
paraphrase of the seed) or respec the assertion to the grounding observable
before the pin moves.

**Status:** open; pin landed 2026-08-15; doctrine decision blocks adoption.

---

## Claude tooling: a guard blocks the canonical keyless spelling in worktrees

**Found:** 2026-08-12, recall-harness build; two worktree agents hit it
independently.

**What.** In worktree agent sessions, a guard hook refused
`env -u GEMINI_API_KEY uv run pytest ... -m e2e`: exactly the keyless
spelling llm-spend.md documents as canonical. The agents' accounts differ on
the mechanism (one: the guard cannot see through the `env` wrapper; the
other: it misread `-m` as a git flag), so the first step is identifying which
layer refuses it, the repo's settings.json guard or the harness's own
worktree protection. Both agents fell back to `GEMINI_API_KEY="" ...`,
equivalent since the conftest skip checks truthiness.

**Why it matters.** A tripwire that blocks the fence's own documented safe
spelling pushes sessions toward ad-hoc workarounds, while the known-dangerous
spellings (quoted multi-word selections) still slip past.

**Options / open questions.** Reproduce in a worktree session and attribute
the refusal; then either teach the guard the `env -u GEMINI_API_KEY` prefix
or bless `GEMINI_API_KEY=""` in llm-spend.md as the worktree-compatible
spelling.

**Status:** open; not started.

---

## Alternative-vendor support: gemini is the only shipped vendor

**Found:** 2026-08-02, live vendor verification with real openai and bedrock
keys.

**What.** Both vendor default files were ported, live-tested, and removed. A
bundled default is a promise (supply a key, get a working system) that neither
vendor had earned, and lexical auto-detect ranked bedrock above gemini, so a
deployment holding both keys silently selected the vendor that cannot complete
a call. A foreign key now configures nothing (fail-fast `ConfigurationError`),
and both vendors stay reachable through explicit `lore.toml` model strings.
Restoring a default file means working the checklist below.

**Why it matters.** Gemini is the only proven vendor, which makes the model
layer single-sourced in practice while the config layer advertises choice.

**Add-back checklist**, per vendor:

- **Instructor mode threading (code).** Per-vendor instructor `mode` from the
  vendor toml through `ModelConfig` into `CompletionProvider`
  (`from_provider(..., mode=...)`). Today the call passes no mode, so every
  vendor inherits TOOLS and its forced `tool_choice`. Nova has no `tool_choice`
  at all and dies on every interpreter and archivist call. Claude on Bedrock
  accepts it, but forced tool use excludes extended thinking, so
  `reasoning_effort` cannot ride TOOLS mode there either: `instructor.Mode.JSON`
  is the enabler for both bedrock targets (`Mode.BEDROCK_JSON` targets the
  native bedrock client, not the litellm route). Open: does Nova emit reliable
  structured output in JSON mode?
- **Cross-vendor e2e harness.** The suite is gemini-coupled: `require_gemini`
  autouse-skips without a gemini key, and the golden bakes gemini embeddings,
  so the bootstrap health check rejects any other embedding model. Verifying a
  vendor needs a way past `require_gemini`, a `./lore.toml` forcing the vendor,
  and a skip for the golden tests. A vendor-parametrized e2e path is the clean
  long-term answer.
- **Batch-order check.** `embed_many`'s positional unpack is verified for
  gemini only; openai and bedrock return indexed entries where order is not
  contractual. A count mismatch fails loud; a silent reorder stores wrong
  vectors permanently. Needs an explicit batch-vs-singles check per vendor.
- **Live rerun** on the candidate config; only a green run restores the vendor
  file. Candidates: openai `gpt-5.6-terra` both roles; bedrock either Claude
  (haiku fast, sonnet reasoning) or `nova-2-lite` both roles, whichever JSON
  mode proves out.

**Files:** `src/lore/providers/completion.py` (instructor mode),
`src/lore/config/vendors/` (removed tomls in git history),
`tests/e2e/conftest.py` (`require_gemini`, golden).

**Status:** open; add-back gated on the checklist.

---

## Observatory: the full build

**Found:** 2026-07-03, design discussion; revised 2026-07-04 after brainstorm.
Spike landed 2026-07-05 via PR #44.

**What.** The spike ships one model-visible entry tool (`observe`), one
app-scoped backend tool (`frontier`), and one Prefab DataTable. The build is
the tiered views behind it.

**Why it matters.** The epistemics are invisible today: the oracle sees one
answer string. The observatory is an attention-allocation instrument, and a
view earns its place by changing what an oracle does next. Direct lever on
adoption, the binding constraint.

**Refactor first.** The epistemic-snapshot loop (attestation map to
`EvidenceInput` to fused scalar to vacuous default to `last_attested`) is
duplicated in `retrieve.enrich` and `observe.frontier`. The tiered views make
it three or more copies that change together; extract the shared concept before
adding the third.

**Views, tiered.**

- **Tier 1, attention.** Decompose the frontier by cause: unexplored (low
  maturity, needs more eyes), stale (decay winning, needs re-attestation or a
  dignified death), contested (ECBF cancellation, needs adjudication). Same
  high uncertainty, three different oracle actions; all derivable today. Plus
  the open-questions queue once `answer_confidence` lands: low-confidence
  answers clustered by question embedding, ranked by frequency and recency, the
  demand-side frontier.
- **Tier 2, legibility.** Hypothesis detail with belief trajectory (every
  attestation row snapshots `c_herd`, so the series is already stored and needs
  no new math). Controversies on conflict metrics PD/CC/DC from
  `lore.math.conflict`, which no read path or renderer currently references;
  the ledger stores no resolution labels, so "contradicts activity" is not
  derivable and the conflict signal is the better definition anyway. Hybrid
  search, the navigation primitive every view links through.
- **Tier 3, ambient.** Novelty feed: recent hypotheses with current rather than
  initial `c_herd`, so newcomers already corroborated or under attack are
  visible.
- **Self-view (the mirror).** Own trust trajectory, recent work with outcomes
  (own stance vs the herd then vs the herd now: the prophet mechanism made
  personally visible), own low-confidence questions. Frame as
  trajectory-with-context including unresolved dissents rather than
  headline-number-first, so bold calls are not chilled before vindication.
  Meaningless under merged `_local` identity; an OIDC-topology feature.
- **Deferred.** FAQ and topical clusters (embedding clustering plus labeling;
  noise at current archive size).
- **Out.** Public oracle-trust views. A leaderboard turns an epistemic
  instrument into a performance metric.

**Also in scope.** `answer_confidence` in [0, 1] emitted by the Archivist
alongside `answer` (one field on the existing instructor schema),
prompt-anchored to mechanical signals it already sees: sparse retrieval, high
fused uncertainty across the retrieved set, neighborhood orthogonal to the
question. Stored as a nullable REAL on `requests`, never the ledger: request
metadata, not an opinion about a hypothesis. NULL means "no question, or the
pipeline never got there"; orphan detection stays the documented join. Answer
text stays unstored. Plus an `[observatory]` config section, the `/observe` MCP
prompt, and architecture.md coverage.

**Constraints that scope the build.** App-scoped tools are the API, no REST
from the iframe; they inherit the authenticated MCP connection. No app tool
takes an `oracle_id`: identity flows from the token, so cross-oracle queries
are unrepresentable rather than forbidden. Queries land as orchestrator read
paths with app tools as thin adapters, same layering as `consult`. Prefab for
v1, presentation only, swappable for hand-authored `ui://` HTML without
touching a tool. The surface is read-only; manual assertions are parked, not
vetoed, because contribution is a byproduct of working and read-only keeps the
security story at one sentence. `observe` returns the Component
unconditionally, which is the only idiom fastmcp supports.

**Open questions / risks.**

- The frontier is bounded by recency (`find_recent`, `FRONTIER_LIMIT = 25`),
  the spike's honest answer to the O(archive) read-time fusion cost. A true
  archive-wide frontier still needs a bound.
- Conversation-bound: no ambient or shareable view. Residual case for a minimal
  server-rendered observatory page; the orchestrator read paths feed either
  surface.
- Two fastmcp gaps are worth upstream issues: the dev harness's browser client
  declares no capabilities at initialize, and reload mode runs stateless HTTP
  that drops initialize params server-side, so `client_supports_extension` is
  unreachable under fastmcp's own tooling. Also worth reporting: Ctrl-C on
  `fastmcp dev apps` dies in a cyclopts asyncio traceback.

**Status:** spike landed; full build not started.

---

## Archivist: a search tool for follow-up retrieval

**Found:** 2026-07-30, programmer request at the e2e-speedup wrap-up.

**What.** The archivist reasons over a fixed neighborhood: the orchestrator
runs two-lane retrieval once, enriches, and hands over the results. A search
tool would let it query the archive mid-reasoning through a bounded
tool-calling loop: probe a suspected paraphrase, chase a contradicted claim's
neighbors, widen a thin neighborhood before declaring novelty.

**Why it matters.** Retrieval bounds paraphrase detection, and a missed
paraphrase becomes a false orthogonal-novel on the append-only ledger: the
failure mode the math cannot digest. Today the archivist cannot ask for more;
the fan-out constant is the only knob.

**Options / open questions.** Expose `search_candidates` plus `enrich` as the
tool (read-only, pre-transaction: the write path is untouched); embed tool
queries via `embed_many`; cap calls per consult, since cost and latency
multiply per consult and the e2e wall clock was just paid down. Instructor
tool-calling vs a hand-rolled loop. Prompt change: golden-rebuild trigger.
IDEA.md's Stage 2 and 3 describe single-shot retrieval, so the interface change
needs explicit approval there. Interacts with the under-grounded-atom filter: a
probe tool may be that filter's mechanism. The reinterpret tool below would
ride the same loop.

**Status:** open; not started.

---

## Archivist: a reinterpret tool for wholesale Interpreter failures

**Found:** 2026-08-03, the wholesale-failure design discussion on the
question-as-anchor extension.

**What.** When Step 0 detects a wholesale Interpreter failure (the composite
itself dropped an envelope-held anchor), the archivist fails the write whole
and the answer asks the oracle to restate. Vote conservation demands the fail:
any partial write would be voted again by the restated consult. But the round
trip spends oracle attention on an infrastructure failure. Wanted: the
archivist triggers a re-run of the Interpreter within the same consult, gets
fresh propositions, and proceeds. The single vote lands once, properly
grounded, and the scribe never hears about it.

**Why it matters.** The restatement is toil the system can absorb. The
Interpreter's grounding miss is stochastic; a second pass with a hint about
what failed is the cheapest fix available. Fail-whole stays the terminal
fallback, so the epistemics lose nothing.

**Options / open questions.** Two shapes. A tool in the archivist's loop,
alongside the search tool: one bounded tool-calling loop hosts both, same
instructor-vs-hand-rolled decision, same IDEA.md Stage 2 and 3 approval. Or an
orchestrator-level retry: the archivist returns a structured reinterpret signal
and the orchestrator loops interpret to reason once, with no tool-calling
machinery and the archivist staying schema-driven. Either way: a hint channel
(the archivist knows which reference failed and which source names it, and a
hinted re-run beats a blind one), a cap (one reinterpretation per consult, then
fail-whole plus the restatement instruction), and the wholesale-failure e2e
probe keeps pinning the fallback. Cost is one extra fast-model call on the
failure path only.

**Status:** open; not started.

---

## Debug UI: provenance viewer as a gated MCP app

**Found:** 2026-08-04, programmer request.

**What.** Two coupled pieces. Provenance grows to capture full model I/O: the
interpreter and archivist calls per consult (inputs and outputs), keyed by
correlation ID alongside the verbatim consult input `requests` already stores.
On top of it, a debug MCP app that renders the full trace per request: consult
input, every model call, resolutions, ledger writes. Off by default; enabled
explicitly via an env var (e.g. `LORE_DEBUG_APP`).

**Why it matters.** Dogfooding debugs blind today: the oracle sees one answer
string, and a wrong resolution (a missed paraphrase, a refused atom) is
invisible without log spelunking. A per-consult trace turns every surprise into
an inspectable artifact. The capture side is already IDEA.md doctrine: storage
is cheap, information is valuable.

**Options / open questions.** The MCP Apps machinery is proven by the
observatory spike: same FastMCPApp idiom, app-scoped backend tools. Separate
app vs a gated view inside the observatory; either way the debug entry tool
registers only when the var is set, so the model-facing surface stays two tools
in normal deployments. Capture vs display gating: capture always (past consults
stay debuggable when the app is enabled later) or only under the var? Storage
shape: one consult makes several model calls, so a sibling table keyed by
correlation ID fits better than columns on `requests`. Where capture hooks: the
provider layer (one seam for both roles) vs the orchestrator. Model I/O embeds
retrieved-neighbor content; fine on-prem, but the security story should say so.
Prior art: the test-lane trace sink (`tests/conftest.py`, `LORE_TRACE_LOG`)
already captures correlation-tagged stage events per consult for rate runs;
production capture wants the same events on a durable store.

**Status:** open; not started.

---

## Audit residual (2026-08-01 panel)

**Found:** 2026-08-01, six-chair panel audit with critic cross-examination.
Verdict: releasable with the six S2 findings fixed; zero S1 findings survived.
The S2 set landed via PR #92. AUDIT.md is gone, so this entry is the only
record of what remains.

Items carry a quoted anchor rather than a line number: the original line
references rotted within days of being written. Grep the anchor.

**Wording fixes.** Each is a word-level substitution.

- **SCI-2 residue.** IDEA.md, "earns nothing: the information factor collapses
  to zero", and the sibling slogan "Rubber-stamping a settled answer earns
  nothing." Both conflate settled (info small, trust ~0.53) with dogmatic (info
  exactly 0). Fix the gloss; add one logic.md clause at the "zero informational
  contribution" site noting the exact limit is unreachable in herds the system
  itself builds, since K >= 1 keeps |c_herd| < 1. IDEA.md half is
  approval-gated; proposal below.
- **CON-2/LIN-9.** archivist.md, "asserting that claim is false, not merely
  old", overstates a graded, attributed disbelief row. If rewording to
  "recording the oracle's disbelief", keep the vividness: it is the deterrent
  behind omit-when-unsure.
- **SCI-1.** IDEA.md Stage 3 ("positive attestation", "disbelief
  attestation"), archivist.md at the CON-2 site, and architecture.md
  ("positive attestation" twice, plus "negative attestation"). The glosses are
  true only for c > 0 and are contradicted by Stage 4's sign rule. Reword
  sign-neutral.
- **CON-4.** docs/logic.md, "push a false opinion", "the false opinion diverges
  from the herd that corrected it", "submitting false opinions when older
  honest attestations have decayed". "False" imports a truth standard the
  algebra cannot observe; use "bad claim" or "insincere attestation", as
  IDEA.md already does.
- **CON-5/LIN-8.** All three LLM-facing prompts open on "shared knowledge
  engine" (contract.md twice, archivist.md, scribe.md) against the canonical
  "shared archive" in IDEA.md and README.md. contract.md contradicts itself
  internally, since it also says "Searches the shared archive". Align on
  archive, or record the split as intentional.
- **SCI-7.** README.md and IDEA.md config tables, "How fast knowledge ages", is
  a world-frame slip; "how fast unrefreshed attestations fade". IDEA.md half is
  approval-gated.
- **SCI-9/ENG-7.** contract.md, "ranked by how little the archive knows about
  each"; "how little the herd has established".
- **LIN-7.** contract.md, "when the oracle asks what to explore", the sole
  "oracle" in the user-register document; "when the user asks".
- **SCR-10.** The negative confidence ladder is coarser than the positive, and
  "I doubt it" at -0.5 overshoots mild skepticism. Add "I'm skeptical" at -0.3.
  Three copies need the same insert: contract.md instructions, contract.md
  field description, scribe.md.
- **LIN-10.** archivist.md, "both cannot be true of the world". Mutual
  exclusivity presupposes coreference of bare definites, never named as a
  precondition. One clause: if the subjects could denote different things, omit.
- **SCR-2.** scribe.md moments list. Correction handling exists only within a
  conversation ("the most recent statement wins"). Add one line assigning the
  correcting consult when an already-contributed position is later reversed.
- **SCR-5.** contract.md `hypothesis` field description. No Scribe-facing
  surface says a compound's single scalar is inherited whole by every atom. One
  interface line: claims held at different confidence go in separate calls.
- **SCR-9.** `observe` appears in the prompts only as a contract.md heading and
  is invisible to the persona; it may also be UI-shaped in text-only clients.
  One clause in scribe.md's moments list, one warning in the description.
- **SCR-11.** README's `[prompts]` table never names the archive's language as
  an operator knob for non-English herds. One sentence. (The only adjacent
  mention is `fulltext_config` in the sqlite section.)
- **CON-1 nit.** archivist.md Example, "Both are true of the world", reads
  truth where the Archivist judges compatibility; "can both be true". The modal
  is already carried earlier by "Claims about different times can both be true".

**Ownership sentences.** Each fix is one sentence owning an instrument choice.

- **ENG-1.** scribe.md carries the ladder's 0.9 cap and its center-pull
  ("Overconfidence corrupts the herd's fusion more than underconfidence"). Both
  are a soft-knee limiter on the input chain, owned in the prompt and unowned
  in IDEA.md's Ingestion section.
- **ENG-4/CON-8.** IDEA.md Stage 3, read path. Frontier surfacing is the
  instrument steering herd attention, the strongest instrument-to-herd feedback
  loop in the system, owned here and not in the spec.
- **CON-6.** IDEA.md, "The mechanical pre-processing step before the
  Archivist", claims a neutrality the same paragraph's lens figure denies. Drop
  "mechanical". Leave the README occurrence, which is operational and justifies
  temperature 0.0. Two further sites the panel did not name carry the same
  word: architecture.md and interpreter.md.
- **SCI-6.** IDEA.md Stage 3, contradicts bullet. A contradicts row records an
  Archivist inference under the oracle's identity; one sentence owns the
  delegation. Skip certainty-discount machinery (YAGNI).
- **SCR-7.** scribe.md and the provenance docs. Provenance's "verbatim" is
  verbatim-of-the-Scribe, one hop from the oracle. Own that, plus the cheap
  norm: quote the oracle's operative words in `reasoning`.
- **CON-7.** docs/logic.md at the ECBF "Contradiction cancels" bullet. `c_herd`
  alone cannot distinguish contested from unexamined; name the oracle count and
  the conflict metrics as the recovery channel. The fix has to bridge naming
  too, since logic.md says `N_O` and never `oracle_count`.
- **ENG-2.** archivist.md's synthesis section. The ledger path has airtight
  input-only guards ("Proposition content comes only from the input"); the
  answer has none. Extend the guard one clause: the answer states only what the
  retrieved set supports.

**Tests and theory.**

- **LOG-3.** The future-timestamp clamp is an undocumented epistemic
  commitment, in tension with the operator-level rejection of negative time.
  One sentence in logic.md's decay boundary cases. Note there are two clamp
  sites, `math/hypothesis.py` and `math/service.py`, not one.
- **LOG-4.** tests/math/test_decay.py's header calls the decay formula
  "Custom formula" where logic.md correctly identifies Def. 14.6 with
  time-varying discount. Align the header.
- **LOG-5.** The loudest degenerate case, (1,0,0) fused with (0,1,0) cancelling
  to vacuous, is never stated as a test. The existing
  `test_two_contradictory_cancel` uses non-dogmatic operands; the dogmatic pair
  reaches `compute_degree_of_conflict` but never `fuse`. One test.
- **LOG-6.** The full-penalty extreme of the informative-commitment table
  (signal 1, align 0) is unattested; punishment asymmetry is half-tested. The
  nearest existing test lands at align ~0.35. One test.
- **LOG-11.** "Surfaces uncertainty clusters" (IDEA.md Stage 3, read path) has
  no test or judge criterion, and no e2e test mentions the frontier at all. One
  e2e case: a settled plus a contested hypothesis, judge asserts the contested
  one is flagged.
- **SCI-8/LOG-10.** The Monte-Carlo archetype bands in logic.md's Trust
  Dynamics Clusters rest on an uncommitted, unrerunnable artifact. Commit the
  simulator as a manual mise task, or annotate the table as a dated one-off.
- **ENG-5.** Archivist `notes` are addressed "to a future oracle" but only the
  count reaches structlog at INFO and the contents at DEBUG. Reword the
  address, or persist notes on provenance. Programmer's call.
- **LOG-7/8/9** (one sentence each). The Opinion constructor's clamp is
  undocumented in logic.md, which documents only the `t_oracle` clamp and
  elsewhere states "No clamping". `maturity_k = inf` serves analytically in a
  promise-bearing math test while config rejects it as non-finite; own the
  analytical device where it is used rather than teaching config to accept inf.
  "Conviction" is overloaded between the decay prose (magnitude eroding) and
  the trust formalism (`|c_oracle_raw|`).
- **Read-after-write.** Rollback is attested, but no test writes through
  consult and reads the same state back through the read path. The nearest is
  `test_write_path_read_then_write`, which is the inverse and fully mocked. One
  integration test; pin time (infinite half-life, or read at the write
  timestamp), else decay makes the comparison legitimately unequal.

**The indexical-present residual** (SCI-4 plus LIN-3, merged by the critic).
One entry for docs/logic.md's Known Residuals section, proposed text:

> **The indexical present.** Two tacit conventions govern reference time:
> self-dated claims are temporal, undated present-tense claims are indexically
> about now. Examples and tests pin both directions; no prose states either.
> The indexical reading is load-bearing (supersession works by contradiction
> because standing claims stay about now), and its cost is real: genuine
> supersession of a standing claim fuses toward zero, and the register reads
> change as controversy. Accepted; no supersession machinery (tried and
> rejected).

**IDEA.md proposals**, approval-gated per CLAUDE.md. This entry proposes; the
programmer disposes.

- **LIN-6** (Interface). "One MCP tool." is stale against the shipped contract,
  which carries an `observe` block IDEA.md never mentions. Proposed: "Two MCP
  tools: `consult`, the epistemic interface, and `observe`, a read-only view of
  the uncertainty frontier. The epistemic write interface is still exactly one
  tool."
- **ENG-1** (Ingestion). Proposed: "The ladder's pull toward center and its 0.9
  cap are deliberate input compression: a soft-knee limiter the instrument
  applies to stated certainty, owned as character rather than claimed as
  neutrality."
- **ENG-4/CON-8** (Stage 3, read path). Proposed: "Surfacing the frontier is
  the instrument steering herd attention; the steering is designed, not
  incidental."
- **CON-6.** Drop the word "mechanical".
- **SCI-6** (Stage 3, contradicts bullet). Proposed: "A contradicts attestation
  records the Archivist's mutual-exclusivity inference under the oracle's
  identity; `correlation_id` and verbatim provenance keep the delegation
  auditable."
- **SCI-2 gloss.** Proposed: "An oracle rubber-stamping settled hypotheses
  earns almost nothing: the information factor shrinks toward zero as the herd
  settles, and the exact zero is the dogmatic limit the system itself never
  builds."
- **SCI-7** (config cell). "How fast unrefreshed attestations fade."

**Open questions**, carried from the Scribe chair, for the programmer:

1. Whose words survive? Provenance stores the Scribe's rendering; the oracle's
   utterance exists nowhere, and Lore cannot audit the difference even in
   principle. Is a verbatim-quote norm in `reasoning` enough, or does
   provenance want an optional utterance column someday?
2. Is "I'm certain" at 0.9 doctrine-mandated softening? The schema accepts 1.0
   and the pipeline digests dogmatic input by design; no doc owns the tension
   with no-soften. What does the Scribe do with "log it at full certainty,
   that's an order"?
3. Should vacuous attestations count as scrutiny? A 0.0 row raises maturity and
   witnesses trust scans while carrying zero evidence: genuine examined
   inconclusiveness, or a hole?
4. Can the Scribe calibrate "multiple"? In a herd of two, "multiple" is one
   colleague, once. Should herd cardinality reach the answer register?
5. Whose confidence is reported confidence? "Berghaus was so sure" admits two
   honest transcriptions at different levels, a world-claim or a
   literature-claim; the level choice changes what the herd later retrieves,
   and no doc decides it.

**Status:** open; none of the families started.
