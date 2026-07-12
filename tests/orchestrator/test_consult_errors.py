"""Error-posture orchestrator tests: internal pydantic failures stay masked.

fastmcp re-raises a pydantic ``ValidationError`` to the client verbatim,
bypassing error masking. Every model constructed inside ``consult`` is lore's
own, so a ``ValidationError`` there is an internal bug: the orchestrator must
surface it as ``DomainInvariantError`` before it reaches that echo arm.
"""

from typing import NoReturn

import pytest
from pydantic import BaseModel, ValidationError

from lore.domain import ConsultLoreRequest, DomainInvariantError
from tests.orchestrator.conftest import make_orchestrator


class _InternalModel(BaseModel):
    value: int


def _raise_internal_validation_error(*_args: object, **_kwargs: object) -> NoReturn:
    """Stand-in constructor that fails with a real pydantic ValidationError."""
    _InternalModel.model_validate({"value": "not an int"})
    raise AssertionError("model_validate should have raised")


class TestConsultInternalValidationErrorPosture:
    async def test_consult_wraps_internal_validation_error_as_domain_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "lore.orchestrator.orchestrator.ConsultLoreResponse",
            _raise_internal_validation_error,
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
            _raise_internal_validation_error,
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
            _raise_internal_validation_error,
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
