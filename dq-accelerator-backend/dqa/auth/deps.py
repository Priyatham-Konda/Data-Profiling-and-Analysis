"""FastAPI dependencies: who is signed in, and do they own what they asked for."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request

from .. import config
from ..store import registry
from . import sessions

_NOT_FOUND = "That assessment could not be found."


def current_user(request: Request) -> dict:
    """The signed-in user ({id, name, email}), or 401.

    Declared on a router and again on a handler, it still runs once per
    request: FastAPI caches a dependency's result within a request.
    """
    user = sessions.user_for(request.cookies.get(config.SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user


def require_owner(request: Request, user: dict = Depends(current_user)) -> None:
    """404 for a run or assessment in the path that belongs to someone else.

    Sits on the runs and assessments routers rather than in each handler, so
    a route added later is covered without anyone remembering to check. An
    id that doesn't exist at all is left to the handler's own 404.
    """
    params = request.path_params
    if "run_id" in params:
        _check(registry.get(params["run_id"]), user)
    if "assessment_id" in params:
        _check(registry.get_assessment(params["assessment_id"]), user)


def _check(record: Optional[dict], user: dict) -> None:
    if record is not None and record.get("owner_id") != user["id"]:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
