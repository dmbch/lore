# TODO

Technical debt and findings discovered during work. Each entry: what, why it matters, options, status.

---

## E2E wall clock: parallelize, cheapen judging, pre-seed the archive

**Found:** 2026-07-20, deploy-flow triage; the release e2e job exceeds 30 minutes.

**What.** The e2e job (`release.yml`) runs 34 tests strictly sequentially: roughly
100 live Gemini round-trips, the archivist at `reasoning_effort = "high"` (15-40s
per call). Pure latency stacking. Three levers:

1. **pytest-xdist.** `-n 8 --dist loadgroup`; an `xdist_group` marker pins the
   ordered knowledge arc to one worker. Session fixtures become per-worker, each
   with its own SQLite file: strictly less cross-test retrieval pollution than
   today's shared archive. Add `--durations=20` so CI reports where time goes.
   Expected: wall clock collapses toward the longest chain, ~5 minutes.
2. **Fast model for all judging.** Judging is binary semantic checking, well below
   the graded task's difficulty, and the fast role already pins `temperature = 0.0`.
   The `grader` parameter and the decorrelation comment in `tests/e2e/conftest.py`
   dissolve. Accepted cost: interpreter suites are judged by the model under test's
   own weights.
3. **Pre-seeded archive.** A golden SQLite fixture replaces live `_seed()` consults
   in the aggregation and decay suites: the priciest and flakiest arrange step, since
   a seed misresolving against another seed poisons its probe. A mise task rebuilds
   the fixture through the real pipeline (real embeddings, real ledger math), run
   manually when seeds, prompts, or models change. Swap-in copies the file per
   worker and re-bases attestation timestamps to the session clock so fixture age
   never leaks into decay math; the bootstrap embedding-model health check makes a
   stale fixture fail loud. The knowledge arc keeps live seeding: the arc is the
   write path under test.

**Why it matters.** The e2e job sits on the release critical path; every merge to
main pays it in wall clock, tokens, and flake exposure. Levers 2 and 3 also cut
cost and flakiness independently of speed.

**Options / open questions.**

- Golden fixture storage: committed binary vs CI cache keyed on seed script, prompts,
  and model ids. Embeddings are not byte-reproducible, so rebuilds churn a committed
  blob; a cache miss in CI needs a key with live-LLM access. Lean committed, decide
  at build time.
- Seed identity: recover hypothesis ids by correlation_id at session start (as
  `_seed` does today, minus the LLM calls) or have the rebuild task emit a
  manifest. Lean correlation_id: no second artifact to drift.
- xdist parallelism assumes a paid-tier Gemini key; free-tier RPM would throttle
  workers back to sequential. Verify before sizing `-n`.
- The golden archive and the evaluation-harness fixture corpus (see that entry)
  likely converge: one corpus, two consumers. Build lever 3 with that in mind.
- Minor: `plan` could run parallel to `gate` in `release.yml`, starting e2e a few
  minutes earlier.

**Status:** open; not started. Interim (2026-07-21): the e2e job is removed from
`release.yml` entirely; it was blocking releases outright. Restoring it to the
release gate, parallelized, is part of this fix.

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

**Options / open questions.** The two evals likely share a fixture corpus. The
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

**Status:** blocked upstream (instructor plus litellm/pydantic pins); dismissed as
non-reachable; revisit on the next instructor release. A scoped pytest filterwarnings
entry (pyproject) silences litellm's teardown RuntimeWarning (`Logging.async_success_handler`
never awaited) in e2e runs; drop the filter together with this entry when the cap lifts.
Re-checked 2026-07-19: instructor's latest release is still 1.15.4; nothing has moved.

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
