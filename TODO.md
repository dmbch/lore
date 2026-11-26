# TODO

Work we intend to do. Each entry: what, why, next step. Standing constraints
live in [docs/testing.md](docs/testing.md). Landed work, and how each entry
got here, lives in git.

---

## Persist archivist notes

Dogfooding debugs blind: a wrong resolution is invisible without reading
logs. Archivist `notes` record every classification the model found hard
(near-misses, refused atoms), and today they reach structlog and nothing
else.

**Next.** Store them per consult, keyed by correlation ID, in a sibling table
to `requests` (that row is written before reasoning starts, by design). A SQL
query is the debug view. Once they persist, archivist.md can address them to
a future oracle again instead of the operator.

## Missing tests

Behaviors that are specified and pinned by nothing:

- **Read-after-write.** No test writes through consult and reads the same
  state back through the read path. One integration test; pin time (infinite
  half-life, or read at the write timestamp) so decay cannot make the
  comparison legitimately unequal.
- **The answer register.** It reports settledness, oracle counts, and
  staleness; no judge criterion reads any of it. One fixture: a settled claim
  beside a thinly attested one, judge asserts the answer separates them by
  count and by confidence. Metered.
- **Uncertainty clusters on read** (IDEA.md Stage 3). No test mentions the
  frontier. One case: a settled plus a contested hypothesis, judge asserts
  the contested one is flagged. Metered.

## Observatory: split the frontier by cause

The spike shipped one entry tool (`observe`), one app-scoped backend tool
(`frontier`), and one table. The epistemics are otherwise invisible: the
oracle sees one answer string.

**Next.** Split the frontier into unexplored (low maturity), stale (decay
winning), and contested (ECBF cancellation): same uncertainty, three
different oracle actions, all derivable today. Extract the epistemic-snapshot
loop first; it is duplicated in `retrieve.enrich` and `observe.frontier`.
The frontier is bounded by recency (`FRONTIER_LIMIT = 25`); an archive-wide
one still needs a bound.

**Constraints.** App-scoped tools are the API; no REST from the iframe. No
app tool takes an `oracle_id`: identity flows from the token. The surface is
read-only. No public oracle-trust views: a leaderboard turns an epistemic
instrument into a performance metric.

## Spend guard asks on keyless spellings

`.claude/settings.json` matches `pytest[^|&;]*e2e` and answers "ask", also
for `env -u GEMINI_API_KEY ...` and `GEMINI_API_KEY="" ...`, which cannot
spend. An agent session has nobody to ask, so the safe spellings are refused.
The same pattern misses `mise run recall` and `recall-protocol`, which
llm-spend.md also names.

**Next.** Teach the guard that a command unsetting or emptying the key cannot
reach the API, and add the recall tasks to the pattern.

## Hydrogen: fast-role effort level

The adoption kept `medium` without probing it. Open: whether it is the right
setting, and how Hydrogen handles very large compounds.

## Alternative vendors

Gemini is the only shipped vendor. The openai and bedrock default files were
ported, live-tested, and removed: a bundled default promises a working system
for a key, and neither vendor had earned that. A foreign key now fails fast;
both vendors stay reachable through explicit `lore.toml` model strings.
Parked until someone needs a second vendor.

**Next**, per vendor, before a default file returns:

- **Instructor mode.** Thread a per-vendor `mode` from the vendor toml
  through `ModelConfig` into `CompletionProvider`. Today every vendor
  inherits TOOLS and its forced `tool_choice`: Nova has no `tool_choice` and
  fails every call, and on Bedrock Claude forced tool use excludes extended
  thinking. `instructor.Mode.JSON` is the enabler for both.
- **Cross-vendor e2e.** `require_gemini` skips without a gemini key and the
  golden archive bakes gemini embeddings.
- **Batch order.** `embed_many`'s positional unpack is verified for gemini
  only. A silent reorder stores wrong vectors permanently; each vendor needs
  an explicit batch-versus-singles check.
- **Detection order.** Auto-detect takes the first vendor file by name whose
  key is set, so a restored `bedrock.toml` outranks gemini for a deployment
  holding both keys.
- **A green live run** on the candidate config.
