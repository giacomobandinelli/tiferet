"""Errors raised by the source adapters and the silver writer (decision 0013).

Both are raised before anything is written, so a failed ingestion leaves the stored silver
tables and the bronze manifest as they were.
"""


class SourceFormatError(ValueError):
    """A downloaded file does not have the structure its parser expects."""

    def __init__(self, source: str, detail: str) -> None:
        super().__init__(f"{source}: {detail}")
        self.source = source
        self.detail = detail


class DataLossError(ValueError):
    """A new download would remove history that the stored series already holds."""

    def __init__(self, series_id: str, detail: str) -> None:
        super().__init__(f"{series_id}: {detail}")
        self.series_id = series_id
        self.detail = detail
