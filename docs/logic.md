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

- **b (belief):** evidence-weighted probability the proposition is true.
- **d (disbelief):** evidence-weighted probability the proposition is false.
- **u (uncertainty):** ignorance, lack of evidence either way.

**Invariant:** `b + d + u = 1.0`. Always. Every operator must preserve this.

**Base rate:** a global constant `a = 0.5` for all propositions. Not carried on individual opinions; it is a system parameter (see Design Decisions). The base rate represents the prior probability in the absence of any evidence.

**Projected probability:** `P = b + a · u`. When `u = 1` (vacuous): `P = a`. When `u = 0` (dogmatic): `P = b`.

**Special opinions:**
- **Vacuous:** `(0, 0, 1)`: complete ignorance. The neutral element for information content.
- **Dogmatic:** any opinion where `u = 0`: the observer claims certainty.

**Why Subjective Logic.** Lore needed a formalism that treats uncertainty as a first-class value, not as the absence of confidence. Weighted averaging conflates "I'm split 50/50" with "I don't know." Dempster-Shafer handles uncertainty but lacks built-in operators for fusion across multiple sources. Bayesian networks require global structure and conditional independence assumptions that don't fit an append-only, multi-oracle system. Subjective Logic provides the BDU tuple, commutative and associative fusion, and temporal decay, all as built-in, algebraically consistent operators.

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

**Proof of uncertainty maximization.** For c > 0: P = 0.5 + 0.5c. Uncertainty maximization (Eq. 3.27) yields ü = 2 · min(P, 1 − P). Since P ≥ 0.5, min(P, 1 − P) = 1 − P = 0.5 − 0.5c = (1 − c)/2, so ü = 1 − c. Then b̈ = P − 0.5ü = (0.5 + 0.5c) − 0.5(1 − c) = c, d̈ = 0. This matches the forward mapping. Symmetric for c < 0.

**Endpoints.** `c = ±1.0` produces dogmatic opinions `(1, 0, 0)` and `(0, 1, 0)`. These are valid in the mapping's mathematical domain. The trust pipeline prevents dogmatic opinions from reaching ECBF: trust discounting with P_effective < 1 (guaranteed when K ≥ 1) strictly reduces `|c|`, and ECBF with non-dogmatic inputs cannot produce dogmatic outputs. The undogmatic constraint is a pipeline property, not an input restriction. Values outside `[-1, 1]` are rejected: they produce invalid opinions (`b > 1` or `d > 1`).

### Inverse Mapping: Opinion → c

```
c = 2P − 1 = 2(b + 0.5u) − 1 = 2b + u − 1 = b − d
```

No clamping: the algebra guarantees `|c| < 1` for all ECBF outputs with non-dogmatic inputs.

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

**Mixed-dogmatic partition.** When some inputs are dogmatic and the rest are non-dogmatic, the equal-weight N-ary mean runs over the dogmatic subset only. The non-dogmatic minority is excluded; its ``u_B/(u_A + u_B)`` weight collapses to zero in the same limit that drives Case II. The implementation follows the tum-i4 Aggregatio reading of Eq. 12.15. Default Lore tuning keeps ``K ≥ 1``, which prevents dogmatic intermediates from ever reaching ECBF; the partition is the formal contract for ``K = 0`` deployments and any future code path that produces dogmatic discounted opinions.

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

CCF is idempotent: `fuse([a, a]) = a`. This means 50 oracles each submitting moderate uncertainty would never drive the fused uncertainty below that of any individual; the herd could never converge.

**ECBF solves two problems:**

1. **Agreement compounds.** 50 oracles corroborating a hypothesis drive uncertainty toward zero via ACBF's evidence accumulation. When P ≈ 0.99, uncertainty maximization yields ü ≈ 0.02: the herd converges.

2. **Contradiction cancels.** When evidence is evenly split (P ≈ 0.5), uncertainty maximization yields ü = 1.0, b = 0, d = 0: the vacuous opinion. The system returns to "we don't know" rather than claiming false certainty about a tie.

**Source correlation.** ECBF assumes source independence, which oracles in an organization aren't, strictly speaking. But uncertainty maximization is inherently conservative: it maximizes ignorance given the evidence. This is the correct stance for epistemic propositions where corroboration should accumulate evidence but the system shouldn't claim more certainty than warranted.

**Multiple attestations from the same oracle** compound via ACBF, with temporal decay as the natural correction: the older attestation decays toward vacuous over time, while the fresh one enters at full strength. No special "latest-per-oracle" logic is needed.

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

