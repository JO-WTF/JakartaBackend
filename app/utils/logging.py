"""Shared logging utilities for the Jakarta backend."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.settings import settings

__all__ = ["logger", "dn_sync_logger", "DN_SYNC_LOG_PATH", "flush_dn_sync_log"]

# Use the uvicorn error logger so messages integrate with the application logs.
logger = logging.getLogger("uvicorn.error")

DN_SYNC_LOG_PATH = Path(os.getenv("DN_SYNC_LOG_PATH", "/tmp/dn_sync.log")).expanduser()
DN_SYNC_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_dn_sync_file_handler: logging.FileHandler | None = None


class _DnSyncLogFilter(logging.Filter):
    """Filter to keep only DN sync logs."""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        return bool(getattr(record, "dn_sync", False))


def _configure_dn_sync_logger(base_logger: logging.Logger) -> logging.LoggerAdapter:
    global _dn_sync_file_handler

    if _dn_sync_file_handler is None or getattr(_dn_sync_file_handler, "baseFilename", None) != str(DN_SYNC_LOG_PATH):
        handler = logging.FileHandler(DN_SYNC_LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        handler.setLevel(logging.DEBUG)
        handler.addFilter(_DnSyncLogFilter())
        base_logger.addHandler(handler)
        _dn_sync_file_handler = handler

    return logging.LoggerAdapter(base_logger, {"dn_sync": True})


dn_sync_logger = _configure_dn_sync_logger(logger)

# Ensure storage path exists when using disk storage to avoid runtime surprises.
if settings.storage_driver != "s3":
    os.makedirs(settings.storage_disk_path, exist_ok=True)


def flush_dn_sync_log() -> None:
    if _dn_sync_file_handler is None:
        return
    flush = getattr(_dn_sync_file_handler, "flush", None)
    if callable(flush):
        flush()
