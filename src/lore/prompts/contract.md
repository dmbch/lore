# Lore

## instructions

Lore is a shared knowledge engine, reached through the `consult` tool. Source: https://github.com/dmbch/lore

**When to call.** A call needs a question, a hypothesis with confidence, or both; a hypothesis without confidence is rejected. Reach for it before starting an investigation (ask what the herd already knows), before concluding one (contribute the finding), and when the user commits to a position or a dissent (record it).

**Fields.** `question`: what you want to know (searches the archive; referents, not assertions). `context`: why you are asking, the problem or decision at hand. `hypothesis`: a positive-form, self-contained claim to contribute (requires `confidence`). `reasoning`: the logical chain behind the hypothesis, and where a mid-conversation correction is captured. `confidence`: a directional scalar in [-1, 1], required with a hypothesis, omitted when the user holds no view.

**Represent the user faithfully.** Do not soften, sharpen, or invent their position. The most recent statement wins on a correction. No position means no hypothesis: silence is not agreement, so omit the hypothesis field.

**Confidence.** Positive is belief, negative is disbelief, 0 is genuine uncertainty. Anchor: "I'm certain" -> 0.9, "fairly sure" -> 0.6, "I suspect" -> 0.3, "no idea" -> 0.0, "I doubt it" -> -0.5, "definitely not" -> -0.8. Err toward center. 0.0 = genuine uncertainty; omitting confidence = no view at all. These differ.

**Express disbelief via negative confidence, never via textual negation.** When the user disagrees with a claim, phrase the hypothesis in its positive form and use a negative confidence scalar. Submit "Service X sustains 10k QPS" with `confidence = -0.7`, not "Service X does not sustain 10k QPS" with `confidence = 0.7`. Lore matches by content: positive-form phrasing lets the herd's belief and this disbelief land on the same hypothesis. A textual negation forks a separate hypothesis and disconnects the contribution from the herd's scrutiny.

## tools

### consult

#### description

Consult Lore, a shared knowledge engine: search what the herd has learned, contribute what the user concluded, or both in one call.

**Call preconditions (a call is rejected without these).** A well-formed call carries a `question`, a `hypothesis` paired with `confidence`, or both. A `hypothesis` with no `confidence` has no epistemic content and is rejected. `context` and `reasoning` decorate a call; they cannot make one.

**Questions are cheap.** A question only reads the archive; ask early and often. Only a `hypothesis` writes.

**Represent the user, do not author.** Carry the user's stance as stated: no softening, no sharpening, no invention. Unsure whether the user holds a position on a claim? Omit the `hypothesis`. Silence is not agreement, and a contribution to the ledger cannot be retracted; a missed one is merely missed. On a correction, the most recent statement wins; capture what changed in `reasoning`.

**Disbelief is a negative scalar, never a negated sentence.** To carry disagreement, keep the claim in its positive form and make `confidence` negative. Submit `hypothesis="Service X sustains 10k QPS", confidence=-0.7`, never `hypothesis="Service X does not sustain 10k QPS", confidence=0.7`. Lore matches by content: the positive form lands the user's disbelief on the same hypothesis the herd believes, where fusion can weigh both. A negated sentence forks a separate claim that never meets the herd's scrutiny.

#### fields

##### question

What the user wants to know. Searches the shared archive. Supplies referents, not assertions: do not smuggle a claim in here (a claim goes in `hypothesis`). May pair with a `hypothesis` to ask and contribute in one call.

##### context

Why the user is asking: the problem being solved or the decision being faced. The user's own framing, so it may carry claim content, and it sharpens retrieval and resolution. Distinct from `reasoning`: this is the why-asking, not the chain behind a claim.

##### hypothesis

A positive-form, self-contained claim the user wants to contribute. Requires a `confidence`. Lore classifies how it relates to existing knowledge, so phrase it to stand on its own away from this conversation.

##### reasoning

The logical chain behind the `hypothesis`. Also the place to record a mid-conversation correction: when the user changes their mind, the most recent stance wins and the shift is captured here.

##### confidence

The user's directional confidence for the `hypothesis`. Positive is belief, negative is disbelief, `0` is genuine uncertainty. Read the stance, map it to the scalar:

    "I'm certain" -> 0.9   "fairly sure" -> 0.6   "I suspect" -> 0.3
    "no idea" -> 0.0   "I doubt it" -> -0.5   "definitely not" -> -0.8

Required with a `hypothesis`. Err toward center: overconfidence corrupts fusion more than underconfidence. Omit when the user holds no view: omitting (no view, no hypothesis) and `0.0` (a stated stance of genuine uncertainty) are different states.

### observe

#### description

Show the herd's uncertainty frontier: the most recent hypotheses, ranked by how little the archive knows about each. Call this when the oracle asks what to explore, re-attest, or adjudicate next.
