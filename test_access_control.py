import os
import unittest
import tempfile
from werkzeug.security import generate_password_hash, check_password_hash

from app import app, get_db_connection, init_db, is_user_enrolled, can_access_course


class Phase77AccessControlTestCase(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key-phase-77"
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["DATABASE_URL"] = f"sqlite:///{self.db_path}"

        self.client = app.test_client()

        with app.app_context():
            init_db()

        self._seed_test_data()

    def tearDown(self):
        os.close(self.db_fd)
        if os.path.exists(self.db_path):
            try:
                os.unlink(self.db_path)
            except OSError:
                pass

    def _seed_test_data(self):
        """Seed admin, test courses, modules, videos, and quizzes for test scenarios."""
        conn = get_db_connection()
        cur = conn.cursor()

        # Seed Admin User
        cur.execute("SELECT id FROM users WHERE email = 'admin@airodrone.com'")
        admin = cur.fetchone()
        if not admin:
            cur.execute(
                """
                INSERT INTO users (name, email, password_hash, role, is_active, created_at)
                VALUES ('Admin User', 'admin@airodrone.com', %s, 'admin', 1, '2026-01-01 00:00:00')
                """,
                (generate_password_hash("AdminPass123!"),),
            )

        # Seed Course 1
        cur.execute("SELECT id FROM courses WHERE slug = 'drone-basics'")
        c1 = cur.fetchone()
        if not c1:
            cur.execute(
                """
                INSERT INTO courses (
                    title, slug, short_description, description, image, level,
                    estimated_duration, learning_outcomes, course_benefits, certificate_description,
                    is_active, created_at, updated_at
                )
                VALUES (
                    'Drone Basics', 'drone-basics', 'Short intro to drone tech',
                    'Detailed course summary covering flight mechanics and drone operations.',
                    '/img/drone.jpg', 'Beginner', '10 Hours of Learning',
                    'Flight physics fundamentals\nBasic multirotor aerodynamics\nPre-flight safety protocols',
                    'Practical hands-on drone flying skills\nVerified completion certificate',
                    '75% verified video completion required for certificate',
                    1, '2026-01-01 00:00:00', '2026-01-01 00:00:00'
                )
                """
            )
            cur.execute("SELECT id FROM courses WHERE slug = 'drone-basics'")
            c1_id = cur.fetchone()["id"]
        else:
            c1_id = c1["id"]

        # Seed Course 2
        cur.execute("SELECT id FROM courses WHERE slug = 'advanced-robotics'")
        c2 = cur.fetchone()
        if not c2:
            cur.execute(
                """
                INSERT INTO courses (
                    title, slug, short_description, description, image, level,
                    estimated_duration, learning_outcomes, course_benefits, certificate_description,
                    is_active, created_at, updated_at
                )
                VALUES (
                    'Advanced Robotics', 'advanced-robotics', 'Short robotics intro',
                    'Advanced STEM robotics engineering curriculum.',
                    '/img/robot.jpg', 'Advanced', '15 Hours of Learning',
                    'Microcontroller programming\nSensor integration',
                    'Build autonomous robotic systems',
                    '75% verified completion required',
                    1, '2026-01-01 00:00:00', '2026-01-01 00:00:00'
                )
                """
            )
            cur.execute("SELECT id FROM courses WHERE slug = 'advanced-robotics'")
            c2_id = cur.fetchone()["id"]
        else:
            c2_id = c2["id"]

        # Seed Module for Course 1
        cur.execute("SELECT id FROM modules WHERE course_id = %s", (c1_id,))
        mod = cur.fetchone()
        if not mod:
            cur.execute(
                """
                INSERT INTO modules (course_id, title, description, sequence, is_active, created_at, updated_at)
                VALUES (%s, 'Module 1: Flight Fundamentals', 'Flight physics', 1, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
                """,
                (c1_id,),
            )
            cur.execute("SELECT id FROM modules WHERE course_id = %s", (c1_id,))
            mod_id = cur.fetchone()["id"]
        else:
            mod_id = mod["id"]

        # Seed Video for Module 1
        cur.execute("SELECT id FROM course_videos WHERE module_id = %s", (mod_id,))
        vid = cur.fetchone()
        if not vid:
            cur.execute(
                """
                INSERT INTO course_videos (module_id, title, description, sequence, duration, youtube_video_id, is_active, created_at, updated_at)
                VALUES (%s, 'Video 1: Lift & Drag', 'Understanding aerodynamics', 1, 300, 'dQw4w9WgXcQ', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
                """,
                (mod_id,),
            )
            cur.execute("SELECT id FROM course_videos WHERE module_id = %s", (mod_id,))
            vid_id = cur.fetchone()["id"]
        else:
            vid_id = vid["id"]

        # Seed Quiz for Module 1
        cur.execute("SELECT id FROM quizzes WHERE module_id = %s", (mod_id,))
        q = cur.fetchone()
        if not q:
            cur.execute(
                """
                INSERT INTO quizzes (module_id, title, description, passing_score, max_attempts, is_active, created_at, updated_at)
                VALUES (%s, 'Module 1 Quiz', 'Test your flight physics knowledge', 75, 5, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
                """,
                (mod_id,),
            )

        conn.commit()
        cur.close()
        conn.close()

        self.c1_id = c1_id
        self.c2_id = c2_id
        self.mod_id = mod_id
        self.vid_id = vid_id

    def login_user(self, email, password):
        """Helper method to log in a user."""
        return self.client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=True,
        )

    def logout_user(self):
        """Helper method to log out."""
        return self.client.get("/logout", follow_redirects=True)

    # ----------------------------------------------------
    # 1. Public Catalogue & Public Course Information Tests (Phase 7.7.1)
    # ----------------------------------------------------

    def test_01_unauthenticated_courses_accessible_publicly(self):
        res = self.client.get("/courses")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Drone Basics", res.data)
        self.assertIn(b"Short intro to drone tech", res.data)

    def test_02_unauthenticated_course_detail_accessible_publicly(self):
        res = self.client.get("/courses/drone-basics")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Drone Basics", res.data)
        self.assertIn(b"Detailed course summary covering flight mechanics", res.data)
        self.assertIn(b"What You'll Learn", res.data)
        self.assertIn(b"Flight physics fundamentals", res.data)
        self.assertIn(b"Course Content", res.data)
        self.assertIn(b"Login to Access Course", res.data)
        # Ensure protected curriculum lesson titles and video IDs are NOT exposed to logged-out users
        self.assertNotIn(b"Video 1: Lift & Drag", res.data)
        self.assertNotIn(b"dQw4w9WgXcQ", res.data)

    def test_03_unauthenticated_video_player_redirects_to_login(self):
        res = self.client.get(f"/courses/drone-basics/video/{self.vid_id}")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.location)

    def test_04_unauthenticated_dashboard_redirects_to_login(self):
        res = self.client.get("/dashboard")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.location)

    def test_05_public_pages_remain_accessible(self):
        for path in ["/", "/about", "/services", "/contact", "/verify"]:
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, f"Public path {path} failed")

    def test_06_self_registration_is_disabled(self):
        res = self.client.get("/register", follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.location)

    # ----------------------------------------------------
    # 2. Admin Student Account Management Tests
    # ----------------------------------------------------

    def test_07_admin_can_access_user_management(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        res = self.client.get("/admin/users")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Student Account Management", res.data)

    def test_08_admin_can_create_student_account(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        res = self.client.post(
            "/admin/users/new",
            data={
                "name": "Om Yadav",
                "father_name": "Ramesh Yadav",
                "email": "om.yadav@example.com",
                "phone": "9876543210",
                "password": "StudentTemp123!",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Student account created successfully", res.data)

        # Verify DB entry
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, name, father_name, phone, password_hash FROM users WHERE email = 'om.yadav@example.com'")
        user = cur.fetchone()
        conn.close()
        self.assertIsNotNone(user)
        self.assertEqual(user["name"], "Om Yadav")
        self.assertEqual(user["father_name"], "Ramesh Yadav")
        # Ensure password is stored as hash, NOT plaintext
        self.assertFalse("StudentTemp123!" in user["password_hash"])
        self.assertTrue(check_password_hash(user["password_hash"], "StudentTemp123!"))

    def test_09_duplicate_email_registration_rejected(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        # First creation
        self.client.post(
            "/admin/users/new",
            data={
                "name": "Student One",
                "email": "duplicate@example.com",
                "password": "Password123!",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        # Duplicate creation attempt
        res = self.client.post(
            "/admin/users/new",
            data={
                "name": "Student Two",
                "email": "duplicate@example.com",
                "password": "Password123!",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Email already registered", res.data)

    def test_10_created_student_can_login(self):
        # Create student account as admin
        self.login_user("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={
                "name": "Login Test Student",
                "email": "logintest@example.com",
                "password": "MyTempPass123!",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.logout_user()

        # Login as student
        res = self.login_user("logintest@example.com", "MyTempPass123!")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Welcome back, Login Test Student!", res.data)

    def test_11_admin_reset_password_generates_temp_pass(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        # Create student
        self.client.post(
            "/admin/users/new",
            data={"name": "Reset Test", "email": "reset@example.com", "password": "OldPassword123!", "is_active": "1"},
            follow_redirects=True,
        )
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE email = 'reset@example.com'")
        u_id = cur.fetchone()["id"]
        conn.close()

        # Reset password
        res = self.client.post(f"/admin/users/{u_id}/reset-password", data={"new_password": "NewTempPass999!"}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"NewTempPass999!", res.data)
        self.assertIn(b"Save this temporary password securely", res.data)

        self.logout_user()
        # Verify new password login works
        res_login = self.login_user("reset@example.com", "NewTempPass999!")
        self.assertEqual(res_login.status_code, 200)

    # ----------------------------------------------------
    # 3. Private Course Authorization & Public Info Layer Tests
    # ----------------------------------------------------

    def test_12_unenrolled_student_sees_public_courses_in_catalogue(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={"name": "No Courses Student", "email": "nocourses@example.com", "password": "Pass123456!", "is_active": "1"},
            follow_redirects=True,
        )
        self.logout_user()

        self.login_user("nocourses@example.com", "Pass123456!")
        res = self.client.get("/courses")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Drone Basics", res.data)
        self.assertIn(b"View Course Details", res.data)

    def test_13_unenrolled_student_course_detail_shows_public_info_and_lock_box(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={"name": "Unassigned Student", "email": "unassigned@example.com", "password": "Pass123456!", "is_active": "1"},
            follow_redirects=True,
        )
        self.logout_user()

        self.login_user("unassigned@example.com", "Pass123456!")
        res = self.client.get("/courses/drone-basics")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Drone Basics", res.data)
        self.assertIn(b"What You'll Learn", res.data)
        self.assertIn(b"Course access has not been assigned to your account", res.data)
        self.assertIn(b"Back to Dashboard", res.data)
        # Ensure protected curriculum lesson titles are NOT exposed
        self.assertNotIn(b"Video 1: Lift & Drag", res.data)

    def test_14_unenrolled_student_video_player_denied_403(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={"name": "Blocked Vid Student", "email": "blockedvid@example.com", "password": "Pass123456!", "is_active": "1"},
            follow_redirects=True,
        )
        self.logout_user()

        self.login_user("blockedvid@example.com", "Pass123456!")
        res = self.client.get(f"/courses/drone-basics/video/{self.vid_id}")
        self.assertEqual(res.status_code, 403)
        self.assertIn(b"Course Access Required", res.data)

    def test_15_unenrolled_student_progress_api_denied_403(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={"name": "Blocked Prog Student", "email": "blockedprog@example.com", "password": "Pass123456!", "is_active": "1"},
            follow_redirects=True,
        )
        self.logout_user()

        self.login_user("blockedprog@example.com", "Pass123456!")
        res = self.client.get(f"/courses/video/{self.vid_id}/progress")
        self.assertEqual(res.status_code, 403)

    def test_16_unenrolled_student_quiz_denied_403(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={"name": "Blocked Quiz Student", "email": "blockedquiz@example.com", "password": "Pass123456!", "is_active": "1"},
            follow_redirects=True,
        )
        self.logout_user()

        self.login_user("blockedquiz@example.com", "Pass123456!")
        res = self.client.get(f"/courses/drone-basics/module/{self.mod_id}/quiz")
        self.assertEqual(res.status_code, 403)
        self.assertIn(b"Course Access Required", res.data)

    def test_17_explicit_enrolled_and_access_functions(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={"name": "Function Test", "email": "functest@example.com", "password": "Pass123456!", "is_active": "1"},
            follow_redirects=True,
        )
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE email = 'functest@example.com'")
        student_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM users WHERE email = 'admin@airodrone.com'")
        admin_id = cur.fetchone()["id"]
        conn.close()

        # Before enrollment
        self.assertFalse(is_user_enrolled(student_id, self.c1_id))
        self.assertFalse(can_access_course(student_id, self.c1_id))

        # Admin access vs enrollment
        self.assertFalse(is_user_enrolled(admin_id, self.c1_id))  # Admin is NOT enrolled by default
        self.assertTrue(can_access_course(admin_id, self.c1_id))  # Admin CAN access course content

        # Assign course
        self.client.post(f"/admin/users/{student_id}/courses/assign", data={"course_id": str(self.c1_id)}, follow_redirects=True)

        # After enrollment
        self.assertTrue(is_user_enrolled(student_id, self.c1_id))
        self.assertTrue(can_access_course(student_id, self.c1_id))

    # ----------------------------------------------------
    # 4. Enrollment & Progress Data Preservation Tests
    # ----------------------------------------------------

    def test_18_enrolled_student_access_granted(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={"name": "Enrolled Student", "email": "enrolled@example.com", "password": "Pass123456!", "is_active": "1"},
            follow_redirects=True,
        )
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE email = 'enrolled@example.com'")
        u_id = cur.fetchone()["id"]

        # Assign Drone Basics course
        self.client.post(f"/admin/users/{u_id}/courses/assign", data={"course_id": str(self.c1_id)}, follow_redirects=True)
        conn.close()
        self.logout_user()

        # Log in as enrolled student
        self.login_user("enrolled@example.com", "Pass123456!")

        # Catalogue shows assigned course
        res_cat = self.client.get("/courses")
        self.assertEqual(res_cat.status_code, 200)
        self.assertIn(b"Drone Basics", res_cat.data)

        # Course detail accessible with curriculum accordion
        res_det = self.client.get("/courses/drone-basics")
        self.assertEqual(res_det.status_code, 200)
        self.assertIn(b"Flight Fundamentals", res_det.data)
        self.assertIn(b"Video 1: Lift &amp; Drag", res_det.data)

        # Video player accessible
        res_vid = self.client.get(f"/courses/drone-basics/video/{self.vid_id}")
        self.assertEqual(res_vid.status_code, 200)

    def test_19_removing_course_access_preserves_student_progress_data(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={"name": "Progress Test Student", "email": "progtest@example.com", "password": "Pass123456!", "is_active": "1"},
            follow_redirects=True,
        )
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE email = 'progtest@example.com'")
        u_id = cur.fetchone()["id"]

        # Assign Drone Basics
        self.client.post(f"/admin/users/{u_id}/courses/assign", data={"course_id": str(self.c1_id)}, follow_redirects=True)
        conn.close()
        self.logout_user()

        # Record progress as student (15s update to pass anti-fast-forward check)
        self.login_user("progtest@example.com", "Pass123456!")
        self.client.post(
            f"/courses/video/{self.vid_id}/progress",
            json={"watched_seconds": 15.0, "duration_seconds": 300.0, "event": "timeupdate"},
        )
        self.logout_user()

        # Remove course access as admin
        self.login_user("admin@airodrone.com", "AdminPass123!")
        self.client.post(f"/admin/users/{u_id}/courses/{self.c1_id}/remove", follow_redirects=True)
        self.logout_user()

        # Verify student sees public page but NOT curriculum when unassigned
        self.login_user("progtest@example.com", "Pass123456!")
        res_blocked = self.client.get("/courses/drone-basics")
        self.assertEqual(res_blocked.status_code, 200)
        self.assertNotIn(b"Video 1: Lift &amp; Drag", res_blocked.data)
        self.assertIn(b"Course access has not been assigned to your account", res_blocked.data)
        self.logout_user()

        # Re-assign course access as admin
        self.login_user("admin@airodrone.com", "AdminPass123!")
        self.client.post(f"/admin/users/{u_id}/courses/assign", data={"course_id": str(self.c1_id)}, follow_redirects=True)
        self.logout_user()

        # Log in as student and verify recorded progress was PRESERVED
        self.login_user("progtest@example.com", "Pass123456!")
        res_prog = self.client.get(f"/courses/video/{self.vid_id}/progress")
        self.assertEqual(res_prog.status_code, 200)
        data = res_prog.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["progress"]["watched_seconds"], 15.0)

    # ----------------------------------------------------
    # 5. Account Deactivation, Bootcamp & Admin Edit Tests
    # ----------------------------------------------------

    def test_20_deactivated_student_blocked_from_login(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={"name": "Deactivate Test", "email": "deactivate@example.com", "password": "Pass123456!", "is_active": "1"},
        )
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE email = 'deactivate@example.com'")
        u_id = cur.fetchone()["id"]

        # Deactivate account
        self.client.post(f"/admin/users/{u_id}/toggle-active")
        conn.close()
        self.logout_user()

        # Attempt login
        res_login = self.login_user("deactivate@example.com", "Pass123456!")
        self.assertEqual(res_login.status_code, 200)
        self.assertIn(b"Account is inactive", res_login.data)

    def test_21_bootcamp_returns_404(self):
        res = self.client.get("/bootcamp")
        self.assertEqual(res.status_code, 404)

    def test_22_admin_edits_public_course_information(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        res = self.client.post(
            f"/admin/courses/{self.c1_id}/edit",
            data={
                "title": "Drone Robotics Mastery",
                "slug": "drone-basics",
                "short_description": "Updated short description for drone card",
                "description": "Updated full description for public course detail page.",
                "level": "Intermediate",
                "image": "images/services/drone.jpg",
                "estimated_duration": "20 Hours of Practical STEM",
                "learning_outcomes": "Outcome 1: Aerodynamics\nOutcome 2: Flight Controller Programming",
                "course_benefits": "Benefit 1: Hands-on drone building\nBenefit 2: Industry certificate",
                "certificate_description": "Verified completion threshold of 75% required",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        self.logout_user()

        # Check public /courses
        res_cat = self.client.get("/courses")
        self.assertEqual(res_cat.status_code, 200)
        self.assertIn(b"Drone Robotics Mastery", res_cat.data)
        self.assertIn(b"Updated short description for drone card", res_cat.data)
        self.assertIn(b"20 Hours of Practical STEM", res_cat.data)

        # Check public /courses/drone-basics
        res_det = self.client.get("/courses/drone-basics")
        self.assertEqual(res_det.status_code, 200)
        self.assertIn(b"Drone Robotics Mastery", res_det.data)
        self.assertIn(b"Updated full description for public course detail page", res_det.data)
        self.assertIn(b"Outcome 1: Aerodynamics", res_det.data)
        self.assertIn(b"Benefit 1: Hands-on drone building", res_det.data)

    def test_23_non_admin_cannot_access_admin_course_edit(self):
        self.login_user("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={"name": "Normal Student", "email": "student@example.com", "password": "Pass123456!", "is_active": "1"},
            follow_redirects=True,
        )
        self.logout_user()

        # Log in as normal student and attempt to edit course
        self.login_user("student@example.com", "Pass123456!")
        res = self.client.get(f"/admin/courses/{self.c1_id}/edit")
        self.assertIn(res.status_code, [302, 403])


if __name__ == "__main__":
    unittest.main()
