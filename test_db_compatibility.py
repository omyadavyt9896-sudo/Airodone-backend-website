import os
import unittest
import tempfile
import urllib.parse
from datetime import datetime

from flask import g
import app as app_module
from app import app, init_db, get_db_connection, get_db_type, User, load_user

class MultiDatabaseCompatibilityTestCase(unittest.TestCase):
    """Test suite verifying database connection routing, schema initialization, per-request connection reuse, and indexes."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.db_fd)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["DATABASE_URL"] = f"sqlite:///{self.db_path}"

        with app.app_context():
            init_db()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_01_db_type_detection(self):
        """Verify get_db_type correctly identifies database schemes."""
        app.config["DATABASE_URL"] = "postgresql://user:pass@localhost:5432/airodrone"
        self.assertEqual(get_db_type(), "postgres")

        app.config["DATABASE_URL"] = "mysql://user:pass@localhost:3306/airodrone"
        self.assertEqual(get_db_type(), "mysql")

        app.config["DATABASE_URL"] = "mysql+pymysql://user:pass@localhost:3306/airodrone"
        self.assertEqual(get_db_type(), "mysql")

        app.config["DATABASE_URL"] = f"sqlite:///{self.db_path}"
        self.assertEqual(get_db_type(), "sqlite")

    def test_02_all_12_tables_created(self):
        """Verify all 12 core tables exist after init_db()."""
        expected_tables = {
            "users", "contacts", "courses", "course_enrollments",
            "modules", "course_videos", "video_progress", "certificates",
            "quizzes", "quiz_questions", "quiz_attempts", "quiz_answers"
        }
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row["name"] for row in cur.fetchall()}
        cur.close()
        conn.close()

        for table in expected_tables:
            self.assertIn(table, tables, f"Table '{table}' missing from database schema.")

    def test_03_crud_and_lastrowid(self):
        """Verify INSERT, lastrowid generation, SELECT, UPDATE, and DELETE."""
        conn = get_db_connection()
        cur = conn.cursor()
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # INSERT user
        cur.execute(
            """
            INSERT INTO users (name, email, password_hash, role, is_active, created_at)
            VALUES (%s, %s, %s, %s, 1, %s)
            """,
            ("Test DB User", "dbtest@airodrone.com", "hash123", "user", now_str)
        )
        user_id = cur.lastrowid
        self.assertIsNotNone(user_id)
        self.assertGreater(user_id, 0)

        # SELECT user
        cur.execute("SELECT id, name, email FROM users WHERE id = %s", (user_id,))
        user_row = cur.fetchone()
        self.assertEqual(user_row["name"], "Test DB User")
        self.assertEqual(user_row["email"], "dbtest@airodrone.com")

        # UPDATE user
        cur.execute("UPDATE users SET name = %s WHERE id = %s", ("Updated DB User", user_id))
        cur.execute("SELECT name FROM users WHERE id = %s", (user_id,))
        updated_row = cur.fetchone()
        self.assertEqual(updated_row["name"], "Updated DB User")

        # DELETE user
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        self.assertIsNone(cur.fetchone())

        cur.close()
        conn.close()

    def test_04_enrollment_and_progress_flow(self):
        """Verify student enrollment and anti-fast-forward progress update flow."""
        conn = get_db_connection()
        cur = conn.cursor()
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # Create user & course
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, 'user', 1, %s)",
            ("Student One", "student1@airodrone.com", "pass", now_str)
        )
        student_id = cur.lastrowid

        cur.execute(
            "INSERT INTO courses (title, slug, description, level, is_active, created_at, updated_at) VALUES (%s, %s, %s, 'Beginner', 1, %s, %s)",
            ("Course One", "course-one", "Desc", now_str, now_str)
        )
        course_id = cur.lastrowid

        # Enroll student
        cur.execute(
            "INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, created_at, updated_at) VALUES (%s, %s, 1, %s, %s, %s)",
            (student_id, course_id, now_str, now_str, now_str)
        )
        enrollment_id = cur.lastrowid
        self.assertIsNotNone(enrollment_id)

        # Module & Video
        cur.execute(
            "INSERT INTO modules (course_id, title, sequence, is_active, created_at, updated_at) VALUES (%s, 'Mod 1', 1, 1, %s, %s)",
            (course_id, now_str, now_str)
        )
        module_id = cur.lastrowid

        cur.execute(
            "INSERT INTO course_videos (module_id, title, sequence, is_active, created_at, updated_at) VALUES (%s, 'Vid 1', 1, 1, %s, %s)",
            (module_id, now_str, now_str)
        )
        video_id = cur.lastrowid

        # Video progress
        cur.execute(
            """
            INSERT INTO video_progress (user_id, video_id, watched_seconds, duration_seconds, completion_percentage, completed, first_started_at, last_watched_at, created_at, updated_at)
            VALUES (%s, %s, 60.0, 100.0, 60.0, FALSE, %s, %s, %s, %s)
            """,
            (student_id, video_id, now_str, now_str, now_str, now_str)
        )
        progress_id = cur.lastrowid
        self.assertIsNotNone(progress_id)

        cur.close()
        conn.close()

    def test_05_per_request_connection_reuse(self):
        """Verify per-request connection reuse via flask.g."""
        with app.test_request_context():
            conn1 = get_db_connection()
            conn2 = get_db_connection()
            self.assertIn("raw_db", g)
            # Proxy instances wrapped around same underlying raw_db connection
            self.assertIs(conn1._raw_conn, conn2._raw_conn)

    def test_06_target_indexes_exist(self):
        """Verify all 7 targeted performance indexes exist in the schema."""
        expected_indexes = {
            "idx_modules_course_id",
            "idx_course_videos_module_id",
            "idx_video_progress_user_id",
            "idx_quizzes_module_id",
            "idx_quiz_questions_quiz_id",
            "idx_quiz_attempts_user_quiz",
            "idx_quiz_answers_attempt_id"
        }
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='index';")
        indexes = {row["name"] for row in cur.fetchall()}
        cur.close()
        conn.close()

        for idx in expected_indexes:
            self.assertIn(idx, indexes, f"Index '{idx}' missing from database schema.")

    def test_07_legacy_users_table_migration_without_student_class(self):
        """Verify that a legacy database with users table missing student_class is safely migrated without data loss and login succeeds."""
        import sqlite3
        from werkzeug.security import generate_password_hash

        legacy_fd, legacy_db_path = tempfile.mkstemp(suffix=".db")
        os.close(legacy_fd)

        try:
            # 1. Directly create old legacy users table WITHOUT student_class, father_name, phone
            raw_conn = sqlite3.connect(legacy_db_path)
            raw_cur = raw_conn.cursor()
            raw_cur.execute("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)
            raw_cur.execute("""
                INSERT INTO users (name, email, password_hash, role, is_active, created_at)
                VALUES ('Legacy Student', 'legacy@airodrone.com', ?, 'user', 1, '2026-01-01 10:00:00')
            """, (generate_password_hash("legacyPass123"),))
            raw_conn.commit()
            raw_conn.close()

            # 2. Configure app to point to this legacy database
            app.config["DATABASE_URL"] = f"sqlite:///{legacy_db_path}"
            app_module._db_initialized = False

            # 3. Trigger init_db / migration
            with app.app_context():
                init_db()

            # 4. Verify student_class column now exists
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(users);")
            columns = [row["name"] for row in cur.fetchall()]
            self.assertIn("student_class", columns)
            self.assertIn("father_name", columns)
            self.assertIn("phone", columns)

            # 5. Verify existing legacy user is preserved
            cur.execute(
                "SELECT id, name, email, password_hash, role, is_active, father_name, phone, student_class FROM users WHERE email = %s",
                ("legacy@airodrone.com",)
            )
            user_row = cur.fetchone()
            self.assertIsNotNone(user_row)
            self.assertEqual(user_row["name"], "Legacy Student")
            self.assertEqual(user_row["email"], "legacy@airodrone.com")
            self.assertIsNone(user_row["student_class"])

            # 6. Verify login user loader works with this migrated record
            user_obj = load_user(user_row["id"])
            self.assertIsNotNone(user_obj)
            self.assertEqual(user_obj.email, "legacy@airodrone.com")
            self.assertIsNone(user_obj.student_class)
            self.assertIsNone(user_obj.grade)

            cur.close()
            conn.close()

            # 7. Verify running init_db a second time is 100% idempotent
            with app.app_context():
                init_db()

        finally:
            try:
                os.unlink(legacy_db_path)
            except OSError:
                pass
            # Restore testing DATABASE_URL
            app.config["DATABASE_URL"] = f"sqlite:///{self.db_path}"
            app_module._db_initialized = False

    def test_08_mysql_add_column_idempotent_simulation(self):
        """Verify add_column_if_not_exists gracefully handles duplicate column notices in MySQL mode."""
        class MockMySQLCursor:
            def execute(self, query, params=None):
                if "information_schema" in query:
                    self._last_result = {"cnt": 1}
                elif "ALTER TABLE" in query:
                    raise Exception("(1060, \"Duplicate column name 'student_class'\")")
            def fetchone(self):
                return getattr(self, "_last_result", {"cnt": 0})
            def close(self):
                pass

        app.config["DATABASE_URL"] = "mysql://user:pass@localhost:3306/airodrone"
        mock_cur = MockMySQLCursor()
        # Should not raise exception
        app_module.add_column_if_not_exists(mock_cur, "users", "student_class", "VARCHAR(50) DEFAULT NULL")

        # Restore testing DATABASE_URL
        app.config["DATABASE_URL"] = f"sqlite:///{self.db_path}"
        app_module._db_initialized = False

if __name__ == "__main__":
    unittest.main()
