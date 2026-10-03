"""The one exception a unit raises when a command ran but cannot produce its result."""

from __future__ import annotations


class Failure(Exception):
    """Exit 1. `message` is what went wrong in terms the caller can act on; `detail` rides along in the JSON."""

    def __init__(self, message: str, **detail):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def payload(self) -> dict:
        return {"error": self.message, **self.detail}
