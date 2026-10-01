"""The /salesforce endpoints: connect, inspect, list objects, disconnect.

Plain `def` handlers on purpose: each makes blocking HTTP calls to
Salesforce, and FastAPI runs sync handlers in its thread pool, so a slow org
never stalls the event loop that serves the polling endpoints.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..base import ConnectorError
from .connections import STORE, connect
from .source import objects_listing

router = APIRouter(prefix="/salesforce", tags=["salesforce"])


def _error(exc: ConnectorError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.to_api())


def _not_found() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "errorCode": "not_found",
            "error": "That Salesforce connection doesn't exist, or the server has "
                     "restarted since. Connect again.",
        },
    )


class ConnectionRequest(BaseModel):
    instanceUrl: str = Field(..., min_length=1)
    clientId: str = Field(..., min_length=1)
    clientSecret: str = Field(..., min_length=1, repr=False)


@router.post("/connections", status_code=201)
def create_connection(payload: ConnectionRequest) -> Any:
    try:
        connection = connect(payload.instanceUrl, payload.clientId, payload.clientSecret)
    except ConnectorError as exc:
        return _error(exc)
    return connection.to_api()


@router.get("/connections/{connection_id}")
def get_connection(connection_id: str) -> Any:
    connection = STORE.get(connection_id)
    if connection is None:
        return _not_found()
    return connection.to_api()


@router.get("/connections/{connection_id}/objects")
def list_objects(connection_id: str, include: str | None = Query(None)) -> Any:
    connection = STORE.get(connection_id)
    if connection is None:
        return _not_found()
    if connection.state != "connected":
        return JSONResponse(
            status_code=409,
            content={
                "errorCode": "not_connected",
                "error": "This Salesforce connection has closed. Connect again to "
                         "choose objects.",
            },
        )
    try:
        return objects_listing(connection, include_all=(include == "all"))
    except ConnectorError as exc:
        return _error(exc)


@router.delete("/connections/{connection_id}")
def delete_connection(connection_id: str) -> Any:
    connection = STORE.get(connection_id)
    if connection is None:
        return _not_found()
    if connection.state == "connected" and connection.assessment_id:
        return JSONResponse(
            status_code=409,
            content={
                "errorCode": "in_use",
                "error": "An assessment is still downloading from this connection. "
                         "Delete the assessment instead, which stops the download.",
            },
        )
    STORE.close(connection_id, "disconnected")
    return Response(status_code=204)
