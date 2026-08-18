# Logic

The mathematical formalism behind Lore. Every formula, derivation, and design rationale lives here. For what these concepts mean and why they matter, see IDEA.md.

All operators and constructs are drawn from Jøsang's Subjective Logic (2016) unless noted otherwise. Citations reference definition numbers from that text.

---

## Notation

Two scalar prefixes carry distinct semantics throughout this document and the codebase:

- **`c_`: confidence** in [-1, 1]. Directional certainty with sign. Oracle input (`c_oracle_raw`), discounted opinion (`c_oracle_discounted`), herd consensus (`c_herd`). Mapped to/from BDU opinions inside `lore.math`. The forward mapping accepts the full mathematical domain [-1, 1]; trust discounting (P_effective < 1 for K ≥ 1) guarantees all pipeline values remain in (-1, 1) exclusive.
- **`t_`: trust** in [0, 1]. Alignment probability. Enters Jøsang's discount operator (Def. 14.6) as a factor of P_effective (see Trust Discounting). Not a confidence scalar: no negative trust, no mapping to BDU.

**`K`** is the maturity half-saturation constant. The formula `M = N_O / (N_O + K)` is a saturation function (rectangular hyperbola): `K` is the standard symbol for the half-saturation parameter.

---

## Binomial Opinions

An opinion expresses an observer's epistemic state about a binary proposition:

```
ω = (b, d, u)
```

- **b (belief):** the belief mass supporting the proposition; b is the lower bound of the projected probability P, not itself a probability (SL Def. 3.1).
- **d (disbelief):** the belief mass against the proposition; symmetrically, 1 − d upper-bounds P.
- **u (uncertainty):** ignorance, lack of evidence either way.

**Invariant:** `b + d + u = 1.0`. Always. Every operator must preserve this.

**Base rate:** a global constant `a = 0.5` for all propositions. Not carried on individual opinions; it is a system parameter (see Design Decisions). The base rate represents the prior probability in the absence of any evidence.

**Projected probability:** `P = b + a · u`. When `u = 1` (vacuous): `P = a`. When `u = 0` (dogmatic): `P = b`.

**Special opinions:**
- **Vacuous:** `(0, 0, 1)`: complete ignorance. The neutral element for information content.
- **Dogmatic:** any opinion where `u = 0`: the observer claims certainty.

**Why Subjective Logic.** Lore needed a formalism that treats uncertainty as a first-class value, not as the absence of confidence. Weighted averaging conflates "I'm split 50/50" with "I don't know." Dempster-Shafer handles uncertainty but has no base rates (its pignistic probability falls back to relative cardinalities) and a single classical combination rule where fusion situations call for a family of operators (cumulative, averaging, constraint); Jøsang's critique is not that Dempster's rule is wrong ("nothing wrong with Dempster's rule per se") but that no single rule fits every situation. Bayesian networks require global structure and conditional independence assumptions that don't fit an append-only, multi-oracle system. Subjective Logic provides the BDU tuple, commutative and associative fusion, and temporal decay, all as built-in, algebraically consistent operators.

---

## Scalar Confidence Mapping

The scalar confidence value `c ∈ [-1, 1]` is the system-wide representation for epistemic state outside the math module. BDU triples are the internal representation within `lore.math`; no other layer ever sees them. The scalar prevents LLM hallucination of invalid triples and produces uncertainty-maximized opinions by construction.

### Forward Mapping: c → Opinion

Given confidence `c`:

```
P = 0.5 + 0.5c                (projected probability)
```

The opinion is uncertainty-maximized by construction:

```
c > 0:  ω = (c, 0, 1 − c)     (belief and uncertainty)
c < 0:  ω = (0, |c|, 1 − |c|) (disbelief and uncertainty)
c = 0:  ω = (0, 0, 1)         (vacuous, pure ignorance)
```

**Proof of uncertainty maximization.** For c > 0: P = 0.5 + 0.5c. Uncertainty maximization (Eq. 3.27) yields ü = 2 · min(P, 1 − P). Since P > 0.5, min(P, 1 − P) = 1 − P = 0.5 − 0.5c = (1 − c)/2, so ü = 1 − c. Then b̈ = P − 0.5ü = (0.5 + 0.5c) − 0.5(1 − c) = c, d̈ = 0. This matches the forward mapping. Symmetric for c < 0.

**Endpoints.** `c = ±1.0` produces dogmatic opinions `(1, 0, 0)` and `(0, 1, 0)`. These are valid in the mapping's mathematical domain. The trust pipeline prevents dogmatic opinions from reaching ECBF: trust discounting with P_effective < 1 (guaranteed when K ≥ 1) strictly reduces `|c|`, and ECBF with non-dogmatic inputs cannot produce dogmatic outputs. The undogmatic constraint is a pipeline property, not an input restriction. Values outside `[-1, 1]` are rejected: they produce invalid opinions (`b > 1` or `d > 1`).

### Inverse Mapping: Opinion → c

```
c = 2P − 1 = 2(b + 0.5u) − 1 = 2b + u − 1 = b − d
```

No clamping on `c`: the algebra guarantees `|c| < 1` for all ECBF outputs with non-dogmatic inputs.

The `Opinion` constructor is the one place a clamp does apply to b, d, and u. It validates each component against `[0, 1]` widened by EPSILON, then clamps the accepted value into `[0, 1]` exactly. Like the `t_oracle` clamp documented under Oracle Trust, this is an IEEE 754 safety net, not a semantic correction: it makes the invariant the type promises algebraically true of the stored value, so call sites need not each re-handle a component sitting a float ulp outside its interval. A component genuinely outside the tolerance still raises.

**Lossless for uncertainty-maximized opinions.** When min(b, d) = 0 (the ECBF output shape), the roundtrip is exact: `to_confidence(to_opinion(c)) = c`. For non-maximized opinions (which only arise internally, never from the interface), the inverse projects through P. This is lossy but directionally correct.

### Prior Art Note

This mapping is novel. BRS Eq. 15 maps a scalar feedback value `v in [-1, 1]` to evidence parameters `r = w(1+v)/2, s = w(1-v)/2`: a one-way decomposition for ingestion, not a bidirectional opinion mapping. The scalar confidence mapping is a bidirectional constructive mapping that produces valid uncertainty-maximized opinions by construction. The key property (lossless roundtrip for uncertainty-maximized opinions) has no BRS analog.

Only the scalar is stored in the ledger; the BDU representation is derivable from it without loss (see Design Decisions: System-Wide Scalar Representation).

---

## Aleatory Cumulative Belief Fusion (ACBF)

ACBF (Def. 12.5) accumulates evidence from independent sources. It is the first step of ECBF.

**Case I.** For two opinions where at least one is non-dogmatic:

```
κ   = u_A + u_B − u_A · u_B
b_⊕ = (b_A · u_B + b_B · u_A) / κ
d_⊕ = (d_A · u_B + d_B · u_A) / κ
u_⊕ = (u_A · u_B) / κ
```

**Case II.** When all opinions are dogmatic (u = 0): the result is their average with equal weights (γ_i = 1/N, generalizing Eq. 12.15). This is the limit case: formally, γ_A = lim u_B/(u_A + u_B) as both approach zero. The implementation must handle this explicitly to avoid division by zero.

**Mixed-dogmatic partition.** When some inputs are dogmatic and the rest are non-dogmatic, the equal-weight N-ary mean runs over the dogmatic subset only. The non-dogmatic minority is excluded; its ``u_B/(u_A + u_B)`` weight collapses to zero in the same limit that drives Case II. The implementation follows Aggregatio's ``cumulativeCollectionFuse`` partitioning, whose own source cites Jøsang, Wang & Zhang (FUSION 2017, DOI 10.23919/ICIF.2017.8009820), Eqs. 16-17; in the book's terms the exclusion is Case I's dogmatic limit. Default Lore tuning keeps ``K ≥ 1``, which prevents dogmatic intermediates from ever reaching ECBF; the partition is the formal contract for ``K = 0`` deployments and any future code path that produces dogmatic discounted opinions.

**Algebraic underflow detection.** The dogmatic predicate above extends the routing to opinions whose ``u`` is small enough that ``u · u`` would underflow to zero in IEEE-754 doubles (anything below the ``2^-1074`` knee). The routing predicate `_u_in_underflow_regime` compares ``2 · log₂(u)`` to the minimum-positive-double exponent rather than testing ``u * u == 0.0`` directly, so the decision is independent of FTZ/DAZ FP-environment flags and fast-math contexts that flush subnormals platform-specifically. The boundary classifier on ``Opinion`` (``is_dogmatic`` using ``EPSILON``) is intentionally distinct from this routing predicate: ``Opinion.is_dogmatic`` is a user-facing tolerance for "treat this as dogmatic"; the routing predicate identifies the exact ``u`` range where Case I's algebra emits an order-dependent ``u = 0`` intermediate. ``_acbf_pair``'s Case II guard remains an exact ``u == 0.0`` check: Eq. 12.14 is well-defined for any ``u > 0`` and the algebraic helper only redirects inputs whose pairwise reduction would otherwise corrupt the result.

**Properties (over non-dogmatic inputs):**
- **Commutative:** `ACBF(A, B) = ACBF(B, A)`. Order of attestation doesn't matter.
- **Associative:** `ACBF(ACBF(A, B), C) = ACBF(A, ACBF(B, C))`. Pairwise reduction is valid for N opinions.
- **Non-idempotent:** `ACBF(A, A) ≠ A`. Duplicate evidence compounds, equivalent to adding evidence counters.
- **Vacuous neutrality:** fusing with the vacuous opinion preserves information content.

The associativity property holds strictly inside Case I. Once any input crosses into the underflow regime, the algebra branches into the equal-weight N-ary mean (Eq. 12.15), which is commutative but not associative with Case I.

**N-ary fusion:** because ACBF is associative over non-dogmatic inputs, N such opinions can be fused by sequential pairwise reduction in any order. The N-ary mean over the dogmatic (or partitioned) subset is applied as a single step rather than via pairwise reduction.

---

## Epistemic Cumulative Belief Fusion (ECBF)

ECBF (Def. 12.6) is the sole fusion operator in Lore. It extends ACBF with uncertainty maximization to produce epistemic opinions.

### Step 1: ACBF

Accumulate all evidence using ACBF (see above).

### Step 2: Uncertainty Maximization

After ACBF accumulates all evidence, uncertainty maximization (§3.5.6, Eq. 3.27) pushes uncertainty to its epistemic maximum while preserving the projected probability P. For binomial opinions with a = 0.5:

```
P = b + 0.5 · u                (projected probability)
ü = 2 · min(P, 1 − P)          (maximum epistemic uncertainty)
b̈ = P − 0.5 · ü               (residual belief)
d̈ = (1 − P) − 0.5 · ü         (residual disbelief)
```

**Output shape:** the result always has `min(b, d) = 0`. The opinion sits on the simplex boundary: the system expresses belief-and-uncertainty or disbelief-and-uncertainty, never both simultaneously.

Uncertainty maximization is applied **once**, after all evidence is accumulated, not between pairwise ACBF steps.

ECBF itself is therefore not associative: maximizing between pairwise steps discards canceled evidence. Counterexample: A = (0.5, 0.3, 0.2), B = (0.3, 0.5, 0.2), C = A. Pairwise ECBF(ECBF(A, B), C) = (0.2, 0, 0.8) with P = 0.6; N-ary maximize-once yields (1/13, 0, 12/13) with P = 7/13. Associativity belongs to the inner ACBF alone (Def. 12.5, the aleatory operator).

