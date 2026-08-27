import os
from datetime import datetime
from functools import wraps
import re

import secrets
import io
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file, send_from_directory, abort
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.pdfgen import canvas

import urllib.parse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import psycopg2
from psycopg2.extras import RealDictCursor

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    pymysql = None

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
# Ensure DATABASE_URL is set in your environment
app.config["DATABASE_URL"] = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/airodrone")

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "info"


@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.static_folder, 'robots.txt')


@app.route('/favicon.ico')
def favicon_ico():
    return send_from_directory(os.path.join(app.static_folder, 'images', 'favicon'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')


@app.route('/googlef2b0a301d69fdec5.html')
def google_verification():
    return send_from_directory(app.root_path, 'googlef2b0a301d69fdec5.html', mimetype='text/html')




# ---------- User Model ----------

class User(UserMixin):
    def __init__(self, id, name, email, role, active=True, father_name=None, phone=None):
        self.id = id
        self.name = name
        self.email = email
        self.role = role
        self._active = active
        self.father_name = father_name
        self.phone = phone

    @property
    def is_active(self):
        return self._active

    def is_admin(self):
        return self.role == "admin"


@login_manager.user_loader
def load_user(user_id):
    """Load user from database."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT id, name, email, role, is_active, father_name, phone
        FROM users
        WHERE id = %s
        """,
        (user_id,),
    )
    user = cur.fetchone()
    cur.close()
    conn.close()

    if user and user["is_active"]:
        return User(
            id=user["id"],
            name=user["name"],
            email=user["email"],
            role=user["role"],
            active=bool(user["is_active"]),
            father_name=user.get("father_name"),
            phone=user.get("phone"),
        )

    return None


def is_user_enrolled(user_id, course_id):
    """Check if a student has an explicit active enrollment for a course in database."""
    if not user_id or not course_id:
        return False
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            "SELECT id FROM course_enrollments WHERE user_id = %s AND course_id = %s AND is_active = 1",
            (user_id, course_id)
        )
        enrollment = cur.fetchone()
        cur.close()
        conn.close()
        return bool(enrollment)
    except Exception as e:
        app.logger.error(f"Error checking enrollment for user {user_id}, course {course_id}: {e}")
        return False


def can_access_course(user_id, course_id):
    """Check if user can access course content (enrolled student OR admin)."""
    if not user_id or not course_id:
        return False
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
        u = cur.fetchone()
        cur.close()
        conn.close()
        if u and u["role"] == "admin":
            return True
        return is_user_enrolled(user_id, course_id)
    except Exception as e:
        app.logger.error(f"Error checking access for user {user_id}, course {course_id}: {e}")
        return False


# ---------- Database helpers ----------

import sqlite3

class SQLiteDictCursor:
    def __init__(self, cursor):
        self.cursor = cursor
    def execute(self, sql, params=None):
        sql_converted = sql.replace('%s', '?').replace('SERIAL PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT').replace('INT AUTO_INCREMENT PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT')
        if 'ALTER TABLE' in sql_converted and 'IF NOT EXISTS' in sql_converted:
            sql_converted = sql_converted.replace('IF NOT EXISTS', '')
        try:
            if params is None:
                return self.cursor.execute(sql_converted)
            return self.cursor.execute(sql_converted, params)
        except sqlite3.OperationalError as e:
            if 'duplicate column name' in str(e).lower():
                return
            raise e
    @property
    def lastrowid(self):
        return self.cursor.lastrowid
    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None:
            return None
        return dict(row)
    def fetchall(self):
        rows = self.cursor.fetchall()
        return [dict(r) for r in rows]
    def close(self):
        self.cursor.close()

class SQLiteConnWrapper:
    def __init__(self, conn):
        self.conn = conn
    def cursor(self, cursor_factory=None):
        return SQLiteDictCursor(self.conn.cursor())
    def commit(self):
        self.conn.commit()
    def rollback(self):
        self.conn.rollback()
    def close(self):
        self.conn.close()

def get_db_type():
    """Detect database engine type: 'mysql', 'sqlite', or 'postgres'."""
    db_url = app.config.get("DATABASE_URL", "")
    if "mysql" in db_url.lower():
        return "mysql"
    elif "sqlite" in db_url.lower():
        return "sqlite"
    return "postgres"

from flask import has_request_context, g

class RequestConnProxy:
    """Proxy object wrapping raw connection so helper calls to conn.close() during request processing do not destroy g.raw_db."""
    def __init__(self, raw_conn):
        self._raw_conn = raw_conn

    def cursor(self, *args, **kwargs):
        return self._raw_conn.cursor(*args, **kwargs)

    def commit(self):
        return self._raw_conn.commit()

    def rollback(self):
        return self._raw_conn.rollback()

    def close(self):
        # Explicit no-op during request processing. Teardown hook handles actual socket closure.
        pass

    def __getattr__(self, name):
        return getattr(self._raw_conn, name)

def get_db_cursor(conn):
    """Obtain a database cursor configured to return dictionary rows across PostgreSQL, MySQL, and SQLite."""
    db_type = get_db_type()
    if db_type == "postgres":
        return conn.cursor(cursor_factory=RealDictCursor)
    return conn.cursor()

def create_raw_db_connection():
    """Create a new raw database connection based on configured DATABASE_URL (supports MySQL, SQLite, PostgreSQL)."""
    db_url = app.config.get("DATABASE_URL", "")
    if "mysql" in db_url.lower():
        if not pymysql:
            raise ImportError("PyMySQL driver is required for MySQL connections. Install with 'pip install PyMySQL'.")
        clean_url = db_url.replace("mysql+pymysql://", "mysql://")
        parsed = urllib.parse.urlparse(clean_url)
        password = urllib.parse.unquote(parsed.password) if parsed.password else ""
        return pymysql.connect(
            host=parsed.hostname or "localhost",
            port=parsed.port or 3306,
            user=parsed.username or "root",
            password=password,
            database=parsed.path.lstrip("/"),
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True
        )
    elif "sqlite" in db_url.lower():
        sqlite_path = db_url.replace("sqlite:///", "").replace("sqlite://", "") or "airodrone.db"
        conn = sqlite3.connect(sqlite_path)
        conn.row_factory = sqlite3.Row
        return SQLiteConnWrapper(conn)
    try:
        return psycopg2.connect(db_url)
    except Exception:
        sqlite_path = os.path.join(os.path.dirname(__file__), "airodrone.db")
        conn = sqlite3.connect(sqlite_path)
        conn.row_factory = sqlite3.Row
        return SQLiteConnWrapper(conn)

def get_db_connection():
    """
    Obtain database connection.
    If within a Flask request context, reuses g.raw_db for the request duration.
    Otherwise (standalone / CLI / tests), creates and returns a standalone connection.
    """
    if has_request_context():
        if "raw_db" not in g or g.raw_db is None:
            g.raw_db = create_raw_db_connection()
        return RequestConnProxy(g.raw_db)
    return create_raw_db_connection()

@app.teardown_appcontext
def close_db_connection(exception=None):
    """Safely close per-request database connection if present at app context teardown."""
    raw_db = g.pop("raw_db", None)
    if raw_db is not None:
        try:
            raw_db.close()
        except Exception as e:
            app.logger.error(f"Error closing per-request DB connection: {e}")

def add_column_if_not_exists(cur, table, column, col_type):
    """Safely execute ALTER TABLE ADD COLUMN across PostgreSQL, SQLite, and MySQL."""
    db_type = get_db_type()
    if db_type == "postgres":
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type};")
    elif db_type == "sqlite":
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type};")
        except Exception:
            pass
    elif db_type == "mysql":
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type};")
        except Exception as e:
            if "1060" in str(e) or "duplicate" in str(e).lower():
                pass
            else:
                raise e

def create_index_if_not_exists(cur, index_name, table, columns):
    """Safely create database index if it does not already exist across PostgreSQL, MySQL, and SQLite."""
    db_type = get_db_type()
    cols_str = ", ".join(columns)
    if db_type in ("postgres", "sqlite"):
        cur.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({cols_str});")
    elif db_type == "mysql":
        try:
            cur.execute(f"CREATE INDEX {index_name} ON {table} ({cols_str});")
        except Exception as e:
            if "1061" in str(e) or "duplicate" in str(e).lower():
                pass
            else:
                raise e

def init_db():
    """Create database tables if they do not exist and seed initial courses."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    db_type = get_db_type()
    pk_def = "INT AUTO_INCREMENT PRIMARY KEY" if db_type == "mysql" else "SERIAL PRIMARY KEY"
    
    # Create contacts table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS contacts (
            id {pk_def},
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            subject TEXT,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # Create users table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS users (
            id {pk_def},
            name TEXT NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )
    # Ensure father_name and phone columns exist
    add_column_if_not_exists(cur, "users", "father_name", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "users", "phone", "TEXT DEFAULT NULL")

    # Create courses table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS courses (
            id {pk_def},
            title TEXT NOT NULL,
            slug VARCHAR(255) UNIQUE NOT NULL,
            description TEXT,
            image TEXT,
            level TEXT DEFAULT 'All Levels',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # Ensure public course information columns exist (Phase 7.7.1)
    add_column_if_not_exists(cur, "courses", "short_description", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "courses", "learning_outcomes", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "courses", "course_benefits", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "courses", "estimated_duration", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "courses", "certificate_description", "TEXT DEFAULT NULL")

    # Create course_enrollments table (Phase 7.7)
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS course_enrollments (
            id {pk_def},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
            is_active INTEGER NOT NULL DEFAULT 1,
            assigned_at TEXT NOT NULL,
            assigned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, course_id)
        )
        """
    )

    # Create modules table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS modules (
            id {pk_def},
            course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT,
            sequence INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Create course_videos table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS course_videos (
            id {pk_def},
            module_id INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT,
            sequence INTEGER NOT NULL DEFAULT 1,
            duration TEXT DEFAULT '10:00',
            youtube_video_id TEXT DEFAULT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # Ensure youtube_video_id column exists if table was created previously
    add_column_if_not_exists(cur, "course_videos", "youtube_video_id", "TEXT")

    # Create video_progress table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS video_progress (
            id {pk_def},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            video_id INTEGER NOT NULL REFERENCES course_videos(id) ON DELETE CASCADE,
            watched_seconds FLOAT NOT NULL DEFAULT 0.0,
            duration_seconds FLOAT NOT NULL DEFAULT 0.0,
            completion_percentage FLOAT NOT NULL DEFAULT 0.0,
            completed BOOLEAN NOT NULL DEFAULT FALSE,
            first_started_at TEXT NOT NULL,
            last_watched_at TEXT NOT NULL,
            completed_at TEXT DEFAULT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, video_id)
        )
        """
    )

    # Create certificates table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS certificates (
            id {pk_def},
            certificate_id VARCHAR(50) UNIQUE NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
            student_name VARCHAR(255) NOT NULL,
            course_name VARCHAR(255) NOT NULL,
            completion_percentage FLOAT NOT NULL,
            issued_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, course_id)
        )
        """
    )

    # Create quizzes table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS quizzes (
            id {pk_def},
            module_id INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT,
            passing_score INTEGER NOT NULL DEFAULT 70,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Create quiz_questions table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS quiz_questions (
            id {pk_def},
            quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
            question_text TEXT NOT NULL,
            option_a TEXT NOT NULL,
            option_b TEXT NOT NULL,
            option_c TEXT NOT NULL,
            option_d TEXT NOT NULL,
            correct_option VARCHAR(1) NOT NULL,
            explanation TEXT,
            sequence INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Create quiz_attempts table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id {pk_def},
            quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            score INTEGER NOT NULL DEFAULT 0,
            total_questions INTEGER NOT NULL DEFAULT 0,
            correct_answers INTEGER NOT NULL DEFAULT 0,
            passed BOOLEAN NOT NULL DEFAULT FALSE,
            attempt_number INTEGER NOT NULL DEFAULT 1,
            is_invalidated BOOLEAN NOT NULL DEFAULT FALSE,
            started_at TEXT NOT NULL,
            submitted_at TEXT DEFAULT NULL
        )
        """
    )

    # Create quiz_answers table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS quiz_answers (
            id {pk_def},
            attempt_id INTEGER NOT NULL REFERENCES quiz_attempts(id) ON DELETE CASCADE,
            question_id INTEGER NOT NULL REFERENCES quiz_questions(id) ON DELETE CASCADE,
            selected_option VARCHAR(1),
            is_correct BOOLEAN NOT NULL DEFAULT FALSE
        )
        """
    )

    # Create targeted performance indexes across PostgreSQL, MySQL, and SQLite
    create_index_if_not_exists(cur, "idx_modules_course_id", "modules", ["course_id"])
    create_index_if_not_exists(cur, "idx_course_videos_module_id", "course_videos", ["module_id"])
    create_index_if_not_exists(cur, "idx_video_progress_user_id", "video_progress", ["user_id"])
    create_index_if_not_exists(cur, "idx_quizzes_module_id", "quizzes", ["module_id"])
    create_index_if_not_exists(cur, "idx_quiz_questions_quiz_id", "quiz_questions", ["quiz_id"])
    create_index_if_not_exists(cur, "idx_quiz_attempts_user_quiz", "quiz_attempts", ["user_id", "quiz_id"])
    create_index_if_not_exists(cur, "idx_quiz_answers_attempt_id", "quiz_answers", ["attempt_id"])

    conn.commit()

    # Create default admin user if none exists
    cur.execute("SELECT COUNT(*) as count FROM users WHERE role = 'admin'")
    admin_exists = cur.fetchone()["count"]

    if admin_exists == 0:
        default_password = "admin123"  # Change this in production!
        password_hash = generate_password_hash(default_password)
        created_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO users (name, email, password_hash, role, is_active, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            ("Admin User", "admin@steroaim.com", password_hash, "admin", 1, created_at),
        )
        conn.commit()
        print("Default admin user created: admin@steroaim.com / admin123")

    # Seed initial courses if not present
    seed_initial_courses(cur, conn)

    cur.close()
    conn.close()


