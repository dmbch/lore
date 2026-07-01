# Lore

**As far as we know.**

---

## The Idea

Teams hemorrhage knowledge. Someone debugs a subtle issue, traces a primary source, confirms a field observation, and the insight immediately evaporates into a chat or a scratchpad. Six months later, someone else burns the same three hours rediscovering it.

Lore is a shared archive for people who think for a living. It connects centaurs (a human and a frontier model, working together) into a herd that shares its memory. You work the way you already work: ask questions, offer hunches, say "I'm pretty sure" or "I doubt this, but." Contribution is a byproduct of working, never a separate task. The commons grows with use.

What makes this interesting is not the storage — it's the group epistemics.

Lore tracks two distinct kinds of being right. Whether you agreed with the herd when you spoke, and whether the herd eventually came around to your position. The second one is the trick: it is how the prophet (early, alone, vindicated) earns more than the bandwagoner (late, settled, contributing nothing). Being early and right is the most valuable move an oracle can make. Rubber-stamping a settled answer earns nothing.

Dissent is priced honestly. When an oracle contradicts something the herd believes, the counter-claim does not enter a vacuum: the herd's existing position transfers onto it as prior evidence, and the oracle's opinion fuses against that real belief rather than silence. Contrarians who turn out right earn proper credit for pushing against the grain. Contrarians who turn out wrong are measured against what the herd actually thought.

Knowledge has a half-life. Evidence fades unless someone re-touches it; a claim attested last week outweighs one from last year. The defense against stale knowledge is not a cleanup crew but a living herd that re-attests what matters.

All of it (decay rates, trust weighting, diversity thresholds) runs on a handful of epistemic hyperparameters. A fast-moving field like ML wants short half-lives and fast convergence. A stable one like medieval numismatics wants long ones, so well-attested facts do not quietly fade. The epistemics tune to the field.

Lore is open-source infrastructure, deployed on the organization's own premises or private cloud. Knowledge and IP never leave the organization's control. All prompts, scoring logic, and decision mechanisms are auditable in source.

The rest of this document is how it works.

---

## The Four Actors

**The Oracle** (human). The human in the loop. Decides where to look, which results pass the smell test, and what the evidence means.

**The Scribe** (frontend LLM). The user-facing model, consulting with Lore Core via MCP. Translates the oracle's intent into structured arguments: extracts hypotheses, chains reasoning, captures directional confidence. Expresses the oracle's concluded judgment about the evidence, not a tally of sources. When the oracle corrects course mid-conversation, the Scribe captures the correction in the reasoning field.

**The Interpreter** (fast LLM in Lore Core). The mechanical pre-processing step before the Archivist. Normalizes jargon, extracts retrieval keywords for the authority lane, and decomposes composite hypotheses into atomic propositions: single-issue statements that stand alone. Inputs already atomic pass through unchanged. The Interpreter is a lens, not a judge.

**The Archivist** (reasoning LLM in Lore Core). Retrieves the semantic neighborhood and reasons over it: paraphrase, contradiction, or orthogonal-novel. The only entity in the system that makes semantic judgments.

---

## The Data Model

Three (logical) tables.

### The Archive (Hypotheses)

