"""record (skeleton)."""

from artist.errors import Failure


def __getattr__(name):
    raise Failure(f"record.{name} is not implemented yet")
