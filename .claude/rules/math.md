---
paths:
  - "src/lore/math/**/*.py"
---

# Math Rules

@docs/logic.md is the canonical reference. Every formula implemented here must match it.

## Prior Art Protocol

Every operator implementation and test must be verified against prior art before committing:

1. **Jøsang (2016)** — cite by definition number. Mathpix Markdown at `references/subjective-logic.md`.
2. **Jøsang & Ismail (2002)** — evidence-to-opinion mapping. Mathpix Markdown at `references/beta-reputation-system.md`.
3. **Reference implementation** — cross-check against `references/src/Aggregatio/subjective_logic/src/main/java/de/tum/i4/subjectivelogic/` (tum-i4, Java). The sole retained reference for cumulative fusion operators; key file `SubjectiveOpinion.java`.
4. **Edge cases** — verify vacuous, dogmatic, and both-dogmatic degenerate cases.

Neither programmer nor Claude trusts their own math alone.

## Implementation Constraints

- **Opinion** is `(b, d, u)`. Base rate is a system constant `BASE_RATE = 0.5`.
- **ECBF** = ACBF + uncertainty maximization. Output always has `min(b, d) = 0`.
- **Decay** is calculated at read time, never stored.
- **Invariant `b + d + u = 1.0`** must hold after every operation.
