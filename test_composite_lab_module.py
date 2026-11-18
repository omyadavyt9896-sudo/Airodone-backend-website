"""
Test Suite for Composite Skill Lab (CBSE) Module.
Tests public landing page, contact enquiry workflow, duplicate protection,
admin authentication, enquiries management, packages CRUD, sectors CRUD,
FAQs CRUD, Content CMS, and dedicated dashboard metrics.
"""

import os
import sys
import unittest

# Ensure working directory is set to app root
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, get_db_connection, get_db_cursor
from flask_login import login_user

class CompositeLabModuleTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        cls.client = app.test_client()

        # Find or create an admin user and a regular student user for testing
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        
        # Admin user
        cur.execute("SELECT id, email, password_hash, role FROM users WHERE role = 'admin' LIMIT 1")
        admin_row = cur.fetchone()
        if not admin_row:
            cur.execute(
                "INSERT INTO users (name, email, password_hash, role, is_active) VALUES ('Test Admin', 'test_csl_admin@airodrone.in', 'hash', 'admin', 1)"
            )
            conn.commit()
            cur.execute("SELECT id, email, password_hash, role FROM users WHERE email = 'test_csl_admin@airodrone.in'")
            admin_row = cur.fetchone()
        cls.admin_user_id = admin_row["id"]
        cls.admin_email = admin_row["email"]

        # Student user
        cur.execute("SELECT id, email, password_hash, role FROM users WHERE role IN ('user', 'student') LIMIT 1")
        student_row = cur.fetchone()
        if not student_row:
            cur.execute(
                "INSERT INTO users (name, email, password_hash, role, is_active) VALUES ('Test Student', 'test_csl_student@airodrone.in', 'hash', 'student', 1)"
            )
            conn.commit()
            cur.execute("SELECT id, email, password_hash, role FROM users WHERE email = 'test_csl_student@airodrone.in'")
            student_row = cur.fetchone()
        cls.student_user_id = student_row["id"]
        cls.student_email = student_row["email"]

        cur.close()
        conn.close()

    def login_as_admin(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_user_id)
            sess["_fresh"] = True

    def login_as_student(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.student_user_id)
            sess["_fresh"] = True

    def logout(self):
        with self.client.session_transaction() as sess:
            sess.clear()

    # 1. Test Public Landing Page
    def test_01_public_landing_page(self):
        res = self.client.get("/composite-lab")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        
        # Verify 10 essential sections
        self.assertIn("Composite Skill Lab", html)
        self.assertIn("What is a Composite Skill Lab?", html)
        self.assertIn("Laboratory Specifications", html)
        self.assertIn("Six Core CBSE Skill Sectors", html)
        self.assertIn("Equipment &amp; Infrastructure Overview", html)
        self.assertIn("Complete Turnkey Setup Inclusions", html)
        self.assertIn("Composite Skill Lab Packages", html)
        self.assertIn("14-Day Implementation Roadmap", html)
        self.assertIn("Frequently Asked Questions", html)
        self.assertIn("Ready to Set Up a CBSE Composite Skill Lab", html)

        # Verify Packages loaded
        self.assertIn("Foundation Lab", html)
        self.assertIn("Makerspace Lab", html)
        self.assertIn("Future Lab", html)

        # Verify Nav bar has link
        self.assertIn("/composite-lab", html)

    # 2. Test Dedicated Contact Page GET & Preselected Package
    def test_02_public_contact_page_get(self):
        res = self.client.get("/composite-lab/contact?package=Makerspace%20Lab")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Request Composite Skill Lab Proposal", html)
        self.assertIn("School / Institution Name", html)
        self.assertIn("Makerspace Lab", html)
        self.assertIn("selected", html)

    # 3. Test Contact Form Validation & Submission
    def test_03_contact_form_submission_and_duplicate_protection(self):
        import time
        # Missing required fields
        res = self.client.post("/composite-lab/contact", data={
            "school_name": "",
            "institution_type": "",
        }, follow_redirects=True)
        html = res.data.decode("utf-8")
        self.assertIn("Institution / School Name is required", html)

        # Fresh unique submission
        ts = int(time.time())
        unique_phone = f"98{ts % 100000000:08d}"
        unique_email = f"principal_{ts}@greenwoodschool.edu.in"
        school_name = f"Greenwood School {ts}"
        res = self.client.post("/composite-lab/contact", data={
            "school_name": school_name,
            "institution_type": "CBSE Private School",
            "cbse_affiliation_no": "2130888",
            "contact_person": "Dr. Ananya Roy",
            "designation": "Principal / Head of School",
            "email": unique_email,
            "phone": unique_phone,
            "state": "Karnataka",
            "city": "Bengaluru",
            "address": "Sarjapur Road, Campus 2",
            "student_strength": "1,200 - 2,500 Students",
            "existing_lab": "Science Labs Only",
            "preferred_package": "Makerspace Lab",
            "room_size": "600 Sq. Ft. (Recommended)",
            "budget": "₹2.5 Lakh - ₹4.5 Lakh",
            "required_sectors": ["Electronics & Mechatronics", "Coding & Robotics", "IT/ITeS & AI"],
            "additional_requirements": "Looking to inaugurate before upcoming academic semester inspection.",
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("received successfully", html)

        # Verify database record
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT * FROM composite_lab_enquiries WHERE email = %s", (unique_email,))
        enquiry = cur.fetchone()
        self.assertIsNotNone(enquiry)
        self.assertEqual(enquiry["school_name"], school_name)
        self.assertEqual(enquiry["status"], "New")
        enquiry_id = enquiry["id"]

        # Verify activity log
        cur.execute("SELECT * FROM composite_lab_activity_logs WHERE enquiry_id = %s", (enquiry_id,))
        log = cur.fetchone()
        self.assertIsNotNone(log)
        self.assertIn("New enquiry received", log["action"])

        cur.close()
        conn.close()

        # Test duplicate submission protection within 5 mins
        res_dup = self.client.post("/composite-lab/contact", data={
            "school_name": school_name,
            "institution_type": "CBSE Private School",
            "cbse_affiliation_no": "2130888",
            "contact_person": "Dr. Ananya Roy",
            "designation": "Principal / Head of School",
            "email": unique_email,
            "phone": unique_phone,
            "state": "Karnataka",
            "city": "Bengaluru",
            "address": "Sarjapur Road, Campus 2",
            "preferred_package": "Makerspace Lab",
            "room_size": "600 Sq. Ft. (Recommended)",
            "required_sectors": ["Electronics & Mechatronics"],
        }, follow_redirects=True)
        html_dup = res_dup.data.decode("utf-8")
        self.assertIn("recently submitted", html_dup)

    # 4. Test Admin Access Control
    def test_04_admin_access_control(self):
        self.logout()
        # Anonymous user redirected to login
        res = self.client.get("/admin/composite-lab")
        self.assertEqual(res.status_code, 302)
        self.assertIn("login", res.headers.get("Location", ""))

        # Regular student user forbidden (403)
        self.login_as_student()
        res = self.client.get("/admin/composite-lab")
        self.assertEqual(res.status_code, 403)

        # Admin user allowed (200)
        self.login_as_admin()
        res = self.client.get("/admin/composite-lab")
        self.assertEqual(res.status_code, 200)

    # 5. Test Admin Enquiry Management & Notes
    def test_05_admin_enquiry_details_and_notes(self):
        self.login_as_admin()
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id FROM composite_lab_enquiries ORDER BY id DESC LIMIT 1")
        enquiry_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        # View details
        res = self.client.get(f"/admin/composite-lab/enquiry/{enquiry_id}")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Greenwood School", html)

        # Post an internal note
        res_note = self.client.post(f"/admin/composite-lab/enquiry/{enquiry_id}/notes", data={
            "note": "Called Dr. Ananya Roy. They requested an itemized proposal for 600 sq ft by Friday.",
        }, follow_redirects=True)
        self.assertEqual(res_note.status_code, 200)
        html_note = res_note.data.decode("utf-8")
        self.assertIn("Internal note saved", html_note)

        # Update status to 'Contacted'
        res_st = self.client.post(f"/admin/composite-lab/enquiry/{enquiry_id}/status", data={
            "status": "Contacted",
        }, follow_redirects=True)
        self.assertEqual(res_st.status_code, 200)
        html_st = res_st.data.decode("utf-8")
        self.assertIn("Status updated to &#39;Contacted&#39;", html_st)

        # Assign team member
        res_assign = self.client.post(f"/admin/composite-lab/enquiry/{enquiry_id}/assign", data={
            "assigned_to": self.admin_user_id,
        }, follow_redirects=True)
        self.assertEqual(res_assign.status_code, 200)

    # 6. Test Admin Packages CRUD
    def test_06_admin_packages_crud(self):
        self.login_as_admin()

        # List packages
        res = self.client.get("/admin/composite-lab/packages")
        self.assertEqual(res.status_code, 200)

        # Create package
        res_add = self.client.post("/admin/composite-lab/packages/new", data={
            "name": "Robotics Explorer Lab",
            "slug": "robotics-explorer-lab",
            "price": "249999",
            "price_display": "₹2,49,999",
            "badge": "Special Edition",
            "target_grades": "Classes 6 - 8",
            "ideal_for": "Middle school robotics",
            "description": "Specialized starter pack for coding & robotics.",
            "features": "Arduino Kits\nObstacle avoidance rovers\nCurriculum manual",
            "display_order": "4",
            "is_active": "1",
        }, follow_redirects=True)
        self.assertEqual(res_add.status_code, 200)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id FROM composite_lab_packages WHERE slug = 'robotics-explorer-lab'")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        pkg_id = row["id"]

        # Edit package
        res_edit = self.client.post(f"/admin/composite-lab/packages/{pkg_id}/edit", data={
            "name": "Robotics Explorer Lab (Updated)",
            "slug": "robotics-explorer-lab",
            "price": "259999",
            "price_display": "₹2,59,999",
            "badge": "Updated",
            "target_grades": "Classes 6 - 9",
            "ideal_for": "Middle school robotics updated",
            "description": "Updated description",
            "features": "Arduino Kits\nObstacle avoidance rovers\nCurriculum manual\nExtended Warranty",
            "display_order": "4",
            "is_active": "1",
        }, follow_redirects=True)
        self.assertEqual(res_edit.status_code, 200)

        # Toggle active
        res_toggle = self.client.post(f"/admin/composite-lab/packages/{pkg_id}/toggle-active", follow_redirects=True)
        self.assertEqual(res_toggle.status_code, 200)

        # Delete package
        res_del = self.client.post(f"/admin/composite-lab/packages/{pkg_id}/delete", follow_redirects=True)
        self.assertEqual(res_del.status_code, 200)

        cur.execute("SELECT id FROM composite_lab_packages WHERE id = %s", (pkg_id,))
        self.assertIsNone(cur.fetchone())
        cur.close()
        conn.close()

    # 7. Test Admin Skill Sectors CRUD
    def test_07_admin_sectors_crud(self):
        self.login_as_admin()

        # List sectors
        res = self.client.get("/admin/composite-lab/sectors")
        self.assertEqual(res.status_code, 200)

        # Create sector
        res_add = self.client.post("/admin/composite-lab/sectors/new", data={
            "name": "Drone & Aviation Technology",
            "code": "drone_aviation",
            "icon": "bot",
            "cbse_subject_code": "Skill Sub Code 999",
            "description": "Aerodynamics and micro quadcopter fabrication.",
            "equipment_highlights": "Quadcopter build kits, simulators, flight controllers",
            "display_order": "7",
            "is_active": "1",
        }, follow_redirects=True)
        self.assertEqual(res_add.status_code, 200)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id FROM composite_lab_sectors WHERE code = 'drone_aviation'")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        sec_id = row["id"]

        # Delete sector
        res_del = self.client.post(f"/admin/composite-lab/sectors/{sec_id}/delete", follow_redirects=True)
        self.assertEqual(res_del.status_code, 200)

        cur.execute("SELECT id FROM composite_lab_sectors WHERE id = %s", (sec_id,))
        self.assertIsNone(cur.fetchone())
        cur.close()
        conn.close()

    # 8. Test Admin FAQs CRUD
    def test_08_admin_faqs_crud(self):
        self.login_as_admin()

        # List FAQs
        res = self.client.get("/admin/composite-lab/faqs")
        self.assertEqual(res.status_code, 200)

        # Create FAQ
        res_add = self.client.post("/admin/composite-lab/faqs/new", data={
            "question": "Can school teachers operate the lab without prior robotics background?",
            "answer": "Yes, our certified trainers provide 3-day on-campus faculty enablement with step-by-step experiment kits.",
            "category": "Curriculum & Training",
            "display_order": "10",
            "is_active": "1",
        }, follow_redirects=True)
        self.assertEqual(res_add.status_code, 200)

        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT id FROM composite_lab_faqs WHERE question LIKE '%prior robotics background%'")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        faq_id = row["id"]

        # Delete FAQ
        res_del = self.client.post(f"/admin/composite-lab/faqs/{faq_id}/delete", follow_redirects=True)
        self.assertEqual(res_del.status_code, 200)

        cur.execute("SELECT id FROM composite_lab_faqs WHERE id = %s", (faq_id,))
        self.assertIsNone(cur.fetchone())
        cur.close()
        conn.close()

    # 9. Test Dedicated Dashboard
    def test_09_dedicated_dashboard(self):
        self.login_as_admin()
        res = self.client.get("/dashboard/composite-lab")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Composite Skill Lab Analytics", html)
        self.assertIn("Total Enquiries", html)
        self.assertIn("Pipeline Stage Funnel", html)
        self.assertIn("Preferred Package Distribution", html)
        self.assertIn("Geographic Reach by State", html)
        self.assertIn("Skill Sector Demand", html)

    # 10. Test Admin Home Card Link
    def test_10_admin_home_composite_card(self):
        self.login_as_admin()
        res = self.client.get("/admin")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Composite Skill Lab (CBSE)", html)
        self.assertIn("/admin/composite-lab", html)
        self.assertIn("/dashboard/composite-lab", html)

if __name__ == "__main__":
    unittest.main()
