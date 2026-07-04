# TODO

Technical debt and findings discovered during work. Each entry: what, why it matters, options, status.

---

## Prompt-engineering audit: research current best practice, apply to internal prompts

**Found:** 2026-06-21, programmer request.

**What.** Two phases. First, research current prompt-engineering best practice from
authoritative, current sources (vendor guides: Anthropic, Google/Gemini, OpenAI, not
training-cutoff recall). Then apply the findings to every prompt Lore ships:

- **FastMCP server `instructions`**: the Scribe prompt (`prompts/scribe.md`) alone, loaded
  at `src/lore/adapter/mcp.py:106`. What a connecting Scribe model reads. Domain includes
  (`narrative`/`glossary`) feed the core reasoning prompts, not this.
- **`consult` tool description**: `prompts/consult.md`, loaded at `src/lore/adapter/mcp.py:198`.
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