def seed_initial_courses(cur, conn):
    """Seed the five core courses with sample modules and videos."""
    initial_courses = [
        {
            "title": "Drone Technology",
            "slug": "drone-technology",
            "description": "Master aerodynamics, drone components, flight dynamics, and safety protocols for unmanned aerial vehicles.",
            "image": "images/services/drone.jpg",
            "level": "Intermediate",
            "modules": [
                {
                    "title": "Module 1 — Introduction",
                    "description": "Overview of unmanned aerial vehicles, historical evolution, and classifications.",
                    "sequence": 1,
                    "videos": [
                        {"title": "Video 1 — Introduction to Drones", "description": "Fundamentals of UAV technology and applications.", "duration": "08:30", "sequence": 1},
                        {"title": "Video 2 — History of Drone Technology", "description": "Classification from multirotors to fixed-wing drones.", "duration": "12:15", "sequence": 2}
                    ]
                },
                {
                    "title": "Module 2 — Drone Components",
                    "description": "Detailed breakdown of flight controllers, ESCs, BLDC motors, and power management.",
                    "sequence": 2,
                    "videos": [
                        {"title": "Video 3 — Drone Components", "description": "Understanding frame, motors, propellers, and ESCs.", "duration": "15:00", "sequence": 1},
                        {"title": "Video 4 — Flight Controller", "description": "Configuring gyro sensors, receiver, and transmitter.", "duration": "18:45", "sequence": 2}
                    ]
                },
                {
                    "title": "Module 3 — Drone Operation",
                    "description": "Pre-flight checks, basic piloting, aerodynamics, and regulatory compliance.",
                    "sequence": 3,
                    "videos": [
                        {"title": "Video 5 — Basic Flight Principles", "description": "Pitch, roll, yaw, and altitude control mechanisms.", "duration": "10:20", "sequence": 1}
                    ]
                }
            ]
        },
        {
            "title": "Artificial Intelligence",
            "slug": "artificial-intelligence",
            "description": "Learn fundamental AI concepts, machine learning models, computer vision, and neural networks through real-world projects.",
            "image": "images/services/ai.jpg",
            "level": "Beginner to Advanced",
            "modules": [
                {
                    "title": "Module 1 — AI Foundations & Machine Learning",
                    "description": "Introduction to AI principles, data processing, and supervised learning algorithms.",
                    "sequence": 1,
                    "videos": [
                        {"title": "Video 1 — What is Artificial Intelligence?", "description": "Exploring rule-based systems vs machine learning.", "duration": "09:45", "sequence": 1},
                        {"title": "Video 2 — Supervised vs Unsupervised Learning", "description": "Regression, classification, and clustering overview.", "duration": "14:20", "sequence": 2}
                    ]
                },
                {
                    "title": "Module 2 — Computer Vision & Deep Learning",
                    "description": "Image processing fundamentals and introduction to artificial neural networks.",
                    "sequence": 2,
                    "videos": [
                        {"title": "Video 3 — Introduction to Neural Networks", "description": "Perceptrons, activation functions, and backpropagation.", "duration": "16:30", "sequence": 1},
                        {"title": "Video 4 — Computer Vision Fundamentals", "description": "Object detection and image classification basics.", "duration": "11:50", "sequence": 2}
                    ]
                }
            ]
        },
        {
            "title": "Robotics",
            "slug": "robotics",
            "description": "Design, build, and program autonomous robots using sensors, microcontrollers, and motor drivers.",
            "image": "images/services/robotics.jpg",
            "level": "All Levels",
            "modules": [
                {
                    "title": "Module 1 — Introduction to Robotics",
                    "description": "Anatomy of robots, actuators, sensors, and structural framework.",
                    "sequence": 1,
                    "videos": [
                        {"title": "Video 1 — Anatomy of a Robot", "description": "Key structural elements and power distribution.", "duration": "07:50", "sequence": 1},
                        {"title": "Video 2 — Sensors & Actuators", "description": "Ultrasonic, infrared, IR line sensors, and servo motors.", "duration": "13:40", "sequence": 2}
                    ]
                },
                {
                    "title": "Module 2 — Microcontrollers & Programming",
                    "description": "Programming microcontrollers to read sensor inputs and control movement.",
                    "sequence": 2,
                    "videos": [
                        {"title": "Video 3 — Microcontroller Setup", "description": "Arduino & ESP32 pinouts and basic code structure.", "duration": "15:10", "sequence": 1},
                        {"title": "Video 4 — Motor Control & Kinematics", "description": "H-bridge drivers and differential drive control.", "duration": "17:25", "sequence": 2}
                    ]
                }
            ]
        },
        {
            "title": "Coding & Programming",
            "slug": "coding-programming",
            "description": "Build strong programming foundations in Python and JavaScript, from logic building to algorithm design.",
            "image": "images/services/coding.jpg",
            "level": "Beginner",
            "modules": [
                {
                    "title": "Module 1 — Computational Thinking & Logic",
                    "description": "Logic flow, algorithms, variables, control structures, and loops.",
                    "sequence": 1,
                    "videos": [
                        {"title": "Video 1 — Logic Building & Flowcharts", "description": "Deconstructing problems into structured algorithmic steps.", "duration": "10:00", "sequence": 1},
                        {"title": "Video 2 — Getting Started with Python", "description": "Syntax, variables, data types, and basic I/O.", "duration": "14:15", "sequence": 2}
                    ]
                },
                {
                    "title": "Module 2 — Functions & Data Structures",
                    "description": "Modular code design using functions, lists, dictionaries, and file handling.",
                    "sequence": 2,
                    "videos": [
                        {"title": "Video 3 — Control Flow & Loops", "description": "Conditional statements and loop iterations.", "duration": "12:45", "sequence": 1},
                        {"title": "Video 4 — Functions & Reusable Code", "description": "Parameters, return values, and scope.", "duration": "16:00", "sequence": 2}
                    ]
                }
            ]
        },
        {
            "title": "ATL Lab Setup & Training",
            "slug": "atl-lab-setup-training",
            "description": "Comprehensive guide to setting up Atal Tinkering Labs, equipment maintenance, and STEM mentor capacity building.",
            "image": "images/services/atl.jpg",
            "level": "Educator / Professional",
            "modules": [
                {
                    "title": "Module 1 — ATL Infrastructure & Procurement",
                    "description": "NITI Aayog compliance, lab layout planning, and safety equipment guidelines.",
                    "sequence": 1,
                    "videos": [
                        {"title": "Video 1 — Setting Up Your ATL Lab", "description": "Space optimization, package unboxing, and inventory tagging.", "duration": "11:30", "sequence": 1},
                        {"title": "Video 2 — Equipment Calibration & Safety", "description": "3D printer setup, soldering safety, and workbench organization.", "duration": "15:50", "sequence": 2}
                    ]
                },
                {
                    "title": "Module 2 — Innovation Mentorship & Activity Roadmaps",
                    "description": "Structuring year-round tinkering activities, hackathons, and NEP 2020 projects.",
                    "sequence": 2,
                    "videos": [
                        {"title": "Video 3 — NEP 2020 Aligned STEM Modules", "description": "Integrating tinkering into school academic calendars.", "duration": "13:20", "sequence": 1},
                        {"title": "Video 4 — Mentoring Student Innovation Projects", "description": "Guiding students from ideation to competition prototypes.", "duration": "18:00", "sequence": 2}
                    ]
                }
            ]
        }
    ]

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    for course_data in initial_courses:
        cur.execute("SELECT id FROM courses WHERE slug = %s", (course_data["slug"],))
        existing = cur.fetchone()
        if not existing:
            if get_db_type() == "postgres":
                cur.execute(
                    """
                    INSERT INTO courses (title, slug, description, image, level, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                    RETURNING id
                    """,
                    (
                        course_data["title"],
                        course_data["slug"],
                        course_data["description"],
                        course_data["image"],
                        course_data["level"],
                        now_str,
                        now_str,
                    ),
                )
                course_id = cur.fetchone()["id"]
            else:
                cur.execute(
                    """
                    INSERT INTO courses (title, slug, description, image, level, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                    """,
                    (
                        course_data["title"],
                        course_data["slug"],
                        course_data["description"],
                        course_data["image"],
                        course_data["level"],
                        now_str,
                        now_str,
                    ),
                )
                course_id = cur.lastrowid
            
            for mod_data in course_data["modules"]:
                if get_db_type() == "postgres":
                    cur.execute(
                        """
                        INSERT INTO modules (course_id, title, description, sequence, is_active, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, 1, %s, %s)
                        RETURNING id
                        """,
                        (
                            course_id,
                            mod_data["title"],
                            mod_data["description"],
                            mod_data["sequence"],
                            now_str,
                            now_str,
                        ),
                    )
                    module_id = cur.fetchone()["id"]
                else:
                    cur.execute(
                        """
                        INSERT INTO modules (course_id, title, description, sequence, is_active, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, 1, %s, %s)
                        """,
                        (
                            course_id,
                            mod_data["title"],
                            mod_data["description"],
                            mod_data["sequence"],
                            now_str,
                            now_str,
                        ),
                    )
                    module_id = cur.lastrowid
                
                for vid_data in mod_data.get("videos", []):
                    cur.execute(
                        """
                        INSERT INTO course_videos (module_id, title, description, sequence, duration, youtube_video_id, is_active, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, NULL, 1, %s, %s)
                        """,
                        (
                            module_id,
                            vid_data["title"],
                            vid_data["description"],
                            vid_data["sequence"],
                            vid_data["duration"],
                            now_str,
                            now_str,
                        ),
                    )
            conn.commit()


def extract_youtube_id(input_str):
    """
    Sanitize and extract an 11-character YouTube Video ID from input.
    Handles raw IDs or YouTube URLs (watch?v=..., youtu.be/..., embed/...).
    Returns None if input is empty or invalid.
    """
    if not input_str:
        return None

    cleaned = input_str.strip()
    if not cleaned:
        return None

    # Standard 11-character YouTube video ID
    if re.match(r"^[a-zA-Z0-9_-]{11}$", cleaned):
        return cleaned

    # URL patterns
    patterns = [
        r"(?:v=|\/embed\/|\/v\/|\/vi\/|\/e\/|youtu\.be\/|\/shorts\/)([a-zA-Z0-9_-]{11})",
    ]

    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if match:
            return match.group(1)

    return None


def parse_duration_seconds(duration_str):
    """Parse duration string like '08:30' or '01:15:00' or float string to seconds float."""
    if not duration_str:
        return 600.0

    val = str(duration_str).strip()
    if not val:
        return 600.0

    parts = val.split(":")
    try:
        if len(parts) == 1:
            return max(1.0, float(parts[0]))
        elif len(parts) == 2:
            mins = float(parts[0])
            secs = float(parts[1])
            return max(1.0, mins * 60.0 + secs)
        elif len(parts) == 3:
            hrs = float(parts[0])
            mins = float(parts[1])
            secs = float(parts[2])
            return max(1.0, hrs * 3600.0 + mins * 60.0 + secs)
    except ValueError:
        pass

    return 600.0


def calculate_course_completion(user_id, course_id):
    """
    Calculate verified course completion percentage server-side.
    Formula: (sum of verified watched seconds for course active videos / sum of course active videos total duration) * 100.0
    Returns tuple: (completion_percentage, watched_seconds, total_duration_seconds)
    """
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT v.id, v.duration
        FROM course_videos v
        JOIN modules m ON v.module_id = m.id
        WHERE m.course_id = %s AND v.is_active = 1 AND m.is_active = 1
        """,
        (course_id,),
    )
    videos = cur.fetchall()

    if not videos:
        cur.close()
        conn.close()
        return (0.0, 0.0, 0.0)

    total_course_duration = 0.0
    video_durations = {}
    for v in videos:
        dur = parse_duration_seconds(v["duration"])
        video_durations[v["id"]] = dur
        total_course_duration += dur

    if total_course_duration <= 0:
        cur.close()
        conn.close()
        return (0.0, 0.0, 0.0)

    video_ids = list(video_durations.keys())
    if video_ids:
        placeholders = ",".join(str(v_id) for v_id in video_ids)
        cur.execute(
            f"""
            SELECT video_id, watched_seconds, completed, duration_seconds
            FROM video_progress
            WHERE user_id = %s AND video_id IN ({placeholders})
            """,
            (user_id,),
        )
        progress_rows = cur.fetchall()
    else:
        progress_rows = []
    cur.close()
    conn.close()

    total_watched_seconds = 0.0
    for p in progress_rows:
        vid_id = p["video_id"]
        target_dur = video_durations.get(vid_id, 600.0)

        if p["completed"]:
            total_watched_seconds += target_dur
        else:
            total_watched_seconds += min(float(p["watched_seconds"] or 0.0), target_dur)

    completion_pct = min(100.0, (total_watched_seconds / total_course_duration) * 100.0)
    return (completion_pct, total_watched_seconds, total_course_duration)


def generate_unique_certificate_id(cur):
    """Generate unique AIR-2026-XXXXXXXX certificate ID."""
    current_year = datetime.utcnow().strftime("%Y")
    for _ in range(100):
        rand_part = secrets.token_hex(4).upper()
        cert_id = f"AIR-{current_year}-{rand_part}"
        cur.execute("SELECT id FROM certificates WHERE certificate_id = %s", (cert_id,))
        if not cur.fetchone():
            return cert_id
    return f"AIR-{current_year}-{secrets.token_hex(6).upper()}"


def generate_certificate_pdf(certificate):
    """
    Generate a professional server-side PDF certificate using reportlab.
    Returns BytesIO buffer.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(letter))
    width, height = landscape(letter)

    # Background / Outer Decorative Borders
    c.setStrokeColor(colors.HexColor("#0F172A"))
    c.setLineWidth(4)
    c.rect(20, 20, width - 40, height - 40)

    c.setStrokeColor(colors.HexColor("#2563EB"))
    c.setLineWidth(1.5)
    c.rect(26, 26, width - 52, height - 52)

    c.setStrokeColor(colors.HexColor("#CBD5E1"))
    c.setLineWidth(0.5)
    c.rect(30, 30, width - 60, height - 60)

    # Header - Brand Title
    c.setFont("Helvetica-Bold", 32)
    c.setFillColor(colors.HexColor("#0F172A"))
    c.drawCentredString(width / 2.0, height - 85, "AIRODRONE")

    c.setFont("Helvetica", 12)
    c.setFillColor(colors.HexColor("#2563EB"))
    c.drawCentredString(width / 2.0, height - 105, "FUTURE-READY STEM & INNOVATION LEARNING")

    # Decorative Line
    c.setStrokeColor(colors.HexColor("#2563EB"))
    c.setLineWidth(2)
    c.line(width / 2.0 - 100, height - 118, width / 2.0 + 100, height - 118)

    # Certificate Title
    c.setFont("Helvetica-Bold", 26)
    c.setFillColor(colors.HexColor("#0F172A"))
    c.drawCentredString(width / 2.0, height - 160, "CERTIFICATE OF COMPLETION")

    # Presentation text
    c.setFont("Helvetica", 14)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawCentredString(width / 2.0, height - 200, "This certificate is proudly presented to")

    # Student Name
    c.setFont("Helvetica-Bold", 28)
    c.setFillColor(colors.HexColor("#1E293B"))
    c.drawCentredString(width / 2.0, height - 240, str(certificate["student_name"]).upper())

    # Name underline
    c.setStrokeColor(colors.HexColor("#94A3B8"))
    c.setLineWidth(1)
    c.line(width / 2.0 - 180, height - 248, width / 2.0 + 180, height - 248)

    # Course Text
    c.setFont("Helvetica", 14)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawCentredString(width / 2.0, height - 280, "for successfully completing the STEM course")

    # Course Name
    c.setFont("Helvetica-Bold", 22)
    c.setFillColor(colors.HexColor("#2563EB"))
    c.drawCentredString(width / 2.0, height - 315, str(certificate["course_name"]))

    # Completion Stats
    completion_text = f"Verified Course Completion: {certificate['completion_percentage']:.1f}%"
    c.setFont("Helvetica-Bold", 13)
    c.setFillColor(colors.HexColor("#16A34A"))
    c.drawCentredString(width / 2.0, height - 345, completion_text)

    # Footer Metadata - Certificate ID & Issue Date
    c.setFont("Helvetica", 10)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawString(60, 90, f"Certificate ID: {certificate['certificate_id']}")
    c.drawString(60, 72, f"Issue Date: {certificate['issued_at']}")
    c.drawString(60, 54, f"Verification: /verify-certificate/{certificate['certificate_id']}")

    # Authorized Signature Line
    c.setStrokeColor(colors.HexColor("#475569"))
    c.setLineWidth(1)
    c.line(width - 240, 90, width - 60, 90)

    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(colors.HexColor("#0F172A"))
    c.drawCentredString(width - 150, 74, "Airodrone Academic Board")

    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#64748B"))
    c.drawCentredString(width - 150, 58, "Authorized Signatory")

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer


