# TODO

Technical debt and findings discovered during work. Each entry: what, why it matters, options, status.

---

## Prompt-engineering audit: research current best practice, apply to internal prompts

**Found:** 2026-06-21, programmer request.

**What.** Two phases. First, research current prompt-engineering best practice from
authoritative, current sources (vendor guides: Anthropic, Google/Gemini, OpenAI, not
training-cutoff recall). Then apply the findings to every prompt Lore ships:

- **FastMCP server `instructions`**: the assembled Scribe prompt (`prompts/scribe.md`,
  plus optional `narrative`/`glossary`), built by `build_system_prompt` and surfaced at
  `src/lore/adapter/mcp.py:111`. What a connecting Scribe model reads.
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