### Why ECBF, Not CCF

Lore's propositions are epistemic: "Did PR #405 cause the memory leak?" is a one-time fact, not a repeatable experiment. This distinction drove the choice of ECBF over CCF (Def. 12.9).

CCF is idempotent: `fuse([a, a]) = a`. This means 50 oracles each submitting the *identical* moderately-uncertain opinion would never drive the fused uncertainty below that of any individual (for merely-agreeing, non-identical inputs CCF can still reduce uncertainty; idempotency's premise is the identical case); the herd could never converge on repeated agreement.

**ECBF solves two problems:**

1. **Agreement compounds.** 50 oracles corroborating a hypothesis drive uncertainty toward zero via ACBF's evidence accumulation. When P ≈ 0.99, uncertainty maximization yields ü ≈ 0.02: the herd converges.

2. **Contradiction cancels.** When evidence is evenly split (P ≈ 0.5), uncertainty maximization yields ü = 1.0, b = 0, d = 0: the vacuous opinion. The system returns to "we don't know" rather than claiming false certainty about a tie.

   The cancelled result is indistinguishable from an untouched one: both read (0, 0, 1), so the fused state alone cannot separate a fifty-oracle deadlock from a hypothesis nobody has attested. The distinct-oracle count `N_O` (carried on the read path as `oracle_count`) recovers the difference, and the conflict metrics of §4.8 (PD, CC, DC) grade it. Any surface reporting a vacuous state should carry the count beside it; a bare "we don't know" conflates a fight with a silence.

**Source correlation.** ECBF assumes source independence, which oracles in an organization aren't, strictly speaking. Uncertainty maximization is not a defense: Eq. 3.27 preserves P, so correlated agreement compounds into the projected probability exactly as independent agreement would. The exposure is real and bounded by other mechanisms; see Known Residuals.

**Multiple attestations from the same oracle** compound via ACBF. Temporal decay corrects *staleness*, on the half-life timescale: an older attestation fades toward vacuous while the fresh one enters at full strength. Repetition inside a half-life still compounds; the guard that does not move with time is maturity's distinct-oracle count, which one oracle cannot raise however often they repeat themselves. No special "latest-per-oracle" logic is needed.

### Behavioral Summary

| Property | CCF (rejected) | ECBF (chosen) |
|---|---|---|
| Idempotency | Yes: `fuse([a,a]) = a` | No: duplicate evidence compounds |
| Associativity | Semi-associative (N-ary required) | Not associative as an operator; N-ary via associative inner ACBF, maximized once |
| Vacuous neutrality | `fuse([a, V]) = a` | `fuse([a, V]) = fuse([a])` |
| Output shape | Any valid (b, d, u) | Always `min(b, d) = 0` |
| Contradiction | Conflict → partial uncertainty | Conflict → vacuous |
| N agreeing oracles | No convergence | Convergence toward consensus |

---

## Temporal Decay

Unattested knowledge drifts back toward ignorance. Each individual attestation's opinion decays exponentially by its age:

```
b(Δt) = b₀ · e^(−λΔt)
d(Δt) = d₀ · e^(−λΔt)
u(Δt) = 1 − (1 − u₀) · e^(−λΔt)

where Δt = t_now − t_attestation (integer seconds)
```

λ = ln(2) / half_life, where half_life comes from `[epistemics] attestation_half_life` in the config. Decay is calculated at read time, never stored. The base rate a is preserved.

**Per-attestation, not post-fusion.** Each attestation decays individually by its own age before fusion. The nearest prior art differs on the axis: BRS (2002, Eq. 12) weights each evidence contribution by `λ^(n−i)`, sequence position rather than wall-clock age (the order in which feedback was received "plays a key role"). The time-based anchor is Jøsang (2016) §16.2.2 (Eqs. 16.5/16.6): discrete-period ageing of accumulated ratings. Structurally, Lore's decay is the trust-discounting operator (Def. 14.6) with `P = e^(−λΔt)`: b and d scale by P, uncertainty absorbs the rest, and the preserved base rate matches Eq. 14.6's third line. Note the symbol inversion: Lore's λ is a decay rate (λ = 0 means no decay); BRS and Jøsang 2016 use λ as a retention factor (λ = 1 means nothing is forgotten). The principle is shared: fresh evidence naturally dominates stale evidence because old decayed attestations carry high uncertainty, contributing proportionally less to cumulative fusion.

**Invariant preservation:** `b₀ · e^(−λΔt) + d₀ · e^(−λΔt) + 1 − (1 − u₀) · e^(−λΔt) = (b₀ + d₀ + u₀) · e^(−λΔt) − e^(−λΔt) + 1`. Since `b₀ + d₀ + u₀ = 1`, this simplifies to `e^(−λΔt) − e^(−λΔt) + 1 = 1`. ✓

**Boundary cases:**
- Δt = 0: opinion unchanged (e⁰ = 1).
- Δt → ∞: opinion approaches vacuous (0, 0, 1).
- λ = 0: time-independent (no decay).
- Δt < 0 (attestation timestamped after `t_now`): clamped to 0, so a future-dated row reads as undecayed rather than sharpened. The `decay` operator itself rejects negative `t`; the clamp lives at both call sites that compute an age, `math/hypothesis.py` and `math/service.py`. The commitment is that clock skew and future-dating cost the herd nothing beyond a row that has not started aging: the alternative, letting a negative Δt amplify belief past its stated value, would make a wrong clock an evidence multiplier.

**Monotonicity:** uncertainty monotonically increases between attestations. Belief and disbelief monotonically decrease. Knowledge that nobody re-encounters returns to "we don't know."

---

## Hypothesis State Computation

The epistemic state of a hypothesis at any time t is:

```
ω_H(t) = ECBF( decay(ω₁, λ, t−t₁), decay(ω₂, λ, t−t₂), ..., decay(ωₙ, λ, t−tₙ) )
```

Equivalently:

```
decayed     = [decay(ωᵢ, λ, t − tᵢ) for each attestation i]
acbf_result = ACBF(decayed)
ω_H(t)     = maximize_uncertainty(acbf_result)
```

Decay each attestation by its individual age. Accumulate with ACBF. Uncertainty-maximize once at the end.

**Read path:** t = t_now. All attestations decay by their current age.

**Write path:** within the same transaction as a ledger write, the system simulates a read at t = t_write. The new attestation has Δt = 0 (undecayed). Existing attestations decay relative to t_write. The full set is fused with ECBF. All opinions entering fusion are the *discounted* values (`c_oracle_discounted`), not the raw oracle input; trust discounting is applied before fusion, not after.

**Single-attestation case:** N = 1 produces the uncertainty-maximized form of the single (possibly decayed) opinion.

**All-stale case:** when all attestations have decayed to near-vacuous, the fused result approaches the vacuous opinion. The hypothesis becomes "Stale."

---

## Trust Discounting

Jøsang's trust discounting operator (Def. 14.6) applied at write time. Each attestation is discounted before it enters ECBF fusion, based on two factors: the oracle's trust and the hypothesis's maturity.

### The Discount Operator (Def. 14.6)

For the binomial case with base rate a = 0.5:

```
Given P_effective ∈ [0, 1]:

b_disc = P_effective · b_source
d_disc = P_effective · d_source
u_disc = 1 − P_effective · (1 − u_source)
```

BDU invariant preserved by construction: `b_disc + d_disc + u_disc = P_effective · (b + d) + 1 − P_effective · (1 − u) = P_effective · (1 − u) + 1 − P_effective + P_effective · u = 1`. ✓

When P_effective = 1: opinion unchanged (transparent). When P_effective = 0: opinion becomes vacuous.

**The base rate is preserved.** Projected probability after discount: `P_disc = b_disc + 0.5 · u_disc = P_effective · b + 0.5 · (1 − P_effective · (1 − u)) = P_effective · b + 0.5 − 0.5 · P_effective + 0.5 · P_effective · u = P_effective · (b + 0.5u) + 0.5 · (1 − P_effective) = P_effective · P_source + 0.5 · (1 − P_effective)`. When P_source = 0.5 (vacuous): P_disc = 0.5. Discount preserves the base rate for vacuous opinions.

**Uncertainty maximization is preserved.** For uncertainty-maximized source opinions (min(b, d) = 0): if b_source = 0, then b_disc = 0 and min(b_disc, d_disc) = 0. If d_source = 0, then d_disc = 0 and min(b_disc, d_disc) = 0. The output is uncertainty-maximized.

### The Scalar Shortcut

For uncertainty-maximized source opinions (which all oracle inputs are by construction):

```
c_oracle_discounted = P_effective · c_oracle_raw
```

Direction preserved; magnitude reduced.

**Proof.** For c > 0, source opinion is (c, 0, 1 − c). Def. 14.6 gives (P_eff · c, 0, 1 − P_eff · c). Inverse mapping: b − d = P_eff · c. Symmetric for c < 0. The output is uncertainty-maximized (min(b, d) = 0 is preserved by the proof above).

**Magnitude reduction.** |c_oracle_discounted| = |P_effective · c_oracle_raw| ≤ |c_oracle_raw|. Trust discounting never amplifies; it can only reduce magnitude. When K ≥ 1: P_effective < 1.0, so |c_oracle_discounted| < |c_oracle_raw| strictly for c ≠ 0 (the vacuous input is the fixed point: 0 maps to 0). Even if the oracle submits c = ±1.0, the discounted value is strictly interior to (-1, 1).

**M and t_oracle have no underlying opinion structure.** They are scalars in [0, 1] interpreted as projected probabilities of implicit trust edges (following the structure of Def. 14.7 / Eq. 14.13). Trust revision operators (Jøsang §14.5) cannot apply to these edges; they are novel compositions, not direct instantiations of agent-to-agent referral trust.

### P_effective: Two-Edge Trust Path

The effective discount factor composes two independent trust signals:

```
P_effective = M · t_oracle
```

Where M is hypothesis maturity (see below) and t_oracle is oracle trust (see below). The operator that consumes P_effective is Def. 14.6 (the binomial trust discount with base rate a = 0.5), applied once per attestation (one source, one target). (An edge-count caveat: the book titles Def. 14.6 a "Two-Edge Path" because it counts the terminal functional edge alongside the referral edge; Lore's "single-edge" reading counts referral edges only. Same operator, different bookkeeping.) The path-form generalization (Def. 14.7 / Eq. 14.13) reduces to Def. 14.6 when the path has one edge; Lore's composition `M · t_oracle` is a product of two implicit edge probabilities collapsed into a single effective discount, so only the single-edge form is ever instantiated. The discount algebra depends only on P_effective, not on the internal structure of the trust edges.

### Graceful Degradation

When K = 0: M = 1.0 for all N_O ≥ 1. With perfect alignment t_oracle = 1.0, P_effective = 1.0 and the discount is fully transparent: dogmatic opinions pass through undiscounted. This is an explicit deployer opt-in: K = 0 disables the maturity safeguard. ECBF with dogmatic inputs can produce dogmatic outputs. Deployers who set K = 0 accept this trade-off.

When K ≥ 1 (default): M < 1.0 always, so P_effective < 1.0 strictly. Trust discounting reduces every opinion toward vacuous. Even c = ±1.0 inputs become strictly interior to (-1, 1) after discount. ECBF with non-dogmatic inputs cannot produce dogmatic outputs: the ACBF formula preserves u > 0 when all input uncertainties are positive (u_⊕ = u_A · u_B / κ > 0), and uncertainty maximization preserves the projected probability. The undogmatic constraint is a pipeline property of K ≥ 1, not an input restriction.

---

## Hypothesis Maturity

The system's confidence that a hypothesis has been adequately scrutinized. A saturation function (rectangular hyperbola) over distinct oracle count:

```
n_oracle_prior = COUNT(DISTINCT oracle_id)
                 WHERE hypothesis_id = X
                 AND timestamp < t_write
                 AND oracle_id != current_oracle_id

N_O = n_oracle_prior + 1    (including the current attestor)
M   = N_O / (N_O + K)
```

Where K = half-saturation constant (deployment parameter, default K = 1). The filter excludes the current oracle from the prior count, making `+1` unconditionally correct: a new oracle has no prior attestations to exclude; a returning oracle is excluded then re-added exactly once. No double-counting by construction.

```
n_oracle_prior = 0 → N_O = 1, K = 1:  M = 1/2 = 0.50
n_oracle_prior = 1 → N_O = 2:          M = 2/3 ≈ 0.67
n_oracle_prior = 4 → N_O = 5:          M = 5/6 ≈ 0.83
n_oracle_prior = 9 → N_O = 10:         M = 10/11 ≈ 0.91
```

**Properties:**
- **No deadlock:** M > 0 for all N_O ≥ 1. The first attestation always contributes.
- **Monotonically increasing, concave** (diminishing returns).
- **K = 1** means "one phantom skeptic is always in the room."
- **K = 0** makes maturity transparent: M = 1.0 for all N_O ≥ 1.

`n_oracle_prior` is derivable from the ledger, and the Recorder derives it at write time against the transaction's attestation snapshot to compute M. It is *also* persisted on the row as a write-time snapshot: trust scans read the column instead of recomputing the count with a correlated subquery. The immutable ledger remains the source of truth; the column is a consistent-by-construction cache of what the Recorder saw.

The same `N_O = n_oracle_prior + 1` rule is reused inside the oracle trust derivation (see Adaptive Blend below). Both call sites apply it relative to "the current oracle on the row being scored": at write time that is the new attestor, at trust-scan time it is the oracle whose trust we are computing. One rule, two roles.

---

## Oracle Trust

The oracle's alignment probability t_oracle ∈ [0, 1], computed from the immutable ledger at write time. No peer assessment, no settlement events. A bounded scan of the oracle's recent attestation history, gated by a witness rule: only rows whose hypothesis carries evidence from other oracles are scored. The derivation composes standard Subjective Logic operators (PD (Eq. 4.61) for alignment, Def. 14.6 (binomial trust discounting) for the informative-commitment gate) with Lore's own maturity saturation M for the adaptive blend (a Lore construction, see Hypothesis Maturity), into a conviction-weighted average. No novel operators; one Lore-defined saturation function.

### The Principle

Two questions, not one:

1. **Did the oracle's opinion match the herd?** The alignment signal.
2. **Was there any uncertainty for the oracle's opinion to resolve?** The information signal.

Trust accrues only when both conditions hold: the oracle had something to say (conviction), and the herd needed to hear it (information). Agreement with a near-dogmatic herd resolves no uncertainty and earns no credit. Saying nothing on a fresh hypothesis earns no credit. Both are informationally empty events.

### Alignment (PD-Based, Others-Only Reference)

For each past attestation by oracle X on some hypothesis P_i:

```
c_herd_prior_i = c_herd from the preceding attestation on P_i (0.0 if first)
c_herd_now_i   = the others-only herd state of P_i, recomputed at scan time:
                 decay + ECBF over the attestations by every oracle except X
                 (the synthetic _transfer included) inside the attestation
                 decay window

align_write_i = 1 − 0.5 · |c_oracle_raw_i − c_herd_prior_i|        (1 − PD, Eq. 4.61)
align_read_i  = 1 − 0.5 · |c_oracle_raw_i − c_herd_now_i|          (1 − PD, Eq. 4.61)
```

Both signals are binomial specializations of PD: since `P = 0.5 + 0.5c`, we have `|P_a − P_b| = 0.5 · |c_a − c_b|`. Alignment is `1 − PD`, in (0, 1] for any inputs in (−1, 1).

**The read-time reference is self-free.** An earlier revision read `c_herd_now` from the hypothesis's latest stored row, which included X's own attestations: agreeing with your own echo counted as alignment, and iterated solo histories converged on t_oracle ≈ 0.92 (see Security Analysis, Self-Referential Read-Time Credit). The reference is now recomputed from the ledger with X's rows excluded. This is exact, not an approximation: hypothesis state is always derived at read time, so the others-only re-fusion runs the ordinary read path over a filtered row set. `c_herd_now` survives as the name of this recomputed state; no column stores it.

**The witness rule.** A row enters the trust scan only if at least one other oracle (the synthetic `_transfer` counts) has attested on its hypothesis inside the attestation decay window. Unwitnessed rows contribute to neither numerator nor denominator: without a witness there is no reference to align against, and a solo history is informationally identical to no history. The rule gates trust and nothing else: the hypothesis is stored, retrieved, and fused like any other, and because trust is recomputed from the ledger on every scan, the row starts counting the moment the herd answers. The rule generalizes the transfer attestation's rationale, blocking self-referential trust credit, from contradicting novels to all novels.

### Adaptive Blend (Replaces Fixed `w`)

The balance between write-time and read-time alignment is not a configured constant. It is derived per attestation from the same maturity saturation M that governs trust discounting:

```
n_oracle_prior_i = distinct oracles with attestations on P_i before this row,
                   excluding the current oracle X
N_O_i            = n_oracle_prior_i + 1
M_write_i        = N_O_i / (N_O_i + K)

align_i = M_write_i · align_write_i + (1 − M_write_i) · align_read_i
```

M_write_i uses exactly the same `N_O = n_oracle_prior + 1` rule and the same K as the discount operator's hypothesis maturity. One M per attestation, not two.

**Why adaptive.** A fresh hypothesis has no meaningful herd state at write time: `c_herd_prior ≈ 0`, which is informationally identical to the vacuous base rate. Comparing the oracle to this empty signal is meaningless. As diverse oracles scrutinize the hypothesis and the herd converges, the write-time snapshot becomes informative. M_write encodes exactly this: it is the system's confidence that the prior herd state was worth agreeing with in the first place.

| State | n_oracle_prior | M_write (K=1) | Dominant signal |
|---|---|---|---|
| Fresh hypothesis, oracle X is first attester | 0 | 0.50 | 50/50: read-time at parity, its K = 1 ceiling |
| Second distinct oracle | 1 | 0.67 | Read-time still weighty (prophet-friendly) |
| Fifth distinct oracle | 4 | 0.83 | Write-time dominates (conformity-rewarding) |
| Tenth+ distinct oracle | 9 | 0.91 | Write-time strongly dominates |

Fresh hypothesis: read-time holds half the weight, its K = 1 ceiling, which caps the write-time penalty against the vacuous prior at half; K > 1 shifts fresh rows further toward read-time. Mature hypothesis: write-time dominates → the oracle is judged by the herd's state when they spoke, which has become an informative reference.

**K = 0 degeneracy.** When K = 0: M_write = 1.0 for all N_O ≥ 1, so `align_i = align_write_i`, pure write-time. This is consistent with the discount operator's K = 0 behavior ("maturity transparent"): both the discount and the trust blend become transparent together. An explicit deployer opt-in.

### The Informative-Commitment Gate (Anti-Farming)

Alignment without information is empty. If the herd already holds `c_herd_prior = 0.95`, an oracle submitting `c_oracle_raw = 0.95` produces `align = 1.0`: perfect agreement, but the oracle has resolved no uncertainty. The herd already knew. The measurement is unreliable as evidence about the oracle's judgment: they may be a clear thinker, or they may be rubber-stamping. We should not accrue trust from signals whose informational value is near zero.

The herd's uncertainty at write time is exactly the reliability of the alignment signal:

```
info_i = 1 − |c_herd_prior_i|   = u_herd_prior_i
```

For uncertainty-maximized opinions (all stored `c_herd` values, see System-Wide Scalar Representation), the identity `u = 1 − |c|` is exact: `info_i` is literally the herd's uncertainty mass at the moment of the oracle's attestation.

| Herd state at write time | \|c_herd_prior\| | info |
|---|---|---|
| Vacuous (fresh) | 0.00 | 1.00 (full credit) |
| Moderately formed | 0.50 | 0.50 |
| Near-dogmatic (settled) | 0.95 | 0.05 (negligible) |
| Dogmatic | 1.00 | 0.00 (no credit) |

Info alone does not close the farming surface. The scattershot vector: an oracle spraying near-vacuous conviction across fresh, witnessed hypotheses collects `info = 1` on every row, and the fresh-row write leg is near-perfect against a vacuous prior (`align_write = 1 − 0.5 · conviction ≈ 1`) and holds half the blend wherever the herd lands (`align ≥ 0.75 − 0.5 · conviction`, averaging ≈ 0.9). Under info-only calibration those rows scored high effective alignment while asserting almost nothing (pre-fix realized farm ≈ 0.80; see Security Analysis, Low-Conviction Scattershot). The commitment half of the informative-commitment principle must live in the calibration too. The composite signal:

```
signal_i          = conviction_i · info_i
effective_align_i = signal_i · align_i + (1 − signal_i) · 0.5
```

Rather than using either factor as a row weight (a weight cancels symmetrically in a weighted average and fails to bound farming), we apply the binomial form of Jøsang's trust discounting operator (Def. 14.6) to each row's alignment score, with `signal_i` as the discount factor and base rate `a = 0.5`. This is the projected-probability expression derived in the Trust Discounting section above, `P' = P_eff · P_source + 0.5 · (1 − P_eff)`, with `P_eff = signal_i` and `P_source = align_i`. The product form is itself canonical: two Def. 14.6 discounts toward the same base rate compose into one, `p₂ · (p₁ · a + (1 − p₁) · 0.5) + (1 − p₂) · 0.5 = p₁p₂ · a + (1 − p₁p₂) · 0.5`, so discounting alignment by info and the result by conviction is algebraically the single discount by `conviction · info`.

**The algebraic form is canonical Def. 14.6.** The semantic choice (using herd uncertainty at write time and the speaker's own commitment as a discount on an alignment measurement, rather than as trust in an information source) is a novel application of the operator, in the same spirit as the composition `P_effective = M · t_oracle` used in the main trust pipeline (which composes non-opinion scalars into a Def. 14.7-style product). Neither introduces a new operator; both reuse standard SL operators with reinterpreted inputs.

**Properties:**

| conviction | info | signal | align | effective_align | Scenario |
|---|---|---|---|---|---|
| 1.00 | 1.00 | 1.00 | 1.00 | 1.000 | Committed rightness on a fully-uncertain herd: full credit |
| 1.00 | 1.00 | 1.00 | 0.00 | 0.000 | Committed wrongness on a fully-uncertain herd: full penalty |
| 1.00 | 0.00 | 0.00 | 1.00 | 0.500 | Bandwagon on dogmatic herd: neutral at any conviction |
| 0.20 | 1.00 | 0.20 | 1.00 | 0.600 | Hedged rightness on a fresh herd: capped at 0.5 + conviction/2 |
| 0.50 | 0.50 | 0.25 | 1.00 | 0.625 | Moderate conviction, half-formed herd: partial credit |
| 0.50 | 0.50 | 0.25 | 0.00 | 0.375 | Moderate conviction, half-formed herd, wrong: partial penalty |

The asymmetries are deliberate. *Informative, committed* wrongness (signal = 1, align = 0) is punished to 0; *uninformative* wrongness is neutralized to 0.5 (an oracle disagreeing with a dogmatic herd is epistemically indistinguishable from a prophet the herd cannot move to meet; we default to neutral until fresh evidence arrives); *hedged* anything is pinned near 0.5 (an oracle who asserts almost nothing can be credited with almost nothing, in either direction).

### Conviction Weighting (Weight and Calibration)

```
conviction_i = |c_oracle_raw_i|
```

For uncertainty-maximized oracle inputs, `conviction = 1 − u_oracle`. This is the term's load-bearing sense throughout the trust formalism: a fixed property of the row, `|c_oracle_raw|` as the oracle stated it, which no later event changes. The decay prose uses the same word informally for what erodes with age ("decay erodes conviction, not direction"); that erosion is of the fused opinion's magnitude, never of a stored `conviction_i`. Trust scans read the raw scalar, so a decayed herd state cannot retroactively soften how hard an oracle once committed.

Conviction plays two roles:

- **Row weight.** Each row enters the aggregate weighted by `conviction_i · weight_i`: a vacuous attestation carries no weight at all, and the all-vacuous history falls through to the base-rate fallback.
- **Calibration factor.** Conviction is half of the composite `signal_i = conviction_i · info_i` that discounts the row's alignment toward 0.5.

**Why both, and why that is not a double-count.** A weight normalizes out over a uniform history: `Σ(x · c · w) / Σ(c · w) = x` whenever every row carries the same effective alignment x, so the weight alone cannot bound what a uniform low-conviction campaign earns; that is precisely the scattershot vector. A calibration moves each x itself and cannot cancel. The two roles are complementary, not redundant: drop the weight and vacuous rows dilute the average; drop the calibration and hedged rows score at full signal strength. Structurally the motivation parallels Jøsang's conjunctive certainty CC (Eq. 4.62), a product of two certainties: both parties must have something to say for agreement or conflict to be meaningful. Lore's pair complements one factor: speaker certainty (conviction) × audience uncertainty (info). The speaker had something to say, and the audience could benefit from hearing it.

### Trust Derivation

Temporal decay as in the previous formulation, with its own half-life:

```
weight_i = e^(−λ_trust · Δt_i)    where λ_trust = ln(2) / trust_half_life
```

Trust decay is independent of attestation decay. Attestation decay governs how fast *knowledge* ages; trust decay governs how fast *track records* age.

Conviction-weighted average of effective alignment scores over all recent attestations by X:

```
t_oracle = Σ(effective_align_i · conviction_i · weight_i)
         / Σ(conviction_i · weight_i)
```

### Boundary Cases

**Cold start:** no history → `t_oracle = 0.5` (base rate trust: the connection to a = 0.5 is deliberate).

**Empty-denominator fallback:** when the countable rows contribute `Σ(conviction_i · weight_i) = 0`, fall back to `t_oracle = 0.5`. Since every `weight_i > 0`, this happens exactly when every countable row has `conviction_i = 0` (an all-vacuous history) or when the witness rule leaves no countable rows at all (an all-solo history). Both are informationally identical to cold start, and the fallback treats them identically.

**The witness theorem.** *Theorem:* if no oracle other than X has attested a row's hypothesis inside the attestation decay window, the row contributes to neither numerator nor denominator; if that holds for every row, the empty-denominator fallback fires and `t_oracle = 0.5` exactly. Solo spam of novel hypotheses earns base rate however large the campaign: there is no reference to have aligned with, so there is no alignment to score.

**The bandwagon theorem.** Handled non-asymptotically by the numerator, at any conviction. *Theorem:* if every `info_i = 0` and at least one `conviction_i > 0`, then

```
signal_i          = conviction_i · 0 = 0
effective_align_i = 0 · align_i + 1 · 0.5 = 0.5    for every row
t_oracle = Σ(0.5 · c_i · w_i) / Σ(c_i · w_i) = 0.5
```

exactly. A purely dogmatic bandwagon history produces trust equal to base rate, by direct algebra, not by limit, not by fallback, not by floating-point luck. Bandwagon farming cannot build trust above base rate.

**The conviction theorem (calibration mirror).** As `conviction_i → 0`, `signal_i → 0` and `effective_align_i → 0.5` regardless of alignment; at `conviction_i = 0` the row's weight is also zero and it leaves the aggregate entirely. A hedged history is pinned to base rate twice over: the calibration flattens each row's signal, and the weight removes the row at the vacuous limit. Together with the bandwagon theorem this is the informative-commitment principle as algebra: either factor of `signal = conviction · info` at zero collapses the row's evidence to base rate.

**Defensive clamp:** the algebra guarantees `t_oracle ∈ [0, 1]` for any non-empty denominator. A `max(0.0, min(1.0, ...))` clamp is retained as an IEEE 754 safety net against floating-point drift, not a semantic correction.

**Deterministic accumulation.** The numerator and denominator sums use `math.fsum`, which is Shewchuk-exact and order-independent. The ledger stores `t_oracle`, so deterministic accumulation matters more than raw precision: bit-stable trust across row reorderings means the value persisted on the attestation row does not depend on the iteration order of the trust scan.

**No prior row, no signal.** When the trust scan returns a row that is the oracle's first attestation on its hypothesis, `c_herd_prior` defaults to `0.0`. This is algebraically equivalent to a stored `c_herd_prior = 0.0`: both produce `info = 1 - |c_herd_prior| = 1`, so the calibration is left entirely to conviction (`signal = conviction`), exactly what the Prophet archetype requires: their defining trait is committing hard while the herd knows nothing. Detecting fresh hypotheses elsewhere in the pipeline relies on `n_oracle_prior` (the distinct-oracle count) rather than `c_herd_prior` because the latter cannot distinguish an empty herd from a balanced one.

**Bounds.** Decay is the soft bound (old attestations contribute negligible weight). The hard bound is `5 × trust_half_life`: at five half-lives, residual weight is ≈3%. Beyond that is noise.

**Path-dependent trust.** Trust at write time is computed from the *previous* state: lagged trust, the same approach as PageRank and BRS. This is inherent to any system where trust affects fusion and fusion affects trust. The lag ensures each write sees a consistent snapshot, and over time the system converges as fresh attestations dominate stale ones via decay.

### Worked Examples

Four archetypes, each computed against the same seeded ledger. To keep the arithmetic transparent: K = 1, trust decay disabled (weight = 1 for every row), and `c_herd_now` taken as the others-only recomputed state after all seeded attestations (the scored oracle's own rows excluded, per the Alignment section). Every example row is witnessed; the witness theorem covers the unwitnessed case exactly.

All values rounded to three decimals.

#### Example 1: The Prophet

Oracle P speaks first on a fresh hypothesis H with `c = 0.8`. Three later oracles attest on H in sequence, converging the herd toward strong belief.

| Event | oracle | c_oracle_raw | c_herd_prior | c_herd_after |
|---|---|---|---|---|
| 1 | P | 0.8 | 0.00 | 0.20 |
| 2 | A | 0.7 | 0.20 | 0.35 |
| 3 | B | 0.8 | 0.35 | 0.50 |
| 4 | C | 0.75 | 0.50 | 0.60 |

(Herd values are illustrative post-ECBF-and-decay scalars; the 0.60 read against P's row is the others-only re-fusion of A, B, and C's evidence, which is what the herd converged to without P's own voice in the reference.)

At the moment we compute P's trust, P has exactly one historical attestation (row 1), witnessed by A, B, and C. n_oracle_prior for row 1 = 0 (P was first), so N_O = 1 and M_write = 1/(1+1) = 0.500.

- `align_write      = 1 − 0.5 · |0.8 − 0.00| = 1 − 0.400 = 0.600`
- `align_read       = 1 − 0.5 · |0.8 − 0.60| = 1 − 0.100 = 0.900`
- `align            = 0.500 · 0.600 + 0.500 · 0.900 = 0.300 + 0.450 = 0.750`
- `conviction       = |0.8| = 0.800`
- `info             = 1 − |0.00| = 1.000`
- `signal           = 0.800 · 1.000 = 0.800`
- `effective_align  = 0.800 · 0.750 + 0.200 · 0.5 = 0.600 + 0.100 = 0.700`
- numerator: `0.700 · 0.800 · 1 = 0.560`
- denominator: `0.800 · 1 = 0.800`
- `t_oracle = 0.560 / 0.800 = 0.700`

Because the herd was fully uncertain when P spoke (`info = 1`), the calibration is carried by conviction alone: `signal = 0.8`, so P keeps 80% of their alignment signal and base rate fills the rest. Had nobody followed, the witness rule would have dropped the row and P would sit at 0.5: prophecy is scored only once the herd shows up to be right about. Adaptive w matters most when the prophet has *multiple* attestations on fresh hypotheses: every one gets M_write ≈ 0.5 and defers to read-time, protecting the prophet from write-time penalties on vacuous priors. (A fixed w = 0.5 would be numerically identical here; a fixed w = 0.8 would have produced `align = 0.8·0.600 + 0.2·0.900 = 0.660` and `t_oracle = 0.8·0.660 + 0.1 = 0.628`: about 7 points of penalty for being "far from nothing.")

#### Example 2: The Bandwagoner

Oracle B only ever attests on hypotheses the herd has already settled. Take a single representative row (the rest of the history would contribute similar values) against a near-dogmatic herd:

| hypothesis | c_oracle_raw | n_oracle_prior | c_herd_prior | c_herd_now |
|---|---|---|---|---|
| H1 (settled, pro) | 0.90 | 4 | 0.92 | 0.94 |

N_O = 5, M_write = 5/6 ≈ 0.833: write-time dominates. Alignment is near-perfect (the bandwagoner is matching the herd by design).

- `align_write      = 1 − 0.5 · |0.90 − 0.92| = 0.990`
- `align_read       = 1 − 0.5 · |0.90 − 0.94| = 0.980`
- `align            = 0.833 · 0.990 + 0.167 · 0.980 = 0.9883`
- `info             = 1 − 0.92 = 0.080`
- `conviction       = 0.900`
- `signal           = 0.900 · 0.080 = 0.072`
- `effective_align  = 0.072 · 0.9883 + 0.928 · 0.5 = 0.0712 + 0.4640 = 0.5352`

The alignment is 0.9883, almost perfect. But `info = 0.08` collapses the signal to 0.072 before that alignment can matter: `effective_align ≈ 0.535`, barely above base rate, and full conviction cannot buy it back (conviction multiplies a near-zero info). Because every row of a bandwagon history looks like this, the conviction-weighted average over such rows stays pinned just above 0.5. The asymptotic case is covered by the bandwagon theorem in Boundary Cases: when the herd is fully dogmatic on every row (`info_i = 0`), each `effective_align_i = 0.5` exactly, so `t_oracle = 0.5` by direct algebra, no fallback, no limit. Bandwagon farming cannot build trust above base rate.

#### Example 3: The Contrarian

Oracle C attests against the herd on one fresh and one moderately-formed hypothesis.

| Event | hypothesis | c_oracle_raw | n_oracle_prior | c_herd_prior | c_herd_now |
|---|---|---|---|---|---|
| 1 | H4 (fresh) | −0.8 | 0 | 0.00 | 0.30 |
| 2 | H5 (moderate) | −0.7 | 2 | 0.50 | 0.55 |

Row 1: N_O = 1, M_write = 0.500.
- `align_write      = 1 − 0.5 · |−0.8 − 0.00| = 1 − 0.400 = 0.600`
- `align_read       = 1 − 0.5 · |−0.8 − 0.30| = 1 − 0.550 = 0.450`
- `align            = 0.500 · 0.600 + 0.500 · 0.450 = 0.525`
- `info             = 1 − 0.00 = 1.000`
- `conviction       = 0.800`
- `signal           = 0.800 · 1.000 = 0.800`
- `effective_align  = 0.800 · 0.525 + 0.200 · 0.5 = 0.420 + 0.100 = 0.520`
- num: `0.520 · 0.800 = 0.416`
- den: `0.800`

Row 2: N_O = 3, M_write = 3/4 = 0.750.
- `align_write      = 1 − 0.5 · |−0.7 − 0.50| = 1 − 0.600 = 0.400`
- `align_read       = 1 − 0.5 · |−0.7 − 0.55| = 1 − 0.625 = 0.375`
- `align            = 0.750 · 0.400 + 0.250 · 0.375 = 0.300 + 0.09375 = 0.39375`
- `info             = 1 − 0.50 = 0.500`
- `conviction       = 0.700`
- `signal           = 0.700 · 0.500 = 0.350`
- `effective_align  = 0.350 · 0.39375 + 0.650 · 0.5 = 0.1378 + 0.3250 = 0.4628`
- num: `0.4628 · 0.700 = 0.3240`
- den: `0.700`

Totals:
- numerator ≈ `0.416 + 0.3240 = 0.7400`
- denominator ≈ `0.800 + 0.700 = 1.500`
- `t_oracle ≈ 0.7400 / 1.500 ≈ 0.493`

Contrarian earns trust close to base rate, slightly below 0.5. Row 1 is on a fresh hypothesis (`info = 1`), so the calibration is pure conviction: the genuine disagreement passes at 80% strength, and because the blended alignment (0.525) already sits near base rate, the pull toward 0.5 barely moves it. Row 2 is softened twice, by a half-formed herd and by moderate conviction (`signal = 0.35`): `effective_align = 0.463` rather than `0.394`. The contrarian's honest, committed disagreements on informative hypotheses still pull trust down; hedged or less-informative disagreements are discounted toward neutral.

#### Example 4: The Honest Conformist

Oracle H attests on two mature-but-still-fluid hypotheses with moderate conviction, closely aligned with the herd.

| Event | hypothesis | c_oracle_raw | n_oracle_prior | c_herd_prior | c_herd_now |
|---|---|---|---|---|---|
| 1 | H6 (mature, fluid) | 0.60 | 4 | 0.40 | 0.55 |
| 2 | H7 (very mature, fluid) | 0.50 | 9 | 0.30 | 0.45 |

Row 1: N_O = 5, M_write = 5/6 ≈ 0.833.
- `align_write      = 1 − 0.5 · |0.60 − 0.40| = 1 − 0.100 = 0.900`
- `align_read       = 1 − 0.5 · |0.60 − 0.55| = 1 − 0.025 = 0.975`
- `align            = 0.833 · 0.900 + 0.167 · 0.975 = 0.7500 + 0.1625 = 0.9125`
- `info             = 1 − 0.40 = 0.600`
- `conviction       = 0.600`
- `signal           = 0.600 · 0.600 = 0.360`
- `effective_align  = 0.360 · 0.9125 + 0.640 · 0.5 = 0.3285 + 0.3200 = 0.6485`
- num: `0.6485 · 0.600 = 0.3891`
- den: `0.600`

Row 2: N_O = 10, M_write = 10/11 ≈ 0.909.
- `align_write      = 1 − 0.5 · |0.50 − 0.30| = 1 − 0.100 = 0.900`
- `align_read       = 1 − 0.5 · |0.50 − 0.45| = 1 − 0.025 = 0.975`
- `align            = 0.909 · 0.900 + 0.091 · 0.975 = 0.8182 + 0.0886 = 0.9068`
- `info             = 1 − 0.30 = 0.700`
- `conviction       = 0.500`
- `signal           = 0.500 · 0.700 = 0.350`
- `effective_align  = 0.350 · 0.9068 + 0.650 · 0.5 = 0.3174 + 0.3250 = 0.6424`
- num: `0.6424 · 0.500 = 0.3212`
- den: `0.500`

Totals:
- numerator ≈ `0.3891 + 0.3212 = 0.7103`
- denominator ≈ `0.600 + 0.500 = 1.100`
- `t_oracle ≈ 0.7103 / 1.100 ≈ 0.646`

The honest conformist earns solid trust around 0.65, above base rate but now below the prophet's 0.700: conviction calibration rewards the prophet's defining trait, committing hard where the herd knows nothing, over careful agreement where it knows plenty. The cap is structural: each row's `effective_align` is bounded above by `0.5 + signal/2 = 0.5 + conviction · info/2` (since `align ∈ [0, 1]`), so reaching 1.0 requires perfect alignment, full conviction, *and* a near-fully-uncertain herd at once. An oracle who only attests on maturely-formed hypotheses is capped around the typical `conviction · info` of their targets. Climbing higher requires committed contributions on less-formed hypotheses, where agreement actually resolves uncertainty: exactly the informative commitment the formula is designed to reward.

---

## Epistemic Transfer

When oracle γ contributes a novel hypothesis h₂ that contradicts one or more existing hypotheses, the system stores a single **transfer attestation** on h₂ carrying the herd's already-discounted prior on the contradiction. The transfer prevents γ from receiving self-referential trust credit on the novel; without it, γ attests on a vacuous h₂, trivially agrees with themselves, and earns unwarranted trust.

### Definition

For a single contradicted hypothesis h₁, the transfer is the negated decayed `c_herd` from h₁'s most recent attestation row:

```
c_transfer = −decay(c_herd(h₁, t_latest), λ, t_now − t_latest)
```

Where `t_latest` is the timestamp of h₁'s most recent attestation row, and `c_herd(h₁, t_latest)` is the stored `c_herd` value on that row: the cumulative herd consensus at write time under the cumulative storage convention. In scalar form, `decay(c) = c · e^(−λΔt)`: decay scales b and d by the same factor, so it commutes with the scalar mapping.

For multiple contradicted hypotheses, the transfer fuses across them via ECBF before negation:

```
c_transfer = −ECBF(
    decay(c_herd(h_i, t_latest_i), λ, t_now − t_latest_i)
    for h_i in contradicted
)
```

Each contradicted hypothesis contributes one evidence piece: its latest stored `c_herd` at `t_latest_i`. The pieces are decayed individually to `t_now`, lifted to their uncertainty-maximized opinions (the lossless bijection; see System-Wide Scalar Representation), fused via ECBF, and mapped back; the fused result is negated to produce the transfer's confidence.

The transfer attestation is written to the ledger with:

```
oracle_id           = "_transfer"          (synthetic, not a real oracle)
timestamp           = t_now                (recorded inside the same transaction)
c_oracle_raw        = c_transfer
c_oracle_discounted = c_transfer           (no further discount: c_herd is post-pipeline)
t_oracle            = 1.0                  (full credibility: encodes the herd's prior)
c_herd              = c_transfer           (sole attestation at that point)
```

The transfer is written *before* the oracle's own attestation on h₂ within the same transaction. Insertion order plus `(timestamp, id)` ledger ordering guarantee the trust scan picks up the transfer row as `c_herd_prior` when scoring γ's subsequent attestation. The oracle's attestation at the same `t_now` then fuses against this non-vacuous prior rather than an empty slate.

### Why no source-level discount

`c_herd` is itself ECBF over `c_oracle_discounted` values. Each contributing row's `c_oracle_discounted = M_at_write · t_oracle · c_oracle_raw` already carries source maturity and trust factors. Transferring the negated `c_herd` is transferring an already-discounted prior; applying a second `M(h₁)` would double-discount the same quantity. The pipeline's source-level work has already been done at h₁'s write times.

### Latest row, not full re-fusion

The transfer reads `c_herd` from each contradicted hypothesis's most recent attestation row. Under the cumulative storage convention, every row's `c_herd` is the cumulative herd consensus at write time: a sufficient statistic for the herd's prior at `t_latest`. Decaying that scalar forward to `t_now` is an approximation: decay does not commute with fusion (two c = 0.6 rows one half-life apart, read a further half-life later: decayed-forward 0.329 vs canonical re-fusion 0.377; see Per-Attestation Decay over Post-Fusion Decay). The transfer accepts the gap with eyes open: its role is blocking self-referential trust credit on the novel's otherwise vacuous slate, not reproducing the canonical herd state.

### Partition completeness as proxy

The transfer assumes the herd's belief on `¬h₁` is a usable proxy for the herd's belief on `h₂`. That is correct under exhaustive binary partition `{h₁, ¬h₁}` and overstates otherwise: "the speed of light is 300,000 km/s" and "= 150,000 km/s" do not partition the value space; the truth could be neither. The complement operator (Def. 6.3) on a binomial opinion ω = (b, d, u) is ω̄ = (d, b, u) with base rate ā = 1 − a (Eq. 6.6); Lore's global a = 0.5 is that map's fixed point, so the triple form is exact here. The partition assumption is what makes the inversion match the herd's belief on h₂.

We accept the proxy with eyes open. The transfer's role is to block γ's self-referential trust credit on h₂'s otherwise vacuous slate; that role does not depend on partition exhaustivity. If h₂ later turns out compatible with the herd's broader belief space, further attestations on h₂ compound against the transfer and resolve the gap. If h₂ is genuinely contrary, the transfer correctly prices γ's attestation against the existing prior.

The conditional deduction operator (Ch. 9) would transfer more aggressively by propagating disbelief through conditional dependencies. We do not use it; full-complement transfer of the herd's already-discounted prior is the simplest honest move.

### Worked Example

Two oracles (α, β) attest h₁ = "the speed of light is 300,000 km/s" with c ≈ 0.9 each. The latest attestation row on h₁ stores `c_herd = +0.4` at `t_latest`. Oracle γ contributes h₂ = "the speed of light is 150,000 km/s" with confidence c = 0.8, contradicting h₁. Assume negligible decay since `t_latest`.

**Transfer computation:**

```
c_transfer = −c_herd(h₁, t_latest) = −0.4
```

**Ledger state on h₂ after the transaction:**

| row | oracle_id | timestamp | c_oracle_raw | c_oracle_discounted | c_herd |
|---|---|---|---|---|---|
| 1 | _transfer | t_now | −0.4 | −0.4 | −0.4 |
| 2 | γ | t_now | 0.8 | (discounted) | (fused) |

Row 2's c_herd reflects ECBF fusion of the transfer's negative evidence with γ's positive (discounted) attestation. The novel hypothesis does not start vacuous; it starts from the herd's contrary prior.

**Trust impact on γ:**

When γ's trust is computed, the trust scan examines γ's attestation on h₂ (row 2). The window finds the preceding row on h₂ (the transfer, row 1) and sets `c_herd_prior = −0.4`.

```
align_write = 1 − 0.5 · |c_oracle_raw − c_herd_prior|
            = 1 − 0.5 · |0.8 − (−0.4)|
            = 1 − 0.5 · 1.2
            = 0.4
```

Without the transfer, `c_herd_prior` would be 0.0 (vacuous), and `align_write = 1 − 0.5 · |0.8| = 0.6`. The transfer reveals that γ is asserting a position contrary to the herd's established view; γ's write-time alignment drops accordingly, preventing self-boost above base rate.

### Multi-Contradict Worked Example

If γ contradicts both h₁ (`c_herd = +0.4` at `t_latest_1`) and h₃ (`c_herd = +0.6` at `t_latest_3`), the evidence pieces fuse via ECBF before negation. Decayed to `t_now` (assume negligible decay), ECBF over `(+0.4, +0.6)` yields `c_fused`; the transfer is `−c_fused`. One transfer row lands on the novel.

If γ contradicts h₁ (`c_herd = +0.4`) and h₃ (`c_herd = −0.4`), the evidence pieces are balanced; ECBF returns near-vacuous; the transfer rounds to zero and **no transfer row is written**. γ's attestation on h₂ proceeds against a vacuous prior, which is correct, because the herd is not net-against h₂.

### Design Decisions

**Single transfer row, even for multi-contradict.** Multiple contradictions fuse at the transfer-evidence level via ECBF, not as separate ledger rows. One transaction produces at most one transfer attestation on the novel.

**Transfer at full credibility.** `t_oracle = 1.0` and `c_oracle_raw = c_oracle_discounted`. The transfer encodes the herd's already-discounted prior, not a fresh source claim; further trust discounting would re-discount the same quantity.

**Same-timestamp ordering.** The transfer and the oracle's attestation share `timestamp = t_now`. Ledger ordering by `(timestamp, id)` plus monotonic `id` ensure the transfer (inserted first) precedes the oracle's row. No `t_now − 1` fudge.

**Maturity inflation accepted.** The `_transfer` oracle counts as a distinct oracle in future `n_oracle_prior` computations on h₂. This inflates M from (say) 1/2 to 2/3 for the next attestation. The inflation is in the helpful direction: higher M_write means more weight on write-time alignment, where the oracle's position is measured against the transfer's negative prior. Adding `WHERE oracle_id != '_transfer'` to both backends' trust scans would be complexity for a minor refinement. KISS.

**Bounded double-counting.** When oracles from h₁ later attest directly on h₂, evidence from the original contradiction overlaps with the transfer. Decay naturally mitigates: the transfer attestation ages toward vacuous as fresh attestations dominate via ECBF. The overlap is bounded and self-correcting.

### Boundary Cases

**No contradicted hypotheses.** A pure orthogonal-novel writes no transfer row; the oracle's attestation lands on a vacuous slate, which is correct.

**Vacuous contradicted hypothesis** (latest `c_herd = 0.0`): `c_transfer = 0.0`. No transfer attestation is stored. The novel correctly starts vacuous: there is no herd state to transfer.

**Balanced multi-contradict** (ECBF over the contradicted priors fuses to ≈ 0): no transfer attestation is stored. The novel starts vacuous, correctly reflecting the absence of net contrary herd opinion.

**Near-zero transfer** (|c_transfer| < ε, where ε is `[epistemics] transfer_threshold`, default 1e-3): when the fused magnitude falls below the epistemic-significance floor, no transfer attestation is stored. The threshold is a deployment knob, deliberately decoupled from IEEE float noise.

### References

- Jøsang (2016) Def. 6.3: Complement of binomial opinions: ω̄ = (d, b, u), ā = 1 − a (Eq. 6.6); a = 0.5 is the base-rate fixed point. The inversion operator for binary propositions; the partition-completeness assumption underlying the transfer's proxy.
- Jøsang (2016) Def. 12.6: Epistemic cumulative belief fusion. Used to combine multiple contradicted priors at the transfer-evidence level.

---

## Conflict Metrics

Three metrics for comparing two opinions, drawn from Jøsang (2016) §4.8. Available as math operations, trivially derivable from `c_oracle_raw` and `c_herd_prior` (itself a LAG window over the ledger). Not stored on the attestation row.

### Projected Distance (PD)

Eq. 4.61, binomial case:

```
PD(ω_a, ω_b) = |P_a − P_b|
```

How far apart two opinions are in projected probability space. PD = 0 means identical projections; PD = 1 means maximally opposed (one dogmatically true, the other dogmatically false).

**Symmetry:** PD(a, b) = PD(b, a). ✓

### Conjunctive Certainty (CC)

Eq. 4.62:

```
CC(ω_a, ω_b) = (1 − u_a)(1 − u_b)
```

How certain both opinions are, jointly. CC = 1 when both are dogmatic; CC = 0 when either is vacuous. Conflict and agreement are only meaningful between opinions that have something to say.

**Symmetry:** CC(a, b) = CC(b, a). ✓

### Degree of Conflict (DC)

Def. 4.20 (Eq. 4.63):

```
DC(ω_a, ω_b) = PD · CC
```

High DC requires both disagreement (high PD) and conviction (high CC). Two vacuous opinions can't conflict; two identical dogmatic opinions can't conflict. DC captures the intuition that conflict requires both directional opposition and evidential weight.

---

## Propositional Decomposition

Composite hypotheses are decomposed into atomic propositions for embedding precision. This is a pre-processing step performed by the Interpreter (fast LLM), not a mathematical operator.

**Why decompose.** Embedding models average out meaning across a sentence. A composite claim like "Service X switched to gRPC in Q3 and latency dropped 40%" buries two distinct propositions in one vector. Atomic propositions produce sharper embeddings with higher information density, enabling more precise semantic retrieval.

**No structural links.** Atomic propositions enter the archive as independent hypotheses. No join table, no type column, no parent-child relationships. The epistemic state of each atomic proposition (its BDU from the ledger) already captures its significance. Structural links would add schema complexity without semantic benefit; the topology emerges from the Archivist's reasoning over the vector space, not from predetermined graph edges.

**Research validation:**
- Dense X Retrieval (Chen et al., EMNLP 2024): proposition-level retrieval outperforms passage and sentence level across five QA benchmarks.
- AFEV (2025): iterative LLM decomposition of complex claims into atomic facts; SOTA on five fact-checking benchmarks; marginal runtime cost.
- T2RAG (2025): graph-free atomic proposition retrieval outperforms GraphRAG by 11%, reduces retrieval cost by 45%.
- DecMetrics (2025): quality metrics for decomposition: completeness, correctness, semantic entropy.

---

## Prior Art & References

### Consultation Protocol

Every math implementation and test must be verified against prior art before committing:

1. **Jøsang (2016)**: *Subjective Logic*, Springer. Mathpix Markdown at `references/subjective-logic.md`.
   - Ch. 3: Binomial Opinions, §3.5.6 Uncertainty Maximization
   - Ch. 4: §4.8 Conflict Metrics (Def. 4.20 DC, Eqs. 4.61–4.63)
   - Ch. 12: Belief Fusion (Def. 12.5 ACBF, Def. 12.6 ECBF)
   - Ch. 6: Complement (Def. 6.3), used in Epistemic Transfer
   - Ch. 9: Conditional Deduction (harsher alternative to complement, not used)
   - Ch. 14: Trust Discounting (Def. 14.6), Multi-Edge Trust Paths (Def. 14.7, Eq. 14.13)
   - Cite by definition number.

2. **Jøsang & Ismail (2002)**: *The Beta Reputation System.* Mathpix Markdown at `references/beta-reputation-system.md`. Prior art for per-attestation decay (Eq. 12; order-based forgetting, not wall-clock ageing) and scalar confidence (Eq. 15).

3. **Reference implementation**: cross-check against `references/src/Aggregatio/` (Java, tum-i4). Cumulative fusion. Key: `SubjectiveOpinion.java`. Aggregatio's own cited source for the N-ary partitioning is Jøsang, Wang & Zhang, "Multi-source fusion in subjective logic" (FUSION 2017, DOI 10.23919/ICIF.2017.8009820).

4. **Edge cases**: verify vacuous, dogmatic, and both-dogmatic degenerate cases against at least one reference.

Neither programmer nor Claude trusts their own math alone.

### Canonical Citations

- Jøsang, A. (2016). *Subjective Logic: A Formalism for Reasoning Under Uncertainty.* Springer.
- Jøsang, A. & Ismail, R. (2002). *The Beta Reputation System.* Proc. 15th Bled eCommerce Conference. Prior art for per-attestation decay (Eq. 12; order-based forgetting, not wall-clock ageing) and scalar confidence (Eq. 15).
- Chen et al. (EMNLP 2024). *Dense X Retrieval: What Retrieval Granularity Should We Use?*
- AFEV (2025). *Fact in Fragments: Deconstructing Complex Claims via LLM-based Atomic Fact Extraction and Verification.*
- T2RAG (2025). *Beyond Chunks and Graphs: Retrieval-Augmented Generation through Triplet-Driven Thinking.*
- DecMetrics (2025). *Structured Claim Decomposition Scoring for Factually Consistent LLM Outputs.*

---

## Design Decisions

Key choices absorbed from the architectural decision record:

### Base Rate as System Constant

Opinion is `(b, d, u)`, not `(b, d, u, a)`. The base rate `a = 0.5` is a system constant (`BASE_RATE`), not a field on individual opinions. Carrying `a` on every opinion would suggest it could vary per-proposition when it structurally cannot. A per-opinion base rate creates a validation burden in fusion (must reject mismatched base rates) and propagation overhead in every operator. The simpler design makes invalid states unrepresentable.

**Generalization note:** the uncertainty maximization formula `ü = 2 · min(P, 1 − P)` is specific to `a = 0.5`. The general form is `ü = min(P/a, (1 − P)/(1 − a))`. If the base rate ever changes, uncertainty maximization must change with it.

### Adaptive w over Fixed w

The first version of oracle trust used a global configuration parameter `w` to blend write-time and read-time alignment: `align = w · align_write + (1 − w) · align_read`. This had two unaddressed failure modes:

1. **Prophet penalization.** On a fresh hypothesis, `c_herd_prior ≈ 0` (vacuous). An oracle who speaks first with high conviction is compared against nothing: a meaningless signal. A fixed `w > 0` still forces this meaningless alignment into the average, penalizing the prophet for being "far from the prior" when the prior was empty.
2. **Fixed knob, fixed tradeoff.** The deployer had to pick one point on the conformity-prophecy spectrum for all hypotheses, regardless of each hypothesis's maturity. A w tuned for mature hypotheses punished prophets; one tuned for prophets rewarded write-time noise on fresh hypotheses.

The fix is to make the blend adaptive per attestation, derived from the same maturity function already used by the discount operator: `M_write_i = N_O_i / (N_O_i + K)`. On fresh hypotheses, M_write = 0.5 (K = 1), so the write-time penalty against the vacuous prior is capped at half and the prophet's judgment leans on the eventual herd; K > 1 shifts the blend further toward read-time. On mature hypotheses, M_write → 1, so write-time dominates and the oracle is judged by the established consensus. No new parameter, no new operator; M is reused from hypothesis maturity, giving a single coherent meaning to "maturity" across the entire trust pipeline.

### Info Weighting over Flat Alignment

The original trust formula treated every alignment event identically: an oracle agreeing with a near-dogmatic herd earned the same credit as an oracle agreeing with an uncertain one. This opened a bandwagoning attack: an oracle who rubber-stamps settled hypotheses earns perfect alignment for zero informational contribution. Exactly zero is the dogmatic limit: `info = 1 − |c_herd_prior|` reaches 0 only at `|c_herd_prior| = 1`, which K >= 1 keeps out of reach in any herd the pipeline itself builds. A settled herd leaves a small residual info; the gate shrinks the credit rather than erasing it.

A first attempt used `info_i = 1 − |c_herd_prior_i|` as a *row weight* inside a conviction-weighted average: `t_oracle = Σ(align · conv · info · w) / Σ(conv · info · w)`. This was mathematically unsound. Because info appeared symmetrically in numerator and denominator, a pure bandwagoner (every `align_i = 1`) still earned `t_oracle = 1` regardless of info values; the weight cancelled. The defense only fired in the strict limit where every `info_i` was exactly zero (triggering a zero-denominator fallback), which never occurs for merely-settled herds in practice.

The principled move is to apply the binomial form of Jøsang's trust discounting operator (Def. 14.6) at the alignment-measurement level rather than the row-weight level. The derivation (and the proof that a pure bandwagoner on a fully dogmatic herd converges to `t_oracle = 0.5` by direct algebra) lives in The Informative-Commitment Gate under Oracle Trust. The key property is that applying Def. 14.6 to each row's alignment score makes info a *calibration* of the signal, not a weight on the row, so info cannot cancel out of the aggregate. A later revision composed conviction into the same discount (`signal = conviction · info`) after the scattershot vector showed that calibrating by info alone leaves low-conviction farming open; the same cancellation argument applies, and two Def. 14.6 discounts toward one base rate compose into the single product.

### Rejected Approaches

Three alternatives considered and rejected during the trust revision design:

- **Beta-Evidence Mapping (BRS-inspired).** Convert each attestation to a beta-distributed evidence pair, leave-one-out of the hypothesis state at read time, and score the oracle by the improvement in herd uncertainty. Rejected: leave-one-out is impossible *from the stored maximized aggregate*; uncertainty maximization is lossy, and a single scalar's one degree of freedom cannot determine the two the pre-maximization state carried, so `ECBF(history minus row i)` is not reconstructible from `ECBF(history)` and row i. Recomputation from the immutable ledger is available, and is exactly how the witness reference computes its others-only state; what it cannot rescue is this proposal's scoring rule. The bootstrap credit (the first attestation gets full credit for "introducing information") turns out to reward confidence bombing exactly the way the current system punishes it. And the claimed O(K) complexity argument relied on the aggregate-only LOO shortcut that doesn't exist.
- **Effective Distance (novel asymmetric operator).** Replace PD with an asymmetric distance function that rewards an oracle for moving the herd in the right direction. Rejected on two grounds: (1) no precedent in Jøsang or any reference implementation; adopting it would make effective-distance the only operator in `lore.math` without a citation, violating the prior-art protocol; (2) YAGNI: adaptive w already handles the prophet case on immature hypotheses, which is the concrete failure mode effective-distance was meant to address. If a case emerges where adaptive w is insufficient, adding a new distance function is a one-function local change with no storage impact, so the decision is cheaply reversible.
- **"Strict regime" (drop the PD 0.5 factor).** Use `align = 1 − |c_a − c_b|` instead of `1 − 0.5·|c_a − c_b|`, re-centering alignment on [−1, 1] rather than [0, 1]. Rejected as cosmetic: after the trivial linear rescaling the two formulations produce algebraically identical trust scores. No behavioral change, so the simpler canonical Jøsang form (PD = projected probability distance, Eq. 4.61) wins.

### Emergent Trust Grading over Zero Trust

Zero Trust (all oracles equal, no discounting) was the correct starting point: it eliminated a dependency chain of reputation, settlement events, and evidence-to-opinion mapping. But it has structural vulnerabilities: a single oracle submitting c = 1.0 introduces dogmatic belief and zero uncertainty, dominating until dozens of moderate oracles correct it. The system also cannot distinguish one oracle attesting 100 times from 100 oracles attesting once.

Emergent Trust Grading preserves Zero Trust's spirit (no oracle is privileged) while adding write-time discounting that values diversity and accuracy. The default tuning is more conservative than pure Zero Trust: first oracle at cold start gets P_effective = 0.25 (quarter strength). The system bootstraps out of skepticism through herd alignment and hypothesis diversity, not through any oracle being granted authority.

### ECBF over CCF

CCF (Def. 12.9) was initially considered because oracles share information sources and CCF handles dependent sources. But CCF's idempotency (fusing identical opinions is a no-op) meant hypotheses with strong agreement but moderate individual uncertainty could never converge. ECBF's non-idempotency fixes this: agreement compounds, driving uncertainty down. Uncertainty maximization provides the conservative epistemic correction.

### Per-Attestation Decay over Post-Fusion Decay

Post-fusion decay (applying decay to the already-fused hypothesis state) loses temporal resolution: it treats a hypothesis attested yesterday and one attested a year ago the same way. Per-attestation decay preserves the individual contribution timeline. BRS's recursive update (Eq. 13) is not the contrast here: it is exactly equivalent to the per-item form (Eq. 12) and discards nothing. Lore's reason is algebraic: opinion-space decay does not commute with fusion (two c = 0.6 attestations a half-life apart cannot be maintained as a single decaying aggregate that stays equal to re-fusing the individually decayed rows), so each attestation's age must be applied before every fusion. The immutable ledger makes this affordable.

### Propositional Decomposition without Structural Links

Structural links (parent-child, entrenchment) add schema complexity and coupling. The epistemic state of atomic propositions already captures their significance; a well-corroborated atomic premise naturally scores high, regardless of which composite hypothesis it came from. The signal exists in the ledger; graph edges would duplicate it.

### Scalar Confidence over BDU Input

The interface accepts a scalar c ∈ [-1, 1] instead of (b, d, u) triples. This eliminates three failure modes: invalid triples that don't sum to 1, LLM hallucination of inconsistent BDU values, and the normalization layer that was needed to clean them up. The scalar produces uncertainty-maximized opinions by construction; the interface currency and the ECBF output shape are aligned. BDU remains the internal representation.

### System-Wide Scalar Representation

The scalar `c ∈ [-1, 1]` is the universal epistemic representation outside `lore.math`. Oracle input (`c_oracle_raw`), discounted opinion (`c_oracle_discounted`), herd consensus (`c_herd`), and all ledger fields use scalars; BDU triples never cross the math module boundary.

**Lossless bijection.** For uncertainty-maximized opinions with `a = 0.5`, the mapping `c = b − d` is a bijection with `to_opinion(c)`. The proof: uncertainty-maximized opinions have `min(b, d) = 0`, so they sit on exactly two branches: `(c, 0, 1−c)` for `c ≥ 0` and `(0, |c|, 1−|c|)` for `c ≤ 0`. The scalar uniquely identifies the opinion; the roundtrip is exact.

**All stored opinions are uncertainty-maximized.** Oracle input is uncertainty-maximized by construction (the forward mapping). Trust discounting preserves uncertainty maximization (see Trust Discounting). ECBF output is uncertainty-maximized by definition (step 2). Decay preserves uncertainty maximization (it multiplies b and d by the same factor; if one starts at zero, it stays at zero). No pre-maximization intermediate is ever stored.

**Undogmatism as a pipeline property.** With K ≥ 1 (default), trust discounting guarantees P_effective < 1.0, which strictly reduces every oracle input toward vacuous. ECBF with non-dogmatic inputs cannot produce dogmatic outputs: the ACBF formula preserves u > 0 when all input uncertainties are positive, and uncertainty maximization preserves the projected probability. The system is structurally undogmatic not by input clamping but by the algebraic properties of the trust-discount-then-fuse pipeline. With K = 0, maturity is transparent and this guarantee does not hold: an explicit deployer opt-in (see Graceful Degradation).

---

## Security Analysis

Trust discounting, ECBF, and decay compose into a system with bounded vulnerability. This section catalogs known attack surfaces and their mitigations.

### Practical Trust Ceiling

The trust ceiling is not a number; it is a property of the oracle's targets. Each row's effective alignment is bounded above by `effective_align_i ≤ 0.5 + signal_i / 2 = 0.5 + conviction_i · info_i / 2` (substitute `align_i = 1` into Def. 14.6). The conviction-weighted average inherits the bound:

```
t_oracle ≤ 0.5 + 0.5 · ⟨signal⟩_cw
```

where `⟨signal⟩_cw = Σ(signal_i · conviction_i · weight_i) / Σ(conviction_i · weight_i)` is the conviction-weighted mean of the per-row signals `signal_i = conviction_i · info_i`. The ceiling is path-dependent; it tracks both the herd uncertainty the oracle volunteered to resolve and how much of their own credence they spent doing it:

- **Prophets** on fresh herds: `info = 1` makes the signal pure conviction, but the fresh-row blend caps alignment at `1 − 0.25 · conviction` (K = 1, write leg against a vacuous prior), so a perfectly vindicated fresh row tops out at `0.5 + 0.5c − 0.25c²`: monotone in conviction, at most **0.75** at `c = 1`. Nothing on fresh rows approaches 1.0; the ceiling rewards maximal conviction, not hedging.
- **Honest conformists** on mature fluid herds: `⟨conviction · info⟩ ≈ 0.35`, ceiling ≈ 0.65–0.70.
- **Bandwagoners** on settled herds: `info → 0`, ceiling → 0.5 at any conviction.

Reaching 1.0 requires `signal = 1` and `align = 1` on the same row: full conviction, a fully uncertain herd, and perfect agreement with where the witnesses land, and the maturity blend forbids the last two from coexisting at K ≥ 1. The trust metric is, at its core, a measure of how much uncertainty the oracle has committed to resolving. Climbing the ceiling requires not just being right, but being confidently right about things the herd was uncertain about.

### Trust Dynamics Clusters

Under the revised formula, oracles fall into recognizable clusters. The ranges are simulated, not asserted: Monte-Carlo archetype histories run through the implemented formula (4000 histories per archetype, 8-24 rows each, K = 1, decay disabled), reported as the 10th-90th percentile band. The simulation reproduces all four worked examples exactly before generating distributions, and each example value falls inside its archetype's band. The attack-analysis subsections below add detail on the adversarial cases.

**Provenance of this table.** The simulator was a one-off run against the formula as it stood, and it is not committed, so these bands cannot be regenerated or re-checked against a changed formula. Read them as a dated observation rather than a live property: the archetype ordering is pinned by tests in `tests/math/test_service.py`, the specific percentiles are not. Any change to the trust algebra invalidates the numbers here without failing anything, which is the cost of not having committed the simulator.

| Archetype | Target hypotheses | Simulated t_oracle (p10–p90) | Why |
|---|---|---|---|
| Prophet | Fresh herds, later vindicated | ~0.70–0.71 | info = 1: signal is pure conviction; the fresh-row cap is 0.75 |
| Honest conformist | Mature fluid herds | ~0.64–0.65 | Moderate signal, write-time anchored |
| Bandwagoner | Settled herds | ~0.53–0.54 | info → 0 collapses the signal at any conviction |
| Contrarian (mixed targets) | Fresh and moderate herds, against | ~0.47–0.50 | Committed disagreement, softened where info < 1 |
| Informative troll | Fresh herds, wrong | ~0.34–0.36 | High signal, full-strength penalty; floor 0.25 at \|c\| = 1 |
| Hedger (cold start) | Never commits | 0.50 exactly | Denominator fallback |
| Solo spammer | Unwitnessed novels | 0.50 exactly | Witness rule: no reference, no credit |
| Scattershot | Fresh witnessed herds, near-vacuous | ~0.55 | Calibration bound: within conviction of base rate |

The prophet's edge over the honest conformist (~0.70 vs ~0.64) is the calibration working as designed: committing hard where the herd knows nothing beats careful agreement where it knows plenty. The residual gap between the scattershot band and base rate is the fresh-row softness documented under Known Residuals.

### Time Axis: Trust Decay Tuning

The previous subsections cover the spatial axis: alignment, info, path dependence. The time axis is controlled by `[epistemics] trust_half_life` and has its own tradeoffs.

Without a trust-decay half-life, early contributors accrue permanent demigod status: their first high-info attestations never age out of the trust scan, and subsequent honest work can only add to, never erode, their historical record. Too-fast a half-life inverts the failure: a prophet's vindicating rows age out before the herd has had time to catch up, and the prophet is reduced to their aging write-time record against a vacuous prior.

Attestation half-life and trust half-life are independent knobs (`[epistemics] attestation_half_life` and `[epistemics] trust_half_life` in the config). An organization may want long-lived knowledge with fast-adapting trust (a research team whose facts endure but whose individual expertise shifts quickly) or the inverse, where knowledge turns over fast but track records are measured against years of history. The two rates are decoupled by design; pick them for the organization, not for each other.

### Confidence Bombing

A malicious oracle submits c = ±1.0 (or near it) on a fresh hypothesis, attempting to dominate the herd state.

**Mitigation:** Cold-start P_effective = M × t_oracle = 0.5 × 0.5 = 0.25. Even c = 1.0 becomes c_discounted = 0.25. The system absorbs the input at quarter strength. Subsequent honest oracles compound via ECBF, and the bomber's single extreme attestation decays over time. The attack is bounded, one-shot, and self-correcting.

### Echo Chamber Attack (Reputation Cashing)

An oracle builds a high trust score by consistently agreeing with the herd, then exploits that trust to push a bad claim. Reputation cashing is Lore's name for the attack; the underlying observation is BRS's, which describes reputation as an asset that "can be cashed in through a fraudulent transaction."

**Mitigation:** Two defenses compose. First, the informative-commitment gate makes trust-building through conformity much harder: agreeing with a near-dogmatic herd has `info ≈ 0`, so the attestation earns almost no trust credit regardless of how well it aligns. The attacker has to build reputation through *informative* agreement (attestations on hypotheses the herd was uncertain about), which is the honest path. Second, if the attacker does build genuine trust this way, the exploit itself is still bounded: the high-trust insincere attestation enters at elevated P_effective, but it is still a single opinion. Subsequent honest attestations compound against it via ECBF. The attacker's trust score drops immediately on the next trust computation (the insincere attestation diverges from the herd that corrected it; align_read drops). Trust decay ensures the damage window is finite. One bullet, one shot, diminishing damage.

### Bandwagoning

A weaker cousin of reputation cashing: an oracle attests only on already-settled hypotheses, rubber-stamping whatever the herd already believes, with no intent to exploit, just to farm a high t_oracle score for future ECBF leverage.

**Mitigation:** The informative-commitment gate structurally bounds this attack by direct algebra, not by fallback. Every bandwagon row has `info_i = 1 − |c_herd_prior_i| ≈ 0`, so its signal `conviction_i · info_i ≈ 0` and `effective_align_i = signal_i · align_i + (1 − signal_i) · 0.5` is pulled sharply toward 0.5; conviction cannot buy the signal back because it multiplies a near-zero factor. In the fully dogmatic limit (`info_i = 0` on every row), the formula collapses to `effective_align_i = 0.5` exactly (*independent of `align_i` and `conviction_i`*), so the bandwagoner's near-perfect alignment scores contribute nothing. The conviction-weighted average of 0.5s is 0.5: `t_oracle = Σ(0.5 · conv · w) / Σ(conv · w) = 0.5`. No limit, no fallback, no floating-point luck. Bandwagon farming cannot build trust above base rate. The attacker must attest on hypotheses with real uncertainty, which is the honest contribution the system is trying to reward.

### Self-Referential Read-Time Credit

An oracle floods the archive with solo novel hypotheses. Pre-fix, the trust scan's read-time reference was the hypothesis's latest stored `c_herd`, which on a solo hypothesis is the oracle's own row: `align_read ≈ 1` (the reference is the oracle's own discounted echo, `0.5 · t · c`), `info = 1` (fresh prior), and each write raised the trust that discounted the next. The feedback iterated to a fixed point `t* = (1 − 0.5c)/(1 − 0.125c)` at K = 1: ≈ 0.92 at c = 0.2, rising toward 1.0 as conviction shrinks (0.981 already at c = 0.05). Solo spam of low-conviction novels farmed near-maximal trust from an empty room.

**Mitigation:** Two mechanisms, one design. The read-time reference is recomputed at scan time from others-only evidence, so the oracle's own rows can never sit inside it; and the witness rule drops unwitnessed rows from the scan entirely, so an all-solo history earns exactly base rate (the witness theorem). The attack is not damped; it is worth nothing. Two escalations survive. Collusion, two identities attesting each other's novels to manufacture witnesses, is a sybil attack and lands on the authentication boundary below. The second stays inside a single identity and gets the next subsection.

### Transfer-Laundered Witness

The witness rule admits the synthetic `_transfer` oracle, and a transfer row's provenance can be the scored oracle alone. The play: attest a solo novel h₁ (unwitnessed, worth nothing by itself), then submit a novel h₂ contradicting h₁. The consolidated transfer onto h₂ negates h₁'s herd state, which is nothing but the oracle's own discounted echo, yet it lands under the `_transfer` identity: the oracle's h₂ row enters the scan aligned against a reference the oracle authored alone, two hops removed.

**Bound (hand-derived, not simulated; the archetype table stays simulation-only).** The laundered reference is `r = 0.5 · t · |c₁|`, capped by the oracle's own discount, and both blend legs of the h₂ row see it: `c_herd_prior` is the transfer, and the others-only recomputation fuses the transfer alone. With `info = 1 − r`, a laundered row at conviction c scores `effective_align = 1/2 + c · (1 − r)(1 + r − c) / 2`. The oracle's best conviction is `c* = (1 + r)/2`, giving `1/2 + (1 − r)(1 + r)²/8`, maximal at `r = 1/3`; the iterated fixed point is `t* = 1/2 + 4/27 ≈ 0.65`, which a single full-conviction solo row on h₁ approximately realizes (`r = 0.5 · t* ≈ 0.32`).

**Status: accepted residual, documented not closed.** The ceiling coincides with the top of the honest conformist band (~0.65) and stays well under the prophet's ~0.70; each countable row costs two junk hypotheses; and the ledger keeps the whole trail: chains of self-contradicting novels whose only witness is `_transfer` are a detectable signature. Repetition also fights retrieval, since near-duplicate pairs start resolving as paraphrases of earlier junk rather than fresh novels. The clean fix, requiring a non-`_transfer` witness before a row enters the scan, would also delay honest dissent scoring on contradicting novels until a real oracle engages the novel; that trade is left open. The witness theorem itself is unaffected: `_transfer` is formally another oracle. What this attack launders is provenance, which the algebra does not track.

### Low-Conviction Scattershot

An oracle sprays near-vacuous confidence (`c ≈ 0.1–0.2`) across many fresh, witnessed hypotheses. Pre-fix, calibration by info alone let these rows through at full signal: `info = 1` on fresh priors, and the fresh-row write leg is near-perfect against a vacuous prior while holding half the blend regardless of where the herd lands (`align ≥ 0.75 − 0.5 · conviction`, averaging ≈ 0.9), farming t ≈ 0.80 while asserting nearly nothing.

**Mitigation:** Conviction inside the calibration. Each row's distance from base rate is bounded by `|effective_align_i − 0.5| = signal_i · |align_i − 0.5| ≤ conviction_i · |align_i − 0.5|`: at c = 0.2 even a perfectly aligned row moves at most 0.1 from base rate, and a whole campaign of such rows averages within that band. Trust far from 0.5, in either direction, is purchasable only with conviction the oracle actually spent.

### Sybil Attack

An attacker creates multiple oracle identities to simulate false consensus.

**Mitigation:** Delegated to the authentication layer. The math cannot distinguish genuine diversity from manufactured diversity; maturity M increases with distinct oracle count regardless of whether the oracles are independent. OIDC-based authentication is the defense. Organizations choose their identity provider's resistance to sybil creation. This is an explicit architectural boundary: Lore's formalism assumes authenticated identity; the authentication layer provides it.

### Decay Exploitation

An attacker times attestations to exploit decay, submitting insincere attestations when older honest attestations have decayed toward vacuous.

**Mitigation:** This is intended behavior, not a vulnerability. Decay is a feature: knowledge that nobody re-encounters should lose influence. An active herd that re-attests important hypotheses naturally maintains their epistemic state. An attacker who waits for decay to weaken honest opinions is competing against any oracle who re-visits the hypothesis. The defense is a living herd, not a mathematical safeguard.

### K = 0 Deployment Mode

When K = 0: maturity is transparent (M = 1.0 for all N_O ≥ 1). With perfect alignment t_oracle = 1.0, P_effective = 1.0: every opinion retains its full strength. Dogmatic inputs (c = ±1.0) pass through trust discounting undiscounted and can produce dogmatic ECBF outputs.

**This is an explicit deployer opt-in.** K = 0 means "I am disabling the maturity safeguard on purpose." It removes the algebraic guarantee that prevents dogmatic opinions. Deployers who set K = 0 must accept that the system's undogmatic property depends entirely on oracles voluntarily submitting |c| < 1. K ≥ 1 (default) is the recommended deployment mode; the maturity saturation function is the binding undogmatic constraint alongside trust discounting.

### Known Residuals

Bounded weaknesses the current algebra accepts, documented rather than hidden:

- **Fresh-row softness.** A committed-and-wrong attestation on a fresh hypothesis can still land slightly above base rate: at `c = 0.5` fully contradicted by the eventual herd (`c_herd_now = −0.5`), the write leg against the vacuous prior grants `align_write = 0.75`, blending to `align = 0.625` and `effective_align = 0.5625`. The write leg grants `1 − 0.5 · conviction` regardless of outcome, and half of it survives the K = 1 blend; that unearned alignment is the price of not punishing prophets for being "far from nothing". K > 1 shrinks it by shifting fresh rows further toward read-time.
- **The caution tax.** An honest oracle who hedges is capped at `0.5 + conviction / 2` even under perfect vindication. Accepted deliberately: any calibration that exempts honest hedging also reopens the scattershot vector, since the algebra cannot distinguish humility from farming.
- **Transfer-laundered witness.** A single oracle can witness their own novel through the transfer machinery by contradicting their own solo claim; the laundered fixed point tops out at `t* = 1/2 + 4/27 ≈ 0.65` (see the attack subsection). Accepted for now: bounded, expensive in junk hypotheses, and detectable in the ledger.
- **Sparse-herd freeze.** Until oracles answer each other's hypotheses, every row is unwitnessed and every trust score idles at 0.5. A single-oracle or siloed deployment gets base-rate trust indefinitely; attestations still land (at quarter strength), so the archive functions while the trust signal waits for cross-engagement.
- **Correlated corroboration.** ECBF's inner ACBF accumulates evidence from agreeing sources whether or not they are independent; correlated agreement pushes the projected probability P exactly as independent agreement would. Uncertainty maximization (Eq. 3.27) preserves P while redistributing mass toward uncertainty, so it cannot deflate compounded agreement. The real bounds are distinct-oracle maturity M, trust discounting, and decay; tightly coupled herds should raise K.
- **Retroactivity.** t_oracle is recomputed from the ledger on every scan; nothing stores a live trust score. Changing the trust algebra therefore shifts every oracle's effective score at their next write. The per-row `t_oracle` values persisted on old attestations are historical record of what the discount was at write time, not current state, and are never rewritten.
- **The indexical present.** Two tacit conventions govern reference time: self-dated claims are temporal, undated present-tense claims are indexically about now. Examples and tests pin both directions; no prose states either. The indexical reading is load-bearing (supersession works by contradiction because standing claims stay about now), and its cost is real: genuine supersession of a standing claim fuses toward zero, and the register reads change as controversy. Accepted; no supersession machinery (tried and rejected).