# ---------- Decorators ----------

def admin_required(f):
    """Decorator to require admin role."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin():
            flash("Access denied. Admin privileges required.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated_function


# ---------- Service content ----------

SERVICES = [
    {
        "id": 1,
        "slug": "artificial-intelligence",
        "name": "Artificial Intelligence",
        "icon": "ai",
        "short_description": "Hands-on AI learning with real-world projects, tailored for students and innovators.",
    },
    {
        "id": 2,
        "slug": "robotics",
        "name": "Robotics",
        "icon": "robotics",
        "short_description": "Design, build, and program robots to solve practical problems and compete globally.",
    },
    {
        "id": 3,
        "slug": "coding-programming",
        "name": "Coding & Programming",
        "icon": "code",
        "short_description": "Foundational to advanced coding programs in multiple languages for all age groups.",
    },
    {
        "id": 4,
        "slug": "electronics-designing",
        "name": "Electronics & Designing",
        "icon": "electronics",
        "short_description": "Learn circuit design, sensors, and embedded systems for innovation and prototyping.",
    },
    {
        "id": 5,
        "slug": "drone-technology",
        "name": "Drone Technology",
        "icon": "drone",
        "short_description": "Explore the future of aerial technology with safe, structured drone education.",
    },
    {
        "id": 6,
        "slug": "web-designing",
        "name": "Web Designing",
        "icon": "web",
        "short_description": "Create modern, responsive websites with strong fundamentals in design and UX.",
    },
    {
        "id": 7,
        "slug": "atl-lab-setup-training",
        "name": "ATL Lab Setup & Training",
        "icon": "lab",
        "short_description": "End-to-end Atal Tinkering Lab setup, mentoring, and capacity building for schools.",
    },
    {
        "id": 8,
        "slug": "competitions-workshops",
        "name": "Competitions & Workshops",
        "icon": "workshop",
        "short_description": "High-impact competitions, bootcamps, and workshops that inspire innovation.",
    },
]


SERVICE_DETAILS = {
    "artificial-intelligence": {
        "title": "Artificial Intelligence",
        "hero_subtitle": "Build intelligent solutions using data, algorithms, and creativity.",
        "overview": (
            "Our Artificial Intelligence program introduces learners to core AI concepts including "
            "machine learning, neural networks, computer vision, and natural language processing. "
            "Through guided projects and real-world case studies, students learn how intelligent "
            "systems are designed, trained, and deployed across industries."
        ),
        "key_benefits": [
            "Strong conceptual foundation in AI and machine learning.",
            "Hands-on mini-projects such as image classification and chatbots.",
            "Exposure to ethical AI, responsible innovation, and real-world applications.",
            "Curriculum aligned with modern STEM and NEP 2020 guidelines.",
        ],
        "who_for": [
            "Schools and ATL labs introducing AI for the first time.",
            "Students (Grade 6–12) interested in next-generation technologies.",
            "Teachers looking to integrate AI into classroom projects.",
            "Institutions planning AI-focused innovation cells or clubs.",
        ],
    },
    "robotics": {
        "title": "Robotics",
        "hero_subtitle": "From ideas to fully functional robots that move, sense, and react.",
        "overview": (
            "Our Robotics program blends mechanical design, electronics, and programming to help "
            "students build and program robots for real-world tasks. Learners work with motors, "
            "sensors, controllers, and structured problem statements to understand how autonomous "
            "systems are built and controlled."
        ),
        "key_benefits": [
            "Project-based learning using industry-grade robotics kits.",
            "Step-by-step learning path from basic movement to autonomous navigation.",
            "Development of logical thinking, teamwork, and problem-solving skills.",
            "Preparation for national and international robotics competitions.",
        ],
        "who_for": [
            "Schools, colleges, and ATL labs setting up or upgrading robotics infrastructure.",
            "Students keen on mechatronics, automation, and engineering.",
            "STEM coordinators and mentors guiding robotics clubs.",
            "Institutions preparing for robotics Olympiads and hackathons.",
        ],
    },
    "coding-programming": {
        "title": "Coding & Programming",
        "hero_subtitle": "Build strong coding foundations that unlock future-ready careers.",
        "overview": (
            "Our Coding & Programming track is structured from absolute beginner to advanced levels. "
            "Students learn computational thinking, algorithms, and programming constructs using "
            "languages such as Scratch, Python, and JavaScript. The program emphasizes writing clean, "
            "logical code and applying it to meaningful projects."
        ),
        "key_benefits": [
            "Age-appropriate curriculum from visual to text-based programming.",
            "Focus on logic building, algorithmic thinking, and debugging.",
            "Project-oriented learning with games, apps, and automation scripts.",
            "Strong foundation for future careers in software and data science.",
        ],
        "who_for": [
            "Schools implementing structured coding programs under NEP 2020.",
            "Students in Grades 4–12 exploring software and app development.",
            "Teachers looking for turnkey programming modules and resources.",
            "Parents seeking structured after-school coding programs.",
        ],
    },
    "electronics-designing": {
        "title": "Electronics & Designing",
        "hero_subtitle": "Understand the building blocks of modern electronic systems.",
        "overview": (
            "Our Electronics & Designing module focuses on core electronics concepts such as voltage, "
            "current, sensors, actuators, and microcontrollers. Learners design and prototype real "
            "circuits for automation, monitoring, and IoT applications using safe, student-friendly "
            "hardware platforms."
        ),
        "key_benefits": [
            "Fundamental understanding of circuits and electronic components.",
            "Hands-on experience with sensors, microcontrollers, and prototyping boards.",
            "Integration of electronics with coding and mechanical systems.",
            "Encourages innovation through custom project design and tinkering.",
        ],
        "who_for": [
            "ATL and innovation labs focusing on hardware-centric projects.",
            "Students interested in electronics, embedded systems, and IoT.",
            "Teachers facilitating hardware and tinker-based learning.",
            "Institutions building capacity for hardware startup ecosystems.",
        ],
    },
    "drone-technology": {
        "title": "Drone Technology",
        "hero_subtitle": "Discover the science and regulations behind unmanned aerial vehicles.",
        "overview": (
            "Our Drone Technology program introduces students to the fundamentals of aerodynamics, "
            "flight control, safety, and regulations related to drones. Participants learn how drones "
            "are built, configured, and used in fields like agriculture, surveying, security, and media."
        ),
        "key_benefits": [
            "Structured curriculum combining theory, simulation, and practical demos.",
            "Focus on safety, compliance, and responsible usage of drones.",
            "Awareness of emerging drone-based career and business opportunities.",
            "Option to integrate drone projects into school exhibitions and fairs.",
        ],
        "who_for": [
            "Institutions exploring drone labs and aerial technology exposure.",
            "Students excited about aviation, aerospace, and emerging tech.",
            "ATL and STEM coordinators planning theme-based innovation modules.",
            "Clubs and communities hosting drone-focused events and camps.",
        ],
    },
    "web-designing": {
        "title": "Web Designing",
        "hero_subtitle": "Design and build beautiful, responsive websites from the ground up.",
        "overview": (
            "Our Web Designing program teaches the complete journey from wireframes to working "
            "websites. Students learn HTML, CSS, basic JavaScript, and user experience principles, "
            "with a strong focus on clean structure, accessibility, and responsive layouts."
        ),
        "key_benefits": [
            "End-to-end understanding of how modern websites are planned and built.",
            "Practical skills in layout, typography, color, and responsive design.",
            "Portfolio-ready projects such as personal sites and landing pages.",
            "Foundation for further learning in full-stack development and UI/UX.",
        ],
        "who_for": [
            "Students interested in design, front-end development, and digital presence.",
            "Schools introducing web design modules in ICT or computer science.",
            "Educators who want ready-to-use website projects for classrooms.",
            "Entrepreneurs and clubs needing simple, self-managed web pages.",
        ],
    },
    "atl-lab-setup-training": {
        "title": "ATL Lab Setup & Training",
        "hero_subtitle": "End-to-end Atal Tinkering Lab implementation and educator enablement.",
        "overview": (
            "We provide comprehensive support to set up, operationalize, and scale Atal Tinkering Labs "
            "in alignment with NITI Aayog guidelines. From infrastructure planning and equipment "
            "selection to hands-on mentor training and activity roadmaps, we ensure your ATL becomes "
            "a vibrant hub of innovation on campus."
        ),
        "key_benefits": [
            "Turnkey ATL setup support including planning, procurement, and layout.",
            "Structured training programs for teachers, mentors, and coordinators.",
            "Year-round activity calendars, project ideas, and competition readiness.",
            "Compliance with government norms and best practices in ATL management.",
        ],
        "who_for": [
            "Schools approved for ATL grants and planning their lab setup.",
            "Existing ATL schools looking to re-energize lab usage.",
            "School management teams and ATL in‑charge coordinators.",
            "NGOs and partners supporting ATL implementation at scale.",
        ],
    },
    "competitions-workshops": {
        "title": "Competitions & Workshops",
        "hero_subtitle": "Short-term, high-impact programs that ignite curiosity and innovation.",
        "overview": (
            "We design and deliver themed competitions, hackathons, and workshops across domains such "
            "as AI, robotics, coding, design thinking, and entrepreneurship. These programs are "
            "structured to be engaging, time-bound, and outcome-driven, helping students showcase and "
            "elevate their skills in a collaborative environment."
        ),
        "key_benefits": [
            "Ready-to-run event formats with problem statements and evaluation rubrics.",
            "Engaging workshops that balance theory, demonstration, and hands-on practice.",
            "Opportunities for students to build portfolios, win awards, and gain confidence.",
            "Customizable formats for school events, festivals, and corporate CSR programs.",
        ],
        "who_for": [
            "Schools planning annual tech fests, exhibitions, or innovation days.",
            "Colleges and training institutes hosting themed hackathons.",
            "Corporates and NGOs running STEM outreach or CSR programs.",
            "ATL and STEM coordinators looking for curated competition formats.",
        ],
    },
}


def find_service_by_slug(slug):
    """Return the base service dictionary for a given slug."""
    return next((s for s in SERVICES if s["slug"] == slug), None)


# ---------- Public Routes ----------

@app.route("/health")
def health_check():
    """Simple production health check endpoint for monitoring."""
    return jsonify({"status": "ok"}), 200


@app.route("/")
def home():
    return render_template("home.html", active_page="home", services=SERVICES[:4])


@app.route("/about")
def about():
    return render_template("about.html", active_page="about")


@app.route("/services")
def services():
    return render_template("services.html", active_page="services", services=SERVICES)


@app.route("/services/<slug>")
def service_detail(slug):
    base_service = find_service_by_slug(slug)
    detail = SERVICE_DETAILS.get(slug)
    if not base_service or not detail:
        return render_template("service-detail.html", active_page="services", not_found=True), 404

    return render_template(
        "service-detail.html",
        active_page="services",
        service=base_service,
        detail=detail,
    )


@app.route("/contact", methods=["GET", "POST"])
def contact():
    error = None
    success = False

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()

        if not name or not email or not message:
            error = "Please fill in your name, email, and message."
        else:
            created_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO contacts (name, email, phone, subject, message, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (name, email, phone, subject, message, created_at),
                )
                conn.commit()
                cur.close()
                conn.close()
                success = True
            except Exception:
                error = "Something went wrong while submitting the form. Please try again."

    return render_template(
        "contact.html",
        active_page="contact",
        error=error,
        success=success,
    )


# ---------- Course Routes ----------

@app.route("/courses")
def courses():
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            """
            SELECT c.id, c.title, c.slug, c.short_description, c.description, c.image, c.level, c.estimated_duration, c.created_at,
                   COUNT(DISTINCT m.id) AS total_modules,
                   COUNT(DISTINCT v.id) AS total_videos
            FROM courses c
            LEFT JOIN modules m ON m.course_id = c.id AND m.is_active = 1
            LEFT JOIN course_videos v ON v.module_id = m.id AND v.is_active = 1
            WHERE c.is_active = 1
            GROUP BY c.id, c.title, c.slug, c.short_description, c.description, c.image, c.level, c.estimated_duration, c.created_at
            ORDER BY c.id ASC
            """
        )
        courses_list = cur.fetchall()

        if current_user.is_authenticated:
            for c in courses_list:
                c["is_enrolled"] = can_access_course(current_user.id, c["id"])
                if c["is_enrolled"] and not current_user.is_admin():
                    comp_pct, _, _ = calculate_course_completion(current_user.id, c["id"])
                    c["progress_pct"] = round(comp_pct, 1)
                    cur.execute("SELECT certificate_id FROM certificates WHERE user_id = %s AND course_id = %s", (current_user.id, c["id"]))
                    cert = cur.fetchone()
                    c["has_certificate"] = bool(cert)
                else:
                    c["progress_pct"] = 0.0
                    c["has_certificate"] = False
        else:
            for c in courses_list:
                c["is_enrolled"] = False
                c["progress_pct"] = 0.0
                c["has_certificate"] = False

        cur.close()
        conn.close()
    except Exception as e:
        app.logger.error(f"Error fetching courses: {e}")
        courses_list = []

    return render_template("courses.html", active_page="courses", courses=courses_list)


@app.route("/courses/<slug>")
def course_detail(slug):
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        
        # Fetch course detail
        cur.execute(
            """
            SELECT id, title, slug, short_description, description, image, level,
                   estimated_duration, learning_outcomes, course_benefits, certificate_description, created_at
            FROM courses
            WHERE slug = %s AND is_active = 1
            """,
            (slug,),
        )
        course = cur.fetchone()
        
        if not course:
            cur.close()
            conn.close()
            return render_template("course_detail.html", active_page="courses", not_found=True), 404
            
        # Count aggregate modules and videos
        cur.execute(
            """
            SELECT COUNT(DISTINCT m.id) AS total_modules,
                   COUNT(DISTINCT v.id) AS total_videos
            FROM courses c
            LEFT JOIN modules m ON m.course_id = c.id AND m.is_active = 1
            LEFT JOIN course_videos v ON v.module_id = m.id AND v.is_active = 1
            WHERE c.id = %s
            """,
            (course["id"],),
        )
        counts = cur.fetchone()
        total_modules = counts["total_modules"] if counts else 0
        total_videos = counts["total_videos"] if counts else 0

        # Parse bullet lists for outcomes & benefits
        raw_outcomes = course.get("learning_outcomes") or ""
        learning_outcomes_list = [line.strip() for line in raw_outcomes.splitlines() if line.strip()]

        raw_benefits = course.get("course_benefits") or ""
        course_benefits_list = [line.strip() for line in raw_benefits.splitlines() if line.strip()]

        is_enrolled = False
        if current_user.is_authenticated:
            is_enrolled = can_access_course(current_user.id, course["id"])

        modules = []
        user_progress_map = {}
        course_completion_pct = 0.0
        certificate_info = None
        completed_videos_count = 0
        has_any_progress_in_course = False
        next_video_id = None

        if is_enrolled:
            # Fetch full curriculum details ONLY for enrolled students or admin
            cur.execute(
                """
                SELECT id, title, description, sequence
                FROM modules
                WHERE course_id = %s AND is_active = 1
                ORDER BY sequence ASC, id ASC
                """,
                (course["id"],),
            )
            modules = cur.fetchall()

            if current_user.is_authenticated:
                cur.execute(
                    """
                    SELECT video_id, watched_seconds, completion_percentage, completed
                    FROM video_progress
                    WHERE user_id = %s
                    """,
                    (current_user.id,),
                )
                prog_rows = cur.fetchall()
                user_progress_map = {p["video_id"]: p for p in prog_rows}

                completion_pct, _, _ = calculate_course_completion(current_user.id, course["id"])
                course_completion_pct = round(completion_pct, 1)

                cur.execute(
                    """
                    SELECT certificate_id, completion_percentage, issued_at
                    FROM certificates
                    WHERE user_id = %s AND course_id = %s
                    """,
                    (current_user.id, course["id"]),
                )
                certificate_info = cur.fetchone()

            first_uncompleted_video_id = None
            first_video_id = None

            for mod in modules:
                cur.execute(
                    """
                    SELECT id, title, description, sequence, duration, youtube_video_id
                    FROM course_videos
                    WHERE module_id = %s AND is_active = 1
                    ORDER BY sequence ASC, id ASC
                    """,
                    (mod["id"],),
                )
                vids = cur.fetchall()
                for v in vids:
                    if first_video_id is None:
                        first_video_id = v["id"]
                    prog = user_progress_map.get(v["id"])
                    v["progress"] = prog
                    if prog and (prog.get("watched_seconds", 0) > 0 or prog.get("completed")):
                        has_any_progress_in_course = True
                    if prog and prog.get("completed"):
                        completed_videos_count += 1
                    elif first_uncompleted_video_id is None:
                        first_uncompleted_video_id = v["id"]

                mod["videos"] = vids

                # Fetch active module quiz
                cur.execute(
                    """
                    SELECT q.id, q.title, q.description, q.passing_score, q.max_attempts, q.is_active,
                           COUNT(qq.id) AS total_questions
                    FROM quizzes q
                    LEFT JOIN quiz_questions qq ON qq.quiz_id = q.id AND qq.is_active = 1
                    WHERE q.module_id = %s AND q.is_active = 1
                    GROUP BY q.id, q.title, q.description, q.passing_score, q.max_attempts, q.is_active
                    """,
                    (mod["id"],),
                )
                mod_quiz = cur.fetchone()
                mod["quiz"] = mod_quiz

                if mod_quiz and current_user.is_authenticated:
                    cur.execute(
                        """
                        SELECT id, score, passed, attempt_number, is_invalidated
                        FROM quiz_attempts
                        WHERE quiz_id = %s AND user_id = %s AND submitted_at IS NOT NULL
                        ORDER BY attempt_number DESC
                        """,
                        (mod_quiz["id"], current_user.id),
                    )
                    attempts = cur.fetchall()
                    attempts_used = len(attempts)
                    attempts_remaining = max(0, mod_quiz["max_attempts"] - attempts_used)
                    has_passed = any(a["passed"] for a in attempts)
                    highest_score = max([a["score"] for a in attempts], default=0) if attempts else 0
                    latest_attempt_id = attempts[0]["id"] if attempts else None
                    mod["quiz_status"] = {
                        "passed": has_passed,
                        "attempts_used": attempts_used,
                        "attempts_remaining": attempts_remaining,
                        "highest_score": highest_score,
                        "latest_attempt_id": latest_attempt_id
                    }

            next_video_id = first_uncompleted_video_id or first_video_id

        cur.close()
        conn.close()
        
        return render_template(
            "course_detail.html",
            active_page="courses",
            course=course,
            is_enrolled=is_enrolled,
            modules=modules,
            total_modules=total_modules,
            total_videos=total_videos,
            learning_outcomes_list=learning_outcomes_list,
            course_benefits_list=course_benefits_list,
            completed_videos_count=completed_videos_count,
            has_any_progress_in_course=has_any_progress_in_course,
            next_video_id=next_video_id,
            course_completion_pct=course_completion_pct,
            certificate_info=certificate_info,
        )
    except Exception as e:
        app.logger.error(f"Error fetching course detail for {slug}: {e}")
        return render_template("course_detail.html", active_page="courses", not_found=True), 404


@app.route("/courses/<course_slug>/video/<int:video_id>")
@login_required
def video_player(course_slug, video_id):
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        
        # Verify course exists
        cur.execute(
            "SELECT id, title, slug, level, description, image FROM courses WHERE slug = %s AND is_active = 1",
            (course_slug,)
        )
        course = cur.fetchone()
        if not course:
            cur.close()
            conn.close()
            return render_template("video_player.html", active_page="courses", not_found=True), 404

        if not can_access_course(current_user.id, course["id"]):
            cur.close()
            conn.close()
            return render_template("course_access_denied.html", active_page="courses"), 403

        # Verify video and relationship
        cur.execute(
            """
            SELECT v.id, v.title, v.description, v.sequence, v.duration, v.youtube_video_id, v.is_active,
                   m.id AS module_id, m.title AS module_title, m.sequence AS module_sequence
            FROM course_videos v
            JOIN modules m ON v.module_id = m.id
            WHERE v.id = %s AND m.course_id = %s AND v.is_active = 1 AND m.is_active = 1
            """,
            (video_id, course["id"]),
        )
        video = cur.fetchone()

        if not video:
            cur.close()
            conn.close()
            return render_template("video_player.html", active_page="courses", not_found=True), 404

        # Fetch modules and videos for sidebar playlist navigation
        cur.execute(
            """
            SELECT id, title, description, sequence
            FROM modules
            WHERE course_id = %s AND is_active = 1
            ORDER BY sequence ASC, id ASC
            """,
            (course["id"],),
        )
        modules = cur.fetchall()

        all_videos_list = []
        for mod in modules:
            cur.execute(
                """
                SELECT id, title, description, sequence, duration, youtube_video_id, is_active
                FROM course_videos
                WHERE module_id = %s AND is_active = 1
                ORDER BY sequence ASC, id ASC
                """,
                (mod["id"],),
            )
            mod["videos"] = cur.fetchall()
            for v in mod["videos"]:
                v["module_title"] = mod["title"]
                all_videos_list.append(v)

        prev_video = None
        next_video = None
        current_idx = -1
        for idx, v in enumerate(all_videos_list):
            if v["id"] == video["id"]:
                current_idx = idx
                break
        
        if current_idx > 0:
            prev_video = all_videos_list[current_idx - 1]
        if current_idx >= 0 and current_idx < len(all_videos_list) - 1:
            next_video = all_videos_list[current_idx + 1]

        # Fetch user's progress for all videos in course for sidebar badges
        cur.execute(
            """
            SELECT video_id, watched_seconds, completion_percentage, completed
            FROM video_progress
            WHERE user_id = %s
            """,
            (current_user.id,),
        )
        prog_rows = cur.fetchall()
        user_progress_map = {p["video_id"]: p for p in prog_rows}

        for mod in modules:
            for v in mod["videos"]:
                v["progress"] = user_progress_map.get(v["id"])

        # Fetch user's progress for current video
        cur.execute(
            """
            SELECT watched_seconds, duration_seconds, completion_percentage, completed
            FROM video_progress
            WHERE user_id = %s AND video_id = %s
            """,
            (current_user.id, video_id),
        )
        video_prog = cur.fetchone()

        cur.close()
        conn.close()

        return render_template(
            "video_player.html",
            active_page="courses",
            course=course,
            video=video,
            modules=modules,
            prev_video=prev_video,
            next_video=next_video,
            video_progress=video_prog,
        )
    except Exception as e:
        app.logger.error(f"Error loading video player for {course_slug}/video/{video_id}: {e}")
        return render_template("video_player.html", active_page="courses", not_found=True), 404


# ---------- Progress Tracking API Routes ----------

@app.route("/courses/video/<int:video_id>/progress", methods=["GET"])
@login_required
def get_video_progress(video_id):
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            """
            SELECT v.id, m.course_id
            FROM course_videos v
            JOIN modules m ON v.module_id = m.id
            WHERE v.id = %s AND v.is_active = 1 AND m.is_active = 1
            """,
            (video_id,)
        )
        video = cur.fetchone()
        if not video:
            cur.close()
            conn.close()
            return jsonify({"success": False, "error": "Video not found"}), 404

        if not can_access_course(current_user.id, video["course_id"]):
            cur.close()
            conn.close()
            return jsonify({"success": False, "error": "Course access required"}), 403

        cur.execute(
            """
            SELECT user_id, video_id, watched_seconds, duration_seconds, completion_percentage, completed, last_watched_at, completed_at
            FROM video_progress
            WHERE user_id = %s AND video_id = %s
            """,
            (current_user.id, video_id),
        )
        progress = cur.fetchone()
        cur.close()
        conn.close()

        if not progress:
            progress = {
                "user_id": current_user.id,
                "video_id": video_id,
                "watched_seconds": 0.0,
                "duration_seconds": 0.0,
                "completion_percentage": 0.0,
                "completed": False,
                "completed_at": None,
            }

        return jsonify({"success": True, "progress": progress})
    except Exception as e:
        app.logger.error(f"Error fetching progress for video {video_id}: {e}")
        return jsonify({"success": False, "error": "Internal error"}), 500


@app.route("/courses/video/<int:video_id>/progress", methods=["POST"])
@login_required
def update_video_progress(video_id):
    try:
        data = request.get_json(silent=True) or {}
        client_watched = float(data.get("watched_seconds", 0.0) or 0.0)
        client_duration = float(data.get("duration_seconds", 0.0) or 0.0)
        event_name = str(data.get("event", "timeupdate"))

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            """
            SELECT v.id, m.course_id
            FROM course_videos v
            JOIN modules m ON v.module_id = m.id
            WHERE v.id = %s AND v.is_active = 1 AND m.is_active = 1
            """,
            (video_id,)
        )
        video = cur.fetchone()
        if not video:
            cur.close()
            conn.close()
            return jsonify({"success": False, "error": "Video not found"}), 404

        if not can_access_course(current_user.id, video["course_id"]):
            cur.close()
            conn.close()
            return jsonify({"success": False, "error": "Course access required"}), 403

        now_dt = datetime.utcnow()
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        cur.execute(
            """
            SELECT id, watched_seconds, duration_seconds, completion_percentage, completed, last_watched_at
            FROM video_progress
            WHERE user_id = %s AND video_id = %s
            """,
            (current_user.id, video_id),
        )
        existing = cur.fetchone()

        if existing:
            prev_watched = float(existing["watched_seconds"] or 0.0)
            already_completed = bool(existing["completed"])
            prev_last_watched_str = existing["last_watched_at"]

            if already_completed:
                # Video is ALREADY completed. Maintain completed status, allow position update for replay.
                new_watched = max(0.0, client_watched)
                cur.execute(
                    """
                    UPDATE video_progress
                    SET watched_seconds = %s, last_watched_at = %s, updated_at = %s
                    WHERE user_id = %s AND video_id = %s
                    """,
                    (new_watched, now_str, now_str, current_user.id, video_id),
                )
                conn.commit()
                cur.close()
                conn.close()
                return jsonify({
                    "success": True,
                    "progress": {
                        "watched_seconds": new_watched,
                        "completion_percentage": 100.0,
                        "completed": True,
                    }
                })

            # Uncompleted video: Strict server-side wall-clock validation
            try:
                prev_dt = datetime.strptime(prev_last_watched_str, "%Y-%m-%d %H:%M:%S")
                elapsed_wall_seconds = max(0.0, (now_dt - prev_dt).total_seconds())
            except Exception:
                elapsed_wall_seconds = 10.0

            # Cap maximum legitimate advancement per sync call (elapsed + 3s tolerance, max 15s per sync)
            max_allowed_delta = min(elapsed_wall_seconds + 3.0, 15.0)
            max_legitimate_position = prev_watched + max_allowed_delta

            if client_watched <= prev_watched:
                # Backward seeking or review: keep furthest legitimate position
                new_watched = prev_watched
            else:
                # Advancing forward: clamp to server-validated maximum position
                new_watched = min(client_watched, max_legitimate_position)

            duration = client_duration if client_duration > 0 else float(existing["duration_seconds"] or 0.0)
            calc_percentage = (new_watched / duration * 100.0) if duration > 0 else 0.0

            # Server-side completion condition check (>= 90% watched)
            is_newly_completed = calc_percentage >= 90.0 or (event_name == "ended" and calc_percentage >= 85.0)

            if is_newly_completed:
                final_percentage = 100.0
                final_watched = duration if duration > 0 else new_watched
                cur.execute(
                    """
                    UPDATE video_progress
                    SET watched_seconds = %s, duration_seconds = %s, completion_percentage = %s,
                        completed = TRUE, completed_at = %s, last_watched_at = %s, updated_at = %s
                    WHERE user_id = %s AND video_id = %s
                    """,
                    (final_watched, duration, final_percentage, now_str, now_str, now_str, current_user.id, video_id),
                )
                conn.commit()
                cur.close()
                conn.close()
                return jsonify({
                    "success": True,
                    "progress": {
                        "watched_seconds": final_watched,
                        "completion_percentage": 100.0,
                        "completed": True,
                    }
                })
            else:
                cur.execute(
                    """
                    UPDATE video_progress
                    SET watched_seconds = %s, duration_seconds = %s, completion_percentage = %s,
                        last_watched_at = %s, updated_at = %s
                    WHERE user_id = %s AND video_id = %s
                    """,
                    (new_watched, duration, calc_percentage, now_str, now_str, current_user.id, video_id),
                )
                conn.commit()
                cur.close()
                conn.close()
                return jsonify({
                    "success": True,
                    "progress": {
                        "watched_seconds": new_watched,
                        "completion_percentage": calc_percentage,
                        "completed": False,
                    }
                })

        else:
            # First progress record insertion for this user & video
            # Cannot jump higher than 15.0s on first sync
            new_watched = min(client_watched, 15.0)
            duration = client_duration if client_duration > 0 else 0.0
            calc_percentage = (new_watched / duration * 100.0) if duration > 0 else 0.0
            is_completed = calc_percentage >= 90.0

            cur.execute(
                """
                INSERT INTO video_progress
                (user_id, video_id, watched_seconds, duration_seconds, completion_percentage, completed,
                 first_started_at, last_watched_at, completed_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    current_user.id,
                    video_id,
                    duration if is_completed else new_watched,
                    duration,
                    100.0 if is_completed else calc_percentage,
                    is_completed,
                    now_str,
                    now_str,
                    now_str if is_completed else None,
                    now_str,
                    now_str,
                ),
            )
            conn.commit()
            cur.close()
            conn.close()

            return jsonify({
                "success": True,
                "progress": {
                    "watched_seconds": duration if is_completed else new_watched,
                    "completion_percentage": 100.0 if is_completed else calc_percentage,
                    "completed": is_completed,
                }
            })

    except Exception as e:
        app.logger.error(f"Error updating video progress for video {video_id}: {e}")
        return jsonify({"success": False, "error": "Internal error"}), 500


