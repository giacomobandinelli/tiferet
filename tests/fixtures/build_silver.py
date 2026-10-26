"""Write the synthetic silver Parquet fixtures used by `tests/data/test_store.py`.

Run from the repository root with `uv run python tests/fixtures/build_silver.py`. The series
`synthetic_cpi` is monthly and invented: three development rows (one of them a revision) in
`silver/dev/` and one row that became known in January 2023 in `silver/sealed/`, the stand-in
for the holdout partition.
"""

from pathlib import Path

import pandas as pd

from tiferet.data.schema import validate_silver

FIXTURES = Path(__file__).resolve().parent / "silver"
SERIES = "synthetic_cpi"


def _frame(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    obs, known, value = zip(*rows, strict=True)
    frame = pd.DataFrame(
        {
            "series_id": SERIES,
            "obs_date": pd.to_datetime(list(obs)),
            "known_at": pd.to_datetime(list(known), utc=True),
            "value": list(value),
        }
    )
    return validate_silver(frame)


DEVELOPMENT = [
    ("2022-10-01", "2022-11-15 07:00:00", 100.0),
    ("2022-11-01", "2022-12-15 07:00:00", 101.0),
    ("2022-10-01", "2022-12-15 07:00:00", 100.5),
]
SEALED = [("2022-12-01", "2023-01-16 07:00:00", 102.0)]


def main() -> None:
    for folder, rows in (("dev", DEVELOPMENT), ("sealed", SEALED)):
        (FIXTURES / folder).mkdir(parents=True, exist_ok=True)
        _frame(rows).to_parquet(FIXTURES / folder / f"{SERIES}.parquet", index=False)


if __name__ == "__main__":
    main()
