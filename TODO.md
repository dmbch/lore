# TODO

Technical debt and findings discovered during work. Each entry: what, why it matters, options, status.

---

## Prompt-engineering audit: research current best practice, apply to internal prompts

**Found:** 2026-06-21, programmer request.

**What.** Two phases. First, research current prompt-engineering best practice from
authoritative, current sources (vendor guides: Anthropic, Google/Gemini, OpenAI, not
training-cutoff recall). Then apply the findings to every prompt Lore ships:

- **FastMCP server `instructions`**: the `_INSTRUCTIONS` constant in
  `src/lore/adapter/mcp.py`. What a connecting Scribe model reads. Domain includes
  (`narrative`/`glossary`) feed the core reasoning prompts, not this. The Scribe persona
  served as the `consult` MCP prompt lives in `prompts/scribe.md`.
- **`consult` tool description**: the `_TOOL_DESCRIPTION` constant in `src/lore/adapter/mcp.py`.
- **`consult` parameter descriptions**: `_PARAM_DESCRIPTIONS` in `src/lore/adapter/mcp.py`
  (question / context / hypothesis / reasoning / confidence).
- **Interpreter system prompt**: `prompts/interpreter.md` (fast model: normalize jargon,
  extract retrieval keywords, decompose composites).
- **Archivist system prompt**: `prompts/archivist.md` (reasoning model: paraphrase /
  contradicts / orthogonal-novel resolution; emits structured resolutions).

**Why it matters.** The prompts are the seam between Lore's epistemics and the LLMs that
drive them. A mis-tuned Interpreter over-decomposes or drops keywords; a mis-tuned
Archivist mislabels a paraphrase as novel, or hallucinates a hypothesis ID, the one
failure mode the math cannot digest (see architecture.md). Prompt quality moves retrieval
and resolution accuracy directly, and these prompts were authored without a deliberate
pass against current guidance.

**Options / open questions.**

- **Vendor neutrality vs. vendor-specific tuning.** Lore is vendor-neutral (Gemini /
  OpenAI / Bedrock defaults), but prompt-engineering advice is partly model-specific.
  Decide: keep prompts robustly cross-vendor, or tune to the default vendor (Gemini) and
  accept per-vendor variants later. The Scribe runs on whatever front-end model the oracle
  uses (Claude in dogfooding); its prompt may want different treatment from the in-Core
  Interpreter/Archivist prompts.
- **Structured-output prompting** for the Archivist is its own discipline (resolutions go
  through `instructor`); the research should cover structured / tool-use prompting, not
  just freeform.
- Whether to add prompt regression coverage (golden input → expected resolution) so a
  prompt edit can't silently degrade behavior.

**Status:** deferred: research plus prompt-content changes; no code-structure change.

---

## MCP-native exploration app (MCP Apps extension)

**Found:** 2026-07-03, design discussion.

**What.** An exploration and management UI served through the MCP Apps extension
(SEP-1865, first official MCP extension; host-side spec finalizes 2026-07-28). One
model-visible entry tool (working name `explore`) returns a UI rendered in the client's
sandboxed iframe; everything behind it is app-only backend tools (`visibility: ["app"]`)
the model never sees and the context window never carries. FastMCP >= 3.2 wires this
natively (`FastMCPApp`, Prefab components); our floor is already 3.4.2.

Candidate views, not all at once:

- **Hybrid hypothesis search**: reuse the two-lane retrieval path as-is.
- **Open questions**: blocked on a data gap; provenance stores the question verbatim but
  not the answer or retrieval outcome, so "unanswered" is not derivable today. Decide
  whether provenance should record the answer (in the spirit of "storage is cheap").
- **New hypotheses feed**: recent orthogonal-novel activity from the ledger.
- **New controversies**: recent `contradicts` activity / conflict metrics.
- **Frequently asked/answered questions**: provenance frequency over question embeddings.
- **Topical clusters**: last or not yet; needs embedding clustering plus labeling, and is
  noise at current archive size.

**Why it matters.** The epistemics are invisible today: the oracle sees one `answer`
string. A UI that shows the herd's belief structure (uncertainty frontier, controversies,
decay) at the moment of use is a direct lever on adoption, the binding constraint named in
PLAN.md.

