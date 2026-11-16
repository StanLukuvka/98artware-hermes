"""The base of every error art98 raises on purpose."""

from __future__ import annotations


class Art98Error(Exception):
    """Base of every error art98 raises on purpose, so callers and the CLI catch one thing.

    Each error keeps its built-in base second (`class StateError(Art98Error, ValueError)`),
    so `except ValueError` and friends still work. Plain `ValueError` and `RuntimeError`
    raised for a programming mistake are not Art98Errors: they keep their traceback.
    """


class UsageError(Art98Error, ValueError):
    """A mistake in what the caller passed in: a scene author's argument to an art98 call."""


class NotFound(UsageError, KeyError):
    """The caller named something that isn't there: a window id, a menu label, a child window.

    It is a `KeyError` and so a `LookupError` too, as the lookups it replaces were.
    """

    __str__ = Exception.__str__  # KeyError would put the message in quotes


class GuestError(Art98Error, RuntimeError):
    """The guest did something art98 can't control or expected otherwise, so the step failed."""


class MissingFile(Art98Error, FileNotFoundError):
    """A file the user named, or the setup needs, isn't there."""
