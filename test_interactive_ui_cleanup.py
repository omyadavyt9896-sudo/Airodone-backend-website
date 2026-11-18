# -*- coding: utf-8 -*-
"""
Test Suite for AIRODRONE - Premium Interactive UI, Hover Effects & Lab Page Cleanup
Verifies:
1. Complete removal of "MORE INNOVATION PACKAGES" / "Explore Other Laboratories" section from individual lab pages.
2. Footer follows naturally after the main laboratory content.
3. Main /lab-setup catalogue still works and displays all labs.
4. Interactive information boxes (Why Establish, Equipment, Learning Areas, Benefits, Implementation Support) exist with rich content.
5. Reusable CSS hover animations, transforms, color palette, dark mode overrides, micro-interactions, mobile touch safety, and prefers-reduced-motion.
"""
import os
import sys
import unittest

backend_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(backend_dir)
sys.path.insert(0, backend_dir)
import app as flask_app


class LabInteractiveUICleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        flask_app.app.config["TESTING"] = True
        flask_app.app.config["WTF_CSRF_ENABLED"] = False
        cls.client = flask_app.app.test_client()
        flask_app.init_db()

    def test_01_bottom_explore_section_completely_removed(self):
        """Verify Explore Other Laboratories section is completely removed from all individual lab detail pages."""
        slugs = ["robotics-lab", "atl-lab", "artificial-intelligence-lab", "stem-innovation-lab", "drone-technology-lab"]
        for slug in slugs:
            resp = self.client.get(f"/lab-setup/{slug}")
            self.assertEqual(resp.status_code, 200)
            html = resp.data.decode("utf-8")

            # Must NOT contain section 8 headings, texts or buttons
            self.assertNotIn("MORE INNOVATION PACKAGES", html, f"Found 'MORE INNOVATION PACKAGES' in /lab-setup/{slug}")
            self.assertNotIn("Explore Other Laboratories", html, f"Found 'Explore Other Laboratories' in /lab-setup/{slug}")
            self.assertNotIn("Explore Details", html, f"Found 'Explore Details' in /lab-setup/{slug}")
            self.assertNotIn("lab-related-grid", html, f"Found 'lab-related-grid' in /lab-setup/{slug}")

            # Verify footer is present after the CTA section
            cta_pos = html.find("Ready to Set Up Your")
            footer_pos = html.find("<footer")
            self.assertTrue(cta_pos > 0, f"CTA section missing in /lab-setup/{slug}")
            self.assertTrue(footer_pos > cta_pos, f"Footer does not follow CTA in /lab-setup/{slug}")

    def test_02_main_lab_setup_catalogue_intact(self):
        """Verify the main /lab-setup catalogue page still loads all labs and navigation works."""
        resp = self.client.get("/lab-setup")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("Robotics Lab", html)
        self.assertIn("ATL Lab", html)
        self.assertIn("Artificial Intelligence Lab", html)
        self.assertIn("STEM Innovation Lab", html)
        self.assertIn("Drone Technology Lab", html)

    def test_03_card_elements_present_on_detail_page(self):
        """Verify all target card classes exist on individual lab pages."""
        resp = self.client.get("/lab-setup/robotics-lab")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        self.assertIn("lab-about-callout", html)
        self.assertIn("lab-equipment-card", html)
        self.assertIn("lab-learning-card", html)
        self.assertIn("lab-benefit-card", html)
        self.assertIn("lab-support-card", html)

    def test_04_css_hover_effects_and_micro_interactions(self):
        """Verify CSS contains premium hover rules, micro-interactions, dark mode overrides and accessibility."""
        css_path = os.path.join(backend_dir, "static", "css", "style.css")
        self.assertTrue(os.path.exists(css_path))
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()

        # Equipment card hover
        self.assertIn(".lab-equipment-card:hover", css)
        self.assertIn(".lab-equipment-card:hover .lab-equipment-image img", css)
        self.assertIn(".lab-equipment-card:hover .lab-equipment-name", css)

        # Learning card hover
        self.assertIn(".lab-learning-card:hover", css)
        self.assertIn(".lab-learning-card:hover .lab-learning-icon-wrap", css)
        self.assertIn(".lab-learning-card:hover .lab-learning-title", css)

        # Benefit card hover
        self.assertIn(".lab-benefit-card:hover", css)
        self.assertIn(".lab-benefit-card:hover .lab-benefit-icon-wrap", css)
        self.assertIn(".lab-benefit-card:hover .lab-benefit-title", css)

        # Support card hover
        self.assertIn(".lab-support-card:hover", css)
        self.assertIn(".lab-support-card:hover .lab-support-icon-wrap", css)

        # Why establish callout hover
        self.assertIn(".lab-about-callout:hover", css)
        self.assertIn(".lab-about-callout:hover h4 svg", css)

        # Dark mode hover overrides
        self.assertIn(".dark-mode .lab-equipment-card:hover", css)
        self.assertIn(".dark-mode .lab-about-callout:hover", css)

        # Mobile touch safety & prefers-reduced-motion
        self.assertIn("@media (hover: none) or (max-width: 768px)", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)

        # Micro-interactions and focus rings
        self.assertIn(".lab-hero-back-link:hover", css)
        self.assertIn(":focus-visible", css)


if __name__ == "__main__":
    unittest.main()
