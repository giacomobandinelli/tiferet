"""SQL counterpart of `view.align`: place per-observation results on decision dates.

A SQL feature receives one relation with columns `input, obs_date, known_at, value, as_of`,
built by `tiferet.data.pit_sql.inputs`. `aligned_query` wraps a per-observation query in the
shared alignment: business-day decision dates from the first Berlin date on which any input
became known to the Berlin date of `as_of`, each joined to the latest result available by
23:59:59 Berlin time on that date.
"""

from tiferet.data.store import check_series_id

INPUTS = "inputs"
"""Name under which a feature's query refers to its input relation."""

_ALIGNED = """
WITH computed_rows AS (
{computed}
),
computed AS (
    SELECT available_at, value FROM computed_rows
    QUALIFY row_number() OVER (PARTITION BY available_at ORDER BY obs_date DESC) = 1
),
bounds AS (
    SELECT min((known_at AT TIME ZONE 'Europe/Berlin')::DATE) AS first_date,
           max((as_of AT TIME ZONE 'Europe/Berlin')::DATE) AS last_date
    FROM inputs
),
dates AS (
    SELECT day::DATE AS date,
           timezone('Europe/Berlin', day::TIMESTAMP + INTERVAL '23:59:59') AS decision_time
    FROM bounds, generate_series(first_date::TIMESTAMP, last_date::TIMESTAMP, INTERVAL 1 DAY)
        AS days(day)
    WHERE isodow(day) < 6
)
SELECT dates.date, computed.value
FROM dates ASOF LEFT JOIN computed ON dates.decision_time >= computed.available_at
ORDER BY dates.date
"""


def input_rows(role: str) -> str:
    """Query of one input's rows, with each row's availability as the running max of known_at.

    The result has columns `obs_date, value, available_at`.
    """
    return f"""
    SELECT obs_date, value,
           max(known_at) OVER (ORDER BY obs_date ROWS UNBOUNDED PRECEDING) AS available_at
    FROM {INPUTS} WHERE input = '{check_series_id(role)}'
    """


def aligned_query(computed: str) -> str:
    """Wrap `computed` (columns `obs_date, available_at, value`) in the decision-date alignment."""
    return _ALIGNED.format(computed=computed)
