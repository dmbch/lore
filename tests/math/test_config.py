"""Tests for EpistemicsConfig: the four epistemic hyperparameters."""

import pytest
from pydantic import ValidationError

from lore.math import EpistemicsConfig

_90_DAYS = 90 * 86400.0


def _epistemics(attestation_half_life: str | float) -> EpistemicsConfig:
    """Vary only the attestation half-life; the rest are fixed valid defaults.

    Lets the duration-grammar tests below read as a single moving part.
    """
    return EpistemicsConfig(
        attestation_half_life=attestation_half_life,  # pyright: ignore[reportArgumentType]
        trust_half_life=_90_DAYS,
        maturity_k=1.0,
    )


# ---------------------------------------------------------------------------
# Half-life duration grammar (bare seconds + y/M/d/h/m/s units)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (86400.0, 86400.0),  # bare float
        (3600, 3600.0),  # bare int → float
        ("3600s", 3600.0),  # seconds
        ("60m", 3600.0),  # minutes
        ("24h", 86400.0),  # hours
        ("90d", _90_DAYS),  # days
        ("3M", 3 * 2592000.0),  # months (30d)
        ("1y", 31536000.0),  # years (365d)
        ("1.5h", 5400.0),  # fractional
        ("  90d  ", _90_DAYS),  # surrounding whitespace tolerated
    ],
)
def test_half_life_grammar(value: str | float, expected: float) -> None:
    assert _epistemics(value).attestation_half_life == expected


@pytest.mark.parametrize("bad", ["90x", "hello"])
def test_half_life_malformed_string_raises(bad: str) -> None:
    with pytest.raises(ValidationError, match="invalid half_life"):
        _epistemics(bad)


@pytest.mark.parametrize("bad", [-1.0, 0.0])
def test_half_life_nonpositive_raises(bad: float) -> None:
    with pytest.raises(ValidationError, match="must be positive"):
        _epistemics(bad)


def test_both_half_lives_parse_independently() -> None:
    ec = EpistemicsConfig(
        attestation_half_life="90d",  # pyright: ignore[reportArgumentType]
        trust_half_life="45d",  # pyright: ignore[reportArgumentType]
        maturity_k=1.0,
    )
    assert ec.attestation_half_life == _90_DAYS
    assert ec.trust_half_life == 45 * 86400.0


# ---------------------------------------------------------------------------
# maturity_k
# ---------------------------------------------------------------------------


def test_rejects_negative_maturity_k() -> None:
    with pytest.raises(ValidationError, match="maturity_k"):
        EpistemicsConfig(attestation_half_life=_90_DAYS, trust_half_life=_90_DAYS, maturity_k=-1.0)


def test_maturity_k_zero_is_valid_transparent_mode() -> None:
    ec = EpistemicsConfig(attestation_half_life=_90_DAYS, trust_half_life=_90_DAYS, maturity_k=0)
    assert ec.maturity_k == 0


# ---------------------------------------------------------------------------
# transfer_threshold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0.0, -1e-3])
def test_rejects_nonpositive_transfer_threshold(bad: float) -> None:
    with pytest.raises(ValidationError, match="transfer_threshold"):
        EpistemicsConfig(
            attestation_half_life=_90_DAYS,
            trust_half_life=_90_DAYS,
            maturity_k=1.0,
            transfer_threshold=bad,
        )


def test_transfer_threshold_defaults_to_one_thousandth() -> None:
    assert _epistemics(_90_DAYS).transfer_threshold == 1e-3


def test_accepts_explicit_transfer_threshold() -> None:
    ec = EpistemicsConfig(
        attestation_half_life=_90_DAYS,
        trust_half_life=_90_DAYS,
        maturity_k=1.0,
        transfer_threshold=0.05,
    )
    assert ec.transfer_threshold == 0.05


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------


def test_epistemics_is_frozen() -> None:
    ec = _epistemics(_90_DAYS)
    with pytest.raises(ValidationError, match="frozen"):
        ec.attestation_half_life = 1.0  # pyright: ignore[reportAttributeAccessIssue]


def test_epistemics_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EpistemicsConfig(
            attestation_half_life=_90_DAYS,
            trust_half_life=_90_DAYS,
            maturity_k=1.0,
            alignment_weight=0.5,  # pyright: ignore[reportCallIssue]
        )
