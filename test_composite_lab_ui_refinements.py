"""
Verification Suite for Composite Skill Lab UI/UX Refinement & Interaction Improvements.
Tests:
1. Package card height reduction & expand/collapse mechanics
2. Elimination of excessive generic emojis in favour of lab photos & SVGs
3. Pricing button hover/click styling without black hover state
4. Enquiry page (/composite-lab/contact) 6-section redesign with hero media and benefits
5. Dark mode CSS styling coverage
"""

import os
import sys
import unittest
import re

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app

class CompositeLabUIRefinementsTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        cls.client = app.test_client()

    def test_01_package_card_height_reduction_and_toggle(self):
        """Test that package cards initially show 4 items with collapsible container and View More toggle."""
        res = self.client.get("/composite-lab")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Core elements present
        self.assertIn("csl-pricing-card", html)
        self.assertIn("csl-features-collapsible", html)
        self.assertIn("csl-toggle-details-btn", html)
        self.assertIn("View More Inclusions", html)
        self.assertIn("togglePackageCard", html)
        self.assertIn("csl-btn-enquire", html)

        # Check all 3 packages are rendered with enquiry links
        self.assertIn("Enquire for Foundation Lab", html)
        self.assertIn("Enquire for Makerspace Lab", html)
        self.assertIn("Enquire for Future Lab", html)

    def test_02_vector_icons_reduction_and_equipment_photos(self):
        """Test that generic emojis were replaced with real photographs and SVGs."""
        res = self.client.get("/composite-lab")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Equipment section has real laboratory images
        self.assertIn("csl-hardware-media", html)
        self.assertIn("images/services/electronics.jpg", html)
        self.assertIn("images/services/robotics.jpg", html)
        self.assertIn("images/services/workshop.jpg", html)
        self.assertIn("images/services/coding.jpg", html)

        # Confirm turnkey steps have SVG icons
        self.assertIn("csl-inc-icon-wrap", html)
        self.assertIn("csl-inc-step-num", html)

    def test_03_pricing_buttons_no_black_hover(self):
        """Test style.css to ensure button hover/click states are consistently blue with no black hover."""
        with open("static/css/style.css", "r", encoding="utf-8") as f:
            css = f.read()

        # Check csl-btn-enquire is properly styled with blue palette
        self.assertIn(".csl-btn-enquire", css)
        self.assertIn("#2563eb", css) # default blue
        self.assertIn("#1d4ed8", css) # hover blue
        self.assertIn("#1e40af", css) # active blue
        self.assertIn("scale(0.98)", css) # active scale
        self.assertIn("#38bdf8", css) # focus outline

        # Ensure .csl-btn-ghost:hover was neutralized from #0f172a
        self.assertNotIn(".csl-btn-ghost:hover {\n    background: #0f172a;", css)

    def test_04_contact_enquiry_page_redesign(self):
        """Test /composite-lab/contact has hero photo, 3 benefits, and 6 numbered form sections."""
        res = self.client.get("/composite-lab/contact")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Hero section
        self.assertIn("Request Composite Skill Lab Proposal", html)
        self.assertIn("csl-contact-hero-media", html)
        self.assertIn("images/services/atl.jpg", html)
        self.assertIn("14-Day Rapid Turnkey Setup", html)
        self.assertIn("100% CBSE Standard Curriculum", html)
        self.assertIn("On-site Faculty Training &amp; AMC", html)

        # 6 numbered sections
        self.assertIn("School &amp; Institution Information", html)
        self.assertIn("Key Contact Person", html)
        self.assertIn("Campus Location", html)
        self.assertIn("Laboratory Specifications", html)
        self.assertIn("Required Skill Sectors", html)
        self.assertIn("Additional Requirements", html)

        # Check interactive checkbox cards
        self.assertIn("csl-sector-checkbox-card", html)
        self.assertIn("required_sectors", html)

        # Form fields
        self.assertIn('name="school_name"', html)
        self.assertIn('name="institution_type"', html)
        self.assertIn('name="contact_person"', html)
        self.assertIn('name="email"', html)
        self.assertIn('name="phone"', html)
        self.assertIn('name="state"', html)
        self.assertIn('name="city"', html)
        self.assertIn('name="preferred_package"', html)
        self.assertIn('name="room_size"', html)
        self.assertIn('name="budget"', html)
        self.assertIn('name="additional_requirements"', html)

    def test_05_dark_mode_coverage(self):
        """Test style.css for dark mode coverage of all Composite Skill Lab components."""
        with open("static/css/style.css", "r", encoding="utf-8") as f:
            css = f.read()

        self.assertIn(".dark-mode .csl-pricing-card", css)
        self.assertIn(".dark-mode .csl-form-card", css)
        self.assertIn(".dark-mode .csl-form-input", css)
        self.assertIn(".dark-mode .csl-sector-checkbox-card", css)
        self.assertIn(".dark-mode .csl-toggle-details-btn", css)

if __name__ == "__main__":
    unittest.main()
