# TODO

Technical debt and findings discovered during work. Each entry: what, why it matters, options, status.

---

## Deployment docs (platform shape, secrets, IdP, OTel collector)

**Found:** 2026-06-07, while planning the logging refactor.

**What.** Document a dogfooding deployment end-to-end: container/platform shape (persistent volume for SQLite, env mapping for `DATABASE_URL`/`OIDC_URL`/`BASE_URL`/`FASTMCP_TRANSPORT`/`OTEL_*`), the platform's secrets workflow, an OIDC IdP example including workspace-restriction passthrough via `extra_authorize_params`, and an OTLP collector setup (endpoint, tenancy headers, sampling).

**Why it matters.** Walking a new operator through a reference deployment shouldn't require reading the source.

**Status:** deferred — docs-only, no code change.

---

## Documentation editing pass — strip AI tropes, tighten docstrings

**Found:** 2026-06-21, programmer request.

**What.** A systematic editing pass over every prose document and docstring, one editing
agent per area working independently:

- `README.md` — net-additive here; see README requirements below
- `docs/architecture.md`
- `docs/logic.md` — **logician agent required** (math-correctness gate, per
  `.claude/rules/math.md`); preserve canonical notation, prose only
- `IDEA.md` — edits gated on explicit programmer approval (canonical spec)
- production docstrings (`src/lore/**`)
- test docstrings (`tests/**`)
- `CLAUDE.md` and `.claude/rules/*.md` if in scope

**Editorial standard.** Two lenses — don't point the docstring axe at the prose docs:

- *Docstrings* earn their place only by explaining a non-obvious *why*. Restating the
  function name, signature, or types is noise — delete it (already the house rule). Keep
  module docstrings that name a module's role in its layer.
- *Prose docs* (README, IDEA, architecture, logic) exist partly to explain rationale and
  the mental model — that is the job, not a trope. Trim AI tropes, self-congratulation,
  and flowery justification for standard practice. **Assume reader competence with the
  tooling** (Docker, k8s, Postgres): explain *Lore's* behavior and footguns — "no network
  call at boot", the health check refusing on embedding-model mismatch, `FASTMCP_HOST`
  defaulting to loopback — not what `-i`/`-v`/`-p` do or the `-v`-vs-`--mount` directory
  quirk. A good-clever detail is worth a line unless self-explaining.
- **Two-directional, not a deletion sweep**: cut tropes/redundancy *and* fill genuine
  gaps. The goal is better documentation, not less.
- Trope-vs-voice is a judgment call. When unsure, the editing agent leaves it and the
  proofreading agent surfaces it — no unilateral flattening of authored voice.

**README requirements** (reported pain: people don't immediately grok how to run Lore):

- **What Lore is** — the centaur/herd metaphor stays; the rest of the framing is negotiable.
- **How to run it** — today's examples are per-piece `docker run` snippets padded with
  standard-Docker explanation; trim the tooling-101 and add one *coherent* worked
  deployment. Target audience: PostgreSQL in a k8s-ish environment using a single-env-var
  vendor shortcut (e.g. `GEMINI_API_KEY`) — they want the declarative artifact, not a wall
  of `-e` flags.
- **Reference deployment** — a sanitized `fly.toml` + redacted env vars (fly.io as the
  worked example). None is in the repo, so this needs the programmer's real one as input.
  Redaction bar (public-repo rule, `feedback_no_private_details`): app name, org,
  hostnames, secret values → placeholders.
- **Basic config example** — a minimal, droppable `lore.toml`, not only the field tables.
- **env/config reference** — already strong; keep it, sync to the renamed keys.

**Process.** Per area: editing agent does the pass, then one proofreading agent reviews and
surfaces the judgment calls needing human authorization (ambiguous intent, spec-level
wording, anything touching `IDEA.md` or the `docs/logic.md` formalism). No agent lands
unreviewed prose.

**Depends on.** The config-deliberate branch renames README config keys to
`[epistemics]`/`[auth]`/`[postgres] timeout` and drops `[limits] answer` (PLAN Chunk 16).
Run this pass after that lands, or it edits keys mid-rename.

**Status:** deferred — docs and docstrings only, no behavior change. Multi-agent fan-out
(one branch, reviewable per area).