**Per-attestation, not post-fusion.** Each attestation decays individually by its own age before fusion. This follows Jøsang's canonical design (BRS 2002, Eq. 12) where individual evidence contributions are weighted by age before aggregation. Lore uses continuous elapsed time (`e^(−λΔt)`) where BRS uses discrete periods (`λ^(n−i)`). The principle is the same: fresh evidence naturally dominates stale evidence because old decayed attestations carry high uncertainty, contributing proportionally less to cumulative fusion.

**Invariant preservation:** `b₀ · e^(−λΔt) + d₀ · e^(−λΔt) + 1 − (1 − u₀) · e^(−λΔt) = (b₀ + d₀ + u₀) · e^(−λΔt) − e^(−λΔt) + 1`. Since `b₀ + d₀ + u₀ = 1`, this simplifies to `e^(−λΔt) − e^(−λΔt) + 1 = 1`. ✓

**Boundary cases:**
- Δt = 0: opinion unchanged (e⁰ = 1).
- Δt → ∞: opinion approaches vacuous (0, 0, 1).
- λ = 0: time-independent (no decay).

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

**Magnitude reduction.** |c_oracle_discounted| = |P_effective · c_oracle_raw| ≤ |c_oracle_raw|. Trust discounting never amplifies; it can only reduce magnitude. When K ≥ 1: P_effective < 1.0, so |c_oracle_discounted| < |c_oracle_raw| strictly. Even if the oracle submits c = ±1.0, the discounted value is strictly interior to (-1, 1).

**M and t_oracle have no underlying opinion structure.** They are scalars in [0, 1] interpreted as projected probabilities of implicit trust edges (following the structure of Def. 14.7 / Eq. 14.13). Trust revision operators (Jøsang §14.5) cannot apply to these edges; they are novel compositions, not direct instantiations of agent-to-agent referral trust.

### P_effective: Two-Edge Trust Path

The effective discount factor composes two independent trust signals:

```
P_effective = M · t_oracle
```

Where M is hypothesis maturity (see below) and t_oracle is oracle trust (see below). The operator that consumes P_effective is Def. 14.6 (the single-edge binomial trust discount with base rate a = 0.5), applied once per attestation (one source, one target). The path-form generalization (Def. 14.7 / Eq. 14.13) reduces to Def. 14.6 when the path has one edge; Lore's composition `M · t_oracle` is a product of two implicit edge probabilities collapsed into a single effective discount, so only the single-edge form is ever instantiated. The discount algebra depends only on P_effective, not on the internal structure of the trust edges.

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

`n_oracle_prior` is not stored on the attestation; it is derived from the ledger at write time and used to compute M. Storage is unnecessary; the ledger is the source of truth.

The same `N_O = n_oracle_prior + 1` rule is reused inside the oracle trust derivation (see Adaptive Blend below). Both call sites apply it relative to "the current oracle on the row being scored": at write time that is the new attestor, at trust-scan time it is the oracle whose trust we are computing. One rule, two roles.

---

## Oracle Trust

The oracle's alignment probability t_oracle ∈ [0, 1], computed from the immutable ledger at write time. No peer assessment, no settlement events. A bounded scan of the oracle's recent attestation history. The derivation is built entirely from standard Subjective Logic operators (PD (Eq. 4.61) for alignment, the maturity saturation M for the adaptive blend, and Def. 14.6 (binomial trust discounting) for information weighting), composed into a conviction-weighted average. No novel operators.

### The Principle

Two questions, not one:

1. **Did the oracle's opinion match the herd?** The alignment signal.
2. **Was there any uncertainty for the oracle's opinion to resolve?** The information signal.

Trust accrues only when both conditions hold: the oracle had something to say (conviction), and the herd needed to hear it (information). Agreement with a near-dogmatic herd resolves no uncertainty and earns no credit. Saying nothing on a fresh hypothesis earns no credit. Both are informationally empty events.

### Alignment (PD-Based, Unchanged)

For each past attestation by oracle X on some hypothesis P_i:

```
c_herd_prior_i = c_herd from the preceding attestation on P_i (0.0 if first)
c_herd_now_i   = c_herd from the latest attestation on P_i (by any oracle, including X)

align_write_i = 1 − 0.5 · |c_oracle_raw_i − c_herd_prior_i|        (1 − PD, Eq. 4.61)
align_read_i  = 1 − 0.5 · |c_oracle_raw_i − c_herd_now_i|          (1 − PD, Eq. 4.61)
```

Both signals are binomial specializations of PD: since `P = 0.5 + 0.5c`, we have `|P_a − P_b| = 0.5 · |c_a − c_b|`. Alignment is `1 − PD`, in (0, 1] for any inputs in (−1, 1).

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
| Fresh hypothesis, oracle X is first attester | 0 | 0.50 | 50/50: read-time dominates over vacuous prior |
| Second distinct oracle | 1 | 0.67 | Read-time still weighty (prophet-friendly) |
| Fifth distinct oracle | 4 | 0.83 | Write-time dominates (conformity-rewarding) |
| Tenth+ distinct oracle | 9 | 0.91 | Write-time strongly dominates |

