"""Shared test infrastructure for the math core.

Strategies generate random valid inputs for Hypothesis property-based tests.
Tolerance constants control how tightly we compare floating-point results.
"""

from hypothesis import strategies as st

from lore.math.opinion import Opinion

# --- Hypothesis strategies ---

# Generate valid (b, d, u) triples uniformly on the 2-simplex.
# Dirichlet-style: sample 3 exponentials, normalize to sum to 1.
# This avoids the bias of sequential sampling (which over-represents
# the high-b, low-u corner of the simplex) and gives uniform coverage
# across vacuous, dogmatic, belief-heavy, and disbelief-heavy opinions.
bdu_strategy = st.lists(
    st.floats(min_value=1e-9, max_value=1.0),
    min_size=3,
    max_size=3,
).map(lambda xs: tuple(x / sum(xs) for x in xs))

# Generate valid Opinion instances with random BDU values.
opinion_strategy = bdu_strategy.map(lambda bdu: Opinion(b=bdu[0], d=bdu[1], u=bdu[2]))

# --- Tolerances ---

# Wider tolerance for property tests where floating-point error accumulates
# across multiple operations (fusion, discounting, etc.). EPSILON (1e-9) is
# too tight for chained arithmetic; 1e-6 absorbs accumulated rounding while
# still catching real bugs.
PROP_TOL = 1e-6
