"""Harness of the locked look-ahead test (plan §10.1; decisions 0003, 0010).

Protected with `test_no_look_ahead_registry.py`: once tagged it is not edited. It builds
synthetic inputs from declared input specs, reads them through `pit.get` (Python features) or
`pit_sql.inputs` (SQL features), and reports the moments at which a feature differs from what
it could have known. Every read is in research mode on development dates, with an empty
environment so the holdout is sealed.
"""

import functools
import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

from tiferet.data import pit, pit_sql
from tiferet.data.schema import KNOWN_AT, OBS_DATE, SERIES_ID, VALUE, validate_silver
from tiferet.data.store import BERLIN, InMemoryStore
from tiferet.features import registry
from tiferet.features.view import Frequency, InputSpec, PitSeries, decision_time

SEALED: dict[str, str] = {}
START = pd.Timestamp("2018-01-01")
END = START + pd.offsets.BDay(600)
OBS_FREQ = {
    Frequency.DAILY: "B",
    Frequency.WEEKLY: "W-FRI",
    Frequency.MONTHLY: "MS",
    Frequency.QUARTERLY: "QS",
}
# Each lag crosses at least one day boundary, so aligning on obs_date instead of known_at
# shows up as a leak at every frequency.
LAG = {
    Frequency.DAILY: pd.Timedelta(days=1, hours=8),
    Frequency.WEEKLY: pd.Timedelta(days=5),
    Frequency.MONTHLY: pd.Timedelta(days=45),
    Frequency.QUARTERLY: pd.Timedelta(days=60),
}
EVENT_SHARE = 0.7  # the spike and the revision sit 70% into each input's history
GRID = 15  # as_of moments checked around each event
REVISION_DELAY = pd.Timedelta(days=2 * GRID)

CON = duckdb.connect()
_TABLES = itertools.count()


def synthetic(spec: InputSpec, seed: int) -> pd.DataFrame:
    """A positive random walk observed at the spec's frequency, published after its lag."""
    obs = pd.date_range(START, END, freq=OBS_FREQ[spec.frequency])
    steps = np.random.default_rng(seed).normal(0.0, 0.01, len(obs))
    known_at = (obs + LAG[spec.frequency]).tz_localize(BERLIN)
    values = 100 * np.exp(np.cumsum(steps))
    frame = pd.DataFrame({SERIES_ID: spec.role, OBS_DATE: obs, KNOWN_AT: known_at, VALUE: values})
    return validate_silver(frame)


@dataclass(frozen=True)
class Scenario:
    """Inputs by role, readable through `pit.get` and through `pit_sql.inputs`."""

    frames: dict[str, pd.DataFrame]

    @functools.cached_property
    def store(self) -> InMemoryStore:
        return InMemoryStore(pd.concat(self.frames.values(), ignore_index=True))

    @functools.cached_property
    def table(self) -> str:
        """A DuckDB silver table of the inputs on the shared connection."""
        name = f"silver_{next(_TABLES)}"
        CON.register("frames", pd.concat(self.frames.values(), ignore_index=True))
        CON.execute(
            f"CREATE TABLE {name} AS"
            " SELECT series_id, obs_date::DATE AS obs_date, known_at, value FROM frames"
        )
        CON.unregister("frames")
        return name

    def views(self, as_of: pd.Timestamp) -> dict[str, PitSeries]:
        def read(role: str) -> pd.DataFrame:
            return pit.get(role, as_of, mode=pit.Mode.RESEARCH, store=self.store, env=SEALED)

        return {role: PitSeries.from_frame(read(role), as_of) for role in self.frames}

    def relation(self, as_of: pd.Timestamp) -> duckdb.DuckDBPyRelation:
        roles = {role: role for role in self.frames}
        return pit_sql.inputs(CON, self.table, roles, as_of, mode=pit.Mode.RESEARCH, env=SEALED)

    def last_moment(self) -> pd.Timestamp:
        """The decision moment of the last date on which any input became known."""
        known = pd.concat([frame[KNOWN_AT] for frame in self.frames.values()])
        last = pd.DatetimeIndex([known.max().tz_convert(BERLIN)])
        return pd.Timestamp(decision_time(last)[0])

    def with_frame(self, role: str, frame: pd.DataFrame) -> "Scenario":
        return Scenario({**self.frames, role: validate_silver(frame)})


@dataclass(frozen=True)
class Target:
    """A feature as one callable giving its values from the inputs known at a moment."""

    name: str
    inputs: tuple[InputSpec, ...]
    run: Callable[[Scenario, pd.Timestamp], pd.Series]

    def full(self, scenario: Scenario) -> pd.Series:
        """Values on every decision date, computed once on the whole history."""
        return self.run(scenario, scenario.last_moment())

    def at(self, scenario: Scenario, as_of: pd.Timestamp) -> float:
        """The value known at `as_of`, from the inputs truncated by the point-in-time reader."""
        result = self.run(scenario, as_of)
        return float(result.iloc[-1]) if not result.empty else math.nan


def python_target(feature: registry.PythonFeature) -> Target:
    return Target(feature.name, feature.inputs, lambda s, t: registry.compute(feature, s.views(t)))


