"""Typed HTTP gateway errors shared by API routers."""

from __future__ import annotations


class GatewayError(Exception):
    """A sanitized HTTP error with a stable machine-readable code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = str(code)
        self.message = str(message)


__all__ = ["GatewayError"]
