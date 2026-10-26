"""Registry of features (plan §3.3, §10.1; decision 0010).

A feature declares its inputs and is a pure function of their point-in-time views. A Python
feature maps each input role to a `PitSeries` and returns a float Series indexed by daily
decision date up to the views' `as_of`. A SQL feature maps the relation built by
`tiferet.data.pit_sql.inputs` to a relation with columns `date, value`. The locked look-ahead
test iterates this registry, so every feature registered here is covered by it.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import duckdb
import pandas as pd

from tiferet.data.store import check_series_id
from tiferet.features.view import InputSpec, PitSeries

PythonFn = Callable[[Mapping[str, PitSeries]], pd.Series]
SqlFn = Callable[[duckdb.DuckDBPyRelation], duckdb.DuckDBPyRelation]


@dataclass(frozen=True)
class PythonFeature:
    name: str
    inputs: tuple[InputSpec, ...]
    fn: PythonFn

    @property
    def roles(self) -> frozenset[str]:
        return frozenset(spec.role for spec in self.inputs)


@dataclass(frozen=True)
class SqlFeature:
    name: str
    inputs: tuple[InputSpec, ...]
    fn: SqlFn

    @property
    def roles(self) -> frozenset[str]:
        return frozenset(spec.role for spec in self.inputs)


_PYTHON: dict[str, PythonFeature] = {}
_SQL: dict[str, SqlFeature] = {}


def _check_declaration(name: str, inputs: tuple[InputSpec, ...]) -> None:
    check_series_id(name)
    if name in _PYTHON or name in _SQL:
        raise ValueError(f"feature {name!r} is already registered")
    roles = [check_series_id(spec.role) for spec in inputs]
    if not roles or len(set(roles)) != len(roles):
        raise ValueError(f"feature {name!r} needs one or more inputs with distinct roles")


def register_python(name: str, *inputs: InputSpec) -> Callable[[PythonFn], PythonFn]:
    """Register a Python feature under `name` with the declared inputs."""

    def decorate(fn: PythonFn) -> PythonFn:
        _check_declaration(name, inputs)
        _PYTHON[name] = PythonFeature(name, inputs, fn)
        return fn

    return decorate


def register_sql(name: str, *inputs: InputSpec) -> Callable[[SqlFn], SqlFn]:
    """Register a SQL feature under `name` with the declared inputs."""

    def decorate(fn: SqlFn) -> SqlFn:
        _check_declaration(name, inputs)
        _SQL[name] = SqlFeature(name, inputs, fn)
        return fn

    return decorate


def python_features() -> dict[str, PythonFeature]:
    return dict(_PYTHON)


def sql_features() -> dict[str, SqlFeature]:
    return dict(_SQL)


def _check_output(name: str, result: pd.Series) -> pd.Series:
    if not isinstance(result.index, pd.DatetimeIndex) or not pd.api.types.is_float_dtype(result):
        raise TypeError(f"feature {name!r} must return a float Series on a DatetimeIndex")
    return result


def compute(feature: PythonFeature, views: Mapping[str, PitSeries]) -> pd.Series:
    """Run a Python feature on views of exactly its declared inputs."""
    if set(views) != feature.roles:
        raise ValueError(f"feature {feature.name!r} takes inputs {sorted(feature.roles)}")
    return _check_output(feature.name, feature.fn(views))


def compute_sql(feature: SqlFeature, inputs: duckdb.DuckDBPyRelation) -> pd.Series:
    """Run a SQL feature on its input relation and return its values by decision date."""
    present = {row[0] for row in inputs.unique("input").fetchall()}
    if not present <= feature.roles:
        raise ValueError(f"feature {feature.name!r} takes inputs {sorted(feature.roles)}")
    frame = feature.fn(inputs).df()
    index = pd.DatetimeIndex(frame["date"], name="date").as_unit("us")
    result = pd.Series(frame["value"].to_numpy(dtype=float), index=index, name="value")
    return _check_output(feature.name, result)


def evaluate(feature: PythonFeature, views: Mapping[str, PitSeries]) -> float:
    """The feature's value known at the views' `as_of`; NaN if there is none."""
    result = compute(feature, views)
    return float(result.iloc[-1]) if not result.empty else float("nan")


def evaluate_sql(feature: SqlFeature, inputs: duckdb.DuckDBPyRelation) -> float:
    """SQL counterpart of `evaluate`."""
    result = compute_sql(feature, inputs)
    return float(result.iloc[-1]) if not result.empty else float("nan")
