"""Shared logging utilities for the Jakarta backend."""

from __future__ import annotations

import logging

# Use the uvicorn error logger so messages integrate with the application logs.
logger = logging.getLogger("uvicorn.error")

__all__ = ["logger"]
