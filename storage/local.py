"""
Local filesystem storage backend for development and test environments.
"""

import logging
import os
import shutil
from typing import Generator, Optional, Tuple

from storage.base import (
    BaseStorageBackend,
    build_video_storage_path,
    sanitize_storage_filename,
)

logger = logging.getLogger("storage.local")


class LocalStorageBackend(BaseStorageBackend):
    """
    Local filesystem video storage backend.
    """

    def __init__(self, base_dir: Optional[str] = None):
        """
        Args:
            base_dir: Root directory for local uploads (defaults to project's uploads folder)
        """
        if base_dir:
            self.base_dir = os.path.abspath(base_dir)
        else:
            # Default to uploads/ in project root
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            self.base_dir = os.path.join(project_root, "uploads")
        
        self.videos_dir = os.path.join(self.base_dir, "videos")
        os.makedirs(self.videos_dir, exist_ok=True)

    def _resolve_full_path(self, storage_path: str) -> str:
        """
        Resolves a storage path to a local absolute filesystem path.
        Handles both hierarchical paths ('videos/course_1/module_2/vid_1.mp4')
        and legacy flat filenames ('vid_1_1720000000_intro.mp4').
        """
        if not storage_path:
            return ""

        if os.path.isabs(storage_path):
            return storage_path

        # If path starts with 'videos/', resolve relative to base_dir
        clean_path = storage_path.replace("\\", "/")
        if clean_path.startswith("videos/"):
            return os.path.join(self.base_dir, clean_path)

        # Hierarchical direct or legacy fallback in self.videos_dir
        direct_in_videos = os.path.join(self.videos_dir, clean_path)
        if os.path.exists(direct_in_videos):
            return direct_in_videos

        # Fallback relative to base_dir
        return os.path.join(self.base_dir, clean_path)

    def save_video(
        self,
        file_obj,
        course_id: int,
        module_id: int,
        original_filename: str,
    ) -> Tuple[bool, str, Optional[str]]:
        try:
            safe_name = sanitize_storage_filename(original_filename, prefix=f"vid_{course_id}_{module_id}")
            rel_storage_path = build_video_storage_path(course_id, module_id, safe_name)
            full_path = self._resolve_full_path(rel_storage_path)

            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            if hasattr(file_obj, "save"):
                file_obj.save(full_path)
            elif hasattr(file_obj, "read"):
                file_obj.seek(0)
                with open(full_path, "wb") as f_out:
                    shutil.copyfileobj(file_obj, f_out)
            else:
                return False, "", "Invalid file object provided for local storage."

            file_size = os.path.getsize(full_path)
            logger.info(f"Saved video locally: {rel_storage_path} ({file_size} bytes)")
            return True, rel_storage_path, None
        except Exception as e:
            logger.error(f"Failed to save video locally: {e}", exc_info=True)
            return False, "", f"Local storage save error: {str(e)}"

    def delete_video(self, storage_path: str) -> bool:
        if not storage_path:
            return True
        try:
            full_path = self._resolve_full_path(storage_path)
            if os.path.exists(full_path) and os.path.isfile(full_path):
                os.remove(full_path)
                logger.info(f"Deleted local video: {storage_path}")
            return True
        except Exception as e:
            logger.error(f"Error deleting local video {storage_path}: {e}")
            return False

    def video_exists(self, storage_path: str) -> bool:
        if not storage_path:
            return False
        full_path = self._resolve_full_path(storage_path)
        return os.path.exists(full_path) and os.path.isfile(full_path)

    def get_video_size(self, storage_path: str) -> Optional[int]:
        if not storage_path:
            return None
        full_path = self._resolve_full_path(storage_path)
        if os.path.exists(full_path) and os.path.isfile(full_path):
            return os.path.getsize(full_path)
        return None

    def open_video_stream(
        self,
        storage_path: str,
        start_byte: int = 0,
        length: Optional[int] = None,
        chunk_size: int = 65536,
    ) -> Generator[bytes, None, None]:
        full_path = self._resolve_full_path(storage_path)
        if not os.path.exists(full_path):
            return

        total_size = os.path.getsize(full_path)
        if start_byte >= total_size:
            return

        bytes_remaining = total_size - start_byte
        if length is not None:
            bytes_remaining = min(bytes_remaining, length)

        with open(full_path, "rb") as f:
            f.seek(start_byte)
            while bytes_remaining > 0:
                read_len = min(chunk_size, bytes_remaining)
                data = f.read(read_len)
                if not data:
                    break
                bytes_remaining -= len(data)
                yield data

    def get_video_url(self, storage_path: str) -> Optional[str]:
        # Local storage is protected and streamed via the LMS /courses/video/<id>/stream endpoint
        return None

    def _save_image_file(
        self,
        file_obj,
        subfolder: str,
        filename_prefix: str,
        entity_id: int,
        original_filename: str,
    ) -> Tuple[bool, str, Optional[str]]:
        try:
            import time
            import secrets
            ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else "jpg"
            if ext not in ("jpg", "jpeg", "png", "webp"):
                ext = "jpg"
            random_token = secrets.token_hex(4)
            safe_name = f"{filename_prefix}_{int(entity_id)}_{int(time.time())}_{random_token}.{ext}"
            rel_storage_path = f"uploads/{subfolder}/{safe_name}"

            full_path_base = os.path.join(self.base_dir, subfolder, safe_name)
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            full_path_static = os.path.join(project_root, "static", "uploads", subfolder, safe_name)

            os.makedirs(os.path.dirname(full_path_base), exist_ok=True)
            os.makedirs(os.path.dirname(full_path_static), exist_ok=True)

            if hasattr(file_obj, "save"):
                file_obj.save(full_path_static)
                try:
                    shutil.copy2(full_path_static, full_path_base)
                except Exception:
                    pass
            elif hasattr(file_obj, "read"):
                file_obj.seek(0)
                with open(full_path_static, "wb") as f_out:
                    shutil.copyfileobj(file_obj, f_out)
                try:
                    shutil.copy2(full_path_static, full_path_base)
                except Exception:
                    pass
            else:
                return False, "", "Invalid file object provided for local storage."

            logger.info(f"Saved image locally: {rel_storage_path}")
            return True, rel_storage_path, None
        except Exception as e:
            logger.error(f"Failed to save image locally: {e}", exc_info=True)
            return False, "", f"Local storage save error: {str(e)}"

    def _delete_image_file(self, storage_path: str, subfolder: str) -> bool:
        if not storage_path:
            return True
        try:
            clean_path = storage_path.replace("\\", "/").lstrip("/")
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            filename = os.path.basename(clean_path)
            paths_to_check = [
                os.path.join(project_root, "static", clean_path),
                os.path.join(project_root, "static", "uploads", subfolder, filename),
                os.path.join(self.base_dir, subfolder, filename),
                os.path.join(self.base_dir, clean_path),
            ]
            for p in paths_to_check:
                if os.path.exists(p) and os.path.isfile(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            return True
        except Exception as e:
            logger.warning(f"Could not delete local image '{storage_path}': {e}")
            return False

    def _image_file_exists(self, storage_path: str, subfolder: str) -> bool:
        if not storage_path:
            return False
        clean_path = storage_path.replace("\\", "/").lstrip("/")
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        filename = os.path.basename(clean_path)
        paths_to_check = [
            os.path.join(project_root, "static", clean_path),
            os.path.join(project_root, "static", "uploads", subfolder, filename),
            os.path.join(self.base_dir, subfolder, filename),
            os.path.join(self.base_dir, clean_path),
        ]
        return any(os.path.exists(p) and os.path.isfile(p) for p in paths_to_check)

    def save_category_image(self, file_obj, category_id: int, original_filename: str) -> Tuple[bool, str, Optional[str]]:
        return self._save_image_file(file_obj, "categories", "category", category_id, original_filename)

    def delete_category_image(self, storage_path: str) -> bool:
        return self._delete_image_file(storage_path, "categories")

    def category_image_exists(self, storage_path: str) -> bool:
        return self._image_file_exists(storage_path, "categories")

    def save_learning_path_image(self, file_obj, path_id: int, original_filename: str) -> Tuple[bool, str, Optional[str]]:
        return self._save_image_file(file_obj, "learning_paths", "learning_path", path_id, original_filename)

    def delete_learning_path_image(self, storage_path: str) -> bool:
        return self._delete_image_file(storage_path, "learning_paths")

    def learning_path_image_exists(self, storage_path: str) -> bool:
        return self._image_file_exists(storage_path, "learning_paths")

    def save_course_image(self, file_obj, course_id: int, original_filename: str) -> Tuple[bool, str, Optional[str]]:
        return self._save_image_file(file_obj, "courses", "course", course_id, original_filename)

    def delete_course_image(self, storage_path: str) -> bool:
        return self._delete_image_file(storage_path, "courses")

    def course_image_exists(self, storage_path: str) -> bool:
        return self._image_file_exists(storage_path, "courses")

    def save_catalogue_hero_image(self, file_obj, original_filename: str) -> Tuple[bool, str, Optional[str]]:
        return self._save_image_file(file_obj, "catalogue", "catalogue_hero", 0, original_filename)

    def delete_catalogue_hero_image(self, storage_path: str) -> bool:
        return self._delete_image_file(storage_path, "catalogue")

    def catalogue_hero_image_exists(self, storage_path: str) -> bool:
        return self._image_file_exists(storage_path, "catalogue")



