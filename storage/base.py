"""
Storage abstraction base classes and utilities for Airodrone LMS.
Enables pluggable persistent storage backends (Local, Hostinger SFTP/FTPS, Cloudflare R2 / S3 / Bunny).
"""

import os
import re
import time
from abc import ABC, abstractmethod
from typing import Generator, Optional, Tuple
from werkzeug.utils import secure_filename


def sanitize_storage_filename(original_name: str, prefix: str = "vid") -> str:
    """
    Sanitize and create a collision-resistant, safe filename.
    Prevents path traversal and arbitrary filesystem attacks.
    """
    clean_name = secure_filename(original_name) or "lesson_video.mp4"
    # Ensure standard alphanumeric and basic chars only
    clean_name = re.sub(r"[^a-zA-Z0-9._-]", "_", clean_name)
    timestamp = int(time.time())
    if prefix:
        return f"{prefix}_{timestamp}_{clean_name}"
    return f"{timestamp}_{clean_name}"


def build_video_storage_path(course_id: int, module_id: int, safe_filename: str) -> str:
    """
    Builds the standardized hierarchical storage path:
    videos/course_<course_id>/module_<module_id>/<safe_filename>
    """
    # Prevent path traversal
    safe_filename = os.path.basename(safe_filename)
    return f"videos/course_{int(course_id)}/module_{int(module_id)}/{safe_filename}"


class BaseStorageBackend(ABC):
    """
    Abstract interface for video storage operations.
    """

    @abstractmethod
    def save_video(
        self,
        file_obj,
        course_id: int,
        module_id: int,
        original_filename: str,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Save a video file to persistent storage.

        Args:
            file_obj: File-like object (e.g. Werkzeug FileStorage or file handle)
            course_id: Parent course ID
            module_id: Parent module ID
            original_filename: Original client filename

        Returns:
            (success: bool, storage_path_or_msg: str, error_message: Optional[str])
        """
        pass

    @abstractmethod
    def delete_video(self, storage_path: str) -> bool:
        """
        Delete a video from persistent storage.

        Args:
            storage_path: Relative storage path (e.g. 'videos/course_1/module_2/vid_1.mp4')

        Returns:
            True if deleted or did not exist, False on failure.
        """
        pass

    @abstractmethod
    def video_exists(self, storage_path: str) -> bool:
        """
        Check if video exists in storage.
        """
        pass

    @abstractmethod
    def get_video_size(self, storage_path: str) -> Optional[int]:
        """
        Get the size in bytes of the stored video.
        """
        pass

    @abstractmethod
    def open_video_stream(
        self,
        storage_path: str,
        start_byte: int = 0,
        length: Optional[int] = None,
        chunk_size: int = 65536,
    ) -> Generator[bytes, None, None]:
        """
        Open a memory-efficient generator yielding chunks for HTTP byte-range streaming.

        Args:
            storage_path: Relative storage path
            start_byte: Starting byte offset
            length: Number of bytes to stream (or None for till EOF)
            chunk_size: Chunk buffer size in bytes (default 64 KB)

        Yields:
            bytes chunks
        """
        pass

    @abstractmethod
    def get_video_url(self, storage_path: str) -> Optional[str]:
        """
        Return public or signed URL if storage supports direct serving, or None if served via stream proxy.
        """
        pass

