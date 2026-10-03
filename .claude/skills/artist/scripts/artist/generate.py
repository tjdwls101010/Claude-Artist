"""generate (skeleton)."""

from artist.errors import Failure


def __getattr__(name):
    raise Failure(f"generate.{name} is not implemented yet")
