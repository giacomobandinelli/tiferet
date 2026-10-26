"""No feature reacts to data before it was known (plan §10.1; decisions 0003, 0010).

Protected test: once tagged it is not edited. It iterates the feature registry, so a feature
registered later is covered without changing this file. Every Python and SQL feature is run
on synthetic inputs built from its declared input specs, once per input:

- a spike in that input changes nothing before the spike's `known_at`, on the point-in-time
  path and on a single full-history computation, and does change the feature afterwards, so
  the comparison is not between values that never move;
- the full-history value at a cut date equals the value computed from history truncated at
  the cut by the point-in-time reader (`pit.get` or `pit_sql.inputs`);
- a revised value is not visible before the revision's `known_at`, and is from then on.

Leaky features and a leaky reader defined here, in Python and SQL, must be caught, which
proves every check can fail. The harness lives in `look_ahead_harness.py`.
"""

from collections.abc import Mapping

import duckdb
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import tiferet.features  # noqa: F401  (importing the package fills the registry)
from locked.look_ahead_harness import (
    CON,
    Scenario,
    Target,
    base,
    base_full,
    python_target,
    revise,
    revision_leaks,
    spike_leaks_full,
    spike_leaks_pit,
    spike_reactions,
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
    decision_time,
)

TARGETS = [python_target(f) for f in registry.python_features().values()]
TARGETS += [sql_target(f) for f in registry.sql_features().values()]
CASES = [pytest.param(t, spec, id=f"{t.name}-{spec.role}") for t in TARGETS for spec in t.inputs]


def teardown_module() -> None:
    CON.close()


def test_registry_covers_python_sql_and_multi_input_features() -> None:
    assert any(len(f.inputs) > 1 for f in registry.python_features().values())
    assert any(len(f.inputs) > 1 for f in registry.sql_features().values())


@pytest.mark.parametrize(("target", "spec"), CASES)
def test_spike_is_invisible_before_known(target: Target, spec: InputSpec) -> None:
    scenario = base(target.inputs)
    with_spike, known_at = spiked(scenario, spec.role)
    plain, spike = base_full(target), target.full(with_spike)
    assert not spike_leaks_pit(target, scenario, with_spike, known_at)
    assert not spike_leaks_full(plain, spike, known_at)
    assert spike_reactions(plain, spike, known_at), "the spike never changes the feature"
    before = plain[decision_time(pd.DatetimeIndex(plain.index)) < known_at]
    assert before.notna().any(), "the feature has no value before the spike"


@pytest.mark.parametrize("target", [pytest.param(t, id=t.name) for t in TARGETS])
@settings(max_examples=25, derandomize=True, deadline=None)
@given(position=st.floats(min_value=0.0, max_value=1.0))
def test_full_history_equals_truncated_history(target: Target, position: float) -> None:
    full = base_full(target)
    cut = full.index[min(int(position * len(full)), len(full) - 1)]
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


PRICE = InputSpec("price", Frequency.DAILY)
CPI = InputSpec("cpi", Frequency.MONTHLY)
LEAKY = [
    (python_target(registry.PythonFeature("centred_mean", (PRICE,), _centred_mean)), "price"),
    (python_target(registry.PythonFeature("cpi_by_period", (PRICE, CPI), _cpi_by_period)), "cpi"),
    (sql_target(registry.SqlFeature("sql_by_period", (PRICE,), _sql_by_period)), "price"),
]
SMA = registry.python_features()["sma_200"]
LEAKY_READER = Target(
    "unfiltered_sma_200", SMA.inputs, lambda s, t: registry.compute(SMA, _unfiltered(s, t))
)


@pytest.mark.parametrize(("target", "role"), LEAKY, ids=[t.name for t, _ in LEAKY])
def test_checks_catch_a_leaky_feature(target: Target, role: str) -> None:
    scenario = base(target.inputs)
    with_spike, known_at = spiked(scenario, role)
    full = base_full(target)
    assert spike_leaks_full(full, target.full(with_spike), known_at)
    assert truncation_leaks(target, scenario, full, list(full.index[len(full) // 2 :: 7]))


def test_checks_catch_a_leaky_reader() -> None:
    scenario = base(LEAKY_READER.inputs)
    with_spike, known_at = spiked(scenario, "price")
    assert spike_leaks_pit(LEAKY_READER, scenario, with_spike, known_at)
    assert revision_leaks(LEAKY_READER, scenario, revise(scenario, "price"))
