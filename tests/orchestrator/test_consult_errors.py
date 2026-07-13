"""Error-posture orchestrator tests: internal pydantic failures stay masked.

fastmcp re-raises a pydantic ``ValidationError`` to the client verbatim,
bypassing error masking. Every model constructed inside ``consult`` is lore's
own, so a ``ValidationError`` there is an internal bug: the orchestrator must
surface it as ``DomainInvariantError`` before it reaches that echo arm.
"""

import pytest
from pydantic import ValidationError

from lore.domain import ConsultLoreRequest, DomainInvariantError
from tests.orchestrator.conftest import make_orchestrator, raise_internal_validation_error


class TestConsultInternalValidationErrorPosture:
    async def test_consult_wraps_internal_validation_error_as_domain_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "lore.orchestrator.orchestrator.ConsultLoreResponse",
            raise_internal_validation_error,
        )
        fixture = make_orchestrator()

        with pytest.raises(DomainInvariantError):
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(question="What is X?"),
                correlation_id="corr-1",
            )

    async def test_consult_domain_error_keeps_validation_error_as_cause(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Operators keep the diagnostic through the exception chain."""
        monkeypatch.setattr(
            "lore.orchestrator.orchestrator.ConsultLoreResponse",
            raise_internal_validation_error,
        )
        fixture = make_orchestrator()

        with pytest.raises(DomainInvariantError) as exc_info:
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(question="What is X?"),
                correlation_id="corr-1",
            )

        assert isinstance(exc_info.value.__cause__, ValidationError)

    async def test_consult_domain_error_message_omits_request_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "lore.orchestrator.orchestrator.ConsultLoreResponse",
            raise_internal_validation_error,
        )
        fixture = make_orchestrator()
        marker = "hexactinellid sponge reefs off Hecate Strait"

        with pytest.raises(DomainInvariantError) as exc_info:
            await fixture.orchestrator.consult(
                oracle_id="oracle-1",
                request=ConsultLoreRequest(question=marker),
                correlation_id="corr-1",
            )

        assert marker not in str(exc_info.value)