# ---------- Certificate Routes ----------

@app.route("/courses/<course_slug>/certificate", methods=["POST"])
@login_required
def generate_course_certificate(course_slug):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        "SELECT id, title, slug FROM courses WHERE slug = %s AND is_active = 1",
        (course_slug,),
    )
    course = cur.fetchone()
    if not course:
        cur.close()
        conn.close()
        flash("Course not found.", "error")
        return redirect(url_for("courses"))

    # Calculate verified course completion percentage server-side
    completion_pct, watched_sec, total_sec = calculate_course_completion(current_user.id, course["id"])

    if completion_pct < 75.0:
        cur.close()
        conn.close()
        flash(f"Course progress is {completion_pct:.1f}%. Complete at least 75% of this course to earn your certificate.", "error")
        return redirect(url_for("course_detail", slug=course_slug))

    # Check if certificate already exists for user and course
    cur.execute(
        "SELECT certificate_id FROM certificates WHERE user_id = %s AND course_id = %s",
        (current_user.id, course["id"]),
    )
    existing_cert = cur.fetchone()

    if existing_cert:
        cur.close()
        conn.close()
        flash("Certificate already generated for this course.", "info")
        return redirect(url_for("view_certificate", certificate_id=existing_cert["certificate_id"]))

    # Generate new unique certificate ID
    cert_id = generate_unique_certificate_id(cur)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    issue_date_str = datetime.utcnow().strftime("%B %d, %Y")

    try:
        cur.execute(
            """
            INSERT INTO certificates
            (certificate_id, user_id, course_id, student_name, course_name, completion_percentage, issued_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                cert_id,
                current_user.id,
                course["id"],
                current_user.name,
                course["title"],
                round(completion_pct, 1),
                issue_date_str,
                now_str,
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        flash("Congratulations! Your Certificate of Completion has been issued.", "success")
        return redirect(url_for("view_certificate", certificate_id=cert_id))
    except Exception as e:
        conn.rollback()
        cur.execute(
            "SELECT certificate_id FROM certificates WHERE user_id = %s AND course_id = %s",
            (current_user.id, course["id"]),
        )
        existing = cur.fetchone()
        cur.close()
        conn.close()
        if existing:
            return redirect(url_for("view_certificate", certificate_id=existing["certificate_id"]))
        flash("Could not generate certificate. Please try again.", "error")
        return redirect(url_for("course_detail", slug=course_slug))


@app.route("/certificates")
@login_required
def student_certificates():
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT c.certificate_id, c.course_name, c.completion_percentage, c.issued_at, co.slug AS course_slug
        FROM certificates c
        LEFT JOIN courses co ON c.course_id = co.id
        WHERE c.user_id = %s
        ORDER BY c.id DESC
        """,
        (current_user.id,),
    )
    certificates_list = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("student_certificates.html", active_page="certificates", certificates=certificates_list)


