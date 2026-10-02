import os
import sys

os.environ['TEMP'] = r'F:\temp'
os.environ['TMP'] = r'F:\temp'

from app import app, get_db_connection, get_db_cursor, get_learning_categories_with_counts, get_active_learning_categories

def run_tests():
    print("=" * 60)
    print("STARTING END-TO-END VERIFICATION SUITE")
    print("=" * 60)

    # Fetch Admin user for authenticated client sessions
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, email, role FROM users WHERE role = 'admin' LIMIT 1")
    admin_user = cur.fetchone()
    assert admin_user, "No admin user found in database!"
    admin_id = admin_user['id']
    admin_email = admin_user['email']
    print(f"Using Admin user: ID {admin_id}, Email {admin_email}")

    # Fetch Category IDs
    cur.execute("SELECT id, name, slug FROM learning_categories WHERE slug = 'coding-programming'")
    coding_cat = cur.fetchone()
    cur.execute("SELECT id, name, slug FROM learning_categories WHERE slug = 'drone-technology'")
    drone_cat = cur.fetchone()
    cur.close()
    conn.close()

    assert coding_cat and drone_cat, "Required categories not found!"
    coding_id = coding_cat['id']
    drone_id = drone_cat['id']

    client = app.test_client()

    # Login as admin
    with client.session_transaction() as sess:
        sess['_user_id'] = str(admin_id)
        sess['_fresh'] = True

    # -----------------------------------------------------------------
    # TEST 1: Baseline Counts
    # -----------------------------------------------------------------
    print("\n--- TEST 1: Existing Category Counts ---")
    baseline_public = {c['id']: c['course_count'] for c in get_active_learning_categories()}
    baseline_admin = {c['id']: c['course_count'] for c in get_learning_categories_with_counts(active_only=False)}

    print(f"Coding & Programming baseline count: {baseline_public[coding_id]}")
    print(f"Drone Technology baseline count: {baseline_public[drone_id]}")
    assert baseline_public == baseline_admin, "Baseline counts mismatch between public and admin!"
    print("TEST 1 PASSED: Baseline counts verified and consistent.")

    # -----------------------------------------------------------------
    # TEST 2: Add Course under Coding & Programming
    # -----------------------------------------------------------------
    print("\n--- TEST 2: Add Course to Coding & Programming ---")
    test_slug = "e2e-test-python-basics"
    res_add = client.post("/admin/courses/new", data={
        "title": "E2E Test Python Basics",
        "slug": test_slug,
        "category_id": str(coding_id),
        "learning_path_id": "",
        "grade": "2",
        "level": "Beginner",
        "description": "A test course created to verify dynamic database count updates.",
        "short_description": "Test course short description.",
        "is_active": "1"
    }, follow_redirects=True)
    assert res_add.status_code == 200, f"Add course failed with status {res_add.status_code}"

    # Verify newly created course in DB
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, title, category_id, is_active FROM courses WHERE slug = %s", (test_slug,))
    created_course = cur.fetchone()
    cur.close()
    conn.close()
    assert created_course, "Created course was not found in database!"
    test_course_id = created_course['id']
    print(f"Created Course ID: {test_course_id}, Title: {created_course['title']}")

    # Verify counts increased
    counts_after_add_public = {c['id']: c['course_count'] for c in get_active_learning_categories()}
    counts_after_add_admin = {c['id']: c['course_count'] for c in get_learning_categories_with_counts(active_only=False)}

    print(f"Coding & Programming count after add: {counts_after_add_public[coding_id]} (was {baseline_public[coding_id]})")
    assert counts_after_add_public[coding_id] == baseline_public[coding_id] + 1, "Coding & Programming count did not increase by 1!"
    assert counts_after_add_public == counts_after_add_admin, "Public and admin counts mismatch after add!"
    print("TEST 2 PASSED: Course creation increased category count dynamically.")

    # -----------------------------------------------------------------
    # TEST 3: Edit Course Category (Move to Drone Technology)
    # -----------------------------------------------------------------
    print("\n--- TEST 3: Edit Course Category (Move to Drone Technology) ---")
    res_edit = client.post(f"/admin/courses/{test_course_id}/edit", data={
        "title": "E2E Test Python Basics",
        "slug": test_slug,
        "category_id": str(drone_id),
        "learning_path_id": "",
        "grade": "3",
        "level": "Intermediate",
        "description": "Updated course moved to Drone Technology.",
        "short_description": "Updated short description.",
        "is_active": "1"
    }, follow_redirects=True)
    assert res_edit.status_code == 200, f"Edit course failed with status {res_edit.status_code}"

    # Verify counts shifted
    counts_after_move_public = {c['id']: c['course_count'] for c in get_active_learning_categories()}
    counts_after_move_admin = {c['id']: c['course_count'] for c in get_learning_categories_with_counts(active_only=False)}

    print(f"Coding & Programming count after move: {counts_after_move_public[coding_id]} (should be baseline: {baseline_public[coding_id]})")
    print(f"Drone Technology count after move: {counts_after_move_public[drone_id]} (should be baseline + 1: {baseline_public[drone_id] + 1})")

    assert counts_after_move_public[coding_id] == baseline_public[coding_id], "Coding count did not decrease back to baseline!"
    assert counts_after_move_public[drone_id] == baseline_public[drone_id] + 1, "Drone Technology count did not increase by 1!"
    assert counts_after_move_public == counts_after_move_admin, "Public and admin counts mismatch after edit!"
    print("TEST 3 PASSED: Category edit correctly updated counts across both categories.")

    # -----------------------------------------------------------------
    # TEST 4: Delete Course
    # -----------------------------------------------------------------
    print("\n--- TEST 4: Delete Course ---")
    res_delete = client.post(f"/admin/courses/{test_course_id}/delete", data={
        "action": "permanent_delete",
        "confirm_title": "E2E Test Python Basics",
        "force": "1"
    }, follow_redirects=True)
    assert res_delete.status_code == 200, f"Delete course failed with status {res_delete.status_code}"

    # Verify course deleted from DB
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id FROM courses WHERE id = %s", (test_course_id,))
    deleted_check = cur.fetchone()
    cur.close()
    conn.close()
    assert not deleted_check, "Deleted course still exists in database!"

    # Verify counts returned to baseline
    counts_after_del_public = {c['id']: c['course_count'] for c in get_active_learning_categories()}
    counts_after_del_admin = {c['id']: c['course_count'] for c in get_learning_categories_with_counts(active_only=False)}

    print(f"Drone Technology count after delete: {counts_after_del_public[drone_id]} (should be baseline: {baseline_public[drone_id]})")
    assert counts_after_del_public[drone_id] == baseline_public[drone_id], "Drone count did not return to baseline!"
    assert counts_after_del_public == baseline_public, "Counts did not restore to original baseline!"
    assert counts_after_del_public == counts_after_del_admin, "Public and admin counts mismatch after delete!"
    print("TEST 4 PASSED: Course deletion restored exact baseline counts.")

    # -----------------------------------------------------------------
    # TEST 5: Admin & Public Consistency
    # -----------------------------------------------------------------
    print("\n--- TEST 5: Admin / Public Consistency ---")
    res_learn = client.get("/learn")
    assert res_learn.status_code == 200
    learn_html = res_learn.get_data(as_text=True)

    res_admin_cats = client.get("/admin/learning-categories")
    assert res_admin_cats.status_code == 200
    admin_cats_html = res_admin_cats.get_data(as_text=True)

    for cat in get_active_learning_categories():
        expected_text = f"{cat['course_count']} Courses"
        print(f"Checking '{cat['name']}': {expected_text}")
        assert expected_text in learn_html, f"Expected '{expected_text}' in /learn page"
        assert expected_text in admin_cats_html, f"Expected '{expected_text}' in admin categories page"
    print("TEST 5 PASSED: Admin and /learn pages are 100% consistent.")

    # -----------------------------------------------------------------
    # TEST 6: Home Page Learning Section
    # -----------------------------------------------------------------
    print("\n--- TEST 6: Home Page Learning Programs ---")
    res_home = client.get("/")
    assert res_home.status_code == 200
    home_html = res_home.get_data(as_text=True)
    assert "Explore Our Learning Programs" in home_html
    assert "View All Learning Programs" in home_html
    for cat in get_active_learning_categories():
        assert f"/learn/category/{cat['slug']}" in home_html
    print("TEST 6 PASSED: Home page reflects the same database categories and working links.")

    # -----------------------------------------------------------------
    # TEST 7: Route Verifications & Security
    # -----------------------------------------------------------------
    print("\n--- TEST 7: Existing Functionality & Routes ---")
    routes = [
        ("/", 200),
        ("/about", 200),
        ("/learn", 200),
        ("/products", 200),
        ("/contact", 200),
        ("/login", 200),
        ("/admin", 200),
        ("/admin/courses", 200),
        ("/admin/learning-categories", 200),
        ("/admin/learning-paths", 200),
        ("/admin/certificates", 200),
        ("/admin/enrollments", 200),
        ("/admin/users", 200),
        ("/admin/products", 200),
        ("/admin/orders", 200),
    ]
    for route, expected_code in routes:
        res = client.get(route)
        print(f"GET {route:<32} -> {res.status_code} (expected {expected_code})")
        assert res.status_code == expected_code, f"Route {route} returned {res.status_code}, expected {expected_code}"

    # Verify redirects and 404s
    res_courses = client.get("/courses")
    print(f"GET /courses                      -> {res_courses.status_code} Location: {res_courses.headers.get('Location')}")
    assert res_courses.status_code in (301, 302) and "/learn" in res_courses.headers.get("Location", "")

    res_services = client.get("/services")
    print(f"GET /services                     -> {res_services.status_code}")
    assert res_services.status_code == 404

    print("TEST 7 PASSED: All platform routes, redirects, and 404s confirmed.")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED SUCCESSFULLY! 100% VERIFIED.")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
