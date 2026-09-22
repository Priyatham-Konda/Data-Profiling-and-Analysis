"""FastAPI application."""
from __future__ import annotations

import logging
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import config
from ..store import registry
from .routes import router

logging.basicConfig(
    level=os.environ.get("DQA_LOG_LEVEL", "INFO"),
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)
log = logging.getLogger("dqa")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    registry.init()
    # Fail fast on a malformed rule pack rather than on the first upload.
    from ..rules.loader import load_pack

    log.info("loaded %d rule templates", len(load_pack()))
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        lifespan=lifespan,
        title="DQ Accelerator API",
        version="1.0.0",
        description="Data quality assessment for arbitrary CSV exports.",
    )

    origins = os.environ.get(
        "DQA_CORS_ORIGINS",
        "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173",
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix=os.environ.get("DQA_API_PREFIX", "/api"))

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "dimensions": config.DIMENSIONS}

    # Every error the frontend sees must carry {"error": "..."} with a
    # human-readable message, never a stack trace.
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "Something went wrong."
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        message = first.get("msg", "The request was not valid.")
        return JSONResponse(
            status_code=400,
            content={"error": f"{field}: {message}" if field else message},
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "Something went wrong while handling that request."},
        )

    return app


app = create_app()
