import os
import io
import unittest
import tempfile
from datetime import datetime
from werkzeug.security import generate_password_hash
from app import (
    app, get_db_connection, get_db_cursor, init_db, GRADES, get_grade_from_class,
    can_access_course, calculate_course_completion, VIDEO_UPLOAD_FOLDER
)

class LMSFinalTestCase(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key-lms-final"
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["DATABASE_URL"] = f"sqlite:///{self.db_path}"

        self.client = app.test_client()

        with app.app_context():
            init_db()

        self._seed_data()

    def tearDown(self):
        os.close(self.db_fd)
        if os.path.exists(self.db_path):
            try:
                os.unlink(self.db_path)
            except OSError:
                pass

    def _seed_data(self):
        conn = get_db_connection()
        cur = conn.cursor()

        # Seed Admin
        cur.execute(
            """
            INSERT INTO users (name, email, password_hash, role, is_active, created_at)
            VALUES ('Admin User', 'admin@airodrone.com', ?, 'admin', 1, '2026-01-01 00:00:00')
            """,
            (generate_password_hash("AdminPass123!"),),
        )

        # Seed 5 Courses (Grade 1 to Grade 5)
        for g in range(1, 6):
            cur.execute(
                """
                INSERT INTO courses (
                    title, slug, short_description, description, level, grade, image,
                    estimated_duration, is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
                """,
                (
                    f"Grade {g} STEM Course",
                    f"grade-{g}-course",
                    f"Short description for Grade {g}",
                    f"Full curriculum for Grade {g} STEM learners",
                    "Beginner",
                    g,
                    "images/services/drone.jpg",
                    "10 Hours",
                ),
            )

        # Create module, video, quiz, and project for Grade 4 course
        cur.execute("SELECT id FROM courses WHERE slug = 'grade-4-course'")
        c4_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO modules (course_id, title, description, sequence, is_active, created_at, updated_at)
            VALUES (?, 'Flight Mechanics Module', 'Aerodynamics & Drone Assembly', 1, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (c4_id,),
        )
        cur.execute("SELECT id FROM modules WHERE course_id = ?", (c4_id,))
        m1_id = cur.fetchone()["id"]

        # Create a video file for testing streaming
        vid_filename = "test_lesson_1.mp4"
        upload_dir = VIDEO_UPLOAD_FOLDER
        os.makedirs(upload_dir, exist_ok=True)
        test_video_path = os.path.join(upload_dir, vid_filename)
        with open(test_video_path, "wb") as vf:
            vf.write(b"SAMPLE_MP4_VIDEO_BINARY_DATA_FOR_STREAMING_0123456789" * 100)

        cur.execute(
            """
            INSERT INTO course_videos (module_id, title, description, sequence, duration, video_file, is_active, created_at, updated_at)
            VALUES (?, 'Lesson 1: Introduction to Multirotors', 'Intro to drone aerodynamics', 1, '10:00', ?, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (m1_id, vid_filename),
        )
        cur.execute("SELECT id FROM course_videos WHERE module_id = ?", (m1_id,))
        self.vid_id = cur.fetchone()["id"]

        # Create Quiz
        cur.execute(
            """
            INSERT INTO quizzes (module_id, title, description, passing_score, max_attempts, is_active, created_at, updated_at)
            VALUES (?, 'Flight Physics Quiz', 'Test your knowledge on drone dynamics', 70, 5, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (m1_id,),
        )
        cur.execute("SELECT id FROM quizzes WHERE module_id = ?", (m1_id,))
        self.quiz_id = cur.fetchone()["id"]

        # Create Project
        cur.execute(
            """
            INSERT INTO projects (module_id, title, description, max_marks, deadline, is_active, created_at, updated_at)
            VALUES (?, 'Build Drone Frame Simulation', 'Design and simulate a 4-rotor drone frame.', 100, '2026-12-31', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (m1_id,),
        )
        cur.execute("SELECT id FROM projects WHERE module_id = ?", (m1_id,))
        self.project_id = cur.fetchone()["id"]

        self.c4_id = c4_id
        self.m1_id = m1_id

        # Query all course ids
        for g in range(1, 6):
            cur.execute("SELECT id FROM courses WHERE slug = ?", (f"grade-{g}-course",))
            setattr(self, f"c{g}_id", cur.fetchone()["id"])

        conn.commit()
        cur.close()
        conn.close()

    def login(self, email, password):
        return self.client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=True,
        )

    def logout(self):
        return self.client.get("/logout", follow_redirects=True)

    # ----------------------------------------------------
    # 1. Educational Grade Mapping & Catalog Navigation
    # ----------------------------------------------------
    def test_grade_mapping_helper(self):
        self.assertEqual(get_grade_from_class("1"), 1)
        self.assertEqual(get_grade_from_class("Class 2"), 1)
        self.assertEqual(get_grade_from_class("3"), 2)
        self.assertEqual(get_grade_from_class("5"), 2)
        self.assertEqual(get_grade_from_class("6"), 3)
        self.assertEqual(get_grade_from_class("8"), 3)
        self.assertEqual(get_grade_from_class("9"), 4)
        self.assertEqual(get_grade_from_class("Class 10"), 4)
        self.assertEqual(get_grade_from_class("11"), 5)
        self.assertEqual(get_grade_from_class("12"), 5)
        self.assertIsNone(get_grade_from_class("invalid"))

    def test_public_courses_shows_5_grades(self):
        res = self.client.get("/courses")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Learning Categories", res.data)
        # Should not display individual courses on the categories view
        self.assertNotIn(b"grade-1-course", res.data)
        self.assertNotIn(b"grade-4-course", res.data)
        self.assertNotIn(b"Short description for Grade 1", res.data)

    def test_grade_filtered_catalogue_strict_isolation_all_grades(self):
        for g in range(1, 6):
            res = self.client.get(f"/courses?grade={g}")
            self.assertEqual(res.status_code, 200)
            self.assertIn(f"Grade {g} STEM Course".encode("utf-8"), res.data)
            self.assertIn(f"grade-{g}-course".encode("utf-8"), res.data)
            self.assertIn(b"Back to All Grades", res.data)

            # Ensure courses from all other grades are strictly NOT rendered
            for other_g in range(1, 6):
                if other_g != g:
                    self.assertNotIn(f"Grade {other_g} STEM Course".encode("utf-8"), res.data)
                    self.assertNotIn(f"grade-{other_g}-course".encode("utf-8"), res.data)
                    self.assertNotIn(f"Explore Grade {other_g} Courses".encode("utf-8"), res.data)

    def test_inactive_courses_never_appear_publicly(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            """
            INSERT INTO courses (title, slug, short_description, description, level, grade, is_active, created_at, updated_at)
            VALUES ('Inactive Grade 5 Course', 'inactive-grade-5', 'Hidden description', 'Full desc', 'Advanced', 5, 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """
        )
        conn.commit()
        cur.close()
        conn.close()

        res_g5 = self.client.get("/courses?grade=5")
        self.assertEqual(res_g5.status_code, 200)
        self.assertIn(b"Grade 5 STEM Course", res_g5.data)
        self.assertNotIn(b"Inactive Grade 5 Course", res_g5.data)

    def test_admin_created_course_dynamic_display(self):
        self.login("admin@airodrone.com", "AdminPass123!")
        # Admin creates new active Grade 5 course
        res = self.client.post(
            "/admin/courses/new",
            data={
                "title": "Robotics Exploration",
                "slug": "robotics-exploration",
                "grade": "5",
                "short_description": "Advanced robotics for senior secondary",
                "description": "Comprehensive robotics track",
                "level": "Advanced",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        self.logout()

        # Public Grade 5 page now automatically includes BOTH Grade 5 courses
        res_g5 = self.client.get("/courses?grade=5")
        self.assertEqual(res_g5.status_code, 200)
        self.assertIn(b"Grade 5 STEM Course", res_g5.data)
        self.assertIn(b"Robotics Exploration", res_g5.data)

        # Grade 4 page must NOT show this Grade 5 course
        res_g4 = self.client.get("/courses?grade=4")
        self.assertEqual(res_g4.status_code, 200)
        self.assertNotIn(b"Robotics Exploration", res_g4.data)

    def test_clicking_course_opens_slug(self):
        res = self.client.get("/courses?grade=4")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"/courses/grade-4-course", res.data)

        res_course = self.client.get("/courses/grade-4-course")
        self.assertEqual(res_course.status_code, 200)
        self.assertIn(b"Grade 4 STEM Course", res_course.data)

    # ----------------------------------------------------
    # 2. Admin Course Management Grade Hierarchy Tests
    # ----------------------------------------------------
    def test_admin_courses_shows_only_5_grade_cards_and_no_individual_courses(self):
        self.login("admin@airodrone.com", "AdminPass123!")
        res = self.client.get("/admin/courses")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Admin &mdash; Course Management", res.data)
        self.assertIn(b"Grade 1", res.data)
        self.assertIn(b"Grade 2", res.data)
        self.assertIn(b"Grade 3", res.data)
        self.assertIn(b"Grade 4", res.data)
        self.assertIn(b"Grade 5", res.data)
        self.assertIn(b"Manage Grade 1 Courses", res.data)
        self.assertIn(b"Manage Grade 5 Courses", res.data)

        # Ensure individual course management cards are NOT on this top screen
        self.assertNotIn(b"grade-1-course", res.data)
        self.assertNotIn(b"grade-4-course", res.data)
        self.assertNotIn(b"Short description for Grade 1", res.data)
        self.logout()

    def test_admin_grade_selection_shows_only_courses_for_selected_grade(self):
        self.login("admin@airodrone.com", "AdminPass123!")
        for g in range(1, 6):
            res = self.client.get(f"/admin/courses?grade={g}")
            self.assertEqual(res.status_code, 200)
            self.assertIn(f"Grade {g} STEM Course".encode("utf-8"), res.data)
            self.assertIn(f"+ Add Course to Grade {g}".encode("utf-8"), res.data)
            self.assertIn(b"Manage Course &rarr;", res.data)
            self.assertIn(b"Back to All Grades", res.data)

            # Ensure courses from other grades are NOT present
            for other_g in range(1, 6):
                if other_g != g:
                    self.assertNotIn(f"Grade {other_g} STEM Course".encode("utf-8"), res.data)
                    self.assertNotIn(f"Manage Grade {other_g} Courses".encode("utf-8"), res.data)
        self.logout()

    def test_admin_add_course_prepopulates_selected_grade(self):
        self.login("admin@airodrone.com", "AdminPass123!")
        res_form = self.client.get("/admin/courses/new?grade=5")
        self.assertEqual(res_form.status_code, 200)
        self.assertIn(b'value="5"', res_form.data)
        self.assertIn(b'data-grade="5"', res_form.data)

        # Create course in Grade 5
        res_post = self.client.post(
            "/admin/courses/new",
            data={
                "title": "Senior AI Labs",
                "slug": "senior-ai-labs",
                "grade": "5",
                "short_description": "Advanced AI curriculum",
                "description": "Deep learning and neural networks",
                "level": "Advanced",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(res_post.status_code, 200)

        # Verify it appears on Admin Grade 5 page
        res_g5 = self.client.get("/admin/courses?grade=5")
        self.assertEqual(res_g5.status_code, 200)
        self.assertIn(b"Senior AI Labs", res_g5.data)

        # Verify it does NOT appear on Admin Grade 4 page
        res_g4 = self.client.get("/admin/courses?grade=4")
        self.assertEqual(res_g4.status_code, 200)
        self.assertNotIn(b"Senior AI Labs", res_g4.data)
        self.logout()

    def test_admin_deactivate_course_changes_status_without_deleting(self):
        self.login("admin@airodrone.com", "AdminPass123!")
        res_toggle = self.client.post(f"/admin/courses/{self.c4_id}/toggle-active", follow_redirects=True)
        self.assertEqual(res_toggle.status_code, 200)

        # In Admin view, it is still listed under Grade 4 with Inactive status
        res_g4_admin = self.client.get("/admin/courses?grade=4")
        self.assertEqual(res_g4_admin.status_code, 200)
        self.assertIn(b"Grade 4 STEM Course", res_g4_admin.data)
        self.assertIn(b"Inactive", res_g4_admin.data)

        # In Public view, it is hidden from students
        self.logout()
        res_g4_public = self.client.get("/courses?grade=4")
        self.assertEqual(res_g4_public.status_code, 200)
        self.assertNotIn(b"Grade 4 STEM Course", res_g4_public.data)

    # ----------------------------------------------------
    # 2. Access Control via Class and Grade Allocation
    # ----------------------------------------------------
    def test_student_class_allocation_and_access(self):
        self.login("admin@airodrone.com", "AdminPass123!")
        # Create student in Class 10 (which maps to Grade 4)
        res = self.client.post(
            "/admin/users/new",
            data={
                "name": "Grade 4 Student",
                "email": "g4student@school.com",
                "password": "Pass123456!",
                "student_class": "10",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id FROM users WHERE email = 'g4student@school.com'")
        u_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        # Before admin allocates course, student has NO access
        self.logout()
        self.login("g4student@school.com", "Pass123456!")
        res_no_acc = self.client.get("/courses/grade-4-course")
        self.assertIn(b"Course access has not been assigned to your account", res_no_acc.data)
        self.logout()

        # Admin allocates Grade 4 course
        self.login("admin@airodrone.com", "AdminPass123!")
        res_alloc = self.client.post(
            f"/admin/users/{u_id}/courses/assign",
            data={"course_id": str(self.c4_id)},
            follow_redirects=True,
        )
        self.assertEqual(res_alloc.status_code, 200)
        self.assertIn(b"Course access to", res_alloc.data)
        self.logout()

        # Log in as Class 10 student
        self.login("g4student@school.com", "Pass123456!")

        # Should have access to Grade 4 course curriculum
        res_det = self.client.get("/courses/grade-4-course")
        self.assertEqual(res_det.status_code, 200)
        self.assertIn(b"Flight Mechanics Module", res_det.data)
        self.assertIn(b"Lesson 1: Introduction to Multirotors", res_det.data)
        self.assertIn(b"Build Drone Frame Simulation", res_det.data)

        # Dashboard should list Grade 4 course
        res_dash = self.client.get("/dashboard")
        self.assertEqual(res_dash.status_code, 200)
        self.assertIn(b"Grade 4 STEM Course", res_dash.data)

    # ----------------------------------------------------
    # 3. Video Streaming & Seek Prevention
    # ----------------------------------------------------
    def test_video_streaming_endpoint_range_request(self):
        # Create student and assign Grade 4 course
        self.login("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={
                "name": "Stream Student",
                "email": "streamer@school.com",
                "password": "Pass123456!",
                "student_class": "9",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id FROM users WHERE email = 'streamer@school.com'")
        u_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        self.client.post(f"/admin/users/{u_id}/courses/assign", data={"course_id": str(self.c4_id)}, follow_redirects=True)
        self.logout()

        self.login("streamer@school.com", "Pass123456!")

        # Request partial byte range
        headers = {"Range": "bytes=0-100"}
        res = self.client.get(f"/courses/video/{self.vid_id}/stream", headers=headers)
        self.assertEqual(res.status_code, 206)
        self.assertEqual(res.headers.get("Accept-Ranges"), "bytes")
        self.assertIn("bytes 0-100/", res.headers.get("Content-Range"))
        self.assertEqual(len(res.data), 101)

    def test_video_progress_and_completion_sync(self):
        self.login("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={
                "name": "Progress Student",
                "email": "progress@school.com",
                "password": "Pass123456!",
                "student_class": "9",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id FROM users WHERE email = 'progress@school.com'")
        u_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        self.client.post(f"/admin/users/{u_id}/courses/assign", data={"course_id": str(self.c4_id)}, follow_redirects=True)
        self.logout()

        self.login("progress@school.com", "Pass123456!")

        # Send 50% progress update
        res = self.client.post(
            f"/courses/video/{self.vid_id}/progress",
            json={
                "watched_seconds": 300,
                "duration_seconds": 600,
                "event": "timeupdate",
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["progress"]["completion_percentage"], 50.0)

    # ----------------------------------------------------
    # 4. Student Project Submission & Admin Evaluation
    # ----------------------------------------------------
    def test_student_project_submission_and_evaluation(self):
        self.login("admin@airodrone.com", "AdminPass123!")
        self.client.post(
            "/admin/users/new",
            data={
                "name": "Project Submitter",
                "email": "projectuser@school.com",
                "password": "Pass123456!",
                "student_class": "10",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id FROM users WHERE email = 'projectuser@school.com'")
        u_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        self.client.post(f"/admin/users/{u_id}/courses/assign", data={"course_id": str(self.c4_id)}, follow_redirects=True)
        self.logout()

        # Student submits project
        self.login("projectuser@school.com", "Pass123456!")
        dummy_pdf = io.BytesIO(b"%PDF-1.4 Drone Simulation Report...")
        res_sub = self.client.post(
            f"/courses/grade-4-course/module/{self.m1_id}/project",
            data={
                "submission_text": "Completed the CAD modeling and stability calculations.",
                "submission_file": (dummy_pdf, "drone_sim_report.pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(res_sub.status_code, 200)
        self.assertIn(b"Your project has been submitted successfully", res_sub.data)
        self.logout()

        # Admin evaluates submission
        self.login("admin@airodrone.com", "AdminPass123!")

        # 1. View submissions list - check To Evaluate tab
        res_list = self.client.get(f"/admin/projects/{self.project_id}/submissions")
        self.assertEqual(res_list.status_code, 200)
        self.assertIn(b"To Evaluate (1)", res_list.data)
        self.assertIn(b"Evaluated (0)", res_list.data)
        self.assertIn(b"Project Submitter", res_list.data)

        # Get submission id
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id FROM project_submissions WHERE project_id = %s", (self.project_id,))
        sub_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        # 2. Open Evaluation GET page - must NOT crash with 'project' is undefined
        res_eval_get = self.client.get(f"/admin/submissions/{sub_id}/evaluate")
        self.assertEqual(res_eval_get.status_code, 200)
        self.assertIn(b"Evaluate Student Submission", res_eval_get.data)
        self.assertIn(b"Awarded Marks", res_eval_get.data)
        self.assertIn(b"out of 100", res_eval_get.data)

        # 3. Submit evaluation (Marks 95)
        res_eval = self.client.post(
            f"/admin/submissions/{sub_id}/evaluate",
            data={
                "marks": "95",
                "feedback": "Outstanding aerodynamic design and simulation results.",
            },
            follow_redirects=True,
        )
        self.assertEqual(res_eval.status_code, 200)
        self.assertIn(b"evaluated successfully", res_eval.data)
        # Should now be on Evaluated tab with count 1
        self.assertIn(b"To Evaluate (0)", res_eval.data)
        self.assertIn(b"Evaluated (1)", res_eval.data)
        self.assertIn(b"95 / 100", res_eval.data)
        self.logout()

        # Student checks project page and sees evaluation feedback and marks
        self.login("projectuser@school.com", "Pass123456!")
        res_proj_page = self.client.get(f"/courses/grade-4-course/module/{self.m1_id}/project")
        self.assertEqual(res_proj_page.status_code, 200)
        self.assertIn(b"Instructor Evaluation", res_proj_page.data)
        self.assertIn(b"95 / 100", res_proj_page.data)
        self.assertIn(b"Outstanding aerodynamic design", res_proj_page.data)

    # ----------------------------------------------------
    # 5. Course Completion Calculation
    # ----------------------------------------------------
    def test_calculate_course_completion_formula(self):
        # Create student
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            """
            INSERT INTO users (name, email, password_hash, role, student_class, is_active, created_at)
            VALUES ('Calc User', 'calc@example.com', 'dummy_hash', 'student', '10', 1, '2026-01-01 00:00:00')
            """
        )
        conn.commit()
        cur.execute("SELECT id FROM users WHERE email = 'calc@example.com'")
        user_id = cur.fetchone()["id"]

        cur.execute("SELECT id FROM users WHERE email = 'admin@airodrone.com'")
        admin_id = cur.fetchone()["id"]

        # Initial state: 0% completion (3 items total: 1 video, 1 quiz, 1 project)
        comp, _, _ = calculate_course_completion(user_id, self.c4_id)
        self.assertEqual(comp, 0.0)

        # Step 2: Watched video 100% (adds 100%)
        cur.execute(
            """
            INSERT INTO video_progress (user_id, video_id, watched_seconds, completed, completion_percentage, first_started_at, last_watched_at, created_at, updated_at)
            VALUES (?, ?, 600, 1, 100.0, '2026-01-01 00:00:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (user_id, self.vid_id),
        )
        conn.commit()
        # 100 / 3 = 33.3%
        comp, _, _ = calculate_course_completion(user_id, self.c4_id)
        self.assertAlmostEqual(comp, 33.3, places=1)

        # Step 3: Passed quiz (adds 100%)
        cur.execute(
            """
            INSERT INTO quiz_attempts (quiz_id, user_id, score, total_questions, correct_answers, passed, attempt_number, is_invalidated, started_at, submitted_at)
            VALUES (?, ?, 100.0, 10, 10, 1, 1, 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (self.quiz_id, user_id),
        )
        conn.commit()
        # (100 + 100) / 3 = 66.7%
        comp, _, _ = calculate_course_completion(user_id, self.c4_id)
        self.assertAlmostEqual(comp, 66.7, places=1)

        # Step 4: Submitted project evaluated (adds 100%)
        cur.execute(
            """
            INSERT INTO project_submissions (project_id, user_id, submission_text, status, marks, feedback, evaluated_by, evaluated_at, submitted_at, updated_at)
            VALUES (?, ?, 'My submission', 'evaluated', 90, 'Good job', ?, '2026-01-01 00:00:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (self.project_id, user_id, admin_id),
        )
        conn.commit()
        # (100 + 100 + 100) / 3 = 100.0%
        comp, _, _ = calculate_course_completion(user_id, self.c4_id)
        self.assertEqual(comp, 100.0)

        cur.close()
        conn.close()

    # ----------------------------------------------------
    # 6. Strict Grade-Based Student Course Allocation Tests
    # ----------------------------------------------------
    def test_student_course_allocation_strict_grade_matching(self):
        """
        Comprehensive test for:
        1. Class 10 maps to Grade 4.
        2. Admin sees only Grade 4 courses when allocating courses to Class 10.
        3. Admin can give Grade 4 course access.
        4. Student receives access after admin gives it.
        5. Admin can remove the student's course access.
        6. Removed course is no longer accessible to that student.
        7. Grade 5 courses cannot be assigned to a Grade 4 student.
        8. Grade 1-3 courses cannot be assigned to a Grade 4 student.
        9. Course creation/editing still supports all Grades 1-5.
        10. Protected content authorization is strictly enforced.
        """
        self.login("admin@airodrone.com", "AdminPass123!")

        # 1. Create student in Class 10 (maps to Grade 4)
        res_create = self.client.post(
            "/admin/users/new",
            data={
                "name": "Class 10 Student",
                "email": "class10@school.com",
                "password": "Pass123456!",
                "student_class": "10",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(res_create.status_code, 200)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id FROM users WHERE email = 'class10@school.com'")
        student_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        # 2. Admin opens /admin/users/<student_id>/courses
        # Should show ONLY Grade 4 courses (NOT Grade 1, 2, 3, or 5)
        res_view = self.client.get(f"/admin/users/{student_id}/courses")
        self.assertEqual(res_view.status_code, 200)
        self.assertIn(b"Class 10", res_view.data)
        self.assertIn(b"Grade 4", res_view.data)
        self.assertIn(b"Available Courses", res_view.data)
        self.assertIn(b"Grade 4 STEM Course", res_view.data)
        self.assertIn(b"+ Assign Course", res_view.data)

        # Ensure other grades are NOT displayed in this student's allocation list
        self.assertNotIn(b"Grade 1 STEM Course", res_view.data)
        self.assertNotIn(b"Grade 2 STEM Course", res_view.data)
        self.assertNotIn(b"Grade 3 STEM Course", res_view.data)
        self.assertNotIn(b"Grade 5 STEM Course", res_view.data)

        # 7 & 8. Verify admin CANNOT assign courses from other grades (e.g. Grade 1, 2, 3, 5)
        for invalid_course_id in [self.c1_id, self.c2_id, self.c3_id, self.c5_id]:
            res_invalid = self.client.post(
                f"/admin/users/{student_id}/courses/assign",
                data={"course_id": str(invalid_course_id)},
                follow_redirects=True,
            )
            self.assertEqual(res_invalid.status_code, 200)
            self.assertIn(b"Course cannot be assigned because it belongs to Grade", res_invalid.data)

        # Verify student still has no access to any course
        self.logout()
        self.login("class10@school.com", "Pass123456!")
        res_stud_no_acc = self.client.get("/courses/grade-4-course")
        self.assertIn(b"Course access has not been assigned to your account", res_stud_no_acc.data)
        self.logout()

        # 3. Admin gives access to Grade 4 course
        self.login("admin@airodrone.com", "AdminPass123!")
        res_give = self.client.post(
            f"/admin/users/{student_id}/courses/assign",
            data={"course_id": str(self.c4_id)},
            follow_redirects=True,
        )
        self.assertEqual(res_give.status_code, 200)
        self.assertIn(b"Course access to", res_give.data)
        self.assertIn(b"Remove Access", res_give.data)
        self.logout()

        # 4. Student receives access after admin gives it
        self.login("class10@school.com", "Pass123456!")
        res_stud_acc = self.client.get("/courses/grade-4-course")
        self.assertEqual(res_stud_acc.status_code, 200)
        self.assertIn(b"Flight Mechanics Module", res_stud_acc.data)
        self.assertNotIn(b"Course access has not been assigned to your account", res_stud_acc.data)
        self.logout()

        # 5. Admin removes student course access
        self.login("admin@airodrone.com", "AdminPass123!")
        res_remove = self.client.post(
            f"/admin/users/{student_id}/courses/{self.c4_id}/remove",
            follow_redirects=True,
        )
        self.assertEqual(res_remove.status_code, 200)
        self.assertIn(b"+ Assign Course", res_remove.data)
        self.assertNotIn(b"Remove Access", res_remove.data)
        self.logout()

        # 6. Removed course is no longer accessible to that student
        self.login("class10@school.com", "Pass123456!")
        res_stud_after_remove = self.client.get("/courses/grade-4-course")
        self.assertIn(b"Course access has not been assigned to your account", res_stud_after_remove.data)
        self.logout()

    def test_grade_catalog_routes(self):
        """Test /courses and all 5 educational grade catalogue filters."""
        res_main = self.client.get("/courses")
        self.assertEqual(res_main.status_code, 200)
        self.assertIn(b"Learning Categories", res_main.data)

        for grade in range(1, 6):
            res_grade = self.client.get(f"/courses?grade={grade}")
            self.assertEqual(res_grade.status_code, 200)
            self.assertIn(f"Grade {grade}".encode("utf-8"), res_grade.data)

    def test_course_detail_page_loads_and_renders_curriculum_and_components(self):
        """Test full course detail rendering, curriculum, videos, optional quiz and project."""
        # 1. Test public unauthenticated view on Grade 5 course
        res_g5 = self.client.get("/courses/grade-5-course")
        self.assertEqual(res_g5.status_code, 200)
        self.assertIn(b"Grade 5 STEM Course", res_g5.data)
        self.assertIn(b"Grade 5", res_g5.data)
        self.assertIn(b"Course Content", res_g5.data)
        self.assertIn(b"Course access is provided by your administrator", res_g5.data)

        # 2. Test enrolled student on grade-4-course with video, quiz, and project
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            """
            INSERT INTO users (name, email, password_hash, role, student_class, is_active, created_at)
            VALUES ('Detail Student', 'detail_student@school.com', %s, 'user', '10', 1, '2026-01-01 00:00:00')
            """,
            (generate_password_hash("StudentPass123!"),),
        )
        conn.commit()
        cur.execute("SELECT id FROM users WHERE email = 'detail_student@school.com'")
        student_id = cur.fetchone()["id"]

        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, created_at, updated_at)
            VALUES (%s, %s, 1, %s, %s, %s)
            """,
            (student_id, self.c4_id, now_str, now_str, now_str),
        )
        conn.commit()
        cur.close()
        conn.close()

        self.login("detail_student@school.com", "StudentPass123!")
        res_detail = self.client.get("/courses/grade-4-course")
        self.assertEqual(res_detail.status_code, 200)
        self.assertIn(b"Grade 4 STEM Course", res_detail.data)
        self.assertIn(b"Grade 4", res_detail.data)
        self.assertIn(b"Classes 9", res_detail.data)
        self.assertIn(b"Flight Mechanics Module", res_detail.data)
        self.assertIn(b"Lesson 1: Introduction to Multirotors", res_detail.data)
        self.assertIn(b"Watch Video", res_detail.data)
        self.assertIn(b"Flight Physics Quiz", res_detail.data)
        self.assertIn(b"Build Drone Frame Simulation", res_detail.data)
        self.assertNotIn(b"Course access has not been assigned to your account", res_detail.data)
        self.logout()

    def test_video_player_sidebar_includes_optional_quiz_and_project(self):
        """Test video player sidebar includes active quizzes and projects, and omits them when absent."""
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            """
            INSERT INTO users (name, email, password_hash, role, student_class, is_active, created_at)
            VALUES ('Video Sidebar Student', 'sidebar_student@school.com', %s, 'user', '10', 1, '2026-01-01 00:00:00')
            """,
            (generate_password_hash("StudentPass123!"),),
        )
        conn.commit()
        cur.execute("SELECT id FROM users WHERE email = 'sidebar_student@school.com'")
        student_id = cur.fetchone()["id"]

        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, created_at, updated_at)
            VALUES (%s, %s, 1, %s, %s, %s)
            """,
            (student_id, self.c4_id, now_str, now_str, now_str),
        )

        # Create Module 2 for Course 4 with ONLY a video (no quiz, no project)
        cur.execute(
            """
            INSERT INTO modules (course_id, title, description, sequence, is_active, created_at, updated_at)
            VALUES (%s, 'Module 2: Advanced Avionics', 'Avionics description', 2, 1, %s, %s)
            """,
            (self.c4_id, now_str, now_str),
        )
        conn.commit()
        cur.execute("SELECT id FROM modules WHERE course_id = %s AND sequence = 2", (self.c4_id,))
        m2_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO course_videos (module_id, title, description, sequence, duration, video_file, is_active, created_at, updated_at)
            VALUES (%s, 'Lesson 2: Avionics Sensors', 'Sensor description', 1, '12:00', 'test_lesson_1.mp4', 1, %s, %s)
            """,
            (m2_id, now_str, now_str),
        )
        conn.commit()
        cur.close()
        conn.close()

        self.login("sidebar_student@school.com", "StudentPass123!")
        res = self.client.get(f"/courses/grade-4-course/video/{self.vid_id}")
        self.assertEqual(res.status_code, 200)

        # Module 1 has video, quiz, and project -> All must appear
        self.assertIn(b"Flight Mechanics Module", res.data)
        self.assertIn(b"Lesson 1: Introduction to Multirotors", res.data)
        self.assertIn(b"Quiz: Flight Physics Quiz", res.data)
        self.assertIn(b"Project: Build Drone Frame Simulation", res.data)

        # Module 2 has only video -> Video appears, but no quiz or project for Mod 2
        self.assertIn(b"Module 2: Advanced Avionics", res.data)
        self.assertIn(b"Lesson 2: Avionics Sensors", res.data)
        self.assertNotIn(b"Quiz: None", res.data)
        self.assertNotIn(b"Project: None", res.data)
        self.logout()

    def test_video_player_speed_restrictions_and_anti_seek_elements(self):
        """Test video player UI enforces 1.25x max speed when incomplete and unlocks features when completed."""
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            """
            INSERT INTO users (name, email, password_hash, role, student_class, is_active, created_at)
            VALUES ('Speed Test Student', 'speed_student@school.com', %s, 'user', '10', 1, '2026-01-01 00:00:00')
            """,
            (generate_password_hash("StudentPass123!"),),
        )
        conn.commit()
        cur.execute("SELECT id FROM users WHERE email = 'speed_student@school.com'")
        student_id = cur.fetchone()["id"]

        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, created_at, updated_at)
            VALUES (%s, %s, 1, %s, %s, %s)
            """,
            (student_id, self.c4_id, now_str, now_str, now_str),
        )
        conn.commit()
        cur.close()
        conn.close()

        # 1. Incomplete video view
        self.login("speed_student@school.com", "StudentPass123!")
        res_incomplete = self.client.get(f"/courses/grade-4-course/video/{self.vid_id}")
        self.assertEqual(res_incomplete.status_code, 200)
        self.assertIn(b'data-speed="0.5"', res_incomplete.data)
        self.assertIn(b'data-speed="0.75"', res_incomplete.data)
        self.assertIn(b'data-speed="1.0"', res_incomplete.data)
        self.assertIn(b'data-speed="1.25"', res_incomplete.data)
        self.assertIn(b"Max 1.25x on first watch", res_incomplete.data)
        self.assertIn(b"Please watch the video before skipping ahead.", res_incomplete.data)

        # 2. Mark video completed in database
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            """
            INSERT INTO video_progress (user_id, video_id, watched_seconds, duration_seconds, completion_percentage, completed, first_started_at, last_watched_at, completed_at, created_at, updated_at)
            VALUES (%s, %s, 600.0, 600.0, 100.0, 1, %s, %s, %s, %s, %s)
            """,
            (student_id, self.vid_id, now_str, now_str, now_str, now_str, now_str),
        )
        conn.commit()
        cur.close()
        conn.close()

        # 3. Completed video view
        res_complete = self.client.get(f"/courses/grade-4-course/video/{self.vid_id}")
        self.assertEqual(res_complete.status_code, 200)
        self.assertIn(b"Completed \xe2\x80\x94 Normal seeking unlocked", res_complete.data)
        self.logout()

    def test_admin_course_create_with_grade_and_thumbnail_upload(self):
        """Test admin course creation with Grade 1 & 5 and thumbnail upload."""
        self.login("admin@steroaim.com", "admin123")

        # 1. Create Grade 1 course with image upload
        img_data = io.BytesIO(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")
        res1 = self.client.post(
            "/admin/courses/new",
            data={
                "title": "Grade 1 STEM Foundations",
                "slug": "grade-1-stem-foundations",
                "description": "Foundational STEM course for early learners.",
                "level": "Beginner",
                "grade": "1",
                "thumbnail_file": (img_data, "stem_thumb.png"),
                "is_active": "1",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(res1.status_code, 200)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id, grade, image FROM courses WHERE slug = 'grade-1-stem-foundations'")
        c1 = cur.fetchone()
        self.assertIsNotNone(c1)
        self.assertEqual(c1["grade"], 1)
        self.assertTrue(c1["image"].startswith("uploads/courses/course_"))
        self.assertTrue(c1["image"].endswith(".png"))

        # 2. Create Grade 5 course with default image
        res2 = self.client.post(
            "/admin/courses/new",
            data={
                "title": "Grade 5 Quantum & AI",
                "slug": "grade-5-quantum-ai",
                "description": "Advanced quantum computing and AI course for senior students.",
                "level": "Advanced",
                "grade": "5",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(res2.status_code, 200)

        cur.execute("SELECT id, grade, image FROM courses WHERE slug = 'grade-5-quantum-ai'")
        c5 = cur.fetchone()
        self.assertIsNotNone(c5)
        self.assertEqual(c5["grade"], 5)
        self.assertIn(c5["image"], ["", "images/services/drone.jpg"])

        cur.close()
        conn.close()
        self.logout()

    def test_admin_course_create_with_invalid_image_format_rejected(self):
        """Test admin course creation rejects invalid thumbnail file types."""
        self.login("admin@steroaim.com", "admin123")
        invalid_file = io.BytesIO(b"binary executable payload")
        res = self.client.post(
            "/admin/courses/new",
            data={
                "title": "Invalid Image Course",
                "slug": "invalid-image-course",
                "description": "Course with invalid thumbnail file.",
                "level": "Beginner",
                "grade": "1",
                "thumbnail_file": (invalid_file, "exploit.exe"),
                "is_active": "1",
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Invalid image format", res.data)
        self.logout()

    def test_admin_course_edit_preserves_thumbnail_or_replaces(self):
        """Test admin course edit preserves existing image or updates when new image uploaded."""
        self.login("admin@steroaim.com", "admin123")

        # 1. Edit without upload -> image preserved
        res_edit1 = self.client.post(
            f"/admin/courses/{self.c4_id}/edit",
            data={
                "title": "Grade 4 STEM Course Updated",
                "slug": "grade-4-stem-course-updated",
                "description": "Updated course description.",
                "level": "Intermediate",
                "grade": "4",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(res_edit1.status_code, 200)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT image FROM courses WHERE id = %s", (self.c4_id,))
        course_img1 = cur.fetchone()["image"]
        self.assertEqual(course_img1, "images/services/drone.jpg")

        # 2. Edit with new image upload -> image updated
        new_img = io.BytesIO(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb")
        res_edit2 = self.client.post(
            f"/admin/courses/{self.c4_id}/edit",
            data={
                "title": "Grade 4 STEM Course Updated 2",
                "slug": "grade-4-stem-course-updated-2",
                "description": "Updated course description 2.",
                "level": "Intermediate",
                "grade": "4",
                "thumbnail_file": (new_img, "new_banner.jpg"),
                "is_active": "1",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(res_edit2.status_code, 200)

        cur.execute("SELECT image FROM courses WHERE id = %s", (self.c4_id,))
        course_img2 = cur.fetchone()["image"]
        self.assertTrue(course_img2.startswith("uploads/courses/course_"))
        self.assertTrue(course_img2.endswith(".jpg"))

        cur.close()
        conn.close()
        self.logout()

    def test_admin_student_course_allocation_and_cross_grade_rejection(self):
        """Test student course allocation page and strict backend rejection of cross-grade course assignments."""
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            """
            INSERT INTO users (name, email, password_hash, role, student_class, is_active, created_at)
            VALUES ('Class 4 Student', 'class4_student@school.com', %s, 'user', '4', 1, '2026-01-01 00:00:00')
            """,
            (generate_password_hash("StudentPass123!"),),
        )
        conn.commit()
        cur.execute("SELECT id FROM users WHERE email = 'class4_student@school.com'")
        student_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        self.login("admin@steroaim.com", "admin123")

        # 1. View course allocation page for Class 4 (Grade 2) student
        res_page = self.client.get(f"/admin/users/{student_id}/courses")
        self.assertEqual(res_page.status_code, 200)
        self.assertIn(b"Class 4", res_page.data)
        self.assertIn(b"Grade 2", res_page.data)
        self.assertIn(b"Available Courses", res_page.data)
        self.assertIn(b"Grade 2 STEM Course", res_page.data)

        # Ensure Grade 1, 3, 4, 5 courses are NOT in the list
        self.assertNotIn(b"Grade 1 STEM Course", res_page.data)
        self.assertNotIn(b"Grade 3 STEM Course", res_page.data)
        self.assertNotIn(b"Grade 4 STEM Course", res_page.data)
        self.assertNotIn(b"Grade 5 STEM Course", res_page.data)

        # 2. Assign Grade 2 course -> succeeds
        res_assign = self.client.post(
            f"/admin/users/{student_id}/courses/assign",
            data={"course_id": self.c2_id},
            follow_redirects=True,
        )
        self.assertEqual(res_assign.status_code, 200)
        self.assertIn(b"Course access to", res_assign.data)
        self.assertIn(b"Assigned Courses", res_assign.data)

        # Verify enrollment is active
        self.assertTrue(can_access_course(student_id, self.c2_id))

        # 3. Cross-Grade Backend Rejection: Try to assign Grade 1, 3, 4, 5 courses
        for cross_id, cross_grade in [
            (self.c1_id, 1),
            (self.c3_id, 3),
            (self.c4_id, 4),
            (self.c5_id, 5),
        ]:
            res_cross = self.client.post(
                f"/admin/users/{student_id}/courses/assign",
                data={"course_id": cross_id},
                follow_redirects=True,
            )
            self.assertEqual(res_cross.status_code, 200)
            self.assertIn(b"Course cannot be assigned because it belongs to Grade", res_cross.data)
            self.assertFalse(can_access_course(student_id, cross_id))

        # 4. Remove Grade 2 access -> succeeds
        res_remove = self.client.post(
            f"/admin/users/{student_id}/courses/{self.c2_id}/remove",
            follow_redirects=True,
        )
        self.assertEqual(res_remove.status_code, 200)
        self.assertIn(b"removed", res_remove.data)
        self.assertFalse(can_access_course(student_id, self.c2_id))

        self.logout()

    def test_20_admin_video_upload_and_edit_lifecycle(self):
        """Verify adding new video with uploaded file and editing metadata/replacing video file."""
        self.login("admin@steroaim.com", "admin123")

        # 1. Add video lesson with MP4 file
        dummy_mp4 = io.BytesIO(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42")
        res_add = self.client.post(
            f"/admin/modules/{self.m1_id}/videos/new",
            data={
                "title": "Aero Design Masterclass",
                "description": "Comprehensive drone aerodynamics walkthrough.",
                "duration": "14:20",
                "sequence": "3",
                "is_active": "1",
                "video_file": (dummy_mp4, "aero_masterclass.mp4"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(res_add.status_code, 200)
        self.assertIn(b"added successfully", res_add.data)

        # Verify in DB
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id, title, duration, video_file FROM course_videos WHERE title = 'Aero Design Masterclass'")
        vid = cur.fetchone()
        self.assertIsNotNone(vid)
        self.assertTrue(vid["video_file"].endswith("aero_masterclass.mp4"))
        vid_id = vid["id"]
        cur.close()
        conn.close()

        # 2. Edit video metadata without uploading a replacement file
        res_edit = self.client.post(
            f"/admin/videos/{vid_id}/edit",
            data={
                "title": "Aero Design Masterclass (Updated)",
                "description": "Updated description.",
                "duration": "15:00",
                "sequence": "3",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(res_edit.status_code, 200)
        self.assertIn(b"updated successfully", res_edit.data)

        # Verify existing file preserved
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT title, duration, video_file FROM course_videos WHERE id = %s", (vid_id,))
        vid_updated = cur.fetchone()
        self.assertEqual(vid_updated["title"], "Aero Design Masterclass (Updated)")
        self.assertEqual(vid_updated["duration"], "15:00")
        self.assertTrue(vid_updated["video_file"].endswith("aero_masterclass.mp4"))
        cur.close()
        conn.close()

        # 3. Edit video and upload replacement file
        replacement_mp4 = io.BytesIO(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42_v2")
        res_replace = self.client.post(
            f"/admin/videos/{vid_id}/edit",
            data={
                "title": "Aero Design Masterclass (Updated)",
                "description": "Updated description.",
                "duration": "15:00",
                "sequence": "3",
                "is_active": "1",
                "video_file": (replacement_mp4, "aero_masterclass_v2.mp4"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(res_replace.status_code, 200)
        self.assertIn(b"updated successfully", res_replace.data)

        # Verify new file saved in DB
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT video_file FROM course_videos WHERE id = %s", (vid_id,))
        vid_replaced = cur.fetchone()
        self.assertTrue(vid_replaced["video_file"].endswith("aero_masterclass_v2.mp4"))
        cur.close()
        conn.close()

        self.logout()


if __name__ == "__main__":
    unittest.main()