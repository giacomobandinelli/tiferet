"""No feature reacts to data before it was known (plan §10.1; decisions 0003, 0010, 0011).

Protected test: once tagged it is not edited. It iterates the feature registry, so a feature
registered later is covered without changing this file. Every Python and SQL feature is run
on synthetic inputs built from its declared input specs, with as much history as each input's
declared warm-up needs, once per input:

- a one-day spike in that input changes nothing before the spike's `known_at`, on the
  point-in-time path and on a single full-history computation;
- lasting level shifts from the same `known_at` on, multiplicative and additive in both
  directions, change nothing before it on the full-history computation;
- the spike or one of the shifts does change the feature from then on, and the feature has
  values before it, so a feature that never moves or is always NaN fails;
- the full-history value at a cut date equals the value computed from history truncated at
  the cut by the point-in-time reader (`pit.get` or `pit_sql.inputs`);
- a revised value is not visible before the revision's `known_at`, and is from then on.

Leaky features, a leaky reader and features that never move, defined here in Python and SQL,
must be caught, which proves every check can fail. The harness lives in
`look_ahead_harness.py`.
"""

from collections.abc import Iterator, Mapping

import duckdb
import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import tiferet.features  # noqa: F401  (importing the package fills the registry)
from locked.look_ahead_harness import (
    Scenario,
    Target,
    base,
    base_full,
    has_value_before,
    leaks_full,
    python_target,
    reacts,
    responses,
    revise,
    revision_leaks,
    spike_leaks_pit,
    spiked,
    sql_target,
    truncation_leaks,
)
from tiferet.data.schema import KNOWN_AT, OBS_DATE, VALUE
from tiferet.features import registry
from tiferet.features.sql_view import INPUTS, aligned_query
from tiferet.features.view import (
    Frequency,
    InputSpec,
    PitSeries,
    align,
    decision_dates,
)

TARGETS = [python_target(f) for f in registry.python_features().values()]
TARGETS += [sql_target(f) for f in registry.sql_features().values()]
CASES = [pytest.param(t, spec, id=f"{t.name}-{spec.role}") for t in TARGETS for spec in t.inputs]
LONG_WARMUP = 504  # a 2-year window of daily observations


def test_registry_covers_python_sql_multi_input_and_long_window_features() -> None:
    assert any(len(f.inputs) > 1 for f in registry.python_features().values())
    assert any(len(f.inputs) > 1 for f in registry.sql_features().values())
    warmups = [registry.warmup(spec) for target in TARGETS for spec in target.inputs]
    assert max(warmups) >= LONG_WARMUP


@pytest.mark.parametrize(("target", "spec"), CASES)
def test_spike_and_shifts_are_invisible_before_known(target: Target, spec: InputSpec) -> None:
    scenario = base(target.inputs)
    with_spike, known_at = spiked(scenario, spec.role)
    assert not spike_leaks_pit(target, scenario, with_spike, known_at)
    found = responses(target, spec.role)
    assert not {name: r.leaks for name, r in found.items() if r.leaks}
    assert reacts(found), "neither a spike nor a level shift changes the feature"
    assert has_value_before(target, spec.role), (
        "the feature has no value before the event; is its declared warm-up too short?"
    )


@pytest.mark.parametrize("target", [pytest.param(t, id=t.name) for t in TARGETS])
@settings(max_examples=25, derandomize=True, deadline=None)
@given(position=st.floats(min_value=0.0, max_value=1.0))
def test_full_history_equals_truncated_history(target: Target, position: float) -> None:
    full = base_full(target)
    valued = full.dropna().index
    cut = valued[min(int(position * len(valued)), len(valued) - 1)]
    assert not truncation_leaks(target, base(target.inputs), full, [cut])


@pytest.mark.parametrize(("target", "spec"), CASES)
def test_revision_is_invisible_before_its_known_at(target: Target, spec: InputSpec) -> None:
    scenario = base(target.inputs)
    revision = revise(scenario, spec.role)

    def seen(t: pd.Timestamp) -> float:
        return float(revision.revised.views(t)[spec.role].values[revision.obs_date])

    assert seen(revision.revised_known - pd.Timedelta(microseconds=1)) == revision.first
    assert seen(revision.revised_known) == revision.second
    assert not revision_leaks(target, scenario, revision)


