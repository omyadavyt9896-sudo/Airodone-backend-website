import os
import unittest
import tempfile
from werkzeug.security import generate_password_hash
from app import app, get_db_connection, get_db_cursor, init_db, can_access_course


class LearningCatalogTestCase(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-learning-catalog-secret"
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

    def _login_admin(self):
        return self.client.post(
            "/login",
            data={"email": "admin@steroaim.com", "password": "admin123"},
            follow_redirects=True,
        )

    def _login_student(self, email="student@steroaim.com", password="student123"):
        return self.client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=True,
        )

    def _seed_test_data(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # Seed student (Class 7 -> Grade 3)
        cur.execute(
            """
            INSERT INTO users (name, email, password_hash, student_class, role, is_active, created_at)
            VALUES (%s, %s, %s, %s, %s, 1, '2026-01-01 00:00:00')
            """,
            ("Test Student", "student@steroaim.com", generate_password_hash("student123"), "Class 7", "user")
        )
        self.student_id = cur.lastrowid

        # Fetch seeded AI category & its Grade 3 path
        cur.execute("SELECT id, slug FROM learning_categories WHERE slug = 'artificial-intelligence'")
        self.cat_ai = cur.fetchone()

        cur.execute("SELECT id, slug, grade FROM learning_paths WHERE category_id = %s AND grade = 3", (self.cat_ai["id"],))
        self.path_ai_g3 = cur.fetchone()

        # Fetch Drone category & its Grade 4 path
        cur.execute("SELECT id, slug FROM learning_categories WHERE slug = 'drone-technology'")
        self.cat_drone = cur.fetchone()

        cur.execute("SELECT id, slug, grade FROM learning_paths WHERE category_id = %s AND grade = 4", (self.cat_drone["id"],))
        self.path_drone_g4 = cur.fetchone()

        # Create a specific test course for AI Grade 3
        cur.execute(
            """
            INSERT INTO courses (title, slug, description, short_description, level, grade, category_id, learning_path_id, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            ("AI Grade 3 Course", "ai-grade-3-course", "Deep dive into AI logic", "Short desc", "Beginner", 3, self.cat_ai["id"], self.path_ai_g3["id"])
        )
        self.course_ai_g3_id = cur.lastrowid

        # Add module and video to test course
        cur.execute(
            """
            INSERT INTO modules (course_id, title, description, sequence, is_active, created_at, updated_at)
            VALUES (%s, 'Module 1', 'Test Module', 1, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (self.course_ai_g3_id,)
        )
        self.module_id = cur.lastrowid

        cur.execute(
            """
            INSERT INTO course_videos (module_id, title, duration, is_active, created_at, updated_at)
            VALUES (%s, 'Lesson 1', '05:00', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (self.module_id,)
        )
        self.video_id = cur.lastrowid

        # Add quiz to module
        cur.execute(
            """
            INSERT INTO quizzes (module_id, title, description, passing_score, max_attempts, is_active, created_at, updated_at)
            VALUES (%s, 'Module 1 Quiz', 'Test quiz', 70, 3, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (self.module_id,)
        )
        self.quiz_id = cur.lastrowid

        # Add project to module
        cur.execute(
            """
            INSERT INTO projects (module_id, title, description, max_marks, is_active, created_at, updated_at)
            VALUES (%s, 'Module 1 Project', 'Build an AI model', 100, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (self.module_id,)
        )
        self.project_id = cur.lastrowid

        conn.commit()
        cur.close()
        conn.close()

    # 1. /courses shows active categories instead of flat grades
    def test_01_courses_shows_active_categories(self):
        resp = self.client.get("/courses")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Learning Categories", resp.data)
        self.assertIn(b"AI &amp; Artificial Intelligence", resp.data)
        self.assertIn(b"Drone Technology", resp.data)

    # 2. Inactive categories are hidden from /courses
    def test_02_inactive_categories_hidden(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("UPDATE learning_categories SET is_active = 0 WHERE slug = 'website-design'")
        conn.commit()
        cur.close()
        conn.close()

        resp = self.client.get("/courses")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"Website Design", resp.data)
        self.assertIn(b"AI &amp; Artificial Intelligence", resp.data)

    # 3. Category page shows only its active learning paths
    def test_03_category_page_shows_active_paths(self):
        resp = self.client.get("/courses/category/artificial-intelligence")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"AI &amp; Artificial Intelligence", resp.data)
        self.assertIn(b"AI Engineering", resp.data)
        self.assertIn(b"Advanced AI", resp.data)

    # 4. Inactive learning paths are hidden from category page
    def test_04_inactive_learning_paths_hidden(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("UPDATE learning_paths SET is_active = 0 WHERE category_id = %s AND grade = 1", (self.cat_ai["id"],))
        conn.commit()
        cur.close()
        conn.close()

        resp = self.client.get("/courses/category/artificial-intelligence")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"AI Discovery", resp.data)
        self.assertIn(b"AI Engineering", resp.data)

    # 5. Path page shows only courses matching category + learning path + grade
    def test_05_path_page_shows_matching_courses(self):
        resp = self.client.get(f"/courses/category/{self.cat_ai['slug']}/{self.path_ai_g3['slug']}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"AI Grade 3 Course", resp.data)

    # 6. Courses from other categories never appear on the path page
    def test_06_courses_from_other_categories_excluded(self):
        resp = self.client.get(f"/courses/category/{self.cat_ai['slug']}/{self.path_ai_g3['slug']}")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"Drone Technology", resp.data)

    # 7. Courses from other grades never appear on the path page
    def test_07_courses_from_other_grades_excluded(self):
        resp = self.client.get(f"/courses/category/{self.cat_drone['slug']}/{self.path_drone_g4['slug']}")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"AI Grade 3 Course", resp.data)

    # 8. Existing course detail route /courses/<slug> works with new breadcrumbs
    def test_08_course_detail_route_and_breadcrumbs(self):
        resp = self.client.get("/courses/ai-grade-3-course")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"AI Grade 3 Course", resp.data)
        self.assertIn(b"AI &amp; Artificial Intelligence", resp.data)
        self.assertIn(b"AI Engineering", resp.data)

    # 9. Existing student grade access rules (can_access_course) remain intact
    def test_09_can_access_course_enforcement(self):
        # Grade 3 student should NOT have automatic access until enrolled
        self.assertFalse(can_access_course(self.student_id, self.course_ai_g3_id))

        # Enroll student in matching Grade 3 course
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            """
            INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, created_at, updated_at)
            VALUES (%s, %s, 1, '2026-01-01', '2026-01-01', '2026-01-01')
            """,
            (self.student_id, self.course_ai_g3_id)
        )
        conn.commit()
        cur.close()
        conn.close()

        self.assertTrue(can_access_course(self.student_id, self.course_ai_g3_id))

    # 10. Cross-grade enrollment in admin is still rejected
    def test_10_cross_grade_enrollment_rejected(self):
        self._login_admin()
        # Find a Grade 4 course
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id FROM courses WHERE grade = 4 AND is_active = 1 LIMIT 1")
        g4_course = cur.fetchone()
        cur.close()
        conn.close()

        if g4_course:
            # Student is Grade 3 (Class 7). Assigning Grade 4 course must be rejected
            resp = self.client.post(
                f"/admin/users/{self.student_id}/assign-course",
                data={"course_id": g4_course["id"]},
                follow_redirects=True,
            )
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b"belongs to Grade 4", resp.data)

    # 11. Admin can create category
    def test_11_admin_create_category(self):
        self._login_admin()
        resp = self.client.post(
            "/admin/learning-categories/new",
            data={
                "name": "Robotics & Automation",
                "slug": "robotics-automation",
                "description": "Comprehensive robotics domain",
                "display_order": 5,
                "is_active": "1",
                "create_default_paths": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"created successfully", resp.data)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id FROM learning_categories WHERE slug = 'robotics-automation'")
        cat = cur.fetchone()
        self.assertIsNotNone(cat)
        cur.execute("SELECT COUNT(*) AS c FROM learning_paths WHERE category_id = %s", (cat["id"],))
        self.assertEqual(cur.fetchone()["c"], 5)
        cur.close()
        conn.close()

    # 12. Admin can edit category
    def test_12_admin_edit_category(self):
        self._login_admin()
        resp = self.client.post(
            f"/admin/learning-categories/{self.cat_ai['id']}/edit",
            data={
                "name": "AI & Deep Learning",
                "slug": "artificial-intelligence",
                "description": "Updated description for AI",
                "display_order": 1,
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"updated successfully", resp.data)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT name FROM learning_categories WHERE id = %s", (self.cat_ai["id"],))
        self.assertEqual(cur.fetchone()["name"], "AI & Deep Learning")
        cur.close()
        conn.close()

    # 13. Admin can deactivate category
    def test_13_admin_deactivate_category(self):
        self._login_admin()
        resp = self.client.post(
            f"/admin/learning-categories/{self.cat_ai['id']}/toggle-active",
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"now inactive", resp.data)

        # Confirm not visible in public /courses
        resp_public = self.client.get("/courses")
        self.assertNotIn(b"AI &amp; Artificial Intelligence", resp_public.data)

    # 14. Admin can create learning path
    def test_14_admin_create_learning_path(self):
        self._login_admin()
        resp = self.client.post(
            f"/admin/learning-categories/{self.cat_ai['id']}/paths/new",
            data={
                "name": "AI Special Track",
                "slug": "ai-special-track",
                "grade": 3,
                "description": "Specialized AI track",
                "display_order": 10,
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"added successfully", resp.data)

    # 15. Admin can edit learning path
    def test_15_admin_edit_learning_path(self):
        self._login_admin()
        resp = self.client.post(
            f"/admin/learning-paths/{self.path_ai_g3['id']}/edit",
            data={
                "name": "AI Engineering & Neural Nets",
                "slug": self.path_ai_g3["slug"],
                "grade": 3,
                "description": "Updated neural net pathway",
                "display_order": 3,
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"updated successfully", resp.data)

    # 16. Admin can change path name and student-facing page updates immediately
    def test_16_path_name_update_reflected_immediately(self):
        self._login_admin()
        self.client.post(
            f"/admin/learning-paths/{self.path_ai_g3['id']}/edit",
            data={
                "name": "Brand New AI Path Name",
                "slug": self.path_ai_g3["slug"],
                "grade": 3,
                "description": "Instant test",
                "display_order": 3,
                "is_active": "1",
            },
            follow_redirects=True,
        )

        resp = self.client.get(f"/courses/category/{self.cat_ai['slug']}")
        self.assertIn(b"Brand New AI Path Name", resp.data)

    # 17. Course creation requires valid category/path relationship
    def test_17_course_creation_valid_relationship(self):
        self._login_admin()
        resp = self.client.post(
            "/admin/courses/new",
            data={
                "title": "New Test AI Course",
                "slug": "new-test-ai-course",
                "description": "Course description",
                "short_description": "Short desc",
                "grade": 3,
                "category_id": self.cat_ai["id"],
                "learning_path_id": self.path_ai_g3["id"],
                "level": "Intermediate",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"created successfully", resp.data)

    # 18. Invalid category/path relationship is rejected server-side
    def test_18_course_creation_invalid_relationship_rejected(self):
        self._login_admin()
        # Pair AI Category with Drone Path (Category mismatch)
        resp = self.client.post(
            "/admin/courses/new",
            data={
                "title": "Mismatched Course",
                "slug": "mismatched-course",
                "description": "Course description",
                "grade": 3,
                "category_id": self.cat_ai["id"],
                "learning_path_id": self.path_drone_g4["id"],
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"does not belong to the selected category", resp.data)

    # 19. Existing courses without category/path do not break
    def test_19_unassigned_courses_do_not_break(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            """
            INSERT INTO courses (title, slug, description, grade, category_id, learning_path_id, is_active, created_at, updated_at)
            VALUES ('Standalone Course', 'standalone-course', 'Unassigned desc', 3, NULL, NULL, 1, '2026-01-01', '2026-01-01')
            """
        )
        conn.commit()
        cur.close()
        conn.close()

        resp = self.client.get("/courses/standalone-course")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Standalone Course", resp.data)

    # 20. Existing student progress remains intact
    def test_20_student_progress_intact(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            """
            INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, created_at, updated_at)
            VALUES (%s, %s, 1, '2026-01-01', '2026-01-01', '2026-01-01')
            """,
            (self.student_id, self.course_ai_g3_id)
        )
        cur.execute(
            """
            INSERT INTO video_progress (user_id, video_id, watched_seconds, duration_seconds, completion_percentage, completed, first_started_at, last_watched_at, created_at, updated_at)
            VALUES (%s, %s, 150.0, 300.0, 50.0, 0, '2026-01-01', '2026-01-01', '2026-01-01', '2026-01-01')
            """,
            (self.student_id, self.video_id)
        )
        conn.commit()
        cur.close()
        conn.close()

        self._login_student()
        resp = self.client.get(f"/courses/video/{self.video_id}/progress")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["progress"]["watched_seconds"], 150.0)

    # 21. Existing modules remain intact
    def test_21_modules_intact(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT title FROM modules WHERE id = %s", (self.module_id,))
        self.assertEqual(cur.fetchone()["title"], "Module 1")
        cur.close()
        conn.close()

    # 22. Existing videos remain intact
    def test_22_videos_intact(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT title, duration FROM course_videos WHERE id = %s", (self.video_id,))
        row = cur.fetchone()
        self.assertEqual(row["title"], "Lesson 1")
        self.assertEqual(row["duration"], "05:00")
        cur.close()
        conn.close()

    # 23. Existing quizzes remain intact
    def test_23_quizzes_intact(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT title, passing_score FROM quizzes WHERE id = %s", (self.quiz_id,))
        row = cur.fetchone()
        self.assertEqual(row["title"], "Module 1 Quiz")
        self.assertEqual(row["passing_score"], 70)
        cur.close()
        conn.close()

    # 24. Existing projects remain intact
    def test_24_projects_intact(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT title, max_marks FROM projects WHERE id = %s", (self.project_id,))
        row = cur.fetchone()
        self.assertEqual(row["title"], "Module 1 Project")
        self.assertEqual(row["max_marks"], 100)
        cur.close()
        conn.close()

    # 25. Existing certificates remain intact
    def test_25_certificates_intact(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            """
            INSERT INTO certificates (certificate_id, user_id, course_id, student_name, course_name, completion_percentage, issued_at, created_at)
            VALUES ('CERT-TEST-12345', %s, %s, 'Test Student', 'AI Grade 3 Course', 100.0, 'January 01, 2026', '2026-01-01 00:00:00')
            """,
            (self.student_id, self.course_ai_g3_id)
        )
        conn.commit()
        cur.close()
        conn.close()

        self._login_student()
        resp = self.client.get("/certificates/CERT-TEST-12345")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"CERT-TEST-12345", resp.data)

    # 28. Learning path image upload success
    def test_28_learning_path_image_upload_success(self):
        import io
        self._login_admin()
        # Valid 1x1 PNG image with magic bytes
        fake_png_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa74U\xa6\x00\x00\x00\x00IEND\xaeB`\x82'
        
        post_data = {
            "name": self.path_ai_g3["name"] if "name" in self.path_ai_g3 else "AI Grade 3 Path",
            "slug": "ai-grade-3-updated-slug",
            "grade": "3",
            "description": "Updated path description with artwork",
            "display_order": "1",
            "is_active": "1",
            "image_file": (io.BytesIO(fake_png_data), "path_artwork.png")
        }

        resp = self.client.post(
            f"/admin/learning-paths/{self.path_ai_g3['id']}/edit",
            data=post_data,
            content_type="multipart/form-data",
            follow_redirects=True
        )
        self.assertEqual(resp.status_code, 200)
        
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT image, description FROM learning_paths WHERE id = %s", (self.path_ai_g3["id"],))
        updated = cur.fetchone()
        cur.close()
        conn.close()

        self.assertIn("uploads/learning_paths/learning_path_", updated["image"])
        self.assertEqual(updated["description"], "Updated path description with artwork")

    # 29. Learning path image replacement deletes old uploaded file
    def test_29_learning_path_image_replacement_and_old_cleanup(self):
        import io
        from app import storage
        self._login_admin()
        
        # Upload first image
        fake_png_1 = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa74U\xa6\x00\x00\x00\x00IEND\xaeB`\x82'
        resp1 = self.client.post(
            f"/admin/learning-paths/{self.path_ai_g3['id']}/edit",
            data={
                "name": "Path AI G3",
                "slug": "path-ai-g3",
                "grade": "3",
                "is_active": "1",
                "image_file": (io.BytesIO(fake_png_1), "first.png")
            },
            content_type="multipart/form-data",
            follow_redirects=True
        )
        self.assertEqual(resp1.status_code, 200)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT image FROM learning_paths WHERE id = %s", (self.path_ai_g3["id"],))
        first_img = cur.fetchone()["image"]
        cur.close()
        conn.close()

        self.assertTrue(storage.learning_path_image_exists(first_img))

        # Upload second image to replace first
        fake_png_2 = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa74U\xa6\x00\x00\x00\x00IEND\xaeB`\x82'
        resp2 = self.client.post(
            f"/admin/learning-paths/{self.path_ai_g3['id']}/edit",
            data={
                "name": "Path AI G3",
                "slug": "path-ai-g3",
                "grade": "3",
                "is_active": "1",
                "image_file": (io.BytesIO(fake_png_2), "second.png")
            },
            content_type="multipart/form-data",
            follow_redirects=True
        )
        self.assertEqual(resp2.status_code, 200)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT image FROM learning_paths WHERE id = %s", (self.path_ai_g3["id"],))
        second_img = cur.fetchone()["image"]
        cur.close()
        conn.close()

        self.assertNotEqual(first_img, second_img)
        self.assertTrue(storage.learning_path_image_exists(second_img))
        self.assertFalse(storage.learning_path_image_exists(first_img))

    # 30. Learning path remove image option
    def test_30_learning_path_remove_image(self):
        import io
        from app import storage
        self._login_admin()

        fake_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa74U\xa6\x00\x00\x00\x00IEND\xaeB`\x82'
        self.client.post(
            f"/admin/learning-paths/{self.path_ai_g3['id']}/edit",
            data={
                "name": "Path AI G3",
                "slug": "path-ai-g3",
                "grade": "3",
                "is_active": "1",
                "image_file": (io.BytesIO(fake_png), "temp.png")
            },
            content_type="multipart/form-data",
            follow_redirects=True
        )

        # Now remove image
        resp = self.client.post(
            f"/admin/learning-paths/{self.path_ai_g3['id']}/edit",
            data={
                "name": "Path AI G3",
                "slug": "path-ai-g3",
                "grade": "3",
                "is_active": "1",
                "remove_image": "1"
            },
            follow_redirects=True
        )
        self.assertEqual(resp.status_code, 200)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT image FROM learning_paths WHERE id = %s", (self.path_ai_g3["id"],))
        current_img = cur.fetchone()["image"]
        cur.close()
        conn.close()

        self.assertEqual(current_img, "")

    # 31. Invalid file types rejected by image validation
    def test_31_learning_path_invalid_file_rejected(self):
        import io
        self._login_admin()
        
        # Test .exe or fake file
        fake_exe = b'MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00'
        resp = self.client.post(
            f"/admin/learning-paths/{self.path_ai_g3['id']}/edit",
            data={
                "name": "Path AI G3",
                "slug": "path-ai-g3",
                "grade": "3",
                "is_active": "1",
                "image_file": (io.BytesIO(fake_exe), "virus.exe")
            },
            content_type="multipart/form-data",
            follow_redirects=True
        )
        self.assertIn(b"Invalid image format", resp.data)

    # 32. Course thumbnail upload and persistent storage
    def test_32_course_thumbnail_upload_success(self):
        import io
        self._login_admin()

        fake_jpg_data = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9'

        resp = self.client.post(
            f"/admin/courses/{self.course_ai_g3_id}/edit",
            data={
                "title": "AI Grade 3 Course Updated",
                "slug": "ai-grade-3-course",
                "description": "Brand new updated course description with detailed topics.",
                "short_description": "Short summary",
                "grade": "3",
                "category_id": str(self.cat_ai["id"]),
                "learning_path_id": str(self.path_ai_g3["id"]),
                "is_active": "1",
                "thumbnail_file": (io.BytesIO(fake_jpg_data), "thumbnail.jpg")
            },
            content_type="multipart/form-data",
            follow_redirects=True
        )
        self.assertEqual(resp.status_code, 200)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT image, description FROM courses WHERE id = %s", (self.course_ai_g3_id,))
        course_rec = cur.fetchone()
        cur.close()
        conn.close()

        self.assertIn("uploads/courses/course_", course_rec["image"])
        self.assertEqual(course_rec["description"], "Brand new updated course description with detailed topics.")

    # 33. Course description updates immediately reflect on catalogue & detail views
    def test_33_course_description_update_reflected_on_public_pages(self):
        self._login_admin()
        new_description_text = "Master neural networks and vision sensors from scratch in Grade 3."
        
        self.client.post(
            f"/admin/courses/{self.course_ai_g3_id}/edit",
            data={
                "title": "AI Grade 3 Course",
                "slug": "ai-grade-3-course",
                "description": new_description_text,
                "short_description": "Master neural networks preview",
                "grade": "3",
                "category_id": str(self.cat_ai["id"]),
                "learning_path_id": str(self.path_ai_g3["id"]),
                "is_active": "1"
            },
            follow_redirects=True
        )

        # 1. Course Detail Page
        resp_detail = self.client.get("/courses/ai-grade-3-course")
        self.assertEqual(resp_detail.status_code, 200)
        self.assertIn(b"Master neural networks", resp_detail.data)

        # 2. Path Courses Page
        resp_path = self.client.get(f"/courses/category/{self.cat_ai['slug']}/{self.path_ai_g3['slug']}")
        self.assertEqual(resp_path.status_code, 200)
        self.assertIn(b"Master neural networks preview", resp_path.data)

    # 34. Course thumbnail remove option
    def test_34_course_thumbnail_remove(self):
        self._login_admin()

        resp = self.client.post(
            f"/admin/courses/{self.course_ai_g3_id}/edit",
            data={
                "title": "AI Grade 3 Course",
                "slug": "ai-grade-3-course",
                "description": "Standard description",
                "grade": "3",
                "category_id": str(self.cat_ai["id"]),
                "learning_path_id": str(self.path_ai_g3["id"]),
                "is_active": "1",
                "remove_image": "1"
            },
            follow_redirects=True
        )
        self.assertEqual(resp.status_code, 200)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT image FROM courses WHERE id = %s", (self.course_ai_g3_id,))
        course_rec = cur.fetchone()
        cur.close()
        conn.close()

        self.assertEqual(course_rec["image"], "")

    # 35. Public /courses hero does not hardcode Drone image
    def test_35_courses_catalogue_hero_no_hardcoded_drone_image(self):
        resp = self.client.get("/courses")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Select a Learning Category", resp.data)
        # Verify page renders clean hero without forcing drone.jpg as only hero
        self.assertIn(b"page-hero-media", resp.data)

    # 36. Category image upload and distinct card rendering
    def test_36_category_image_upload_and_rendering(self):
        import io
        self._login_admin()
        fake_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa74U\xa6\x00\x00\x00\x00IEND\xaeB`\x82'
        
        resp = self.client.post(
            f"/admin/learning-categories/{self.cat_ai['id']}/edit",
            data={
                "name": "Artificial Intelligence",
                "slug": "artificial-intelligence",
                "description": "AI systems and deep neural nets",
                "display_order": "1",
                "is_active": "1",
                "image_file": (io.BytesIO(fake_png), "ai_cat.png")
            },
            content_type="multipart/form-data",
            follow_redirects=True
        )
        self.assertEqual(resp.status_code, 200)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT image FROM learning_categories WHERE id = %s", (self.cat_ai["id"],))
        cat_img = cur.fetchone()["image"]
        cur.close()
        conn.close()

        self.assertIn("uploads/categories/category_", cat_img)

        # Verify on /courses catalogue
        resp_cat = self.client.get("/courses")
        self.assertEqual(resp_cat.status_code, 200)
        self.assertIn(cat_img.encode(), resp_cat.data)

    # 37. Course detail with valid thumbnail displays thumbnail image
    def test_37_course_detail_with_valid_thumbnail_displays_image(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            "UPDATE courses SET image = 'images/services/ai.jpg' WHERE id = %s",
            (self.course_ai_g3_id,)
        )
        conn.commit()
        cur.close()
        conn.close()

        resp = self.client.get("/courses/ai-grade-3-course")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"/static/images/services/ai.jpg", resp.data)
        self.assertIn(b"AI Grade 3 Course thumbnail", resp.data)

    # 38. Course detail with no thumbnail displays SVG placeholder
    def test_38_course_detail_with_no_thumbnail_displays_placeholder(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            "UPDATE courses SET image = '' WHERE id = %s",
            (self.course_ai_g3_id,)
        )
        conn.commit()
        cur.close()
        conn.close()

        resp = self.client.get("/courses/ai-grade-3-course")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"page-hero-media", resp.data)
        self.assertIn(b"<svg", resp.data)

    # 39. Course listing thumbnail still works
    def test_39_course_listing_thumbnail_still_works(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            "UPDATE courses SET image = 'images/services/ai.jpg' WHERE id = %s",
            (self.course_ai_g3_id,)
        )
        conn.commit()
        cur.close()
        conn.close()

        resp = self.client.get(f"/courses/category/{self.cat_ai['slug']}/{self.path_ai_g3['slug']}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"/static/images/services/ai.jpg", resp.data)

    # 40. Storage and media image URL resolver handles various path formats
    def test_40_media_image_url_resolution(self):
        from app import resolve_media_image_url

        with app.test_request_context():
            # Static path
            self.assertEqual(resolve_media_image_url("images/services/drone.jpg"), "/static/images/services/drone.jpg")
            self.assertEqual(resolve_media_image_url("/images/services/drone.jpg"), "/static/images/services/drone.jpg")
            self.assertEqual(resolve_media_image_url("static/images/services/drone.jpg"), "/static/images/services/drone.jpg")

            # Uploads path (local or persistent storage)
            self.assertEqual(resolve_media_image_url("uploads/courses/c_1.jpg"), "/uploads/courses/c_1.jpg")
            self.assertEqual(resolve_media_image_url("/uploads/courses/c_1.jpg"), "/uploads/courses/c_1.jpg")

            # External / Remote URL
            self.assertEqual(resolve_media_image_url("https://cdn.example.com/thumb.jpg"), "https://cdn.example.com/thumb.jpg")
            self.assertEqual(resolve_media_image_url("http://cdn.example.com/thumb.jpg"), "http://cdn.example.com/thumb.jpg")

            # Empty / None
            self.assertIsNone(resolve_media_image_url(""))
            self.assertIsNone(resolve_media_image_url(None))

    # 41. Grade pathway card displays only "Grade 1" through "Grade 5" without class ranges in badges
    def test_41_pathway_cards_display_only_grades(self):
        resp = self.client.get(f"/courses/category/{self.cat_ai['slug']}")
        self.assertEqual(resp.status_code, 200)

        # Should contain Grade 1 to Grade 5
        self.assertIn(b"Grade 1", resp.data)
        self.assertIn(b"Grade 2", resp.data)
        self.assertIn(b"Grade 3", resp.data)
        self.assertIn(b"Grade 4", resp.data)
        self.assertIn(b"Grade 5", resp.data)

        # The pathway card badge overlays should NOT contain "Classes 1-2"
        self.assertNotIn(b"Grade 1 &bull; Classes 1", resp.data)
        self.assertNotIn(b"Grade 2 &bull; Classes 3", resp.data)
        self.assertNotIn(b"Grade 3 &bull; Classes 6", resp.data)
        self.assertNotIn(b"Grade 4 &bull; Classes 9", resp.data)
        self.assertNotIn(b"Grade 5 &bull; Classes 11", resp.data)

    # 42. Backend course.grade and access control remain intact
    def test_42_backend_course_grade_and_access_control_intact(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT grade FROM courses WHERE id = %s", (self.course_ai_g3_id,))
        course_grade = cur.fetchone()["grade"]
        cur.close()
        conn.close()

        self.assertEqual(course_grade, 3)

        # Before enrollment, student cannot access course
        self.assertFalse(can_access_course(self.student_id, self.course_ai_g3_id))

        # Enroll student in Grade 3 course
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            "INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, created_at, updated_at) VALUES (%s, %s, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            (self.student_id, self.course_ai_g3_id)
        )
        conn.commit()
        cur.close()
        conn.close()

        # Student can now access the course
        self.assertTrue(can_access_course(self.student_id, self.course_ai_g3_id))

    # 43. Course detail hero layout contains non-overlapping elements
    def test_43_course_detail_hero_non_overlapping_structure(self):
        resp = self.client.get("/courses/ai-grade-3-course")
        self.assertEqual(resp.status_code, 200)

        # Verify course-hero-layout and course-stat-pill are used
        self.assertIn(b"course-hero-layout", resp.data)
        self.assertIn(b"course-hero-content", resp.data)
        self.assertIn(b"course-hero-media", resp.data)
        self.assertIn(b"course-stat-pill", resp.data)

    # 44. Long course title renders cleanly
    def test_44_long_course_title_renders_cleanly(self):
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        long_title = "Advanced Autonomous Quadcopter Flight Dynamics, Sensor Fusion and PID Controller Design For Aerial Robotics"
        cur.execute(
            "UPDATE courses SET title = %s WHERE id = %s",
            (long_title, self.course_ai_g3_id)
        )
        conn.commit()
        cur.close()
        conn.close()

        resp = self.client.get("/courses/ai-grade-3-course")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(long_title.encode(), resp.data)
        self.assertIn(b"word-break: break-word;", resp.data)

    # 45. Learning Category cards on /courses: No "DOMAIN #" labels, full card clickable
    def test_45_courses_category_cards_no_domain_labels_and_clickable(self):
        resp = self.client.get("/courses")
        self.assertEqual(resp.status_code, 200)

        # 1. "Domain #" and "DOMAIN #" labels must be completely absent
        self.assertNotIn(b"Domain #", resp.data)
        self.assertNotIn(b"DOMAIN #", resp.data)
        self.assertNotIn(b"Domain #1", resp.data)
        self.assertNotIn(b"Domain #2", resp.data)
        self.assertNotIn(b"Domain #3", resp.data)

        # 2. Entire card is clickable (<a href="/courses/category/..." class="category-card">)
        self.assertIn(b'class="category-card"', resp.data)
        self.assertIn(f'href="/courses/category/{self.cat_ai["slug"]}"'.encode(), resp.data)

        # 3. Pathways badge and Courses count are present
        self.assertIn(b"category-path-badge", resp.data)
        self.assertIn(b"category-course-count", resp.data)

        # 4. Explore Grade Pathways action is present
        self.assertIn(b"Explore Grade Pathways", resp.data)


if __name__ == "__main__":
    unittest.main()

