"""doctor (skeleton)."""

from artist.errors import Failure


def __getattr__(name):
    raise Failure(f"doctor.{name} is not implemented yet")