def _running_mean(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """Not leaky: the mean of every observation so far, read below through a leaky reader.

    Its last value depends on every observation, so a reader that leaks any of them shows.
    """
    price = inputs["price"]
    return align(price.values.expanding().mean(), price, decision_dates(inputs))


def _centred_mean(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """Leaky: the window reaches ten observations into the future."""
    price = inputs["price"]
    computed = price.values.rolling(21, center=True).mean()
    return align(computed, price, decision_dates(inputs))


def _cpi_by_period(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """Leaky: each CPI value is used from the start of its month, before it was published."""
    price, cpi = inputs["price"], inputs["cpi"]
    dates = decision_dates(inputs)
    by_period = cpi.values.reindex(dates, method="ffill")
    return align(price.values, price, dates) / by_period


_SQL_BY_PERIOD = f"""
SELECT obs_date, timezone('Europe/Berlin', obs_date::TIMESTAMP) AS available_at,
       avg(value) OVER (ORDER BY obs_date ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) AS value
FROM {INPUTS} WHERE input = 'price'
"""


def _sql_by_period(inputs: duckdb.DuckDBPyRelation) -> duckdb.DuckDBPyRelation:
    """Leaky: each daily value is used from the start of its day, before it was published."""
    result = inputs.query(INPUTS, aligned_query(_SQL_BY_PERIOD))
    assert result is not None
    return result


def _unfiltered(scenario: Scenario, as_of: pd.Timestamp) -> dict[str, PitSeries]:
    """Leaky reader: the latest version of every observation, whenever it became known."""
    views = {}
    for role, frame in scenario.frames.items():
        latest = frame.sort_values([OBS_DATE, KNOWN_AT]).drop_duplicates(OBS_DATE, keep="last")
        index = pd.DatetimeIndex(latest[OBS_DATE])
        stamp = as_of.tz_convert("UTC")
        known_at = latest[KNOWN_AT].where(latest[KNOWN_AT] <= stamp, stamp)
        views[role] = PitSeries(
            pd.Series(latest[VALUE].to_numpy(), index=index),
            pd.Series(known_at.array, index=index),
            as_of,
        )
    return views


def _constant(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """Never moves: 1.0 on every decision date."""
    dates = decision_dates(inputs)
    return pd.Series(np.ones(len(dates)), index=dates)


def _all_nan(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """Never has a value."""
    dates = decision_dates(inputs)
    return pd.Series(np.full(len(dates), np.nan), index=dates)


PRICE = InputSpec("price", Frequency.DAILY, warmup=21)
CPI = InputSpec("cpi", Frequency.MONTHLY, warmup=1)
LEAKY = [
    (python_target(registry.PythonFeature("centred_mean", (PRICE,), _centred_mean)), "price"),
    (
        python_target(
            registry.PythonFeature("cpi_by_period", (PRICE.with_warmup(1), CPI), _cpi_by_period)
        ),
        "cpi",
    ),
    (sql_target(registry.SqlFeature("sql_by_period", (PRICE,), _sql_by_period)), "price"),
]
MEAN = registry.PythonFeature("running_mean", (PRICE,), _running_mean)
LEAKY_READER = Target(
    "unfiltered_running_mean", MEAN.inputs, lambda s, t: registry.compute(MEAN, _unfiltered(s, t))
)
STATIC = [
    python_target(registry.PythonFeature("constant", (PRICE,), _constant)),
    python_target(registry.PythonFeature("all_nan", (PRICE,), _all_nan)),
]


@pytest.mark.parametrize(("target", "role"), LEAKY, ids=[t.name for t, _ in LEAKY])
def test_checks_catch_a_leaky_feature(target: Target, role: str) -> None:
    scenario = base(target.inputs)
    with_spike, known_at = spiked(scenario, role)
    full = base_full(target)
    assert leaks_full(full, target.full(with_spike), known_at)
    assert any(r.leaks for r in responses(target, role).values())
    assert truncation_leaks(target, scenario, full, list(full.index[len(full) // 2 :: 7]))


def test_checks_catch_a_leaky_reader() -> None:
    scenario = base(LEAKY_READER.inputs)
    with_spike, known_at = spiked(scenario, "price")
    assert spike_leaks_pit(LEAKY_READER, scenario, with_spike, known_at)
    assert revision_leaks(LEAKY_READER, scenario, revise(scenario, "price"))


@pytest.mark.parametrize("target", STATIC, ids=[t.name for t in STATIC])
def test_checks_catch_a_feature_that_never_moves(target: Target) -> None:
    found = responses(target, "price")
    assert not any(r.leaks for r in found.values())
    assert not reacts(found)


def test_checks_catch_a_feature_without_values() -> None:
    assert not has_value_before(STATIC[1], "price")


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(registry, "_PYTHON", dict(registry.python_features()))
    monkeypatch.setattr(registry, "_SQL", dict(registry.sql_features()))
    yield


@pytest.mark.usefixtures("isolated_registry")
def test_registry_refuses_a_feature_without_warm_up() -> None:
    with pytest.raises(ValueError, match="warm-up"):
        registry.register_python("no_warm_up", InputSpec("price", Frequency.DAILY))(_constant)
    with pytest.raises(ValueError, match="warm-up"):
        registry.register_sql("sql_no_warm_up", InputSpec("price", Frequency.DAILY))(_sql_by_period)
