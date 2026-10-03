"""compose (skeleton)."""

from artist.errors import Failure


def __getattr__(name):
    raise Failure(f"compose.{name} is not implemented yet")
