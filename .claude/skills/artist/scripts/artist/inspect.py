"""inspect (skeleton)."""

from artist.errors import Failure


def __getattr__(name):
    raise Failure(f"inspect.{name} is not implemented yet")