Clear claims. A hypothesis is either composite (as submitted by the oracle) or atomic (produced by the Interpreter's decomposition). Same table, same formalism; no type column, no structural links.

A hypothesis carries no stored epistemic state. It is always calculated from the ledger at read time. Hypotheses are identified by content and vector embedding; a full-text index enables lexical search alongside vector similarity.

### The Ledger (Attestations)

An append-only, immutable table. Each row records ten fields:

- `id`: unique identifier for this attestation.
- `hypothesis_id`: the hypothesis being attested.
- `oracle_id`: who attested.
- `correlation_id`: ties all attestations from a single `consult` call together for traceability.
- `timestamp`: when (integer seconds, absolute).
- `t_oracle`: the oracle's trust score in [0, 1] at write time.
- `c_oracle_raw`: the oracle's raw confidence scalar in [-1, 1].
- `c_oracle_discounted`: confidence after trust and maturity discount in [-1, 1].
- `c_herd`: herd consensus after this attestation (scalar from ECBF of all decayed discounted opinions at write time; bounded by algebraic properties of the pipeline, not clamped).
- `n_oracle_prior`: count of distinct oracles that attested before this row, excluding the current oracle. A write-time snapshot the Recorder computes against the transaction's attestation map and persists alongside `c_herd`; trust scans read the column rather than recomputing the count.

Historical attestations are frozen. The ledger is never rewritten.

**Derivable, not stored:** `c_herd_prior` (LAG window over the ledger), `n_prior` (COUNT from the ledger), and conflict metrics PD/CC/DC (derivable from `c_oracle_raw` and `c_herd_prior`). The immutable ledger is the source of truth.

### The Provenance (Requests)

Every `consult` call, read or write, is recorded. One row per request. Each row stores the full consult input verbatim across structured columns — `question`, `context`, `hypothesis`, `reasoning`, `confidence` — plus correlation ID, oracle ID, and timestamp. Storage is cheap; information is valuable.

---

## The Formalism

Jøsang's Subjective Logic. All formulas, derivations, and operator definitions are in [logic.md](docs/logic.md). What follows is what each concept means and why it matters.

### Binomial Opinions

Every opinion is a triple: belief (b), disbelief (d), uncertainty (u), always summing to 1. Belief and disbelief represent evidence for and against; uncertainty represents ignorance. A global base rate (a = 0.5) fills in when evidence is absent.

A brand-new hypothesis with no attestations carries a vacuous opinion (0, 0, 1): pure ignorance. A fully certain opinion has u = 0. Everything in between represents partial knowledge. The formalism treats uncertainty as a first-class value, not the absence of confidence.

### Ingestion

The oracle's confidence is a scalar c in [-1, 1]. The Scribe captures directional confidence, not BDU triples; Lore maps the scalar to an uncertainty-maximized opinion internally. Only the scalar is stored in the ledger. BDU lives inside the math engine, derivable from c without loss for uncertainty-maximized opinions (see [logic.md](docs/logic.md)).

### Emergent Trust Grading

No oracle is privileged. All must earn influence. Every attestation is discounted before fusion, by two independent signals that compose into a single effective discount:

- **Hypothesis maturity**: how many distinct oracles have scrutinized the claim. One oracle saying something a hundred times is not the same as a hundred oracles saying it once. M = N_O / (N_O + K), where N_O is the distinct oracle count and K is a deployment parameter (default K = 1: one phantom skeptic is always in the room).
- **Oracle trust**: how well the oracle's past opinions align with where the herd has landed. Computed from a bounded scan of recent history, combining two alignment signals: write-time alignment (did the oracle agree with the herd when they spoke?) and read-time alignment (did the herd come around to the oracle's position?). The balance between them is not a configured knob; it is derived per attestation from the same hypothesis maturity M that governs discounting. On fresh hypotheses, read-time alignment dominates: the system defers judgment until the herd has moved. On mature hypotheses, write-time alignment dominates: the oracle is judged against established consensus. Prophecy and delusion are epistemically indistinguishable until the herd catches up; adaptive blending encodes this as a deployment commitment rather than an act of faith.

**The informative-commitment principle.** Trust accrues only when two things hold at once: the oracle had something to say (conviction: non-vacuous confidence), and the herd needed to hear it (information: non-trivial herd uncertainty at write time). The two conditions do different jobs. Conviction is a row weight; a vacuous attestation carries no weight at all. Information calibrates the alignment signal: each row's alignment score is discounted toward the base rate 0.5 in proportion to the herd's certainty at write time, using the binomial form of Jøsang's trust discounting operator (Def. 14.6) at the alignment-measurement level. Against a fully dogmatic herd, every agreement row collapses to 0.5 by direct algebra; bandwagon farming cannot build trust above base rate. Saying nothing on a fresh hypothesis also earns no credit. Two orthogonal gates on a single aggregate.

The effective discount P_effective = M x t_oracle enters Jøsang's trust discounting operator (Def. 14.6), which reduces the oracle's opinion toward vacuous in proportion to the system's uncertainty about the source. The discounted opinion, not the raw one, enters ECBF fusion.

**The bootstrap arc.** The default tuning is skeptical. A new oracle's first attestation on a fresh hypothesis enters at quarter strength (M = 0.5, t_oracle = 0.5, P_effective = 0.25). From there, the system earns its own confidence. As diverse oracles scrutinize a hypothesis, maturity rises. As an oracle's track record aligns with the herd, trust rises. As agreement compounds through ECBF, uncertainty falls. No individual oracle is granted authority; the herd converges through the algebra of accumulated evidence.

**Bounded vulnerability.** The trust pipeline is robust against common attacks:

- **Fresh-hypothesis attack.** A malicious oracle submitting extreme confidence on a fresh hypothesis is absorbed at quarter strength. One bullet, diminishing damage, self-correcting as honest oracles compound against it.
- **Bandwagon farming.** An oracle rubber-stamping settled hypotheses earns nothing: the information factor collapses to zero, and agreement produces no trust credit.
- **Trust exploitation.** An oracle who builds high trust through genuine contribution and then spends it on a bad claim gets one high-trust attestation before the herd corrects the hypothesis and the oracle's own trust drops.
- **Sybil attacks.** Delegated to the authentication layer. The math assumes authenticated identity; the IdP provides it.

