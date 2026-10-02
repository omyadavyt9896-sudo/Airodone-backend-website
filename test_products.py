import os
import io
import unittest
import tempfile
from werkzeug.security import generate_password_hash
from app import app, get_db_connection, init_db

class ProductSystemTestCase(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key-products"
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["DATABASE_URL"] = f"sqlite:///{self.db_path}"

        self.client = app.test_client()

        with app.app_context():
            init_db()

        self._seed_test_users()

    def tearDown(self):
        os.close(self.db_fd)
        if os.path.exists(self.db_path):
            try:
                os.unlink(self.db_path)
            except OSError:
                pass

    def _seed_test_users(self):
        conn = get_db_connection()
        cur = conn.cursor()

        # Admin
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, '2026-01-01')",
            ("Admin User", "admin_prod@test.com", generate_password_hash("pass123"), "admin")
        )
        # Sub-Admin
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, '2026-01-01')",
            ("SubAdmin User", "subadmin_prod@test.com", generate_password_hash("pass123"), "sub_admin")
        )
        # Teacher
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, '2026-01-01')",
            ("Teacher User", "teacher_prod@test.com", generate_password_hash("pass123"), "teacher")
        )
        # Student / User
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, '2026-01-01')",
            ("Student User", "student_prod@test.com", generate_password_hash("pass123"), "user")
        )
        conn.commit()
        cur.close()
        conn.close()

    def _login(self, email, password="pass123"):
        return self.client.post("/login", data={"email": email, "password": password}, follow_redirects=True)

    # 1. Public /products page works.
    def test_01_public_products_page_works(self):
        res = self.client.get("/products")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"AI Vision Starter Kit", res.data)

    # 2. Only active products appear publicly.
    def test_02_only_active_products_appear_publicly(self):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE products SET is_active = 0 WHERE slug = 'ai-vision-starter-kit'")
        conn.commit()
        cur.close()
        conn.close()

        res = self.client.get("/products")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(b"ai-vision-starter-kit", res.data)

    # 3. Product detail page works.
    def test_03_product_detail_page_works(self):
        res = self.client.get("/products/ai-vision-starter-kit")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"AI Vision Starter Kit", res.data)
        self.assertIn(b"ABOUT THIS PRODUCT", res.data)
        self.assertIn(b"WHAT'S INCLUDED", res.data)
        self.assertIn(b"APPLICATIONS", res.data)

    # 4. Product price renders correctly.
    def test_04_product_price_renders_correctly(self):
        res = self.client.get("/products/ai-vision-starter-kit")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"\xe2\x82\xb917,999", res.data)  # ₹17,999 in UTF-8

    # 5. Available product shows Buy Now.
    def test_05_available_product_shows_buy_now(self):
        res = self.client.get("/products/ai-vision-starter-kit")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Buy Now", res.data)
        self.assertIn(b"Available", res.data)

    # 6. Out-of-stock product cannot be purchased.
    def test_06_out_of_stock_product_cannot_be_purchased(self):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE products SET availability = 'Out of Stock' WHERE slug = 'ai-vision-starter-kit'")
        conn.commit()
        cur.close()
        conn.close()

        res = self.client.get("/products/ai-vision-starter-kit")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Out of Stock", res.data)
        self.assertIn(b"Currently Unavailable", res.data)

    # 7. Admin can create a product.
    def test_07_admin_can_create_product(self):
        self._login("admin_prod@test.com")
        res = self.client.post("/admin/products/new", data={
            "name": "Test Admin Kit",
            "slug": "test-admin-kit",
            "price": "12999.00",
            "short_description": "Test kit short desc",
            "description": "Full description of test kit",
            "availability": "Available",
            "shipping_setting": "free",
            "components": "Comp 1\nComp 2",
            "applications": "App 1\nApp 2",
            "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Test Admin Kit", res.data)

    # 8. Sub-Admin can create a product.
    def test_08_subadmin_can_create_product(self):
        self._login("subadmin_prod@test.com")
        res = self.client.post("/admin/products/new", data={
            "name": "SubAdmin Test Kit",
            "slug": "subadmin-test-kit",
            "price": "8999.00",
            "short_description": "Subadmin kit short desc",
            "description": "Subadmin kit full desc",
            "availability": "Available",
            "shipping_setting": "free",
            "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"SubAdmin Test Kit", res.data)

    # 9. Teacher cannot manage products.
    def test_09_teacher_cannot_manage_products(self):
        self._login("teacher_prod@test.com")
        res = self.client.get("/admin/products")
        self.assertEqual(res.status_code, 403)

        res2 = self.client.post("/admin/products/new", data={"name": "Forbidden Kit"}, follow_redirects=False)
        self.assertEqual(res2.status_code, 403)

    # 10. Student/User cannot manage products.
    def test_10_student_cannot_manage_products(self):
        self._login("student_prod@test.com")
        res = self.client.get("/admin/products")
        self.assertEqual(res.status_code, 403)

        res2 = self.client.post("/admin/products/new", data={"name": "Forbidden Kit"}, follow_redirects=False)
        self.assertEqual(res2.status_code, 403)

    # 11. Admin can edit product.
    def test_11_admin_can_edit_product(self):
        self._login("admin_prod@test.com")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM products WHERE slug = 'ai-vision-starter-kit'")
        p_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        res = self.client.post(f"/admin/products/{p_id}/edit", data={
            "name": "AI Vision Starter Kit Updated",
            "slug": "ai-vision-starter-kit",
            "price": "19999.00",
            "short_description": "Updated short desc",
            "description": "Updated description",
            "availability": "Available",
            "shipping_setting": "free",
            "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"AI Vision Starter Kit Updated", res.data)

    # 12. Sub-Admin can edit product.
    def test_12_subadmin_can_edit_product(self):
        self._login("subadmin_prod@test.com")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM products WHERE slug = 'robotics-starter-kits'")
        p_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        res = self.client.post(f"/admin/products/{p_id}/edit", data={
            "name": "Robotics Starter Kits Subadmin Modified",
            "slug": "robotics-starter-kits",
            "price": "6499.00",
            "short_description": "Subadmin modified desc",
            "description": "Full desc",
            "availability": "Available",
            "shipping_setting": "free",
            "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Robotics Starter Kits Subadmin Modified", res.data)

    # 13. Admin can change price.
    def test_13_admin_can_change_price(self):
        self._login("admin_prod@test.com")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM products WHERE slug = 'drone-and-uav-kits'")
        p_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        # Drone price change from 29999 to 32999
        res = self.client.post(f"/admin/products/{p_id}/edit", data={
            "name": "Drone & UAV Kits",
            "slug": "drone-and-uav-kits",
            "price": "32999.00",
            "short_description": "Drone kit short desc",
            "description": "Full desc",
            "availability": "Available",
            "shipping_setting": "free",
            "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT price FROM products WHERE slug = 'drone-and-uav-kits'")
        price_val = float(cur.fetchone()["price"])
        cur.close()
        conn.close()
        self.assertEqual(price_val, 32999.00)

    # 14. Admin can change availability.
    def test_14_admin_can_change_availability(self):
        self._login("admin_prod@test.com")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM products WHERE slug = 'ai-vision-starter-kit'")
        p_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        res = self.client.post(f"/admin/products/{p_id}/edit", data={
            "name": "AI Vision Starter Kit",
            "slug": "ai-vision-starter-kit",
            "price": "17999.00",
            "availability": "Out of Stock",
            "shipping_setting": "free",
            "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT availability FROM products WHERE id = %s", (p_id,))
        status = cur.fetchone()["availability"]
        cur.close()
        conn.close()
        self.assertEqual(status, "Out of Stock")

    # 15. Admin can change shipping setting.
    def test_15_admin_can_change_shipping_setting(self):
        self._login("admin_prod@test.com")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM products WHERE slug = 'robotics-starter-kits'")
        p_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        res = self.client.post(f"/admin/products/{p_id}/edit", data={
            "name": "Robotics Starter Kits",
            "slug": "robotics-starter-kits",
            "price": "5999.00",
            "availability": "Available",
            "shipping_setting": "paid",
            "shipping_charge": "150.00",
            "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT shipping_free, shipping_charge FROM products WHERE id = %s", (p_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        self.assertEqual(row["shipping_free"], 0)
        self.assertEqual(float(row["shipping_charge"]), 150.00)

    # 16. Admin can deactivate product.
    def test_16_admin_can_deactivate_product(self):
        self._login("admin_prod@test.com")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM products WHERE slug = 'ai-vision-starter-kit'")
        p_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        res = self.client.post(f"/admin/products/{p_id}/toggle-active", follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT is_active FROM products WHERE id = %s", (p_id,))
        is_act = cur.fetchone()["is_active"]
        cur.close()
        conn.close()
        self.assertEqual(is_act, 0)

    # 17. Local image upload works.
    def test_17_local_image_upload_works(self):
        self._login("admin_prod@test.com")
        png_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        img_file = (io.BytesIO(png_data), "valid_product.png")

        res = self.client.post("/admin/products/new", data={
            "name": "Image Product Kit",
            "slug": "image-product-kit",
            "price": "9999.00",
            "availability": "Available",
            "shipping_setting": "free",
            "is_active": "1",
            "image": img_file
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Image Product Kit", res.data)

    # 18. Invalid image upload is rejected.
    def test_18_invalid_image_upload_rejected(self):
        self._login("admin_prod@test.com")
        fake_file = (io.BytesIO(b"<?php echo 'malicious script'; ?>"), "malicious.php")

        res = self.client.post("/admin/products/new", data={
            "name": "Bad Image Kit",
            "slug": "bad-image-kit",
            "price": "5000.00",
            "availability": "Available",
            "shipping_setting": "free",
            "is_active": "1",
            "image": fake_file
        }, follow_redirects=True)
        self.assertIn(b"Invalid image format", res.data)

    # 19. Editing without a new image preserves the old image.
    def test_19_edit_without_new_image_preserves_old_image(self):
        self._login("admin_prod@test.com")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, image FROM products WHERE slug = 'ai-vision-starter-kit'")
        row = cur.fetchone()
        p_id = row["id"]
        old_img = row["image"]
        cur.close()
        conn.close()

        res = self.client.post(f"/admin/products/{p_id}/edit", data={
            "name": "AI Vision Starter Kit Renamed",
            "slug": "ai-vision-starter-kit",
            "price": "17999.00",
            "availability": "Available",
            "shipping_setting": "free",
            "is_active": "1"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT image FROM products WHERE id = %s", (p_id,))
        new_img = cur.fetchone()["image"]
        cur.close()
        conn.close()
        self.assertEqual(new_img, old_img)

    # 20. Existing image paths continue to render.
    def test_20_existing_image_paths_continue_to_render(self):
        res = self.client.get("/products")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"/uploads/products/AI_VISION_STARTER_KIT.png", res.data)

    # 21. Product deletion behaves safely.
    def test_21_product_deletion_behaves_safely(self):
        self._login("admin_prod@test.com")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM products WHERE slug = 'ai-vision-starter-kit'")
        p_id = cur.fetchone()["id"]
        cur.close()
        conn.close()

        res = self.client.post(f"/admin/products/{p_id}/delete", follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM products WHERE id = %s", (p_id,))
        deleted = cur.fetchone()
        cur.close()
        conn.close()
        self.assertIsNone(deleted)


if __name__ == "__main__":
    unittest.main()
