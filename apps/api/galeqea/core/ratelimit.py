"""Request rate limiting (slowapi). Only routes that opt in with ``@limiter.limit``
are throttled; the login route uses it to cap password guessing per client IP, on
top of the per-account lockout in ``security``."""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=[])
