import logging
from enum import StrEnum
from typing import Any


class StatusFormatter(logging.Formatter):
    FILL = 65

    def formatMessage(self, record: logging.LogRecord) -> str:
        tag: Any | None = getattr(record, "status", None)
        if tag:
            record: logging.LogRecord = logging.makeLogRecord(record.__dict__)
            record.message = (
                record.message.ljust(StatusFormatter.FILL, ".") + f"[{tag}]"
            )
        return super().formatMessage(record=record)


class _Status(StrEnum):
    START = "START"
    DONE = "DONE"
    FAILED = "FAILED"


START = {"status": "START"}
DONE = {"status": "DONE"}
FAILED = {"status": "FAILED"}
