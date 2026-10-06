"""Structured failures for static M3 protocol analysis."""

from __future__ import annotations


class M3AnalysisError(Exception):
    """A malformed or unsupported input with stable byte location context."""

    def __init__(self, message: str, *, source: str, offset: int) -> None:
        self.message = message
        self.source = source
        self.offset = offset
        super().__init__(f"source={source!r}, offset=0x{offset:X}: {message}")