Fresh hypothesis: read-time dominates → the prophet is judged by the eventual herd, not by the vacuous prior they had no reason to agree with. Mature hypothesis: write-time dominates → the oracle is judged by the herd's state when they spoke, which has become an informative reference.

**K = 0 degeneracy.** When K = 0: M_write = 1.0 for all N_O ≥ 1, so `align_i = align_write_i`, pure write-time. This is consistent with the discount operator's K = 0 behavior ("maturity transparent"): both the discount and the trust blend become transparent together. An explicit deployer opt-in.

### Information Weighting (Anti-Bandwagoning)

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

Rather than using `info` as a row weight (which cancels symmetrically in a weighted average and fails to bound bandwagoning), we apply the binomial form of Jøsang's trust discounting operator (Def. 14.6) to each row's alignment score, with `info_i` as the discount factor and base rate `a = 0.5`:

```
effective_align_i = info_i · align_i + (1 − info_i) · 0.5
```

This is the projected-probability expression derived in the Trust Discounting section above. Substituting `P_eff = info_i` and `P_source = align_i`:

```
P' = P_eff · P_source + 0.5 · (1 − P_eff) = info · align + 0.5 · (1 − info)
```

**The algebraic form is canonical Def. 14.6.** The semantic choice (using herd uncertainty at write time as a discount on an alignment measurement, rather than as trust in an information source) is a novel application of the operator, in the same spirit as the composition `P_effective = M · t_oracle` used in the main trust pipeline (which composes non-opinion scalars into a Def. 14.7-style product). Neither introduces a new operator; both reuse standard SL operators with reinterpreted inputs.

**Properties:**

| info | align | effective_align | Scenario |
|---|---|---|---|
| 1.00 | 1.00 | 1.000 | Perfect alignment on fully-uncertain herd: full credit |
| 1.00 | 0.00 | 0.000 | Perfect misalignment on fully-uncertain herd: full penalty |
| 0.50 | 1.00 | 0.750 | Bandwagon on half-formed herd: partial credit |
| 0.50 | 0.00 | 0.250 | Contrarian on half-formed herd: partial penalty |
| 0.00 | 1.00 | 0.500 | Bandwagon on dogmatic herd: neutral (no credit, no penalty) |
| 0.00 | 0.00 | 0.500 | Contrarian on dogmatic herd: neutral (cannot tell crank from prophet) |

The asymmetry is deliberate: *informative* wrongness (info=1, align=0) still gets punished to 0; *uninformative* wrongness gets neutralized to 0.5. An oracle disagreeing with a dogmatic herd is epistemically indistinguishable from a prophet the herd cannot move to meet; we default to neutral until fresh evidence arrives.

### Conviction Weighting (Preserved)

```
conviction_i = |c_oracle_raw_i|
```

For uncertainty-maximized oracle inputs, `conviction = 1 − u_oracle`. Conviction weights each row's contribution to the aggregate: "did the oracle commit to an opinion strongly enough for this row to count?"

**The informative-commitment principle.** Conviction and information do orthogonal jobs:
- **Conviction** is a row weight: how much this attestation matters in the aggregate trust score.
- **Info** is a signal calibration: how reliable the alignment measurement is as evidence about the oracle's judgment.

Trust credit requires both to be non-trivial. An oracle must have expressed conviction (conviction > 0) for the row to count at all, and the herd must have been uncertain (info > 0) for the alignment signal to escape the pull toward 0.5. These are different axes (one gates row weight, the other gates signal strength), so there is no double-count. Structurally the motivation mirrors Jøsang's conjunctive certainty CC (Eq. 4.62), which requires "both speakers to have something to say"; here we require "the speaker had something to say and the audience could benefit from hearing it."

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

**Empty-denominator fallback:** when `Σ(conviction_i · weight_i) = 0`, fall back to `t_oracle = 0.5`. Since every `weight_i > 0`, the denominator is zero exactly when every `conviction_i = 0`: an all-vacuous history. The oracle has never expressed a committed opinion. This is informationally identical to cold start, and the fallback treats them identically.

**The all-bandwagon case needs no fallback.** It is handled non-asymptotically by the numerator. *Theorem:* if every `info_i = 0` and at least one `conviction_i > 0`, then

```
effective_align_i = 0 · align_i + 1 · 0.5 = 0.5    for every row
t_oracle = Σ(0.5 · c_i · w_i) / Σ(c_i · w_i) = 0.5
```

exactly. A purely dogmatic bandwagon history produces trust equal to base rate, by direct algebra, not by limit, not by fallback, not by floating-point luck. Bandwagon farming cannot build trust above base rate.

