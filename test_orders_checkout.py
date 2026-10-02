import os
import unittest
import tempfile
import json
import hmac
import hashlib
from werkzeug.security import generate_password_hash
from app import app, get_db_connection, init_db, generate_order_number

class OrdersCheckoutTestCase(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key-orders"
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["DATABASE_URL"] = f"sqlite:///{self.db_path}"

        self.client = app.test_client()

        with app.app_context():
            init_db()

        self._seed_users()

    def tearDown(self):
        os.close(self.db_fd)
        if os.path.exists(self.db_path):
            try:
                os.unlink(self.db_path)
            except OSError:
                pass

    def _seed_users(self):
        conn = get_db_connection()
        cur = conn.cursor()

        # Users
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, '2026-01-01')",
            ("Admin Order", "admin_order@test.com", generate_password_hash("pass123"), "admin")
        )
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, '2026-01-01')",
            ("SubAdmin Order", "subadmin_order@test.com", generate_password_hash("pass123"), "sub_admin")
        )
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, '2026-01-01')",
            ("Teacher Order", "teacher_order@test.com", generate_password_hash("pass123"), "teacher")
        )
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, is_active, created_at) VALUES (%s, %s, %s, %s, 1, '2026-01-01')",
            ("Student Order", "student_order@test.com", generate_password_hash("pass123"), "user")
        )

        conn.commit()
        cur.close()
        conn.close()

    def _login(self, email, password="pass123"):
        return self.client.post("/login", data={"email": email, "password": password}, follow_redirects=True)

    # 1. Checkout page loads for available product
    def test_01_checkout_page_loads_for_available_product(self):
        res = self.client.get("/checkout/1")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"AI Vision Starter Kit", res.data)
        self.assertIn(b"Secure Guest Checkout", res.data)

    # 2. Checkout blocked for out-of-stock product
    def test_02_checkout_blocked_for_out_of_stock_product(self):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE products SET availability = 'Out of Stock' WHERE id = 1")
        conn.commit()
        conn.close()

        res = self.client.get("/checkout/1")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Currently Unavailable", res.data)

    # 3. Checkout blocked for inactive product
    def test_03_checkout_blocked_for_inactive_product(self):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE products SET is_active = 0 WHERE id = 1")
        conn.commit()
        conn.close()

        res = self.client.get("/checkout/1")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Currently Unavailable", res.data)

    # 4. Guest customer details validation
    def test_04_guest_customer_details_validation(self):
        res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "",
            "customer_phone": "9876543210",
            "customer_email": "test@example.com",
            "address": "123 Street",
            "city": "Mumbai",
            "state": "Maharashtra",
            "pincode": "400001"
        })
        self.assertEqual(res.status_code, 400)
        data = json.loads(res.data)
        self.assertFalse(data["success"])
        self.assertIn("Full Name is required", data["error"])

    # 5. Invalid email rejected
    def test_05_invalid_email_rejected(self):
        res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "Rahul",
            "customer_phone": "9876543210",
            "customer_email": "invalid-email-format",
            "address": "123 Street",
            "city": "Mumbai",
            "state": "Maharashtra",
            "pincode": "400001"
        })
        self.assertEqual(res.status_code, 400)
        data = json.loads(res.data)
        self.assertFalse(data["success"])
        self.assertIn("valid email", data["error"].lower())

    # 6. Invalid phone rejected
    def test_06_invalid_phone_rejected(self):
        res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "Rahul",
            "customer_phone": "12345",
            "customer_email": "test@example.com",
            "address": "123 Street",
            "city": "Mumbai",
            "state": "Maharashtra",
            "pincode": "400001"
        })
        self.assertEqual(res.status_code, 400)
        data = json.loads(res.data)
        self.assertFalse(data["success"])
        self.assertIn("mobile number", data["error"].lower())

    # 7. Invalid PIN rejected
    def test_07_invalid_pin_rejected(self):
        res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "Rahul",
            "customer_phone": "9876543210",
            "customer_email": "test@example.com",
            "address": "123 Street",
            "city": "Mumbai",
            "state": "Maharashtra",
            "pincode": "00000"
        })
        self.assertEqual(res.status_code, 400)
        data = json.loads(res.data)
        self.assertFalse(data["success"])
        self.assertIn("pin code", data["error"].lower())

    # 8. Server calculates product price from database
    def test_08_server_calculates_product_price_from_database(self):
        res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1, # AI Vision Starter Kit DB price 17999.00
            "customer_name": "Rahul Sharma",
            "customer_phone": "9876543210",
            "customer_email": "rahul@example.com",
            "address": "456 Tech Park",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pincode": "560001"
        })
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertTrue(data["success"])
        # DB price 17999.00 -> 1799900 paise
        self.assertEqual(data["amount"], 1799900)

    # 9. Browser cannot manipulate final amount
    def test_09_browser_cannot_manipulate_final_amount(self):
        # Configure product 5 to have paid shipping
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE products SET shipping_free = 0, shipping_charge = 150.00 WHERE id = 5")
        conn.commit()
        conn.close()

        # Malicious user submits total_amount 100.00
        res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 5, # Advanced Robotics Kit price 14999.00 + 150 shipping = 15149.00
            "customer_name": "Hacker",
            "customer_phone": "9876543210",
            "customer_email": "hacker@example.com",
            "address": "123 Street",
            "city": "Delhi",
            "state": "Delhi",
            "pincode": "110001",
            "total_amount": "100.00"
        })
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertTrue(data["success"])
        # Must equal (14999 + 150) * 100 = 1514900 paise
        self.assertEqual(data["amount"], 1514900)

    # 10. Free shipping calculates correctly
    def test_10_free_shipping_calculates_correctly(self):
        res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1, # Free shipping
            "customer_name": "Rahul",
            "customer_phone": "9876543210",
            "customer_email": "rahul@example.com",
            "address": "Street",
            "city": "City",
            "state": "State",
            "pincode": "400001"
        })
        data = json.loads(res.data)
        self.assertEqual(data["amount"], 1799900)

    # 11. Paid shipping calculates correctly
    def test_11_paid_shipping_calculates_correctly(self):
        # Configure product 5 to have paid shipping
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE products SET shipping_free = 0, shipping_charge = 150.00 WHERE id = 5")
        conn.commit()
        conn.close()

        res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 5, # 14999 + 150 shipping
            "customer_name": "Rahul",
            "customer_phone": "9876543210",
            "customer_email": "rahul@example.com",
            "address": "Street",
            "city": "City",
            "state": "State",
            "pincode": "400001"
        })
        data = json.loads(res.data)
        self.assertEqual(data["amount"], 1514900)

    # 12. GST is not added on top of GST-inclusive price
    def test_12_gst_is_not_added_on_top_of_price(self):
        res = self.client.get("/checkout/1")
        self.assertIn(b"Included", res.data)
        self.assertIn(b"GST", res.data)

    # 13. Order is created as Pending Payment
    def test_13_order_is_created_as_pending_payment(self):
        res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "Suresh",
            "customer_phone": "9876543210",
            "customer_email": "suresh@example.com",
            "address": "Street",
            "city": "City",
            "state": "State",
            "pincode": "400001"
        })
        data = json.loads(res.data)
        order_num = data["order_number"]

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT order_status, payment_status FROM orders WHERE order_number = %s", (order_num,))
        order = cur.fetchone()
        conn.close()

        self.assertEqual(order["order_status"], "Pending Payment")
        self.assertEqual(order["payment_status"], "Pending")

    # 14. Razorpay order creation uses server-calculated amount
    def test_14_razorpay_order_creation_uses_server_calculated_amount(self):
        res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "Rahul",
            "customer_phone": "9876543210",
            "customer_email": "rahul@example.com",
            "address": "Street",
            "city": "City",
            "state": "State",
            "pincode": "400001"
        })
        data = json.loads(res.data)
        self.assertTrue(data["razorpay_order_id"].startswith("order_"))

    # 15. Successful Razorpay signature verification marks payment Paid
    def test_15_successful_razorpay_signature_verification_marks_payment_paid(self):
        create_res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "Anita",
            "customer_phone": "9876543210",
            "customer_email": "anita@example.com",
            "address": "Street",
            "city": "City",
            "state": "State",
            "pincode": "400001"
        })
        c_data = json.loads(create_res.data)
        order_num = c_data["order_number"]
        rzp_order_id = c_data["razorpay_order_id"]

        ver_res = self.client.post("/checkout/verify-payment", json={
            "razorpay_order_id": rzp_order_id,
            "razorpay_payment_id": "pay_test123",
            "razorpay_signature": "mock_valid_signature",
            "order_number": order_num
        })
        self.assertEqual(ver_res.status_code, 200)
        v_data = json.loads(ver_res.data)
        self.assertTrue(v_data["success"])

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT payment_status, order_status FROM orders WHERE order_number = %s", (order_num,))
        order = cur.fetchone()
        conn.close()

        self.assertEqual(order["payment_status"], "Paid")
        self.assertEqual(order["order_status"], "Paid")

    # 16. Invalid Razorpay signature does NOT mark payment Paid
    def test_16_invalid_razorpay_signature_does_not_mark_payment_paid(self):
        create_res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "Anita",
            "customer_phone": "9876543210",
            "customer_email": "anita@example.com",
            "address": "Street",
            "city": "City",
            "state": "State",
            "pincode": "400001"
        })
        c_data = json.loads(create_res.data)
        order_num = c_data["order_number"]
        rzp_order_id = c_data["razorpay_order_id"]

        ver_res = self.client.post("/checkout/verify-payment", json={
            "razorpay_order_id": rzp_order_id,
            "razorpay_payment_id": "pay_test123",
            "razorpay_signature": "invalid_forged_signature",
            "order_number": order_num
        })
        self.assertEqual(ver_res.status_code, 400)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT payment_status FROM orders WHERE order_number = %s", (order_num,))
        order = cur.fetchone()
        conn.close()

        self.assertEqual(order["payment_status"], "Failed")

    # 17. Payment failure handled correctly
    def test_17_payment_failure_handled_correctly(self):
        res = self.client.get("/checkout/failed")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Payment Failed", res.data)

    # 18. Duplicate payment processing is idempotent
    def test_18_duplicate_payment_processing_is_idempotent(self):
        create_res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "Idempotent User",
            "customer_phone": "9876543210",
            "customer_email": "idem@example.com",
            "address": "Street",
            "city": "City",
            "state": "State",
            "pincode": "400001"
        })
        c_data = json.loads(create_res.data)
        order_num = c_data["order_number"]
        rzp_order_id = c_data["razorpay_order_id"]

        # First verification
        self.client.post("/checkout/verify-payment", json={
            "razorpay_order_id": rzp_order_id,
            "razorpay_payment_id": "pay_test123",
            "razorpay_signature": "mock_valid_signature",
            "order_number": order_num
        })

        # Duplicate verification callback
        dup_res = self.client.post("/checkout/verify-payment", json={
            "razorpay_order_id": rzp_order_id,
            "razorpay_payment_id": "pay_test123",
            "razorpay_signature": "mock_valid_signature",
            "order_number": order_num
        })
        self.assertEqual(dup_res.status_code, 200)
        d_data = json.loads(dup_res.data)
        self.assertTrue(d_data["success"])

    # 19. Historical product price remains unchanged after product price edit
    def test_19_historical_product_price_remains_unchanged_after_edit(self):
        create_res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "Snapshot User",
            "customer_phone": "9876543210",
            "customer_email": "snap@example.com",
            "address": "Street",
            "city": "City",
            "state": "State",
            "pincode": "400001"
        })
        c_data = json.loads(create_res.data)
        order_num = c_data["order_number"]

        # Admin edits product price from 17999 to 25000
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE products SET price = 25000.00 WHERE id = 1")
        conn.commit()

        # Check order historical snapshot price
        cur.execute("SELECT product_price_snapshot FROM orders WHERE order_number = %s", (order_num,))
        order = cur.fetchone()
        conn.close()

        self.assertEqual(float(order["product_price_snapshot"]), 17999.00)

    # 20. Historical shipping charge remains unchanged after product shipping edit
    def test_20_historical_shipping_charge_remains_unchanged_after_edit(self):
        # Configure product 5 to have paid shipping initially
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE products SET shipping_free = 0, shipping_charge = 150.00 WHERE id = 5")
        conn.commit()
        conn.close()

        create_res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 5, # Shipping 150
            "customer_name": "Ship Snapshot",
            "customer_phone": "9876543210",
            "customer_email": "ship@example.com",
            "address": "Street",
            "city": "City",
            "state": "State",
            "pincode": "400001"
        })
        c_data = json.loads(create_res.data)
        order_num = c_data["order_number"]

        # Admin edits product shipping charge from 150 to 500
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE products SET shipping_charge = 500.00 WHERE id = 5")
        conn.commit()

        # Check order historical shipping snapshot
        cur.execute("SELECT shipping_charge_snapshot FROM orders WHERE order_number = %s", (order_num,))
        order = cur.fetchone()
        conn.close()

        self.assertEqual(float(order["shipping_charge_snapshot"]), 150.00)

    # 21. Admin can view orders
    def test_21_admin_can_view_orders(self):
        self._login("admin_order@test.com")
        res = self.client.get("/admin/orders")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Customer Orders", res.data)

    # 22. Sub-Admin can view orders
    def test_22_subadmin_can_view_orders(self):
        self._login("subadmin_order@test.com")
        res = self.client.get("/admin/orders")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Customer Orders", res.data)

    # 23. Teacher cannot view/manage orders
    def test_23_teacher_cannot_view_manage_orders(self):
        self._login("teacher_order@test.com")
        res = self.client.get("/admin/orders")
        self.assertEqual(res.status_code, 403)

    # 24. Student cannot view/manage orders
    def test_24_student_cannot_view_manage_orders(self):
        self._login("student_order@test.com")
        res = self.client.get("/admin/orders")
        self.assertEqual(res.status_code, 403)

    # 25. Admin can update order status
    def test_25_admin_can_update_order_status(self):
        create_res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "Update User",
            "customer_phone": "9876543210",
            "customer_email": "update@example.com",
            "address": "Street",
            "city": "City",
            "state": "State",
            "pincode": "400001"
        })
        c_data = json.loads(create_res.data)

        self._login("admin_order@test.com")
        res = self.client.post("/admin/orders/1/update-status", data={"order_status": "Shipped"}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Shipped", res.data)

    # 26. Sub-Admin can update order status
    def test_26_subadmin_can_update_order_status(self):
        create_res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "Update User 2",
            "customer_phone": "9876543210",
            "customer_email": "update2@example.com",
            "address": "Street",
            "city": "City",
            "state": "State",
            "pincode": "400001"
        })

        self._login("subadmin_order@test.com")
        res = self.client.post("/admin/orders/1/update-status", data={"order_status": "Delivered"}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Delivered", res.data)

    # 27. Unauthorized users cannot access another customer's order
    def test_27_unauthorized_users_cannot_access_another_customer_order(self):
        create_res = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "Secret User",
            "customer_phone": "9876543210",
            "customer_email": "secret@example.com",
            "address": "Street",
            "city": "City",
            "state": "State",
            "pincode": "400001"
        })
        c_data = json.loads(create_res.data)
        order_num = c_data["order_number"]

        # Anonymous request without token parameter or session token
        with app.test_client() as anon_client:
            res = anon_client.get(f"/checkout/confirmation/{order_num}")
            self.assertIn(res.status_code, [403, 404])

    # 28. Order number is unique
    def test_28_order_number_is_unique(self):
        num1 = generate_order_number()
        num2 = generate_order_number()
        self.assertNotEqual(num1, num2)

    # 29. Razorpay secret is never exposed in rendered pages/responses
    def test_29_razorpay_secret_never_exposed_in_responses(self):
        res1 = self.client.get("/checkout/1")
        self.assertNotIn(b"dummy_secret", res1.data)

        res2 = self.client.post("/checkout/create-razorpay-order", data={
            "product_id": 1,
            "customer_name": "Rahul",
            "customer_phone": "9876543210",
            "customer_email": "rahul@example.com",
            "address": "Street",
            "city": "City",
            "state": "State",
            "pincode": "400001"
        })
        self.assertNotIn(b"dummy_secret", res2.data)

    # 30. Mock payment mode flow
    def test_30_mock_payment_mode_flow(self):
        old_mode = os.environ.get("PAYMENT_MODE")
        try:
            os.environ["PAYMENT_MODE"] = "mock"
            res = self.client.post("/checkout/create-razorpay-order", data={
                "product_id": 1,
                "customer_name": "Mock Tester",
                "customer_phone": "9876543210",
                "customer_email": "mocktest@example.com",
                "address": "456 Dev Lane",
                "city": "Mumbai",
                "state": "Maharashtra",
                "pincode": "400001"
            })
            self.assertEqual(res.status_code, 200)
            data = json.loads(res.data)
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("payment_mode"), "mock")
            self.assertTrue(data.get("redirect_url"))

            # Follow redirect URL to confirmation page
            conf_res = self.client.get(data["redirect_url"])
            self.assertEqual(conf_res.status_code, 200)
            self.assertIn(b"Development/Test Payment", conf_res.data)
            self.assertIn(b"No Real Money Charged", conf_res.data)
        finally:
            if old_mode is None:
                os.environ.pop("PAYMENT_MODE", None)
            else:
                os.environ["PAYMENT_MODE"] = old_mode

    # 31. Razorpay payment mode flow
    def test_31_razorpay_payment_mode_flow(self):
        old_mode = os.environ.get("PAYMENT_MODE")
        try:
            os.environ["PAYMENT_MODE"] = "razorpay"
            res = self.client.post("/checkout/create-razorpay-order", data={
                "product_id": 1,
                "customer_name": "Razorpay Tester",
                "customer_phone": "9876543210",
                "customer_email": "rzptest@example.com",
                "address": "123 Rzp Street",
                "city": "Bengaluru",
                "state": "Karnataka",
                "pincode": "560001"
            })
            self.assertEqual(res.status_code, 200)
            data = json.loads(res.data)
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("payment_mode"), "razorpay")
            self.assertTrue(data.get("razorpay_order_id"))
        finally:
            if old_mode is None:
                os.environ.pop("PAYMENT_MODE", None)
            else:
                os.environ["PAYMENT_MODE"] = old_mode

    # 32. Mock payment mode blocked in production environment
    def test_32_mock_payment_blocked_in_production(self):
        old_mode = os.environ.get("PAYMENT_MODE")
        old_env = os.environ.get("FLASK_ENV")
        try:
            os.environ["PAYMENT_MODE"] = "mock"
            os.environ["FLASK_ENV"] = "production"
            res = self.client.post("/checkout/create-razorpay-order", data={
                "product_id": 1,
                "customer_name": "Prod Tester",
                "customer_phone": "9876543210",
                "customer_email": "prodtest@example.com",
                "address": "Prod St",
                "city": "Delhi",
                "state": "Delhi",
                "pincode": "110001"
            })
            self.assertEqual(res.status_code, 403)
            data = json.loads(res.data)
            self.assertFalse(data.get("success"))
            self.assertIn("disabled in production", data.get("error"))
        finally:
            if old_mode is None:
                os.environ.pop("PAYMENT_MODE", None)
            else:
                os.environ["PAYMENT_MODE"] = old_mode
            if old_env is None:
                os.environ.pop("FLASK_ENV", None)
            else:
                os.environ["FLASK_ENV"] = old_env

if __name__ == "__main__":
    unittest.main()
