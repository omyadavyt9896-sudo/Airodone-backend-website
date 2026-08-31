"""
Regression tests for Video Upload Resilience and MySQL Connection Recovery.
Tests large/small video uploads, database reconnect/health ping, orphan file cleanup on DB failure,
edit lifecycle, and invalid format rejection.
"""

import io
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock
from werkzeug.security import generate_password_hash

import storage
from app import app, init_db, get_db_connection, get_db_cursor, ping_or_reconnect_db


class TestVideoUploadResilience(unittest.TestCase):

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.upload_dir = tempfile.mkdtemp(prefix="airodrone_test_uploads_")

        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["DATABASE_URL"] = f"sqlite:///{self.db_path}"
        app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
        self.client = app.test_client()

        # Set storage backend to local pointing to temp upload dir
        storage.reset_storage()
        self.local_backend = storage.LocalStorageBackend(base_dir=self.upload_dir)
        storage._storage_instance = self.local_backend

        with app.app_context():
            init_db()
            self._seed_data()

    def tearDown(self):
        storage.reset_storage()
        try:
            os.close(self.db_fd)
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
        except Exception:
            pass
        if os.path.exists(self.upload_dir):
            import shutil
            shutil.rmtree(self.upload_dir, ignore_errors=True)

    def _seed_data(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # Admin user
        cur.execute(
            "INSERT INTO users (email, password_hash, name, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, %s)",
            ("admin_upload@steroaim.com", generate_password_hash("AdminPass123"), "Admin Tester", "admin", now_str)
        )
        self.admin_id = cur.lastrowid or 1

        # Course
        cur.execute(
            """
            INSERT INTO courses (title, slug, description, short_description, level, grade, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s)
            """,
            ("Aerodynamics Lab", "aerodynamics-lab", "Aero Desc", "Aero Short", "Advanced", 5, now_str, now_str)
        )
        self.course_id = cur.lastrowid or 1

        # Module
        cur.execute(
            "INSERT INTO modules (course_id, title, sequence, is_active, created_at, updated_at) VALUES (%s, %s, 1, 1, %s, %s)",
            (self.course_id, "Module 1 - Airfoil Design", now_str, now_str)
        )
        self.module_id = cur.lastrowid or 1

        conn.commit()
        cur.close()
        conn.close()

    def _login_admin(self):
        return self.client.post(
            "/login",
            data={"email": "admin_upload@steroaim.com", "password": "AdminPass123"},
            follow_redirects=True,
        )

    def test_01_small_video_upload_success(self):
        """Small video (~2.5 MB) upload succeeds and saves metadata in database."""
        self._login_admin()
        small_payload = b"X" * (250 * 1024)  # 250 KB test chunk
        data = {
            "title": "Airfoil Flow Analysis",
            "description": "Flow visualization around symmetrical airfoils",
            "duration": "05:30",
            "sequence": "1",
            "is_active": "1",
            "video_file": (io.BytesIO(small_payload), "airfoil_flow.mp4"),
        }

        res = self.client.post(
            f"/admin/modules/{self.module_id}/videos/new",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"added successfully", res.data)

        # Verify in DB
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT title, video_file FROM course_videos WHERE module_id = %s", (self.module_id,))
        video = cur.fetchone()
        cur.close()
        conn.close()

        self.assertIsNotNone(video)
        self.assertEqual(video["title"], "Airfoil Flow Analysis")
        self.assertTrue(video["video_file"].startswith(f"videos/course_{self.course_id}/module_{self.module_id}/"))
        self.assertTrue(storage.video_exists(video["video_file"]))

    def test_02_large_video_upload_simulated(self):
        """Large video upload succeeds without hanging or timing out."""
        self._login_admin()
        large_payload = b"LARGE_MP4_CHUNK_" * (100 * 1024)  # ~1.6 MB fixture
        data = {
            "title": "Full Flight Mechanics Lecture",
            "description": "Comprehensive 45-minute lesson on thrust and lift",
            "duration": "45:00",
            "sequence": "2",
            "is_active": "1",
            "video_file": (io.BytesIO(large_payload), "flight_mechanics.mp4"),
        }

        res = self.client.post(
            f"/admin/modules/{self.module_id}/videos/new",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"added successfully", res.data)

    def test_03_db_connection_ping_and_reconnect(self):
        """Verify ping_or_reconnect_db safely pings or reconnects alive connection."""
        conn = get_db_connection()
        healthy_conn = ping_or_reconnect_db(conn)
        self.assertIsNotNone(healthy_conn)

        # Test simulated MySQL connection with ping method
        mock_raw = MagicMock()
        mock_raw.ping.return_value = True
        mock_proxy = MagicMock()
        mock_proxy._raw_conn = mock_raw

        res_conn = ping_or_reconnect_db(mock_proxy)
        mock_raw.ping.assert_called_with(reconnect=True)

    def test_04_orphan_file_cleanup_on_db_insert_failure(self):
        """If database insertion fails after file save, the newly uploaded video file is cleaned up."""
        self._login_admin()
        dummy_file = io.BytesIO(b"TEST_VIDEO_ORPHAN_DATA")
        data = {
            "title": "Orphan Test Lesson",
            "sequence": "1",
            "video_file": (dummy_file, "orphan_test.mp4"),
        }

        real_get_db_cursor = app.view_functions.get("admin_add_video")
        from app import get_db_cursor as real_get_cursor

        def selective_cursor_fn(conn):
            cur = real_get_cursor(conn)
            orig_execute = cur.execute
            def failing_execute(sql, params=None):
                if "INSERT INTO course_videos" in sql:
                    raise Exception("Simulated DB Connection Disconnect on INSERT")
                return orig_execute(sql, params)
            cur.execute = failing_execute
            return cur

        with patch("app.get_db_cursor", side_effect=selective_cursor_fn):
            res = self.client.post(
                f"/admin/modules/{self.module_id}/videos/new",
                data=data,
                content_type="multipart/form-data",
                follow_redirects=True,
            )
            self.assertEqual(res.status_code, 200)
            self.assertIn(b"Database error saving video record", res.data)

        # Verify no orphan file exists on disk
        videos_on_disk = []
        for root, _, files in os.walk(self.upload_dir):
            for f in files:
                if "orphan_test" in f:
                    videos_on_disk.append(f)
        self.assertEqual(len(videos_on_disk), 0, "Orphan file was not cleaned up!")

    def test_05_invalid_file_format_rejected(self):
        """Uploading an invalid file format (e.g. .exe) is rejected immediately."""
        self._login_admin()
        data = {
            "title": "Malicious Upload Attempt",
            "video_file": (io.BytesIO(b"MALICIOUS_BYTES"), "virus.exe"),
        }

        res = self.client.post(
            f"/admin/modules/{self.module_id}/videos/new",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Invalid video file format", res.data)

    def test_06_video_edit_replaces_file_and_cleans_old_file(self):
        """Editing a video and uploading a new file cleans up the old file only after DB commit succeeds."""
        self._login_admin()
        # 1. Create initial video
        data1 = {
            "title": "Original Lesson",
            "duration": "10:00",
            "sequence": "1",
            "is_active": "1",
            "video_file": (io.BytesIO(b"VERSION_1_DATA"), "v1.mp4"),
        }
        self.client.post(
            f"/admin/modules/{self.module_id}/videos/new",
            data=data1,
            content_type="multipart/form-data",
            follow_redirects=True,
        )

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id, video_file FROM course_videos WHERE title = %s", ("Original Lesson",))
        v1_record = cur.fetchone()
        cur.close()
        conn.close()

        v1_id = v1_record["id"]
        v1_path = v1_record["video_file"]
        self.assertTrue(storage.video_exists(v1_path))

        # 2. Edit video and upload version 2
        data2 = {
            "title": "Updated Lesson Title",
            "duration": "12:00",
            "sequence": "1",
            "is_active": "1",
            "video_file": (io.BytesIO(b"VERSION_2_DATA"), "v2.mp4"),
        }
        res = self.client.post(
            f"/admin/videos/{v1_id}/edit",
            data=data2,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"updated successfully", res.data)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT title, video_file FROM course_videos WHERE id = %s", (v1_id,))
        v2_record = cur.fetchone()
        cur.close()
        conn.close()

        v2_path = v2_record["video_file"]
        self.assertEqual(v2_record["title"], "Updated Lesson Title")
        self.assertNotEqual(v1_path, v2_path)
        # New file exists, old file is cleaned up
        self.assertTrue(storage.video_exists(v2_path))
        self.assertFalse(storage.video_exists(v1_path))

    def test_07_edit_failure_cleans_new_file_and_preserves_old(self):
        """If video edit DB UPDATE fails, the newly uploaded file is deleted and old file is kept."""
        self._login_admin()
        # 1. Create initial video
        data1 = {
            "title": "Existing Lesson",
            "duration": "10:00",
            "sequence": "1",
            "is_active": "1",
            "video_file": (io.BytesIO(b"ORIGINAL_BYTES"), "orig.mp4"),
        }
        self.client.post(
            f"/admin/modules/{self.module_id}/videos/new",
            data=data1,
            content_type="multipart/form-data",
            follow_redirects=True,
        )

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id, video_file FROM course_videos WHERE title = %s", ("Existing Lesson",))
        orig_record = cur.fetchone()
        cur.close()
        conn.close()

        v_id = orig_record["id"]
        orig_path = orig_record["video_file"]
        self.assertTrue(storage.video_exists(orig_path))

        # 2. Try to update with failing DB UPDATE
        from app import get_db_cursor as real_get_cursor
        def failing_update_cursor(conn):
            cur = real_get_cursor(conn)
            orig_execute = cur.execute
            def failing_exec(sql, params=None):
                if "UPDATE course_videos" in sql:
                    raise Exception("Simulated DB Disconnect on UPDATE")
                return orig_execute(sql, params)
            cur.execute = failing_exec
            return cur

        with patch("app.get_db_cursor", side_effect=failing_update_cursor):
            res = self.client.post(
                f"/admin/videos/{v_id}/edit",
                data={
                    "title": "Failed Edit Attempt",
                    "video_file": (io.BytesIO(b"NEW_BYTES_FAIL"), "fail_new.mp4"),
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )
            self.assertEqual(res.status_code, 200)
            self.assertIn(b"Database error updating video record", res.data)

        # Original file is preserved
        self.assertTrue(storage.video_exists(orig_path))

        # Newly attempted replacement is NOT orphaned on disk
        orphan_files = []
        for root, _, files in os.walk(self.upload_dir):
            for f in files:
                if "fail_new" in f:
                    orphan_files.append(f)
        self.assertEqual(len(orphan_files), 0)

    def test_08_file_size_exceeded_handled_gracefully(self):
        """Upload exceeding MAX_CONTENT_LENGTH triggers 413 error handler and flash redirect."""
        self._login_admin()
        app.config["MAX_CONTENT_LENGTH"] = 100 * 1024  # 100 KB limit for test

        oversized_data = {
            "title": "Oversized Video",
            "video_file": (io.BytesIO(b"Z" * (200 * 1024)), "too_large.mp4"),
        }
        res = self.client.post(
            f"/admin/modules/{self.module_id}/videos/new",
            data=oversized_data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"The uploaded file is too large", res.data)


if __name__ == "__main__":
    unittest.main()