**Defensive clamp:** the algebra guarantees `t_oracle ∈ [0, 1]` for any non-empty denominator. A `max(0.0, min(1.0, ...))` clamp is retained as an IEEE 754 safety net against floating-point drift, not a semantic correction.

**Deterministic accumulation.** The numerator and denominator sums use `math.fsum`, which is Shewchuk-exact and order-independent. The ledger stores `t_oracle`, so deterministic accumulation matters more than raw precision: bit-stable trust across row reorderings means the value persisted on the attestation row does not depend on the iteration order of the trust scan.

**No prior row, no signal.** When the trust scan returns a row that is the oracle's first attestation on its hypothesis, `c_herd_prior` defaults to `0.0`. This is algebraically equivalent to a stored `c_herd_prior = 0.0`: both produce `info = 1 - |c_herd_prior| = 1`, so the alignment signal passes through undamped, exactly what the Prophet archetype requires. Detecting fresh hypotheses elsewhere in the pipeline relies on `n_oracle_prior` (the distinct-oracle count) rather than `c_herd_prior` because the latter cannot distinguish an empty herd from a balanced one.

**Bounds.** Decay is the soft bound (old attestations contribute negligible weight). The hard bound is `5 × trust_half_life`: at five half-lives, residual weight is ≈3%. Beyond that is noise.

**Path-dependent trust.** Trust at write time is computed from the *previous* state: lagged trust, the same approach as PageRank and BRS. This is inherent to any system where trust affects fusion and fusion affects trust. The lag ensures each write sees a consistent snapshot, and over time the system converges as fresh attestations dominate stale ones via decay.

### Worked Examples

Four archetypes, each computed against the same seeded ledger. To keep the arithmetic transparent: K = 1, trust decay disabled (weight = 1 for every row), and `c_herd_now` taken as the ledger's current state after all seeded attestations.

All values rounded to three decimals.

#### Example 1: The Prophet

Oracle P speaks first on a fresh hypothesis H with `c = 0.8`. Three later oracles attest on H in sequence, converging the herd toward strong belief.

| Event | oracle | c_oracle_raw | c_herd_prior | c_herd_after |
|---|---|---|---|---|
| 1 | P | 0.8 | 0.00 | 0.20 |
| 2 | A | 0.7 | 0.20 | 0.35 |
| 3 | B | 0.8 | 0.35 | 0.50 |
| 4 | C | 0.75 | 0.50 | 0.60 |

(Herd values are illustrative post-ECBF-and-decay scalars; exact ECBF output is not needed for the trust computation; only the ledger-recorded `c_herd` values are.)

At the moment we compute P's trust, P has exactly one historical attestation (row 1). n_oracle_prior for row 1 = 0 (P was first), so N_O = 1 and M_write = 1/(1+1) = 0.500.

- `align_write      = 1 − 0.5 · |0.8 − 0.00| = 1 − 0.400 = 0.600`
- `align_read       = 1 − 0.5 · |0.8 − 0.60| = 1 − 0.100 = 0.900`
- `align            = 0.500 · 0.600 + 0.500 · 0.900 = 0.300 + 0.450 = 0.750`
- `conviction       = |0.8| = 0.800`
- `info             = 1 − |0.00| = 1.000`
- `effective_align  = 1.000 · 0.750 + 0.000 · 0.5 = 0.750`
- numerator: `0.750 · 0.800 · 1 = 0.600`
- denominator: `0.800 · 1 = 0.800`
- `t_oracle = 0.600 / 0.800 = 0.750`

