"""Accounts: self sign-up, sign-in, and who owns what.

Every endpoint outside /auth needs a signed-in user, and every run,
assessment and Salesforce connection belongs to the user who created it.
Someone else's id answers 404, never 403, so ids can't be probed to learn
what exists.

The session is an opaque random token in an HttpOnly cookie
(config.SESSION_COOKIE). The registry stores only its SHA-256 hash.
"""
from .deps import current_user, require_owner

__all__ = ["current_user", "require_owner"]
