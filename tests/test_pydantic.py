"""Tests for the shared pydantic dialect: DataModel and ConfigModel."""

import pytest
from pydantic import ConfigDict, ValidationError

from lore._pydantic import ConfigModel, DataModel


class _DataExample(DataModel):
    count: int


class _ConfigExample(ConfigModel):
    count: int


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