**Decisions locked (2026-07-03).**

- `consult` stays the only model-facing tool besides the `explore` entry point; the
  one-tool discipline survives.
- No REST from the iframe. App-scoped tools are the API; they inherit the authenticated
  MCP connection. (Iframe CSP defaults to `connect-src 'none'` anyway; a `connectDomains`
  allowlist exists but would mean owning a second auth story.)
- Queries land as orchestrator read paths; app tools are thin adapter wrappers, same
  layering as `consult`. The queries survive any later change of surface.
- Prefab (`prefab-ui`, pinned) for v1. Presentation only, swappable for a hand-authored
  `ui://` HTML template without touching a tool. Same author as FastMCP; marginal vendor
  risk near zero given the existing dependency.
- The surface is read-only. If manual assertions ever land, they route through `consult`
  (Interpreter and Archivist still run; only the Scribe's structuring is bypassed), never
  a direct write.

**Options / open questions.**

- Manual assertions from the UI: viable via `consult`, but decide whether an unstructured
  human hypothesis without a Scribe is wanted at all.
- Renders only in app-capable clients (Claude desktop/web, ChatGPT, VS Code, Goose);
  terminal Claude Code shows the text fallback (`ctx.client_supports_extension()`).
- Conversation-bound: no ambient or shareable view. Residual case for a minimal
  server-rendered observatory page, chiefly the adoption metrics already listed as a
  PLAN.md follow-up.
- Spike before planning: one `explore` tool, one app-only `frontier` tool, one DataTable
  with uncertainty rendering; verifies Prefab's catalog can express the frontier view.

**Status:** deferred; own plan cycle after Group P lands. Spike first.

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

**Status:** spike reviewed 2026-07-05, up as PR #44; rebased onto the fastmcp-flow main
2026-07-13. The rebase aligned the spike with the landed error posture: tool-side scrubs
deleted in favor of fastmcp's native masking, the `DomainInvariantError` wrap extended
to `frontier`, and `last_attested` retyped to a calendar date (`date | None`) matching
`SearchResult`. 2026-07-16: text fallback dropped for the unconditional Component (see
findings); the component branch is verified over a real client session (prefab
structured content on the wire). Live desktop-iframe render pending. Next: full build
per the tiered views.

---

## Authority lane ANDs every keyword token: long keyword lists match nothing

**Found:** 2026-07-03, interpreter prompt pilot.

**What.** `search_candidates` joins all Interpreter keywords into one query string
(`retrieve.py`). SQLite double-quotes each token, and FTS5 treats adjacent quoted tokens
as implicit AND; PostgreSQL's `plainto_tsquery` inserts AND between all lexemes. Every
token of every keyword must therefore co-occur in a single hypothesis for the authority
lane to return it. More or longer keywords make the query stricter, not broader; a
specific 8-keyword list will often match zero rows and the lane silently contributes
nothing while proximity carries the whole search.

**Why it matters.** The rewritten interpreter prompt allows up to 8 keywords, most
specific first (ordering matters because the list head survives `max_keywords`
truncation). Under AND semantics that tuning narrows the lane it feeds. Retrieval recall
bounds paraphrase detection, and nothing measures either today.

**Options / open questions.**

- OR the keywords per keyword (each keyword a quoted phrase, keywords joined by OR),
  keeping tokens within one keyword ANDed. Matches the intuition the prompt now teaches.
- Query per keyword with RRF merge, mirroring the per-source loop that already exists.
- Fold into the retrieval-recall eval (PLAN.md follow-up): measure before tuning.

**Status:** promoted to PLAN.md Group R (2026-07-04): OR the keywords, rank
multi-keyword matches higher. The recall eval stays a follow-up for tuning weights, not
a gate for the semantics fix.

---

## Persist FastMCP OAuth state so container recycles stop forcing re-login

**Found:** 2026-07-07, programmer note. API surface verified against the installed
`fastmcp` 3.4.2 and `py-key-value-aio` 0.4.5, not docs.

**What.** On hosts that recycle (containers, k8s pods), a FastMCP server fronting Google
Workspace OIDC can push oracles back through login. Reading the shipped 3.4.2 source
narrowed the original three suspected causes to one real failure plus a refresh-token
precondition:

- **Real: ephemeral state.** `OIDCProxy(client_storage=None)` defaults to an encrypted
  file store (`FileTreeStore`, Fernet-wrapped), not an in-memory store, under a
  `platformdirs` data directory. It persists, but that directory lives inside the
  container filesystem, so a recycle without a mounted volume wipes it.
- **Precondition: refresh token.** Google returns a `refresh_token` only with
  `access_type=offline` and `prompt=consent`, passed via
  `extra_authorize_params={"access_type": "offline", "prompt": "consent"}` (the parameter
  is `extra_authorize_params`, not `extra_authorization_params`). Without a stored refresh
  token, nothing renews a session wherever state lives.
- **Not a cause on 3.4.2: JWT key rotation.** `jwt_signing_key=None` derives the key from
  the upstream client secret via PBKDF2 with a fixed salt (`jwt_issuer.derive_jwt_key`),
  so it is stable across restarts as long as the client secret is; the default
  storage-encryption key derives the same deterministic way. Pinning `jwt_signing_key`
  explicitly is hygiene (it survives a client-secret rotation), not the described failure.

So the fix is: persist the OAuth state store across recycles, and force offline+consent so
a refresh token exists to persist.

**Why it matters.** The GHCR image is our only delivery, aimed at exactly these recycling
on-prem/private-cloud hosts. A recurring re-login
tax lands on adoption, the binding constraint named in PLAN.md. Auth state at rest also
wants deliberate handling, not an incidental patch.

**Options.**

- **Mounted volume (simplest).** Persist the default file store's `platformdirs` path on a
  mounted volume. No code, no new dependency. Viable wherever the deploy can attach one.
- **DB-backed store.** Supply `client_storage` as an `AsyncKeyValue` implementation:
  - Off-the-shelf `key_value.aio.stores.postgresql.PostgreSQLStore`: least code, but pulls
    `asyncpg` (a second Postgres driver beside our psycopg 3) and opens its own pool. It
    does not reuse ours.
  - Custom, in our repository layer: a `BaseStore` subclass implementing
    `_get`/`_put`/`_delete_managed_entry` over Lore's own connections. It owes both
    backends, psycopg and SQLite, mirroring `repositories/{postgres,sqlite}` and chosen by
    the same `factory.py` selection, so OAuth state persists in whichever backend the
    deployment already runs. `py-key-value-aio` ships a `postgresql` store but no SQLite
    one, so off-the-shelf cannot give this parity. The FastMCP contract is a KV protocol
    (collections, relative TTL, `Mapping[str, Any]` values, bulk variants over
    `ManagedEntry`), so it is a KV adapter at the repo layer, not a reuse of the
    ledger/archive repos.
- Either DB path wraps the store in `FernetEncryptionWrapper` (the same wrapper FastMCP
  applies to its default), keyed by a static secret, so a DB dump does not leak tokens.

**Decision (2026-07-07).** If we go DB-backed rather than a volume, the store lives in the
repository layer as a KV-protocol adapter with both a Postgres and a SQLite backing,
mirroring `repositories/{postgres,sqlite}` and chosen by the same backend selection:
persistence is not epistemic, and a get/put/delete identity/session backend is a clean
abstraction that belongs there. Off-the-shelf `PostgreSQLStore` is rejected: it covers
Postgres only (there is no native SQLite KV store), pulls `asyncpg`, and opens its own pool.

**Open questions.**

- Volume vs. DB store: a mounted volume may be enough and is cheaper. DB-backed wins only
  where the deploy is volume-hostile (stateless-by-policy k8s) or where centralizing state
  in Postgres is preferred anyway.
- Static secret provisioning: where `jwt_signing_key` and the store-encryption secret come
  from (env, secret store), and how rotation stages without stranding live sessions.
- SQLite single-node: the file-based default store on a mounted volume may already suffice
  there, so the custom SQLite backing earns its keep only if colocating OAuth state in the
  app's own SQLite file is worth it.

**Status:** deferred; not urgent until a persistent OIDC deployment is exercised. No code
yet. Test the mounted-volume path before building a store.