@app.route("/certificates/<certificate_id>")
@login_required
def view_certificate(certificate_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT c.id, c.certificate_id, c.user_id, c.course_id, c.student_name, c.course_name,
               c.completion_percentage, c.issued_at, co.slug AS course_slug
        FROM certificates c
        LEFT JOIN courses co ON c.course_id = co.id
        WHERE c.certificate_id = %s
        """,
        (certificate_id,),
    )
    cert = cur.fetchone()
    cur.close()
    conn.close()

    if not cert:
        return render_template("view_certificate.html", active_page="certificates", not_found=True), 404

    # Security check: User can only view their own certificate (or admin)
    if cert["user_id"] != current_user.id and not current_user.is_admin():
        abort(403)

    return render_template("view_certificate.html", active_page="certificates", cert=cert)


@app.route("/certificates/<certificate_id>/download")
@login_required
def download_certificate_pdf(certificate_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT certificate_id, user_id, student_name, course_name, completion_percentage, issued_at
        FROM certificates
        WHERE certificate_id = %s
        """,
        (certificate_id,),
    )
    cert = cur.fetchone()
    cur.close()
    conn.close()

    if not cert:
        abort(404)

    # Security check: User can only download their own certificate (or admin)
    if cert["user_id"] != current_user.id and not current_user.is_admin():
        abort(403)

    pdf_buffer = generate_certificate_pdf(cert)
    filename = f"Airodrone_Certificate_{cert['certificate_id']}.pdf"

    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf"
    )


@app.route("/verify", methods=["GET", "POST"])
def verify_lookup():
    if request.method == "POST":
        cert_id = request.form.get("certificate_id", "").strip()
        if cert_id:
            return redirect(url_for("verify_certificate", certificate_id=cert_id))
    return render_template("verify_lookup.html", active_page="verify")


@app.route("/verify-certificate/<certificate_id>")
def verify_certificate(certificate_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT certificate_id, student_name, course_name, completion_percentage, issued_at
        FROM certificates
        WHERE certificate_id = %s
        """,
        (certificate_id,),
    )
    cert = cur.fetchone()
    cur.close()
    conn.close()

    return render_template("verify_certificate.html", active_page="verify", cert=cert)


# ---------- Authentication Routes ----------

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    flash("Public self-registration is disabled. Student accounts are created and managed by your Airodrone administrator.", "info")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            error = "Please enter both email and password."
        elif not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            error = "Please enter a valid email format."
        else:
            conn = get_db_connection()
            cur = get_db_cursor(conn)
            cur.execute(
                """
                SELECT id, name, email, password_hash, role, is_active
                FROM users
                WHERE email = %s
                """,
                (email,),
            )
            user = cur.fetchone()
            cur.close()
            conn.close()

            if not user:
                error = "Invalid email. No account found."
            elif not user["is_active"]:
                error = "Account is inactive."
            elif not check_password_hash(user["password_hash"], password):
                error = "Wrong password."
            else:
                user_obj = User(
                    id=user["id"],
                    name=user["name"],
                    email=user["email"],
                    role=user["role"],
                    active=bool(user["is_active"]),
                )

                remember = True if request.form.get("remember") else False
                login_user(user_obj, remember=remember)
                flash(f"Welcome back, {user['name']}!", "success")
                next_page = request.args.get("next")
                return redirect(next_page) if next_page else redirect(url_for("dashboard"))

    return render_template("login.html", error=error, active_page="login")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out successfully.", "success")
    return redirect(url_for("home"))


# ---------- Protected Routes ----------

@app.route("/dashboard")
@login_required
def dashboard():
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # Query latest watched video for student
        cur.execute(
            """
            SELECT vp.video_id, vp.watched_seconds, vp.completion_percentage,
                   cv.title AS video_title, c.title AS course_title, c.slug AS course_slug
            FROM video_progress vp
            JOIN course_videos cv ON vp.video_id = cv.id
            JOIN modules m ON cv.module_id = m.id
            JOIN courses c ON m.course_id = c.id
            WHERE vp.user_id = %s
            ORDER BY vp.updated_at DESC, vp.id DESC
            LIMIT 1
            """,
            (current_user.id,),
        )
        latest_progress = cur.fetchone()

        # Query certificate count for student
        cur.execute("SELECT COUNT(*) AS count FROM certificates WHERE user_id = %s", (current_user.id,))
        cert_count_row = cur.fetchone()
        cert_count = cert_count_row["count"] if cert_count_row else 0

        # Query assigned courses for student (or all active courses for admin)
        if current_user.is_admin():
            cur.execute("SELECT id, title, slug, level, image FROM courses WHERE is_active = 1 ORDER BY id ASC")
        else:
            cur.execute(
                """
                SELECT c.id, c.title, c.slug, c.level, c.image
                FROM courses c
                JOIN course_enrollments e ON e.course_id = c.id AND e.user_id = %s AND e.is_active = 1
                WHERE c.is_active = 1
                ORDER BY c.id ASC
                """,
                (current_user.id,)
            )
        assigned_courses = cur.fetchall()

        cur.close()
        conn.close()
    except Exception as e:
        app.logger.error(f"Error loading dashboard for user {current_user.id}: {e}")
        latest_progress = None
        cert_count = 0
        assigned_courses = []

    return render_template(
        "dashboard.html",
        active_page="dashboard",
        latest_progress=latest_progress,
        cert_count=cert_count,
        assigned_courses=assigned_courses,
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        father_name = request.form.get("father_name", "").strip()
        raw_phone = request.form.get("phone", "").strip()

        # Clean digits from raw phone
        clean_digits = re.sub(r"\D", "", raw_phone)
        if len(clean_digits) == 12 and clean_digits.startswith("91"):
            clean_digits = clean_digits[2:]

        if not name or len(name) < 2:
            error = "Please enter a valid full name (at least 2 characters)."
        elif not father_name or len(father_name) < 2:
            error = "Please enter a valid father's name (at least 2 characters)."
        elif not raw_phone or not re.match(r"^[6-9]\d{9}$", clean_digits):
            error = "Please enter a valid 10-digit Indian mobile number."
        else:
            normalized_phone = f"+91 {clean_digits[:5]} {clean_digits[5:]}"
            try:
                conn = get_db_connection()
                cur = get_db_cursor(conn)
                cur.execute(
                    """
                    UPDATE users
                    SET name = %s, father_name = %s, phone = %s
                    WHERE id = %s
                    """,
                    (name, father_name, normalized_phone, current_user.id),
                )
                conn.commit()
                cur.close()
                conn.close()

                current_user.name = name
                current_user.father_name = father_name
                current_user.phone = normalized_phone

                flash("Profile updated successfully!", "success")
                return redirect(url_for("profile"))
            except Exception as e:
                app.logger.error(f"Error updating profile for user {current_user.id}: {e}")
                error = "An error occurred while saving profile changes."

    return render_template("profile.html", active_page="dashboard", error=error)


# ---------- Admin Routes ----------

@app.route("/admin")
@admin_required
def admin():
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT id, name, email, phone, subject, message, created_at
        FROM contacts
        ORDER BY created_at DESC
        """
    )
    submissions = cur.fetchall()
    cur.close()
    conn.close()

    return render_template(
        "admin.html",
        active_page="admin",
        submissions=submissions,
    )


@app.route("/admin/contacts")
@admin_required
def admin_contacts():
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT id, name, email, phone, subject, message, created_at
        FROM contacts
        ORDER BY created_at DESC
        """
    )
    contacts = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("admin_contacts.html", active_page="admin", contacts=contacts)


@app.route("/admin/users")
@admin_required
def admin_users():
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT u.id, u.name, u.father_name, u.email, u.phone, u.role, u.is_active, u.created_at,
               COUNT(DISTINCT e.course_id) AS enrolled_count
        FROM users u
        LEFT JOIN course_enrollments e ON e.user_id = u.id AND e.is_active = 1
        GROUP BY u.id, u.name, u.father_name, u.email, u.phone, u.role, u.is_active, u.created_at
        ORDER BY u.created_at DESC, u.id DESC
        """
    )
    users = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("admin_users.html", active_page="admin", users=users)


@app.route("/admin/users/new", methods=["GET", "POST"])
@admin_required
def admin_user_new():
    error = None
    form_data = {}
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        father_name = request.form.get("father_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        is_active = 1 if request.form.get("is_active") else 0

        form_data = {
            "name": name,
            "father_name": father_name,
            "email": email,
            "phone": phone,
            "password": password,
            "is_active": is_active,
        }

        if not name or not email or not password:
            error = "Please fill in all required fields (Name, Email, Password)."
        elif not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            error = "Please enter a valid email address."
        elif len(password) < 6:
            error = "Temporary password must be at least 6 characters long."
        else:
            conn = get_db_connection()
            cur = get_db_cursor(conn)
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            existing = cur.fetchone()

            if existing:
                cur.close()
                conn.close()
                error = "Email already registered. Duplicate account creation is not allowed."
            else:
                password_hash = generate_password_hash(password)
                created_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    """
                    INSERT INTO users (name, father_name, email, phone, password_hash, role, is_active, created_at)
                    VALUES (%s, %s, %s, %s, %s, 'user', %s, %s)
                    """,
                    (name, father_name or None, email, phone or None, password_hash, is_active, created_at),
                )
                conn.commit()
                cur.close()
                conn.close()

                flash(f"Student account created successfully for {name} ({email}).", "success")
                return redirect(url_for("admin_users"))

    return render_template("admin_student_form.html", active_page="admin", edit_mode=False, error=error, form_data=form_data)


@app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_user_edit(user_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        "SELECT id, name, father_name, email, phone, role, is_active FROM users WHERE id = %s",
        (user_id,),
    )
    student = cur.fetchone()

    if not student:
        cur.close()
        conn.close()
        flash("Student account not found.", "error")
        return redirect(url_for("admin_users"))

    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        father_name = request.form.get("father_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        is_active = 1 if request.form.get("is_active") else 0

        if not name or not email:
            error = "Please fill in all required fields (Name, Email)."
        elif not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            error = "Please enter a valid email address."
        else:
            cur.execute("SELECT id FROM users WHERE email = %s AND id != %s", (email, user_id))
            existing = cur.fetchone()
            if existing:
                error = "Email address is already in use by another user."
            else:
                cur.execute(
                    """
                    UPDATE users
                    SET name = %s, father_name = %s, email = %s, phone = %s, is_active = %s
                    WHERE id = %s
                    """,
                    (name, father_name or None, email, phone or None, is_active, user_id),
                )
                conn.commit()
                cur.close()
                conn.close()
                flash(f"Student account updated for {name}.", "success")
                return redirect(url_for("admin_users"))

    cur.close()
    conn.close()
    return render_template("admin_student_form.html", active_page="admin", edit_mode=True, student=student, error=error)


@app.route("/admin/users/<int:user_id>/toggle-active", methods=["POST"])
@admin_required
def admin_user_toggle_active(user_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, name, role, is_active FROM users WHERE id = %s", (user_id,))
    student = cur.fetchone()

    if not student:
        cur.close()
        conn.close()
        flash("Student account not found.", "error")
        return redirect(url_for("admin_users"))

    if student["role"] == "admin":
        cur.close()
        conn.close()
        flash("Cannot deactivate an admin user account.", "error")
        return redirect(url_for("admin_users"))

    new_active = 0 if student["is_active"] else 1
    cur.execute("UPDATE users SET is_active = %s WHERE id = %s", (new_active, user_id))
    conn.commit()
    cur.close()
    conn.close()

    status_str = "activated" if new_active else "deactivated"
    flash(f"Account for {student['name']} has been {status_str}.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def admin_user_reset_password(user_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, name, email FROM users WHERE id = %s", (user_id,))
    student = cur.fetchone()

    if not student:
        cur.close()
        conn.close()
        flash("Student account not found.", "error")
        return redirect(url_for("admin_users"))

    temp_password = request.form.get("new_password", "").strip()
    if not temp_password:
        import secrets
        temp_password = "Airo" + secrets.token_hex(3)

    password_hash = generate_password_hash(temp_password)
    cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id))
    conn.commit()
    cur.close()
    conn.close()

    flash(
        f"Password reset successfully for {student['name']}. Temporary password: '{temp_password}'. Save this temporary password securely. It will not be shown again.",
        "info"
    )
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/courses")
@admin_required
def admin_user_courses(user_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, name, email, father_name, phone, is_active FROM users WHERE id = %s", (user_id,))
    student = cur.fetchone()

    if not student:
        cur.close()
        conn.close()
        flash("Student account not found.", "error")
        return redirect(url_for("admin_users"))

    # Fetch assigned courses
    cur.execute(
        """
        SELECT c.id, c.title, c.slug, c.level, e.assigned_at
        FROM courses c
        JOIN course_enrollments e ON e.course_id = c.id AND e.user_id = %s AND e.is_active = 1
        WHERE c.is_active = 1
        ORDER BY c.title ASC
        """,
        (user_id,)
    )
    assigned_courses = cur.fetchall()

    # Fetch unassigned courses
    assigned_ids = [c["id"] for c in assigned_courses]
    if assigned_ids:
        cur.execute(
            """
            SELECT id, title, slug, level
            FROM courses
            WHERE is_active = 1 AND id NOT IN (%s)
            ORDER BY title ASC
            """ % ",".join(str(i) for i in assigned_ids)
        )
    else:
        cur.execute("SELECT id, title, slug, level FROM courses WHERE is_active = 1 ORDER BY title ASC")
    unassigned_courses = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "admin_student_courses.html",
        active_page="admin",
        student=student,
        assigned_courses=assigned_courses,
        unassigned_courses=unassigned_courses
    )


