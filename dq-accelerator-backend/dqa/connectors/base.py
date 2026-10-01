"""The contract every external system implements.

Deliberately small. A system's job ends once an object's records are on disk
as a CSV: from there the object is an ordinary run and goes through exactly
the pipeline an uploaded file does. That is what keeps the upload path and
every external system producing comparable scores.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# progress(fraction 0-1 of the download, detail shown to the user verbatim)
ProgressFn = Callable[[float, str], None]
# Raises the pipeline's cancellation exception if the run was deleted.
CheckCancelledFn = Callable[[], None]
# Download one object to `dest` as CSV; returns metadata stored with the run.
Extractor = Callable[[Path, ProgressFn, CheckCancelledFn], dict]


class ConnectorError(Exception):
    """A failure the user should see, with a code the frontend branches on.

    `message` is shown verbatim, so it is written for a person and says what
    to do next, not what went wrong internally.
    """

    def __init__(self, code: str, message: str, http_status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def to_api(self) -> dict:
        return {"errorCode": self.code, "error": self.message}


class ExtractionError(Exception):
    """Downloading one object failed. Shown as that object's run `error`."""


@dataclass
class SourceObject:
    name: str
    label: str


@dataclass
class AssessmentPlan:
    """Everything the generic assessment code needs from a system.

    `extractor(name)` returns the function that downloads one object.
    `close(reason)` is called once when no further access to the system is
    needed -- after the last download, or when the assessment is deleted --
    and must release every credential the system is holding.
    """
    name: str
    source: dict
    objects: list[SourceObject]
    extractor: Callable[[str], Extractor]
    close: Callable[[str], None]
