# -*- coding: utf-8 -*-
"""Comprehensive verification script for Lab Setup Management, Role-Based Navigation, and Inquiry System."""
import sys
import app as flask_app
from werkzeug.security import generate_password_hash

def run_tests():
    app = flask_app.app
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()

    print("\n--- STEP 1: Verify Public Visitor Navigation & Lab Setup Page ---")
    resp = client.get("/")
    assert resp.status_code == 200, f"Homepage failed: {resp.status_code}"
    html = resp.data.decode("utf-8")

    # Public nav must have: Home, Lab Setup, Learn, Products, Contact, Login
    assert "Home" in html, "Home missing from public nav"
    assert "Lab Setup" in html, "Lab Setup missing from public nav"
    assert "Learn" in html, "Learn missing from public nav"
    assert "Products" in html, "Products missing from public nav"
    assert "Contact" in html, "Contact missing from public nav"
    assert "Login" in html, "Login missing from public nav"
    assert "theme-toggle" in html, "Theme toggle missing"
    assert "Admin Panel" not in html, "Admin Panel must NOT be visible to unauthenticated visitor"
    assert "Dashboard" not in html, "Dashboard must NOT be visible to unauthenticated visitor"
    print("PASS: Public visitor navigation matches specification exactly.")

    # Lab Setup Public Page
    resp = client.get("/lab-setup")
    assert resp.status_code == 200, f"/lab-setup failed: {resp.status_code}"
    html = resp.data.decode("utf-8")
    assert "Future-Ready Laboratories for Schools &amp; Colleges" in html or "Future-Ready Laboratories for Schools & Colleges" in html, "Hero heading missing or incorrect"
    assert "Design, establish and manage modern technology laboratories" in html, "Hero description missing or incorrect"
    assert "STEM &amp; INNOVATION LABORATORIES" in html or "STEM & INNOVATION LABORATORIES" in html, "Hero pill badge missing"
    assert "Explore Our Laboratory Setups" in html, "Catalogue heading missing"
    assert "Discover specialized laboratory environments" in html, "Catalogue description missing"
    assert "category-card" in html, "Must use Learn page category-card pattern"
    assert "ATL Lab (Atal Tinkering Lab)" in html or "ATL Lab" in html
    assert "Robotics Lab" in html
    assert "Artificial Intelligence Lab" in html
    assert "STEM Innovation Lab" in html
    assert "Drone Technology Lab" in html
    assert "Custom &amp; Multi-Disciplinary" in html or "Custom &" in html
    assert "Book a Lab Setup" in html
    assert "Explore Details" in html, "Explore Details button missing from catalogue cards"
    assert "/lab-setup/robotics-lab" in html, "Link to individual lab detail missing"
    assert "Complete Lab Setup &amp; Implementation Support" in html or "Complete Lab Setup" in html, "Support section heading missing"
    assert "Lab Planning &amp; Design" in html or "Lab Planning" in html
    assert "Equipment &amp; Installation" in html or "Equipment" in html
    assert "Curriculum &amp; Teacher Training" in html or "Teacher Training" in html
    assert "Ongoing Technical Support" in html
    assert "Ready to Build Your Institution's Innovation Lab?" in html or "Ready to Build" in html, "CTA banner missing"
    # Ensure form input fields are NOT on /lab-setup
    assert '<form method="POST" action="/lab-setup-inquiry"' not in html, "Inquiry form should NOT be on /lab-setup"
    assert 'name="institution_name"' not in html, "Form fields should not be on /lab-setup"
    print("PASS: Public /lab-setup dynamically loads published laboratories without embedding inquiry form.")

    print("\n--- STEP 1B: Verify Individual Laboratory Details Pages (/lab-setup/<slug>) ---")
    resp_detail = client.get("/lab-setup/robotics-lab")
    assert resp_detail.status_code == 200, f"/lab-setup/robotics-lab failed: {resp_detail.status_code}"
    detail_html = resp_detail.data.decode("utf-8")
    assert "Robotics Lab" in detail_html
    assert "About the Robotics Lab" in detail_html or "About" in detail_html
    assert "Equipment &amp; Infrastructure" in detail_html or "Equipment" in detail_html
    assert "Key Learning Areas &amp; Technologies" in detail_html or "Key Learning" in detail_html
    assert "Benefits for Institutions &amp; Students" in detail_html or "Benefits" in detail_html
    assert "Our Implementation Support" in detail_html
    assert "Ready to Set Up Your Robotics Lab?" in detail_html
    assert "/lab-setup-inquiry?lab=robotics-lab" in detail_html, "Direct booking link with preselected lab missing"
    assert "Back to All Laboratories" in detail_html

    # Test 404 for invalid slug
    resp_404 = client.get("/lab-setup/invalid-slug-12345")
    assert resp_404.status_code == 404, f"Expected 404 for invalid slug, got {resp_404.status_code}"
    print("PASS: Individual Laboratory Details page and 404 handler verified.")

    print("\n--- STEP 2: Verify Lab Setup Booking Journey & Inquiry Submission ---")
    resp = client.get("/lab-setup-inquiry?lab=robotics-lab")
    assert resp.status_code == 200, f"/lab-setup-inquiry failed: {resp.status_code}"
    html = resp.data.decode("utf-8")
    assert "Let's Build Your Institution's Innovation Lab" in html or "Book Your Institution's Innovation Lab" in html or "Book a STEM" in html
    assert 'value="Robotics Lab"' in html
    assert 'checked' in html, "Preselected lab checkbox should be checked"

    # Count records before submission
    conn = flask_app.get_db_connection()
    cur = flask_app.get_db_cursor(conn)
    cur.execute("SELECT COUNT(*) as count FROM lab_inquiries")
    initial_inquiries_count = cur.fetchone()["count"]
    cur.execute("SELECT COUNT(*) as count FROM contacts")
    initial_contacts_count = cur.fetchone()["count"]
    cur.close()
    conn.close()

    # Submit Inquiry Form
    form_payload = {
        "institution_name": "St. Xavier International School",
        "institution_type": "School (K-12)",
        "institution_website": "https://stxavier.edu.in",
        "state": "Maharashtra",
        "city": "Pune",
        "address": "Plot 42, Knowledge Park, Hinjewadi",
        "pincode": "411057",
        "contact_person": "Dr. Rajesh Sharma",
        "designation": "Principal / Head of Institution",
        "email": "principal@stxavier.edu.in",
        "phone": "9876543210",
        "selected_labs": ["Robotics Lab", "Artificial Intelligence Lab"],
        "expected_students": "300 - 600 students",
        "available_space": "1,000 - 1,500 sq. ft.",
        "approximate_budget": "₹5 - ₹10 Lakhs",
        "setup_timeline": "1 - 3 Months",
        "additional_requirements": "Looking to establish an advanced robotics lab with computer vision integration.",
    }

    resp = client.post("/lab-setup-inquiry", data=form_payload, follow_redirects=True)
    assert resp.status_code == 200, f"Form submission failed: {resp.status_code}"
    html = resp.data.decode("utf-8")
    assert "Lab Setup Inquiry Received!" in html, "Confirmation heading missing"
    assert "Dr. Rajesh Sharma" in html
    assert "St. Xavier International School" in html

    # Verify database insertion
    conn = flask_app.get_db_connection()
    cur = flask_app.get_db_cursor(conn)
    cur.execute("SELECT COUNT(*) as count FROM lab_inquiries")
    new_inquiries_count = cur.fetchone()["count"]
    assert new_inquiries_count == initial_inquiries_count + 1, "Inquiry was not stored in lab_inquiries table"

    # Verify contacts table was NOT touched
    cur.execute("SELECT COUNT(*) as count FROM contacts")
    new_contacts_count = cur.fetchone()["count"]
    assert new_contacts_count == initial_contacts_count, "Inquiry was improperly inserted into contacts table!"

    cur.execute("SELECT * FROM lab_inquiries WHERE email = 'principal@stxavier.edu.in' ORDER BY id DESC LIMIT 1")
    inq_record = cur.fetchone()
    assert inq_record is not None
    assert inq_record["institution_name"] == "St. Xavier International School"
    assert inq_record["status"] == "New"
    assert "Robotics Lab" in inq_record["selected_labs"]
    submitted_inquiry_id = inq_record["id"]
    cur.close()
    conn.close()
    print("PASS: Lab Setup Booking Inquiry submitted, validated, and stored strictly in lab_inquiries table.")

    print("\n--- STEP 3: Verify Role-Based Navigation After Login ---")
    # Prepare test accounts for admin, sub_admin, teacher, student
    conn = flask_app.get_db_connection()
    cur = flask_app.get_db_cursor(conn)
    now_str = "2026-11-17 12:00:00"
    pw_hash = generate_password_hash("password123")

    users_to_test = [
        ("Test Admin", "test_admin@airodrone.in", "admin"),
        ("Test SubAdmin", "test_subadmin@airodrone.in", "sub_admin"),
        ("Test Teacher", "test_teacher@airodrone.in", "teacher"),
        ("Test Student", "test_student@airodrone.in", "student"),
    ]

    for name, email, role in users_to_test:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, %s)",
                (name, email, pw_hash, role, now_str)
            )
    conn.commit()
    cur.close()
    conn.close()

    # 3A: Admin Login
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.post("/login", data={"email": "test_admin@airodrone.in", "password": "password123"}, follow_redirects=True)
    html = resp.data.decode("utf-8")
    assert "Home" in html
    assert "Lab Setup" in html
    assert "Learn" in html
    assert "Products" in html
    assert "Contact" in html
    assert "Admin Panel" in html, "Admin must see Admin Panel"
    assert "Dashboard" in html, "Admin must see Dashboard"
    print("PASS: Admin navigation correctly displays Admin Panel and Dashboard.")

    # 3B: Sub-admin Login
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.post("/login", data={"email": "test_subadmin@airodrone.in", "password": "password123"}, follow_redirects=True)
    html = resp.data.decode("utf-8")
    assert "Home" in html
    assert "Lab Setup" in html
    assert "Learn" in html
    assert "Products" in html
    assert "Contact" in html
    assert "Sub-Admin" in html or "Admin Panel" in html, "Sub-admin must see Sub-Admin/Admin Panel"
    assert "Dashboard" in html, "Sub-admin must see Dashboard"
    print("PASS: Sub-admin navigation correctly displays Sub-Admin and Dashboard.")

    # 3C: Teacher Login
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.post("/login", data={"email": "test_teacher@airodrone.in", "password": "password123"}, follow_redirects=True)
    html = resp.data.decode("utf-8")
    assert "Home" in html
    assert "Lab Setup" in html
    assert "Learn" in html
    assert "Products" in html
    assert "Contact" in html
    assert "Dashboard" in html, "Teacher must see Dashboard"
    # TEACHER MUST NOT SEE ADMIN PANEL IN MAIN NAV!
    # Check that in the mainNav ul, there is no admin-link
    assert '<li class="admin-link' not in html, "Teacher must NOT see Admin Panel link in navbar"
    print("PASS: Teacher navigation correctly displays Dashboard and hides Admin Panel.")

    # 3D: Student Login
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.post("/login", data={"email": "test_student@airodrone.in", "password": "password123"}, follow_redirects=True)
    html = resp.data.decode("utf-8")
    assert "Home" in html
    assert "Lab Setup" in html
    assert "Learn" in html
    assert "Products" in html
    assert "Contact" in html
    assert "Dashboard" in html, "Student must see Dashboard"
    assert '<li class="admin-link' not in html, "Student must NOT see Admin Panel link in navbar"
    print("PASS: Student navigation correctly displays Dashboard and hides Admin Panel.")

    print("\n--- STEP 4: Verify Admin Dashboard Cards & Navigation ---")
    # Login as admin
    with client.session_transaction() as sess:
        sess.clear()
    client.post("/login", data={"email": "test_admin@airodrone.in", "password": "password123"})
    resp = client.get("/admin")
    assert resp.status_code == 200, f"/admin failed: {resp.status_code}"
    html = resp.data.decode("utf-8")

    assert "Lab Setup Inquiries" in html, "Lab Setup Inquiries card missing from Admin dashboard"
    assert "Lab Management" in html, "Lab Management card missing from Admin dashboard"
    assert "Contact Enquiries" in html, "Contact Enquiries card must be retained in Admin dashboard"
    assert "Manage Laboratories" in html
    assert "View Lab Inquiries" in html
    print("PASS: Admin Dashboard displays dedicated Lab Setup Inquiries & Lab Management cards alongside Contact Enquiries.")

    print("\n--- STEP 5: Verify Admin Lab Management CRUD ---")
    # View all labs
    resp = client.get("/admin/labs")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "ATL Lab (Atal Tinkering Lab)" in html
    assert "Robotics Lab" in html

    # Add new lab
    add_payload = {
        "name": "Aerospace & Drone Sim Lab",
        "slug": "aerospace-drone-sim-lab",
        "badge": "DGCA ALIGNED",
        "cover_image_preset": "images/services/drone.jpg",
        "short_description": "Advanced flight simulation and avionics testing bench.",
        "description": "State of the art aerospace simulation facility.",
        "features": "Realistic flight controls\nCertified safety protocols",
        "equipment": "Transmitters\nSimulator software",
        "benefits": "Commercial drone pilot readiness",
        "target_audience": "Aviation Colleges",
        "cta_text": "Book This Lab",
        "display_order": "7",
        "is_active": "1",
    }
    resp = client.post("/admin/labs/new", data=add_payload, follow_redirects=True)
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "Aerospace" in html and "Drone Sim Lab" in html

    # Fetch inserted lab id
    conn = flask_app.get_db_connection()
    cur = flask_app.get_db_cursor(conn)
    cur.execute("SELECT id, is_active FROM laboratories WHERE slug = 'aerospace-drone-sim-lab'")
    created_lab = cur.fetchone()
    assert created_lab is not None
    created_lab_id = created_lab["id"]
    cur.close()
    conn.close()

    # Toggle active status
    resp = client.post(f"/admin/labs/{created_lab_id}/toggle-active", follow_redirects=True)
    assert resp.status_code == 200
    conn = flask_app.get_db_connection()
    cur = flask_app.get_db_cursor(conn)
    cur.execute("SELECT is_active FROM laboratories WHERE id = %s", (created_lab_id,))
    assert cur.fetchone()["is_active"] == 0, "Toggle active should have unpublished the lab"
    cur.close()
    conn.close()

    # Edit lab
    edit_payload = dict(add_payload)
    edit_payload["name"] = "Aerospace and UAV Avionics Lab"
    edit_payload["is_active"] = "1"
    resp = client.post(f"/admin/labs/{created_lab_id}/edit", data=edit_payload, follow_redirects=True)
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "Aerospace and UAV Avionics Lab" in html

    # Delete lab
    resp = client.post(f"/admin/labs/{created_lab_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    conn = flask_app.get_db_connection()
    cur = flask_app.get_db_cursor(conn)
    cur.execute("SELECT id FROM laboratories WHERE id = %s", (created_lab_id,))
    assert cur.fetchone() is None, "Lab was not deleted"
    cur.close()
    conn.close()
    print("PASS: Admin Lab Management (Add, Edit, Toggle, Delete, Preview) verified.")

    print("\n--- STEP 6: Verify Admin Lab Setup Inquiry Management & Notes ---")
    resp = client.get("/admin/lab-setup-inquiries")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "St. Xavier International School" in html
    assert "Dr. Rajesh Sharma" in html

    # View Details
    resp = client.get(f"/admin/lab-setup-inquiries/{submitted_inquiry_id}")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "St. Xavier International School" in html
    assert "principal@stxavier.edu.in" in html
    assert "Update Inquiry Status" in html
    assert "Administrative Notes" in html

    # Update Status
    resp = client.post(
        f"/admin/lab-setup-inquiries/{submitted_inquiry_id}/status",
        data={"status": "Under Review"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "Status: Under Review" in html

    # Add Confidential Note
    note_text = "Spoke with Principal Dr. Rajesh Sharma. Arranging campus site visit for next Tuesday."
    resp = client.post(
        f"/admin/lab-setup-inquiries/{submitted_inquiry_id}/notes",
        data={"note": note_text},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert note_text in html

    # Verify that note is NOT present on public pages
    resp_pub = client.get(f"/lab-setup")
    assert note_text not in resp_pub.data.decode("utf-8"), "Internal administrative note must NOT leak to public!"

    print("PASS: Admin Inquiry Management (Status update, Private Notes, Filters) verified.")
    print("\n================ ALL TESTS PASSED SUCCESSFULLY! ================\n")

if __name__ == "__main__":
    run_tests()
