import unittest
import os
import tempfile
from datetime import datetime
from werkzeug.security import generate_password_hash

from app import app, init_db, get_db_connection

class QuizSystemTestCase(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["DATABASE_URL"] = f"sqlite:///{self.db_path}"
        self.client = app.test_client()

        with app.app_context():
            init_db()

        # Seed test admin & test student users
        conn = get_db_connection()
        cur = conn.cursor()
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # Admin user
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, %s)",
            ("Admin Test", "admin_test@test.com", generate_password_hash("adminpass"), "admin", now_str)
        )
        # Student user 1
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, %s)",
            ("Student One", "student1@test.com", generate_password_hash("studentpass"), "user", now_str)
        )
        # Student user 2
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, %s)",
            ("Student Two", "student2@test.com", generate_password_hash("studentpass"), "user", now_str)
        )
        # Sample Course & Module
        cur.execute(
            "INSERT INTO courses (title, slug, description, level, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, 1, %s, %s)",
            ("Drone Tech Test", "drone-tech-test", "Test Course Description", "Beginner", now_str, now_str)
        )
        cur.execute("SELECT id FROM courses WHERE slug = 'drone-tech-test'")
        course_id = cur.fetchone()["id"]

        cur.execute(
            "INSERT INTO modules (course_id, title, description, sequence, is_active, created_at, updated_at) VALUES (%s, %s, %s, 1, 1, %s, %s)",
            (course_id, "Module 1 Intro", "Intro module description", now_str, now_str)
        )
        cur.execute("SELECT id FROM modules WHERE course_id = %s", (course_id,))
        self.module_id = cur.fetchone()["id"]
        self.course_id = course_id
        self.course_slug = "drone-tech-test"

        conn.commit()

        # Fetch IDs for users
        cur.execute("SELECT id FROM users WHERE email = 'admin_test@test.com'")
        self.admin_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM users WHERE email = 'student1@test.com'")
        self.student1_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM users WHERE email = 'student2@test.com'")
        self.student2_id = cur.fetchone()["id"]

        # Enroll test students in test course
        cur.execute(
            "INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, created_at, updated_at) VALUES (%s, %s, 1, %s, %s, %s)",
            (self.student1_id, self.course_id, now_str, now_str, now_str)
        )
        cur.execute(
            "INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, created_at, updated_at) VALUES (%s, %s, 1, %s, %s, %s)",
            (self.student2_id, self.course_id, now_str, now_str, now_str)
        )
        conn.commit()

        cur.close()
        conn.close()

    def tearDown(self):
        os.close(self.db_fd)
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
        except PermissionError:
            pass

    def login_admin(self):
        self.client.get("/logout")
        return self.client.post("/login", data={"email": "admin_test@test.com", "password": "adminpass"}, follow_redirects=True)

    def login_student1(self):
        self.client.get("/logout")
        return self.client.post("/login", data={"email": "student1@test.com", "password": "studentpass"}, follow_redirects=True)

    def login_student2(self):
        self.client.get("/logout")
        return self.client.post("/login", data={"email": "student2@test.com", "password": "studentpass"}, follow_redirects=True)

    def create_quiz_helper(self, title="Drone Basics Quiz", passing_score=70, max_attempts=5):
        conn = get_db_connection()
        cur = conn.cursor()
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "INSERT INTO modules (course_id, title, description, sequence, is_active, created_at, updated_at) VALUES (%s, %s, 'Desc', 1, 1, %s, %s)",
            (self.course_id, f"Module {datetime.utcnow().timestamp()}", now_str, now_str)
        )
        cur.execute("SELECT id FROM modules WHERE course_id = %s ORDER BY id DESC LIMIT 1", (self.course_id,))
        mod_id = cur.fetchone()["id"]

        cur.execute(
            "INSERT INTO quizzes (module_id, title, description, passing_score, max_attempts, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, 1, %s, %s)",
            (mod_id, title, "Test Desc", passing_score, max_attempts, now_str, now_str)
        )
        conn.commit()
        cur.execute("SELECT id FROM quizzes WHERE module_id = %s ORDER BY id DESC LIMIT 1", (mod_id,))
        quiz_id = cur.fetchone()["id"]
        cur.close()
        conn.close()
        return quiz_id, mod_id

    def create_question_helper(self, quiz_id, question_text="What does ESC stand for?", correct_option="B"):
        conn = get_db_connection()
        cur = conn.cursor()
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO quiz_questions (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option, explanation, sequence, is_active, created_at, updated_at)
            VALUES (%s, %s, 'Electric Speed Controller', 'Electronic Speed Controller', 'Engine System Controller', 'Emergency Stop Control', %s, 'ESC stands for Electronic Speed Controller.', 1, 1, %s, %s)
            """,
            (quiz_id, question_text, correct_option, now_str, now_str)
        )
        conn.commit()
        cur.execute("SELECT id FROM quiz_questions WHERE quiz_id = %s ORDER BY id DESC LIMIT 1", (quiz_id,))
        q_id = cur.fetchone()["id"]
        cur.close()
        conn.close()
        return q_id

    # 1. Admin creates quiz
    def test_01_admin_create_quiz(self):
        self.login_admin()
        res = self.client.post(
            f"/admin/modules/{self.module_id}/quiz/new",
            data={"title": "Drone Basics Quiz", "description": "Test quiz desc", "passing_score": 70, "max_attempts": 5, "is_active": "1"},
            follow_redirects=True
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Quiz created successfully", res.data)

    # 2. Admin edits quiz
    def test_02_admin_edit_quiz(self):
        quiz_id, mod_id = self.create_quiz_helper()
        self.login_admin()
        res = self.client.post(
            f"/admin/quizzes/{quiz_id}/edit",
            data={"title": "Updated Drone Quiz", "description": "Updated desc", "passing_score": 80, "max_attempts": 5, "is_active": "1"},
            follow_redirects=True
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Quiz updated successfully", res.data)

    # 3. Admin creates question
    def test_03_admin_create_question(self):
        quiz_id, mod_id = self.create_quiz_helper()
        self.login_admin()
        res = self.client.post(
            f"/admin/quizzes/{quiz_id}/questions/new",
            data={
                "question_text": "What does ESC stand for?",
                "option_a": "Electric Speed Controller",
                "option_b": "Electronic Speed Controller",
                "option_c": "Engine System Controller",
                "option_d": "Emergency Stop Control",
                "correct_option": "B",
                "explanation": "ESC stands for Electronic Speed Controller.",
                "sequence": 1,
                "is_active": "1"
            },
            follow_redirects=True
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Question added successfully", res.data)

    # 4. Admin edits question
    def test_04_admin_edit_question(self):
        quiz_id, mod_id = self.create_quiz_helper()
        q_id = self.create_question_helper(quiz_id)
        self.login_admin()
        res = self.client.post(
            f"/admin/questions/{q_id}/edit",
            data={
                "question_text": "What does ESC stand for in Drones?",
                "option_a": "Electric Speed Controller",
                "option_b": "Electronic Speed Controller",
                "option_c": "Engine System Controller",
                "option_d": "Emergency Stop Control",
                "correct_option": "B",
                "explanation": "ESC controls motor RPM.",
                "sequence": 1,
                "is_active": "1"
            },
            follow_redirects=True
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Question updated successfully", res.data)

    # 5. Security: Student cannot see correct_option before submission in source code!
    def test_05_security_no_correct_option_in_take_page(self):
        quiz_id, mod_id = self.create_quiz_helper()
        self.create_question_helper(quiz_id)
        self.login_student1()
        res = self.client.get(f"/courses/{self.course_slug}/module/{mod_id}/quiz/start")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(b'correct_option', res.data)
        self.assertNotIn(b'correct_answers', res.data)
        self.assertNotIn(b'ESC stands for Electronic Speed Controller', res.data)

    # 6. Student starts and submits quiz, server calculates score and returns explanation
    def test_06_student_submit_and_server_scoring(self):
        quiz_id, mod_id = self.create_quiz_helper()
        q_id = self.create_question_helper(quiz_id)
        self.login_student1()
        
        # Start quiz
        res_start = self.client.get(f"/courses/{self.course_slug}/module/{mod_id}/quiz/start")
        self.assertEqual(res_start.status_code, 200)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM quiz_attempts WHERE quiz_id = %s AND user_id = %s ORDER BY id DESC LIMIT 1", (quiz_id, self.student1_id))
        att_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        # Submit correct answer 'B'
        res_sub = self.client.post(
            f"/courses/{self.course_slug}/module/{mod_id}/quiz/submit",
            data={"attempt_id": att_id, f"question_{q_id}": "B"},
            follow_redirects=True
        )
        self.assertEqual(res_sub.status_code, 200)
        self.assertIn(b"100%", res_sub.data)
        self.assertIn(b"Assessment Passed", res_sub.data)
        self.assertIn(b"ESC stands for Electronic Speed Controller", res_sub.data)

    # 7. Server-side 5 attempt limit enforcement
    def test_07_attempt_limit_enforced(self):
        quiz_id, mod_id = self.create_quiz_helper(max_attempts=5)
        self.create_question_helper(quiz_id)
        self.login_student1()

        conn = get_db_connection()
        cur = conn.cursor()
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        # Insert 5 completed attempts
        for i in range(1, 6):
            cur.execute(
                "INSERT INTO quiz_attempts (quiz_id, user_id, score, total_questions, correct_answers, passed, attempt_number, started_at, submitted_at) VALUES (%s, %s, 50, 1, 0, 0, %s, %s, %s)",
                (quiz_id, self.student1_id, i, now_str, now_str)
            )
        conn.commit()
        cur.close()
        conn.close()

        # Attempt 6 MUST be rejected server-side!
        res = self.client.get(f"/courses/{self.course_slug}/module/{mod_id}/quiz/start", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Maximum attempts limit reached", res.data)

    # 8. Tab-switch / window-blur invalidates active attempt
    def test_08_tab_switch_invalidation(self):
        quiz_id, mod_id = self.create_quiz_helper()
        self.create_question_helper(quiz_id)
        self.login_student1()
        
        # Start quiz
        self.client.get(f"/courses/{self.course_slug}/module/{mod_id}/quiz/start")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM quiz_attempts WHERE quiz_id = %s AND user_id = %s AND submitted_at IS NULL ORDER BY id DESC LIMIT 1", (quiz_id, self.student1_id))
        att_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        # Call invalidate endpoint
        res = self.client.post(
            f"/courses/{self.course_slug}/module/{mod_id}/quiz/invalidate",
            data={"attempt_id": att_id}
        )
        self.assertEqual(res.status_code, 200)
        json_data = res.get_json()
        self.assertEqual(json_data["status"], "invalidated")

        # Verify attempt is marked invalidated in database
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT is_invalidated, passed FROM quiz_attempts WHERE id = %s", (att_id,))
        attempt_row = cur.fetchone()
        cur.close()
        conn.close()

        self.assertTrue(bool(attempt_row["is_invalidated"]))
        self.assertFalse(bool(attempt_row["passed"]))

    # 9. Student isolation: Student 2 cannot submit Student 1's attempt
    def test_09_user_isolation(self):
        quiz_id, mod_id = self.create_quiz_helper()
        q_id = self.create_question_helper(quiz_id)
        self.login_student1()
        res_start = self.client.get(f"/courses/{self.course_slug}/module/{mod_id}/quiz/start")
        self.assertEqual(res_start.status_code, 200)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, started_at, submitted_at FROM quiz_attempts WHERE quiz_id = %s AND user_id = %s ORDER BY id DESC LIMIT 1", (quiz_id, self.student1_id))
        att_before = cur.fetchone()
        att1_id = att_before["id"]
        cur.close()
        conn.close()

        self.assertIsNone(att_before["submitted_at"], f"Attempt should be unsubmitted upon start, got {att_before}")

        # Login as Student 2 and attempt to submit Student 1's attempt
        self.login_student2()
        res = self.client.post(
            f"/courses/{self.course_slug}/module/{mod_id}/quiz/submit",
            data={"attempt_id": att1_id, f"question_{q_id}": "B"},
            follow_redirects=True
        )
        self.assertEqual(res.status_code, 200)

        # Confirm user isolation: Student 1's attempt was NOT submitted by Student 2
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, submitted_at FROM quiz_attempts WHERE id = %s", (att1_id,))
        attempt_after = cur.fetchone()
        cur.close()
        conn.close()
        self.assertIsNone(attempt_after["submitted_at"], f"Attempt after Student 2 submit was modified: {attempt_after}")

    # 10. Admin deletes question & quiz
    def test_10_admin_delete_question_and_quiz(self):
        quiz_id, mod_id = self.create_quiz_helper()
        q_id = self.create_question_helper(quiz_id)
        self.login_admin()

        res_q = self.client.post(f"/admin/questions/{q_id}/delete", follow_redirects=True)
        self.assertEqual(res_q.status_code, 200)
        self.assertIn(b"Question deleted successfully", res_q.data)

        res_qz = self.client.post(f"/admin/quizzes/{quiz_id}/delete", follow_redirects=True)
        self.assertEqual(res_qz.status_code, 200)
        self.assertIn(b"Quiz deleted successfully", res_qz.data)

    # 11. Regression test: Verify /bootcamp still returns 404
    def test_11_bootcamp_returns_404(self):
        res = self.client.get("/bootcamp")
        self.assertEqual(res.status_code, 404)

if __name__ == "__main__":
    unittest.main()
