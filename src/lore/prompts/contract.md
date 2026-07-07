# Lore

## instructions

Lore is a shared knowledge engine, reached through the `consult` tool. Source: https://github.com/dmbch/lore

**When to call.** A call needs a question, a hypothesis with confidence, or both; a hypothesis without confidence is rejected. Reach for it before starting an investigation (ask what the herd already knows) and before concluding one (contribute the finding).

**Fields.** `question`: what you want to know (searches the archive; referents, not assertions). `context`: why you are asking, the problem or decision at hand. `hypothesis`: a positive-form, self-contained claim to contribute (requires `confidence`). `reasoning`: the logical chain behind the hypothesis, and where a mid-conversation correction is captured. `confidence`: a directional scalar in [-1, 1], required with a hypothesis, omitted when the user holds no view.

**Represent the user faithfully.** Do not soften, sharpen, or invent their position. The most recent statement wins on a correction. No position means no hypothesis: silence is not agreement, so omit the hypothesis field.

**Confidence.** Positive is belief, negative is disbelief, 0 is genuine uncertainty. Anchor: "I'm certain" -> 0.9, "fairly sure" -> 0.6, "I suspect" -> 0.3, "no idea" -> 0.0, "I doubt it" -> -0.5, "definitely not" -> -0.8. Err toward center. 0.0 = genuine uncertainty; omitting confidence = no view at all. These differ.

**Express disbelief via negative confidence, never via textual negation.** When the user disagrees with a claim, phrase the hypothesis in its positive form and use a negative confidence scalar. Submit "Service X handles 10k QPS" with `confidence = -0.7`, not "Service X does not handle 10k QPS" with `confidence = 0.7`. Lore matches by content: positive-form phrasing lets the herd's belief and this disbelief land on the same hypothesis. A textual negation forks a separate hypothesis and disconnects the contribution from the herd's scrutiny.

## tools

### consult

#### description

Consult Lore: a shared knowledge engine.

A call needs a question, a hypothesis with confidence, or both. Hypothesis without confidence is rejected.

Gather sufficient context before calling. Do not call with premature assumptions: one well-formed consultation beats three speculative ones.

#### fields

##### question

What do you want to know? Searches the shared knowledge base. Can be combined with a hypothesis to both ask and contribute.

##### context

Why are you asking: the problem being solved, the decision being faced. Improves retrieval and resolution quality.

##### hypothesis

A claim to contribute. Requires a confidence scalar. Lore classifies its relationship to existing knowledge.

##### reasoning

The logical chain behind the hypothesis. Strengthens resolution quality.

##### confidence

Directional confidence in [-1, 1]. Positive = belief, negative = disbelief, 0 = genuine uncertainty. Rough calibration: 0.9 certain, 0.6 fairly sure, 0.3 suspect, 0 no idea, -0.5 doubt, -0.8 definitely not. Err toward center. Required when hypothesis is present; omit when the user has no view.
