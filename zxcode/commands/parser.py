"""Slash-command line parser."""

from __future__ import annotations

from .model import CommandInvocation


def parse(line: str) -> CommandInvocation | None:
    """Parse a user input line.

    Returns ``None`` for non-command input. A bare ``/`` or a ``/`` followed
    by whitespace yields an invocation with an empty name, which the
    dispatcher treats as an unknown command.
    """
    stripped = line.strip()
    if not stripped.startswith("/"):
        return None
    rest = stripped[1:]
    if not rest or rest[0].isspace():
        return CommandInvocation(name="", args=rest.strip())
    name, _, args = rest.partition(" ")
    return CommandInvocation(name=name.lower(), args=args.strip())