See [logic.md](docs/logic.md) for the full formalism and security analysis.

### Epistemic Fusion

When multiple oracles attest to a hypothesis, their opinions are fused using ECBF (Epistemic Cumulative Belief Fusion). Two properties matter:

- **Agreement compounds.** Corroborating oracles drive uncertainty toward zero; the herd converges.
- **Contradiction cancels.** When evidence is evenly split, the system returns to "we don't know" rather than claiming false certainty about a tie.

### Decay

Unattested knowledge drifts back toward ignorance. Each attestation decays individually by its age; fresh evidence naturally dominates stale evidence. Decay is calculated at read time, never stored. The ledger is immutable; what changes is how old evidence is interpreted as time passes.

---

## The Interface

One MCP tool.

### `consult`

```
consult:
  question:    (optional) what do you want to know?
  context:     (optional) why are you asking?
  hypothesis:  (optional) a new claim
  reasoning:   (optional) the logical chain and relationship to existing context
  confidence:  (optional) float in [-1, 1], the oracle's directional
               confidence for the new hypothesis
```

A call must carry a `question`, a `hypothesis`, or both. A hypothesis without confidence is rejected: a claim with no confidence scalar has no epistemic content. `context` and `reasoning` decorate a valid call but cannot make one. `confidence = 0.0` paired with a hypothesis is the genuine vacuous state and is accepted; only `confidence = None` (absent) fails the rule.

The Scribe structures the oracle's intent into this schema. Positive confidence expresses belief, negative expresses disbelief, zero expresses ignorance. The Scribe need not be precise, only directionally correct. Lore maps the scalar to an uncertainty-maximized opinion internally.

The response carries one field, `answer`: the Archivist's synthesized response to the question, grounded in the herd's collective knowledge.

The oracle never needs to know about hypothesis IDs, confidence mappings, or BDU triples. The feedback loop is the pipeline itself; the Scribe submits new hypotheses in subsequent `consult` calls, retrieval finds the neighborhood, and the Archivist resolves the relationships.

---

## The Execution Loop

When `consult` is called:

### Stage 1: Decomposition (The Interpreter)

**On write** (hypothesis provided). The Interpreter normalizes jargon, extracts retrieval keywords for the authority lane, and decomposes the hypothesis into atomic propositions if it is composite. Output: the normalized original hypothesis, retrieval keywords, and zero or more atomic propositions. If the hypothesis is already atomic, the Interpreter returns only the normalized original.

**On read** (no hypothesis). The Interpreter normalizes the question text for consistent embedding and extracts retrieval keywords for the authority lane. No decomposition needed.

### Stage 2: Retrieval (Two-Lane Search)

Each output from the Interpreter gets its own vector embedding. Retrieval uses two lanes, solved in a single SQL query:

- **Lane 1, Proximity:** vector cosine similarity on hypothesis embeddings. Fetches 2x the configured limit.
- **Lane 2, Authority:** full-text search on hypothesis content, queried with the Interpreter's extracted keywords. Fetches 2x the configured limit.

Both lanes fan out at 2x limit; UNION deduplicates into a candidate pool. Each lane produces a bounded RRF score (`1/(k+rank)`, k=60). The composite is a weighted sum; weights are configurable. Results are truncated to the configured limit before handoff to the Archivist.

Composites match composites, atomics match atomics, cross-granularity matches happen naturally.

Before reasoning, the orchestrator enriches each retrieved hypothesis with its current epistemic state (decay + ECBF over the ledger). The Archivist receives grounded inputs, not raw matches.

### Stage 3: Resolution (The Archivist's Reasoning)

**On read** (no hypothesis). The Archivist calculates the current epistemic state of each retrieved hypothesis (fusing the ledger, applying decay) and synthesizes the herd's beliefs. It surfaces uncertainty clusters: the frontier where the centaur's work would have the most impact.

**On write** (hypothesis provided). The Archivist receives the normalized original, its atomic propositions (if any), and all retrieved hypotheses with their current epistemic states and per-lane retrieval scores. Per-lane scores inform the Archivist's judgment about the relationship. It can see which atomic propositions already have epistemic history, which premises the herd has opinions on, and reasons about the composite hypothesis with grounded inputs.

The Archivist thinks proposition by proposition — the original hypothesis and every atom the Interpreter produced. Each becomes a resolution naming exactly one primary relationship, optionally paired with a list of contradicted hypotheses:

