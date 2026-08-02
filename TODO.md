# TODO

Technical debt and findings discovered during work. Each entry: what, why it matters, options, status.

---

## Mutation testing for the math suite

**Found:** 2026-07-25, trust-fix planning.

**What.** Add mutation testing for `lore.math` first, wider later (mutmut is
the boring choice; cosmic-ray the alternative). The suite pins exact values
(archetype trust scores, fusion triples) behind 100% line coverage, but
coverage proves execution, not detection: a flipped sign in `_acbf_pair`, a
dropped factor in the conviction calibration, or a `>=`/`>` swap in a window
guard could survive any assertion looser than it looks.

**Why it matters.** The math module is the product, and the trust-farming
vector was caught by simulation, not by the suite. Mutation score measures
directly whether the tests would catch the next algebra regression; the trust
fix landed security-load-bearing branches (witness rule, conviction
calibration) worth hardening first.

**Options / open questions.** Scope to `src/lore/math` initially; run as a
manual mise task rather than CI-blocking until baseline score and runtime are
known; pick a threshold after the baseline. Whether the trust-scan SQL
(window guards, exclusions) can be covered via the repository tests or needs
its own target set.

**Baseline (2026-08-02, isolated worktree run).** mutmut 3.7 on `src/lore/math`:
461 mutants, 434 killed, 27 survived = 94%. Core algebra airtight (every
`_acbf_pair` sign flip and conviction-calibration mutant killed); survivors are
equivalent mutants (defensive clamps, unasserted `ValueError` messages) plus 3
real boundary/wiring gaps (`prepare_attestation` accepting `t_oracle == 0`,
`build_math`'s `maturity_k` passthrough, a zero-age trust row). The config
(`[tool.mutmut]`, a `mutants/`-scoped Hypothesis harness, a `mise run mutation`
task) is preserved on branch `chore/mutmut-baseline` (off main), not folded into
the main line: it reproduces the baseline in an isolated worktree
(~73 mutants/s) yet times out every mutant on the heavier dev checkout
(~0.47/s), because mutmut runs the full ~2s property suite per mutant and hits
its auto-timeout (no CLI override). Likely fix: cap Hypothesis `max_examples` in
the `mutants/`-scoped profile so each run stays under the timeout; confirm the
capped run still reproduces the survivor set before folding the mise task in.

**Status:** open; baseline established 2026-08-02, mise-task fold-in deferred
(runner times out on the dev checkout).

---

## E2E: residual risks after the speedup

**Found:** 2026-07-20, deploy-flow triage; the release e2e job exceeded 30
minutes and was removed from `release.yml` outright (2026-07-21). Rewritten
2026-07-29: the speedup landed and the gate is restored.

**What.** The 30-minute runs were a deadlock, not latency. The composition
root's cache sweep suspended mid-DELETE holding the pool lock on the session
loop while each test ran on its own function loop; the first consult of any
run hung until the job timeout. Fixed on `build/e2e-loop-topology` (one
session loop for fixtures and tests, five-minute per-test caps). Honest
baseline after the fix: 34 tests, 379s sequential, ~100 live LLM calls.

`build/e2e-speedup` then cut wall clock and call count:

- **xdist.** `mise run e2e` runs `-n auto --dist loadgroup`; the ordered
  knowledge arc is pinned to one worker via `xdist_group`. Session fixtures
  are per-worker, each with its own archive: less cross-suite retrieval
  pollution than the old shared file.
- **Fast-role judging.** Every `judge()` call runs on the fast model; the
  `grader` knob is gone. Accepted cost: interpreter suites are judged by the
  model under test's own weights.
- **Golden archive.** `tests/e2e/fixtures/golden.db.gz` (150KB gzipped, 1MiB
  budget enforced) replaces the 11 live seed consults in the aggregation
  suite. Per-worker copies re-base attestation timestamps to now; the
  bootstrap dimension check fails a stale fixture loud. `mise run
  golden-rebuild` rebuilds through the real pipeline; triggers are corpus,
  prompt, model, or epistemics/trust-math changes (the fixture bakes
  write-time ledger math). The knowledge arc keeps live seeding (the write
  path under test) and the decay test keeps its one live seed (different
  epistemics settings).
- **Synthetic decay clock.** The decay test constructs `t_now` values
  instead of sleeping through half-lives.
- **Batched embeddings.** One request per task-type group via
  `Embedder.embed_many` (provider, orchestrator). Latency was already
  overlapped by gather; the win is RPM headroom under xdist. Review
  fallout: batching left the request-scoped embedding cache dead code
  (Gemini's task types never share a key; same-type duplicates collapse
  into one batch), so the cache is gone and `Embedder` is just
  `embed_many`.

Measured: gate 1 (xdist + judging + synthetic clock) 34/34 in 79.4s on 10
workers, no 429s. Gate 2 (golden + batching) 33/34 in 88.4s, aggregation
without a single live seed consult; the one failure was the deixis probe, a
known stochastic class, green on isolated retry. Wall clock against
baseline: 379s to 88.4s. The gate is back in `release.yml`: the canonical
mise invocation, a 15-minute job timeout as the deadlock backstop, tag
needs e2e and smoke.

**Why it matters.** The suite is the only end-to-end check on the release
path. What remains is risk at its edges: unverified vendor configs, model
behavior that shifts under alias flips, one stochastic probe that can block
a tag.

**Findings / open items.**

- Release-path flake exposure: the deixis probe is a known stochastic class
  (one failure in gate 2, green on retry); a flake blocks the tag until a
  workflow re-run. A `pytest-rerunfailures` annotation was tried and dropped
  (2026-08-02): it broke mutmut's runner and masks a genuinely degrading
  probe. Accepted: re-run the release workflow on the rare flake.
- The archivist has no instruction for under-grounded atoms (prompt read,
  2026-07-30). The interpreter passes unresolved references through by
  design ("no guess, no caveat, no flag"), and err-toward-novel then stores
  the vague atom as a fresh hypothesis on the append-only ledger. Wanted:
  refuse defective atoms (an atom vaguer or broader than its composite,
  whose anchor the envelope holds) and record them in `notes`; an honestly
  vague composite still stores as-is, mirroring the flash compound-split
  posture (resolution loss acceptable, corruption not). Prompt change:
  golden-rebuild trigger. Lands before the retry annotation.
- Vendor configs for openai and bedrock: ported (`gpt-5.6-terra` and the
  `us.`-profile `nova-2-lite`, both roles) and live-tested 2026-08-02.
  Bedrock fails outright (Nova rejects `tool_choice`); openai still
  unverified. Spun out to its own entry below.
- Pin gemini models instead of riding `-latest`? The 2026-07-21 alias flip
  cost a detour; openai and bedrock have no rolling aliases, so the port
  puts them on deliberate version bumps. Pinning gemini aligns all three
  vendors on one bump protocol; riding keeps dogfooding on the vendor
  frontier, which is itself eval signal. Undecided.
- After any `-latest` alias flip, run e2e deliberately and re-probe tuning:
  the 2026-07-21 flip to Gemini 3 made the inherited fast-role
  `temperature = 0.0` pin toxic (interpreter grounding failures); vendor
  files own temperature now.
- flash declines to split very large compound hypotheses: resolution loss,
  not corruption. Eval-harness material (see that entry).
- An archivist filter for accidentally generalized (under-grounded) atoms is
  a candidate improvement; landing it is a golden-rebuild trigger.
- Assessed and rejected: a default narrative for the archivist and
  interpreter prompts; both already carry consequence-level collective
  context.
- Minor: `plan` could run parallel to `gate` in `release.yml`, starting the
  live jobs a few minutes earlier. `release` must then name `gate` in its
  needs list, or the tag loses the unit gate.

**Status:** landed on main via PR #76 (2026-07-30); residual items open.

---

## Alternative-vendor support: openai and bedrock unverified live

**Found:** 2026-08-02, G2 live vendor verification (real openai/bedrock keys).

**What.** The vendor port landed on the build branch (`gpt-5.6-terra` both roles
for openai; `us.amazon.nova-2-lite-v1:0` both roles for bedrock; medium/high
effort; embeddings unchanged). Live verification then ran and split into three
findings:

- **Bedrock fails outright.** Nova rejects `tool_choice`
  (`litellm.UnsupportedParamsError`). `CompletionProvider` builds its client
  with `instructor.from_provider("litellm/<model>", async_client=True)`, which
  defaults to TOOLS mode and forces the schema via `tool_choice`; Nova has no
  such param, so every interpreter and archivist call dies (27/27 completion
  tests). `reasoning_effort` was never reached. Pre-existing: the prior Nova
  config used the same mode, so bedrock was never live-functional; G2 only
  surfaced it.
- **Bedrock can't use the golden fixture.** The aggregation suite loads
  `golden.db.gz`, baked with gemini embeddings; the bootstrap health check
  rejects Titan as an embedding-model mismatch (8 errors). Any non-gemini e2e
  must skip the golden tests.
- **OpenAI: untested.** Expected to pass (openai supports `tool_choice`), but
  the `gpt-5.6-terra` model ID, `reasoning_effort` acceptance, and `embed_many`
  batch ordering are all unconfirmed.

**Why it matters.** The vendor tomls ship as auto-detect defaults. A deployer
supplying only bedrock keys gets a config that cannot complete a single call,
silent until first use. Gemini is the only proven path.

**Options / open questions.**

- **Bedrock fix (code, not config).** Thread a per-vendor instructor `mode`
  from the vendor toml through `ModelConfig` into `CompletionProvider`
  (`from_provider(..., mode=...)`); set bedrock to `instructor.Mode.JSON`
  (JSON-schema prompting, no `tool_choice`). Open: does Nova emit reliable
  structured output in JSON mode? `Mode.BEDROCK_JSON` exists but targets the
  native bedrock client, not the litellm route; `Mode.JSON` is the general fit.
- **Cross-vendor e2e harness.** The suite is gemini-coupled: `require_gemini`
  autouse-skips without a gemini key, auto-detect ranks Bedrock > Gemini >
  OpenAI, and the golden bakes gemini embeddings. Verifying another vendor needs
  a way past `require_gemini` (real/dummy gemini key, or parametrize), a way to
  force the vendor (openai needs a `./lore.toml` override to outrank a present
  gemini key), and `-k "not aggregation"` to skip the golden. A
  vendor-parametrized e2e path is the clean long-term answer.
- **OpenAI verification (should pass).** Temporary `./lore.toml` forcing the
  openai models + `OPENAI_API_KEY` + a non-empty `GEMINI_API_KEY` (unblocks
  `require_gemini`; gemini is never called); `mise run e2e` with
  `-k "not aggregation"`. Confirm the model ID, `reasoning_effort`, and batch
  order.
- **Batch-order check.** `embed_many`'s positional unpack
  (`[d.embedding for d in response.data]`) is verified for gemini only;
  openai/bedrock return indexed entries where order is not contractual. A count
  mismatch fails loud; a silent reorder stores wrong vectors permanently. Needs
  an explicit batch-vs-singles check per vendor.
- **Priority call.** If bedrock is not a deployment target, it can stay a
  documented non-functional default or be dropped from auto-detect. The port
  commit is not reverted: the prior config was equally broken, and these are the
  intended models once the JSON-mode fix lands.

**Files:** `src/lore/providers/completion.py` (instructor mode),
`src/lore/config/vendors/{openai,bedrock}.toml`, `tests/e2e/conftest.py`
(`require_gemini`, golden).

**Status:** open; deferred 2026-08-02. openai config committed, unverified;
bedrock config committed, non-functional pending the instructor JSON-mode
change; gemini the only proven vendor.

---

## Archivist: a search tool for follow-up retrieval

**Found:** 2026-07-30, programmer request at the e2e-speedup wrap-up.

**What.** The archivist reasons over a fixed neighborhood: the orchestrator
runs two-lane retrieval once, enriches, and hands over the results. A search
tool would let it query the archive mid-reasoning through a bounded
tool-calling loop: probe a suspected paraphrase, chase a contradicted
claim's neighbors, widen a thin neighborhood before declaring novelty.

**Why it matters.** Retrieval bounds paraphrase detection, and a missed
paraphrase becomes a false orthogonal-novel on the append-only ledger: the
failure mode the math cannot digest. Today the archivist cannot ask for
more; the fan-out constant is the only knob.

**Options / open questions.** Expose `search_candidates` + `enrich` as the
tool (read-only, pre-transaction: the write path is untouched); embed tool
queries via `embed_many`; cap calls per consult (cost and latency multiply
per consult, and the e2e wall clock just got paid down). Instructor
tool-calling vs. a hand-rolled loop. Prompt change: golden-rebuild trigger.
IDEA.md's Stage 2/3 describe single-shot retrieval, so the interface change
needs explicit approval there. Interacts with the under-grounded-atom
filter (e2e entry): a probe tool may be that filter's mechanism.

**Status:** open; not started.

---

## Evaluation harness: retrieval recall and prompt regression

**Found:** 2026-07-19, TODO sweep; carried from the prompt-audit (2026-06-21) and
authority-lane (2026-07-03) entries, both otherwise landed.

**What.** Two measurements the pipeline lacks. A retrieval-recall eval: a fixture corpus
and query set scoring the two-lane search, so lane weights and `max_keywords` are tuned
against numbers. Prompt regression coverage: golden input → expected resolution cases
pinning Interpreter and Archivist behavior, so a prompt edit cannot silently degrade
decomposition or paraphrase detection.

**Why it matters.** Retrieval recall bounds paraphrase detection, and a mislabeled or
hallucinated resolution is the one failure mode the math cannot digest. Both surfaces
were tuned by review and live pilots; nothing measures either. The next prompt or weight
change flies blind.

**Options / open questions.** The two evals likely share a fixture corpus; a
first corpus is seeded at `tests/e2e/corpus.py` (the golden-archive seeds). The
aged-attestation e2e probe (`800286c`) seeds the prompt side. Decide whether evals run in
CI (live LLM calls: cost and flake) or as a manual mise task.

**Status:** open; not started.

---

## litellm security bumps blocked by the instructor + pydantic pin chain

**Found:** 2026-07-03, dependency security triage.

**What.** Seven open Dependabot advisories target litellm (2 critical, 5 high), all in
the litellm proxy server: auth bypass, SQL injection, SSTI, MCP stdio RCE, guardrail
sandbox escape, API-key and role endpoints. Lore uses litellm as an SDK client only
(`litellm.aembedding`, instructor-wrapped completions, `get_model_info`, the `otel`
callback) and never starts the proxy, so none are reachable. They also cannot be cleared
by upgrading: `instructor==1.15.4` (latest) caps `litellm<=1.83.7`, and `litellm==1.83.7`
hard-pins `pydantic==2.12.5`, which conflicts with our `pydantic>=2.13.4`. Every fix
version (1.83.7 / 1.83.10 / 1.83.14 / 1.84.0) is out of reach without downgrading pydantic
or dropping instructor.

**Why it matters.** The security dashboard shows standing criticals that are not
exploitable here. That trains the reflex to ignore it and can mask a future reachable
alert. The reflex remedy, bumping litellm, silently fails to resolve or forces a pydantic
downgrade.

**Options / open questions.**

- Watch instructor releases. The moment one lifts the `litellm<=1.83.7` cap (and pulls a
  litellm that drops the `pydantic==2.12.5` pin), the whole litellm stack jumps to current
  in one grouped PR under the new dependabot config.
- The seven alerts are dismissed on GitHub as `not_used`. Reopen and re-triage once the
  upgrade path opens.
- Reachability holds only while Lore stays an SDK consumer. Adopting the litellm proxy
  re-exposes all seven.

**Status:** resolved 2026-07-31 on `build/dependabot-uv-sync`. The `[litellm]`
extra is dropped: litellm is a direct dependency, and the extra only contributed
instructor's cap. litellm sits at 1.94.0 with the whole stack bumped, and the
filterwarnings entry is gone with it. macOS builds litellm's sdist with a
mise-provisioned Rust toolchain until upstream ships macOS wheels
(BerriAI/litellm#31261). The seven dismissed alerts clear once main carries the
new lock; instructor's litellm compatibility now rests on the e2e gate (34/34
against 1.94.0) instead of instructor's own cap.

---

## Observatory: MCP-native exploration app (MCP Apps)

**Found:** 2026-07-03, design discussion; revised 2026-07-04 after brainstorm.

**What.** A read-only exploration UI (the observatory) served through the MCP Apps
extension (SEP-1865; host-side spec finalizes 2026-07-28). One model-visible entry tool,
`observe`, returns UI rendered in the client's sandboxed iframe; everything behind it is
app-scoped backend tools the model never sees and the context window never carries.
`/observe` ships as an MCP prompt (client slash command) that nudges the model to call
the entry tool. `consult` and `observe` are the entire model-facing surface.

FastMCP 3.4.2 (the current floor) ships the machinery: `fastmcp.apps.FastMCPApp`,
`AppConfig`, `ResourceCSP`, prefab synthesis, `fastmcp apps dev`. Idiom (verified by the
spike, 2026-07-04): build a `FastMCPApp(name)` provider, register the entry tool with
`@app.ui()` and backend tools with `@app.tool()`, then `server.add_provider(app)`. Not
`server.tool(app=...)`. `@app.tool()` defaults to `visibility=["app"]` (the model never
sees it); `@app.ui()` to `visibility=["model"]` and auto-wires the prefab renderer
resource. `visibility` is the `AppConfig` spelling under the decorator, chosen via the
`model=` flag, not a `server.tool` kwarg. The decorators return the wrapped function
unchanged, so tool bodies can live at module scope and be unit-tested directly.

**Why it matters.** The epistemics are invisible today: the oracle sees one answer
string. The observatory is an attention-allocation instrument; a view earns its place by
changing what an oracle does next. Direct lever on adoption, the binding constraint.

**Decisions locked.**

- `consult` and `observe` are the only model-facing tools. The one-tool discipline
  survives as a two-tool discipline.
- No REST from the iframe. App-scoped tools are the API; they inherit the authenticated
  MCP connection. No app tool ever takes an `oracle_id` parameter: identity flows from
  the token, so cross-oracle queries are unrepresentable rather than forbidden.
- Queries land as orchestrator read paths; app tools are thin adapters, same layering as
  `consult`. The read paths are the real work (new read use cases, repo Protocol
  extensions on both backends under the drift guard) and survive any later change of
  surface; only search touches an LLM provider (query embedding).
- Prefab (prefab-ui, pinned) for v1. Presentation only, swappable for hand-authored
  `ui://` HTML without touching a tool.
- The surface is read-only. Manual assertions parked, not vetoed: a validated form could
  capture reasoning and confidence adequately, but contribution is a byproduct of
  working, never a separate task, and read-only keeps the app's security story at one
  sentence.
- Answer confidence, not answer text, lands in provenance. The Archivist emits
  `answer_confidence` in [0, 1] alongside `answer` (one field on the existing instructor
  schema), prompt-anchored to mechanical signals it already sees: sparse retrieval, high
  fused uncertainty across the retrieved set, neighborhood orthogonal to the question.
  Stored as a nullable REAL on `requests`, never the ledger: request metadata, not an
  opinion about a hypothesis. NULL means "no question, or the pipeline never got there";
  orphan detection stays the documented join. Answer text stays unstored (display
  material, not queryable; revisit if a FAQ view needs payload).
- Public oracle-trust views are out. A trust leaderboard turns an epistemic instrument
  into a performance metric. Self-view is in (see views below).

**Views, tiered.**

- Tier 1, attention: the frontier decomposed by cause: unexplored (low maturity, needs
  more eyes), stale (decay winning, needs re-attestation or a dignified death),
  contested (ECBF cancellation, needs adjudication). Same high u, three different oracle
  actions. All derivable today. Plus the open-questions queue once `answer_confidence`
  lands: low-confidence answers clustered by question embedding, ranked by frequency and
  recency: demand-side frontier.
- Tier 2, legibility: hypothesis detail with belief trajectory (every attestation row
  snapshots `c_herd`; the time series is already stored, zero new math); controversies
  on conflict metrics PD/CC/DC from `lore.math.conflict` (the ledger stores no
  resolution labels, so "contradicts activity" is not derivable; the conflict signal is
  the better definition anyway); hybrid search (two-lane retrieval as-is, the navigation
  primitive every view links through).
- Tier 3, ambient: novelty feed: recent hypotheses with current (not initial) `c_herd`,
  so newcomers already corroborated or under attack are visible.
- Self-view (the mirror): own trust trajectory (`t_oracle` series from own rows, current
  score via the existing trust scan), recent work with outcomes (own stance vs. herd
  then vs. herd now: the prophet mechanism made personally visible), own low-confidence
  questions. Frame as trajectory-with-context including unresolved dissents, not
  headline-number-first, to avoid chilling bold calls before vindication. Meaningless
  under merged `_local` identity; an OIDC-topology feature.
- Deferred: FAQ and topical clusters (embedding clustering plus labeling; noise at
  current archive size).

**Open questions / risks.**

- The frontier query is O(archive) read-time fusions. Trivial at dogfooding size; bound
  it (recent-activity window or LIMIT) before it matters.
- Client split (decided 2026-07-16): Claude Desktop is the primary observatory client;
  Claude Code consults but is not an observatory target. `observe` returns the Component
  unconditionally (the idiomatic `@app.ui` shape); text-only clients see fastmcp's
  `[Rendered Prefab UI]` placeholder, accepted.
- Host-side spec is pre-final until 2026-07-28; FastMCP absorbs the churn, Prefab is
  pinned. Marginal risk.
- Conversation-bound: no ambient or shareable view. Residual case for a minimal
  server-rendered observatory page (adoption metrics, a PLAN.md follow-up); the
  orchestrator read paths feed either surface.

**Spike (landed 2026-07-04, branch `build/observatory-spike`).** One `observe` entry
tool, one app-scoped `frontier` backend tool, one Prefab DataTable with uncertainty
rendering. Findings:

- Prefab expresses the frontier: `prefab_ui.components.DataTable(columns=[DataTableColumn(
  key, header, sortable, format, align)], rows=[dict], search, paginated)`; `DataTable` is
  a `Component`. Cells are plain dicts keyed by column `key`; per-column `format`
  (`number:2`, `date`, ...) drives display. An `@app.ui` returning `Component`
  builds with no output-schema trouble.
- Idiom corrected (see above): `FastMCPApp` decorators plus `add_provider`, not
  `server.tool(app=...)`.
- Text fallback reversed (2026-07-16, live `fastmcp dev apps` acceptance). The spike
  gated on `ctx.client_supports_extension(UI_EXTENSION_ID)` with a plain-text table for
  non-UI clients, per the `client_supports_extension` docstring. Two fastmcp findings
  killed it: the dev harness's browser client declares no capabilities at initialize, so
  the gate is unreachable under fastmcp's own tooling (iframe waits forever), and reload
  mode (`--reload`, the default) runs stateless HTTP, which drops initialize params
  server-side, false-ing the gate for every client. None of fastmcp's shipped app
  providers gate; the supported idiom is Component, always. Both fastmcp gaps are worth
  an upstream issue (also: Ctrl-C on `fastmcp dev apps` dies in a cyclopts asyncio
  traceback).
- Frontier bounded by recency (`find_recent` + `FRONTIER_LIMIT=25`), the honest spike
  answer to the O(archive) open question above. A full-archive frontier still to revisit.
- Uncertainty surfaced as `1 - |c_herd|` via `MathService.compute_uncertainty` (projection
  composition, no new formula); `FrontierEntry` is the frozen row type.
- The epistemic-snapshot loop (attestation map → `EvidenceInput` → fused scalar →
  vacuous default → `last_attested`) now has two occurrences: `retrieve.enrich` and
  `observe.frontier`. The tiered views make it three-plus that change together: extract
  the shared concept during the full build.

Non-goals, deferred to the full build: the other tiered views, the `answer_confidence`
provenance column, self-view, the `/observe` MCP prompt, an `[observatory]` config
section, architecture.md updates.

**Status:** spike reviewed 2026-07-05, landed on main via PR #44; rebased onto the
fastmcp-flow main 2026-07-13. The rebase aligned the spike with the landed error posture: tool-side scrubs
deleted in favor of fastmcp's native masking, the `DomainInvariantError` wrap extended
to `frontier`, and `last_attested` retyped to a calendar date (`date | None`) matching
`SearchResult`. 2026-07-16: text fallback dropped for the unconditional Component (see
findings); the component branch is verified over a real client session (prefab
structured content on the wire). Live desktop-iframe render verified. Next: full build
per the tiered views.
