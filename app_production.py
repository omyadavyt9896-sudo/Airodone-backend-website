import os
import re
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# --- Production Capabilities ---
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "super-secure-production-key")
app.config["DATABASE_URL"] = os.environ.get("DATABASE_URL", "postgresql://postgres:password@localhost:5432/airodrone")

# Secure Session & Cookie Configuration
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SECURE=True,
)

csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")

# Token Serializer for Email and Password Resets
serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "info"




@app.after_request
def add_security_headers(response):
    response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' 'unsafe-eval' https://fonts.googleapis.com https://fonts.gstatic.com"
    response.headers['X-Frame-Options'] = "SAMEORIGIN"
    response.headers['X-Content-Type-Options'] = "nosniff"
    response.headers['Strict-Transport-Security'] = "max-age=31536000; includeSubDomains"
    return response


# ---------- Database Helpers (PostgreSQL) ----------

def get_db_connection():
    conn = psycopg2.connect(app.config["DATABASE_URL"])
    conn.autocommit = True
    return conn

def init_db():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(50) NOT NULL DEFAULT 'user',
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    email VARCHAR(255) NOT NULL,
                    phone VARCHAR(50),
                    subject VARCHAR(255),
                    message TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
            """)

            cur.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
            admin_count = cur.fetchone()[0]

            if admin_count == 0:
                default_password = os.environ.get("ADMIN_DEFAULT_PASSWORD", "admin123")
                password_hash = generate_password_hash(default_password)
                created_at = datetime.utcnow()
                cur.execute(
                    """
                    INSERT INTO users (name, email, password_hash, role, is_active, is_verified, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    ("Admin User", "admin@steroaim.com", password_hash, "admin", True, True, created_at),
                )
                print("Default admin user created: admin@steroaim.com")


# ---------- User Model ----------

class User(UserMixin):
    def __init__(self, id, name, email, role, is_verified, active=True):
        self.id = id
        self.name = name
        self.email = email
        self.role = role
        self.is_verified = is_verified
        self._active = active

    @property
    def is_active(self):
        return self._active

    def is_admin(self):
        return self.role == "admin"


@login_manager.user_loader
def load_user(user_id):
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, name, email, role, is_active, is_verified FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()

    if user and user["is_active"]:
        return User(
            id=user["id"],
            name=user["name"],
            email=user["email"],
            role=user["role"],
            is_verified=user["is_verified"],
            active=bool(user["is_active"])
        )
    return None

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin():
            flash("Access denied. Admin privileges required.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated_function


# ---------- Email Verification Mock ----------
def send_verification_email(user_email):
    # Mock function using local prints. 
    # In production, link to Flask-Mail or external providers like SendGrid.
    token = serializer.dumps(user_email, salt='email-verification-salt')
    link = url_for('confirm_email', token=token, _external=True)
    print(f"\\n--- Verification Email ---\\nTo: {user_email}\\nLink: {link}\\n-------------------------\\n")

def send_password_reset_email(user_email):
    token = serializer.dumps(user_email, salt='password-reset-salt')
    link = url_for('reset_password_with_token', token=token, _external=True)
    print(f"\\n--- Password Reset Email ---\\nTo: {user_email}\\nLink: {link}\\n----------------------------\\n")


# ---------- Authentication Routes ----------

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        # Password Strength Validation Regex
        password_regex = r"^(?=.*[A-Za-z])(?=.*\\d)[A-Za-z\\d]{8,}$"

        if not name or not email or not password:
            error = "Please fill in all fields."
        elif not re.match(r"[^@]+@[^@]+\\.[^@]+", email):
            error = "Invalid email format."
        elif password != confirm_password:
            error = "Passwords do not match."
        elif not re.match(password_regex, password):
            error = "Password must be at least 8 characters and include both letters and numbers."
        else:
            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
                        existing = cur.fetchone()

                        if existing:
                            error = "Account registration failed."
                        else:
                            password_hash = generate_password_hash(password)
                            created_at = datetime.utcnow()
                            cur.execute(
                                """
                                INSERT INTO users (name, email, password_hash, role, is_active, is_verified, created_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                                """,
                                (name, email, password_hash, "user", True, False, created_at),
                            )
                            send_verification_email(email)
                            flash("Registration successful! Please check your email to verify your account.", "success")
                            return redirect(url_for("login"))
            except Exception as e:
                error = "An error occurred during registration. Please try again."

    return render_template("register.html", error=error, active_page="register")


@app.route("/verify/<token>")
def confirm_email(token):
    try:
        email = serializer.loads(token, salt='email-verification-salt', max_age=3600)
    except (SignatureExpired, BadSignature):
        flash("The confirmation link is invalid or has expired.", "error")
        return redirect(url_for('login'))
        
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET is_verified = TRUE WHERE email = %s", (email,))
    
    flash("Account verified successfully! You may now login.", "success")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # Always run hashing to mitigate timing attacks
        dummy_hash = generate_password_hash("dummy_constant_time_prevention")
        db_password_hash = dummy_hash
        user_valid = False

        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, name, email, password_hash, role, is_active, is_verified FROM users WHERE email = %s", (email,))
                user = cur.fetchone()

                if user:
                    db_password_hash = user["password_hash"]
                
                # Check password consistently regardless of user existence
                password_is_valid = check_password_hash(db_password_hash, password)

                if user and user["is_active"] and password_is_valid:
                    if not user["is_verified"]:
                        error = "Please verify your email before logging in."
                    else:
                        user_valid = True

        if user_valid:
            user_obj = User(
                id=user["id"], name=user["name"], email=user["email"],
                role=user["role"], is_verified=user["is_verified"], active=user["is_active"]
            )
            remember = True if request.form.get("remember") else False
            login_user(user_obj, remember=remember)
            flash(f"Welcome back, {user['name']}!", "success")
            next_page = request.args.get("next")
            return redirect(next_page) if next_page else redirect(url_for("dashboard"))
        else:
            if not error:
                error = "Invalid credentials."

    return render_template("login.html", error=error, active_page="login")


@app.route("/reset_password", methods=["GET", "POST"])
@limiter.limit("3 per minute", methods=["POST"])
def reset_password_request():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE email = %s", (email,))
                user = cur.fetchone()
                if user:
                    send_password_reset_email(email)
        # Always return generic success to avoid email enumeration
        flash("If your email is registered, you will receive a reset link.", "info")
        return redirect(url_for("login"))
    return render_template("reset_password_request.html")


@app.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password_with_token(token):
    try:
        email = serializer.loads(token, salt='password-reset-salt', max_age=1800) # 30 mins
    except:
        flash("The reset link is invalid or has expired.", "error")
        return redirect(url_for('login'))
        
    error = None
    if request.method == "POST":
        password = request.form.get("password")
        password_regex = r"^(?=.*[A-Za-z])(?=.*\\d)[A-Za-z\\d]{8,}$"
        
        if not re.match(password_regex, password):
            error = "Password must be at least 8 characters and include both letters and numbers."
        else:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    new_hash = generate_password_hash(password)
                    cur.execute("UPDATE users SET password_hash = %s WHERE email = %s", (new_hash, email))
            flash("Your password has been reset successfully. Please log in.", "success")
            return redirect(url_for("login"))
            
    return render_template("reset_password.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    flash("You have been logged out securely.", "success")
    return redirect(url_for("home"))




# Include standard app routes...
@app.route("/")
def home():
    return render_template("home.html", active_page="home")

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", active_page="dashboard")

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