- **Paraphrase.** The proposition IS an existing hypothesis — the same claim, perhaps rephrased. The resolution sets `corroborates` to that hypothesis's ID. Action: positive attestation on the corroborated hypothesis. No new node.
- **Orthogonal-novel.** The proposition is genuinely new and does not paraphrase anything in the archive. The resolution sets `contributes` to a self-contained, atomic statement of the new content. Action: store the novel and write a positive attestation on it.
- **Contradicts.** Either form may additionally list `contradicts: [HypothesisId, ...]` — existing hypotheses the proposition is mutually exclusive with. Action: disbelief attestation on each contradicted hypothesis. For an orthogonal-novel paired with contradicts, the herd's dampened contrary position transfers onto the novel as a single consolidated transfer attestation (see [logic.md](docs/logic.md)).

Across all resolutions in one consult, each existing hypothesis ID appears at most once — whether in `corroborates` or in any `contradicts` list. An oracle attests on each existing hypothesis at most once per consult call.

### Stage 4: Record & Fuse

In a single database transaction, the orchestrator dispatches each resolution. The sign of `request.confidence` carries the oracle's stance: `corroborates` and `contributes` write `+c`; each `contradicts` entry writes `−c`. Magnitude comes from the trust-discount pipeline below; the orchestrator does no inference about direction.

For each oracle attestation produced by a resolution:

- Map the confidence scalar to `c_oracle_raw`.
- Compute oracle trust: `t_oracle` from a bounded scan of the oracle's recent attestation history.
- Compute hypothesis maturity: M = N_O / (N_O + K), where N_O = the distinct prior oracles on this hypothesis plus the current one (`n_oracle_prior` + 1).
- Compute P_effective = M x t_oracle. Apply trust discounting: `c_oracle_discounted` = P_effective x `c_oracle_raw`.
- Compute `c_herd`: the new hypothesis state (decay + ECBF of all discounted attestations including the new one, projected to scalar).
- Persist the attestation row to the immutable ledger.

For an orthogonal-novel paired with `contradicts`, a single consolidated transfer attestation lands on the novel before the oracle's own. It carries the herd's already-discounted prior, derived from the latest `c_herd` row of each contradicted hypothesis fused via decayed ECBF and negated. It is recorded under the synthetic `_transfer` oracle with full credibility — no further discount, since `c_herd` already encodes source-level maturity. If the fused result rounds to zero (e.g. balanced contradictions), no transfer row is written. The oracle's attestation on the novel then fuses against this dampened prior rather than a vacuous one.

A single `consult` call may produce multiple writes. All writes execute within the same transaction; if any step fails, the transaction rolls back. After a successful write, read-after-write is always consistent.

---

## The Hyperparameters

How the organization tunes Lore to the shape of the field it serves. The first four are epistemic; the last two are retrieval plumbing.

- **attestation decay** (`[epistemics] attestation_half_life`): how fast knowledge ages. Short for fast-moving fields, long for stable scholarship. Duration string (e.g. `"90d"`).
- **maturity** (`[epistemics] maturity_k`, K in the formalism): half-saturation constant for oracle diversity. Higher values require more oracle diversity before the trust discount lifts. K also governs the adaptive blend between write-time and read-time alignment in oracle trust computation: a fresh hypothesis (low maturity) weights read-time validation, a mature hypothesis weights write-time agreement. K = 0 makes maturity transparent and collapses the blend to pure write-time. Default: 1.
- **trust decay** (`[epistemics] trust_half_life`): separate decay time scale for oracle trust alignment. Controls how fast track records age. Decoupled from attestation decay because an organization may want long-lived knowledge but fast-adapting trust, or vice versa.
- **transfer threshold** (`[epistemics] transfer_threshold`): epistemic-significance floor for the consolidated transfer attestation. When an orthogonal-novel contradicts existing claims and the fused herd magnitude falls below this value, no transfer row is written — the algebra has nothing meaningful to carry over. Decoupled from IEEE float noise so operators can tune what counts as "informationally meaningful." Default: 1e-3.
- **Retrieval weights** (`[retrieval]`): composite score weights for two-lane retrieval. Default: proximity 0.5, authority 0.5.
- **Retrieval limits** (`[retrieval]`): final result count, fan-out multiplier, and `max_keywords` for the authority lane.

---

## References

### Canonical

- Jøsang, A. (2016). *Subjective Logic: A Formalism for Reasoning Under Uncertainty.* Springer. Covers binomial opinions, epistemic cumulative belief fusion (ECBF, Def. 12.6), uncertainty maximization (Eq. 3.27), trust discounting (Def. 14.6), multi-edge trust paths (Def. 14.7, Eq. 14.13), conflict metrics (Def. 4.20).

### Reference Implementations

Implementation consulted for verification of the core operators:

- **tum-i4/Aggregatio** (GitHub, Java). Cumulative fusion operators for multi-agent systems. Primary verification reference.