def sql_target(feature: registry.SqlFeature) -> Target:
    return Target(
        feature.name, feature.inputs, lambda s, t: registry.compute_sql(feature, s.relation(t))
    )


@functools.cache
def base(inputs: tuple[InputSpec, ...]) -> Scenario:
    return Scenario({spec.role: synthetic(spec, seed) for seed, spec in enumerate(inputs)})


@functools.cache
def base_full(target: Target) -> pd.Series:
    return target.full(base(target.inputs))


def same(a: float, b: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


def moments_before(known_at: pd.Timestamp) -> list[pd.Timestamp]:
    """Daily moments up to `known_at`, the last one a microsecond before it."""
    steps = [known_at - pd.Timedelta(days=d) for d in range(GRID - 1, 0, -1)]
    return [*steps, known_at - pd.Timedelta(microseconds=1)]


def moments_from(known_at: pd.Timestamp) -> list[pd.Timestamp]:
    return [known_at + pd.Timedelta(days=d) for d in range(GRID)]


def event_row(frame: pd.DataFrame) -> tuple[int, float, pd.Timestamp]:
    """Position, value and `known_at` of the row 70% into an input's history."""
    row = int(len(frame) * EVENT_SHARE)
    return row, float(frame[VALUE].iloc[row]), pd.Timestamp(frame[KNOWN_AT].iloc[row])


def with_value(frame: pd.DataFrame, row: int, value: float) -> pd.DataFrame:
    values = frame[VALUE].to_numpy(copy=True)
    values[row] = value
    return frame.assign(**{VALUE: values})


def spiked(scenario: Scenario, role: str) -> tuple[Scenario, pd.Timestamp]:
    """The scenario with the event value of `role` multiplied by ten, and its `known_at`."""
    frame = scenario.frames[role]
    row, value, known_at = event_row(frame)
    return scenario.with_frame(role, with_value(frame, row, value * 10.0)), known_at


@dataclass(frozen=True)
class Revision:
    """A scenario whose event value of one input is revised later, with its references."""

    revised: Scenario
    as_if_first: Scenario
    obs_date: pd.Timestamp
    first: float
    second: float
    revised_known: pd.Timestamp


def revise(scenario: Scenario, role: str) -> Revision:
    frame = scenario.frames[role]
    row, first, first_known = event_row(frame)
    second = first * 1.5
    revision = with_value(frame, row, second).iloc[[row]]
    revision = revision.assign(**{KNOWN_AT: revision[KNOWN_AT] + REVISION_DELAY})
    return Revision(
        revised=scenario.with_frame(role, pd.concat([frame, revision], ignore_index=True)),
        as_if_first=scenario.with_frame(role, with_value(frame, row, second)),
        obs_date=pd.Timestamp(frame[OBS_DATE].iloc[row]),
        first=first,
        second=second,
        revised_known=first_known + REVISION_DELAY,
    )


def spike_leaks_pit(
    target: Target, plain: Scenario, with_spike: Scenario, known_at: pd.Timestamp
) -> list[pd.Timestamp]:
    """Moments before the spike was known at which the point-in-time value differs."""
    return [
        t
        for t in moments_before(known_at)
        if not same(target.at(plain, t), target.at(with_spike, t))
    ]


def differing(plain: pd.Series, other: pd.Series, dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
    return [d for d in dates if not same(float(plain[d]), float(other[d]))]


def spike_leaks_full(
    plain: pd.Series, spike: pd.Series, known_at: pd.Timestamp
) -> list[pd.Timestamp]:
    """Decision dates before the spike was known at which the full-history value differs."""
    dates = pd.DatetimeIndex(plain.index)
    return differing(plain, spike, dates[decision_time(dates) < known_at])


def spike_reactions(
    plain: pd.Series, spike: pd.Series, known_at: pd.Timestamp
) -> list[pd.Timestamp]:
    """Decision dates from the spike's `known_at` on at which the full-history value differs."""
    dates = pd.DatetimeIndex(plain.index)
    return differing(plain, spike, dates[decision_time(dates) >= known_at])


def truncation_leaks(
    target: Target, scenario: Scenario, full: pd.Series, cuts: list[pd.Timestamp]
) -> list[pd.Timestamp]:
    """Cut dates at which the full-history value differs from the truncated-history value."""
    moments = decision_time(pd.DatetimeIndex(cuts))
    return [
        cut
        for cut, moment in zip(cuts, moments, strict=True)
        if not same(float(full[cut]), target.at(scenario, moment))
    ]


def revision_leaks(target: Target, scenario: Scenario, revision: Revision) -> list[pd.Timestamp]:
    """Moments at which the feature on revised inputs differs from what was known then.

    Before the revision is known the feature must equal its value on the unrevised inputs;
    from then on, its value on inputs that held the revised value from the start.
    """
    before = [
        t
        for t in moments_before(revision.revised_known)
        if not same(target.at(revision.revised, t), target.at(scenario, t))
    ]
    after = [
        t
        for t in moments_from(revision.revised_known)
        if not same(target.at(revision.revised, t), target.at(revision.as_if_first, t))
    ]
    return before + after
