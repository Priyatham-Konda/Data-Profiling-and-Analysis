"""The /auth endpoints: sign up, sign in, sign out, and who am I.

Plain `def` handlers on purpose: hashing a password deliberately takes tens
of milliseconds of CPU, and FastAPI runs sync handlers in its thread pool,
so a burst of sign-ins never stalls the event loop that serves the polls.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .. import config
from ..store import registry
from . import passwords, sessions
from .deps import current_user

router = APIRouter(prefix="/auth", tags=["auth"])

# Deliberately loose: the one way to know an address works is to send it
# mail, and this only has to catch typing mistakes.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def _public(user: dict) -> dict:
    """The only user shape that leaves the server."""
    return {"id": user["id"], "name": user["name"], "email": user["email"]}


def _signed_in(user: dict, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content=_public(user))
    sessions.start(user["id"], response)
    return response


# Length limits only stop absurd payloads; the messages a person actually
# sees come from the checks in the handlers.
class SignUpRequest(BaseModel):
    name: str = Field(..., max_length=200)
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=1024, repr=False)


class SignInRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=1024, repr=False)


@router.post("/signup", status_code=201)
def sign_up(payload: SignUpRequest) -> Any:
    name = payload.name.strip()
    email = payload.email.strip().lower()
    if not name:
        return _error(400, "Enter your name.")
    if not _EMAIL.match(email):
        return _error(400, "Enter a valid email address.")
    if len(payload.password) < config.PASSWORD_MIN_LENGTH:
        return _error(
            400, f"Use a password of at least {config.PASSWORD_MIN_LENGTH} characters."
        )

    user = registry.create_user(
        f"usr_{uuid.uuid4().hex[:12]}", name, email,
        passwords.hash_password(payload.password),
    )
    if user is None:
        return _error(409, "An account with this email already exists. Sign in instead.")
    return _signed_in(user, status_code=201)


@router.post("/login")
def sign_in(payload: SignInRequest) -> Any:
    user = registry.user_by_email(payload.email.strip().lower())
    stored = user["password_hash"] if user else passwords.dummy_hash()
    # One message for both an unknown email and a wrong password, so the
    # form can't be used to find out who has an account.
    if not passwords.verify_password(payload.password, stored) or user is None:
        return _error(401, "That email and password don't match an account.")
    return _signed_in(user)


@router.post("/logout")
def sign_out(request: Request) -> Any:
    # Succeeds even without a session: signing out must never be the thing
    # that fails and leaves someone stuck signed in.
    response = Response(status_code=204)
    sessions.end(request.cookies.get(config.SESSION_COOKIE), response)
    return response


@router.get("/me")
def me(user: dict = Depends(current_user)) -> Any:
    return _public(user)