@app.route("/admin/users/<int:user_id>/courses/assign", methods=["POST"])
@admin_required
def admin_user_assign_course(user_id):
    course_id = request.form.get("course_id", type=int)
    if not course_id:
        flash("Invalid course selection.", "error")
        return redirect(url_for("admin_user_courses", user_id=user_id))

    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, name FROM users WHERE id = %s", (user_id,))
    student = cur.fetchone()
    cur.execute("SELECT id, title FROM courses WHERE id = %s AND is_active = 1", (course_id,))
    course = cur.fetchone()

    if not student or not course:
        cur.close()
        conn.close()
        flash("Student or course not found.", "error")
        return redirect(url_for("admin_user_courses", user_id=user_id))

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    cur.execute(
        "SELECT id, is_active FROM course_enrollments WHERE user_id = %s AND course_id = %s",
        (user_id, course_id)
    )
    existing = cur.fetchone()

    if existing:
        cur.execute(
            """
            UPDATE course_enrollments
            SET is_active = 1, assigned_at = %s, assigned_by = %s, updated_at = %s
            WHERE id = %s
            """,
            (now_str, current_user.id, now_str, existing["id"])
        )
    else:
        cur.execute(
            """
            INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, assigned_by, created_at, updated_at)
            VALUES (%s, %s, 1, %s, %s, %s, %s)
            """,
            (user_id, course_id, now_str, current_user.id, now_str, now_str)
        )

    conn.commit()
    cur.close()
    conn.close()

    flash(f"Course '{course['title']}' assigned to {student['name']}.", "success")
    return redirect(url_for("admin_user_courses", user_id=user_id))


@app.route("/admin/users/<int:user_id>/courses/<int:course_id>/remove", methods=["POST"])
@admin_required
def admin_user_remove_course(user_id, course_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, name FROM users WHERE id = %s", (user_id,))
    student = cur.fetchone()
    cur.execute("SELECT id, title FROM courses WHERE id = %s", (course_id,))
    course = cur.fetchone()

    if not student or not course:
        cur.close()
        conn.close()
        flash("Student or course not found.", "error")
        return redirect(url_for("admin_user_courses", user_id=user_id))

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        UPDATE course_enrollments
        SET is_active = 0, updated_at = %s
        WHERE user_id = %s AND course_id = %s
        """,
        (now_str, user_id, course_id)
    )
    conn.commit()
    cur.close()
    conn.close()

    flash(f"Course access to '{course['title']}' removed for {student['name']}. Progress and certificates preserved.", "info")
    return redirect(url_for("admin_user_courses", user_id=user_id))


@app.route("/admin/courses")
@app.route("/admin/courses")
@admin_required
def admin_courses():
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT c.id, c.title, c.slug, c.description, c.image, c.level, c.is_active, c.created_at,
               COUNT(DISTINCT m.id) AS total_modules,
               COUNT(DISTINCT v.id) AS total_videos
        FROM courses c
        LEFT JOIN modules m ON m.course_id = c.id
        LEFT JOIN course_videos v ON v.module_id = m.id
        GROUP BY c.id, c.title, c.slug, c.description, c.image, c.level, c.is_active, c.created_at
        ORDER BY c.id ASC
        """
    )
    courses_list = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("admin_courses.html", active_page="admin", courses=courses_list)


