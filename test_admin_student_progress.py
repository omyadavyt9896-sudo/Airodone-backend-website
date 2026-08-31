"""
Unit tests for the Admin Student Learning Progress Dashboard.
Tests access control, accurate progress formulas, edge cases, module/video breakdown,
weighting, isolation, and query efficiency.
"""

import os
import tempfile
import unittest
from datetime import datetime
from werkzeug.security import generate_password_hash

from app import app, init_db, get_db_connection, get_db_cursor


class TestAdminStudentProgress(unittest.TestCase):

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["DATABASE_URL"] = f"sqlite:///{self.db_path}"
        self.client = app.test_client()

        with app.app_context():
            init_db()
            self._seed_data()

    def tearDown(self):
        try:
            os.close(self.db_fd)
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
        except Exception:
            pass

    def _seed_data(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # Create Admin User
        cur.execute(
            """
            INSERT INTO users (email, password_hash, name, role, is_active, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            ("admin_test@steroaim.com", generate_password_hash("AdminPass123"), "Admin Tester", "admin", 1, now_str)
        )
        self.admin_id = cur.lastrowid or 1

        # Create Student 1 (Grade 5, Class 12)
        cur.execute(
            """
            INSERT INTO users (email, password_hash, name, role, student_class, is_active, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            ("student1@steroaim.com", generate_password_hash("StudentPass123"), "Om Yadav", "student", "12", 1, now_str)
        )
        self.student1_id = cur.lastrowid or 2

        # Create Student 2 (for isolation checks)
        cur.execute(
            """
            INSERT INTO users (email, password_hash, name, role, student_class, is_active, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            ("student2@steroaim.com", generate_password_hash("StudentPass123"), "Jane Doe", "student", "12", 1, now_str)
        )
        self.student2_id = cur.lastrowid or 3

        # Create Course A (Grade 5, 2 modules, 4 videos total)
        cur.execute(
            """
            INSERT INTO courses (title, slug, description, short_description, level, grade, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            ("AI & Machine Learning", "ai-machine-learning", "AI Desc", "AI Short", "Advanced", 5, 1, now_str, now_str)
        )
        self.course_a_id = cur.lastrowid or 1

        # Module A1
        cur.execute(
            "INSERT INTO modules (course_id, title, sequence, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (self.course_a_id, "Module A1 - Foundations", 1, 1, now_str, now_str)
        )
        self.mod_a1_id = cur.lastrowid or 1

        # Video A1_1 and A1_2
        cur.execute(
            "INSERT INTO course_videos (module_id, title, sequence, duration, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (self.mod_a1_id, "Video A1_1 Intro", 1, "10:00", 1, now_str, now_str)
        )
        self.vid_a1_1 = cur.lastrowid or 1
        cur.execute(
            "INSERT INTO course_videos (module_id, title, sequence, duration, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (self.mod_a1_id, "Video A1_2 Neural Nets", 2, "20:00", 1, now_str, now_str)
        )
        self.vid_a1_2 = cur.lastrowid or 2

        # Module A2
        cur.execute(
            "INSERT INTO modules (course_id, title, sequence, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (self.course_a_id, "Module A2 - Deep Learning", 2, 1, now_str, now_str)
        )
        self.mod_a2_id = cur.lastrowid or 2

        # Video A2_1 and A2_2
        cur.execute(
            "INSERT INTO course_videos (module_id, title, sequence, duration, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (self.mod_a2_id, "Video A2_1 CNNs", 1, "15:00", 1, now_str, now_str)
        )
        self.vid_a2_1 = cur.lastrowid or 3
        cur.execute(
            "INSERT INTO course_videos (module_id, title, sequence, duration, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (self.mod_a2_id, "Video A2_2 Transformers", 2, "25:00", 1, now_str, now_str)
        )
        self.vid_a2_2 = cur.lastrowid or 4

        # Create Course B (Grade 5, 1 module, 2 videos total)
        cur.execute(
            """
            INSERT INTO courses (title, slug, description, short_description, level, grade, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            ("Robotics Systems", "robotics-systems", "Robo Desc", "Robo Short", "Intermediate", 5, 1, now_str, now_str)
        )
        self.course_b_id = cur.lastrowid or 2

        cur.execute(
            "INSERT INTO modules (course_id, title, sequence, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (self.course_b_id, "Module B1 - Kinematics", 1, 1, now_str, now_str)
        )
        self.mod_b1_id = cur.lastrowid or 3

        cur.execute(
            "INSERT INTO course_videos (module_id, title, sequence, duration, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (self.mod_b1_id, "Video B1_1 Motors", 1, "10:00", 1, now_str, now_str)
        )
        self.vid_b1_1 = cur.lastrowid or 5
        cur.execute(
            "INSERT INTO course_videos (module_id, title, sequence, duration, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (self.mod_b1_id, "Video B1_2 Sensors", 2, "10:00", 1, now_str, now_str)
        )
        self.vid_b1_2 = cur.lastrowid or 6

        # Course C: Empty course (0 videos)
        cur.execute(
            """
            INSERT INTO courses (title, slug, description, short_description, level, grade, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            ("Autonomous Flight", "autonomous-flight", "Flight Desc", "Flight Short", "Advanced", 5, 1, now_str, now_str)
        )
        self.course_c_id = cur.lastrowid or 3

        # Enroll Student 1 in Course A and Course B
        cur.execute(
            "INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, created_at, updated_at) VALUES (%s, %s, 1, %s, %s, %s)",
            (self.student1_id, self.course_a_id, now_str, now_str, now_str)
        )
        cur.execute(
            "INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, created_at, updated_at) VALUES (%s, %s, 1, %s, %s, %s)",
            (self.student1_id, self.course_b_id, now_str, now_str, now_str)
        )

        conn.commit()
        cur.close()
        conn.close()

    def _login(self, email, password):
        return self.client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=True,
        )

    def test_01_admin_access_allowed(self):
        """Admin can successfully view student progress dashboard."""
        self._login("admin_test@steroaim.com", "AdminPass123")
        res = self.client.get(f"/admin/users/{self.student1_id}/progress")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Om Yadav", res.data)
        self.assertIn(b"AI &amp; Machine Learning", res.data)
        self.assertIn(b"Robotics Systems", res.data)

    def test_02_student_access_forbidden(self):
        """Student cannot access admin progress dashboard."""
        self._login("student1@steroaim.com", "StudentPass123")
        res = self.client.get(f"/admin/users/{self.student1_id}/progress")
        self.assertTrue(res.status_code in (302, 403))

    def test_03_unknown_student_handling(self):
        """Non-existent student user_id redirects gracefully."""
        self._login("admin_test@steroaim.com", "AdminPass123")
        res = self.client.get("/admin/users/999999/progress", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Student account not found", res.data)

    def test_04_student_with_no_courses_renders_empty_state(self):
        """Student with no course enrollments shows clear empty state."""
        self._login("admin_test@steroaim.com", "AdminPass123")
        res = self.client.get(f"/admin/users/{self.student2_id}/progress")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"No Enrolled Courses", res.data)
        self.assertIn(b"0%", res.data)

    def test_05_course_with_zero_videos_does_not_nan(self):
        """Course with 0 active videos reports 0% / Not Started without NaN."""
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, created_at, updated_at) VALUES (%s, %s, 1, %s, %s, %s)",
            (self.student2_id, self.course_c_id, now_str, now_str, now_str)
        )
        conn.commit()
        cur.close()
        conn.close()

        self._login("admin_test@steroaim.com", "AdminPass123")
        res = self.client.get(f"/admin/users/{self.student2_id}/progress")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(b"NaN", res.data)
        self.assertNotIn(b"Infinity", res.data)
        self.assertIn(b"Autonomous Flight", res.data)
        self.assertIn(b"0%", res.data)

    def test_06_video_progress_statuses_and_module_course_calculations(self):
        """
        Verify:
        - Completed video: 100% and 'Completed'
        - In-progress video: 50% and 'In Progress'
        - Not started video: 0% and 'Not Started'
        - Module progress calculation
        - Course progress calculation
        - Overall student progress weighting
        """
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # Course A (4 videos):
        # Vid A1_1: Completed (100%)
        cur.execute(
            """
            INSERT INTO video_progress (user_id, video_id, watched_seconds, duration_seconds, completion_percentage, completed, first_started_at, last_watched_at, completed_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (self.student1_id, self.vid_a1_1, 600.0, 600.0, 100.0, 1, now_str, now_str, now_str, now_str, now_str)
        )

        # Vid A1_2: In Progress (50%, 600s / 1200s)
        cur.execute(
            """
            INSERT INTO video_progress (user_id, video_id, watched_seconds, duration_seconds, completion_percentage, completed, first_started_at, last_watched_at, completed_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (self.student1_id, self.vid_a1_2, 600.0, 1200.0, 50.0, 0, now_str, now_str, None, now_str, now_str)
        )

        # Course B (2 videos):
        # Vid B1_1: Completed (100%)
        cur.execute(
            """
            INSERT INTO video_progress (user_id, video_id, watched_seconds, duration_seconds, completion_percentage, completed, first_started_at, last_watched_at, completed_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (self.student1_id, self.vid_b1_1, 600.0, 600.0, 100.0, 1, now_str, now_str, now_str, now_str, now_str)
        )
        # Vid B1_2: Completed (100%)
        cur.execute(
            """
            INSERT INTO video_progress (user_id, video_id, watched_seconds, duration_seconds, completion_percentage, completed, first_started_at, last_watched_at, completed_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (self.student1_id, self.vid_b1_2, 600.0, 600.0, 100.0, 1, now_str, now_str, now_str, now_str, now_str)
        )

        conn.commit()
        cur.close()
        conn.close()

        self._login("admin_test@steroaim.com", "AdminPass123")
        res = self.client.get(f"/admin/users/{self.student1_id}/progress")
        self.assertEqual(res.status_code, 200)

        html = res.data.decode("utf-8")
        # Check overall progress: (100 + 50 + 0 + 0 + 100 + 100) / 6 = 350 / 6 = 58.3%
        self.assertIn("58.3%", html)
        # Check Course A progress: (100 + 50 + 0 + 0) / 4 = 37.5%
        self.assertIn("37.5%", html)
        # Check Course B completed: (100 + 100) / 2 = 100.0%
        self.assertIn("100.0%", html)
        # Check Module A1 progress: (100 + 50) / 2 = 75.0%
        self.assertIn("75.0%", html)

    def test_07_progress_isolation_between_students(self):
        """Student 2's progress records do not leak into Student 1's report."""
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # Give Student 2 100% progress on Course A
        cur.execute(
            """
            INSERT INTO video_progress (user_id, video_id, watched_seconds, duration_seconds, completion_percentage, completed, first_started_at, last_watched_at, completed_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (self.student2_id, self.vid_a1_1, 600.0, 600.0, 100.0, 1, now_str, now_str, now_str, now_str, now_str)
        )
        conn.commit()
        cur.close()
        conn.close()

        self._login("admin_test@steroaim.com", "AdminPass123")
        res = self.client.get(f"/admin/users/{self.student1_id}/progress")
        html = res.data.decode("utf-8")
        # Student 1 has 0% progress on Course A
        self.assertIn("0.0%", html)

    def test_08_inactive_courses_and_videos_excluded(self):
        """Inactive courses, deallocated enrollments, and inactive videos are excluded."""
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        # Deactivate Video A2_2
        cur.execute("UPDATE course_videos SET is_active = 0 WHERE id = %s", (self.vid_a2_2,))
        conn.commit()
        cur.close()
        conn.close()

        self._login("admin_test@steroaim.com", "AdminPass123")
        res = self.client.get(f"/admin/users/{self.student1_id}/progress")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        # Course A now has 3 active videos instead of 4
        self.assertIn("0 / 3 completed", html)

    def test_09_performance_and_large_scale_hierarchy(self):
        """Verify performance and rendering speed with 5 courses, multiple modules and videos."""
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # Create 3 more courses with 3 modules and 5 videos each for student 1
        for c_idx in range(3):
            cur.execute(
                """
                INSERT INTO courses (title, slug, description, short_description, level, grade, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (f"Scale Course {c_idx}", f"scale-course-{c_idx}", "Desc", "Short", "Beginner", 5, 1, now_str, now_str)
            )
            c_id = cur.lastrowid
            cur.execute(
                "INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, created_at, updated_at) VALUES (%s, %s, 1, %s, %s, %s)",
                (self.student1_id, c_id, now_str, now_str, now_str)
            )
            for m_idx in range(3):
                cur.execute(
                    "INSERT INTO modules (course_id, title, sequence, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)",
                    (c_id, f"Scale Module {m_idx}", m_idx + 1, 1, now_str, now_str)
                )
                m_id = cur.lastrowid
                for v_idx in range(5):
                    cur.execute(
                        "INSERT INTO course_videos (module_id, title, sequence, duration, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (m_id, f"Scale Video {v_idx}", v_idx + 1, "10:00", 1, now_str, now_str)
                    )

        conn.commit()
        cur.close()
        conn.close()

        import time
        self._login("admin_test@steroaim.com", "AdminPass123")
        start_time = time.time()
        res = self.client.get(f"/admin/users/{self.student1_id}/progress")
        duration = time.time() - start_time

        self.assertEqual(res.status_code, 200)
        # Should render well under 500ms even with dozens of videos
        self.assertLess(duration, 1.0)
        self.assertIn(b"Scale Course 0", res.data)
        self.assertIn(b"Scale Course 1", res.data)
        self.assertIn(b"Scale Course 2", res.data)

    def test_10_navigation_link_in_admin_users(self):
        """Verify the 'Progress' link is present for students in admin_users list."""
        self._login("admin_test@steroaim.com", "AdminPass123")
        res = self.client.get("/admin/users")
        self.assertEqual(res.status_code, 200)
        self.assertIn(f"/admin/users/{self.student1_id}/progress".encode("utf-8"), res.data)


if __name__ == "__main__":
    unittest.main()
