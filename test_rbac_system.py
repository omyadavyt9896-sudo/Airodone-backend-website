import os
import unittest
import tempfile
from werkzeug.security import generate_password_hash

from app import app, get_db_connection, init_db, can_access_course, is_teacher_assigned_to_course, get_teacher_assigned_course_ids


class LmsRbacSystemTestCase(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-rbac-secret-key"
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["DATABASE_URL"] = f"sqlite:///{self.db_path}"

        self.client = app.test_client()

        with app.app_context():
            init_db()

        self._seed_rbac_data()

    def tearDown(self):
        os.close(self.db_fd)
        if os.path.exists(self.db_path):
            try:
                os.unlink(self.db_path)
            except OSError:
                pass

    def _seed_rbac_data(self):
        conn = get_db_connection()
        cur = conn.cursor()

        # Password hash
        pw_hash = generate_password_hash("Password123!")

        # 1. Admin
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, 'admin', 1, '2026-01-01 00:00:00')",
            ("Super Admin", "superadmin@airodrone.com", pw_hash)
        )
        self.admin_id = cur.lastrowid

        # 2. Sub-Admin
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, 'sub_admin', 1, '2026-01-01 00:00:00')",
            ("Sub Admin User", "subadmin@airodrone.com", pw_hash)
        )
        self.sub_admin_id = cur.lastrowid

        # 3. Teacher 1 (Assigned to Course 1)
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, 'teacher', 1, '2026-01-01 00:00:00')",
            ("Teacher One", "teacher1@airodrone.com", pw_hash)
        )
        self.teacher_1_id = cur.lastrowid

        # 4. Teacher 2 (Unassigned)
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, 'teacher', 1, '2026-01-01 00:00:00')",
            ("Teacher Two", "teacher2@airodrone.com", pw_hash)
        )
        self.teacher_2_id = cur.lastrowid

        # 5. Student 1 (Enrolled in Course 1)
        cur.execute(
            "INSERT INTO users (name, father_name, student_class, email, password_hash, role, is_active, created_at) VALUES (%s, %s, '5', %s, %s, 'user', 1, '2026-01-01 00:00:00')",
            ("Student Alpha", "Father Alpha", "student_alpha@airodrone.com", pw_hash)
        )
        self.student_1_id = cur.lastrowid

        # 6. Student 2 (Enrolled in Course 2 only)
        cur.execute(
            "INSERT INTO users (name, father_name, student_class, email, password_hash, role, is_active, created_at) VALUES (%s, %s, '5', %s, %s, 'user', 1, '2026-01-01 00:00:00')",
            ("Student Beta", "Father Beta", "student_beta@airodrone.com", pw_hash)
        )
        self.student_2_id = cur.lastrowid

        # Seed Courses
        cur.execute(
            """
            INSERT INTO courses (title, slug, short_description, description, image, level, grade, is_active, created_at, updated_at)
            VALUES ('Course One AI', 'course-one-ai', 'Desc', 'Full desc', '/img1.jpg', 'Beginner', 2, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """
        )
        self.course_1_id = cur.lastrowid

        cur.execute(
            """
            INSERT INTO courses (title, slug, short_description, description, image, level, grade, is_active, created_at, updated_at)
            VALUES ('Course Two Drones', 'course-two-drones', 'Desc', 'Full desc', '/img2.jpg', 'Intermediate', 2, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """
        )
        self.course_2_id = cur.lastrowid

        # Assign Course 1 to Teacher 1
        cur.execute(
            "INSERT INTO teacher_assignments (teacher_id, course_id, assigned_at) VALUES (%s, %s, '2026-01-01 00:00:00')",
            (self.teacher_1_id, self.course_1_id)
        )

        # Enroll Student 1 in Course 1
        cur.execute(
            "INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, assigned_by, created_at, updated_at) VALUES (%s, %s, 1, '2026-01-01 00:00:00', %s, '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            (self.student_1_id, self.course_1_id, self.admin_id)
        )

        # Enroll Student 2 in Course 2
        cur.execute(
            "INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, assigned_by, created_at, updated_at) VALUES (%s, %s, 1, '2026-01-01 00:00:00', %s, '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            (self.student_2_id, self.course_2_id, self.admin_id)
        )

        # Seed Module & Project in Course 1
        cur.execute(
            "INSERT INTO modules (course_id, title, sequence, is_active, created_at, updated_at) VALUES (%s, 'Module 1', 1, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            (self.course_1_id,)
        )
        self.mod_1_id = cur.lastrowid

        cur.execute(
            "INSERT INTO projects (module_id, title, description, max_marks, is_active, created_at, updated_at) VALUES (%s, 'Project 1', 'Build bot', 100, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            (self.mod_1_id,)
        )
        self.project_1_id = cur.lastrowid

        # Seed Project Submission for Student 1
        cur.execute(
            """
            INSERT INTO project_submissions (project_id, user_id, submission_text, status, submitted_at, updated_at)
            VALUES (%s, %s, 'Here is my AI bot code', 'submitted', '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (self.project_1_id, self.student_1_id)
        )
        self.submission_1_id = cur.lastrowid

        # Seed Module & Project in Course 2
        cur.execute(
            "INSERT INTO modules (course_id, title, sequence, is_active, created_at, updated_at) VALUES (%s, 'Module 2', 1, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            (self.course_2_id,)
        )
        self.mod_2_id = cur.lastrowid

        cur.execute(
            "INSERT INTO projects (module_id, title, description, max_marks, is_active, created_at, updated_at) VALUES (%s, 'Project 2', 'Drone flight', 100, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            (self.mod_2_id,)
        )
        self.project_2_id = cur.lastrowid

        cur.execute(
            """
            INSERT INTO project_submissions (project_id, user_id, submission_text, status, submitted_at, updated_at)
            VALUES (%s, %s, 'Here is my drone flight log', 'submitted', '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (self.project_2_id, self.student_2_id)
        )
        self.submission_2_id = cur.lastrowid

        conn.commit()
        conn.close()

    def _login(self, email, password="Password123!"):
        self.client.get("/logout")
        return self.client.post("/login", data={"email": email, "password": password}, follow_redirects=True)

    # ----------------------------------------------------
    # 1. ADMIN PERMISSIONS TESTS
    # ----------------------------------------------------
    def test_admin_can_create_student_teacher_subadmin(self):
        self._login("superadmin@airodrone.com")

        # Create Student
        res = self.client.post("/admin/users/new", data={
            "name": "New Student", "email": "newstudent@airodrone.com", "password": "Password123!", "role": "user", "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Create Teacher
        res = self.client.post("/admin/users/new", data={
            "name": "New Teacher", "email": "newteacher@airodrone.com", "password": "Password123!", "role": "teacher", "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Create Sub-Admin
        res = self.client.post("/admin/users/new", data={
            "name": "New SubAdmin", "email": "newsubadmin@airodrone.com", "password": "Password123!", "role": "sub_admin", "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    def test_admin_can_access_system_settings_and_audit_logs(self):
        self._login("superadmin@airodrone.com")
        res1 = self.client.get("/admin/learning-catalogue-settings")
        self.assertEqual(res1.status_code, 200)

        res2 = self.client.get("/admin/audit-logs")
        self.assertEqual(res2.status_code, 200)

    def test_admin_can_evaluate_project(self):
        self._login("superadmin@airodrone.com")
        res = self.client.post(f"/admin/submissions/{self.submission_1_id}/evaluate", data={
            "marks": 95, "feedback": "Excellent work!"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    # ----------------------------------------------------
    # 2. SUB-ADMIN PERMISSIONS TESTS (POSITIVE & NEGATIVE)
    # ----------------------------------------------------
    def test_subadmin_can_create_student_and_teacher(self):
        self._login("subadmin@airodrone.com")

        # Can create student
        res1 = self.client.post("/admin/users/new", data={
            "name": "SubAdmin Student", "email": "substudent@airodrone.com", "password": "Password123!", "role": "user", "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res1.status_code, 200)

        # Can create teacher
        res2 = self.client.post("/admin/users/new", data={
            "name": "SubAdmin Teacher", "email": "subteacher@airodrone.com", "password": "Password123!", "role": "teacher", "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res2.status_code, 200)

    def test_subadmin_cannot_create_subadmin_or_admin(self):
        self._login("subadmin@airodrone.com")

        # Sub-Admin attempts to post role=sub_admin -> MUST RETURN 403 FORBIDDEN
        res1 = self.client.post("/admin/users/new", data={
            "name": "Hacked SubAdmin", "email": "hackedsub@airodrone.com", "password": "Password123!", "role": "sub_admin", "is_active": "1"
        })
        self.assertEqual(res1.status_code, 403)

        # Sub-Admin attempts to post role=admin -> MUST RETURN 403 FORBIDDEN
        res2 = self.client.post("/admin/users/new", data={
            "name": "Hacked Admin", "email": "hackedadmin@airodrone.com", "password": "Password123!", "role": "admin", "is_active": "1"
        })
        self.assertEqual(res2.status_code, 403)

    def test_subadmin_cannot_edit_or_deactivate_subadmin_or_admin(self):
        self._login("subadmin@airodrone.com")

        # Cannot edit admin
        res1 = self.client.get(f"/admin/users/{self.admin_id}/edit")
        self.assertEqual(res1.status_code, 403)

        # Cannot toggle active status on admin
        res2 = self.client.post(f"/admin/users/{self.admin_id}/toggle-active")
        self.assertEqual(res2.status_code, 403)

    def test_subadmin_cannot_access_system_settings_or_audit_logs(self):
        self._login("subadmin@airodrone.com")

        res1 = self.client.get("/admin/learning-catalogue-settings")
        self.assertEqual(res1.status_code, 403)

        res2 = self.client.get("/admin/audit-logs")
        self.assertEqual(res2.status_code, 403)

    # ----------------------------------------------------
    # 3. TEACHER PERMISSIONS TESTS (STRICT SCOPE & NO ADMIN)
    # ----------------------------------------------------
    def test_teacher_can_access_assigned_student_and_course(self):
        self._login("teacher1@airodrone.com")

        # Teacher 1 is assigned to Course 1, and Student 1 is in Course 1
        self.assertTrue(can_access_course(self.teacher_1_id, self.course_1_id))
        self.assertTrue(is_teacher_assigned_to_course(self.teacher_1_id, self.course_1_id))

        # Teacher 1 can view Student 1 progress
        res1 = self.client.get(f"/admin/users/{self.student_1_id}/progress")
        self.assertEqual(res1.status_code, 200)

        # Teacher 1 can view Project 1 submissions (Course 1)
        res2 = self.client.get(f"/admin/projects/{self.project_1_id}/submissions")
        self.assertEqual(res2.status_code, 200)

        # Teacher 1 can evaluate Submission 1 (Course 1)
        res3 = self.client.post(f"/admin/submissions/{self.submission_1_id}/evaluate", data={
            "marks": 90, "feedback": "Good job!"
        }, follow_redirects=True)
        self.assertEqual(res3.status_code, 200)

    def test_teacher_cannot_access_unassigned_course_or_student(self):
        self._login("teacher1@airodrone.com")

        # Teacher 1 is NOT assigned to Course 2
        self.assertFalse(can_access_course(self.teacher_1_id, self.course_2_id))

        # Teacher 1 attempting to view Student 2 progress (Student 2 is in Course 2 only) -> MUST RETURN 403
        res1 = self.client.get(f"/admin/users/{self.student_2_id}/progress")
        self.assertEqual(res1.status_code, 403)

        # Teacher 1 attempting to view Project 2 submissions (Course 2) -> MUST RETURN 403
        res2 = self.client.get(f"/admin/projects/{self.project_2_id}/submissions")
        self.assertEqual(res2.status_code, 403)

        # Teacher 1 attempting to evaluate Submission 2 (Course 2) -> MUST RETURN 403
        res3 = self.client.post(f"/admin/submissions/{self.submission_2_id}/evaluate", data={
            "marks": 80, "feedback": "Unauthorized attempt"
        })
        self.assertEqual(res3.status_code, 403)

    def test_teacher_cannot_perform_user_or_course_management(self):
        self._login("teacher1@airodrone.com")

        # Global admin listings are blocked -> 403
        self.assertEqual(self.client.get("/admin/courses").status_code, 403)
        self.assertEqual(self.client.get("/admin/users").status_code, 403)

        # Cannot access user creation -> 403
        res1 = self.client.get("/admin/users/new")
        self.assertEqual(res1.status_code, 403)

        res2 = self.client.post("/admin/users/new", data={
            "name": "Illegal Student", "email": "illegal@airodrone.com", "password": "Password123!", "role": "user"
        })
        self.assertEqual(res2.status_code, 403)

        # Cannot edit student -> 403
        res3 = self.client.get(f"/admin/users/{self.student_1_id}/edit")
        self.assertEqual(res3.status_code, 403)

        # Cannot create global course -> 403
        res4 = self.client.get("/admin/courses/new")
        self.assertEqual(res4.status_code, 403)

        # Cannot delete course -> 403
        res5 = self.client.post(f"/admin/courses/{self.course_1_id}/delete")
        self.assertEqual(res5.status_code, 403)

        # Cannot deactivate course -> 403
        res6 = self.client.post(f"/admin/courses/{self.course_1_id}/toggle-active")
        self.assertEqual(res6.status_code, 403)

        # Cannot access system settings or audit logs -> 403
        res7 = self.client.get("/admin/learning-catalogue-settings")
        self.assertEqual(res7.status_code, 403)

        res8 = self.client.get("/admin/audit-logs")
        self.assertEqual(res8.status_code, 403)

    def test_teacher_academic_management_in_assigned_course(self):
        self._login("teacher1@airodrone.com")

        # Assigned Course 1
        # 1. Open course detail -> 200
        res1 = self.client.get(f"/admin/courses/{self.course_1_id}")
        self.assertEqual(res1.status_code, 200)

        # 2. Add module form -> 200
        res2 = self.client.get(f"/admin/courses/{self.course_1_id}/modules/new")
        self.assertEqual(res2.status_code, 200)

        # 3. Add video form -> 200
        res3 = self.client.get(f"/admin/modules/{self.mod_1_id}/videos/new")
        self.assertEqual(res3.status_code, 200)

        # 4. Add quiz form -> 200
        res4 = self.client.get(f"/admin/modules/{self.mod_1_id}/quiz/new")
        self.assertEqual(res4.status_code, 200)

        # 5. Add project form -> 200
        res5 = self.client.get(f"/admin/modules/{self.mod_1_id}/project/new")
        self.assertEqual(res5.status_code, 200)

        # 6. Edit project form -> 200
        res6 = self.client.get(f"/admin/projects/{self.project_1_id}/edit")
        self.assertEqual(res6.status_code, 200)

    def test_teacher_academic_management_in_unassigned_course_blocked(self):
        self._login("teacher1@airodrone.com")

        # Unassigned Course 2
        # 1. Open course detail -> 403
        res1 = self.client.get(f"/admin/courses/{self.course_2_id}")
        self.assertEqual(res1.status_code, 403)

        # 2. Add module form -> 403
        res2 = self.client.get(f"/admin/courses/{self.course_2_id}/modules/new")
        self.assertEqual(res2.status_code, 403)

        # 3. Add video form -> 403
        res3 = self.client.get(f"/admin/modules/{self.mod_2_id}/videos/new")
        self.assertEqual(res3.status_code, 403)

        # 4. Add quiz form -> 403
        res4 = self.client.get(f"/admin/modules/{self.mod_2_id}/quiz/new")
        self.assertEqual(res4.status_code, 403)

        # 5. Add project form -> 403
        res5 = self.client.get(f"/admin/modules/{self.mod_2_id}/project/new")
        self.assertEqual(res5.status_code, 403)

        # 6. Edit project form -> 403
        res6 = self.client.get(f"/admin/projects/{self.project_2_id}/edit")
        self.assertEqual(res6.status_code, 403)

    def test_teacher_parameter_tampering_blocked(self):
        self._login("teacher1@airodrone.com")

        # Teacher 1 is assigned to Course 1, but attempts to manipulate URLs to access Course 2 resources
        # 1. Edit module of Course 2 -> 403
        res1 = self.client.get(f"/admin/modules/{self.mod_2_id}/edit")
        self.assertEqual(res1.status_code, 403)

        # 2. Delete module of Course 2 -> 403
        res2 = self.client.post(f"/admin/modules/{self.mod_2_id}/delete")
        self.assertEqual(res2.status_code, 403)

        # 3. Add video to module of Course 2 -> 403
        res3 = self.client.get(f"/admin/modules/{self.mod_2_id}/videos/new")
        self.assertEqual(res3.status_code, 403)

        # 4. Edit project of Course 2 -> 403
        res4 = self.client.get(f"/admin/projects/{self.project_2_id}/edit")
        self.assertEqual(res4.status_code, 403)

        # 5. Delete project of Course 2 -> 403
        res5 = self.client.post(f"/admin/projects/{self.project_2_id}/delete")
        self.assertEqual(res5.status_code, 403)

    # ----------------------------------------------------
    # 4. STUDENT ISOLATION TESTS
    # ----------------------------------------------------
    def test_student_cannot_access_any_admin_routes(self):
        self._login("student_alpha@airodrone.com")

        res1 = self.client.get("/admin")
        self.assertEqual(res1.status_code, 403)

        res2 = self.client.get("/admin/users")
        self.assertEqual(res2.status_code, 403)

    # ----------------------------------------------------
    # 5. LEARNING CATEGORY PERMISSIONS & DEPENDENCY SAFETY TESTS
    # ----------------------------------------------------
    def test_admin_and_subadmin_category_management_and_dependency_safety(self):
        # Admin creates empty category
        self._login("superadmin@airodrone.com")
        res1 = self.client.post("/admin/learning-categories/new", data={
            "name": "Empty Test Domain", "slug": "empty-test-domain", "description": "Empty domain for testing deletion", "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res1.status_code, 200)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM learning_categories WHERE slug = 'empty-test-domain'")
        empty_cat = cur.fetchone()
        empty_cat_id = empty_cat["id"]
        conn.close()

        # Admin edits category
        res2 = self.client.post(f"/admin/learning-categories/{empty_cat_id}/edit", data={
            "name": "Updated Empty Domain", "slug": "empty-test-domain", "description": "Updated description", "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res2.status_code, 200)

        # Admin deactivates category
        res3 = self.client.post(f"/admin/learning-categories/{empty_cat_id}/toggle-active", follow_redirects=True)
        self.assertEqual(res3.status_code, 200)

        # Admin deletes empty category -> ALLOWED
        res4 = self.client.post(f"/admin/learning-categories/{empty_cat_id}/delete", follow_redirects=True)
        self.assertEqual(res4.status_code, 200)

        # Sub-Admin creates category, links course, and attempts delete -> BLOCKED BY DEPENDENCY SAFETY
        self._login("subadmin@airodrone.com")
        res5 = self.client.post("/admin/learning-categories/new", data={
            "name": "SubAdmin Domain", "slug": "subadmin-domain", "description": "Domain with course", "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res5.status_code, 200)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM learning_categories WHERE slug = 'subadmin-domain'")
        sub_cat_id = cur.fetchone()["id"]
        # Link course_1 to sub_cat_id
        cur.execute("UPDATE courses SET category_id = %s WHERE id = %s", (sub_cat_id, self.course_1_id))
        conn.commit()
        conn.close()

        # Sub-Admin attempts to delete non-empty category -> BLOCKED (shows warning flash message and redirects)
        res6 = self.client.post(f"/admin/learning-categories/{sub_cat_id}/delete", follow_redirects=True)
        self.assertEqual(res6.status_code, 200)
        self.assertIn(b"This category cannot be deleted because it contains courses or learning paths", res6.data)

        # Verify course_1 and submission_1 still exist and remain untouched
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM courses WHERE id = %s", (self.course_1_id,))
        self.assertIsNotNone(cur.fetchone())
        cur.execute("SELECT id FROM project_submissions WHERE id = %s", (self.submission_1_id,))
        self.assertIsNotNone(cur.fetchone())
        conn.close()

    def test_teacher_and_student_category_management_blocked(self):
        # Teacher attempt category management -> 403
        self._login("teacher1@airodrone.com")

        res1 = self.client.get("/admin/learning-categories")
        self.assertEqual(res1.status_code, 403)

        res2 = self.client.get("/admin/learning-categories/new")
        self.assertEqual(res2.status_code, 403)

        res3 = self.client.post("/admin/learning-categories/new", data={"name": "Teacher Cat", "slug": "teacher-cat"})
        self.assertEqual(res3.status_code, 403)

        res4 = self.client.post("/admin/learning-categories/1/delete")
        self.assertEqual(res4.status_code, 403)

        # Student attempt category management -> 403
        self._login("student_alpha@airodrone.com")

        res5 = self.client.get("/admin/learning-categories")
        self.assertEqual(res5.status_code, 403)

        res6 = self.client.post("/admin/learning-categories/1/delete")
        self.assertEqual(res6.status_code, 403)

    # ----------------------------------------------------
    # 6. TEACHER CONSOLE ASSIGNED COURSE VISIBILITY TESTS
    # ----------------------------------------------------
    def test_teacher_assigned_course_appears_in_teacher_console(self):
        # Teacher 1 is assigned to Course One AI
        self._login("teacher1@airodrone.com")
        res = self.client.get("/admin")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Course One AI", res.data)
        self.assertIn(b"Open Course", res.data)

        # Teacher 1 can click and open assigned Course 1 detail view
        res_detail = self.client.get(f"/admin/courses/{self.course_1_id}")
        self.assertEqual(res_detail.status_code, 200)
        self.assertIn(b"Course One AI", res_detail.data)

    def test_teacher_only_sees_assigned_courses(self):
        # Teacher 1 is assigned to Course 1 ('Course One AI') but NOT Course 2 ('Course Two Drones')
        self._login("teacher1@airodrone.com")
        res1 = self.client.get("/admin")
        self.assertEqual(res1.status_code, 200)
        self.assertIn(b"Course One AI", res1.data)
        self.assertNotIn(b"Course Two Drones", res1.data)

        # Teacher 2 is unassigned and should see NO assigned courses
        self._login("teacher2@airodrone.com")
        res2 = self.client.get("/admin")
        self.assertEqual(res2.status_code, 200)
        self.assertNotIn(b"Course One AI", res2.data)
        self.assertNotIn(b"Course Two Drones", res2.data)


if __name__ == "__main__":
    unittest.main()
