"""sheet (skeleton)."""

from artist.errors import Failure


def __getattr__(name):
    raise Failure(f"sheet.{name} is not implemented yet")
