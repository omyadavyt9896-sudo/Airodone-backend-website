"""
Unit tests for the Airodrone LMS Storage Abstraction Layer.
Tests LocalStorageBackend, HostingerStorageBackend configuration/validation,
path sanitization, chunked range streaming, and backend factory.
"""

import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import storage
from storage.base import sanitize_storage_filename, build_video_storage_path
from storage.local import LocalStorageBackend
from storage.hostinger import HostingerStorageBackend


class TestStorageBackend(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="airodrone_test_storage_")
        self.local_backend = LocalStorageBackend(base_dir=self.temp_dir)
        storage.reset_storage()

    def tearDown(self):
        storage.reset_storage()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_path_sanitization_and_structure(self):
        """Verify filename sanitization prevents path traversal and produces standard paths."""
        dirty_name = "../../../etc/passwd_exploit.mp4"
        clean = sanitize_storage_filename(dirty_name, prefix="vid_10_20")
        self.assertNotIn("..", clean)
        self.assertNotIn("/", clean)
        self.assertNotIn("\\", clean)
        self.assertTrue(clean.startswith("vid_10_20_"))
        self.assertTrue(clean.endswith("passwd_exploit.mp4"))

        storage_path = build_video_storage_path(course_id=5, module_id=12, safe_filename=clean)
        self.assertEqual(storage_path, f"videos/course_5/module_12/{clean}")

    def test_02_local_storage_save_and_retrieve(self):
        """Verify saving and retrieving video on LocalStorageBackend."""
        dummy_content = b"TEST_VIDEO_PAYLOAD_CHUNK_DATA" * 50
        dummy_file = io.BytesIO(dummy_content)

        success, rel_path, err = self.local_backend.save_video(
            file_obj=dummy_file,
            course_id=1,
            module_id=2,
            original_filename="intro_flight.mp4",
        )
        self.assertTrue(success)
        self.assertIsNone(err)
        self.assertTrue(rel_path.startswith("videos/course_1/module_2/"))
        self.assertTrue(rel_path.endswith("intro_flight.mp4"))

        # Verify existence and size
        self.assertTrue(self.local_backend.video_exists(rel_path))
        self.assertEqual(self.local_backend.get_video_size(rel_path), len(dummy_content))

    def test_03_local_storage_chunked_streaming(self):
        """Verify memory-efficient chunk generator for HTTP byte ranges."""
        dummy_content = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 10  # 360 bytes
        dummy_file = io.BytesIO(dummy_content)

        success, rel_path, _ = self.local_backend.save_video(
            dummy_file, course_id=3, module_id=4, original_filename="stream_test.mp4"
        )
        self.assertTrue(success)

        # Full stream
        full_chunks = list(self.local_backend.open_video_stream(rel_path, start_byte=0, length=None, chunk_size=64))
        full_streamed = b"".join(full_chunks)
        self.assertEqual(full_streamed, dummy_content)

        # Byte Range stream (e.g. bytes 10-49 = 40 bytes)
        range_chunks = list(self.local_backend.open_video_stream(rel_path, start_byte=10, length=40, chunk_size=16))
        range_streamed = b"".join(range_chunks)
        self.assertEqual(range_streamed, dummy_content[10:50])
        self.assertEqual(len(range_streamed), 40)

    def test_04_local_storage_delete(self):
        """Verify video deletion from local storage."""
        dummy_file = io.BytesIO(b"TO_BE_DELETED")
        success, rel_path, _ = self.local_backend.save_video(
            dummy_file, course_id=1, module_id=1, original_filename="delete_me.mp4"
        )
        self.assertTrue(self.local_backend.video_exists(rel_path))

        del_res = self.local_backend.delete_video(rel_path)
        self.assertTrue(del_res)
        self.assertFalse(self.local_backend.video_exists(rel_path))

    def test_05_hostinger_storage_unconfigured_safety(self):
        """Verify Hostinger backend gracefully handles unconfigured credentials without crash."""
        hostinger_backend = HostingerStorageBackend(
            host="", username="", password=""
        )
        self.assertFalse(hostinger_backend.is_configured())

        dummy_file = io.BytesIO(b"HOSTINGER_TEST")
        success, rel_path, err = hostinger_backend.save_video(
            dummy_file, course_id=1, module_id=1, original_filename="test.mp4"
        )
        self.assertFalse(success)
        self.assertEqual(rel_path, "")
        self.assertIn("credentials not configured", err)

    def test_06_storage_factory_selection(self):
        """Verify storage factory returns LocalStorageBackend by default and switches based on env."""
        with patch.dict(os.environ, {"STORAGE_BACKEND": "local"}, clear=False):
            storage.reset_storage()
            backend = storage.get_storage()
            self.assertIsInstance(backend, LocalStorageBackend)

        with patch.dict(
            os.environ,
            {
                "STORAGE_BACKEND": "hostinger",
                "HOSTINGER_SFTP_HOST": "ftp.example.com",
                "HOSTINGER_SFTP_USERNAME": "test_user",
                "HOSTINGER_SFTP_PASSWORD": "test_password",
            },
            clear=False,
        ):
            storage.reset_storage()
            backend = storage.get_storage()
            self.assertIsInstance(backend, HostingerStorageBackend)
            self.assertTrue(backend.is_configured())


if __name__ == "__main__":
    unittest.main()

