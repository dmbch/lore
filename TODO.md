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
non-reachable; revisit on the next instructor release.
