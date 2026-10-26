"""Tests for the feature registry (decision 0010)."""

from collections.abc import Iterator, Mapping

import pandas as pd
import pytest

import tiferet.features  # noqa: F401  (importing the package fills the registry)
from tiferet.features import registry
from tiferet.features.view import Frequency, InputSpec, PitSeries

PRICE = InputSpec("price", Frequency.DAILY)


def constant(inputs: Mapping[str, PitSeries]) -> pd.Series:
    return pd.Series([1.0], index=pd.DatetimeIndex(["2022-01-03"]))


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(registry, "_PYTHON", dict(registry.python_features()))
    monkeypatch.setattr(registry, "_SQL", dict(registry.sql_features()))
    yield


def test_reference_features_are_registered() -> None:
    assert {"sma_200", "momentum_12_1", "real_price"} <= set(registry.python_features())
    assert "sql_sma_200" in registry.sql_features()


def test_listing_returns_a_copy() -> None:
    registry.python_features().clear()
    assert registry.python_features()


@pytest.mark.usefixtures("isolated_registry")
def test_duplicate_name_is_refused() -> None:
    with pytest.raises(ValueError, match="already registered"):
        registry.register_python("sma_200", PRICE)(constant)
    with pytest.raises(ValueError, match="already registered"):
        registry.register_sql("sma_200", PRICE)(lambda rel: rel)


@pytest.mark.usefixtures("isolated_registry")
@pytest.mark.parametrize("inputs", [(), (PRICE, PRICE)], ids=["no-inputs", "repeated-role"])
def test_inputs_must_be_present_and_distinct(inputs: tuple[InputSpec, ...]) -> None:
    with pytest.raises(ValueError, match="distinct roles"):
        registry.register_python("new_feature", *inputs)(constant)


def test_compute_needs_exactly_the_declared_inputs() -> None:
    feature = registry.PythonFeature("constant", (PRICE,), constant)
    with pytest.raises(ValueError, match="takes inputs"):
        registry.compute(feature, {})


def test_compute_checks_the_output() -> None:
    feature = registry.PythonFeature("bad", (PRICE,), lambda inputs: pd.Series(["x"]))
    price = PitSeries(
        pd.Series([], dtype=float, index=pd.DatetimeIndex([])),
        pd.Series([], dtype="datetime64[us, UTC]", index=pd.DatetimeIndex([])),
        pd.Timestamp("2022-01-03", tz="UTC"),
    )
    with pytest.raises(TypeError, match="float Series"):
        registry.compute(feature, {"price": price})