@app.route("/admin/courses/<int:course_id>")
@admin_required
def admin_course_detail(course_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT id, title, slug, description, image, level, is_active, created_at, updated_at
        FROM courses
        WHERE id = %s
        """,
        (course_id,),
    )
    course = cur.fetchone()

    if not course:
        cur.close()
        conn.close()
        flash("Course not found.", "error")
        return redirect(url_for("admin_courses"))

    cur.execute(
        """
        SELECT id, title, description, sequence, is_active
        FROM modules
        WHERE course_id = %s
        ORDER BY sequence ASC, id ASC
        """,
        (course_id,),
    )
    modules = cur.fetchall()

    total_videos = 0
    for m in modules:
        cur.execute(
            """
            SELECT id, title, description, sequence, duration, youtube_video_id, is_active
            FROM course_videos
            WHERE module_id = %s
            ORDER BY sequence ASC, id ASC
            """,
            (m["id"],),
        )
        m["videos"] = cur.fetchall()
        total_videos += len(m["videos"])

        # Fetch module quiz if configured
        cur.execute(
            """
            SELECT q.id, q.title, q.description, q.passing_score, q.max_attempts, q.is_active,
                   COUNT(qq.id) AS total_questions
            FROM quizzes q
            LEFT JOIN quiz_questions qq ON qq.quiz_id = q.id
            WHERE q.module_id = %s
            GROUP BY q.id, q.title, q.description, q.passing_score, q.max_attempts, q.is_active
            """,
            (m["id"],),
        )
        m["quiz"] = cur.fetchone()

    cur.close()
    conn.close()

    return render_template(
        "admin_course_detail.html",
        active_page="admin",
        course=course,
        modules=modules,
        total_videos=total_videos,
    )


# ---------- Admin Course CRUD ----------

@app.route("/admin/courses/new", methods=["GET", "POST"])
@admin_required
def admin_add_course():
    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        slug = request.form.get("slug", "").strip()
        short_description = request.form.get("short_description", "").strip()
        description = request.form.get("description", "").strip()
        level = request.form.get("level", "Beginner").strip()
        image = request.form.get("image", "images/services/drone.jpg").strip()
        estimated_duration = request.form.get("estimated_duration", "").strip()
        learning_outcomes = request.form.get("learning_outcomes", "").strip()
        course_benefits = request.form.get("course_benefits", "").strip()
        certificate_description = request.form.get("certificate_description", "").strip()
        is_active = 1 if request.form.get("is_active") else 0

        if not title:
            error = "Course title is required."
        elif not description:
            error = "Course description is required."
        else:
            if not short_description:
                short_description = description[:160]
            if not slug:
                slug = re.sub(r"[^\w\s-]", "", title.lower())
                slug = re.sub(r"[-\s]+", "-", slug).strip("-")

            conn = get_db_connection()
            cur = get_db_cursor(conn)

            cur.execute("SELECT id FROM courses WHERE slug = %s", (slug,))
            if cur.fetchone():
                error = f"A course with slug '{slug}' already exists."
                cur.close()
                conn.close()
            else:
                now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                if get_db_type() == "postgres":
                    cur.execute(
                        """
                        INSERT INTO courses (
                            title, slug, short_description, description, level, image,
                            estimated_duration, learning_outcomes, course_benefits, certificate_description,
                            is_active, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (title, slug, short_description, description, level, image,
                         estimated_duration, learning_outcomes, course_benefits, certificate_description,
                         is_active, now_str, now_str),
                    )
                    new_course_id = cur.fetchone()["id"]
                else:
                    cur.execute(
                        """
                        INSERT INTO courses (
                            title, slug, short_description, description, level, image,
                            estimated_duration, learning_outcomes, course_benefits, certificate_description,
                            is_active, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (title, slug, short_description, description, level, image,
                         estimated_duration, learning_outcomes, course_benefits, certificate_description,
                         is_active, now_str, now_str),
                    )
                    new_course_id = cur.lastrowid
                conn.commit()
                cur.close()
                conn.close()

                flash(f"Course '{title}' created successfully!", "success")
                return redirect(url_for("admin_course_detail", course_id=new_course_id))

    return render_template("admin_course_form.html", active_page="admin", action="Create", error=error)


@app.route("/admin/courses/<int:course_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_course(course_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT * FROM courses WHERE id = %s", (course_id,))
    course = cur.fetchone()

    if not course:
        cur.close()
        conn.close()
        flash("Course not found.", "error")
        return redirect(url_for("admin_courses"))

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        slug = request.form.get("slug", "").strip()
        short_description = request.form.get("short_description", "").strip()
        description = request.form.get("description", "").strip()
        level = request.form.get("level", "Beginner").strip()
        image = request.form.get("image", "images/services/drone.jpg").strip()
        estimated_duration = request.form.get("estimated_duration", "").strip()
        learning_outcomes = request.form.get("learning_outcomes", "").strip()
        course_benefits = request.form.get("course_benefits", "").strip()
        certificate_description = request.form.get("certificate_description", "").strip()
        is_active = 1 if request.form.get("is_active") else 0

        if not title:
            error = "Course title is required."
        elif not description:
            error = "Course description is required."
        else:
            if not short_description:
                short_description = description[:160]
            if not slug:
                slug = re.sub(r"[^\w\s-]", "", title.lower())
                slug = re.sub(r"[-\s]+", "-", slug).strip("-")

            cur.execute("SELECT id FROM courses WHERE slug = %s AND id != %s", (slug, course_id))
            if cur.fetchone():
                error = f"Another course with slug '{slug}' already exists."
            else:
                now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    """
                    UPDATE courses
                    SET title = %s, slug = %s, short_description = %s, description = %s, level = %s, image = %s,
                        estimated_duration = %s, learning_outcomes = %s, course_benefits = %s, certificate_description = %s,
                        is_active = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (title, slug, short_description, description, level, image,
                     estimated_duration, learning_outcomes, course_benefits, certificate_description,
                     is_active, now_str, course_id),
                )
                conn.commit()
                cur.close()
                conn.close()

                flash(f"Course '{title}' updated successfully!", "success")
                return redirect(url_for("admin_course_detail", course_id=course_id))

    cur.close()
    conn.close()
    return render_template("admin_course_form.html", active_page="admin", action="Edit", course=course, error=error)


@app.route("/admin/courses/<int:course_id>/toggle-active", methods=["POST"])
@admin_required
def admin_toggle_course_active(course_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, title, is_active FROM courses WHERE id = %s", (course_id,))
    course = cur.fetchone()

    if course:
        new_active = 0 if course["is_active"] else 1
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("UPDATE courses SET is_active = %s, updated_at = %s WHERE id = %s", (new_active, now_str, course_id))
        conn.commit()
        status_name = "activated" if new_active else "deactivated"
        flash(f"Course '{course['title']}' has been {status_name}.", "success")

    cur.close()
    conn.close()
    return redirect(url_for("admin_course_detail", course_id=course_id))


@app.route("/admin/courses/<int:course_id>/delete", methods=["POST"])
@admin_required
def admin_delete_course(course_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, title FROM courses WHERE id = %s", (course_id,))
    course = cur.fetchone()

    if not course:
        cur.close()
        conn.close()
        flash("Course not found.", "error")
        return redirect(url_for("admin_courses"))

    # Database Safety Check: Check if certificates or video progress exist for this course
    cur.execute("SELECT COUNT(*) AS count FROM certificates WHERE course_id = %s", (course_id,))
    cert_count = cur.fetchone()["count"]

    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM video_progress vp
        JOIN course_videos v ON vp.video_id = v.id
        JOIN modules m ON v.module_id = m.id
        WHERE m.course_id = %s
        """,
        (course_id,),
    )
    prog_count = cur.fetchone()["count"]

    if cert_count > 0 or prog_count > 0:
        # Soft delete: Deactivate course, modules, and videos to protect student records
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("UPDATE courses SET is_active = 0, updated_at = %s WHERE id = %s", (now_str, course_id))
        cur.execute("UPDATE modules SET is_active = 0, updated_at = %s WHERE course_id = %s", (now_str, course_id))
        cur.execute(
            """
            UPDATE course_videos SET is_active = 0, updated_at = %s
            WHERE module_id IN (SELECT id FROM modules WHERE course_id = %s)
            """,
            (now_str, course_id),
        )
        conn.commit()
        cur.close()
        conn.close()
        flash(
            f"Course '{course['title']}' has existing student records ({cert_count} certificates, {prog_count} progress rows). It has been deactivated (soft deleted) to preserve student records.",
            "info",
        )
        return redirect(url_for("admin_courses"))
    else:
        # Hard delete if no student records exist
        cur.execute("DELETE FROM courses WHERE id = %s", (course_id,))
        conn.commit()
        cur.close()
        conn.close()
        flash(f"Course '{course['title']}' deleted successfully.", "success")
        return redirect(url_for("admin_courses"))


# ---------- Admin Module CRUD ----------

@app.route("/admin/courses/<int:course_id>/modules/new", methods=["GET", "POST"])
@admin_required
def admin_add_module(course_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, title FROM courses WHERE id = %s", (course_id,))
    course = cur.fetchone()

    if not course:
        cur.close()
        conn.close()
        flash("Course not found.", "error")
        return redirect(url_for("admin_courses"))

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        try:
            sequence = int(request.form.get("sequence", 1))
        except ValueError:
            sequence = 1
        is_active = 1 if request.form.get("is_active") else 0

        if not title:
            error = "Module title is required."
        else:
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                """
                INSERT INTO modules (course_id, title, description, sequence, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (course_id, title, description, sequence, is_active, now_str, now_str),
            )
            conn.commit()
            cur.close()
            conn.close()

            flash(f"Module '{title}' added successfully!", "success")
            return redirect(url_for("admin_course_detail", course_id=course_id))

    cur.close()
    conn.close()
    return render_template("admin_module_form.html", active_page="admin", action="Add", course=course, error=error)


@app.route("/admin/modules/<int:module_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_module(module_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT m.id, m.course_id, m.title, m.description, m.sequence, m.is_active,
               c.title AS course_title
        FROM modules m
        JOIN courses c ON m.course_id = c.id
        WHERE m.id = %s
        """,
        (module_id,),
    )
    module = cur.fetchone()

    if not module:
        cur.close()
        conn.close()
        flash("Module not found.", "error")
        return redirect(url_for("admin_courses"))

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        try:
            sequence = int(request.form.get("sequence", 1))
        except ValueError:
            sequence = 1
        is_active = 1 if request.form.get("is_active") else 0

        if not title:
            error = "Module title is required."
        else:
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                """
                UPDATE modules
                SET title = %s, description = %s, sequence = %s, is_active = %s, updated_at = %s
                WHERE id = %s
                """,
                (title, description, sequence, is_active, now_str, module_id),
            )
            conn.commit()
            cur.close()
            conn.close()

            flash(f"Module '{title}' updated successfully!", "success")
            return redirect(url_for("admin_course_detail", course_id=module["course_id"]))

    cur.close()
    conn.close()
    return render_template("admin_module_form.html", active_page="admin", action="Edit", module=module, course={"id": module["course_id"], "title": module["course_title"]}, error=error)


@app.route("/admin/modules/<int:module_id>/delete", methods=["POST"])
@admin_required
def admin_delete_module(module_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, course_id, title FROM modules WHERE id = %s", (module_id,))
    module = cur.fetchone()

    if not module:
        cur.close()
        conn.close()
        flash("Module not found.", "error")
        return redirect(url_for("admin_courses"))

    course_id = module["course_id"]

    # Check for dependent video_progress rows
    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM video_progress vp
        JOIN course_videos v ON vp.video_id = v.id
        WHERE v.module_id = %s
        """,
        (module_id,),
    )
    prog_count = cur.fetchone()["count"]

    if prog_count > 0:
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("UPDATE modules SET is_active = 0, updated_at = %s WHERE id = %s", (now_str, module_id))
        cur.execute("UPDATE course_videos SET is_active = 0, updated_at = %s WHERE module_id = %s", (now_str, module_id))
        conn.commit()
        cur.close()
        conn.close()
        flash(
            f"Module '{module['title']}' has {prog_count} student progress records. It has been deactivated (soft deleted) to preserve progress data.",
            "info",
        )
    else:
        cur.execute("DELETE FROM modules WHERE id = %s", (module_id,))
        conn.commit()
        cur.close()
        conn.close()
        flash(f"Module '{module['title']}' deleted successfully.", "success")

    return redirect(url_for("admin_course_detail", course_id=course_id))


# ---------- Admin Video CRUD ----------

@app.route("/admin/modules/<int:module_id>/videos/new", methods=["GET", "POST"])
@admin_required
def admin_add_video(module_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT m.id, m.title AS module_title, c.id AS course_id, c.title AS course_title
        FROM modules m
        JOIN courses c ON m.course_id = c.id
        WHERE m.id = %s
        """,
        (module_id,),
    )
    mod_info = cur.fetchone()

    if not mod_info:
        cur.close()
        conn.close()
        flash("Module not found.", "error")
        return redirect(url_for("admin_courses"))

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        duration = request.form.get("duration", "10:00").strip()
        raw_youtube_id = request.form.get("youtube_video_id", "").strip()
        try:
            sequence = int(request.form.get("sequence", 1))
        except ValueError:
            sequence = 1
        is_active = 1 if request.form.get("is_active") else 0

        if not title:
            error = "Video title is required."
        else:
            extracted_yt_id = None
            if raw_youtube_id:
                extracted_yt_id = extract_youtube_id(raw_youtube_id)
                if not extracted_yt_id:
                    error = "Invalid YouTube Video ID format. Enter an 11-character Video ID or a valid YouTube URL."

            if not error:
                now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    """
                    INSERT INTO course_videos (module_id, title, description, sequence, duration, youtube_video_id, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (module_id, title, description, sequence, duration, extracted_yt_id, is_active, now_str, now_str),
                )
                conn.commit()
                cur.close()
                conn.close()

                flash(f"Video '{title}' added successfully!", "success")
                return redirect(url_for("admin_course_detail", course_id=mod_info["course_id"]))

    cur.close()
    conn.close()
    return render_template("admin_video_form.html", active_page="admin", action="Add", mod_info=mod_info, error=error)


@app.route("/admin/videos/<int:video_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_video(video_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT v.id, v.title, v.description, v.sequence, v.duration, v.youtube_video_id, v.is_active,
               m.id AS module_id, m.title AS module_title,
               c.id AS course_id, c.title AS course_title, c.slug AS course_slug
        FROM course_videos v
        JOIN modules m ON v.module_id = m.id
        JOIN courses c ON m.course_id = c.id
        WHERE v.id = %s
        """,
        (video_id,),
    )
    video = cur.fetchone()

    if not video:
        cur.close()
        conn.close()
        flash("Video not found.", "error")
        return redirect(url_for("admin_courses"))

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        duration = request.form.get("duration", "").strip()
        raw_youtube_id = request.form.get("youtube_video_id", "").strip()
        try:
            sequence = int(request.form.get("sequence", 1))
        except ValueError:
            sequence = 1
        is_active = 1 if request.form.get("is_active") else 0

        if not title:
            error = "Video title is required."
        else:
            extracted_yt_id = None
            if raw_youtube_id:
                extracted_yt_id = extract_youtube_id(raw_youtube_id)
                if not extracted_yt_id:
                    error = "Invalid YouTube Video ID format. Enter an 11-character Video ID or a valid YouTube URL."

            if not error:
                now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    """
                    UPDATE course_videos
                    SET title = %s, description = %s, sequence = %s, duration = %s, youtube_video_id = %s, is_active = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (title, description, sequence, duration, extracted_yt_id, is_active, now_str, video_id),
                )
                conn.commit()
                cur.close()
                conn.close()
                flash(f"Video '{title}' updated successfully!", "success")
                return redirect(url_for("admin_course_detail", course_id=video["course_id"]))

    cur.close()
    conn.close()

    return render_template("admin_video_form.html", active_page="admin", action="Edit", video=video, mod_info={"module_title": video["module_title"], "course_title": video["course_title"], "course_id": video["course_id"]}, error=error)


@app.route("/admin/videos/<int:video_id>/remove-youtube-id", methods=["POST"])
@admin_required
def admin_remove_youtube_id(video_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT v.id, m.course_id
        FROM course_videos v
        JOIN modules m ON v.module_id = m.id
        WHERE v.id = %s
        """,
        (video_id,),
    )
    video = cur.fetchone()
    course_id = video["course_id"] if video else None

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        UPDATE course_videos
        SET youtube_video_id = NULL, updated_at = %s
        WHERE id = %s
        """,
        (now_str, video_id),
    )
    conn.commit()
    cur.close()
    conn.close()

    flash("YouTube Video ID removed successfully.", "success")
    if course_id:
        return redirect(url_for("admin_course_detail", course_id=course_id))
    return redirect(url_for("admin_courses"))


@app.route("/admin/videos/<int:video_id>/delete", methods=["POST"])
@admin_required
def admin_delete_video(video_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT v.id, v.title, m.course_id
        FROM course_videos v
        JOIN modules m ON v.module_id = m.id
        WHERE v.id = %s
        """,
        (video_id,),
    )
    video = cur.fetchone()

    if not video:
        cur.close()
        conn.close()
        flash("Video not found.", "error")
        return redirect(url_for("admin_courses"))

    course_id = video["course_id"]

    # Check for dependent student progress rows
    cur.execute("SELECT COUNT(*) AS count FROM video_progress WHERE video_id = %s", (video_id,))
    prog_count = cur.fetchone()["count"]

    if prog_count > 0:
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("UPDATE course_videos SET is_active = 0, updated_at = %s WHERE id = %s", (now_str, video_id))
        conn.commit()
        cur.close()
        conn.close()
        flash(
            f"Video '{video['title']}' has {prog_count} student progress records. It has been deactivated (soft deleted) to preserve progress data.",
            "info",
        )
    else:
        cur.execute("DELETE FROM course_videos WHERE id = %s", (video_id,))
        conn.commit()
        cur.close()
        conn.close()
        flash(f"Video '{video['title']}' deleted successfully.", "success")

    return redirect(url_for("admin_course_detail", course_id=course_id))


# ==================================================
# Phase 7.6: Student Quiz Routes
# ==================================================

@app.route("/courses/<course_slug>/module/<int:module_id>/quiz")
@login_required
def student_quiz_overview(course_slug, module_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    
    cur.execute("SELECT id, title, slug FROM courses WHERE slug = %s AND is_active = 1", (course_slug,))
    course = cur.fetchone()
    if not course:
        cur.close()
        conn.close()
        flash("Course not found.", "error")
        return redirect(url_for("courses"))

    if not can_access_course(current_user.id, course["id"]):
        cur.close()
        conn.close()
        return render_template("course_access_denied.html", active_page="courses"), 403
        
    cur.execute("SELECT id, title, description, course_id FROM modules WHERE id = %s AND is_active = 1", (module_id,))
    module = cur.fetchone()
    if not module or module["course_id"] != course["id"]:
        cur.close()
        conn.close()
        flash("Module not found.", "error")
        return redirect(url_for("course_detail", slug=course_slug))
        
    cur.execute(
        """
        SELECT q.id, q.title, q.description, q.passing_score, q.max_attempts, q.is_active,
               COUNT(qq.id) AS total_questions
        FROM quizzes q
        LEFT JOIN quiz_questions qq ON qq.quiz_id = q.id AND qq.is_active = 1
        WHERE q.module_id = %s AND q.is_active = 1
        GROUP BY q.id, q.title, q.description, q.passing_score, q.max_attempts, q.is_active
        """,
        (module_id,),
    )
    quiz = cur.fetchone()
    if not quiz:
        cur.close()
        conn.close()
        flash("No active quiz configured for this module yet.", "info")
        return redirect(url_for("course_detail", slug=course_slug))

    # Fetch student attempts for this quiz
    cur.execute(
        """
        SELECT id, score, total_questions, correct_answers, passed, attempt_number, is_invalidated, started_at, submitted_at
        FROM quiz_attempts
        WHERE quiz_id = %s AND user_id = %s AND submitted_at IS NOT NULL
        ORDER BY attempt_number DESC
        """,
        (quiz["id"], current_user.id),
    )
    attempts = cur.fetchall()
    attempts_used = len(attempts)
    attempts_remaining = max(0, quiz["max_attempts"] - attempts_used)
    has_passed = any(a["passed"] for a in attempts)
    highest_score = max([a["score"] for a in attempts], default=0) if attempts else 0

    cur.close()
    conn.close()

    return render_template(
        "student_quiz_overview.html",
        active_page="courses",
        course=course,
        module=module,
        quiz=quiz,
        attempts=attempts,
        attempts_used=attempts_used,
        attempts_remaining=attempts_remaining,
        has_passed=has_passed,
        highest_score=highest_score,
    )


@app.route("/courses/<course_slug>/module/<int:module_id>/quiz/start")
@login_required
def student_quiz_start(course_slug, module_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, title, slug FROM courses WHERE slug = %s AND is_active = 1", (course_slug,))
    course = cur.fetchone()
    if not course:
        cur.close()
        conn.close()
        flash("Course not found.", "error")
        return redirect(url_for("courses"))

    if not can_access_course(current_user.id, course["id"]):
        cur.close()
        conn.close()
        return render_template("course_access_denied.html", active_page="courses"), 403

    cur.execute("SELECT id, title, description, course_id FROM modules WHERE id = %s AND is_active = 1", (module_id,))
    module = cur.fetchone()
    if not module or module["course_id"] != course["id"]:
        cur.close()
        conn.close()
        flash("Module not found.", "error")
        return redirect(url_for("course_detail", slug=course_slug))

    cur.execute(
        """
        SELECT id, title, description, passing_score, max_attempts, is_active
        FROM quizzes
        WHERE module_id = %s AND is_active = 1
        """,
        (module_id,),
    )
    quiz = cur.fetchone()
    if not quiz:
        cur.close()
        conn.close()
        flash("No active quiz for this module.", "error")
        return redirect(url_for("course_detail", slug=course_slug))

    # SERVER-SIDE ATTEMPT COUNT VALIDATION (MAX 5 ATTEMPTS ENFORCED)
    cur.execute(
        "SELECT COUNT(*) as count FROM quiz_attempts WHERE quiz_id = %s AND user_id = %s AND submitted_at IS NOT NULL",
        (quiz["id"], current_user.id),
    )
    attempts_count = cur.fetchone()["count"]

    if attempts_count >= quiz["max_attempts"]:
        cur.close()
        conn.close()
        flash(f"Maximum attempts limit reached ({quiz['max_attempts']}/{quiz['max_attempts']}).", "error")
        return redirect(url_for("student_quiz_overview", course_slug=course_slug, module_id=module_id))

    # Fetch active questions (Excluding correct_option for security!)
    cur.execute(
        """
        SELECT id, question_text, option_a, option_b, option_c, option_d, sequence
        FROM quiz_questions
        WHERE quiz_id = %s AND is_active = 1
        ORDER BY sequence ASC, id ASC
        """,
        (quiz["id"],),
    )
    questions = cur.fetchall()

    if not questions:
        cur.close()
        conn.close()
        flash("No questions configured in this quiz yet.", "error")
        return redirect(url_for("student_quiz_overview", course_slug=course_slug, module_id=module_id))

    # Create new active attempt
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    attempt_num = attempts_count + 1
    cur.execute(
        """
        INSERT INTO quiz_attempts (quiz_id, user_id, score, total_questions, correct_answers, passed, attempt_number, is_invalidated, started_at)
        VALUES (%s, %s, 0, %s, 0, FALSE, %s, FALSE, %s)
        """,
        (quiz["id"], current_user.id, len(questions), attempt_num, now_str),
    )
    conn.commit()

    cur.execute("SELECT id FROM quiz_attempts WHERE user_id = %s AND quiz_id = %s ORDER BY id DESC LIMIT 1", (current_user.id, quiz["id"]))
    res = cur.fetchone()
    attempt_id = res["id"]

    session["active_quiz_attempt_id"] = attempt_id

    cur.close()
    conn.close()

    return render_template(
        "student_quiz_take.html",
        active_page="courses",
        course=course,
        module=module,
        quiz=quiz,
        questions=questions,
        attempt_id=attempt_id,
        attempt_number=attempt_num,
    )


@app.route("/courses/<course_slug>/module/<int:module_id>/quiz/submit", methods=["POST"])
@login_required
def student_quiz_submit(course_slug, module_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    attempt_id = request.form.get("attempt_id", type=int) or session.get("active_quiz_attempt_id")
    if not attempt_id:
        cur.close()
        conn.close()
        flash("Invalid attempt session.", "error")
        return redirect(url_for("student_quiz_overview", course_slug=course_slug, module_id=module_id))

    # Verify attempt ownership & active state
    cur.execute(
        "SELECT id, quiz_id, user_id, attempt_number, submitted_at, is_invalidated FROM quiz_attempts WHERE id = %s AND user_id = %s",
        (attempt_id, current_user.id),
    )
    attempt = cur.fetchone()
    if not attempt or attempt["submitted_at"] is not None or attempt["is_invalidated"]:
        cur.close()
        conn.close()
        flash("Quiz attempt already submitted or invalidated.", "error")
        return redirect(url_for("student_quiz_overview", course_slug=course_slug, module_id=module_id))

    cur.execute("SELECT id, passing_score, is_active FROM quizzes WHERE id = %s", (attempt["quiz_id"],))
    quiz = cur.fetchone()
    if not quiz or not quiz["is_active"]:
        cur.close()
        conn.close()
        flash("Quiz is inactive.", "error")
        return redirect(url_for("course_detail", slug=course_slug))

    # Fetch questions WITH correct_option for server-side evaluation ONLY
    cur.execute(
        "SELECT id, correct_option FROM quiz_questions WHERE quiz_id = %s AND is_active = 1",
        (quiz["id"],),
    )
    questions = cur.fetchall()

    correct_answers_count = 0
    total_questions = len(questions)

    for q in questions:
        q_id = q["id"]
        selected = request.form.get(f"question_{q_id}", "").strip().upper()
        is_corr = (selected == q["correct_option"])
        if is_corr:
            correct_answers_count += 1

        cur.execute(
            """
            INSERT INTO quiz_answers (attempt_id, question_id, selected_option, is_correct)
            VALUES (%s, %s, %s, %s)
            """,
            (attempt_id, q_id, selected if selected in ['A','B','C','D'] else None, is_corr),
        )

    score_pct = round((correct_answers_count / total_questions) * 100) if total_questions > 0 else 0
    passed = (score_pct >= quiz["passing_score"])
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    cur.execute(
        """
        UPDATE quiz_attempts
        SET score = %s, total_questions = %s, correct_answers = %s, passed = %s, submitted_at = %s
        WHERE id = %s
        """,
        (score_pct, total_questions, correct_answers_count, passed, now_str, attempt_id),
    )
    conn.commit()

    session.pop("active_quiz_attempt_id", None)
    cur.close()
    conn.close()

    return redirect(url_for("student_quiz_result", course_slug=course_slug, module_id=module_id, attempt_id=attempt_id))


@app.route("/courses/<course_slug>/module/<int:module_id>/quiz/invalidate", methods=["POST"])
@login_required
def student_quiz_invalidate(course_slug, module_id):
    attempt_id = request.form.get("attempt_id", type=int) or session.get("active_quiz_attempt_id")
    if not attempt_id:
        return jsonify({"status": "error", "message": "No active attempt found"}), 400

    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        "SELECT id, quiz_id, user_id, submitted_at FROM quiz_attempts WHERE id = %s AND user_id = %s",
        (attempt_id, current_user.id),
    )
    attempt = cur.fetchone()
    if attempt and attempt["submitted_at"] is None:
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            UPDATE quiz_attempts
            SET score = 0, passed = FALSE, is_invalidated = TRUE, submitted_at = %s
            WHERE id = %s
            """,
            (now_str, attempt_id),
        )
        conn.commit()

    session.pop("active_quiz_attempt_id", None)
    cur.close()
    conn.close()

    redirect_url = url_for("student_quiz_result", course_slug=course_slug, module_id=module_id, attempt_id=attempt_id)
    return jsonify({"status": "invalidated", "redirect_url": redirect_url})


@app.route("/courses/<course_slug>/module/<int:module_id>/quiz/result/<int:attempt_id>")
@login_required
def student_quiz_result(course_slug, module_id, attempt_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, title, slug FROM courses WHERE slug = %s", (course_slug,))
    course = cur.fetchone()

    cur.execute("SELECT id, title, description, course_id FROM modules WHERE id = %s", (module_id,))
    module = cur.fetchone()

    cur.execute(
        """
        SELECT a.id, a.quiz_id, a.user_id, a.score, a.total_questions, a.correct_answers, a.passed,
               a.attempt_number, a.is_invalidated, a.started_at, a.submitted_at,
               q.title as quiz_title, q.passing_score, q.max_attempts
        FROM quiz_attempts a
        JOIN quizzes q ON q.id = a.quiz_id
        WHERE a.id = %s AND a.user_id = %s
        """,
        (attempt_id, current_user.id),
    )
    attempt = cur.fetchone()
    if not attempt:
        cur.close()
        conn.close()
        flash("Quiz attempt result not found.", "error")
        return redirect(url_for("course_detail", slug=course_slug))

    # Fetch questions and submitted answers WITH explanations (POST-SUBMISSION ONLY)
    cur.execute(
        """
        SELECT q.id, q.question_text, q.option_a, q.option_b, q.option_c, q.option_d,
               q.correct_option, q.explanation, q.sequence,
               ans.selected_option, ans.is_correct
        FROM quiz_questions q
        LEFT JOIN quiz_answers ans ON ans.question_id = q.id AND ans.attempt_id = %s
        WHERE q.quiz_id = %s
        ORDER BY q.sequence ASC, q.id ASC
        """,
        (attempt_id, attempt["quiz_id"]),
    )
    review_questions = cur.fetchall()

    # Total user attempts count
    cur.execute(
        "SELECT COUNT(*) as count FROM quiz_attempts WHERE quiz_id = %s AND user_id = %s AND submitted_at IS NOT NULL",
        (attempt["quiz_id"], current_user.id),
    )
    attempts_used = cur.fetchone()["count"]
    attempts_remaining = max(0, attempt["max_attempts"] - attempts_used)

    cur.close()
    conn.close()

    return render_template(
        "student_quiz_result.html",
        active_page="courses",
        course=course,
        module=module,
        attempt=attempt,
        review_questions=review_questions,
        attempts_remaining=attempts_remaining,
    )


# ==================================================
# Phase 7.6: Admin Quiz Management Routes
# ==================================================

@app.route("/admin/modules/<int:module_id>/quiz/new", methods=["GET", "POST"])
@admin_required
def admin_add_quiz(module_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT m.id, m.title, m.course_id, c.title as course_title FROM modules m JOIN courses c ON c.id = m.course_id WHERE m.id = %s", (module_id,))
    mod_info = cur.fetchone()
    if not mod_info:
        cur.close()
        conn.close()
        flash("Module not found.", "error")
        return redirect(url_for("admin_courses"))

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        passing_score = request.form.get("passing_score", 70, type=int)
        max_attempts = request.form.get("max_attempts", 5, type=int)
        is_active = 1 if request.form.get("is_active") else 0

        if not title:
            error = "Quiz title is required."
        else:
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                """
                INSERT INTO quizzes (module_id, title, description, passing_score, max_attempts, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (module_id, title, description, passing_score, max_attempts, is_active, now_str, now_str),
            )
            conn.commit()
            cur.close()
            conn.close()
            flash("Quiz created successfully!", "success")
            return redirect(url_for("admin_course_detail", course_id=mod_info["course_id"]))

    cur.close()
    conn.close()
    return render_template("admin_quiz_form.html", active_page="admin", action="Create", mod_info=mod_info, quiz=None, error=error)


@app.route("/admin/quizzes/<int:quiz_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_quiz(quiz_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT q.id, q.module_id, q.title, q.description, q.passing_score, q.max_attempts, q.is_active,
               m.course_id, m.title as module_title, c.title as course_title
        FROM quizzes q
        JOIN modules m ON m.id = q.module_id
        JOIN courses c ON c.id = m.course_id
        WHERE q.id = %s
        """,
        (quiz_id,),
    )
    quiz = cur.fetchone()
    if not quiz:
        cur.close()
        conn.close()
        flash("Quiz not found.", "error")
        return redirect(url_for("admin_courses"))

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        passing_score = request.form.get("passing_score", 70, type=int)
        max_attempts = request.form.get("max_attempts", 5, type=int)
        is_active = 1 if request.form.get("is_active") else 0

        if not title:
            error = "Quiz title is required."
        else:
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                """
                UPDATE quizzes
                SET title = %s, description = %s, passing_score = %s, max_attempts = %s, is_active = %s, updated_at = %s
                WHERE id = %s
                """,
                (title, description, passing_score, max_attempts, is_active, now_str, quiz_id),
            )
            conn.commit()
            cur.close()
            conn.close()
            flash("Quiz updated successfully!", "success")
            return redirect(url_for("admin_course_detail", course_id=quiz["course_id"]))

    cur.close()
    conn.close()
    return render_template("admin_quiz_form.html", active_page="admin", action="Edit", mod_info=quiz, quiz=quiz, error=error)


@app.route("/admin/quizzes/<int:quiz_id>/delete", methods=["POST"])
@admin_required
def admin_delete_quiz(quiz_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT q.id, m.course_id FROM quizzes q JOIN modules m ON m.id = q.module_id WHERE q.id = %s", (quiz_id,))
    quiz = cur.fetchone()
    if quiz:
        cur.execute("DELETE FROM quizzes WHERE id = %s", (quiz_id,))
        conn.commit()
        flash("Quiz deleted successfully.", "success")
        course_id = quiz["course_id"]
    else:
        flash("Quiz not found.", "error")
        course_id = None

    cur.close()
    conn.close()
    if course_id:
        return redirect(url_for("admin_course_detail", course_id=course_id))
    return redirect(url_for("admin_courses"))


@app.route("/admin/quizzes/<int:quiz_id>/questions")
@admin_required
def admin_quiz_questions(quiz_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT q.id, q.title, q.description, q.passing_score, q.max_attempts, q.module_id,
               m.course_id, m.title as module_title, c.title as course_title
        FROM quizzes q
        JOIN modules m ON m.id = q.module_id
        JOIN courses c ON c.id = m.course_id
        WHERE q.id = %s
        """,
        (quiz_id,),
    )
    quiz = cur.fetchone()
    if not quiz:
        cur.close()
        conn.close()
        flash("Quiz not found.", "error")
        return redirect(url_for("admin_courses"))

    cur.execute(
        """
        SELECT id, question_text, option_a, option_b, option_c, option_d, correct_option, explanation, sequence, is_active
        FROM quiz_questions
        WHERE quiz_id = %s
        ORDER BY sequence ASC, id ASC
        """,
        (quiz_id,),
    )
    questions = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("admin_quiz_questions.html", active_page="admin", quiz=quiz, questions=questions)


@app.route("/admin/quizzes/<int:quiz_id>/questions/new", methods=["GET", "POST"])
@admin_required
def admin_add_question(quiz_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT q.id, q.title, q.module_id, m.course_id, m.title as module_title, c.title as course_title
        FROM quizzes q
        JOIN modules m ON m.id = q.module_id
        JOIN courses c ON c.id = m.course_id
        WHERE q.id = %s
        """,
        (quiz_id,),
    )
    quiz = cur.fetchone()
    if not quiz:
        cur.close()
        conn.close()
        flash("Quiz not found.", "error")
        return redirect(url_for("admin_courses"))

    error = None
    if request.method == "POST":
        question_text = request.form.get("question_text", "").strip()
        option_a = request.form.get("option_a", "").strip()
        option_b = request.form.get("option_b", "").strip()
        option_c = request.form.get("option_c", "").strip()
        option_d = request.form.get("option_d", "").strip()
        correct_option = request.form.get("correct_option", "A").strip().upper()
        explanation = request.form.get("explanation", "").strip()
        sequence = request.form.get("sequence", 1, type=int)
        is_active = 1 if request.form.get("is_active") else 0

        if not question_text or not option_a or not option_b or not option_c or not option_d:
            error = "Please fill in the question text and all four options A, B, C, D."
        elif correct_option not in ["A", "B", "C", "D"]:
            error = "Correct option must be A, B, C, or D."
        else:
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                """
                INSERT INTO quiz_questions (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option, explanation, sequence, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option, explanation, sequence, is_active, now_str, now_str),
            )
            conn.commit()
            cur.close()
            conn.close()
            flash("Question added successfully!", "success")
            return redirect(url_for("admin_quiz_questions", quiz_id=quiz_id))

    cur.close()
    conn.close()
    return render_template("admin_question_form.html", active_page="admin", action="Create", quiz=quiz, question=None, error=error)


@app.route("/admin/questions/<int:question_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_question(question_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT qq.id, qq.quiz_id, qq.question_text, qq.option_a, qq.option_b, qq.option_c, qq.option_d,
               qq.correct_option, qq.explanation, qq.sequence, qq.is_active,
               q.title as quiz_title, q.module_id, m.course_id
        FROM quiz_questions qq
        JOIN quizzes q ON q.id = qq.quiz_id
        JOIN modules m ON m.id = q.module_id
        WHERE qq.id = %s
        """,
        (question_id,),
    )
    question = cur.fetchone()
    if not question:
        cur.close()
        conn.close()
        flash("Question not found.", "error")
        return redirect(url_for("admin_courses"))

    error = None
    if request.method == "POST":
        question_text = request.form.get("question_text", "").strip()
        option_a = request.form.get("option_a", "").strip()
        option_b = request.form.get("option_b", "").strip()
        option_c = request.form.get("option_c", "").strip()
        option_d = request.form.get("option_d", "").strip()
        correct_option = request.form.get("correct_option", "A").strip().upper()
        explanation = request.form.get("explanation", "").strip()
        sequence = request.form.get("sequence", 1, type=int)
        is_active = 1 if request.form.get("is_active") else 0

        if not question_text or not option_a or not option_b or not option_c or not option_d:
            error = "Please fill in the question text and all four options A, B, C, D."
        elif correct_option not in ["A", "B", "C", "D"]:
            error = "Correct option must be A, B, C, or D."
        else:
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                """
                UPDATE quiz_questions
                SET question_text = %s, option_a = %s, option_b = %s, option_c = %s, option_d = %s,
                    correct_option = %s, explanation = %s, sequence = %s, is_active = %s, updated_at = %s
                WHERE id = %s
                """,
                (question_text, option_a, option_b, option_c, option_d, correct_option, explanation, sequence, is_active, now_str, question_id),
            )
            conn.commit()
            cur.close()
            conn.close()
            flash("Question updated successfully!", "success")
            return redirect(url_for("admin_quiz_questions", quiz_id=question["quiz_id"]))

    cur.close()
    conn.close()
    return render_template("admin_question_form.html", active_page="admin", action="Edit", quiz=question, question=question, error=error)


@app.route("/admin/questions/<int:question_id>/delete", methods=["POST"])
@admin_required
def admin_delete_question(question_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, quiz_id FROM quiz_questions WHERE id = %s", (question_id,))
    question = cur.fetchone()
    if question:
        quiz_id = question["quiz_id"]
        cur.execute("DELETE FROM quiz_questions WHERE id = %s", (question_id,))
        conn.commit()
        flash("Question deleted successfully.", "success")
    else:
        quiz_id = None
        flash("Question not found.", "error")

    cur.close()
    conn.close()
    if quiz_id:
        return redirect(url_for("admin_quiz_questions", quiz_id=quiz_id))
    return redirect(url_for("admin_courses"))


@app.cli.command("init-db")
def init_db_command():
    """Clear existing data and create new tables."""
    init_db()
    print("Database initialized successfully.")

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
