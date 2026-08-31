"""
Storage package initialization and factory for Airodrone LMS.
"""

import logging
import os
from typing import Generator, Optional, Tuple

from storage.base import (
    BaseStorageBackend,
    build_video_storage_path,
    sanitize_storage_filename,
)
from storage.local import LocalStorageBackend
from storage.hostinger import HostingerStorageBackend

logger = logging.getLogger("storage")

_storage_instance: Optional[BaseStorageBackend] = None


def get_storage() -> BaseStorageBackend:
    """
    Factory that returns the configured active storage backend.
    
    Environment Variables:
        STORAGE_BACKEND: 'hostinger' or 'local' (default: 'local')
        HOSTINGER_STORAGE_ENABLED: 'true' / 'false'
    """
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance

    backend_type = os.environ.get("STORAGE_BACKEND", "").strip().lower()
    hostinger_enabled = os.environ.get("HOSTINGER_STORAGE_ENABLED", "").strip().lower() in ("true", "1", "yes")

    if backend_type == "hostinger" or hostinger_enabled:
        hostinger_backend = HostingerStorageBackend()
        if hostinger_backend.is_configured():
            logger.info("Initializing Hostinger Persistent Storage Backend (SFTP/FTPS)")
            _storage_instance = hostinger_backend
            return _storage_instance
        else:
            logger.warning(
                "STORAGE_BACKEND='hostinger' specified but credentials are incomplete. "
                "Falling back to LocalStorageBackend for safety."
            )

    logger.info("Initializing Local Filesystem Storage Backend (development/testing)")
    _storage_instance = LocalStorageBackend()
    return _storage_instance


def reset_storage():
    """Reset the singleton instance (useful for unit tests)."""
    global _storage_instance
    _storage_instance = None


def save_video(
    file_obj,
    course_id: int,
    module_id: int,
    original_filename: str,
) -> Tuple[bool, str, Optional[str]]:
    """Save video to active storage backend."""
    return get_storage().save_video(file_obj, course_id, module_id, original_filename)


def delete_video(storage_path: str) -> bool:
    """Delete video from active storage backend."""
    return get_storage().delete_video(storage_path)


def video_exists(storage_path: str) -> bool:
    """Check if video exists on active storage backend."""
    return get_storage().video_exists(storage_path)


def get_video_size(storage_path: str) -> Optional[int]:
    """Get video size from active storage backend."""
    return get_storage().get_video_size(storage_path)


def open_video_stream(
    storage_path: str,
    start_byte: int = 0,
    length: Optional[int] = None,
    chunk_size: int = 65536,
) -> Generator[bytes, None, None]:
    """Open video stream generator from active storage backend."""
    return get_storage().open_video_stream(storage_path, start_byte, length, chunk_size)


__all__ = [
    "BaseStorageBackend",
    "LocalStorageBackend",
    "HostingerStorageBackend",
    "get_storage",
    "reset_storage",
    "save_video",
    "delete_video",
    "video_exists",
    "get_video_size",
    "open_video_stream",
    "build_video_storage_path",
    "sanitize_storage_filename",
]

