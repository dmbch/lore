"""Tests for the shared pydantic dialect: DataModel, ConfigModel, and
field types."""

import math

import pytest
from pydantic import ConfigDict, ValidationError

from lore._pydantic import (
    ConfigModel,
    DataModel,
    NonEmptyStr,
    NonNegativeFiniteFloat,
    PositiveFiniteFloat,
    SignedUnitInterval,
    UnitInterval,
)


class _DataExample(DataModel):
    count: int


class _ConfigExample(ConfigModel):
    count: int


class _UnitIntervalExample(DataModel):
    value: UnitInterval


class _SignedUnitIntervalExample(DataModel):
    value: SignedUnitInterval


class _NonEmptyStrExample(DataModel):
    value: NonEmptyStr


class TestDataModel:
    def test_data_model_rejects_mutation(self) -> None:
        example = _DataExample(count=1)
        with pytest.raises(ValidationError, match="frozen"):
            example.count = 2  # pyright: ignore[reportAttributeAccessIssue]

    def test_data_model_rejects_coercion(self) -> None:
        with pytest.raises(ValidationError):
            _DataExample(count="1")  # pyright: ignore[reportArgumentType]

    def test_data_model_ignores_extra_keys(self) -> None:
        example = _DataExample(count=1, extra="ignored")  # pyright: ignore[reportCallIssue]
        assert not hasattr(example, "extra")


class TestConfigModel:
    def test_config_model_rejects_extra_keys(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            _ConfigExample(count=1, extra="rejected")  # pyright: ignore[reportCallIssue]

    def test_config_model_is_frozen_and_strict(self) -> None:
        example = _ConfigExample(count=1)
        with pytest.raises(ValidationError, match="frozen"):
            example.count = 2  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(ValidationError):
            _ConfigExample(count="1")  # pyright: ignore[reportArgumentType]

    def test_subclass_override_merges_config(self) -> None:
        class _AllowExample(ConfigModel):
            model_config = ConfigDict(extra="allow")
            count: int

        example = _AllowExample(count=1)
        with pytest.raises(ValidationError, match="frozen"):
            example.count = 2  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(ValidationError):
            _AllowExample(count="1")  # pyright: ignore[reportArgumentType]


class TestUnitInterval:
    def test_unit_interval_accepts_endpoints(self) -> None:
        assert _UnitIntervalExample(value=0.0).value == 0.0
        assert _UnitIntervalExample(value=1.0).value == 1.0

    def test_unit_interval_rejects_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            _UnitIntervalExample(value=-0.1)
        with pytest.raises(ValidationError):
            _UnitIntervalExample(value=1.1)

    def test_unit_interval_rejects_nan_and_inf(self) -> None:
        with pytest.raises(ValidationError):
            _UnitIntervalExample(value=math.nan)
        with pytest.raises(ValidationError):
            _UnitIntervalExample(value=math.inf)
        with pytest.raises(ValidationError):
            _UnitIntervalExample(value=-math.inf)


class TestSignedUnitInterval:
    def test_signed_unit_interval_accepts_endpoints(self) -> None:
        assert _SignedUnitIntervalExample(value=-1.0).value == -1.0
        assert _SignedUnitIntervalExample(value=1.0).value == 1.0

    def test_signed_unit_interval_rejects_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            _SignedUnitIntervalExample(value=-1.1)
        with pytest.raises(ValidationError):
            _SignedUnitIntervalExample(value=1.1)

    def test_signed_unit_interval_rejects_nan_and_inf(self) -> None:
        with pytest.raises(ValidationError):
            _SignedUnitIntervalExample(value=math.nan)
        with pytest.raises(ValidationError):
            _SignedUnitIntervalExample(value=math.inf)
        with pytest.raises(ValidationError):
            _SignedUnitIntervalExample(value=-math.inf)


class TestNonEmptyStr:
    def test_non_empty_str_rejects_empty(self) -> None:
        with pytest.raises(ValidationError):
            _NonEmptyStrExample(value="")
        assert _NonEmptyStrExample(value=" ").value == " "


class _PositiveFiniteFloatExample(DataModel):
    value: PositiveFiniteFloat


class _NonNegativeFiniteFloatExample(DataModel):
    value: NonNegativeFiniteFloat


class TestPositiveFiniteFloat:
    def test_positive_finite_float_rejects_zero(self) -> None:
        with pytest.raises(ValidationError):
            _PositiveFiniteFloatExample(value=0.0)

    def test_positive_finite_float_rejects_nan_and_inf(self) -> None:
        with pytest.raises(ValidationError):
            _PositiveFiniteFloatExample(value=math.nan)
        with pytest.raises(ValidationError):
            _PositiveFiniteFloatExample(value=math.inf)


class TestNonNegativeFiniteFloat:
    def test_non_negative_finite_float_accepts_zero(self) -> None:
        assert _NonNegativeFiniteFloatExample(value=0.0).value == 0.0

    def test_non_negative_finite_float_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            _NonNegativeFiniteFloatExample(value=-0.1)

    def test_non_negative_finite_float_rejects_nan_and_inf(self) -> None:
        with pytest.raises(ValidationError):
            _NonNegativeFiniteFloatExample(value=math.nan)
        with pytest.raises(ValidationError):
            _NonNegativeFiniteFloatExample(value=math.inf)
