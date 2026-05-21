# pyright: reportPrivateUsage=false
"""Decay — prove confidence decreases over real elapsed time.

Writes a hypothesis via the full pipeline, waits for real time to pass,
then verifies that compute_confidence() produces decayed values. The stored
c_herd on the ledger is immutable — decay is computed at read time.
"""

import asyncio
import time

import pytest

from lore.domain import EvidenceInput
from lore.orchestrator import Orchestrator
from tests.e2e.conftest import attestations, consult

pytestmark = pytest.mark.e2e


async def test_decay_reduces_confidence(decay_system: Orchestrator) -> None:
    """Stored c_herd is immutable; computed confidence decreases over time."""
    # Write a hypothesis with high confidence
    await consult(
        decay_system,
        hypothesis="Decay test hypothesis — this claim should lose confidence over time",
        confidence=0.9,
        oracle="oracle-decay",
        correlation_id="decay-01",
    )

    # Fetch the stored attestation
    rows = await attestations(decay_system, "decay-01")
    assert len(rows) >= 1, "Expected at least one attestation"
    stored_c_herd = float(rows[0]["c_herd"])
    stored_timestamp = int(rows[0]["timestamp"])
    stored_c_discounted = float(rows[0]["c_oracle_discounted"])

    # Wait 2 half-lives (~75% decay)
    await asyncio.sleep(4)

    # Compute current confidence with real attestation data
    evidence = [EvidenceInput(c_oracle_discounted=stored_c_discounted, timestamp=stored_timestamp)]
    t_now_1 = int(time.time())
    current_1 = decay_system._math.compute_confidence(attestations=evidence, t_now=t_now_1)

    # Decay reduced confidence
    assert abs(current_1) < abs(stored_c_herd), (
        f"Decay should reduce: |{current_1}| should be < |{stored_c_herd}|"
    )

    # Wait another 1.5 half-lives
    await asyncio.sleep(3)

    # Compute again
    t_now_2 = int(time.time())
    current_2 = decay_system._math.compute_confidence(attestations=evidence, t_now=t_now_2)

    # Monotonic: further decay
    assert abs(current_2) < abs(current_1), (
        f"Decay should be monotonic: |{current_2}| should be < |{current_1}|"
    )

    # Sign preserved (positive stays positive)
    assert current_1 > 0, f"Sign should be preserved: {current_1}"
    assert current_2 > 0, f"Sign should be preserved: {current_2}"

    # Stored c_herd unchanged (immutable ledger)
    rows_after = await attestations(decay_system, "decay-01")
    assert float(rows_after[0]["c_herd"]) == stored_c_herd, "Stored c_herd must be immutable"
