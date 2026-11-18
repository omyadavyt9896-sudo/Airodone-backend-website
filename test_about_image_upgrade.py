# -*- coding: utf-8 -*-
"""
Automated Test Suite for Lab Detail About Section Enhancement
Verifies:
1. Complete removal of the 3 old info cards (Target Audience, Learning Framework, Implementation Type).
2. Large laboratory image replacement with proper layout, styling, and aspect ratio.
3. Admin Image Management (Upload, Preview, Replace, Remove, Alt text).
4. Dynamic image per laboratory (ATL, Robotics, AI, STEM, Drone).
5. Dynamic fallback architecture when no image is uploaded.
6. Professional animations & scroll triggers.
7. Mobile responsiveness (vertical stack with image below intro text).
8. Preserved sections (Why Establish, Navbar, Footer, Inquiry form).
"""
import sys
import io
import re
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as flask_app


class LabAboutSectionUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        flask_app.app.config["TESTING"] = True
        flask_app.app.config["WTF_CSRF_ENABLED"] = False
        cls.client = flask_app.app.test_client()

        # Ensure database is initialized
        flask_app.init_db()

    def test_01_old_cards_completely_removed(self):
        """Test that the 3 old right-side cards are completely removed from the HTML (not merely CSS-hidden)."""
        slugs = ["atl-lab", "robotics-lab", "artificial-intelligence-lab", "drone-technology-lab", "stem-innovation-lab"]
        for slug in slugs:
            resp = self.client.get(f"/lab-setup/{slug}")
            self.assertEqual(resp.status_code, 200, f"Failed to load /lab-setup/{slug}")
            html = resp.data.decode("utf-8")

            # Must NOT contain the 3 old card headings or card markup in the about section
            self.assertNotIn("lab-info-card", html, f"Old lab-info-card found in /lab-setup/{slug}")
            self.assertNotIn("lab-about-cards-col", html, f"Old lab-about-cards-col found in /lab-setup/{slug}")
            self.assertNotIn("TARGET AUDIENCE", html, f"TARGET AUDIENCE card header found in /lab-setup/{slug}")
            self.assertNotIn("LEARNING FRAMEWORK", html, f"LEARNING FRAMEWORK card header found in /lab-setup/{slug}")
            self.assertNotIn("IMPLEMENTATION TYPE", html, f"IMPLEMENTATION TYPE card header found in /lab-setup/{slug}")

    def test_02_new_about_section_structure_and_image(self):
        """Test that the new About section has Left text/callout and Right large image (~45%)."""
        resp = self.client.get("/lab-setup/atl-lab")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # Section presence
        self.assertIn('class="lab-detail-section lab-about-section" id="about"', html)
        self.assertIn('class="lab-about-grid"', html)
        self.assertIn('class="lab-about-col-left"', html)
        self.assertIn('class="lab-about-heading"', html)
        self.assertIn("About the ATL Lab", html)

        # Why Establish callout box preserved
        self.assertIn("Why Establish This Innovation Lab?", html)
        self.assertIn("Active Experiential Learning:", html)
        self.assertIn("Interdisciplinary Integration:", html)
        self.assertIn("Future Skills Mastery:", html)

        # Right column image
        self.assertIn('class="lab-about-col-right', html)
        self.assertIn('class="lab-about-image-wrapper"', html)
        self.assertIn('class="lab-about-img"', html)
        self.assertIn('class="lab-about-image-badge"', html)

    def test_03_dynamic_images_per_laboratory(self):
        """Test that each laboratory displays its own distinct image."""
        labs = {
            "atl-lab": "atl.jpg",
            "robotics-lab": "robotics.jpg",
            "artificial-intelligence-lab": "ai.jpg",
            "stem-innovation-lab": "electronics.jpg",
            "drone-technology-lab": "drone.jpg",
        }
        for slug, expected_file in labs.items():
            resp = self.client.get(f"/lab-setup/{slug}")
            self.assertEqual(resp.status_code, 200)
            html = resp.data.decode("utf-8")

            # Find about image src
            match = re.search(r'class="lab-about-img"[^>]*src="([^"]+)"', html)
            if not match:
                match = re.search(r'src="([^"]+)"[^>]*class="lab-about-img"', html)
            self.assertIsNotNone(match, f"About image not found for {slug}")
            src = match.group(1)
            self.assertIn(expected_file, src, f"Lab {slug} should have image {expected_file}, got {src}")

    def test_04_fallback_image_when_database_image_is_null(self):
        """Test that when a lab has about_image=None, dynamic domain fallback is used."""
        conn = flask_app.get_db_connection()
        cur = flask_app.get_db_cursor(conn)
        cur.execute("UPDATE laboratories SET about_image = NULL, about_image_alt = NULL WHERE slug = 'drone-technology-lab'")
        conn.commit()
        cur.close()
        conn.close()

        resp = self.client.get("/lab-setup/drone-technology-lab")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # Fallback for drone lab should resolve to drone.jpg
        self.assertIn("drone.jpg", html)
        self.assertIn('class="lab-about-img"', html)

        # Restore
        conn = flask_app.get_db_connection()
        cur = flask_app.get_db_cursor(conn)
        cur.execute("UPDATE laboratories SET about_image = 'images/services/drone.jpg', about_image_alt = 'Students assembling drone' WHERE slug = 'drone-technology-lab'")
        conn.commit()
        cur.close()
        conn.close()

    def _login_admin(self):
        """Helper to create and login test admin."""
        conn = flask_app.get_db_connection()
        cur = flask_app.get_db_cursor(conn)
        from werkzeug.security import generate_password_hash
        pw_hash = generate_password_hash("password123")
        cur.execute("SELECT id FROM users WHERE email = 'test_admin@airodrone.in'")
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, %s)",
                ("Test Admin", "test_admin@airodrone.in", pw_hash, "admin", "2026-11-17 12:00:00")
            )
            conn.commit()
        cur.close()
        conn.close()

        self.client.post("/login", data={"email": "test_admin@airodrone.in", "password": "password123"}, follow_redirects=True)

    def test_05_admin_form_has_dedicated_about_image_fields(self):
        """Test that admin lab edit form contains the dedicated About Section Image management interface."""
        self._login_admin()

        resp = self.client.get("/admin/labs/1/edit")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        self.assertIn("About Section Image", html)
        self.assertIn('name="about_image_file"', html)
        self.assertIn('name="about_image_preset"', html)
        self.assertIn('id="aboutImagePreview"', html)
        self.assertIn('name="about_image_alt"', html)
        self.assertIn('name="remove_about_image"', html)

    def test_06_admin_can_upload_and_replace_about_image(self):
        """Test admin uploading a new image file, verifying it is saved and shown on public page."""
        self._login_admin()

        # 1x1 GIF mock image
        mock_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
            b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        post_data = {
            "name": "ATL Lab (Atal Tinkering Lab)",
            "slug": "atl-lab",
            "badge": "ATL Lab",
            "short_description": "A government-backed innovation space.",
            "description": "Comprehensive turnkey setup for ATL.",
            "about_image_alt": "Custom Admin Uploaded ATL Laboratory Image",
            "cta_text": "Book a Lab Setup",
            "display_order": 1,
            "is_active": "1",
            "about_image_file": (io.BytesIO(mock_png), "atl_admin_test.png"),
        }

        resp = self.client.post(
            "/admin/labs/1/edit",
            data=post_data,
            content_type="multipart/form-data",
            follow_redirects=True
        )
        self.assertEqual(resp.status_code, 200)

        # Check in DB
        conn = flask_app.get_db_connection()
        cur = flask_app.get_db_cursor(conn)
        cur.execute("SELECT about_image, about_image_alt FROM laboratories WHERE id = 1")
        row = cur.fetchone()
        cur.close()
        conn.close()

        self.assertIsNotNone(row["about_image"])
        self.assertIn("uploads/labs", row["about_image"])
        self.assertEqual(row["about_image_alt"], "Custom Admin Uploaded ATL Laboratory Image")

        # Verify on public page
        pub_resp = self.client.get("/lab-setup/atl-lab")
        self.assertEqual(pub_resp.status_code, 200)
        pub_html = pub_resp.data.decode("utf-8")
        self.assertIn(row["about_image"], pub_html)
        self.assertIn("Custom Admin Uploaded ATL Laboratory Image", pub_html)

        # Now test replace/preset restore
        restore_data = {
            "name": "ATL Lab (Atal Tinkering Lab)",
            "slug": "atl-lab",
            "badge": "ATL Lab",
            "short_description": "A government-backed innovation space.",
            "description": "Comprehensive turnkey setup for ATL.",
            "about_image_preset": "images/services/atl.jpg",
            "about_image_alt": "Students working in modern ATL laboratory",
            "cta_text": "Book a Lab Setup",
            "display_order": 1,
            "is_active": "1",
        }
        resp = self.client.post(
            "/admin/labs/1/edit",
            data=restore_data,
            follow_redirects=True
        )
        self.assertEqual(resp.status_code, 200)

        # Verify restored
        pub_resp = self.client.get("/lab-setup/atl-lab")
        pub_html = pub_resp.data.decode("utf-8")
        self.assertIn("images/services/atl.jpg", pub_html)

    def test_07_animations_and_responsive_css(self):
        """Test that style.css contains all required animation and responsive rules."""
        css_path = os.path.join(flask_app.app.root_path, "static", "css", "style.css")
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()

        # Check animation classes
        self.assertIn(".lab-about-col-right.pre-animate", css)
        self.assertIn("translateX(36px)", css, "Image slide-in from right missing")
        self.assertIn(".lab-about-intro.pre-animate", css)
        self.assertIn("translateY(18px)", css, "Heading/text fade-in movement missing")
        self.assertIn(".lab-about-callout.pre-animate", css)
        self.assertIn("scale(1.035)", css, "Subtle hover zoom on image missing")

        # Responsive: Mobile stack & image below introductory text
        self.assertIn("@media (max-width: 860px)", css)
        self.assertIn("order: 1", css)
        self.assertIn("order: 2", css)
        self.assertIn("order: 3", css)
        self.assertIn("max-height: 360px", css, "Mobile image height constraint missing")

        # Prefers-reduced-motion
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn(".lab-about-img", css)


if __name__ == "__main__":
    unittest.main()