Because the herd was fully uncertain when P spoke (`info = 1`), no pull toward 0.5 applies: `effective_align = align`. The prophet is judged on their actual alignment, which adaptive w has already shifted toward read-time (the herd's eventual position). Had we used the old fixed-w formula with w = 0.5, the same row would have produced `align = 0.5 · 0.600 + 0.5 · 0.900 = 0.750`, numerically identical here. Adaptive w matters most when the prophet has *multiple* attestations on fresh hypotheses: every one gets M_write ≈ 0.5 and defers to read-time, protecting the prophet from write-time penalties on vacuous priors. A fixed w = 0.8 would have produced `align = 0.8·0.600 + 0.2·0.900 = 0.480 + 0.180 = 0.660`: the prophet would be penalized 12 points for being "far from nothing."

#### Example 2: The Bandwagoner

Oracle B only ever attests on hypotheses the herd has already settled. Take a single representative row (the rest of the history would contribute similar values) against a near-dogmatic herd:

| hypothesis | c_oracle_raw | n_oracle_prior | c_herd_prior | c_herd_now |
|---|---|---|---|---|
| H1 (settled, pro) | 0.90 | 4 | 0.92 | 0.94 |

N_O = 5, M_write = 5/6 ≈ 0.833: write-time dominates. Alignment is near-perfect (the bandwagoner is matching the herd by design).

- `align_write      = 1 − 0.5 · |0.90 − 0.92| = 0.990`
- `align_read       = 1 − 0.5 · |0.90 − 0.94| = 0.980`
- `align            = 0.833 · 0.990 + 0.167 · 0.980 = 0.988`
- `info             = 1 − 0.92 = 0.080`
- `effective_align  = 0.080 · 0.988 + 0.920 · 0.5 = 0.0791 + 0.4600 = 0.5391`
- `conviction       = 0.900`

The alignment is 0.988, almost perfect. But `info = 0.08` collapses it: `effective_align ≈ 0.539`, barely above base rate. Because every row of a bandwagon history looks like this, the conviction-weighted average over such rows stays pinned just above 0.5. The asymptotic case is covered by the theorem in Conviction Weighting (Preserved): when the herd is fully dogmatic on every row (`info_i = 0`), each `effective_align_i = 0.5` exactly, so `t_oracle = 0.5` by direct algebra, no fallback, no limit. Bandwagon farming cannot build trust above base rate.

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
- `effective_align  = 1.000 · 0.525 + 0.000 · 0.5 = 0.525`
- `conviction       = 0.800`
- num: `0.525 · 0.800 = 0.420`
- den: `0.800`

Row 2: N_O = 3, M_write = 3/4 = 0.750.
- `align_write      = 1 − 0.5 · |−0.7 − 0.50| = 1 − 0.600 = 0.400`
- `align_read       = 1 − 0.5 · |−0.7 − 0.55| = 1 − 0.625 = 0.375`
- `align            = 0.750 · 0.400 + 0.250 · 0.375 = 0.300 + 0.09375 = 0.39375`
- `info             = 1 − 0.50 = 0.500`
- `effective_align  = 0.500 · 0.39375 + 0.500 · 0.5 = 0.1969 + 0.2500 = 0.4469`
- `conviction       = 0.700`
- num: `0.4469 · 0.700 = 0.3128`
- den: `0.700`

Totals:
- numerator ≈ `0.420 + 0.3128 = 0.7328`
- denominator ≈ `0.800 + 0.700 = 1.500`
- `t_oracle ≈ 0.7328 / 1.500 ≈ 0.489`

Contrarian earns trust close to base rate, slightly below 0.5. Row 1 is on a fresh hypothesis (`info = 1`) so the pull toward 0.5 does nothing and the genuine disagreement signal passes through. Row 2 is on a moderately-formed hypothesis (`info = 0.5`), so its disagreement is softened: `effective_align = 0.447` rather than `0.394`. The contrarian's honest disagreements on informative hypotheses still pull trust down; disagreements on less-informative hypotheses are discounted.

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
- `effective_align  = 0.600 · 0.9125 + 0.400 · 0.5 = 0.5475 + 0.2000 = 0.7475`
- `conviction       = 0.600`
- num: `0.7475 · 0.600 = 0.4485`
- den: `0.600`

Row 2: N_O = 10, M_write = 10/11 ≈ 0.909.
- `align_write      = 1 − 0.5 · |0.50 − 0.30| = 1 − 0.100 = 0.900`
- `align_read       = 1 − 0.5 · |0.50 − 0.45| = 1 − 0.025 = 0.975`
- `align            = 0.909 · 0.900 + 0.091 · 0.975 = 0.8182 + 0.0886 = 0.9068`
- `info             = 1 − 0.30 = 0.700`
- `effective_align  = 0.700 · 0.9068 + 0.300 · 0.5 = 0.6348 + 0.1500 = 0.7848`
- `conviction       = 0.500`
- num: `0.7848 · 0.500 = 0.3924`
- den: `0.500`

Totals:
- numerator ≈ `0.4485 + 0.3924 = 0.8409`
- denominator ≈ `0.600 + 0.500 = 1.100`
- `t_oracle ≈ 0.8409 / 1.100 ≈ 0.7644`

The honest conformist earns solid trust around 0.77, well above base rate but meaningfully below 1.0. The cap is structural: each row's `effective_align` is bounded above by `0.5 + info/2` (since `align ∈ [0, 1]`), so reaching 1.0 requires *both* perfect alignment *and* near-fully-uncertain herds. An oracle who only attests on maturely-formed hypotheses is capped around the typical `info` of their targets. Climbing higher requires riskier contributions on less-formed hypotheses, where agreement actually resolves uncertainty: exactly the informational commitment the formula is designed to reward.

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

The transfer assumes the herd's belief on `¬h₁` is a usable proxy for the herd's belief on `h₂`. That is correct under exhaustive binary partition `{h₁, ¬h₁}` and overstates otherwise: "the speed of light is 300,000 km/s" and "= 150,000 km/s" do not partition the value space; the truth could be neither. The complement operator (Def. 6.3) on a binomial opinion ω = (b, d, u) is ω̄ = (d, b, u); the partition assumption is what makes that inversion match the herd's belief on h₂.

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

**Near-zero transfer** (|c_transfer| < ε): when the transfer magnitude rounds to zero, no transfer attestation is stored.

### References

- Jøsang (2016) Def. 6.3: Complement of binomial opinions: ω̄ = (d, b, u). The inversion operator for binary propositions; the partition-completeness assumption underlying the transfer's proxy.
- Jøsang (2016) Def. 12.6: Cumulative belief fusion. Used to combine multiple contradicted priors at the transfer-evidence level.

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

2. **Jøsang & Ismail (2002)**: *The Beta Reputation System.* Mathpix Markdown at `references/beta-reputation-system.md`. Foundation for per-attestation decay (Eq. 12) and scalar confidence prior art (Eq. 15).

3. **Reference implementation**: cross-check against `references/src/Aggregatio/` (Java, tum-i4). Cumulative fusion. Key: `SubjectiveOpinion.java`.

4. **Edge cases**: verify vacuous, dogmatic, and both-dogmatic degenerate cases against at least one reference.

Neither programmer nor Claude trusts their own math alone.

### Canonical Citations

- Jøsang, A. (2016). *Subjective Logic: A Formalism for Reasoning Under Uncertainty.* Springer.
- Jøsang, A. & Ismail, R. (2002). *The Beta Reputation System.* Proc. 15th Bled eCommerce Conference. Foundation for per-attestation decay (Eq. 12) and scalar confidence prior art (Eq. 15).
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

The fix is to make the blend adaptive per attestation, derived from the same maturity function already used by the discount operator: `M_write_i = N_O_i / (N_O_i + K)`. On fresh hypotheses, M_write ≈ 0.5 (K = 1), so read-time dominates and the prophet is judged by the eventual herd. On mature hypotheses, M_write → 1, so write-time dominates and the oracle is judged by the established consensus. No new parameter, no new operator; M is reused from hypothesis maturity, giving a single coherent meaning to "maturity" across the entire trust pipeline.

### Info Weighting over Flat Alignment

The original trust formula treated every alignment event identically: an oracle agreeing with a near-dogmatic herd earned the same credit as an oracle agreeing with an uncertain one. This opened a bandwagoning attack: an oracle who rubber-stamps settled hypotheses earns perfect alignment for zero informational contribution.

A first attempt used `info_i = 1 − |c_herd_prior_i|` as a *row weight* inside a conviction-weighted average: `t_oracle = Σ(align · conv · info · w) / Σ(conv · info · w)`. This was mathematically unsound. Because info appeared symmetrically in numerator and denominator, a pure bandwagoner (every `align_i = 1`) still earned `t_oracle = 1` regardless of info values; the weight cancelled. The defense only fired in the strict limit where every `info_i` was exactly zero (triggering a zero-denominator fallback), which never occurs for merely-settled herds in practice.

The principled move is to apply the binomial form of Jøsang's trust discounting operator (Def. 14.6) at the alignment-measurement level rather than the row-weight level. The derivation (and the proof that a pure bandwagoner on a fully dogmatic herd converges to `t_oracle = 0.5` by direct algebra) lives in the Information Weighting subsection under Oracle Trust. The key property is that applying Def. 14.6 to each row's alignment score makes info a *calibration* of the signal, not a weight on the row, so info cannot cancel out of the aggregate.

### Rejected Approaches

Three alternatives considered and rejected during the trust revision design:

- **Beta-Evidence Mapping (BRS-inspired).** Convert each attestation to a beta-distributed evidence pair, leave-one-out of the hypothesis state at read time, and score the oracle by the improvement in herd uncertainty. Rejected: leave-one-out on ECBF is impossible because uncertainty maximization is lossy; you cannot reconstruct `ECBF(history minus row i)` from `ECBF(history)` and row i. The bootstrap credit (the first attestation gets full credit for "introducing information") turns out to reward confidence bombing exactly the way the current system punishes it. And the claimed O(K) complexity argument relied on the LOO shortcut that doesn't exist.
- **Effective Distance (novel asymmetric operator).** Replace PD with an asymmetric distance function that rewards an oracle for moving the herd in the right direction. Rejected on two grounds: (1) no precedent in Jøsang or any reference implementation; adopting it would make effective-distance the only operator in `lore.math` without a citation, violating the prior-art protocol; (2) YAGNI: adaptive w already handles the prophet case on immature hypotheses, which is the concrete failure mode effective-distance was meant to address. If a case emerges where adaptive w is insufficient, adding a new distance function is a one-function local change with no storage impact, so the decision is cheaply reversible.
- **"Strict regime" (drop the PD 0.5 factor).** Use `align = 1 − |c_a − c_b|` instead of `1 − 0.5·|c_a − c_b|`, re-centering alignment on [−1, 1] rather than [0, 1]. Rejected as cosmetic: after the trivial linear rescaling the two formulations produce algebraically identical trust scores. No behavioral change, so the simpler canonical Jøsang form (PD = projected probability distance, Eq. 4.61) wins.

### Emergent Trust Grading over Zero Trust

Zero Trust (all oracles equal, no discounting) was the correct starting point: it eliminated a dependency chain of reputation, settlement events, and evidence-to-opinion mapping. But it has structural vulnerabilities: a single oracle submitting c = 1.0 introduces dogmatic belief and zero uncertainty, dominating until dozens of moderate oracles correct it. The system also cannot distinguish one oracle attesting 100 times from 100 oracles attesting once.

Emergent Trust Grading preserves Zero Trust's spirit (no oracle is privileged) while adding write-time discounting that values diversity and accuracy. The default tuning is more conservative than pure Zero Trust: first oracle at cold start gets P_effective = 0.25 (quarter strength). The system bootstraps out of skepticism through herd alignment and hypothesis diversity, not through any oracle being granted authority.

### ECBF over CCF

CCF (Def. 12.9) was initially considered because oracles share information sources and CCF handles dependent sources. But CCF's idempotency (fusing identical opinions is a no-op) meant hypotheses with strong agreement but moderate individual uncertainty could never converge. ECBF's non-idempotency fixes this: agreement compounds, driving uncertainty down. Uncertainty maximization provides the conservative epistemic correction.

### Per-Attestation Decay over Post-Fusion Decay

Post-fusion decay (applying decay to the already-fused hypothesis state) loses temporal resolution: it treats a hypothesis attested yesterday and one attested a year ago the same way. Per-attestation decay preserves the individual contribution timeline. The immutable ledger makes this possible; no need for the recursive shortcut that discards history.

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

The trust ceiling is not a number; it is a property of the oracle's targets. Each row's effective alignment is bounded above by `effective_align_i ≤ 0.5 + info_i / 2` (substitute `align_i = 1` into Def. 14.6). The conviction-weighted average inherits the bound:

```
t_oracle ≤ 0.5 + 0.5 · ⟨info⟩_cw
```

where `⟨info⟩_cw = Σ(info_i · conviction_i · weight_i) / Σ(conviction_i · weight_i)` is the conviction-weighted mean of the per-row info values. The ceiling is path-dependent; it tracks the herd uncertainty the oracle has volunteered to try to resolve:

- **Prophets** on fresh herds: `⟨info⟩ → 1`, ceiling → 1.0 (approached but not reached in practice; `align_read` is rarely perfect).
- **Honest conformists** on mature fluid herds: `⟨info⟩ ≈ 0.5–0.7`, ceiling ≈ 0.75–0.85.
- **Bandwagoners** on settled herds: `⟨info⟩ → 0`, ceiling → 0.5.

The trust metric is, at its core, a measure of how much uncertainty the oracle has agreed to try to resolve. Climbing the ceiling requires not just being right, but being right about things the herd was uncertain about.

### Trust Dynamics Clusters

Under the revised formula, oracles fall into recognizable clusters. The table summarizes where each archetype tends to settle and why; the attack-analysis subsections below add detail on the adversarial cases.

| Archetype | Target hypotheses | Expected t_oracle | Why |
|---|---|---|---|
| Prophet | Fresh herds, later vindicated | ~0.85–1.0 | High ⟨info⟩, read-time dominates |
| Honest conformist | Mature fluid herds | ~0.75–0.85 | Moderate ⟨info⟩, write-time dominates |
| Bandwagoner | Settled herds | ~0.50–0.55 | info → 0 neuters alignment signal |
| Contrarian (dogmatic herd) | Settled herds | ~0.45–0.50 | info → 0 neutralizes disagreement |
| Informative troll | Fresh herds, wrong | ~0.10–0.30 | High ⟨info⟩, full-strength penalty |
| Hedger (cold start) | Never commits | ~0.50 | Denominator fallback |

### Time Axis: Trust Decay Tuning

The previous subsections cover the spatial axis: alignment, info, path dependence. The time axis is controlled by `[epistemics] trust_half_life` and has its own tradeoffs.

Without a trust-decay half-life, early contributors accrue permanent demigod status: their first high-info attestations never age out of the trust scan, and subsequent honest work can only add to, never erode, their historical record. Too-fast a half-life inverts the failure: a prophet's vindicating rows age out before the herd has had time to catch up, and the prophet is reduced to their aging write-time record against a vacuous prior.

Attestation half-life and trust half-life are independent knobs (`[epistemics] attestation_half_life` and `[epistemics] trust_half_life` in the config). An organization may want long-lived knowledge with fast-adapting trust (a research team whose facts endure but whose individual expertise shifts quickly) or the inverse, where knowledge turns over fast but track records are measured against years of history. The two rates are decoupled by design; pick them for the organization, not for each other.

### Confidence Bombing

A malicious oracle submits c = ±1.0 (or near it) on a fresh hypothesis, attempting to dominate the herd state.

**Mitigation:** Cold-start P_effective = M × t_oracle = 0.5 × 0.5 = 0.25. Even c = 1.0 becomes c_discounted = 0.25. The system absorbs the input at quarter strength. Subsequent honest oracles compound via ECBF, and the bomber's single extreme attestation decays over time. The attack is bounded, one-shot, and self-correcting.

### Echo Chamber Attack (Reputation Cashing)

An oracle builds a high trust score by consistently agreeing with the herd, then exploits that trust to push a false opinion (what BRS 2002 calls "reputation cashing").

**Mitigation:** Two defenses compose. First, info weighting makes trust-building through conformity much harder: agreeing with a near-dogmatic herd has `info ≈ 0`, so the attestation earns almost no trust credit regardless of how well it aligns. The attacker has to build reputation through *informative* agreement (attestations on hypotheses the herd was uncertain about), which is the honest path. Second, if the attacker does build genuine trust this way, the exploit itself is still bounded: the high-trust false attestation enters at elevated P_effective, but it is still a single opinion. Subsequent honest attestations compound against it via ECBF. The attacker's trust score drops immediately on the next trust computation (the false opinion diverges from the herd that corrected it; align_read drops). Trust decay ensures the damage window is finite. One bullet, one shot, diminishing damage.

### Bandwagoning

A weaker cousin of reputation cashing: an oracle attests only on already-settled hypotheses, rubber-stamping whatever the herd already believes, with no intent to exploit, just to farm a high t_oracle score for future ECBF leverage.

**Mitigation:** Info weighting structurally bounds this attack by direct algebra, not by fallback. Every bandwagon row has `info_i = 1 − |c_herd_prior_i| ≈ 0`, so its `effective_align_i = info_i · align_i + (1 − info_i) · 0.5` is pulled sharply toward 0.5. In the fully dogmatic limit (`info_i = 0` on every row), the formula collapses to `effective_align_i = 0 · align_i + 1 · 0.5 = 0.5` exactly (*independent of `align_i`*), so the bandwagoner's near-perfect alignment scores contribute nothing. The conviction-weighted average of 0.5s is 0.5: `t_oracle = Σ(0.5 · conv · w) / Σ(conv · w) = 0.5`. No limit, no fallback, no floating-point luck. Bandwagon farming cannot build trust above base rate. The attacker must attest on hypotheses with real uncertainty, which is the honest contribution the system is trying to reward.

### Sybil Attack

An attacker creates multiple oracle identities to simulate false consensus.

**Mitigation:** Delegated to the authentication layer. The math cannot distinguish genuine diversity from manufactured diversity; maturity M increases with distinct oracle count regardless of whether the oracles are independent. OIDC-based authentication is the defense. Organizations choose their identity provider's resistance to sybil creation. This is an explicit architectural boundary: Lore's formalism assumes authenticated identity; the authentication layer provides it.

### Decay Exploitation

An attacker times attestations to exploit decay, submitting false opinions when older honest attestations have decayed toward vacuous.

**Mitigation:** This is intended behavior, not a vulnerability. Decay is a feature: knowledge that nobody re-encounters should lose influence. An active herd that re-attests important hypotheses naturally maintains their epistemic state. An attacker who waits for decay to weaken honest opinions is competing against any oracle who re-visits the hypothesis. The defense is a living herd, not a mathematical safeguard.

### K = 0 Deployment Mode

When K = 0: maturity is transparent (M = 1.0 for all N_O ≥ 1). With perfect alignment t_oracle = 1.0, P_effective = 1.0: every opinion retains its full strength. Dogmatic inputs (c = ±1.0) pass through trust discounting undiscounted and can produce dogmatic ECBF outputs.

**This is an explicit deployer opt-in.** K = 0 means "I am disabling the maturity safeguard on purpose." It removes the algebraic guarantee that prevents dogmatic opinions. Deployers who set K = 0 must accept that the system's undogmatic property depends entirely on oracles voluntarily submitting |c| < 1. K ≥ 1 (default) is the recommended deployment mode; the maturity saturation function is the binding undogmatic constraint alongside trust discounting.

