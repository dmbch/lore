"""Hypothesis maturity: M = N_O / (N_O + K).

Saturation function over oracle diversity. See docs/logic.md §Hypothesis Maturity.
"""


def compute_maturity(*, n_oracle_prior: int, k: float) -> float:
    """M in (0, 1]. K=0 is transparent; K=1 adds one phantom skeptic."""
    if n_oracle_prior < 0:
        msg = f"n_oracle_prior must be non-negative, got {n_oracle_prior}"
        raise ValueError(msg)
    if k < 0.0:
        msg = f"k must be non-negative, got {k}"
        raise ValueError(msg)
    n_o = n_oracle_prior + 1
    return n_o / (n_o + k)
