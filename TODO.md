# TODO

Work we intend to do. Each entry: what, why it matters, options, status.

Standing constraints (the mutation floor, the golden gate, flake posture, alias
re-probes) live in [docs/testing.md](docs/testing.md). Measurement records live
in [docs/measurements.md](docs/measurements.md). Landed work lives in git.

---

## Formalism docs diverge from the shipped algebra

**Found:** 2026-08-16, dead-instrument sweep of the epistemics layer.

**Why it matters.** logic.md is the canonical reference every operator
implementation is checked against. Where it describes behavior the algebra
cannot produce, the next prior-art check verifies against fiction.

**The K = 0 dogmatic hazard.** The per-row trust ceiling derives to
`1 - 1/(2(1+K))` for K >= 1 and `1/2 + 4/27` at K = 0, so `t_oracle = 1.0`
is unreachable at any finite K and the documented hazard describes a state
the pipeline cannot reach; the informative-commitment gate binds first, at
every K. The dogmatic-fusion complex (Case II, `_dogmatic_average`, the
mixed partition) is likewise unreachable from pipeline data at any
configuration, not just at K >= 1 as the doc states. Both paragraphs predate
the gate. Needs a logician pass to confirm the derivation before anything is
rewritten.

The `n_prior` bullet closed 2026-08-18: nothing derived it, so IDEA.md's
derivables list dropped it and both backends' SQL comments now point at
`n_oracle_prior`. The third bullet became its own entry below.

**Status:** open; not started.

---

## `n_oracle_prior` is 0 on a novel that carries a transfer row

**Found:** 2026-08-16, dead-instrument sweep. Split out of the entry above
2026-08-18: the fix is a decision about behavior, not a doc rewrite.

**What.** `orchestrator/record.py` writes the `_transfer` row on a
contradicting novel, then hardcodes `n_oracle_prior=0` for the oracle's own
attestation on that same novel. The existing-hypothesis path counts distinct
oracles via `_count_distinct_oracles`, which would count `_transfer`, and
both `repositories/protocols.py` ("an ordinary includable oracle here") and
`math/service.py` ("the synthetic `_transfer` included") state the convention
the novel path ignores.

**Why it matters.** At K = 1 the dissenting oracle enters at M = 1/2 rather
than 2/3, so a contrarian's attestation on their own novel is discounted
harder than the stated convention implies. The column is write-time and
immutable, so rows already written keep whichever semantics produced them; a
change applies to new rows only.

**Options.** Count `_transfer` (one line, matches the convention both other
sites state) or keep the exclusion and say why at those two sites. Neither
doc decides it today.

**Status:** open; needs a decision, not a derivation.

---

## Evaluation harness: retrieval recall

**Found:** 2026-07-19, TODO sweep; carried from the prompt-audit (2026-06-21)
and authority-lane (2026-07-03) entries, both otherwise landed.

The harness landed 2026-08-12 and has been narrowed three times since; git
holds the sequence. Current state: `mise run recall` scores labeled queries
(`tests/e2e/queries.py`) with lane-isolated ranks and JSONL receipts
(`scripts/recall.py`), the delta protocol lives in docs/testing.md, and the
corpus reached 28 seeds on 2026-08-17 so the archive now exceeds the
retrieval pool and recall can genuinely fail. The numbers pin this archive;
they do not predict the next one.

**What remains.**

- **Even out the crowding.** Only two of eleven queries drew any competition
  in the first contested run: the rest win uncontested, so their ranks would
  not move until a regression is severe. The axis is distractors per cluster,
  not more clusters.
- **The short-form-archive edge.** The surface-form keyword rule protects
  archives that store abbreviations verbatim, and the golden corpus does not
  pin one: seeding runs the same normalizer that expands them. A direct-write
  fixture could pin it, at the cost of content that never passes through the
  normalizer, which is the loop this eval exists to measure. Coverage waits
  for organic corpus growth; revisit the query labels when a live archive
  stores short forms.

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

**Read of the hook source, 2026-08-18** (not a repro). `.claude/settings.json`
matches on `pytest[^|&;]*e2e`, which the `env -u` spelling satisfies, and
returns `permissionDecision: "ask"` rather than a denial. An agent session has
nobody to ask, so "ask" surfaces as refusal. That points at the repo's own
hook, not harness worktree protection, and neither agent's mechanism fits. It
does not close the entry: after the hook's quote-stripping seds, the
`GEMINI_API_KEY=""` fallback matches the same pattern, so the read does not
explain why the fallback worked.

**Options / open questions.** Reproduce in a worktree session and confirm the
attribution; then teach the guard that a command unsetting or emptying the key
cannot reach the API, which is the distinction the regex cannot currently
draw. Failing that, bless `GEMINI_API_KEY=""` in llm-spend.md as the
worktree-compatible spelling.

**Status:** open; attribution narrowed by source reading, repro pending.

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

**Wording fixes: all landed 2026-08-18** (commits `docs: close the audit
residual's wording and ownership fixes` and `feat(prompts): close the audit
residual's prompt-facing wording`). SCI-1, SCI-2, SCI-7, SCI-9/ENG-7, CON-1,
CON-2/LIN-9, CON-4, CON-5/LIN-8, LIN-7, LIN-10, SCR-2, SCR-5, SCR-9, SCR-10,
SCR-11. The prompt half changes live behavior and is unmeasured: it wants a
golden rebuild plus a rate pass on the decomposition and shapes suites.

**Ownership sentences.** Each fix is one sentence owning an instrument choice.
ENG-1, ENG-4/CON-8, CON-6, and SCI-6 landed 2026-08-18 with the wording batch,
their text taken from the proposals this entry drafted. Remaining:

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

**The indexical-present residual** (SCI-4 plus LIN-3, merged by the critic)
landed 2026-08-18 in docs/logic.md's Known Residuals, as drafted.

**IDEA.md proposals** all landed 2026-08-18 with programmer approval: LIN-6
(the Interface section now names both tools), ENG-1, ENG-4/CON-8, CON-6,
SCI-6, the SCI-2 gloss, and SCI-7.

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

**Status:** open. Wording, the IDEA.md proposals, the indexical-present
residual, and four of the seven ownership sentences landed 2026-08-18; the
prompt half of that batch is unmeasured. Remaining: SCR-7, CON-7, ENG-2, the
tests-and-theory family, and the five Scribe-chair questions.
