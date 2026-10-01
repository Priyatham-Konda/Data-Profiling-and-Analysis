"""External systems the accelerator can pull data from.

One sub-package per system -- `salesforce/` today -- so everything specific
to a system lives in one place and adding the next one touches nothing
else. See README.md in this directory for what each system provides.

The rest of the engine never imports a system directly. It asks this module
for the system by the `source.type` a request names, and gets back an object
exposing `plan()`, which turns a selection of objects into an
AssessmentPlan the generic assessment code can run.
"""
from __future__ import annotations

from types import ModuleType

from .base import AssessmentPlan, ConnectorError, ExtractionError, SourceObject

__all__ = [
    "AssessmentPlan",
    "ConnectorError",
    "ExtractionError",
    "SourceObject",
    "source_for",
    "routers",
]


def _systems() -> dict[str, ModuleType]:
    # Imported lazily so that importing dqa.connectors for its base types
    # doesn't pull in every system's HTTP stack.
    from .salesforce import source as salesforce_source

    return {"salesforce": salesforce_source}


def source_for(source_type: str) -> ModuleType:
    """The system module for a `source.type`, or ConnectorError if unknown."""
    systems = _systems()
    if source_type not in systems:
        known = ", ".join(sorted(systems))
        raise ConnectorError(
            "unknown_source",
            f"'{source_type}' isn't a supported source. Supported: {known}.",
            http_status=400,
        )
    return systems[source_type]


def routers() -> list:
    """Every system's API router, for the app to mount."""
    from .salesforce.routes import router as salesforce_router

    return [salesforce_router]
