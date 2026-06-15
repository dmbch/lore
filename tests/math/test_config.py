"""Tests for EpistemicsConfig — the four epistemic hyperparameters."""

import pytest

from lore.math import EpistemicsConfig

_90_DAYS = 90 * 86400.0


def test_epistemics_parses_duration_strings() -> None:
    ec = EpistemicsConfig(
        attestation_half_life="90d",  # pyright: ignore[reportArgumentType]
        trust_half_life="45d",  # pyright: ignore[reportArgumentType]
        maturity_k=1.0,
    )
    assert ec.attestation_half_life == _90_DAYS
    assert ec.trust_half_life == 45 * 86400.0


def test_epistemics_rejects_malformed_duration_string() -> None:
    with pytest.raises(ValueError, match="invalid half_life"):
        EpistemicsConfig(
            attestation_half_life="soon",  # pyright: ignore[reportArgumentType]
            trust_half_life=_90_DAYS,
            maturity_k=1.0,
        )


def test_epistemics_rejects_nonpositive_half_life() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        EpistemicsConfig(
            attestation_half_life=0.0,
            trust_half_life=_90_DAYS,
            maturity_k=1.0,
        )


def test_epistemics_rejects_negative_maturity_k() -> None:
    with pytest.raises(ValueError, match="maturity_k"):
        EpistemicsConfig(
            attestation_half_life=_90_DAYS,
            trust_half_life=_90_DAYS,
            maturity_k=-1.0,
        )


def test_epistemics_rejects_nonpositive_transfer_threshold() -> None:
    for bad in (0.0, -1e-3):
        with pytest.raises(ValueError, match="transfer_threshold"):
            EpistemicsConfig(
                attestation_half_life=_90_DAYS,
                trust_half_life=_90_DAYS,
                maturity_k=1.0,
                transfer_threshold=bad,
            )


def test_epistemics_default_transfer_threshold() -> None:
    ec = EpistemicsConfig(
        attestation_half_life=_90_DAYS,
        trust_half_life=_90_DAYS,
        maturity_k=1.0,
    )
    assert ec.transfer_threshold == 1e-3


def test_epistemics_accepts_explicit_transfer_threshold() -> None:
    ec = EpistemicsConfig(
        attestation_half_life=_90_DAYS,
        trust_half_life=_90_DAYS,
        maturity_k=1.0,
        transfer_threshold=0.05,
    )
    assert ec.transfer_threshold == 0.05
