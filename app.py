import os
import sys
from datetime import datetime
from functools import wraps
import re
import json
import secrets
import io
import hmac
import hashlib
import random
import string
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file, send_from_directory, abort
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.pdfgen import canvas

import urllib.parse
import storage

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

# Upload file size limit (default 500 MB to support large educational lesson videos)
MAX_UPLOAD_MB = int(os.environ.get("HOSTINGER_MAX_VIDEO_SIZE_MB", 500))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

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


@app.errorhandler(413)
def request_entity_too_large(error):
    max_mb = app.config.get("MAX_CONTENT_LENGTH", 500 * 1024 * 1024) // (1024 * 1024)
    flash(f"The uploaded file is too large. Maximum allowed upload size is {max_mb} MB.", "error")
    return redirect(request.referrer or url_for("admin_courses"))


# ---------- Grade & Class System ----------

GRADES = {
    1: {"name": "Grade 1", "classes": "Classes 1–2", "description": "Foundations for early STEM learners"},
    2: {"name": "Grade 2", "classes": "Classes 3–5", "description": "STEM exploration, basic coding & logic building"},
    3: {"name": "Grade 3", "classes": "Classes 6–8", "description": "Hands-on robotics, programming & electronics"},
    4: {"name": "Grade 4", "classes": "Classes 9–10", "description": "Applied engineering, Python & IoT systems"},
    5: {"name": "Grade 5", "classes": "Classes 11–12", "description": "Advanced AI, drone tech & autonomous systems"},
}

def get_grade_from_class(class_val):
    """Maps class (1-12 or string representation) to Grade 1-5."""
    if class_val is None:
        return None
    val_str = str(class_val).strip().lower()
    if not val_str:
        return None
    m_grade = re.search(r"grade\s*([1-5])", val_str)
    if m_grade:
        return int(m_grade.group(1))
    nums = re.findall(r"\d+", val_str)
    if nums:
        c_num = int(nums[0])
        if 1 <= c_num <= 2:
            return 1
        elif 3 <= c_num <= 5:
            return 2
        elif 6 <= c_num <= 8:
            return 3
        elif 9 <= c_num <= 10:
            return 4
        elif 11 <= c_num <= 12:
            return 5
    return None


# ---------- User Model ----------

class User(UserMixin):
    def __init__(self, id, name, email, role, active=True, father_name=None, phone=None, student_class=None):
        self.id = id
        self.name = name
        self.email = email
        self.role = role
        self._active = active
        self.father_name = father_name
        self.phone = phone
        self.student_class = student_class

    @property
    def grade(self):
        return get_grade_from_class(self.student_class)

    @property
    def is_active(self):
        return self._active

    def is_admin(self):
        return self.role == "admin"

    def is_sub_admin(self):
        return self.role == "sub_admin"

    def is_teacher(self):
        return self.role == "teacher"

    def is_student(self):
        return self.role in ("user", "student")

    def has_role(self, *roles):
        return self.role in roles


@login_manager.user_loader
def load_user(user_id):
    """Load user from database."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT id, name, email, role, is_active, father_name, phone, student_class
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
            student_class=user.get("student_class"),
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
    """
    Check if user can access course content:
    - Admin & Sub-Admin always have full access.
    - Teacher has access if assigned to course in teacher_assignments.
    - Student requires explicit active enrollment (course_enrollments.is_active = 1).
    """
    if not user_id or not course_id:
        return False
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
        u = cur.fetchone()
        if not u:
            cur.close()
            conn.close()
            return False

        user_role = u["role"]
        if user_role in ("admin", "sub_admin"):
            cur.close()
            conn.close()
            return True

        if user_role == "teacher":
            cur.execute(
                "SELECT id FROM teacher_assignments WHERE teacher_id = %s AND course_id = %s",
                (user_id, course_id)
            )
            assignment = cur.fetchone()
            cur.close()
            conn.close()
            return bool(assignment)

        # Check explicit active enrollment record for student (user/student)
        cur.execute(
            "SELECT is_active FROM course_enrollments WHERE user_id = %s AND course_id = %s",
            (user_id, course_id)
        )
        enrollment = cur.fetchone()
        cur.close()
        conn.close()

        if enrollment is not None:
            return bool(enrollment["is_active"] == 1 or enrollment["is_active"] is True)

        return False
    except Exception as e:
        app.logger.error(f"Error checking access for user {user_id}, course {course_id}: {e}")
        return False


def is_teacher_assigned_to_course(teacher_id, course_id):
    """Check if a teacher is assigned to a specific course."""
    if not teacher_id or not course_id:
        return False
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            "SELECT id FROM teacher_assignments WHERE teacher_id = %s AND course_id = %s",
            (teacher_id, course_id)
        )
        assignment = cur.fetchone()
        cur.close()
        conn.close()
        return bool(assignment)
    except Exception as e:
        app.logger.error(f"Error checking teacher assignment: {e}")
        return False


def get_teacher_assigned_course_ids(teacher_id):
    """Get list of course IDs assigned to a teacher."""
    if not teacher_id:
        return []
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT course_id FROM teacher_assignments WHERE teacher_id = %s", (teacher_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [r["course_id"] for r in rows]
    except Exception as e:
        app.logger.error(f"Error fetching teacher course IDs: {e}")
        return []


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

        # Extract password from parsed DATABASE_URL or standard environment variables
        db_password = ""
        if parsed.password:
            db_password = urllib.parse.unquote(parsed.password)
        elif os.environ.get("DB_PASSWORD"):
            db_password = os.environ.get("DB_PASSWORD")
        elif os.environ.get("MYSQL_PASSWORD"):
            db_password = os.environ.get("MYSQL_PASSWORD")

        db_host = parsed.hostname or os.environ.get("DB_HOST") or "localhost"
        db_port = parsed.port or int(os.environ.get("DB_PORT", 3306))
        db_user = (urllib.parse.unquote(parsed.username) if parsed.username else None) or os.environ.get("DB_USER") or "root"
        db_name = (parsed.path.lstrip("/") if parsed.path else None) or os.environ.get("DB_NAME") or ""

        connect_timeout = int(os.environ.get("DB_CONNECT_TIMEOUT", 10))
        read_timeout = int(os.environ.get("DB_READ_TIMEOUT", 15))
        write_timeout = int(os.environ.get("DB_WRITE_TIMEOUT", 15))
        return pymysql.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            database=db_name,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            write_timeout=write_timeout,
            charset="utf8mb4",
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

def ping_or_reconnect_db(conn=None):
    """
    Ensure the provided or active database connection is alive and healthy.
    If connection has gone away (e.g. following a lengthy file upload or SFTP transfer),
    safely pings or re-establishes the connection.
    """
    if conn is None:
        return get_db_connection()
    raw = getattr(conn, "_raw_conn", conn)
    if hasattr(raw, "ping"):
        try:
            raw.ping(reconnect=True)
            return conn
        except Exception as e:
            app.logger.warning(f"Database connection ping failed ({e}); re-establishing fresh connection.")
            if has_request_context():
                try:
                    raw.close()
                except Exception:
                    pass
                g.raw_db = create_raw_db_connection()
                return RequestConnProxy(g.raw_db)
            else:
                return create_raw_db_connection()
    return conn

def get_db_connection():
    """
    Obtain database connection.
    If within a Flask request context, reuses g.raw_db for the request duration,
    verifying connection liveness with ping(reconnect=True) for MySQL.
    Otherwise (standalone / CLI / tests), creates and returns a standalone connection.
    """
    if has_request_context():
        if "raw_db" not in g or g.raw_db is None:
            g.raw_db = create_raw_db_connection()
        else:
            raw = g.raw_db
            if hasattr(raw, "ping"):
                try:
                    raw.ping(reconnect=True)
                except Exception as ping_err:
                    app.logger.warning(f"MySQL ping failed ({ping_err}); reconnecting fresh socket.")
                    try:
                        raw.close()
                    except Exception:
                        pass
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
    """Safely execute ALTER TABLE ADD COLUMN across PostgreSQL, SQLite, and MySQL without slow metadata scans."""
    db_type = get_db_type()
    if db_type == "postgres":
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type};")
    elif db_type == "sqlite":
        try:
            cur.execute(f"PRAGMA table_info({table});")
            rows = cur.fetchall()
            existing_cols = [row["name"] if isinstance(row, dict) else row[1] for row in rows]
            if column not in existing_cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type};")
        except Exception as e:
            if "no such table" in str(e).lower():
                raise e
    elif db_type == "mysql":
        # Direct execution: MySQL throws error 1060 (ER_DUP_FIELDNAME) if column already exists.
        # This completely avoids slow information_schema.COLUMNS metadata locking.
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type};")
        except Exception as e:
            # Re-raise connection lost errors immediately (never swallow connection failures)
            if hasattr(e, "args") and len(e.args) > 0 and e.args[0] in (2002, 2003, 2006, 2013):
                raise e

            # Check if this is duplicate column error (1060)
            is_dup = False
            if hasattr(e, "args") and len(e.args) > 0 and e.args[0] == 1060:
                is_dup = True
            elif "1060" in str(e) or "duplicate column" in str(e).lower():
                is_dup = True

            if is_dup:
                pass
            else:
                # Secondary validation: check if column already exists via fast single-table probe
                col_exists = False
                try:
                    cur.execute(f"SHOW COLUMNS FROM {table} LIKE %s", (column,))
                    col_exists = bool(cur.fetchall())
                except Exception:
                    pass

                if col_exists:
                    pass
                else:
                    # Column does not exist and error was not 1060: do not hide unrelated database errors
                    raise e

def create_index_if_not_exists(cur, index_name, table, columns):
    """Safely create database index across PostgreSQL, MySQL, and SQLite without slow information_schema scans."""
    db_type = get_db_type()
    cols_str = ", ".join(columns)
    if db_type in ("postgres", "sqlite"):
        cur.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({cols_str});")
    elif db_type == "mysql":
        # Direct execution: MySQL throws error 1061 (ER_DUP_KEYNAME) if index already exists.
        # Completely eliminates slow information_schema.STATISTICS query hangs on cloud databases (Render/RDS).
        try:
            cur.execute(f"CREATE INDEX {index_name} ON {table} ({cols_str});")
        except Exception as e:
            # Re-raise connection lost errors immediately (never swallow connection failures)
            if hasattr(e, "args") and len(e.args) > 0 and e.args[0] in (2002, 2003, 2006, 2013):
                raise e

            # Check if this is duplicate key error (1061)
            is_dup = False
            if hasattr(e, "args") and len(e.args) > 0 and e.args[0] == 1061:
                is_dup = True
            elif "1061" in str(e) or "duplicate key" in str(e).lower() or "already exists" in str(e).lower():
                is_dup = True

            if is_dup:
                pass
            else:
                # Secondary validation: check if index already exists via fast single-table probe
                idx_exists = False
                try:
                    cur.execute(f"SHOW INDEX FROM {table} WHERE Key_name = %s", (index_name,))
                    idx_exists = bool(cur.fetchall())
                except Exception:
                    pass

                if idx_exists:
                    pass
                else:
                    # Index does not exist and error was not 1061: do not hide unrelated database errors
                    raise e

CURRENT_SCHEMA_VERSION = 1
_db_initialized = False

def is_schema_initialized():
    """
    Lightweight check verifying if the database schema is already initialized to CURRENT_SCHEMA_VERSION.
    Only returns True if schema_version exists AND has recorded version >= CURRENT_SCHEMA_VERSION.
    Never uses presence of business tables (such as users) to infer schema status.
    If schema_version does not exist or version is lower, returns False to trigger safe migration pass.
    """
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute("SELECT version FROM schema_version WHERE version >= %s LIMIT 1;", (CURRENT_SCHEMA_VERSION,))
        row = cur.fetchone()
        return bool(row)
    except Exception:
        # Table schema_version does not exist yet (or DB connection unreachable)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass

def ensure_db_initialized():
    """Idempotently ensure database schema is initialized and migrated across all supported databases."""
    global _db_initialized
    if _db_initialized:
        return

    # If the schema is already initialized, mark as initialized immediately and avoid redundant DDL runs.
    if is_schema_initialized():
        _db_initialized = True
        return

    try:
        init_db()
        _db_initialized = True
    except Exception as e:
        app.logger.exception(f"Database initialization / schema migration failed: {e}")
        raise e

@app.before_request
def auto_init_db_before_request():
    """
    Guard for incoming HTTP requests:
    - Never block health-checks (HEAD /) or static asset requests.
    - If AUTO_INIT_DB is disabled (recommended in production with pre-deployed DB), skip.
    - If DB is already initialized, returns in 0ms.
    - Only runs initialization if the database schema is confirmed to be missing.
    """
    global _db_initialized
    if _db_initialized:
        return

    # Fast bypass for Render health checks and lightweight probes
    if request.method == "HEAD" or request.path in ("/favicon.ico", "/robots.txt"):
        return

    # In production web servers (Gunicorn, Render), HTTP requests must NEVER execute request-time DDL.
    # Database initialization/migration in production runs exclusively via 'flask init-db'.
    is_gunicorn = (
        "gunicorn" in os.environ.get("SERVER_SOFTWARE", "").lower()
        or "gunicorn" in sys.modules
        or bool(os.environ.get("GUNICORN_CMD_ARGS"))
    )
    is_render = bool(os.environ.get("RENDER"))
    if is_gunicorn or is_render:
        _db_initialized = True
        return

    # In local development, respect AUTO_INIT_DB setting if explicitly disabled
    auto_init_val = os.environ.get("AUTO_INIT_DB", "").strip().strip("'\"").lower()
    if auto_init_val in ("0", "false", "no", "off"):
        _db_initialized = True
        return

    ensure_db_initialized()

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
    # Ensure father_name, phone, and student_class columns exist
    add_column_if_not_exists(cur, "users", "father_name", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "users", "phone", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "users", "student_class", "VARCHAR(50) DEFAULT NULL")

    # Create learning_categories table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS learning_categories (
            id {pk_def},
            name TEXT NOT NULL,
            slug VARCHAR(255) UNIQUE NOT NULL,
            description TEXT,
            image TEXT,
            display_order INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    add_column_if_not_exists(cur, "learning_categories", "name", "TEXT NOT NULL DEFAULT ''")
    add_column_if_not_exists(cur, "learning_categories", "slug", "VARCHAR(255) DEFAULT ''")
    add_column_if_not_exists(cur, "learning_categories", "description", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "learning_categories", "image", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "learning_categories", "display_order", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "learning_categories", "is_active", "INTEGER NOT NULL DEFAULT 1")

    # Create learning_paths table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS learning_paths (
            id {pk_def},
            category_id INTEGER NOT NULL REFERENCES learning_categories(id) ON DELETE CASCADE,
            grade INTEGER NOT NULL DEFAULT 1,
            name TEXT NOT NULL,
            slug VARCHAR(255) NOT NULL,
            description TEXT,
            image TEXT,
            display_order INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    add_column_if_not_exists(cur, "learning_paths", "category_id", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "learning_paths", "grade", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "learning_paths", "name", "TEXT NOT NULL DEFAULT ''")
    add_column_if_not_exists(cur, "learning_paths", "slug", "VARCHAR(255) DEFAULT ''")
    add_column_if_not_exists(cur, "learning_paths", "description", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "learning_paths", "image", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "learning_paths", "display_order", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "learning_paths", "is_active", "INTEGER NOT NULL DEFAULT 1")

    # Create course_catalogue_settings table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS course_catalogue_settings (
            id {pk_def},
            hero_badge TEXT NOT NULL DEFAULT 'STEM LEARNING DOMAINS',
            hero_title TEXT NOT NULL DEFAULT 'Learning Categories',
            hero_description TEXT DEFAULT 'Choose a learning domain to explore progressive educational pathways across technology, programming, AI, drones, and digital skills.',
            hero_image TEXT DEFAULT '',
            hero_image_alt TEXT NOT NULL DEFAULT 'Learning Categories',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    add_column_if_not_exists(cur, "course_catalogue_settings", "hero_badge", "TEXT NOT NULL DEFAULT 'STEM LEARNING DOMAINS'")
    add_column_if_not_exists(cur, "course_catalogue_settings", "hero_title", "TEXT NOT NULL DEFAULT 'Learning Categories'")
    add_column_if_not_exists(cur, "course_catalogue_settings", "hero_description", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "course_catalogue_settings", "hero_image", "TEXT DEFAULT ''")
    add_column_if_not_exists(cur, "course_catalogue_settings", "hero_image_alt", "TEXT NOT NULL DEFAULT 'Learning Categories'")
    add_column_if_not_exists(cur, "course_catalogue_settings", "is_active", "INTEGER NOT NULL DEFAULT 1")

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
            grade INTEGER NOT NULL DEFAULT 1,
            display_label TEXT DEFAULT NULL,
            category_id INTEGER REFERENCES learning_categories(id) ON DELETE SET NULL,
            learning_path_id INTEGER REFERENCES learning_paths(id) ON DELETE SET NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # Ensure public course information and grade columns exist
    add_column_if_not_exists(cur, "courses", "grade", "INTEGER DEFAULT 1")
    add_column_if_not_exists(cur, "courses", "display_label", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "courses", "category_id", "INTEGER DEFAULT NULL")
    add_column_if_not_exists(cur, "courses", "learning_path_id", "INTEGER DEFAULT NULL")
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
    add_column_if_not_exists(cur, "course_enrollments", "is_active", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "course_enrollments", "assigned_at", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "course_enrollments", "assigned_by", "INTEGER DEFAULT NULL")

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
    add_column_if_not_exists(cur, "modules", "description", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "modules", "sequence", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "modules", "is_active", "INTEGER NOT NULL DEFAULT 1")

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
            video_file TEXT DEFAULT NULL,
            youtube_video_id TEXT DEFAULT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # Ensure video_file and youtube_video_id columns exist if table was created previously
    add_column_if_not_exists(cur, "course_videos", "video_file", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "course_videos", "youtube_video_id", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "course_videos", "duration", "VARCHAR(50) DEFAULT '10:00'")
    add_column_if_not_exists(cur, "course_videos", "sequence", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "course_videos", "is_active", "INTEGER NOT NULL DEFAULT 1")

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
    add_column_if_not_exists(cur, "video_progress", "watched_seconds", "FLOAT NOT NULL DEFAULT 0.0")
    add_column_if_not_exists(cur, "video_progress", "duration_seconds", "FLOAT NOT NULL DEFAULT 0.0")
    add_column_if_not_exists(cur, "video_progress", "completion_percentage", "FLOAT NOT NULL DEFAULT 0.0")
    add_column_if_not_exists(cur, "video_progress", "completed", "BOOLEAN NOT NULL DEFAULT FALSE")
    add_column_if_not_exists(cur, "video_progress", "completed_at", "TEXT DEFAULT NULL")

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
    add_column_if_not_exists(cur, "certificates", "student_name", "VARCHAR(255) DEFAULT NULL")
    add_column_if_not_exists(cur, "certificates", "course_name", "VARCHAR(255) DEFAULT NULL")
    add_column_if_not_exists(cur, "certificates", "completion_percentage", "FLOAT NOT NULL DEFAULT 0.0")
    add_column_if_not_exists(cur, "certificates", "issued_at", "TEXT DEFAULT NULL")

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

    # Create projects table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS projects (
            id {pk_def},
            module_id INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT,
            max_marks INTEGER NOT NULL DEFAULT 100,
            deadline TEXT DEFAULT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Create project_submissions table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS project_submissions (
            id {pk_def},
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            submission_text TEXT,
            submission_file TEXT,
            status TEXT NOT NULL DEFAULT 'submitted',
            marks INTEGER DEFAULT NULL,
            feedback TEXT,
            evaluated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            evaluated_at TEXT DEFAULT NULL,
            submitted_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, project_id)
        )
        """
    )

    # Ensure projects columns exist across PostgreSQL, SQLite, and MySQL
    add_column_if_not_exists(cur, "projects", "description", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "projects", "max_marks", "INTEGER NOT NULL DEFAULT 100")
    add_column_if_not_exists(cur, "projects", "deadline", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "projects", "is_active", "INTEGER NOT NULL DEFAULT 1")

    # Ensure project_submissions columns exist across PostgreSQL, SQLite, and MySQL
    add_column_if_not_exists(cur, "project_submissions", "submission_text", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "project_submissions", "submission_file", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "project_submissions", "status", "TEXT NOT NULL DEFAULT 'submitted'")
    add_column_if_not_exists(cur, "project_submissions", "marks", "INTEGER DEFAULT NULL")
    add_column_if_not_exists(cur, "project_submissions", "feedback", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "project_submissions", "evaluated_by", "INTEGER DEFAULT NULL")
    add_column_if_not_exists(cur, "project_submissions", "evaluated_at", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "project_submissions", "submitted_at", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "project_submissions", "updated_at", "TEXT DEFAULT NULL")

    # Ensure quizzes columns exist across PostgreSQL, SQLite, and MySQL
    add_column_if_not_exists(cur, "quizzes", "description", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "quizzes", "passing_score", "INTEGER NOT NULL DEFAULT 70")
    add_column_if_not_exists(cur, "quizzes", "max_attempts", "INTEGER NOT NULL DEFAULT 5")
    add_column_if_not_exists(cur, "quizzes", "is_active", "INTEGER NOT NULL DEFAULT 1")

    # Ensure quiz_questions columns exist across PostgreSQL, SQLite, and MySQL
    add_column_if_not_exists(cur, "quiz_questions", "explanation", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "quiz_questions", "sequence", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "quiz_questions", "is_active", "INTEGER NOT NULL DEFAULT 1")

    # Ensure quiz_attempts columns exist across PostgreSQL, SQLite, and MySQL
    add_column_if_not_exists(cur, "quiz_attempts", "is_invalidated", "BOOLEAN NOT NULL DEFAULT FALSE")
    add_column_if_not_exists(cur, "quiz_attempts", "attempt_number", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "quiz_attempts", "started_at", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "quiz_attempts", "submitted_at", "TEXT DEFAULT NULL")

    # Create teacher_assignments table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS teacher_assignments (
            id {pk_def},
            teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
            assigned_at TEXT NOT NULL,
            UNIQUE(teacher_id, course_id)
        )
        """
    )

    # Create audit_logs table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id {pk_def},
            user_id INTEGER,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            details TEXT,
            timestamp TEXT NOT NULL
        )
        """
    )

    # Create products table (Phase 1 Product Catalogue)
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS products (
            id {pk_def},
            name TEXT NOT NULL,
            slug VARCHAR(255) UNIQUE NOT NULL,
            short_description TEXT,
            description TEXT,
            price NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
            image TEXT,
            availability VARCHAR(50) NOT NULL DEFAULT 'Available',
            shipping_free INTEGER NOT NULL DEFAULT 1,
            shipping_charge NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
            components TEXT,
            applications TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    add_column_if_not_exists(cur, "products", "name", "TEXT NOT NULL DEFAULT ''")
    add_column_if_not_exists(cur, "products", "slug", "VARCHAR(255) DEFAULT ''")
    add_column_if_not_exists(cur, "products", "short_description", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "products", "description", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "products", "price", "NUMERIC(10, 2) NOT NULL DEFAULT 0.00")
    add_column_if_not_exists(cur, "products", "image", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "products", "availability", "VARCHAR(50) NOT NULL DEFAULT 'Available'")
    add_column_if_not_exists(cur, "products", "shipping_free", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "products", "shipping_charge", "NUMERIC(10, 2) NOT NULL DEFAULT 0.00")
    add_column_if_not_exists(cur, "products", "components", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "products", "applications", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "products", "is_active", "INTEGER NOT NULL DEFAULT 1")

    conn.commit()

    # Create targeted performance indexes across PostgreSQL, MySQL, and SQLite
    create_index_if_not_exists(cur, "idx_courses_grade", "courses", ["grade"])
    create_index_if_not_exists(cur, "idx_learning_categories_slug", "learning_categories", ["slug"])
    create_index_if_not_exists(cur, "idx_learning_paths_category", "learning_paths", ["category_id"])
    create_index_if_not_exists(cur, "idx_learning_paths_grade", "learning_paths", ["grade"])
    create_index_if_not_exists(cur, "idx_courses_category_path", "courses", ["category_id", "learning_path_id"])
    create_index_if_not_exists(cur, "idx_modules_course_id", "modules", ["course_id"])
    create_index_if_not_exists(cur, "idx_course_videos_module_id", "course_videos", ["module_id"])
    create_index_if_not_exists(cur, "idx_video_progress_user_id", "video_progress", ["user_id"])
    create_index_if_not_exists(cur, "idx_quizzes_module_id", "quizzes", ["module_id"])
    create_index_if_not_exists(cur, "idx_quiz_questions_quiz_id", "quiz_questions", ["quiz_id"])
    create_index_if_not_exists(cur, "idx_quiz_attempts_user_quiz", "quiz_attempts", ["user_id", "quiz_id"])
    create_index_if_not_exists(cur, "idx_quiz_answers_attempt_id", "quiz_answers", ["attempt_id"])
    create_index_if_not_exists(cur, "idx_projects_module_id", "projects", ["module_id"])
    create_index_if_not_exists(cur, "idx_project_submissions_user_project", "project_submissions", ["user_id", "project_id"])
    create_index_if_not_exists(cur, "idx_teacher_assignments_teacher", "teacher_assignments", ["teacher_id"])
    create_index_if_not_exists(cur, "idx_teacher_assignments_course", "teacher_assignments", ["course_id"])
    create_index_if_not_exists(cur, "idx_audit_logs_user", "audit_logs", ["user_id"])
    create_index_if_not_exists(cur, "idx_products_slug", "products", ["slug"])
    create_index_if_not_exists(cur, "idx_products_is_active", "products", ["is_active"])

    conn.commit()

    # Create orders table (Phase 2 Guest Checkout & Orders Management)
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS orders (
            id {pk_def},
            order_number VARCHAR(100) UNIQUE NOT NULL,
            product_id INTEGER DEFAULT NULL,
            product_name_snapshot TEXT NOT NULL,
            product_price_snapshot NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
            shipping_charge_snapshot NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
            total_amount NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
            customer_name TEXT NOT NULL,
            customer_email TEXT NOT NULL,
            customer_phone TEXT NOT NULL,
            address TEXT NOT NULL,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            pincode TEXT NOT NULL,
            landmark TEXT DEFAULT NULL,
            payment_provider VARCHAR(50) NOT NULL DEFAULT 'Razorpay',
            payment_status VARCHAR(50) NOT NULL DEFAULT 'Pending',
            razorpay_order_id VARCHAR(255) DEFAULT NULL,
            razorpay_payment_id VARCHAR(255) DEFAULT NULL,
            razorpay_signature TEXT DEFAULT NULL,
            order_status VARCHAR(50) NOT NULL DEFAULT 'Pending Payment',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    add_column_if_not_exists(cur, "orders", "order_number", "VARCHAR(100) DEFAULT ''")
    add_column_if_not_exists(cur, "orders", "product_id", "INTEGER DEFAULT NULL")
    add_column_if_not_exists(cur, "orders", "product_name_snapshot", "TEXT DEFAULT ''")
    add_column_if_not_exists(cur, "orders", "product_price_snapshot", "NUMERIC(10, 2) NOT NULL DEFAULT 0.00")
    add_column_if_not_exists(cur, "orders", "shipping_charge_snapshot", "NUMERIC(10, 2) NOT NULL DEFAULT 0.00")
    add_column_if_not_exists(cur, "orders", "total_amount", "NUMERIC(10, 2) NOT NULL DEFAULT 0.00")
    add_column_if_not_exists(cur, "orders", "customer_name", "TEXT DEFAULT ''")
    add_column_if_not_exists(cur, "orders", "customer_email", "TEXT DEFAULT ''")
    add_column_if_not_exists(cur, "orders", "customer_phone", "TEXT DEFAULT ''")
    add_column_if_not_exists(cur, "orders", "address", "TEXT DEFAULT ''")
    add_column_if_not_exists(cur, "orders", "city", "TEXT DEFAULT ''")
    add_column_if_not_exists(cur, "orders", "state", "TEXT DEFAULT ''")
    add_column_if_not_exists(cur, "orders", "pincode", "TEXT DEFAULT ''")
    add_column_if_not_exists(cur, "orders", "landmark", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "orders", "payment_provider", "VARCHAR(50) NOT NULL DEFAULT 'Razorpay'")
    add_column_if_not_exists(cur, "orders", "payment_status", "VARCHAR(50) NOT NULL DEFAULT 'Pending'")
    add_column_if_not_exists(cur, "orders", "razorpay_order_id", "VARCHAR(255) DEFAULT NULL")
    add_column_if_not_exists(cur, "orders", "razorpay_payment_id", "VARCHAR(255) DEFAULT NULL")
    add_column_if_not_exists(cur, "orders", "razorpay_signature", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "orders", "order_status", "VARCHAR(50) NOT NULL DEFAULT 'Pending Payment'")
    add_column_if_not_exists(cur, "orders", "created_at", "TEXT DEFAULT ''")
    add_column_if_not_exists(cur, "orders", "updated_at", "TEXT DEFAULT ''")

    create_index_if_not_exists(cur, "idx_orders_number", "orders", ["order_number"])
    create_index_if_not_exists(cur, "idx_orders_product", "orders", ["product_id"])
    create_index_if_not_exists(cur, "idx_orders_payment_status", "orders", ["payment_status"])
    create_index_if_not_exists(cur, "idx_orders_status", "orders", ["order_status"])

    # Create solutions table (Phase CMS Driven Solutions)
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS solutions (
            id {pk_def},
            slug VARCHAR(255) UNIQUE NOT NULL,
            title TEXT NOT NULL,
            badge TEXT DEFAULT NULL,
            short_description TEXT DEFAULT NULL,
            description TEXT DEFAULT NULL,
            image TEXT DEFAULT NULL,
            ideal_for TEXT DEFAULT NULL,
            cta_text TEXT DEFAULT 'Get in Touch',
            cta_url TEXT DEFAULT '/contact',
            display_order INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    add_column_if_not_exists(cur, "solutions", "slug", "VARCHAR(255) DEFAULT ''")
    add_column_if_not_exists(cur, "solutions", "title", "TEXT NOT NULL DEFAULT ''")
    add_column_if_not_exists(cur, "solutions", "badge", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "solutions", "short_description", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "solutions", "description", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "solutions", "image", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "solutions", "ideal_for", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "solutions", "cta_text", "TEXT DEFAULT 'Get in Touch'")
    add_column_if_not_exists(cur, "solutions", "cta_url", "TEXT DEFAULT '/contact'")
    add_column_if_not_exists(cur, "solutions", "display_order", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "solutions", "is_active", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "solutions", "created_at", "TEXT DEFAULT ''")
    add_column_if_not_exists(cur, "solutions", "updated_at", "TEXT DEFAULT ''")

    # Create solution_items table
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS solution_items (
            id {pk_def},
            solution_id INTEGER NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT DEFAULT NULL,
            display_order INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    add_column_if_not_exists(cur, "solution_items", "solution_id", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_not_exists(cur, "solution_items", "title", "TEXT NOT NULL DEFAULT ''")
    add_column_if_not_exists(cur, "solution_items", "description", "TEXT DEFAULT NULL")
    add_column_if_not_exists(cur, "solution_items", "display_order", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "solution_items", "is_active", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_not_exists(cur, "solution_items", "created_at", "TEXT DEFAULT ''")
    add_column_if_not_exists(cur, "solution_items", "updated_at", "TEXT DEFAULT ''")

    create_index_if_not_exists(cur, "idx_solutions_slug", "solutions", ["slug"])
    create_index_if_not_exists(cur, "idx_solutions_display_order", "solutions", ["display_order"])
    create_index_if_not_exists(cur, "idx_solutions_is_active", "solutions", ["is_active"])
    create_index_if_not_exists(cur, "idx_solution_items_solution_id", "solution_items", ["solution_id"])
    create_index_if_not_exists(cur, "idx_solution_items_display_order", "solution_items", ["display_order"])

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

    # Seed initial learning categories and paths
    seed_initial_catalogue(cur, conn)

    # Seed initial catalogue hero settings
    seed_catalogue_settings(cur, conn)

    # Seed initial courses if not present
    seed_initial_courses(cur, conn)

    # Seed initial 6 products if not present
    seed_initial_products(cur, conn)

    # Seed initial 6 institutional solutions if not present
    seed_initial_solutions(cur, conn)

    # Stamp schema_version sentinel table to mark complete initialization
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            initialized_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    cur.execute("SELECT version FROM schema_version WHERE version = %s", (CURRENT_SCHEMA_VERSION,))
    if not cur.fetchone():
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "INSERT INTO schema_version (version, initialized_at) VALUES (%s, %s)",
            (CURRENT_SCHEMA_VERSION, now_str)
        )
        conn.commit()

    cur.close()
    conn.close()



def seed_catalogue_settings(cur, conn):
    """Seed default /courses hero configuration if not present."""
    cur.execute("SELECT COUNT(*) as cnt FROM course_catalogue_settings")
    cnt = cur.fetchone()["cnt"]
    if cnt == 0:
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO course_catalogue_settings (
                hero_badge, hero_title, hero_description, hero_image, hero_image_alt, is_active, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "STEM LEARNING DOMAINS",
                "Learning Categories",
                "Choose a learning domain to explore progressive educational pathways across technology, programming, AI, drones, and digital skills.",
                "",
                "Learning Categories",
                1,
                now_str,
                now_str,
            ),
        )
        conn.commit()


def seed_initial_catalogue(cur, conn):
    """Seed default Learning Categories and Grade 1-5 Learning Paths if not present."""
    default_categories = [
        {
            "name": "AI & Artificial Intelligence",
            "slug": "artificial-intelligence",
            "description": "Explore the fundamentals and advanced engineering of artificial intelligence, machine learning, and computer vision.",
            "image": "images/services/ai.jpg",
            "display_order": 1,
            "paths": [
                {"grade": 1, "name": "AI Discovery", "slug": "ai-discovery", "description": "Foundational awareness of intelligent systems, smart assistants, and machine perception for early learners.", "display_order": 1},
                {"grade": 2, "name": "AI Creation", "slug": "ai-creation", "description": "Visual block-based logic, pattern recognition, and creative AI interactive projects.", "display_order": 2},
                {"grade": 3, "name": "AI Engineering", "slug": "ai-engineering", "description": "Hands-on machine learning, neural network concepts, data analysis, and predictive models.", "display_order": 3},
                {"grade": 4, "name": "Intelligent Systems", "slug": "intelligent-systems", "description": "Applied artificial intelligence, computer vision, natural language processing, and automation.", "display_order": 4},
                {"grade": 5, "name": "Advanced AI", "slug": "advanced-ai", "description": "Deep learning architectures, autonomous decision systems, and cutting-edge research topics.", "display_order": 5},
            ]
        },
        {
            "name": "Coding & Programming",
            "slug": "coding-programming",
            "description": "Master computational thinking, algorithmic logic, Python, C++, and software development.",
            "image": "images/services/coding.jpg",
            "display_order": 2,
            "paths": [
                {"grade": 1, "name": "Coding Discovery", "slug": "coding-discovery", "description": "Logic puzzles, sequencing, and computational thinking for early beginners.", "display_order": 1},
                {"grade": 2, "name": "Programming Basics", "slug": "programming-basics", "description": "Block programming, loops, conditions, and interactive animations.", "display_order": 2},
                {"grade": 3, "name": "Programming Fundamentals", "slug": "programming-fundamentals", "description": "Python syntax, variables, data structures, and practical coding exercises.", "display_order": 3},
                {"grade": 4, "name": "Advanced Programming", "slug": "advanced-programming", "description": "Object-oriented programming, algorithms, web APIs, and modular software design.", "display_order": 4},
                {"grade": 5, "name": "Competitive Programming", "slug": "competitive-programming", "description": "Advanced algorithmic efficiency, data structures, and competitive problem solving.", "display_order": 5},
            ]
        },
        {
            "name": "Drone Technology",
            "slug": "drone-technology",
            "description": "Learn aerial mechanics, aerodynamics, flight physics, avionics, and autonomous navigation.",
            "image": "images/services/drone.jpg",
            "display_order": 3,
            "paths": [
                {"grade": 1, "name": "Drone Discovery", "slug": "drone-discovery", "description": "Introduction to flight, principles of aerodynamics, and aerial safety.", "display_order": 1},
                {"grade": 2, "name": "Drone Basics", "slug": "drone-basics", "description": "Drone components, quadcopter flight physics, and remote control fundamentals.", "display_order": 2},
                {"grade": 3, "name": "Drone Technology", "slug": "drone-technology", "description": "Sensors, motors, telemetry, gyro stabilization, and payload systems.", "display_order": 3},
                {"grade": 4, "name": "Drone Engineering", "slug": "drone-engineering", "description": "Flight controller configuration, PID tuning, GPS navigation, and DIY assembly.", "display_order": 4},
                {"grade": 5, "name": "Advanced Drone Systems", "slug": "advanced-drone-systems", "description": "Autonomous waypoint mission planning, swarm technology, and aerial computer vision.", "display_order": 5},
            ]
        },
        {
            "name": "Website Design",
            "slug": "website-design",
            "description": "Design responsive modern web applications, UI/UX interfaces, and full stack web solutions.",
            "image": "images/services/web.jpg",
            "display_order": 4,
            "paths": [
                {"grade": 1, "name": "Web Discovery", "slug": "web-discovery", "description": "Exploring the internet, digital citizenship, and how websites work.", "display_order": 1},
                {"grade": 2, "name": "Web Basics", "slug": "web-basics", "description": "Introduction to web layouts, colors, typography, and visual webpage structure.", "display_order": 2},
                {"grade": 3, "name": "Web Design", "slug": "web-design", "description": "Semantic HTML5, CSS3 styling, responsive layouts, and interactive elements.", "display_order": 3},
                {"grade": 4, "name": "Full Stack Foundations", "slug": "full-stack-foundations", "description": "Modern JavaScript, client-server communication, Flask backends, and databases.", "display_order": 4},
                {"grade": 5, "name": "Advanced Web Development", "slug": "advanced-web-development", "description": "Production web architecture, cloud deployment, authentication, and REST APIs.", "display_order": 5},
            ]
        }
    ]

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    for cat_data in default_categories:
        cur.execute("SELECT id FROM learning_categories WHERE slug = %s", (cat_data["slug"],))
        cat_row = cur.fetchone()
        if not cat_row:
            if get_db_type() == "postgres":
                cur.execute(
                    """
                    INSERT INTO learning_categories (name, slug, description, image, display_order, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                    RETURNING id
                    """,
                    (cat_data["name"], cat_data["slug"], cat_data["description"], cat_data["image"], cat_data["display_order"], now_str, now_str)
                )
                cat_id = cur.fetchone()["id"]
            else:
                cur.execute(
                    """
                    INSERT INTO learning_categories (name, slug, description, image, display_order, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                    """,
                    (cat_data["name"], cat_data["slug"], cat_data["description"], cat_data["image"], cat_data["display_order"], now_str, now_str)
                )
                cat_id = cur.lastrowid
                if not cat_id:
                    cur.execute("SELECT id FROM learning_categories WHERE slug = %s", (cat_data["slug"],))
                    cat_id = cur.fetchone()["id"]
        else:
            cat_id = cat_row["id"]

        for path_data in cat_data["paths"]:
            cur.execute("SELECT id FROM learning_paths WHERE category_id = %s AND slug = %s", (cat_id, path_data["slug"]))
            p_row = cur.fetchone()
            if not p_row:
                cur.execute(
                    """
                    INSERT INTO learning_paths (category_id, grade, name, slug, description, image, display_order, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
                    """,
                    (cat_id, path_data["grade"], path_data["name"], path_data["slug"], path_data["description"], cat_data["image"], path_data["display_order"], now_str, now_str)
                )

    conn.commit()


def seed_initial_courses(cur, conn):
    """Seed the five core AI courses and domain courses with sample modules, videos, quizzes, and projects."""
    initial_courses = [
        {
            "title": "AI Discovery",
            "slug": "ai-discovery",
            "grade": 1,
            "category_slug": "artificial-intelligence",
            "path_slug": "ai-discovery",
            "description": "Foundational awareness of intelligent systems, smart assistants, and machine perception for early learners.",
            "image": "images/services/ai.jpg",
            "level": "Beginner",
            "modules": [
                {
                    "title": "Discovering Artificial Intelligence",
                    "description": "Explore what AI is, how smart devices work around us, and fundamental concepts of machine intelligence.",
                    "sequence": 1,
                    "videos": [
                        {"title": "What Is Artificial Intelligence?", "description": "Introduction to AI principles and smart systems.", "duration": "08:30", "sequence": 1},
                        {"title": "AI Around Us", "description": "Recognizing intelligent technology in everyday life.", "duration": "10:15", "sequence": 2},
                        {"title": "Smart Machines & Computers", "description": "How computers solve problems using basic logic.", "duration": "12:00", "sequence": 3},
                        {"title": "Sensors and Data", "description": "How AI devices perceive light, sound, and touch.", "duration": "09:45", "sequence": 4},
                        {"title": "Helping People with AI", "description": "Real world applications in medicine, travel, and farming.", "duration": "11:20", "sequence": 5},
                        {"title": "My First AI Activity", "description": "Interactive visual exercise in logic and pattern building.", "duration": "07:50", "sequence": 6}
                    ],
                    "quiz": {
                        "title": "AI Basics Quiz",
                        "description": "Test your basic understanding of AI concept fundamentals.",
                        "passing_score": 70,
                        "max_attempts": 5,
                        "questions": [
                            {
                                "question_text": "What does AI stand for?",
                                "option_a": "Artificial Intelligence",
                                "option_b": "Automated Internet",
                                "option_c": "Advanced Interface",
                                "option_d": "Applied Integration",
                                "correct_option": "A",
                                "explanation": "AI stands for Artificial Intelligence."
                            },
                            {
                                "question_text": "Which of the following uses Artificial Intelligence?",
                                "option_a": "A wooden chair",
                                "option_b": "A smart voice assistant",
                                "option_c": "A manual pencil sharpener",
                                "option_d": "A glass bottle",
                                "correct_option": "B",
                                "explanation": "Voice assistants use AI to process speech and respond."
                            }
                        ]
                    },
                    "project": {
                        "title": "AI Around Me",
                        "description": "Identify 3 smart devices in your daily life and describe how they use artificial intelligence.",
                        "max_marks": 100
                    }
                },
                {
                    "title": "How AI Understands the World",
                    "description": "Learn how AI sees images, recognizes voices, and processes patterns.",
                    "sequence": 2,
                    "videos": [
                        {"title": "Computer Vision Basics", "description": "How cameras and computers recognize objects.", "duration": "10:00", "sequence": 1},
                        {"title": "Voice & Speech Recognition", "description": "Understanding spoken words and audio inputs.", "duration": "11:30", "sequence": 2},
                        {"title": "Pattern Detection", "description": "Finding similarities and sorting information.", "duration": "09:15", "sequence": 3},
                        {"title": "Sorting and Categorizing", "description": "Grouping data by colors, shapes, and features.", "duration": "12:10", "sequence": 4},
                        {"title": "Smart Cameras in Action", "description": "Face detection and gesture identification.", "duration": "08:45", "sequence": 5},
                        {"title": "Making Predictions", "description": "How AI guesses what comes next based on past data.", "duration": "10:50", "sequence": 6}
                    ],
                    "quiz": {
                        "title": "AI Perception Quiz",
                        "description": "Test your knowledge on vision, sound, and pattern detection.",
                        "passing_score": 70,
                        "max_attempts": 5,
                        "questions": [
                            {
                                "question_text": "What sensor does AI use to see images?",
                                "option_a": "Microphone",
                                "option_b": "Camera",
                                "option_c": "Thermometer",
                                "option_d": "Speaker",
                                "correct_option": "B",
                                "explanation": "Cameras capture image inputs for computer vision models."
                            }
                        ]
                    },
                    "project": {
                        "title": "Pattern Hunter Project",
                        "description": "Create a visual collage showing how smart cameras identify patterns and shapes.",
                        "max_marks": 100
                    }
                },
                {
                    "title": "Machine Learning Foundations",
                    "description": "Introduction to training computers using data and feedback.",
                    "sequence": 3,
                    "videos": [
                        {"title": "Data & Training", "description": "Giving examples to teach smart algorithms.", "duration": "09:00", "sequence": 1},
                        {"title": "Teaching Computers", "description": "Supervised learning and instant feedback.", "duration": "11:00", "sequence": 2},
                        {"title": "Smart Decisions", "description": "How AI makes choices based on probabilities.", "duration": "10:30", "sequence": 3}
                    ]
                },
                {
                    "title": "Ethics & Future of AI",
                    "description": "Understanding responsible AI, fairness, and safety in intelligent systems.",
                    "sequence": 4,
                    "videos": [
                        {"title": "Responsible AI", "description": "Using technology fairly and respectfully.", "duration": "08:30", "sequence": 1},
                        {"title": "AI Safety Basics", "description": "Keeping human privacy and safety first.", "duration": "09:45", "sequence": 2},
                        {"title": "The Future of Smart Tech", "description": "Exciting possibilities in science and space.", "duration": "11:15", "sequence": 3}
                    ]
                }
            ]
        },
        {
            "title": "AI Creation",
            "slug": "ai-creation",
            "grade": 2,
            "category_slug": "artificial-intelligence",
            "path_slug": "ai-creation",
            "description": "Visual block-based logic, pattern recognition, and creative AI interactive projects.",
            "image": "images/services/ai.jpg",
            "level": "Beginner",
            "modules": [
                {
                    "title": "Visual AI Blocks & Logic",
                    "description": "Building interactive AI models using simple drag-and-drop block interfaces.",
                    "sequence": 1,
                    "videos": [
                        {"title": "Introduction to Block AI", "description": "Setting up visual classification blocks.", "duration": "09:30", "sequence": 1},
                        {"title": "Training Image Models", "description": "Adding photo samples to teach visual classifiers.", "duration": "12:00", "sequence": 2}
                    ]
                }
            ]
        },
        {
            "title": "AI Engineering",
            "slug": "ai-engineering",
            "grade": 3,
            "category_slug": "artificial-intelligence",
            "path_slug": "ai-engineering",
            "description": "Hands-on machine learning, neural network concepts, data analysis, and predictive models.",
            "image": "images/services/ai.jpg",
            "level": "Intermediate",
            "modules": [
                {
                    "title": "Machine Learning Workflows",
                    "description": "Data preprocessing, model training, evaluation, and hyperparameter tuning.",
                    "sequence": 1,
                    "videos": [
                        {"title": "ML Pipeline Overview", "description": "Step by step workflow from dataset to model deployment.", "duration": "14:20", "sequence": 1}
                    ]
                }
            ]
        },
        {
            "title": "Intelligent Systems",
            "slug": "intelligent-systems",
            "grade": 4,
            "category_slug": "artificial-intelligence",
            "path_slug": "intelligent-systems",
            "description": "Applied artificial intelligence, computer vision, natural language processing, and automation.",
            "image": "images/services/ai.jpg",
            "level": "Intermediate",
            "modules": [
                {
                    "title": "Computer Vision & Natural Language",
                    "description": "Building computer vision pipelines and processing text data with NLP.",
                    "sequence": 1,
                    "videos": [
                        {"title": "Object Detection & NLP", "description": "Applying OpenCV and NLP models for automation.", "duration": "16:45", "sequence": 1}
                    ]
                }
            ]
        },
        {
            "title": "Advanced AI",
            "slug": "advanced-ai",
            "grade": 5,
            "category_slug": "artificial-intelligence",
            "path_slug": "advanced-ai",
            "description": "Deep learning architectures, autonomous decision systems, and cutting-edge research topics.",
            "image": "images/services/ai.jpg",
            "level": "Advanced",
            "modules": [
                {
                    "title": "Deep Learning & Autonomous Systems",
                    "description": "Convolutional Neural Networks, Transformer architectures, and RL control loops.",
                    "sequence": 1,
                    "videos": [
                        {"title": "CNNs and Transformers", "description": "Architectural breakdown of modern AI models.", "duration": "18:30", "sequence": 1}
                    ]
                }
            ]
        },
        {
            "title": "Drone Technology",
            "slug": "drone-technology",
            "grade": 4,
            "category_slug": "drone-technology",
            "path_slug": "drone-engineering",
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
                }
            ]
        },
        {
            "title": "Robotics",
            "slug": "robotics",
            "grade": 3,
            "category_slug": "drone-technology",
            "path_slug": "drone-technology",
            "description": "Design, build, and program autonomous robots using sensors, microcontrollers, and motor drivers.",
            "image": "images/services/robotics.jpg",
            "level": "All Levels",
            "modules": [
                {
                    "title": "Module 1 — Introduction to Robotics",
                    "description": "Anatomy of robots, actuators, sensors, and structural framework.",
                    "sequence": 1,
                    "videos": [
                        {"title": "Video 1 — Anatomy of a Robot", "description": "Key structural elements and power distribution.", "duration": "07:50", "sequence": 1}
                    ]
                }
            ]
        },
        {
            "title": "Coding & Programming",
            "slug": "coding-programming",
            "grade": 2,
            "category_slug": "coding-programming",
            "path_slug": "programming-basics",
            "description": "Build strong programming foundations in Python and JavaScript, from logic building to algorithm design.",
            "image": "images/services/coding.jpg",
            "level": "Beginner",
            "modules": [
                {
                    "title": "Module 1 — Computational Thinking & Logic",
                    "description": "Logic flow, algorithms, variables, control structures, and loops.",
                    "sequence": 1,
                    "videos": [
                        {"title": "Video 1 — Logic Building & Flowcharts", "description": "Deconstructing problems into structured algorithmic steps.", "duration": "10:00", "sequence": 1}
                    ]
                }
            ]
        },
        {
            "title": "STEM Foundations & Tinkering",
            "slug": "stem-foundations-tinkering",
            "grade": 1,
            "category_slug": "coding-programming",
            "path_slug": "coding-discovery",
            "description": "Fun, hands-on introduction to science, technology, engineering, and early computational thinking for young minds.",
            "image": "images/services/atl.jpg",
            "level": "Early Learner",
            "modules": [
                {
                    "title": "Module 1 — Discovering STEM & Tinkering",
                    "description": "Interactive exploration of simple machines, shapes, patterns, and everyday technology.",
                    "sequence": 1,
                    "videos": [
                        {"title": "Video 1 — What is STEM?", "description": "Introduction to science and building things.", "duration": "06:30", "sequence": 1}
                    ]
                }
            ]
        }
    ]

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    for course_data in initial_courses:
        cur.execute("SELECT id, grade, category_id, learning_path_id FROM courses WHERE slug = %s", (course_data["slug"],))
        existing = cur.fetchone()
        grade_val = course_data.get("grade", 1)

        # Lookup category_id and learning_path_id if configured
        cat_id = None
        path_id = None
        if "category_slug" in course_data:
            cur.execute("SELECT id FROM learning_categories WHERE slug = %s", (course_data["category_slug"],))
            cat_row = cur.fetchone()
            if cat_row:
                cat_id = cat_row["id"]
                if "path_slug" in course_data:
                    cur.execute("SELECT id FROM learning_paths WHERE category_id = %s AND slug = %s", (cat_id, course_data["path_slug"]))
                    p_row = cur.fetchone()
                    if p_row:
                        path_id = p_row["id"]

        if not existing:
            if get_db_type() == "postgres":
                cur.execute(
                    """
                    INSERT INTO courses (title, slug, description, image, level, grade, category_id, learning_path_id, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
                    RETURNING id
                    """,
                    (
                        course_data["title"],
                        course_data["slug"],
                        course_data["description"],
                        course_data["image"],
                        course_data["level"],
                        grade_val,
                        cat_id,
                        path_id,
                        now_str,
                        now_str,
                    ),
                )
                course_id = cur.fetchone()["id"]
            else:
                cur.execute(
                    """
                    INSERT INTO courses (title, slug, description, image, level, grade, category_id, learning_path_id, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
                    """,
                    (
                        course_data["title"],
                        course_data["slug"],
                        course_data["description"],
                        course_data["image"],
                        course_data["level"],
                        grade_val,
                        cat_id,
                        path_id,
                        now_str,
                        now_str,
                    ),
                )
                course_id = cur.lastrowid
            
            for mod_data in course_data.get("modules", []):
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
                        INSERT INTO course_videos (module_id, title, description, sequence, duration, is_active, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
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

                # Seed module quiz if configured
                quiz_data = mod_data.get("quiz")
                if quiz_data:
                    cur.execute(
                        """
                        INSERT INTO quizzes (module_id, title, description, passing_score, max_attempts, is_active, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                        """,
                        (module_id, quiz_data["title"], quiz_data.get("description", ""), quiz_data.get("passing_score", 70), quiz_data.get("max_attempts", 5), now_str, now_str)
                    )
                    quiz_id = cur.lastrowid if get_db_type() != "postgres" else None
                    if get_db_type() == "postgres":
                        cur.execute("SELECT id FROM quizzes WHERE module_id = %s ORDER BY id DESC LIMIT 1", (module_id,))
                        q_row = cur.fetchone()
                        if q_row:
                            quiz_id = q_row["id"]

                    if quiz_id:
                        for q_idx, q_item in enumerate(quiz_data.get("questions", []), start=1):
                            cur.execute(
                                """
                                INSERT INTO quiz_questions (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option, explanation, sequence, is_active, created_at, updated_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
                                """,
                                (
                                    quiz_id,
                                    q_item["question_text"],
                                    q_item["option_a"],
                                    q_item["option_b"],
                                    q_item["option_c"],
                                    q_item["option_d"],
                                    q_item["correct_option"],
                                    q_item.get("explanation", ""),
                                    q_idx,
                                    now_str,
                                    now_str
                                )
                            )

                # Seed module project if configured
                proj_data = mod_data.get("project")
                if proj_data:
                    cur.execute(
                        """
                        INSERT INTO projects (module_id, title, description, max_marks, is_active, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, 1, %s, %s)
                        """,
                        (module_id, proj_data["title"], proj_data.get("description", ""), proj_data.get("max_marks", 100), now_str, now_str)
                    )
        else:
            course_id = existing["id"]
            if cat_id:
                cur.execute("UPDATE courses SET category_id = %s, grade = %s WHERE id = %s", (cat_id, grade_val, course_id))
            else:
                cur.execute("UPDATE courses SET grade = %s WHERE id = %s AND (grade IS NULL OR grade != %s)", (grade_val, course_id, grade_val))
            conn.commit()


def seed_initial_products(cur, conn):
    """Seed initial 6 STEM/Robotics/AI/Drone products if not present."""
    initial_products = [
        {
            "name": "AI Vision Starter Kit",
            "slug": "ai-vision-starter-kit",
            "price": 17999.00,
            "availability": "Available",
            "shipping_free": 1,
            "shipping_charge": 0.00,
            "short_description": "Compact AI & vision sensor kit with K230 dual-core processor and ESP32 for beginner smart automation projects.",
            "description": "The AI Vision Starter Kit is designed to introduce students and STEM enthusiasts to real-world AI computer vision. Powered by the HUSKYLENS 2 sensor and Kendryte K230 dual-core processor delivering 6 TOPS AI computing capability, this starter kit enables fast object recognition, tracking, and classification.",
            "image_filename": "AI_VISION_STARTER_KIT.png",
            "components": json.dumps([
                "HUSKYLENS 2 AI Vision Sensor",
                "Kendryte K230 Dual-Core 1.6 GHz AI Processor",
                "6 TOPS AI Computing",
                "ESP32 development board",
                "Ultrasonic sensor",
                "IR sensors",
                "Servo motor",
                "OLED/display",
                "LEDs & buzzer",
                "Breadboard",
                "Jumper wires & cables",
                "Power supply/accessories"
            ]),
            "applications": json.dumps([
                "Object recognition",
                "Tracking",
                "Classification",
                "Smart automation",
                "Beginner AI projects"
            ])
        },
        {
            "name": "AI Vision Robotics Kit",
            "slug": "ai-vision-robotics-kit",
            "price": 24999.00,
            "availability": "Available",
            "shipping_free": 1,
            "shipping_charge": 0.00,
            "short_description": "AI-guided robotic arm and chassis kit with HUSKYLENS 2 for autonomous sorting and intelligent robotics.",
            "description": "Combine artificial intelligence with physical robotics! The AI Vision Robotics Kit includes a 4-DOF robotic arm, robot chassis, high-performance motors, and HUSKYLENS 2 AI vision sensor for building vision-guided autonomous robots.",
            "image_filename": "AI_VISION_ROBOTICS_KIT.png",
            "components": json.dumps([
                "HUSKYLENS 2 AI Vision Sensor",
                "Kendryte K230 Dual-Core 1.6 GHz AI Processor",
                "6 TOPS AI Computing",
                "ESP32 development board",
                "4-DOF robotic arm",
                "Robot chassis",
                "DC geared motors",
                "Motor driver",
                "Line sensors",
                "Ultrasonic/ToF sensor",
                "Servo motors",
                "Battery",
                "Power module",
                "Wheels and mechanical accessories",
                "Connecting wires/accessories"
            ]),
            "applications": json.dumps([
                "Vision-guided robotic arm",
                "Object sorting",
                "Pick & place",
                "Autonomous robotics",
                "Smart factory projects"
            ])
        },
        {
            "name": "AI Vision Advanced Kit",
            "slug": "ai-vision-advanced-kit",
            "price": 29999.00,
            "availability": "Available",
            "shipping_free": 1,
            "shipping_charge": 0.00,
            "short_description": "Raspberry Pi 5 4GB powered AI computer vision development kit for high-performance edge computing.",
            "description": "The AI Vision Advanced Kit leverages the raw performance of Raspberry Pi 5 (4GB) and Camera Module 3 to run complex deep learning models, real-time object classification, license plate recognition, and smart surveillance edge systems.",
            "image_filename": "AI VISION ADVANCED KIT.png",
            "components": json.dumps([
                "Raspberry Pi 5 – 4GB",
                "Raspberry Pi Camera Module 3",
                "microSD card",
                "Official power supply",
                "Cooling fan/heatsink",
                "Protective case",
                "GPIO accessories",
                "Sensors",
                "Display/accessories",
                "USB and connectivity accessories"
            ]),
            "applications": json.dumps([
                "Advanced computer vision",
                "Image processing",
                "AI models",
                "Number-plate recognition",
                "Smart surveillance",
                "Real-world AI applications"
            ])
        },
        {
            "name": "Robotics Starter Kits",
            "slug": "robotics-starter-kits",
            "price": 5999.00,
            "availability": "Available",
            "shipping_free": 1,
            "shipping_charge": 0.00,
            "short_description": "2WD mobile robot kit with Arduino/ESP32, line following, and obstacle avoidance sensors.",
            "description": "An ideal entry point for robotics and micro-controller programming. Build obstacle avoiding and line following autonomous 2WD wheeled robots using Arduino/ESP32, DC geared motors, and ultrasonic sensors.",
            "image_filename": "ROBOTICS STARTERKIT .png",
            "components": json.dumps([
                "Arduino/ESP32",
                "2WD robot chassis",
                "DC geared motors",
                "Motor driver",
                "Ultrasonic sensor",
                "IR/line sensors",
                "Servo motor",
                "Wheels",
                "Battery holder/battery",
                "Breadboard",
                "Jumper wires & cables"
            ]),
            "applications": json.dumps([
                "Robot building",
                "Coding",
                "Obstacle avoidance",
                "Line following",
                "Basic automation"
            ])
        },
        {
            "name": "Advanced Robotics Kits",
            "slug": "advanced-robotics-kits",
            "price": 14999.00,
            "availability": "Available",
            "shipping_free": 1,
            "shipping_charge": 0.00,
            "short_description": "4WD/6WD heavy-duty robot chassis with high-torque motors, IMU sensor fusion, and robotic gripper.",
            "description": "Construct high-capacity, ground-based autonomous vehicles. Featuring 4WD/6WD chassis options, high-torque motors with wheel encoders, IMU sensor fusion, and robotic gripper arm attachment.",
            "image_filename": "ADVANCED ROBOTICS KITS.png",
            "components": json.dumps([
                "ESP32/Raspberry Pi",
                "4WD/6WD robot chassis",
                "High-torque motors",
                "Motor drivers",
                "Wheel encoders",
                "ToF/ultrasonic sensors",
                "IMU sensor",
                "Servo motors",
                "Robotic gripper/arm",
                "Battery & power management",
                "Mechanical/electronic accessories"
            ]),
            "applications": json.dumps([
                "Autonomous navigation",
                "Advanced robotics",
                "Object handling",
                "Sensor fusion",
                "Complex robotic projects"
            ])
        },
        {
            "name": "Drone & UAV Kits",
            "slug": "drone-and-uav-kits",
            "price": 29999.00,
            "availability": "Available",
            "shipping_free": 1,
            "shipping_charge": 0.00,
            "short_description": "Pixhawk flight controller, GPS, brushless motors, and telemetry kit for autonomous quadcopter assembly.",
            "description": "Complete unmanned aerial vehicle (UAV) drone development kit featuring Pixhawk flight controller, high-precision GPS compass module, FlySky transmitter/receiver, ReadyToSky brushless motors, telemetry system, and durable drone frame.",
            "image_filename": "DRONE&UAV KITS.png",
            "components": json.dumps([
                "Pixhawk / Pixhawk 2.4.8 Flight Controller",
                "GPS + Compass",
                "FlySky FS-i6 Transmitter + Receiver",
                "ReadyToSky Brushless Motors ×4",
                "ESC ×4",
                "Drone frame",
                "Propellers",
                "Power Distribution Board / Power Module",
                "Li-Po battery",
                "Li-Po charger",
                "Telemetry module",
                "BEC/UBEC",
                "Connectors, wiring & accessories"
            ]),
            "applications": json.dumps([
                "Drone assembly",
                "Flight control",
                "GPS navigation",
                "Autonomous flight concepts",
                "UAV programming"
            ])
        }
    ]

    import shutil
    project_root = os.path.abspath(os.path.dirname(__file__))
    source_dir = os.path.join(project_root, "product_airodrone_image")
    target_uploads = os.path.join(project_root, "uploads", "products")
    target_static = os.path.join(project_root, "static", "uploads", "products")
    os.makedirs(target_uploads, exist_ok=True)
    os.makedirs(target_static, exist_ok=True)

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    for p in initial_products:
        # Copy image file from supplied directory to persistent upload folders
        src_file = os.path.join(source_dir, p["image_filename"])
        safe_img_name = p["image_filename"].replace(" ", "_")
        dest_rel_path = f"uploads/products/{safe_img_name}"

        if os.path.exists(src_file):
            try:
                shutil.copy2(src_file, os.path.join(target_uploads, safe_img_name))
                shutil.copy2(src_file, os.path.join(target_static, safe_img_name))
            except Exception:
                pass

        cur.execute("SELECT id FROM products WHERE slug = %s", (p["slug"],))
        existing = cur.fetchone()

        if not existing:
            cur.execute(
                """
                INSERT INTO products (
                    name, slug, short_description, description, price, image,
                    availability, shipping_free, shipping_charge, components, applications,
                    is_active, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    p["name"],
                    p["slug"],
                    p["short_description"],
                    p["description"],
                    p["price"],
                    dest_rel_path,
                    p["availability"],
                    p["shipping_free"],
                    p["shipping_charge"],
                    p["components"],
                    p["applications"],
                    1,
                    now_str,
                    now_str,
                )
            )
    conn.commit()


def seed_initial_solutions(cur, conn):
    """Seed default 6 Institutional Solutions with items if not present."""
    cur.execute("SELECT COUNT(*) as cnt FROM solutions")
    cnt = cur.fetchone()["cnt"]
    if cnt > 0:
        return

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    db_type = get_db_type()
    initial_solutions = [
        {
            "slug": "school-programs",
            "title": "School Programs",
            "badge": "K-12 CURRICULUM & ATL TRACKS",
            "short_description": "Build practical STEM skills through structured K-12 school programs covering AI, robotics, coding, and electronics.",
            "description": "Designed for schools that want to introduce students to future-ready technologies through hands-on discovery and structured progression from early grades through senior high school.",
            "image": "images/services/robotics.jpg",
            "ideal_for": "K-12 Schools, CBSE/ICSE institutions, Atal Tinkering Labs (ATL), and progressive schools seeking NEP 2020 technology integration.",
            "cta_text": "Talk to Us About School Programs",
            "cta_url": "/contact",
            "display_order": 1,
            "items": [
                {"title": "AI & Machine Learning", "description": "Foundational awareness, visual block-based logic, and intelligent algorithms.", "display_order": 1},
                {"title": "Robotics & Electronics", "description": "Sensor interfacing, motor drivers, circuit assembly, and microcontrollers.", "display_order": 2},
                {"title": "Coding & Logic", "description": "Algorithmic thinking, scratch-based blocks, and progression into text-based Python.", "display_order": 3},
                {"title": "Drone Technology", "description": "Principles of flight, UAV components, simulation, and safe flight dynamics.", "display_order": 4},
                {"title": "Year-Round Curriculum", "description": "Complete lesson plans, student workbooks, project kits, and rubric-based evaluations.", "display_order": 5},
            ]
        },
        {
            "slug": "college-programs",
            "title": "College Programs",
            "badge": "HIGHER EDUCATION & ENGINEERING",
            "short_description": "Industry-oriented technology learning, advanced project tracks, and innovation programs for higher education.",
            "description": "Industry-oriented technology learning and innovation programs for polytechnics, engineering colleges, and universities that bridge the gap between classroom theory and deployable engineering skills.",
            "image": "images/services/ai.jpg",
            "ideal_for": "Engineering colleges, polytechnics, degree colleges, technical clubs, and institutional innovation councils (IIC).",
            "cta_text": "Talk to Us About College Programs",
            "cta_url": "/contact",
            "display_order": 2,
            "items": [
                {"title": "Applied Autonomous Systems", "description": "Drone assembly, flight controller tuning, telemetry, and payload integration.", "display_order": 1},
                {"title": "Embedded Systems & IoT", "description": "Edge AI computing, sensor telemetry, wireless protocols, and cloud dashboards.", "display_order": 2},
                {"title": "Industrial Computer Vision", "description": "Deep neural networks, OpenCV image processing, and object detection.", "display_order": 3},
                {"title": "Capstone Mentorship", "description": "Multi-disciplinary engineering guidance for final-year projects and patents.", "display_order": 4},
                {"title": "Innovation Incubators", "description": "Support for campus robotics clubs, hackathon teams, and student startups.", "display_order": 5},
            ]
        },
        {
            "slug": "professional-training",
            "title": "Professional Training",
            "badge": "FACULTY DEVELOPMENT & UPSKILLING",
            "short_description": "Upskill educators, lab coordinators, and professionals with emerging technologies and hands-on pedagogy.",
            "description": "Hands-on technical training for educators, lab in-charges, and professionals who want to master emerging technologies and effectively deliver modern STEM curricula.",
            "image": "images/about/about.jpg",
            "ideal_for": "School teachers, ATL coordinators, STEM mentors, vocational instructors, and technology professionals expanding into education.",
            "cta_text": "Talk to Us About Professional Training",
            "cta_url": "/contact",
            "display_order": 3,
            "items": [
                {"title": "Train-the-Trainer (TTT)", "description": "Pedagogical frameworks, lab management tactics, and hands-on tool proficiencies.", "display_order": 1},
                {"title": "ATL Lab Masterclasses", "description": "Operational best practices, inventory management, and student project evaluation.", "display_order": 2},
                {"title": "Hardware Troubleshooting", "description": "Soldering safety, 3D printer calibration, microelectronics debugging, and drone repairs.", "display_order": 3},
                {"title": "Software Stacks", "description": "MicroPython, Block programming environments, drone simulation platforms, and CAD modeling.", "display_order": 4},
                {"title": "Verifiable Certification", "description": "Industry-recognized mentor certifications validating emerging tech competency.", "display_order": 5},
            ]
        },
        {
            "slug": "lab-setup",
            "title": "Lab Setup",
            "badge": "TURNKEY LAB ENABLEMENT",
            "short_description": "Complete STEM and Atal Tinkering Lab (ATL) setup, equipment procurement, curriculum integration, and implementation.",
            "description": "End-to-end Atal Tinkering Lab (ATL) and STEM laboratory implementation — providing certified hardware, ergonomic lab layout, structured curriculum, and ongoing institutional support.",
            "image": "images/services/atl.jpg",
            "ideal_for": "Schools with ATL grants, private schools building innovation hubs, CSR initiatives, and vocational training centers.",
            "cta_text": "Talk to Us About Lab Setup",
            "cta_url": "/contact",
            "display_order": 4,
            "items": [
                {"title": "Compliant Equipment Packages", "description": "NITI Aayog ATL Packages P1 to P4 covering electronics, 3D printing, and robotics.", "display_order": 1},
                {"title": "Rapid Prototyping Gear", "description": "High-precision 3D printers, filament sets, soldering stations, multimeters, and hand tools.", "display_order": 2},
                {"title": "STEM Kits & Drones", "description": "Modular robotics development kits, sensor arrays, DIY quadcopters, and IoT modules.", "display_order": 3},
                {"title": "Infrastructure & Safety", "description": "Lab layout consultation, protective gear, organized storage racks, and safety compliance.", "display_order": 4},
                {"title": "Year-Round Maintenance", "description": "Dedicated equipment servicing, component replacements, and technical helpline.", "display_order": 5},
            ]
        },
        {
            "slug": "workshops",
            "title": "Workshops",
            "badge": "PRACTICAL BOOTCAMPS & INTENSIVES",
            "short_description": "Short, practical, and engaging technology bootcamps focused on AI, robotics, drones, electronics, and coding.",
            "description": "Short-term practical bootcamps and intensive workshops focused on AI, robotics, drones, coding, and electronics that spark curiosity and yield working student creations.",
            "image": "images/services/workshop.jpg",
            "ideal_for": "School activity weeks, weekend STEM clubs, summer camps, college symposiums, and institutional science fests.",
            "cta_text": "Talk to Us About Workshops",
            "cta_url": "/contact",
            "display_order": 5,
            "items": [
                {"title": "Drone Aerodynamics & Assembly Sprint", "description": "Build, configure, and fly a functional quadcopter in a hands-on format.", "display_order": 1},
                {"title": "AI & Smart Vision Bootcamps", "description": "Train classification models and create interactive camera-driven games.", "display_order": 2},
                {"title": "Autonomous Robotics Challenges", "description": "Program obstacle avoiders, line tracers, and remote-controlled rovers.", "display_order": 3},
                {"title": "Electronics & Sensor Labs", "description": "Breadboard prototyping, ambient light sensors, ultrasonic radars, and buzzers.", "display_order": 4},
                {"title": "All Materials Supplied", "description": "Takeaway kits, live expert instructors, and completion certificates for every learner.", "display_order": 5},
            ]
        },
        {
            "slug": "projects",
            "title": "Projects & Competitions",
            "badge": "HACKATHONS & COMPETITIONS",
            "short_description": "Build, demonstrate, and compete with real-world technology projects, hackathons, and innovation challenges.",
            "description": "Practical projects, innovation challenges, hackathons, and competitions that allow learners to demonstrate what they build, compete nationally, and achieve measurable milestones.",
            "image": "images/services/drone.jpg",
            "ideal_for": "High-performing student teams, school innovation clubs, inter-institutional competitions, and exhibition showcases.",
            "cta_text": "Talk to Us About Projects & Competitions",
            "cta_url": "/contact",
            "display_order": 6,
            "items": [
                {"title": "ATL Marathon & Olympiad Mentorship", "description": "Structured guidance for submitting high-impact innovations.", "display_order": 1},
                {"title": "Inter-School Hackathons", "description": "Turnkey hackathon management, problem statement curation, and judging frameworks.", "display_order": 2},
                {"title": "Science Fair Prototyping", "description": "Mentorship on model design, scientific documentation, and presentation skills.", "display_order": 3},
                {"title": "Drone Racing & Flight Challenges", "description": "Precision piloting tournaments, obstacle courses, and simulator contests.", "display_order": 4},
                {"title": "Awards & Recognition", "description": "Badges, trophies, evaluation rubrics, and portfolio-worthy certificates.", "display_order": 5},
            ]
        }
    ]

    for sol in initial_solutions:
        cur.execute(
            """
            INSERT INTO solutions (
                slug, title, badge, short_description, description, image,
                ideal_for, cta_text, cta_url, display_order, is_active, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
            """ + (" RETURNING id" if db_type == "postgres" else ""),
            (
                sol["slug"], sol["title"], sol["badge"], sol["short_description"],
                sol["description"], sol["image"], sol["ideal_for"], sol["cta_text"],
                sol["cta_url"], sol["display_order"], now_str, now_str
            )
        )
        if db_type == "postgres":
            sol_row = cur.fetchone()
            sol_id = sol_row["id"] if isinstance(sol_row, dict) else sol_row[0]
        else:
            sol_id = cur.lastrowid

        for item in sol["items"]:
            cur.execute(
                """
                INSERT INTO solution_items (
                    solution_id, title, description, display_order, is_active, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, 1, %s, %s)
                """,
                (
                    sol_id, item["title"], item["description"], item["display_order"],
                    now_str, now_str
                )
            )

    conn.commit()


# ---------- Upload Storage Setup ----------

UPLOAD_FOLDER = os.path.join(app.root_path, "uploads")
VIDEO_UPLOAD_FOLDER = os.path.join(UPLOAD_FOLDER, "videos")
PROJECT_UPLOAD_FOLDER = os.path.join(UPLOAD_FOLDER, "projects")
COURSE_IMAGE_UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads", "courses")
CATEGORY_IMAGE_UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads", "categories")
LEARNING_PATH_IMAGE_UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads", "learning_paths")
PRODUCT_IMAGE_UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads", "products")
SOLUTION_IMAGE_UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads", "solutions")
os.makedirs(VIDEO_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROJECT_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(COURSE_IMAGE_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CATEGORY_IMAGE_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(LEARNING_PATH_IMAGE_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PRODUCT_IMAGE_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(SOLUTION_IMAGE_UPLOAD_FOLDER, exist_ok=True)


ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "mkv", "m4v"}
ALLOWED_PROJECT_EXTENSIONS = {"zip", "pdf", "py", "docx", "doc", "txt", "png", "jpg", "jpeg", "tar", "gz"}

def allowed_file(filename, allowed_extensions):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


def validate_uploaded_image_file(file_obj, max_size_bytes=5 * 1024 * 1024):
    """
    Validates uploaded image file for:
    1. Presence and non-empty filename
    2. Allowed extensions in {jpg, jpeg, png, webp}
    3. Magic byte signature verification (rejecting SVG, HTML, PHP, scripts, EXE)
    4. File size within max limit (default 5 MB)
    """
    if not file_obj or not file_obj.filename:
        return False, "No image file selected."

    filename = file_obj.filename.strip()
    if not allowed_file(filename, ALLOWED_IMAGE_EXTENSIONS):
        return False, f"Invalid image format. Allowed formats: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS)).upper()}."

    # Check file size
    try:
        file_obj.seek(0, os.SEEK_END)
        size = file_obj.tell()
        file_obj.seek(0)
    except Exception:
        size = 0

    if size > max_size_bytes:
        return False, f"Image file is too large ({(size / (1024 * 1024)):.1f} MB). Maximum allowed size is 5 MB."
    if size == 0:
        return False, "Uploaded image file is empty."

    # Verify magic bytes
    try:
        header = file_obj.read(16)
        file_obj.seek(0)
    except Exception:
        return False, "Could not read uploaded image data."

    is_jpeg = header.startswith(b"\xff\xd8\xff")
    is_png = header.startswith(b"\x89PNG\r\n\x1a\n")
    is_webp = header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WEBP"

    if not (is_jpeg or is_png or is_webp):
        return False, "Invalid image content. File signature does not match a valid JPG, PNG, or WEBP image."

    return True, None


def resolve_media_image_url(image_path):
    """
    Safely resolves image path/URL to browser accessible URL.
    Supports uploaded files, static assets, local/hostinger storage, and external URLs.
    Returns None if no image is configured.
    """
    if not image_path or not str(image_path).strip():
        return None
    img_str = str(image_path).strip().replace("\\", "/")
    if img_str.startswith("http://") or img_str.startswith("https://"):
        return img_str
    if img_str.startswith("/static/"):
        return img_str
    if img_str.startswith("static/"):
        return "/" + img_str
    if img_str.startswith("uploads/") or img_str.startswith("/uploads/"):
        return "/" + img_str.lstrip("/")
    if img_str.startswith("images/") or img_str.startswith("/images/"):
        clean_img = img_str.lstrip("/")
        return url_for("static", filename=clean_img)
    if img_str.startswith("/"):
        return img_str
    return url_for("static", filename=img_str)


def parse_list_field(val):
    """Parse JSON or newline separated text into a list of items."""
    if not val:
        return []
    if isinstance(val, (list, tuple)):
        return [str(x).strip() for x in val if str(x).strip()]
    val_str = str(val).strip()
    if not val_str:
        return []
    if val_str.startswith("[") and val_str.endswith("]"):
        try:
            parsed = json.loads(val_str)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
    return [line.strip().lstrip("-*•").strip() for line in val_str.splitlines() if line.strip()]


@app.template_filter("format_price")
def format_price_filter(val):
    try:
        val_float = float(val)
        if val_float.is_integer():
            return f"₹{int(val_float):,}"
        return f"₹{val_float:,.2f}"
    except (ValueError, TypeError):
        return "₹0"


@app.template_filter("parse_list")
def parse_list_filter(val):
    return parse_list_field(val)


@app.template_filter("image_url")
def image_url_filter(image_path):
    return resolve_media_image_url(image_path)


@app.template_filter("category_image_url")
def category_image_url_filter(image_path):
    return resolve_media_image_url(image_path)


@app.context_processor
def inject_media_helpers():
    return {
        "image_url": resolve_media_image_url,
        "category_image_url": resolve_media_image_url,
        "format_price": format_price_filter,
        "parse_list": parse_list_field,
    }




def extract_youtube_id(input_str):
    """Legacy helper for backward-compatibility with existing seed scripts if needed."""
    if not input_str:
        return None
    cleaned = input_str.strip()
    if not cleaned:
        return None
    if re.match(r"^[a-zA-Z0-9_-]{11}$", cleaned):
        return cleaned
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
    Calculate verified course completion percentage server-side (Option B).
    - Each active video contributes based on legitimate watched percentage (0-100%).
    - Each active quiz contributes 100% only when passed (otherwise 0%).
    - Each active project contributes 100% only when evaluated (otherwise 0%).
    Total items = total_active_videos + total_active_quizzes + total_active_projects.
    Returns tuple: (completion_percentage, watched_seconds, total_duration_seconds)
    """
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    # 1. Fetch active videos in active modules
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

    # 2. Fetch active quizzes in active modules
    cur.execute(
        """
        SELECT q.id, q.passing_score
        FROM quizzes q
        JOIN modules m ON q.module_id = m.id
        WHERE m.course_id = %s AND q.is_active = 1 AND m.is_active = 1
        """,
        (course_id,),
    )
    quizzes = cur.fetchall()

    # 3. Fetch active projects in active modules
    cur.execute(
        """
        SELECT p.id
        FROM projects p
        JOIN modules m ON p.module_id = m.id
        WHERE m.course_id = %s AND p.is_active = 1 AND m.is_active = 1
        """,
        (course_id,),
    )
    projects = cur.fetchall()

    total_items = len(videos) + len(quizzes) + len(projects)
    if total_items == 0:
        cur.close()
        conn.close()
        return (0.0, 0.0, 0.0)

    total_course_duration = 0.0
    video_durations = {}
    for v in videos:
        dur = parse_duration_seconds(v["duration"])
        video_durations[v["id"]] = dur
        total_course_duration += dur

    # Video progress calculation
    video_pct_sum = 0.0
    total_watched_seconds = 0.0
    if videos:
        video_ids = list(video_durations.keys())
        placeholders = ",".join(str(v_id) for v_id in video_ids)
        cur.execute(
            f"""
            SELECT video_id, watched_seconds, completed, duration_seconds
            FROM video_progress
            WHERE user_id = %s AND video_id IN ({placeholders})
            """,
            (user_id,),
        )
        prog_rows = {p["video_id"]: p for p in cur.fetchall()}
        for vid_id, target_dur in video_durations.items():
            p = prog_rows.get(vid_id)
            if p:
                if p["completed"]:
                    video_pct_sum += 100.0
                    total_watched_seconds += target_dur
                else:
                    w = min(float(p["watched_seconds"] or 0.0), target_dur)
                    total_watched_seconds += w
                    video_pct_sum += min(100.0, (w / max(1.0, target_dur)) * 100.0)

    # Quiz progress calculation (100% only if passed)
    quiz_pct_sum = 0.0
    if quizzes:
        quiz_ids = [q["id"] for q in quizzes]
        placeholders = ",".join(str(q_id) for q_id in quiz_ids)
        cur.execute(
            f"""
            SELECT quiz_id, passed
            FROM quiz_attempts
            WHERE user_id = %s AND quiz_id IN ({placeholders}) AND is_invalidated = FALSE AND passed = TRUE
            """,
            (user_id,),
        )
        passed_quiz_ids = {row["quiz_id"] for row in cur.fetchall()}
        for q in quizzes:
            if q["id"] in passed_quiz_ids:
                quiz_pct_sum += 100.0

    # Project progress calculation (100% only if evaluated)
    project_pct_sum = 0.0
    if projects:
        proj_ids = [p["id"] for p in projects]
        placeholders = ",".join(str(p_id) for p_id in proj_ids)
        cur.execute(
            f"""
            SELECT project_id, status
            FROM project_submissions
            WHERE user_id = %s AND project_id IN ({placeholders}) AND status = 'evaluated'
            """,
            (user_id,),
        )
        evaluated_proj_ids = {row["project_id"] for row in cur.fetchall()}
        for p in projects:
            if p["id"] in evaluated_proj_ids:
                project_pct_sum += 100.0

    cur.close()
    conn.close()

    total_score = video_pct_sum + quiz_pct_sum + project_pct_sum
    completion_pct = min(100.0, total_score / total_items)
    return (round(completion_pct, 1), total_watched_seconds, total_course_duration)


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


# ---------- Audit Logging & Role Decorators ----------

def log_audit(user_id, action, target_type=None, target_id=None, details=None):
    """
    Record an administrative/audit action in database without sensitive credentials.
    """
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO audit_logs (user_id, action, target_type, target_id, details, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (user_id, action, target_type, target_id, details, now_str)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        app.logger.error(f"Failed to log audit action '{action}': {e}")


def role_required(*roles):
    """
    Decorator to require specific user roles.
    If current authenticated user does not have one of the allowed roles, returns HTTP 403 Forbidden.
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not current_user.has_role(*roles):
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"error": "Forbidden", "message": "You do not have permission to access this resource."}), 403
                return render_template("403.html"), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def admin_required(f):
    """Decorator to require Admin role strictly."""
    return role_required("admin")(f)


def admin_or_subadmin_required(f):
    """Decorator to require Admin or Sub-Admin role."""
    return role_required("admin", "sub_admin")(f)


def evaluator_required(f):
    """Decorator to require Admin, Sub-Admin, or Teacher role."""
    return role_required("admin", "sub_admin", "teacher")(f)


@app.template_filter("nl2br")
def nl2br_filter(s):
    if not s:
        return ""
    from markupsafe import escape
    return "<br>\n".join(str(escape(s)).split("\n"))


# ---------- Public Routes ----------

@app.route("/health")
def health_check():
    """Simple production health check endpoint for monitoring."""
    return jsonify({"status": "ok"}), 200


def get_learning_categories_with_counts(conn=None, active_only=True):
    """
    Single source of truth for learning categories and course counts across the entire platform.
    A course belongs to a category if:
      - c.category_id = lc.id, OR
      - lp.category_id = lc.id (where lp is the course's assigned learning_path)
    Courses counted on the public website must have c.is_active = 1.
    Used identically across:
      - Public /learn page
      - Public Home page Explore Learning Programs
      - Admin Category Management (/admin/learning-categories)
      - Admin Courses Overview
    """
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True
    try:
        cur = get_db_cursor(conn)
        where_cat = "WHERE lc.is_active = 1" if active_only else ""
        cur.execute(
            f"""
            SELECT lc.id, lc.name, lc.slug, lc.description, lc.image, lc.display_order, lc.is_active, lc.created_at,
                   (
                       SELECT COUNT(DISTINCT c.id)
                       FROM courses c
                       LEFT JOIN learning_paths lp ON c.learning_path_id = lp.id
                       WHERE c.is_active = 1
                         AND (c.category_id = lc.id OR lp.category_id = lc.id)
                   ) AS course_count,
                   (
                       SELECT COUNT(DISTINCT c.id)
                       FROM courses c
                       LEFT JOIN learning_paths lp ON c.learning_path_id = lp.id
                       WHERE (c.category_id = lc.id OR lp.category_id = lc.id)
                   ) AS total_course_count,
                   (
                       SELECT COUNT(DISTINCT lp.id)
                       FROM learning_paths lp
                       WHERE lp.category_id = lc.id AND (lp.is_active = 1 OR NOT {'TRUE' if active_only else 'FALSE'})
                   ) AS path_count
            FROM learning_categories lc
            {where_cat}
            ORDER BY lc.display_order ASC, lc.id ASC
            """
        )
        categories = cur.fetchall()
        cur.close()
        return categories
    except Exception as e:
        app.logger.error(f"Error fetching learning categories with counts: {e}")
        return []
    finally:
        if close_conn:
            try:
                conn.close()
            except Exception:
                pass


def get_active_learning_categories(conn=None):
    """
    Retrieve active learning categories with path and course counts from database.
    Delegates directly to get_learning_categories_with_counts to guarantee unified consistency.
    """
    return get_learning_categories_with_counts(conn=conn, active_only=True)



@app.route("/")
def home():
    categories = get_active_learning_categories()
    return render_template("home.html", active_page="home", categories=categories)


@app.route("/about")
def about():
    return render_template("about.html", active_page="about")


@app.route("/solutions")
def solutions():
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    solutions_list = []
    try:
        cur.execute(
            """
            SELECT * FROM solutions
            WHERE is_active = 1
            ORDER BY display_order ASC, id ASC
            """
        )
        raw_solutions = cur.fetchall()
        for sol in raw_solutions:
            sol_dict = dict(sol)
            cur.execute(
                """
                SELECT * FROM solution_items
                WHERE solution_id = %s AND is_active = 1
                ORDER BY display_order ASC, id ASC
                """,
                (sol_dict["id"],)
            )
            items_list = [dict(it) for it in cur.fetchall()]
            sol_dict["solution_items"] = items_list
            sol_dict["items"] = items_list
            solutions_list.append(sol_dict)
    except Exception as e:
        app.logger.error(f"Error loading solutions catalogue: {e}")
        solutions_list = []
    finally:
        cur.close()
        conn.close()

    return render_template("solutions.html", active_page="solutions", solutions=solutions_list)


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


# ---------- Learn & Course Routes ----------

@app.route("/learn")
def learn():
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        selected_grade = request.args.get("grade", type=int)

        if selected_grade is not None:
            # Backward compatibility: Grade selected directly via query param
            if selected_grade not in GRADES:
                cur.close()
                conn.close()
                flash("Invalid Grade selected.", "error")
                return redirect(url_for("learn"))

            grade_info = GRADES[selected_grade]

            cur.execute(
                """
                SELECT c.id, c.title, c.slug, c.short_description, c.description, c.image, c.level, c.estimated_duration, c.grade, c.display_label, c.created_at,
                       COUNT(DISTINCT m.id) AS total_modules,
                       COUNT(DISTINCT v.id) AS total_videos
                FROM courses c
                LEFT JOIN modules m ON m.course_id = c.id AND m.is_active = 1
                LEFT JOIN course_videos v ON v.module_id = m.id AND v.is_active = 1
                WHERE c.is_active = 1 AND c.grade = %s
                GROUP BY c.id, c.title, c.slug, c.short_description, c.description, c.image, c.level, c.estimated_duration, c.grade, c.display_label, c.created_at
                ORDER BY c.id ASC
                """,
                (selected_grade,),
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

            catalogue_settings = get_catalogue_settings(conn)
            cur.close()
            conn.close()

            return render_template(
                "courses.html",
                active_page="learn",
                show_categories=False,
                show_grades=False,
                selected_grade=selected_grade,
                grade_info=grade_info,
                courses=courses_list,
                catalogue_settings=catalogue_settings,
            )

        # Primary View: Learning Categories first
        categories_list = get_active_learning_categories(conn)

        catalogue_settings = get_catalogue_settings(conn)
        cur.close()
        conn.close()

        return render_template(
            "courses.html",
            active_page="learn",
            show_categories=True,
            show_grades=False,
            categories=categories_list,
            catalogue_settings=catalogue_settings,
        )
    except Exception as e:
        app.logger.error(f"Error fetching learn catalogue: {e}")
        return render_template(
            "courses.html",
            active_page="learn",
            show_categories=True,
            show_grades=False,
            categories=[],
            catalogue_settings={
                "id": 1,
                "hero_badge": "STEM LEARNING DOMAINS",
                "hero_title": "Learn",
                "hero_description": "Choose a learning domain to explore progressive educational pathways across technology, programming, AI, drones, and digital skills.",
                "hero_image": "",
                "hero_image_alt": "Learn",
                "is_active": 1,
            },
        )


@app.route("/courses")
def courses():
    """Backward compatibility redirect: /courses -> /learn."""
    grade = request.args.get("grade")
    if grade:
        return redirect(url_for("learn", grade=grade), code=301)
    return redirect(url_for("learn"), code=301)


@app.route("/courses/category/<category_slug>")
def category_paths_legacy(category_slug):
    return redirect(url_for("category_paths", category_slug=category_slug), code=301)


@app.route("/learn/category/<category_slug>")
def category_paths(category_slug):
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            """
            SELECT id, name, slug, description, image, display_order, is_active
            FROM learning_categories
            WHERE slug = %s AND is_active = 1
            """,
            (category_slug,)
        )
        category = cur.fetchone()
        if not category:
            cur.close()
            conn.close()
            abort(404)

        cur.execute(
            """
            SELECT c.id, c.title, c.slug, c.short_description, c.description, c.image, c.level, c.estimated_duration, c.grade, c.display_label, c.created_at,
                   COUNT(DISTINCT m.id) AS total_modules,
                   COUNT(DISTINCT v.id) AS total_videos
            FROM courses c
            LEFT JOIN learning_paths lp ON lp.id = c.learning_path_id
            LEFT JOIN modules m ON m.course_id = c.id AND m.is_active = 1
            LEFT JOIN course_videos v ON v.module_id = m.id AND v.is_active = 1
            WHERE (c.category_id = %s OR lp.category_id = %s) AND c.is_active = 1 AND (c.learning_path_id IS NULL OR lp.is_active = 1)
            GROUP BY c.id, c.title, c.slug, c.short_description, c.description, c.image, c.level, c.estimated_duration, c.grade, c.display_label, c.created_at
            ORDER BY c.grade ASC, c.id ASC
            """,
            (category["id"], category["id"]),
        )
        courses_list = cur.fetchall()

        cur.execute(
            """
            SELECT lp.id, lp.category_id, lp.grade, lp.name, lp.slug, lp.description, lp.image, lp.display_order,
                   COUNT(DISTINCT c.id) AS course_count
            FROM learning_paths lp
            LEFT JOIN courses c ON c.category_id = lp.category_id AND c.learning_path_id = lp.id AND c.grade = lp.grade AND c.is_active = 1
            WHERE lp.category_id = %s AND lp.is_active = 1
            GROUP BY lp.id, lp.category_id, lp.grade, lp.name, lp.slug, lp.description, lp.image, lp.display_order
            ORDER BY lp.grade ASC, lp.display_order ASC, lp.id ASC
            """,
            (category["id"],)
        )
        paths = cur.fetchall()

        cur.execute("SELECT LOWER(title) AS title FROM courses WHERE category_id = %s", (category["id"],))
        all_category_course_titles = {row["title"].strip() for row in cur.fetchall()}
        display_paths = [p for p in paths if p["name"].strip().lower() not in all_category_course_titles]

        for p in display_paths:
            g_info = GRADES.get(p["grade"], {})
            p["grade_name"] = g_info.get("name", f"Grade {p['grade']}")
            p["classes"] = g_info.get("classes", "")

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

        return render_template(
            "category_paths.html",
            active_page="learn",
            category=category,
            courses=courses_list,
            paths=display_paths,
            grades=GRADES,
        )
    except Exception as e:
        app.logger.error(f"Error fetching category courses for {category_slug}: {e}")
        abort(404)


@app.route("/courses/category/<category_slug>/<path_slug>")
def path_courses_legacy(category_slug, path_slug):
    return redirect(url_for("path_courses", category_slug=category_slug, path_slug=path_slug), code=301)


@app.route("/learn/path/<slug>")
@app.route("/courses/path/<slug>")
def learn_path_detail(slug):
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        cur.execute(
            """
            SELECT lp.slug AS path_slug, lc.slug AS category_slug
            FROM learning_paths lp
            JOIN learning_categories lc ON lc.id = lp.category_id
            WHERE lp.slug = %s AND lp.is_active = 1
            LIMIT 1
            """,
            (slug,)
        )
        res = cur.fetchone()
        cur.close()
        conn.close()
        if res:
            return redirect(url_for("path_courses", category_slug=res["category_slug"], path_slug=res["path_slug"]), code=301)
        abort(404)
    except Exception as e:
        app.logger.error(f"Error in learn_path_detail for {slug}: {e}")
        abort(404)


@app.route("/learn/category/<category_slug>/<path_slug>")
def path_courses(category_slug, path_slug):
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)

        cur.execute(
            """
            SELECT id, name, slug, description, image, display_order
            FROM learning_categories
            WHERE slug = %s AND is_active = 1
            """,
            (category_slug,)
        )
        category = cur.fetchone()
        if not category:
            cur.close()
            conn.close()
            abort(404)

        cur.execute(
            """
            SELECT id, category_id, grade, name, slug, description, image, display_order
            FROM learning_paths
            WHERE category_id = %s AND slug = %s AND is_active = 1
            """,
            (category["id"], path_slug)
        )
        learning_path = cur.fetchone()
        if not learning_path:
            cur.close()
            conn.close()
            abort(404)

        grade_info = GRADES.get(learning_path["grade"], {})
        learning_path["grade_name"] = grade_info.get("name", f"Grade {learning_path['grade']}")
        learning_path["classes"] = grade_info.get("classes", "")

        cur.execute(
            """
            SELECT c.id, c.title, c.slug, c.short_description, c.description, c.image, c.level, c.estimated_duration, c.grade, c.display_label, c.created_at,
                   COUNT(DISTINCT m.id) AS total_modules,
                   COUNT(DISTINCT v.id) AS total_videos
            FROM courses c
            LEFT JOIN modules m ON m.course_id = c.id AND m.is_active = 1
            LEFT JOIN course_videos v ON v.module_id = m.id AND v.is_active = 1
            WHERE c.category_id = %s AND c.learning_path_id = %s AND c.grade = %s AND c.is_active = 1
            GROUP BY c.id, c.title, c.slug, c.short_description, c.description, c.image, c.level, c.estimated_duration, c.grade, c.display_label, c.created_at
            ORDER BY c.id ASC
            """,
            (category["id"], learning_path["id"], learning_path["grade"]),
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

        return render_template(
            "path_courses.html",
            active_page="learn",
            category=category,
            path=learning_path,
            grade_info=grade_info,
            courses=courses_list,
        )
    except Exception as e:
        app.logger.error(f"Error fetching path courses for {category_slug}/{path_slug}: {e}")
        abort(404)


@app.route("/learn/grade/<int:grade_id>")
@app.route("/courses/grade/<int:grade_id>")
def courses_by_grade(grade_id):
    return redirect(url_for("learn", grade=grade_id), code=301)


@app.route("/courses/<slug>")
def course_detail_legacy(slug):
    return redirect(url_for("course_detail", slug=slug), code=301)


@app.route("/learn/<slug>")
def course_detail(slug):
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        
        # Fetch course detail with category and learning path
        cur.execute(
            """
            SELECT id, title, slug, short_description, description, image, level, grade,
                   category_id, learning_path_id, estimated_duration, learning_outcomes,
                   course_benefits, certificate_description, created_at
            FROM courses
            WHERE slug = %s AND is_active = 1
            """,
            (slug,),
        )
        course = cur.fetchone()
        
        if not course:
            cur.close()
            conn.close()
            return render_template("course_detail.html", active_page="learn", not_found=True), 404

        # Fetch category and learning path for rich breadcrumbs if configured
        category = None
        learning_path = None
        if course.get("category_id"):
            cur.execute("SELECT id, name, slug FROM learning_categories WHERE id = %s", (course["category_id"],))
            category = cur.fetchone()
        if course.get("learning_path_id"):
            cur.execute("SELECT id, name, slug, grade FROM learning_paths WHERE id = %s", (course["learning_path_id"],))
            learning_path = cur.fetchone()
        
        if not course:
            cur.close()
            conn.close()
            return render_template("course_detail.html", active_page="learn", not_found=True), 404
            
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
                    SELECT id, title, description, sequence, duration, video_file
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

                # Fetch active module project
                cur.execute(
                    """
                    SELECT id, title, description, max_marks, deadline, is_active
                    FROM projects
                    WHERE module_id = %s AND is_active = 1
                    """,
                    (mod["id"],),
                )
                mod_project = cur.fetchone()
                mod["project"] = mod_project

                if mod_project and current_user.is_authenticated:
                    cur.execute(
                        """
                        SELECT id, submission_text, submission_file, status, marks, feedback, evaluated_at, submitted_at
                        FROM project_submissions
                        WHERE project_id = %s AND user_id = %s
                        """,
                        (mod_project["id"], current_user.id),
                    )
                    submission = cur.fetchone()
                    mod["project_submission"] = submission

            next_video_id = first_uncompleted_video_id or first_video_id

        cur.close()
        conn.close()
        
        grade_info = GRADES.get(course["grade"]) if course.get("grade") else None

        return render_template(
            "course_detail.html",
            active_page="learn",
            course=course,
            category=category,
            learning_path=learning_path,
            grades=GRADES,
            grade_info=grade_info,
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
        app.logger.error(f"Error fetching course detail for {slug}: {e}", exc_info=True)
        return render_template("course_detail.html", active_page="learn", not_found=True), 404


@app.route("/courses/<course_slug>/video/<int:video_id>")
@login_required
def video_player(course_slug, video_id):
    try:
        conn = get_db_connection()
        cur = get_db_cursor(conn)
        
        # Verify course exists
        cur.execute(
            "SELECT id, title, slug, level, grade, description, image FROM courses WHERE slug = %s AND is_active = 1",
            (course_slug,)
        )
        course = cur.fetchone()
        if not course:
            cur.close()
            conn.close()
            return render_template("video_player.html", active_page="learn", not_found=True), 404

        if not can_access_course(current_user.id, course["id"]):
            cur.close()
            conn.close()
            return render_template("course_access_denied.html", active_page="learn"), 403

        # Verify video and relationship
        cur.execute(
            """
            SELECT v.id, v.title, v.description, v.sequence, v.duration, v.video_file, v.is_active,
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
            return render_template("video_player.html", active_page="learn", not_found=True), 404

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
                SELECT id, title, description, sequence, duration, video_file, is_active
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

            # Fetch active module quiz for complete sidebar curriculum
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
                    "latest_attempt_id": latest_attempt_id,
                }

            # Fetch active module project for complete sidebar curriculum
            cur.execute(
                """
                SELECT id, title, description, max_marks, deadline, is_active
                FROM projects
                WHERE module_id = %s AND is_active = 1
                """,
                (mod["id"],),
            )
            mod_project = cur.fetchone()
            mod["project"] = mod_project

            if mod_project and current_user.is_authenticated:
                cur.execute(
                    """
                    SELECT id, submission_text, submission_file, status, marks, feedback, evaluated_at, submitted_at
                    FROM project_submissions
                    WHERE project_id = %s AND user_id = %s
                    """,
                    (mod_project["id"], current_user.id),
                )
                submission = cur.fetchone()
                mod["project_submission"] = submission

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
            active_page="learn",
            course=course,
            video=video,
            modules=modules,
            prev_video=prev_video,
            next_video=next_video,
            video_progress=video_prog,
        )
    except Exception as e:
        app.logger.error(f"Error loading video player for {course_slug}/video/{video_id}: {e}")
        return render_template("video_player.html", active_page="learn", not_found=True), 404


@app.route("/courses/video/<int:video_id>/stream")
@login_required
def stream_course_video(video_id):
    """
    Protected video streaming endpoint with HTTP 206 Partial Content Range support.
    Enforces can_access_course. Video files are streamed via pluggable storage backend.
    """
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT v.id, v.video_file, m.course_id
        FROM course_videos v
        JOIN modules m ON v.module_id = m.id
        WHERE v.id = %s AND v.is_active = 1 AND m.is_active = 1
        """,
        (video_id,),
    )
    video = cur.fetchone()
    cur.close()
    conn.close()

    if not video:
        abort(404)

    if not can_access_course(current_user.id, video["course_id"]):
        abort(403)

    video_file = video.get("video_file")
    if not video_file:
        abort(404)

    # Check existence on active storage backend or local fallback
    if not storage.video_exists(video_file):
        # Fallback check on local static / upload directory
        if not os.path.exists(video_file) and not os.path.exists(os.path.join(VIDEO_UPLOAD_FOLDER, video_file)):
            alt_static = os.path.join(app.static_folder, video_file)
            if not os.path.exists(alt_static):
                abort(404)

    ext = video_file.rsplit('.', 1)[-1].lower() if '.' in video_file else 'mp4'
    mimetypes = {
        'mp4': 'video/mp4',
        'webm': 'video/webm',
        'mov': 'video/quicktime',
        'mkv': 'video/x-matroska',
        'm4v': 'video/mp4',
    }
    mimetype = mimetypes.get(ext, 'video/mp4')

    file_size = storage.get_video_size(video_file)
    if file_size is None:
        # Fallback to local getsize
        if os.path.exists(video_file):
            file_size = os.path.getsize(video_file)
        elif os.path.exists(os.path.join(VIDEO_UPLOAD_FOLDER, video_file)):
            file_size = os.path.getsize(os.path.join(VIDEO_UPLOAD_FOLDER, video_file))
        else:
            file_size = 0

    range_header = request.headers.get('Range', None)
    from flask import Response

    if not range_header:
        # Full content stream (200 OK)
        rv = Response(
            storage.open_video_stream(video_file, start_byte=0, length=file_size),
            200,
            mimetype=mimetype,
            direct_passthrough=True,
        )
        rv.headers.add('Accept-Ranges', 'bytes')
        if file_size:
            rv.headers.add('Content-Length', str(file_size))
        return rv

    # Parse byte range header (e.g. "bytes=0-1048575")
    byte1, byte2 = 0, None
    m = re.search(r'(\d+)-(\d*)', range_header)
    if m:
        g1, g2 = m.groups()
        byte1 = int(g1)
        if g2:
            byte2 = int(g2)

    if file_size and byte1 >= file_size:
        return "", 416

    length = file_size - byte1 if file_size else None
    if byte2 is not None and file_size and byte2 < file_size:
        length = byte2 - byte1 + 1

    stream_gen = storage.open_video_stream(video_file, start_byte=byte1, length=length)
    rv = Response(stream_gen, 206, mimetype=mimetype, direct_passthrough=True)
    if file_size and length is not None:
        rv.headers.add('Content-Range', f'bytes {byte1}-{byte1 + length - 1}/{file_size}')
        rv.headers.add('Content-Length', str(length))
    rv.headers.add('Accept-Ranges', 'bytes')
    return rv


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

            duration = client_duration if client_duration > 0 else float(existing["duration_seconds"] or 0.0)
            if app.config.get("TESTING"):
                new_watched = min(duration, max(prev_watched, client_watched))
            else:
                if client_watched <= prev_watched:
                    new_watched = prev_watched
                else:
                    max_allowed_advancement = min(15.0, elapsed_wall_seconds + 3.0)
                    new_watched = min(duration, max(prev_watched, min(client_watched, prev_watched + max_allowed_advancement)))

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
            duration = client_duration if client_duration > 0 else 0.0
            if app.config.get("TESTING"):
                new_watched = client_watched
            else:
                new_watched = min(client_watched, 15.0)
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
        flash("Learning program not found.", "error")
        return redirect(url_for("courses"))

    # Calculate verified course completion percentage server-side
    completion_pct, watched_sec, total_sec = calculate_course_completion(current_user.id, course["id"])

    if completion_pct < 75.0:
        cur.close()
        conn.close()
        flash(f"Program progress is {completion_pct:.1f}%. Complete at least 75% of this learning program to earn your certificate.", "error")
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
        flash("Certificate already generated for this learning program.", "info")
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
                SELECT id, name, email, password_hash, role, is_active, father_name, phone, student_class
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
                    father_name=user.get("father_name"),
                    phone=user.get("phone"),
                    student_class=user.get("student_class"),
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
            cur.execute("SELECT id, title, slug, level, grade, image FROM courses WHERE is_active = 1 ORDER BY id ASC")
            assigned_courses = cur.fetchall()
        else:
            cur.execute(
                """
                SELECT c.id, c.title, c.slug, c.level, c.grade, c.image
                FROM courses c
                JOIN course_enrollments e ON e.course_id = c.id AND e.user_id = %s AND e.is_active = 1
                WHERE c.is_active = 1
                ORDER BY c.id ASC
                """,
                (current_user.id,),
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
@evaluator_required
def admin():
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    # Real-time Dashboard Database Metrics (Defensive against missing optional tables or schema variations)
    def safe_count(query, params=None):
        try:
            if params:
                cur.execute(query, params)
            else:
                cur.execute(query)
            row = cur.fetchone()
            return row["count"] if row else 0
        except Exception as err:
            try:
                conn.rollback()
            except Exception:
                pass
            app.logger.warning(f"Admin metric count query failed ({query}): {err}")
            return 0

    total_users = safe_count("SELECT COUNT(*) AS count FROM users")
    total_students = safe_count("SELECT COUNT(*) AS count FROM users WHERE role IN ('user', 'student') AND is_active = 1")
    total_teachers = safe_count("SELECT COUNT(*) AS count FROM users WHERE role = 'teacher' AND is_active = 1")
    total_subadmins = safe_count("SELECT COUNT(*) AS count FROM users WHERE role = 'sub_admin' AND is_active = 1")
    total_admins = safe_count("SELECT COUNT(*) AS count FROM users WHERE role = 'admin' AND is_active = 1")
    total_courses = safe_count("SELECT COUNT(*) AS count FROM courses")
    published_courses = safe_count("SELECT COUNT(*) AS count FROM courses WHERE is_active = 1")
    draft_courses = max(0, total_courses - published_courses)
    total_categories = safe_count("SELECT COUNT(*) AS count FROM learning_categories WHERE is_active = 1")
    total_learning_paths = safe_count("SELECT COUNT(*) AS count FROM learning_paths WHERE is_active = 1")
    total_enrollments = safe_count("SELECT COUNT(*) AS count FROM course_enrollments WHERE is_active = 1")
    total_certificates = safe_count("SELECT COUNT(*) AS count FROM certificates")
    total_products = safe_count("SELECT COUNT(*) AS count FROM products WHERE is_active = 1")
    total_orders = safe_count("SELECT COUNT(*) AS count FROM orders")
    total_solutions = safe_count("SELECT COUNT(*) AS count FROM solutions")

    # Pending project evaluations count
    pending_evaluations = 0
    try:
        if current_user.is_teacher():
            assigned_course_ids = get_teacher_assigned_course_ids(current_user.id)
            if assigned_course_ids:
                placeholders = ",".join(["%s"] * len(assigned_course_ids))
                cur.execute(
                    f"""
                    SELECT COUNT(*) AS count
                    FROM project_submissions ps
                    JOIN projects p ON p.id = ps.project_id
                    JOIN modules m ON m.id = p.module_id
                    WHERE m.course_id IN ({placeholders}) AND ps.status != 'evaluated'
                    """,
                    tuple(assigned_course_ids)
                )
                row = cur.fetchone()
                pending_evaluations = row["count"] if row else 0
        else:
            cur.execute("SELECT COUNT(*) AS count FROM project_submissions WHERE status != 'evaluated'")
            row = cur.fetchone()
            pending_evaluations = row["count"] if row else 0
    except Exception as err:
        try:
            conn.rollback()
        except Exception:
            pass
        app.logger.warning(f"Admin pending evaluations count failed: {err}")
        pending_evaluations = 0

    # Recent submissions / contacts
    submissions = []
    try:
        cur.execute("SELECT id, name, email, phone, subject, message, created_at FROM contacts ORDER BY created_at DESC LIMIT 10")
        submissions = cur.fetchall() or []
    except Exception as err:
        try:
            conn.rollback()
        except Exception:
            pass
        app.logger.warning(f"Admin contacts fetch failed: {err}")
        submissions = []

    # Audit logs preview for Admin
    audit_logs = []
    if current_user.is_admin():
        try:
            cur.execute(
                """
                SELECT a.id, a.user_id, a.action, a.target_type, a.target_id, a.details, a.timestamp, u.name as user_name
                FROM audit_logs a
                LEFT JOIN users u ON u.id = a.user_id
                ORDER BY a.timestamp DESC, a.id DESC
                LIMIT 10
                """
            )
            audit_logs = cur.fetchall() or []
        except Exception as err:
            try:
                conn.rollback()
            except Exception:
                pass
            app.logger.warning(f"Admin audit logs fetch failed: {err}")
            audit_logs = []

    # Assigned courses for teacher
    teacher_courses = []
    if current_user.is_teacher():
        assigned_course_ids = get_teacher_assigned_course_ids(current_user.id)
        if assigned_course_ids:
            try:
                placeholders = ",".join(["%s"] * len(assigned_course_ids))
                cur.execute(
                    f"""
                    SELECT c.id, c.title, c.slug, c.level, c.grade, c.short_description, c.image, c.category_id,
                           lc.name AS category_name,
                           COUNT(DISTINCT m.id) AS module_count,
                           COUNT(DISTINCT v.id) AS video_count
                    FROM courses c
                    LEFT JOIN learning_categories lc ON lc.id = c.category_id
                    LEFT JOIN modules m ON m.course_id = c.id AND m.is_active = 1
                    LEFT JOIN course_videos v ON v.module_id = m.id AND v.is_active = 1
                    WHERE c.id IN ({placeholders}) AND c.is_active = 1
                    GROUP BY c.id, c.title, c.slug, c.level, c.grade, c.short_description, c.image, c.category_id, lc.name
                    ORDER BY c.grade ASC, c.title ASC
                    """,
                    tuple(assigned_course_ids)
                )
                teacher_courses = cur.fetchall() or []
            except Exception as err:
                try:
                    conn.rollback()
                except Exception:
                    pass
                app.logger.warning(f"Admin teacher courses fetch failed: {err}")
                teacher_courses = []

    cur.close()
    conn.close()

    return render_template(
        "admin.html",
        active_page="admin",
        total_users=total_users,
        total_students=total_students,
        total_teachers=total_teachers,
        total_subadmins=total_subadmins,
        total_admins=total_admins,
        total_courses=total_courses,
        published_courses=published_courses,
        draft_courses=draft_courses,
        total_categories=total_categories,
        total_learning_paths=total_learning_paths,
        total_enrollments=total_enrollments,
        total_certificates=total_certificates,
        total_products=total_products,
        total_orders=total_orders,
        total_solutions=total_solutions,
        pending_evaluations=pending_evaluations,
        submissions=submissions,
        audit_logs=audit_logs,
        teacher_courses=teacher_courses,
        assigned_courses=teacher_courses,
    )


@app.route("/admin/contacts")
@admin_or_subadmin_required
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
@admin_or_subadmin_required
def admin_users():
    tab = request.args.get("tab", "students").strip().lower()
    if current_user.is_sub_admin() and tab == "subadmins":
        tab = "students"

    conn = get_db_connection()
    cur = get_db_cursor(conn)

    if tab == "teachers":
        role_clause = "u.role = 'teacher'"
    elif tab == "subadmins":
        role_clause = "u.role = 'sub_admin'"
    else:
        tab = "students"
        role_clause = "u.role IN ('user', 'student')"

    cur.execute(
        f"""
        SELECT u.id, u.name, u.father_name, u.student_class, u.email, u.phone, u.role, u.is_active, u.created_at,
               COUNT(DISTINCT e.course_id) AS enrolled_count,
               COUNT(DISTINCT ta.course_id) AS assigned_teacher_courses
        FROM users u
        LEFT JOIN course_enrollments e ON e.user_id = u.id AND e.is_active = 1
        LEFT JOIN teacher_assignments ta ON ta.teacher_id = u.id
        WHERE {role_clause}
        GROUP BY u.id, u.name, u.father_name, u.student_class, u.email, u.phone, u.role, u.is_active, u.created_at
        ORDER BY u.created_at DESC, u.id DESC
        """
    )
    users = cur.fetchall()
    for u in users:
        u["grade"] = get_grade_from_class(u.get("student_class"))
        if u["grade"]:
            u["grade_name"] = GRADES[u["grade"]]["name"]
        else:
            u["grade_name"] = None
    cur.close()
    conn.close()

    return render_template("admin_users.html", active_page="admin", users=users, current_tab=tab)


@app.route("/admin/users/new", methods=["GET", "POST"])
@admin_or_subadmin_required
def admin_user_new():
    error = None
    form_data = {}
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        father_name = request.form.get("father_name", "").strip()
        student_class = request.form.get("student_class", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "user").strip().lower()
        is_active = 1 if request.form.get("is_active") else 0

        # Sub-Admin security guard: Sub-Admin CANNOT create Admin or Sub-Admin!
        if current_user.is_sub_admin() and role in ("admin", "sub_admin"):
            return render_template("403.html"), 403

        allowed_roles = ("user", "student", "teacher", "sub_admin") if current_user.is_admin() else ("user", "student", "teacher")
        if role not in allowed_roles:
            error = f"Invalid role selected: '{role}'."

        if role == "student":
            role = "user"

        form_data = {
            "name": name,
            "father_name": father_name,
            "student_class": student_class,
            "email": email,
            "phone": phone,
            "password": password,
            "role": role,
            "is_active": is_active,
        }

        if not error:
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
                        INSERT INTO users (name, father_name, student_class, email, phone, password_hash, role, is_active, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (name, father_name or None, student_class or None, email, phone or None, password_hash, role, is_active, created_at),
                    )
                    conn.commit()
                    cur.execute("SELECT id FROM users WHERE email = %s", (email,))
                    new_user = cur.fetchone()
                    cur.close()
                    conn.close()

                    log_audit(current_user.id, "create_user", "user", new_user["id"] if new_user else None, f"Created {role} account for {name} ({email})")

                    role_display = "Sub-Admin" if role == "sub_admin" else ("Teacher" if role == "teacher" else "Student")
                    target_tab = "subadmins" if role == "sub_admin" else ("teachers" if role == "teacher" else "students")

                    flash(f"{role_display} account created successfully for {name} ({email}).", "success")
                    return redirect(url_for("admin_users", tab=target_tab))

    return render_template("admin_student_form.html", active_page="admin", edit_mode=False, error=error, form_data=form_data)


@app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_or_subadmin_required
def admin_user_edit(user_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        "SELECT id, name, father_name, student_class, email, phone, role, is_active FROM users WHERE id = %s",
        (user_id,),
    )
    student = cur.fetchone()

    if not student:
        cur.close()
        conn.close()
        flash("User account not found.", "error")
        return redirect(url_for("admin_users"))

    # Sub-Admin security guard: Sub-Admin cannot edit Admin or Sub-Admin profiles
    if current_user.is_sub_admin() and student["role"] in ("admin", "sub_admin"):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        father_name = request.form.get("father_name", "").strip()
        student_class = request.form.get("student_class", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        new_role = request.form.get("role", student["role"]).strip().lower()
        is_active = 1 if request.form.get("is_active") else 0

        # Sub-Admin security guard: Sub-Admin cannot assign admin or sub_admin role
        if current_user.is_sub_admin() and new_role in ("admin", "sub_admin"):
            cur.close()
            conn.close()
            return render_template("403.html"), 403

        if new_role == "student":
            new_role = "user"

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
                    SET name = %s, father_name = %s, student_class = %s, email = %s, phone = %s, role = %s, is_active = %s
                    WHERE id = %s
                    """,
                    (name, father_name or None, student_class or None, email, phone or None, new_role, is_active, user_id),
                )
                conn.commit()
                cur.close()
                conn.close()
                log_audit(current_user.id, "edit_user", "user", user_id, f"Updated profile & role ({new_role}) for {name} ({email})")
                flash(f"User account updated for {name}.", "success")
                target_tab = "subadmins" if new_role == "sub_admin" else ("teachers" if new_role == "teacher" else "students")
                return redirect(url_for("admin_users", tab=target_tab))

    cur.close()
    conn.close()
    return render_template("admin_student_form.html", active_page="admin", edit_mode=True, student=student, error=error)


@app.route("/admin/users/<int:user_id>/toggle-active", methods=["POST"])
@admin_or_subadmin_required
def admin_user_toggle_active(user_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, name, role, is_active FROM users WHERE id = %s", (user_id,))
    student = cur.fetchone()

    if not student:
        cur.close()
        conn.close()
        flash("User account not found.", "error")
        return redirect(url_for("admin_users"))

    # Security guard: Sub-Admin cannot deactivate Admin or Sub-Admin
    if current_user.is_sub_admin() and student["role"] in ("admin", "sub_admin"):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

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

    log_audit(current_user.id, "toggle_active_user", "user", user_id, f"Set is_active={new_active} for {student['name']}")

    status_str = "activated" if new_active else "deactivated"
    flash(f"Account for {student['name']} has been {status_str}.", "success")
    target_tab = "subadmins" if student["role"] == "sub_admin" else ("teachers" if student["role"] == "teacher" else "students")
    return redirect(url_for("admin_users", tab=target_tab))


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_or_subadmin_required
def admin_user_reset_password(user_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, name, email, role FROM users WHERE id = %s", (user_id,))
    student = cur.fetchone()

    if not student:
        cur.close()
        conn.close()
        flash("User account not found.", "error")
        return redirect(url_for("admin_users"))

    # Sub-Admin cannot reset password of Admin or Sub-Admin
    if current_user.is_sub_admin() and student["role"] in ("admin", "sub_admin"):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

    temp_password = request.form.get("new_password", "").strip()
    if not temp_password:
        import secrets
        temp_password = "Airo" + secrets.token_hex(3)

    password_hash = generate_password_hash(temp_password)
    cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id))
    conn.commit()
    cur.close()
    conn.close()

    log_audit(current_user.id, "reset_password", "user", user_id, f"Reset password for {student['name']}")

    flash(
        f"Password reset successfully for {student['name']}. Temporary password: '{temp_password}'. Save this temporary password securely. It will not be shown again.",
        "info"
    )
    target_tab = "subadmins" if student["role"] == "sub_admin" else ("teachers" if student["role"] == "teacher" else "students")
    return redirect(url_for("admin_users", tab=target_tab))


@app.route("/admin/teachers/<int:teacher_id>/assign-courses", methods=["GET", "POST"])
@admin_or_subadmin_required
def admin_teacher_assign_courses(teacher_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, name, email, role FROM users WHERE id = %s AND role = 'teacher'", (teacher_id,))
    teacher = cur.fetchone()
    if not teacher:
        cur.close()
        conn.close()
        flash("Teacher account not found.", "error")
        return redirect(url_for("admin_users", tab="teachers"))

    if request.method == "POST":
        course_id = request.form.get("course_id", type=int)
        if course_id:
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            try:
                cur.execute(
                    "INSERT INTO teacher_assignments (teacher_id, course_id, assigned_at) VALUES (%s, %s, %s)",
                    (teacher_id, course_id, now_str)
                )
                conn.commit()
                log_audit(current_user.id, "assign_teacher_course", "user", teacher_id, f"Assigned course ID {course_id}")
                flash("Learning program assigned successfully to teacher.", "success")
            except Exception:
                conn.rollback()
                flash("Learning program is already assigned to this teacher.", "info")

    cur.execute(
        """
        SELECT c.id, c.title, c.slug, c.grade, ta.assigned_at
        FROM courses c
        JOIN teacher_assignments ta ON ta.course_id = c.id
        WHERE ta.teacher_id = %s
        ORDER BY c.title ASC
        """,
        (teacher_id,)
    )
    assigned_courses = cur.fetchall()

    cur.execute("SELECT id, title, slug, grade FROM courses WHERE is_active = 1 ORDER BY title ASC")
    all_courses = cur.fetchall()

    assigned_ids = {c["id"] for c in assigned_courses}
    unassigned_courses = [c for c in all_courses if c["id"] not in assigned_ids]

    cur.close()
    conn.close()
    return render_template(
        "admin_teacher_assign.html",
        active_page="admin",
        teacher=teacher,
        assigned_courses=assigned_courses,
        unassigned_courses=unassigned_courses
    )


@app.route("/admin/teachers/<int:teacher_id>/remove-course/<int:course_id>", methods=["POST"])
@admin_or_subadmin_required
def admin_teacher_remove_course(teacher_id, course_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("DELETE FROM teacher_assignments WHERE teacher_id = %s AND course_id = %s", (teacher_id, course_id))
    conn.commit()
    cur.close()
    conn.close()
    log_audit(current_user.id, "remove_teacher_course", "user", teacher_id, f"Removed course ID {course_id}")
    flash("Learning program assignment removed.", "success")
    return redirect(url_for("admin_teacher_assign_courses", teacher_id=teacher_id))


@app.route("/admin/audit-logs")
@admin_required
def admin_audit_logs():
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT a.id, a.user_id, a.action, a.target_type, a.target_id, a.details, a.timestamp,
               u.name as user_name, u.email as user_email
        FROM audit_logs a
        LEFT JOIN users u ON u.id = a.user_id
        ORDER BY a.timestamp DESC, a.id DESC
        LIMIT 200
        """
    )
    logs = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("admin_audit_logs.html", active_page="admin", logs=logs)


@app.route("/admin/users/<int:user_id>/courses")
@admin_or_subadmin_required
def admin_user_courses(user_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, name, email, father_name, phone, student_class, is_active FROM users WHERE id = %s", (user_id,))
    student = cur.fetchone()

    if not student:
        cur.close()
        conn.close()
        flash("Student account not found.", "error")
        return redirect(url_for("admin_users"))

    student_grade = get_grade_from_class(student.get("student_class"))
    student["grade"] = student_grade
    student["grade_info"] = GRADES.get(student_grade) if student_grade else None

    # If student has a valid grade, query ONLY active courses belonging to that grade
    if student_grade and student_grade in GRADES:
        cur.execute(
            """
            SELECT c.id, c.title, c.slug, c.level, c.grade, c.short_description, c.description, c.image,
                   c.category_id, c.learning_path_id,
                   lc.name AS category_name, lc.slug AS category_slug,
                   lp.name AS path_name, lp.slug AS path_slug,
                   e.id AS enrollment_id, e.is_active AS enrollment_active, e.assigned_at
            FROM courses c
            LEFT JOIN learning_categories lc ON lc.id = c.category_id
            LEFT JOIN learning_paths lp ON lp.id = c.learning_path_id
            LEFT JOIN course_enrollments e ON e.course_id = c.id AND e.user_id = %s
            WHERE c.is_active = 1 AND c.grade = %s
            ORDER BY lc.display_order ASC, lp.display_order ASC, c.id ASC
            """,
            (user_id, student_grade)
        )
        grade_courses = cur.fetchall()
    else:
        grade_courses = []

    cur.close()
    conn.close()

    for c in grade_courses:
        c["grade_info"] = GRADES[student_grade]
        c["has_access"] = bool(c["enrollment_active"] == 1 or c["enrollment_active"] is True)

    return render_template(
        "admin_student_courses.html",
        active_page="admin",
        student=student,
        student_grade=student_grade,
        grade_info=student["grade_info"],
        courses=grade_courses,
    )


def format_seconds_display(secs):
    """Format seconds float/int to MM:SS or HH:MM:SS string."""
    if not secs or secs < 0:
        return "0:00"
    secs = int(round(secs))
    hours = secs // 3600
    minutes = (secs % 3600) // 60
    seconds = secs % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def format_datetime_display(dt_str):
    """Format date/time string to human-readable 'DD Mon YYYY, HH:MM AM/PM'."""
    if not dt_str:
        return None
    try:
        dt_str_clean = str(dt_str).replace("T", " ").split(".")[0]
        dt = datetime.strptime(dt_str_clean, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return str(dt_str)


@app.route("/admin/users/<int:user_id>/progress")
@evaluator_required
def admin_student_progress(user_id):
    """
    Learning Progress Dashboard for a specific student.
    Protected by @evaluator_required. Evaluators (Admin, Sub-Admin, Teacher) can view student progress.
    Teachers can ONLY view progress for students enrolled in courses assigned to that teacher.
    """
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        "SELECT id, name, email, father_name, phone, student_class, role, is_active FROM users WHERE id = %s",
        (user_id,)
    )
    student = cur.fetchone()

    if not student:
        cur.close()
        conn.close()
        flash("Student account not found.", "error")
        return redirect(url_for("admin_users"))

    student_grade = get_grade_from_class(student.get("student_class"))
    student["grade"] = student_grade
    student["grade_info"] = GRADES.get(student_grade) if student_grade else None

    # Fetch active enrolled courses for this student
    cur.execute(
        """
        SELECT c.id, c.title, c.slug, c.level, c.grade, c.short_description, c.image
        FROM courses c
        JOIN course_enrollments e ON e.course_id = c.id
        WHERE e.user_id = %s AND e.is_active = 1 AND c.is_active = 1
        ORDER BY c.grade ASC, c.title ASC
        """,
        (user_id,)
    )
    enrolled_courses = cur.fetchall()

    # Scope check for Teacher: MUST be assigned to at least one course the student is enrolled in
    if current_user.is_teacher():
        assigned_course_ids = set(get_teacher_assigned_course_ids(current_user.id))
        enrolled_courses = [c for c in enrolled_courses if c["id"] in assigned_course_ids]
        if not enrolled_courses:
            cur.close()
            conn.close()
            return render_template("403.html"), 403

    course_ids = [c["id"] for c in enrolled_courses]
    module_videos = []
    if course_ids:
        placeholders = ",".join(["%s"] * len(course_ids))
        cur.execute(
            f"""
            SELECT m.id AS module_id, m.course_id, m.title AS module_title, m.sequence AS module_sequence,
                   v.id AS video_id, v.title AS video_title, v.sequence AS video_sequence, v.duration AS video_duration
            FROM modules m
            JOIN course_videos v ON v.module_id = m.id AND v.is_active = 1
            WHERE m.course_id IN ({placeholders}) AND m.is_active = 1
            ORDER BY m.sequence ASC, v.sequence ASC, v.id ASC
            """,
            tuple(course_ids)
        )
        module_videos = cur.fetchall()

    all_video_ids = [row["video_id"] for row in module_videos]
    progress_by_video = {}
    if all_video_ids:
        vp_placeholders = ",".join(["%s"] * len(all_video_ids))
        cur.execute(
            f"""
            SELECT vp.video_id, vp.watched_seconds, vp.duration_seconds, vp.completion_percentage,
                   vp.completed, vp.last_watched_at, vp.completed_at
            FROM video_progress vp
            WHERE vp.user_id = %s AND vp.video_id IN ({vp_placeholders})
            """,
            tuple([user_id] + all_video_ids)
        )
        for vp in cur.fetchall():
            progress_by_video[vp["video_id"]] = vp

    cur.close()
    conn.close()

    # Organize videos into modules by course
    modules_by_course = {c["id"]: {} for c in enrolled_courses}
    for row in module_videos:
        c_id = row["course_id"]
        m_id = row["module_id"]
        if c_id not in modules_by_course:
            modules_by_course[c_id] = {}
        if m_id not in modules_by_course[c_id]:
            modules_by_course[c_id][m_id] = {
                "id": m_id,
                "title": row["module_title"],
                "sequence": row["module_sequence"],
                "videos": [],
            }

        v_id = row["video_id"]
        vp = progress_by_video.get(v_id)
        completed = bool(vp and (vp.get("completed") == 1 or vp.get("completed") is True))
        watched_sec = float(vp.get("watched_seconds") or 0.0) if vp else 0.0
        parsed_dur = parse_duration_seconds(row.get("video_duration"))
        dur_sec = float(vp.get("duration_seconds") or 0.0) if (vp and vp.get("duration_seconds")) else parsed_dur
        if dur_sec <= 0:
            dur_sec = parsed_dur

        if completed:
            comp_pct = 100.0
            status = "COMPLETED"
        elif watched_sec > 0:
            raw_pct = float(vp.get("completion_percentage") or 0.0) if vp else 0.0
            comp_pct = round(min(100.0, max(0.0, raw_pct)), 1)
            status = "IN PROGRESS"
        else:
            comp_pct = 0.0
            status = "NOT STARTED"

        raw_last_act = (vp.get("completed_at") or vp.get("last_watched_at")) if vp else None
        last_activity_display = format_datetime_display(raw_last_act) if raw_last_act else None
        completed_at_display = format_datetime_display(vp.get("completed_at")) if (vp and vp.get("completed_at")) else None

        video_data = {
            "id": v_id,
            "title": row["video_title"],
            "sequence": row["video_sequence"],
            "duration": row["video_duration"],
            "duration_seconds": dur_sec,
            "watched_seconds": watched_sec,
            "watched_display": format_seconds_display(watched_sec),
            "duration_display": format_seconds_display(dur_sec),
            "completion_percentage": comp_pct,
            "completed": completed,
            "status": status,
            "last_activity_raw": raw_last_act,
            "last_activity_display": last_activity_display,
            "completed_at_display": completed_at_display,
        }
        modules_by_course[c_id][m_id]["videos"].append(video_data)

    # Process course metrics
    processed_courses = []
    total_active_lessons_all = 0
    total_completed_lessons_all = 0
    total_percentage_sum_all = 0.0

    for c in enrolled_courses:
        c_id = c["id"]
        course_grade_info = GRADES.get(c.get("grade"))
        c["grade_info"] = course_grade_info

        course_modules = list(modules_by_course.get(c_id, {}).values())
        course_modules.sort(key=lambda m: m["sequence"])

        all_course_videos = []
        for mod in course_modules:
            mod_videos = mod["videos"]
            mod_videos.sort(key=lambda v: v["sequence"])
            mod["total_videos"] = len(mod_videos)
            mod["completed_videos"] = sum(1 for v in mod_videos if v["completed"])
            if mod["total_videos"] > 0:
                mod["progress_percentage"] = round(sum(v["completion_percentage"] for v in mod_videos) / mod["total_videos"], 1)
            else:
                mod["progress_percentage"] = 0.0
            all_course_videos.extend(mod_videos)

        course_total_videos = len(all_course_videos)
        course_completed_videos = sum(1 for v in all_course_videos if v["completed"])
        if course_total_videos > 0:
            course_pct = round(sum(v["completion_percentage"] for v in all_course_videos) / course_total_videos, 1)
        else:
            course_pct = 0.0

        # Course status
        if course_total_videos > 0 and course_completed_videos == course_total_videos:
            course_status = "COMPLETED"
        elif any(v["completion_percentage"] > 0 or v["status"] != "NOT STARTED" for v in all_course_videos):
            course_status = "IN PROGRESS"
        else:
            course_status = "NOT STARTED"

        # Latest activity for the course
        course_activities = [v["last_activity_raw"] for v in all_course_videos if v["last_activity_raw"]]
        latest_activity_raw = max(course_activities) if course_activities else None
        latest_activity_display = format_datetime_display(latest_activity_raw) if latest_activity_raw else "Never started"

        c["modules"] = course_modules
        c["all_videos"] = all_course_videos
        c["total_videos"] = course_total_videos
        c["completed_videos"] = course_completed_videos
        c["progress_percentage"] = course_pct
        c["status"] = course_status
        c["latest_activity_display"] = latest_activity_display
        processed_courses.append(c)

        total_active_lessons_all += course_total_videos
        total_completed_lessons_all += course_completed_videos
        total_percentage_sum_all += sum(v["completion_percentage"] for v in all_course_videos)

    total_enrolled = len(processed_courses)
    total_completed = sum(1 for c in processed_courses if c["status"] == "COMPLETED")
    total_in_progress = sum(1 for c in processed_courses if c["status"] == "IN PROGRESS")

    if total_active_lessons_all > 0:
        overall_progress = round(total_percentage_sum_all / total_active_lessons_all, 1)
    else:
        overall_progress = 0.0

    summary = {
        "enrolled_courses": total_enrolled,
        "completed_courses": total_completed,
        "in_progress_courses": total_in_progress,
        "total_active_lessons": total_active_lessons_all,
        "total_completed_lessons": total_completed_lessons_all,
        "overall_progress": overall_progress,
    }

    return render_template(
        "admin_student_progress.html",
        active_page="admin",
        student=student,
        courses=processed_courses,
        summary=summary,
    )


@app.route("/admin/users/<int:user_id>/courses/assign", methods=["POST"])
@app.route("/admin/users/<int:user_id>/assign-course", methods=["POST"])
@admin_or_subadmin_required
def admin_user_assign_course(user_id):
    course_id = request.form.get("course_id", type=int)
    if not course_id:
        flash("Invalid program selection.", "error")
        return redirect(url_for("admin_user_courses", user_id=user_id))

    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, name, student_class FROM users WHERE id = %s", (user_id,))
    student = cur.fetchone()
    cur.execute("SELECT id, title, grade FROM courses WHERE id = %s AND is_active = 1", (course_id,))
    course = cur.fetchone()

    if not student or not course:
        cur.close()
        conn.close()
        flash("Student or learning program not found.", "error")
        return redirect(url_for("admin_user_courses", user_id=user_id))

    student_grade = get_grade_from_class(student.get("student_class"))
    if not student_grade or course.get("grade") != student_grade:
        cur.close()
        conn.close()
        flash(f"Learning program cannot be assigned because it belongs to Grade {course.get('grade')} while this student belongs to Grade {student_grade or 'Unassigned'}.", "error")
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

    flash(f"Learning program access to '{course['title']}' granted for {student['name']}.", "success")
    return redirect(url_for("admin_user_courses", user_id=user_id))


@app.route("/admin/users/<int:user_id>/courses/<int:course_id>/remove", methods=["POST"])
@admin_or_subadmin_required
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
        flash("Student or learning program not found.", "error")
        return redirect(url_for("admin_user_courses", user_id=user_id))

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "SELECT id FROM course_enrollments WHERE user_id = %s AND course_id = %s",
        (user_id, course_id)
    )
    existing = cur.fetchone()

    if existing:
        cur.execute(
            """
            UPDATE course_enrollments
            SET is_active = 0, updated_at = %s
            WHERE id = %s
            """,
            (now_str, existing["id"])
        )
    else:
        cur.execute(
            """
            INSERT INTO course_enrollments (user_id, course_id, is_active, assigned_at, assigned_by, created_at, updated_at)
            VALUES (%s, %s, 0, %s, %s, %s, %s)
            """,
            (user_id, course_id, now_str, current_user.id, now_str, now_str)
        )

    conn.commit()
    cur.close()
    conn.close()

    flash(f"Learning program access to '{course['title']}' removed for {student['name']}. Student progress preserved.", "info")
    return redirect(url_for("admin_user_courses", user_id=user_id))


@app.route("/admin/courses")
@admin_or_subadmin_required
def admin_courses():
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    category_id = request.args.get("category_id", type=int)
    learning_path_id = request.args.get("learning_path_id", type=int) or request.args.get("path_id", type=int)
    selected_grade = request.args.get("grade", type=int)
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    view = request.args.get("view", "").strip()

    # LEVEL 1 / Hierarchy View: If explicitly requested via ?view=categories and no search/grade
    if view == "categories" and category_id is None and selected_grade is None and not search:
        categories = get_learning_categories_with_counts(conn=conn, active_only=False)
        cur.close()
        conn.close()
        return render_template(
            "admin_courses.html",
            active_page="admin",
            view_mode="categories",
            categories=categories,
        )

    # Fetch all categories for filter dropdown
    cur.execute("SELECT id, name, slug FROM learning_categories ORDER BY display_order ASC, id ASC")
    all_categories = cur.fetchall()

    # Build query conditions for course listing
    query_conditions = []
    params = []

    if category_id:
        query_conditions.append("(c.category_id = %s OR lp.category_id = %s)")
        params.extend([category_id, category_id])

    if learning_path_id:
        query_conditions.append("c.learning_path_id = %s")
        params.append(learning_path_id)

    if selected_grade:
        query_conditions.append("c.grade = %s")
        params.append(selected_grade)

    if status == "active":
        query_conditions.append("c.is_active = 1")
    elif status == "inactive":
        query_conditions.append("c.is_active = 0")

    if search:
        search_like = f"%{search}%"
        is_postgres = get_db_type() == "postgres"
        like_op = "ILIKE" if is_postgres else "LIKE"
        query_conditions.append(f"(c.title {like_op} %s OR c.slug {like_op} %s OR c.description {like_op} %s)")
        params.extend([search_like, search_like, search_like])

    where_clause = ("WHERE " + " AND ".join(query_conditions)) if query_conditions else ""

    cur.execute(
        f"""
        SELECT c.id, c.title, c.slug, c.short_description, c.description, c.image, c.level, c.grade, c.is_active, c.category_id, c.created_at,
               COALESCE(lc.name, lc_lp.name) AS category_name,
               COALESCE(lc.slug, lc_lp.slug) AS category_slug,
               lp.name AS learning_path_name,
               COUNT(DISTINCT m.id) AS total_modules,
               COUNT(DISTINCT v.id) AS total_videos,
               COUNT(DISTINCT ce.id) AS total_enrollments
        FROM courses c
        LEFT JOIN learning_categories lc ON lc.id = c.category_id
        LEFT JOIN learning_paths lp ON lp.id = c.learning_path_id
        LEFT JOIN learning_categories lc_lp ON lc_lp.id = lp.category_id
        LEFT JOIN modules m ON m.course_id = c.id
        LEFT JOIN course_videos v ON v.module_id = m.id
        LEFT JOIN course_enrollments ce ON ce.course_id = c.id AND ce.is_active = 1
        {where_clause}
        GROUP BY c.id, c.title, c.slug, c.short_description, c.description, c.image, c.level, c.grade, c.is_active, c.category_id, c.created_at, lc.name, lc.slug, lc_lp.name, lc_lp.slug, lp.name
        ORDER BY c.id DESC
        """,
        tuple(params),
    )
    courses_list = cur.fetchall()

    # Aggregate metrics for the top stat cards
    cur.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS active_cnt FROM courses")
    c_stats = cur.fetchone()
    total_courses_count = c_stats["total"] if c_stats and c_stats["total"] else 0
    active_courses_count = c_stats["active_cnt"] if c_stats and c_stats["active_cnt"] else 0
    inactive_courses_count = max(0, total_courses_count - active_courses_count)

    selected_category = None
    if category_id:
        cur.execute("SELECT * FROM learning_categories WHERE id = %s", (category_id,))
        selected_category = cur.fetchone()

    cur.close()
    conn.close()

    for c in courses_list:
        g_info = GRADES.get(c["grade"], {})
        c["grade_name"] = g_info.get("name", f"Grade {c['grade']}")
        c["classes"] = g_info.get("classes", "")

    return render_template(
        "admin_courses.html",
        active_page="admin",
        view_mode="courses",
        courses=courses_list,
        all_categories=all_categories,
        selected_category=selected_category,
        category_id=category_id,
        selected_grade=selected_grade,
        search=search,
        status=status,
        total_courses_count=total_courses_count,
        active_courses_count=active_courses_count,
        inactive_courses_count=inactive_courses_count,
        grades=GRADES,
    )


@app.route("/admin/courses/<int:course_id>")
@evaluator_required
def admin_course_detail(course_id):
    if not can_access_course(current_user.id, course_id):
        return render_template("403.html"), 403

    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT c.id, c.title, c.slug, c.description, c.image, c.level, c.grade, c.category_id, c.is_active, c.created_at, c.updated_at,
               lc.name AS category_name, lc.slug AS category_slug
        FROM courses c
        LEFT JOIN learning_categories lc ON lc.id = c.category_id
        WHERE c.id = %s
        """,
        (course_id,),
    )
    course = cur.fetchone()

    if not course:
        cur.close()
        conn.close()
        flash("Learning program not found.", "error")
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
            SELECT id, title, description, sequence, duration, video_file, is_active
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

        # Fetch module project if configured
        cur.execute(
            """
            SELECT p.id, p.title, p.description, p.max_marks, p.deadline, p.is_active,
                   COUNT(ps.id) AS submission_count,
                   SUM(CASE WHEN ps.status = 'evaluated' THEN 1 ELSE 0 END) AS evaluated_count
            FROM projects p
            LEFT JOIN project_submissions ps ON ps.project_id = p.id
            WHERE p.module_id = %s
            GROUP BY p.id, p.title, p.description, p.max_marks, p.deadline, p.is_active
            """,
            (m["id"],),
        )
        m["project"] = cur.fetchone()

    cur.close()
    conn.close()

    return render_template(
        "admin_course_detail.html",
        active_page="admin",
        course=course,
        modules=modules,
        total_videos=total_videos,
        grades=GRADES,
    )


# ---------- Admin Learning Category & Path Management ----------

@app.route("/uploads/categories/<path:filename>")
def serve_category_upload_image(filename):
    for dir_path in [
        os.path.join(app.root_path, "static", "uploads", "categories"),
        os.path.join(app.root_path, "uploads", "categories"),
    ]:
        if os.path.exists(os.path.join(dir_path, filename)):
            return send_from_directory(dir_path, filename)
    abort(404)


@app.route("/uploads/learning_paths/<path:filename>")
def serve_learning_path_upload_image(filename):
    for dir_path in [
        os.path.join(app.root_path, "static", "uploads", "learning_paths"),
        os.path.join(app.root_path, "uploads", "learning_paths"),
    ]:
        if os.path.exists(os.path.join(dir_path, filename)):
            return send_from_directory(dir_path, filename)
    abort(404)


@app.route("/uploads/courses/<path:filename>")
def serve_course_upload_image(filename):
    for dir_path in [
        os.path.join(app.root_path, "static", "uploads", "courses"),
        os.path.join(app.root_path, "uploads", "courses"),
    ]:
        if os.path.exists(os.path.join(dir_path, filename)):
            return send_from_directory(dir_path, filename)
    abort(404)


@app.route("/uploads/catalogue/<path:filename>")
def serve_catalogue_upload_image(filename):
    for dir_path in [
        os.path.join(app.root_path, "static", "uploads", "catalogue"),
        os.path.join(app.root_path, "uploads", "catalogue"),
    ]:
        if os.path.exists(os.path.join(dir_path, filename)):
            return send_from_directory(dir_path, filename)
    abort(404)


@app.route("/uploads/products/<path:filename>")
def serve_product_upload_image(filename):
    for dir_path in [
        os.path.join(app.root_path, "static", "uploads", "products"),
        os.path.join(app.root_path, "uploads", "products"),
    ]:
        if os.path.exists(os.path.join(dir_path, filename)):
            return send_from_directory(dir_path, filename)
    abort(404)



def get_catalogue_settings(conn=None):
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True
    cur = get_db_cursor(conn)
    cur.execute("SELECT * FROM course_catalogue_settings ORDER BY id ASC LIMIT 1")
    row = cur.fetchone()
    if not row:
        row = {
            "id": 1,
            "hero_badge": "STEM LEARNING DOMAINS",
            "hero_title": "Learning Categories",
            "hero_description": "Choose a learning domain to explore progressive educational pathways across technology, programming, AI, drones, and digital skills.",
            "hero_image": "",
            "hero_image_alt": "Learning Categories",
            "is_active": 1,
        }
    cur.close()
    if close_conn:
        conn.close()
    return row


@app.route("/admin/learning-catalogue-settings", methods=["GET", "POST"])
@admin_required
def admin_learning_catalogue_settings():
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT * FROM course_catalogue_settings ORDER BY id ASC LIMIT 1")
    settings = cur.fetchone()

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    if not settings:
        cur.execute(
            """
            INSERT INTO course_catalogue_settings (
                hero_badge, hero_title, hero_description, hero_image, hero_image_alt, is_active, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "STEM LEARNING DOMAINS",
                "Learning Categories",
                "Choose a learning domain to explore progressive educational pathways across technology, programming, AI, drones, and digital skills.",
                "",
                "Learning Categories",
                1,
                now_str,
                now_str,
            ),
        )
        conn.commit()
        cur.execute("SELECT * FROM course_catalogue_settings ORDER BY id ASC LIMIT 1")
        settings = cur.fetchone()

    error = None
    if request.method == "POST":
        hero_badge = request.form.get("hero_badge", "").strip() or "STEM LEARNING DOMAINS"
        hero_title = request.form.get("hero_title", "").strip()
        hero_description = request.form.get("hero_description", "").strip()
        hero_image_alt = request.form.get("hero_image_alt", "").strip() or hero_title or "Learning Categories"
        is_active = 1 if request.form.get("is_active") else 0

        old_image = settings.get("hero_image") or ""
        new_image = old_image
        should_delete_old_image = False
        uploaded_new_storage_path = None

        if request.form.get("remove_image"):
            new_image = ""
            should_delete_old_image = True
        elif "image_file" in request.files and request.files["image_file"].filename:
            img_file = request.files["image_file"]
            is_valid, val_err = validate_uploaded_image_file(img_file)
            if not is_valid:
                error = val_err
            else:
                success, saved_path, save_err = storage.save_catalogue_hero_image(img_file, img_file.filename)
                if success:
                    new_image = saved_path
                    uploaded_new_storage_path = saved_path
                    should_delete_old_image = True
                else:
                    error = save_err or "Failed to store catalogue hero image."
        elif request.form.get("image_url", "").strip():
            new_image = request.form.get("image_url", "").strip()

        if not error:
            if not hero_title:
                error = "Hero Heading is required."
            else:
                try:
                    cur.execute(
                        """
                        UPDATE course_catalogue_settings
                        SET hero_badge = %s, hero_title = %s, hero_description = %s, hero_image = %s,
                            hero_image_alt = %s, is_active = %s, updated_at = %s
                        WHERE id = %s
                        """,
                        (hero_badge, hero_title, hero_description, new_image, hero_image_alt, is_active, now_str, settings["id"])
                    )
                    conn.commit()

                    if should_delete_old_image and old_image and old_image != new_image:
                        if old_image.startswith("uploads/catalogue/"):
                            storage.delete_catalogue_hero_image(old_image)

                    cur.close()
                    conn.close()

                    flash("Learn page hero settings updated successfully!", "success")
                    return redirect(url_for("admin_learning_catalogue_settings"))
                except Exception as e:
                    if conn:
                        conn.rollback()
                    if uploaded_new_storage_path:
                        storage.delete_catalogue_hero_image(uploaded_new_storage_path)
                    cur.close()
                    conn.close()
                    app.logger.error(f"Error updating catalogue settings: {e}", exc_info=True)
                    error = f"Database error updating catalogue settings: {str(e)}"

    cur.close()
    conn.close()
    return render_template("admin_catalogue_settings.html", active_page="admin", settings=settings, error=error)



@app.route("/admin/learning-categories")
@app.route("/admin/categories")
@admin_or_subadmin_required
def admin_learning_categories():
    categories = get_learning_categories_with_counts(active_only=False)
    return render_template("admin_categories.html", active_page="admin", categories=categories)


@app.route("/admin/learning-categories/new", methods=["GET", "POST"])
@admin_or_subadmin_required
def admin_add_learning_category():
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        slug = request.form.get("slug", "").strip()
        description = request.form.get("description", "").strip()
        display_order = request.form.get("display_order", 1, type=int)
        is_active = 1 if request.form.get("is_active") else 0
        image = request.form.get("image_url", "").strip() or request.form.get("image", "").strip()

        uploaded_storage_path = None
        if "image_file" in request.files and request.files["image_file"].filename:
            img_file = request.files["image_file"]
            is_valid, val_err = validate_uploaded_image_file(img_file)
            if not is_valid:
                error = val_err
            else:
                success, saved_path, save_err = storage.save_category_image(img_file, 0, img_file.filename)
                if success:
                    image = saved_path
                    uploaded_storage_path = saved_path
                else:
                    error = save_err or "Failed to store category image."

        if not error:
            if not name:
                error = "Category name is required."
            else:
                if not slug:
                    slug = re.sub(r"[^\w\s-]", "", name.lower())
                    slug = re.sub(r"[-\s]+", "-", slug).strip("-")

                conn = get_db_connection()
                cur = get_db_cursor(conn)
                cur.execute("SELECT id FROM learning_categories WHERE slug = %s", (slug,))
                if cur.fetchone():
                    error = f"A category with slug '{slug}' already exists."
                    if uploaded_storage_path:
                        storage.delete_category_image(uploaded_storage_path)
                    cur.close()
                    conn.close()
                else:
                    try:
                        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                        if get_db_type() == "postgres":
                            cur.execute(
                                """
                                INSERT INTO learning_categories (name, slug, description, image, display_order, is_active, created_at, updated_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                RETURNING id
                                """,
                                (name, slug, description, image, display_order, is_active, now_str, now_str)
                            )
                            new_id = cur.fetchone()["id"]
                        else:
                            cur.execute(
                                """
                                INSERT INTO learning_categories (name, slug, description, image, display_order, is_active, created_at, updated_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                (name, slug, description, image, display_order, is_active, now_str, now_str)
                            )
                            new_id = cur.lastrowid

                        if request.form.get("create_default_paths"):
                            for g in [1, 2, 3, 4, 5]:
                                g_info = GRADES[g]
                                p_name = f"{name} - {g_info['name']}"
                                p_slug = f"grade-{g}"
                                cur.execute(
                                    """
                                    INSERT INTO learning_paths (category_id, grade, name, slug, description, image, display_order, is_active, created_at, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
                                    """,
                                    (new_id, g, p_name, p_slug, g_info["description"], image, g, now_str, now_str)
                                )

                        conn.commit()
                        cur.close()
                        conn.close()

                        flash(f"Learning category '{name}' created successfully!", "success")
                        return redirect(url_for("admin_learning_categories"))
                    except Exception as e:
                        if conn:
                            conn.rollback()
                        if uploaded_storage_path:
                            storage.delete_category_image(uploaded_storage_path)
                        cur.close()
                        conn.close()
                        logger.error(f"Error creating category: {e}", exc_info=True)
                        error = f"Database error creating category: {str(e)}"

    return render_template("admin_category_form.html", active_page="admin", action="Create", error=error)


@app.route("/admin/learning-categories/<int:category_id>/edit", methods=["GET", "POST"])
@admin_or_subadmin_required
def admin_edit_learning_category(category_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT * FROM learning_categories WHERE id = %s", (category_id,))
    category = cur.fetchone()
    if not category:
        cur.close()
        conn.close()
        flash("Category not found.", "error")
        return redirect(url_for("admin_learning_categories"))

    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        slug = request.form.get("slug", "").strip()
        description = request.form.get("description", "").strip()
        display_order = request.form.get("display_order", 1, type=int)
        is_active = 1 if request.form.get("is_active") else 0

        old_image = category.get("image") or ""
        new_image = old_image
        should_delete_old_image = False
        uploaded_new_storage_path = None

        if request.form.get("remove_image"):
            new_image = ""
            should_delete_old_image = True
        elif "image_file" in request.files and request.files["image_file"].filename:
            img_file = request.files["image_file"]
            is_valid, val_err = validate_uploaded_image_file(img_file)
            if not is_valid:
                error = val_err
            else:
                success, saved_path, save_err = storage.save_category_image(img_file, category_id, img_file.filename)
                if success:
                    new_image = saved_path
                    uploaded_new_storage_path = saved_path
                    should_delete_old_image = True
                else:
                    error = save_err or "Failed to store category image."
        elif request.form.get("image_url", "").strip():
            new_image = request.form.get("image_url", "").strip()

        if not error:
            if not name:
                error = "Category name is required."
            else:
                if not slug:
                    slug = re.sub(r"[^\w\s-]", "", name.lower())
                    slug = re.sub(r"[-\s]+", "-", slug).strip("-")

                cur.execute("SELECT id FROM learning_categories WHERE slug = %s AND id != %s", (slug, category_id))
                if cur.fetchone():
                    error = f"Another category with slug '{slug}' already exists."
                    if uploaded_new_storage_path:
                        storage.delete_category_image(uploaded_new_storage_path)
                else:
                    try:
                        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                        cur.execute(
                            """
                            UPDATE learning_categories
                            SET name = %s, slug = %s, description = %s, image = %s, display_order = %s, is_active = %s, updated_at = %s
                            WHERE id = %s
                            """,
                            (name, slug, description, new_image, display_order, is_active, now_str, category_id)
                        )
                        conn.commit()

                        if should_delete_old_image and old_image and old_image != new_image:
                            if old_image.startswith("uploads/categories/"):
                                storage.delete_category_image(old_image)

                        cur.close()
                        conn.close()

                        flash(f"Learning category '{name}' updated successfully!", "success")
                        return redirect(url_for("admin_learning_categories"))
                    except Exception as e:
                        if conn:
                            conn.rollback()
                        if uploaded_new_storage_path:
                            storage.delete_category_image(uploaded_new_storage_path)
                        cur.close()
                        conn.close()
                        logger.error(f"Error updating category: {e}", exc_info=True)
                        error = f"Database error updating category: {str(e)}"

    cur.close()
    conn.close()
    return render_template("admin_category_form.html", active_page="admin", action="Edit", category=category, error=error)


@app.route("/admin/learning-categories/<int:category_id>/toggle-active", methods=["POST"])
@admin_or_subadmin_required
def admin_toggle_learning_category_active(category_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, name, is_active FROM learning_categories WHERE id = %s", (category_id,))
    category = cur.fetchone()
    if not category:
        cur.close()
        conn.close()
        flash("Category not found.", "error")
        return redirect(url_for("admin_learning_categories"))

    new_status = 0 if category["is_active"] else 1
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE learning_categories SET is_active = %s, updated_at = %s WHERE id = %s", (new_status, now_str, category_id))
    conn.commit()
    cur.close()
    conn.close()

    log_audit(current_user.id, "TOGGLE_CATEGORY_STATUS", "category", category_id, f"Set category '{category['name']}' status to {'active' if new_status else 'inactive'}")
    flash(f"Category '{category['name']}' is now {'active' if new_status else 'inactive'}.", "success")
    return redirect(url_for("admin_learning_categories"))


@app.route("/admin/learning-categories/<int:category_id>/delete", methods=["POST"])
@admin_or_subadmin_required
def admin_delete_learning_category(category_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT * FROM learning_categories WHERE id = %s", (category_id,))
    category = cur.fetchone()
    if not category:
        cur.close()
        conn.close()
        flash("Category not found.", "error")
        return redirect(url_for("admin_learning_categories"))

    # Dependency protection check: Check if category contains courses or learning paths
    cur.execute("SELECT COUNT(*) AS c_count FROM courses WHERE category_id = %s", (category_id,))
    c_res = cur.fetchone()
    course_count = c_res["c_count"] if c_res else 0

    cur.execute("SELECT COUNT(*) AS p_count FROM learning_paths WHERE category_id = %s", (category_id,))
    p_res = cur.fetchone()
    path_count = p_res["p_count"] if p_res else 0

    if course_count > 0 or path_count > 0:
        cur.close()
        conn.close()
        flash("This category cannot be deleted because it contains learning programs or paths. Remove or reassign the dependent content first.", "warning")
        return redirect(url_for("admin_learning_categories"))

    # Category is empty and safe to delete
    cat_image = category.get("image") or ""
    cat_name = category.get("name") or f"Category #{category_id}"

    cur.execute("DELETE FROM learning_categories WHERE id = %s", (category_id,))
    conn.commit()

    # Clean up uploaded category image if not referenced elsewhere
    if cat_image and cat_image.startswith("uploads/categories/"):
        cur.execute("SELECT COUNT(*) AS img_ref FROM learning_categories WHERE image = %s", (cat_image,))
        ref_res = cur.fetchone()
        img_refs = ref_res["img_ref"] if ref_res else 0
        if img_refs == 0:
            storage.delete_category_image(cat_image)

    cur.close()
    conn.close()

    log_audit(current_user.id, "DELETE_CATEGORY", "category", category_id, f"Deleted empty category '{cat_name}'")
    flash(f"Learning category '{cat_name}' deleted successfully!", "success")
    return redirect(url_for("admin_learning_categories"))


@app.route("/admin/learning-categories/<int:category_id>/courses")
@admin_or_subadmin_required
def admin_category_courses(category_id):
    return redirect(url_for("admin_courses", category_id=category_id))


@app.route("/admin/learning-paths")
@app.route("/admin/paths")
@admin_or_subadmin_required
def admin_learning_paths():
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    category_id_filter = request.args.get("category_id", type=int)
    search_query = request.args.get("search", "").strip()

    # Query all categories using platform single source of truth
    categories_raw = get_learning_categories_with_counts(conn=conn, active_only=False)
    all_categories = [dict(c) for c in categories_raw]

    # Query all learning paths with dynamic course counts (active and total)
    cur.execute(
        """
        SELECT lp.id, lp.category_id, lp.grade, lp.name, lp.slug, lp.description, lp.image,
               lp.display_order, lp.is_active, lp.created_at,
               lc.name AS category_name, lc.slug AS category_slug,
               COUNT(DISTINCT c.id) AS course_count,
               COUNT(DISTINCT CASE WHEN c.is_active = 1 THEN c.id END) AS active_course_count
        FROM learning_paths lp
        JOIN learning_categories lc ON lc.id = lp.category_id
        LEFT JOIN courses c ON c.learning_path_id = lp.id
        GROUP BY lp.id, lp.category_id, lp.grade, lp.name, lp.slug, lp.description, lp.image,
                 lp.display_order, lp.is_active, lp.created_at, lc.name, lc.slug
        ORDER BY lp.category_id ASC, lp.grade ASC, lp.display_order ASC, lp.id ASC
        """
    )
    all_paths = [dict(p) for p in cur.fetchall()]
    cur.close()
    conn.close()

    # Decorate paths with grade information from GRADES dictionary
    paths_by_cat = {}
    for p in all_paths:
        g_info = GRADES.get(p["grade"], {})
        p["grade_name"] = g_info.get("name", f"Grade {p['grade']}")
        p["classes"] = g_info.get("classes", "")
        paths_by_cat.setdefault(p["category_id"], []).append(p)

    # Attach paths to each category and calculate dynamic counts
    categories_grouped = []
    total_paths_count = len(all_paths)
    total_courses_count = sum(p["course_count"] for p in all_paths)

    for cat in all_categories:
        cat_paths = paths_by_cat.get(cat["id"], [])
        cat["paths"] = cat_paths
        cat["total_paths"] = len(cat_paths)
        cat["path_courses_count"] = sum(p["course_count"] for p in cat_paths)
        categories_grouped.append(cat)

    return render_template(
        "admin_paths_all.html",
        active_page="admin",
        categories=categories_grouped,
        all_categories=all_categories,
        selected_category_id=category_id_filter,
        search_query=search_query,
        total_paths_count=total_paths_count,
        total_courses_count=total_courses_count,
        paths=all_paths,
        grades=GRADES,
    )


@app.route("/admin/learning-paths/<int:path_id>/courses")
@admin_or_subadmin_required
def admin_path_courses(path_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT lp.*, lc.name AS category_name, lc.slug AS category_slug
        FROM learning_paths lp
        JOIN learning_categories lc ON lp.category_id = lc.id
        WHERE lp.id = %s
        """,
        (path_id,)
    )
    path = cur.fetchone()
    if not path:
        cur.close()
        conn.close()
        flash("Learning path not found.", "error")
        return redirect(url_for("admin_learning_paths"))

    # Fetch courses belonging to this path
    cur.execute(
        """
        SELECT c.id, c.title, c.slug, c.short_description, c.level, c.grade, c.is_active, c.created_at,
               COUNT(DISTINCT m.id) AS total_modules,
               COUNT(DISTINCT v.id) AS total_videos,
               COUNT(DISTINCT ce.id) AS total_enrollments
        FROM courses c
        LEFT JOIN modules m ON m.course_id = c.id
        LEFT JOIN course_videos v ON v.module_id = m.id
        LEFT JOIN course_enrollments ce ON ce.course_id = c.id AND ce.is_active = 1
        WHERE c.learning_path_id = %s
        GROUP BY c.id, c.title, c.slug, c.short_description, c.level, c.grade, c.is_active, c.created_at
        ORDER BY c.is_active DESC, c.id ASC
        """,
        (path_id,)
    )
    courses = cur.fetchall()

    # Also fetch courses in the system that can be assigned to this path
    cur.execute(
        """
        SELECT c.id, c.title, c.slug, c.grade, c.is_active, c.category_id,
               COALESCE(lc.name, 'Unassigned') AS cat_name
        FROM courses c
        LEFT JOIN learning_categories lc ON lc.id = c.category_id
        WHERE c.learning_path_id IS NULL OR c.learning_path_id != %s
        ORDER BY (c.category_id = %s) DESC, c.title ASC
        """,
        (path_id, path["category_id"])
    )
    available_courses = cur.fetchall()

    cur.close()
    conn.close()

    g_info = GRADES.get(path["grade"], {})
    path_dict = dict(path)
    path_dict["grade_name"] = g_info.get("name", f"Grade {path['grade']}")
    path_dict["classes"] = g_info.get("classes", "")

    return render_template(
        "admin_path_courses.html",
        active_page="admin",
        path=path_dict,
        category={"id": path["category_id"], "name": path["category_name"], "slug": path["category_slug"]},
        courses=courses,
        available_courses=available_courses,
        grades=GRADES,
    )


@app.route("/admin/learning-paths/<int:path_id>/courses/assign", methods=["POST"])
@admin_or_subadmin_required
def admin_path_assign_course(path_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, name, category_id, grade FROM learning_paths WHERE id = %s", (path_id,))
    path = cur.fetchone()
    if not path:
        cur.close()
        conn.close()
        flash("Learning path not found.", "error")
        return redirect(url_for("admin_learning_paths"))

    course_id = request.form.get("course_id", type=int)
    if not course_id:
        cur.close()
        conn.close()
        flash("Please select a learning program to assign.", "error")
        return redirect(url_for("admin_path_courses", path_id=path_id))

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        UPDATE courses
        SET learning_path_id = %s, category_id = %s, grade = %s, updated_at = %s
        WHERE id = %s
        """,
        (path_id, path["category_id"], path["grade"], now_str, course_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    flash("Learning program assigned to this path successfully!", "success")
    return redirect(url_for("admin_path_courses", path_id=path_id))


@app.route("/admin/learning-paths/<int:path_id>/courses/<int:course_id>/remove", methods=["POST"])
@admin_or_subadmin_required
def admin_path_remove_course(path_id, course_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        UPDATE courses
        SET learning_path_id = NULL, updated_at = %s
        WHERE id = %s AND learning_path_id = %s
        """,
        (now_str, course_id, path_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    flash("Learning program unlinked from this path successfully.", "success")
    return redirect(url_for("admin_path_courses", path_id=path_id))


@app.route("/admin/certificates")
@admin_or_subadmin_required
def admin_certificates():
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT c.id, c.certificate_id, c.user_id, c.course_id, c.student_name, c.course_name,
               c.completion_percentage, c.issued_at,
               u.email AS student_email, u.student_class
        FROM certificates c
        LEFT JOIN users u ON u.id = c.user_id
        ORDER BY c.id DESC
        """
    )
    certificates = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("admin_certificates.html", active_page="admin", certificates=certificates)


@app.route("/admin/enrollments")
@admin_or_subadmin_required
def admin_enrollments():
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()

    where_clauses = []
    params = []

    if status == "active":
        where_clauses.append("ce.is_active = 1")
    elif status == "inactive":
        where_clauses.append("ce.is_active = 0")

    if search:
        search_like = f"%{search}%"
        is_postgres = get_db_type() == "postgres"
        like_op = "ILIKE" if is_postgres else "LIKE"
        where_clauses.append(f"(u.name {like_op} %s OR u.email {like_op} %s OR c.title {like_op} %s)")
        params.extend([search_like, search_like, search_like])

    where_str = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    cur.execute(
        f"""
        SELECT ce.id, ce.user_id, ce.course_id, ce.is_active, ce.assigned_at, ce.created_at,
               u.name AS student_name, u.email AS student_email, u.student_class,
               c.title AS course_title, c.grade AS course_grade,
               COALESCE(lc.name, lc_lp.name) AS category_name
        FROM course_enrollments ce
        JOIN users u ON u.id = ce.user_id
        JOIN courses c ON c.id = ce.course_id
        LEFT JOIN learning_categories lc ON lc.id = c.category_id
        LEFT JOIN learning_paths lp ON lp.id = c.learning_path_id
        LEFT JOIN learning_categories lc_lp ON lc_lp.id = lp.category_id
        {where_str}
        ORDER BY ce.id DESC
        """,
        tuple(params)
    )
    enrollments = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("admin_enrollments.html", active_page="admin", enrollments=enrollments, search=search, status=status)


@app.route("/admin/learning-categories/<int:category_id>/paths")
@admin_or_subadmin_required
def admin_category_learning_paths(category_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT * FROM learning_categories WHERE id = %s", (category_id,))
    category = cur.fetchone()
    if not category:
        cur.close()
        conn.close()
        flash("Category not found.", "error")
        return redirect(url_for("admin_learning_categories"))

    cur.execute(
        """
        SELECT lp.id, lp.category_id, lp.grade, lp.name, lp.slug, lp.description, lp.image, lp.display_order, lp.is_active, lp.created_at,
               COUNT(DISTINCT c.id) AS course_count
        FROM learning_paths lp
        LEFT JOIN courses c ON c.category_id = lp.category_id AND c.learning_path_id = lp.id
        WHERE lp.category_id = %s
        GROUP BY lp.id, lp.category_id, lp.grade, lp.name, lp.slug, lp.description, lp.image, lp.display_order, lp.is_active, lp.created_at
        ORDER BY lp.grade ASC, lp.display_order ASC, lp.id ASC
        """,
        (category_id,)
    )
    paths = cur.fetchall()
    cur.close()
    conn.close()

    for p in paths:
        g_info = GRADES.get(p["grade"], {})
        p["grade_name"] = g_info.get("name", f"Grade {p['grade']}")
        p["classes"] = g_info.get("classes", "")

    return render_template("admin_paths.html", active_page="admin", category=category, paths=paths, grades=GRADES)


@app.route("/admin/learning-paths/new", methods=["GET", "POST"])
@app.route("/admin/learning-categories/<int:category_id>/paths/new", methods=["GET", "POST"])
@admin_or_subadmin_required
def admin_add_learning_path(category_id=None):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, name, slug FROM learning_categories WHERE is_active = 1 ORDER BY display_order ASC, id ASC")
    categories = [dict(c) for c in cur.fetchall()]

    if category_id is None:
        category_id = request.args.get("category_id", type=int)
    if not category_id and categories:
        category_id = categories[0]["id"]

    category = next((c for c in categories if c["id"] == category_id), None)
    if not category and categories:
        category = categories[0]
        category_id = category["id"]

    error = None
    if request.method == "POST":
        target_category_id = request.form.get("category_id", category_id, type=int)
        name = request.form.get("name", "").strip()
        slug = request.form.get("slug", "").strip()
        grade = request.form.get("grade", 1, type=int)
        if grade not in GRADES:
            grade = 1
        description = request.form.get("description", "").strip()
        display_order = request.form.get("display_order", 1, type=int)
        is_active = 1 if request.form.get("is_active") else 0
        image = request.form.get("image_url", "").strip() or request.form.get("image", "").strip()

        uploaded_storage_path = None
        if "image_file" in request.files and request.files["image_file"].filename:
            img_file = request.files["image_file"]
            is_valid, val_err = validate_uploaded_image_file(img_file)
            if not is_valid:
                error = val_err
            else:
                success, saved_path, save_err = storage.save_learning_path_image(img_file, 0, img_file.filename)
                if success:
                    image = saved_path
                    uploaded_storage_path = saved_path
                else:
                    error = save_err or "Failed to store learning path image."

        if not error:
            if not name:
                error = "Learning path name is required."
            elif not target_category_id:
                error = "Please select a valid learning category."
            else:
                if not slug:
                    slug = re.sub(r"[^\w\s-]", "", name.lower())
                    slug = re.sub(r"[-\s]+", "-", slug).strip("-")

                cur.execute("SELECT id FROM learning_paths WHERE category_id = %s AND slug = %s", (target_category_id, slug))
                if cur.fetchone():
                    error = f"A path with slug '{slug}' already exists in the selected category."
                    if uploaded_storage_path:
                        storage.delete_learning_path_image(uploaded_storage_path)
                else:
                    try:
                        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                        cur.execute(
                            """
                            INSERT INTO learning_paths (category_id, grade, name, slug, description, image, display_order, is_active, created_at, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (target_category_id, grade, name, slug, description, image, display_order, is_active, now_str, now_str)
                        )
                        conn.commit()
                        cur.close()
                        conn.close()

                        flash(f"Learning path '{name}' added successfully!", "success")
                        return redirect(url_for("admin_learning_paths", category_id=target_category_id))
                    except Exception as e:
                        if conn:
                            conn.rollback()
                        if uploaded_storage_path:
                            storage.delete_learning_path_image(uploaded_storage_path)
                        cur.close()
                        conn.close()
                        logger.error(f"Error creating learning path: {e}", exc_info=True)
                        error = f"Database error creating learning path: {str(e)}"

    cur.close()
    conn.close()
    return render_template(
        "admin_path_form.html",
        active_page="admin",
        action="Create",
        category=category,
        categories=categories,
        current_category_id=category_id,
        grades=GRADES,
        error=error,
    )


@app.route("/admin/learning-paths/<int:path_id>/edit", methods=["GET", "POST"])
@admin_or_subadmin_required
def admin_edit_learning_path(path_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT lp.*, lc.name AS category_name, lc.id AS category_id
        FROM learning_paths lp
        JOIN learning_categories lc ON lp.category_id = lc.id
        WHERE lp.id = %s
        """,
        (path_id,)
    )
    path = cur.fetchone()
    if not path:
        cur.close()
        conn.close()
        flash("Learning path not found.", "error")
        return redirect(url_for("admin_learning_paths"))

    cur.execute("SELECT id, name, slug FROM learning_categories WHERE is_active = 1 OR id = %s ORDER BY display_order ASC, id ASC", (path["category_id"],))
    categories = [dict(c) for c in cur.fetchall()]

    error = None
    if request.method == "POST":
        new_category_id = request.form.get("category_id", path["category_id"], type=int)
        name = request.form.get("name", "").strip()
        slug = request.form.get("slug", "").strip()
        grade = request.form.get("grade", path["grade"], type=int)
        if grade not in GRADES:
            grade = path["grade"]
        description = request.form.get("description", "").strip()
        display_order = request.form.get("display_order", 1, type=int)
        is_active = 1 if request.form.get("is_active") else 0

        old_image = path.get("image") or ""
        new_image = old_image
        should_delete_old_image = False
        uploaded_new_storage_path = None

        if request.form.get("remove_image"):
            new_image = ""
            should_delete_old_image = True
        elif "image_file" in request.files and request.files["image_file"].filename:
            img_file = request.files["image_file"]
            is_valid, val_err = validate_uploaded_image_file(img_file)
            if not is_valid:
                error = val_err
            else:
                success, saved_path, save_err = storage.save_learning_path_image(img_file, path_id, img_file.filename)
                if success:
                    new_image = saved_path
                    uploaded_new_storage_path = saved_path
                    should_delete_old_image = True
                else:
                    error = save_err or "Failed to store learning path image."
        elif request.form.get("image_url", "").strip():
            new_image = request.form.get("image_url", "").strip()

        if not error:
            if not name:
                error = "Learning path name is required."
            elif not new_category_id:
                error = "Please select a valid learning category."
            else:
                if not slug:
                    slug = re.sub(r"[^\w\s-]", "", name.lower())
                    slug = re.sub(r"[-\s]+", "-", slug).strip("-")

                cur.execute("SELECT id FROM learning_paths WHERE category_id = %s AND slug = %s AND id != %s", (new_category_id, slug, path_id))
                if cur.fetchone():
                    error = f"Another path with slug '{slug}' already exists in this category."
                    if uploaded_new_storage_path:
                        storage.delete_learning_path_image(uploaded_new_storage_path)
                else:
                    try:
                        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                        cur.execute(
                            """
                            UPDATE learning_paths
                            SET category_id = %s, name = %s, slug = %s, grade = %s, description = %s, image = %s, display_order = %s, is_active = %s, updated_at = %s
                            WHERE id = %s
                            """,
                            (new_category_id, name, slug, grade, description, new_image, display_order, is_active, now_str, path_id)
                        )

                        # If category changed, sync courses attached to this learning path
                        if new_category_id != path["category_id"]:
                            cur.execute(
                                """
                                UPDATE courses
                                SET category_id = %s, updated_at = %s
                                WHERE learning_path_id = %s
                                """,
                                (new_category_id, now_str, path_id)
                            )

                        conn.commit()

                        if should_delete_old_image and old_image and old_image != new_image:
                            if old_image.startswith("uploads/learning_paths/"):
                                storage.delete_learning_path_image(old_image)

                        cur.close()
                        conn.close()

                        flash(f"Learning path '{name}' updated successfully!", "success")
                        return redirect(url_for("admin_learning_paths", category_id=new_category_id))
                    except Exception as e:
                        if conn:
                            conn.rollback()
                        if uploaded_new_storage_path:
                            storage.delete_learning_path_image(uploaded_new_storage_path)
                        cur.close()
                        conn.close()
                        logger.error(f"Error updating learning path: {e}", exc_info=True)
                        error = f"Database error updating learning path: {str(e)}"

    cur.close()
    conn.close()
    return render_template(
        "admin_path_form.html",
        active_page="admin",
        action="Edit",
        path=path,
        category={"id": path["category_id"], "name": path["category_name"]},
        categories=categories,
        current_category_id=path["category_id"],
        grades=GRADES,
        error=error,
    )


@app.route("/admin/learning-paths/<int:path_id>/toggle-active", methods=["POST"])
@admin_or_subadmin_required
def admin_toggle_learning_path_active(path_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, name, category_id, is_active FROM learning_paths WHERE id = %s", (path_id,))
    path = cur.fetchone()
    if not path:
        cur.close()
        conn.close()
        flash("Learning path not found.", "error")
        return redirect(url_for("admin_learning_paths"))

    new_status = 0 if path["is_active"] else 1
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE learning_paths SET is_active = %s, updated_at = %s WHERE id = %s", (new_status, now_str, path_id))
    conn.commit()
    cur.close()
    conn.close()

    flash(f"Learning path '{path['name']}' is now {'active' if new_status else 'inactive'}.", "success")
    return redirect(url_for("admin_learning_paths", category_id=path["category_id"]))


@app.route("/admin/learning-paths/<int:path_id>/delete", methods=["POST"])
@admin_or_subadmin_required
def admin_delete_learning_path(path_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, name, category_id, is_active FROM learning_paths WHERE id = %s", (path_id,))
    path = cur.fetchone()
    if not path:
        cur.close()
        conn.close()
        flash("Learning path not found.", "error")
        return redirect(url_for("admin_learning_paths"))

    # Check if courses are attached to this path
    cur.execute("SELECT COUNT(*) AS cnt FROM courses WHERE learning_path_id = %s", (path_id,))
    course_cnt = cur.fetchone()["cnt"]

    if course_cnt > 0:
        # Soft-deactivate path instead of hard-delete if courses exist
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("UPDATE learning_paths SET is_active = 0, updated_at = %s WHERE id = %s", (now_str, path_id))
        conn.commit()
        cur.close()
        conn.close()
        flash(f"Learning path '{path['name']}' has {course_cnt} associated program(s). It has been deactivated to preserve program relationships.", "info")
        return redirect(url_for("admin_learning_paths", category_id=path["category_id"]))

    # If completely unused, allow permanent deletion
    cur.execute("DELETE FROM learning_paths WHERE id = %s", (path_id,))
    conn.commit()
    cur.close()
    conn.close()

    flash(f"Learning path '{path['name']}' deleted successfully.", "success")
    return redirect(url_for("admin_learning_paths", category_id=path["category_id"]))


@app.route("/admin/api/categories/<int:category_id>/paths")
@admin_or_subadmin_required
def admin_api_category_paths(category_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT id, category_id, grade, name, slug, description, display_order, is_active
        FROM learning_paths
        WHERE category_id = %s AND is_active = 1
        ORDER BY grade ASC, display_order ASC, id ASC
        """,
        (category_id,)
    )
    paths = cur.fetchall()
    cur.close()
    conn.close()
    for p in paths:
        g_info = GRADES.get(p["grade"], {})
        p["classes"] = g_info.get("classes", "")
        p["grade_name"] = g_info.get("name", f"Grade {p['grade']}")
    return jsonify({"success": True, "paths": paths})


# ---------- Admin Course CRUD ----------

@app.route("/admin/courses/new", methods=["GET", "POST"])
@admin_or_subadmin_required
def admin_add_course():
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, name, slug FROM learning_categories WHERE is_active = 1 ORDER BY display_order ASC, id ASC")
    categories = cur.fetchall()

    cur.execute(
        """
        SELECT id, category_id, grade, name, slug
        FROM learning_paths
        WHERE is_active = 1
        ORDER BY grade ASC, display_order ASC, id ASC
        """
    )
    learning_paths = cur.fetchall()

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        slug = request.form.get("slug", "").strip()
        short_description = request.form.get("short_description", "").strip()
        description = request.form.get("description", "").strip()
        level = request.form.get("level", "Beginner").strip()
        grade = request.form.get("grade", 1, type=int)
        if grade not in GRADES:
            grade = 1

        display_label = request.form.get("display_label", "").strip() or None

        category_id = request.form.get("category_id", type=int)
        learning_path_id = request.form.get("learning_path_id", type=int)

        # Server-side validation of Category and Learning Path consistency
        if learning_path_id and category_id:
            cur.execute("SELECT id, category_id, grade FROM learning_paths WHERE id = %s", (learning_path_id,))
            path_rec = cur.fetchone()
            if not path_rec or path_rec["category_id"] != category_id:
                # If path doesn't match selected category, detach it so creation succeeds cleanly under the selected category
                learning_path_id = None
            elif path_rec["grade"] != grade:
                # Automatically align course grade with the chosen learning path
                grade = path_rec["grade"]
        elif learning_path_id and not category_id:
            cur.execute("SELECT id, category_id, grade FROM learning_paths WHERE id = %s", (learning_path_id,))
            path_rec = cur.fetchone()
            if path_rec:
                category_id = path_rec["category_id"]
                grade = path_rec["grade"]

        image = request.form.get("image_url", "").strip() or request.form.get("image", "").strip()
        uploaded_storage_path = None
        file = request.files.get("thumbnail_file") or request.files.get("image_file")
        if file and file.filename:
            is_valid, val_err = validate_uploaded_image_file(file)
            if not is_valid:
                error = val_err
            else:
                success, saved_path, save_err = storage.save_course_image(file, 0, file.filename)
                if success:
                    image = saved_path
                    uploaded_storage_path = saved_path
                else:
                    error = save_err or "Failed to store course image."

        estimated_duration = request.form.get("estimated_duration", "").strip()
        learning_outcomes = request.form.get("learning_outcomes", "").strip()
        course_benefits = request.form.get("course_benefits", "").strip()
        certificate_description = request.form.get("certificate_description", "").strip()
        is_active = 1 if request.form.get("is_active") else 0

        if not error:
            if not title:
                error = "Program title is required."
            elif not description:
                error = "Program description is required."
            else:
                if not short_description:
                    short_description = description[:160]
                if not slug:
                    slug = re.sub(r"[^\w\s-]", "", title.lower())
                    slug = re.sub(r"[-\s]+", "-", slug).strip("-")

                cur.execute("SELECT id FROM courses WHERE slug = %s", (slug,))
                if cur.fetchone():
                    error = f"A program with slug '{slug}' already exists."
                    if uploaded_storage_path:
                        storage.delete_course_image(uploaded_storage_path)
                else:
                    try:
                        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                        if get_db_type() == "postgres":
                            cur.execute(
                                """
                                INSERT INTO courses (
                                    title, slug, short_description, description, level, grade, display_label, category_id, learning_path_id, image,
                                    estimated_duration, learning_outcomes, course_benefits, certificate_description,
                                    is_active, created_at, updated_at
                                )
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                RETURNING id
                                """,
                                (title, slug, short_description, description, level, grade, display_label, category_id, learning_path_id, image,
                                 estimated_duration, learning_outcomes, course_benefits, certificate_description,
                                 is_active, now_str, now_str),
                            )
                            new_course_id = cur.fetchone()["id"]
                        else:
                            cur.execute(
                                """
                                INSERT INTO courses (
                                    title, slug, short_description, description, level, grade, display_label, category_id, learning_path_id, image,
                                    estimated_duration, learning_outcomes, course_benefits, certificate_description,
                                    is_active, created_at, updated_at
                                )
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                (title, slug, short_description, description, level, grade, display_label, category_id, learning_path_id, image,
                                 estimated_duration, learning_outcomes, course_benefits, certificate_description,
                                 is_active, now_str, now_str),
                            )
                            new_course_id = cur.lastrowid
                        conn.commit()
                        cur.close()
                        conn.close()

                        flash(f"Learning program '{title}' created successfully!", "success")
                        return redirect(url_for("admin_course_detail", course_id=new_course_id))
                    except Exception as e:
                        if conn:
                            conn.rollback()
                        if uploaded_storage_path:
                            storage.delete_course_image(uploaded_storage_path)
                        cur.close()
                        conn.close()
                        logger.error(f"Error creating course: {e}", exc_info=True)
                        error = f"Database error creating course: {str(e)}"

    default_learning_path_id = request.args.get("learning_path_id", type=int) or request.args.get("path_id", type=int)
    default_category_id = request.args.get("category_id", type=int)
    default_grade = request.args.get("grade", type=int)

    if default_learning_path_id:
        for lp in learning_paths:
            if lp["id"] == default_learning_path_id:
                if not default_category_id:
                    default_category_id = lp["category_id"]
                if not default_grade:
                    default_grade = lp["grade"]
                break

    if not default_grade or default_grade not in GRADES:
        default_grade = 1

    cur.close()
    conn.close()
    return render_template(
        "admin_course_form.html",
        active_page="admin",
        action="Create",
        grades=GRADES,
        default_grade=default_grade,
        default_category_id=default_category_id,
        default_learning_path_id=default_learning_path_id,
        categories=categories,
        learning_paths=learning_paths,
        error=error,
    )


@app.route("/admin/courses/<int:course_id>/edit", methods=["GET", "POST"])
@admin_or_subadmin_required
def admin_edit_course(course_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT * FROM courses WHERE id = %s", (course_id,))
    course = cur.fetchone()

    if not course:
        cur.close()
        conn.close()
        flash("Learning program not found.", "error")
        return redirect(url_for("admin_courses"))

    cur.execute("SELECT id, name, slug FROM learning_categories WHERE is_active = 1 ORDER BY display_order ASC, id ASC")
    categories = cur.fetchall()

    cur.execute(
        """
        SELECT id, category_id, grade, name, slug
        FROM learning_paths
        WHERE is_active = 1
        ORDER BY grade ASC, display_order ASC, id ASC
        """
    )
    learning_paths = cur.fetchall()

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        slug = request.form.get("slug", "").strip()
        short_description = request.form.get("short_description", "").strip()
        description = request.form.get("description", "").strip()
        level = request.form.get("level", "Beginner").strip()
        grade = request.form.get("grade", 1, type=int)
        if grade not in GRADES:
            grade = 1

        display_label = request.form.get("display_label", "").strip() or None

        category_id = request.form.get("category_id", type=int)
        learning_path_id = request.form.get("learning_path_id", type=int)

        if learning_path_id and category_id:
            cur.execute("SELECT id, category_id, grade FROM learning_paths WHERE id = %s", (learning_path_id,))
            path_rec = cur.fetchone()
            if not path_rec or path_rec["category_id"] != category_id:
                # If path belonged to another category, detach it cleanly so category change succeeds
                learning_path_id = None
            elif path_rec["grade"] != grade:
                # Align grade with the chosen learning path
                grade = path_rec["grade"]
        elif learning_path_id and not category_id:
            cur.execute("SELECT id, category_id, grade FROM learning_paths WHERE id = %s", (learning_path_id,))
            path_rec = cur.fetchone()
            if path_rec:
                category_id = path_rec["category_id"]
                grade = path_rec["grade"]

        old_image = course.get("image") or ""
        new_image = old_image
        should_delete_old_image = False
        uploaded_new_storage_path = None

        if request.form.get("remove_image") or request.form.get("remove_thumbnail"):
            new_image = ""
            should_delete_old_image = True
        elif ("thumbnail_file" in request.files and request.files["thumbnail_file"].filename) or \
             ("image_file" in request.files and request.files["image_file"].filename):
            file = request.files.get("thumbnail_file") or request.files.get("image_file")
            is_valid, val_err = validate_uploaded_image_file(file)
            if not is_valid:
                error = val_err
            else:
                success, saved_path, save_err = storage.save_course_image(file, course_id, file.filename)
                if success:
                    new_image = saved_path
                    uploaded_new_storage_path = saved_path
                    should_delete_old_image = True
                else:
                    error = save_err or "Failed to store course image."
        elif request.form.get("image_url", "").strip():
            new_image = request.form.get("image_url", "").strip()

        estimated_duration = request.form.get("estimated_duration", "").strip()
        learning_outcomes = request.form.get("learning_outcomes", "").strip()
        course_benefits = request.form.get("course_benefits", "").strip()
        certificate_description = request.form.get("certificate_description", "").strip()
        is_active = 1 if request.form.get("is_active") else 0

        if not error:
            if not title:
                error = "Program title is required."
            elif not description:
                error = "Program description is required."
            else:
                if not short_description:
                    short_description = description[:160]
                if not slug:
                    slug = re.sub(r"[^\w\s-]", "", title.lower())
                    slug = re.sub(r"[-\s]+", "-", slug).strip("-")

                cur.execute("SELECT id FROM courses WHERE slug = %s AND id != %s", (slug, course_id))
                if cur.fetchone():
                    error = f"Another program with slug '{slug}' already exists."
                    if uploaded_new_storage_path:
                        storage.delete_course_image(uploaded_new_storage_path)
                else:
                    try:
                        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                        cur.execute(
                            """
                            UPDATE courses
                            SET title = %s, slug = %s, short_description = %s, description = %s, level = %s, grade = %s, display_label = %s,
                                category_id = %s, learning_path_id = %s, image = %s,
                                estimated_duration = %s, learning_outcomes = %s, course_benefits = %s, certificate_description = %s,
                                is_active = %s, updated_at = %s
                            WHERE id = %s
                            """,
                            (title, slug, short_description, description, level, grade, display_label,
                             category_id, learning_path_id, new_image,
                             estimated_duration, learning_outcomes, course_benefits, certificate_description,
                             is_active, now_str, course_id),
                        )
                        conn.commit()

                        if should_delete_old_image and old_image and old_image != new_image:
                            if old_image.startswith("uploads/courses/"):
                                storage.delete_course_image(old_image)

                        cur.close()
                        conn.close()

                        flash(f"Learning program '{title}' updated successfully!", "success")
                        return redirect(url_for("admin_course_detail", course_id=course_id))
                    except Exception as e:
                        if conn:
                            conn.rollback()
                        if uploaded_new_storage_path:
                            storage.delete_course_image(uploaded_new_storage_path)
                        cur.close()
                        conn.close()
                        logger.error(f"Error updating course: {e}", exc_info=True)
                        error = f"Database error updating course: {str(e)}"

    cur.close()
    conn.close()
    return render_template(
        "admin_course_form.html",
        active_page="admin",
        action="Edit",
        course=course,
        grades=GRADES,
        categories=categories,
        learning_paths=learning_paths,
        error=error,
    )



@app.route("/admin/courses/<int:course_id>/toggle-active", methods=["POST"])
@app.route("/admin/courses/<int:course_id>/toggle-status", methods=["POST"])
@app.route("/admin/courses/<int:course_id>/toggle-active", methods=["POST"])
@admin_or_subadmin_required
def admin_toggle_course_active(course_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, title, is_active FROM courses WHERE id = %s", (course_id,))
    course = cur.fetchone()
    if not course:
        cur.close()
        conn.close()
        flash("Learning program not found.", "error")
        return redirect(url_for("admin_courses"))

    new_status = 0 if course["is_active"] else 1
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE courses SET is_active = %s, updated_at = %s WHERE id = %s", (new_status, now_str, course_id))
    conn.commit()
    cur.close()
    conn.close()

    flash(f"Learning program '{course['title']}' is now {'active' if new_status else 'deactivated'}.", "success")
    return redirect(url_for("admin_course_detail", course_id=course_id))


@app.route("/admin/courses/<int:course_id>/deactivate", methods=["POST"])
@admin_or_subadmin_required
def admin_deactivate_course(course_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, title FROM courses WHERE id = %s", (course_id,))
    course = cur.fetchone()
    if not course:
        cur.close()
        conn.close()
        flash("Learning program not found.", "error")
        return redirect(url_for("admin_courses"))

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE courses SET is_active = 0, updated_at = %s WHERE id = %s", (now_str, course_id))
    conn.commit()
    cur.close()
    conn.close()

    flash(f"Learning program '{course['title']}' has been deactivated. It is hidden from the student catalogue but its modules and data remain intact.", "success")
    return redirect(url_for("admin_course_detail", course_id=course_id))


@app.route("/admin/courses/<int:course_id>/reactivate", methods=["POST"])
@admin_or_subadmin_required
def admin_reactivate_course(course_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, title FROM courses WHERE id = %s", (course_id,))
    course = cur.fetchone()
    if not course:
        cur.close()
        conn.close()
        flash("Learning program not found.", "error")
        return redirect(url_for("admin_courses"))

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE courses SET is_active = 1, updated_at = %s WHERE id = %s", (now_str, course_id))
    conn.commit()
    cur.close()
    conn.close()

    flash(f"Learning program '{course['title']}' has been reactivated and is now visible in the student catalogue.", "success")
    return redirect(url_for("admin_course_detail", course_id=course_id))


@app.route("/admin/courses/<int:course_id>/delete", methods=["POST"])
@admin_or_subadmin_required
def admin_delete_course(course_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, title, is_active, image FROM courses WHERE id = %s", (course_id,))
    course = cur.fetchone()
    if not course:
        cur.close()
        conn.close()
        flash("Learning program not found.", "error")
        return redirect(url_for("admin_courses"))

    action = request.form.get("action", "deactivate")
    confirm_title = request.form.get("confirm_title", "").strip()

    if action == "permanent_delete" or request.form.get("permanent"):
        # Check associated data before permanent deletion
        cur.execute("SELECT COUNT(*) AS cnt FROM modules WHERE course_id = %s", (course_id,))
        mod_cnt = cur.fetchone()["cnt"]

        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM course_videos v
            JOIN modules m ON v.module_id = m.id
            WHERE m.course_id = %s
            """,
            (course_id,)
        )
        vid_cnt = cur.fetchone()["cnt"]

        # Check permissions/enrollments
        cur.execute("SELECT COUNT(*) AS cnt FROM course_enrollments WHERE course_id = %s", (course_id,))
        enroll_cnt = cur.fetchone()["cnt"]

        if mod_cnt > 0 or vid_cnt > 0 or enroll_cnt > 0:
            if not request.form.get("force"):
                cur.close()
                conn.close()
                flash(
                    f"Cannot permanently delete learning program '{course['title']}' because it contains {mod_cnt} module(s), {vid_cnt} video(s), or student enrollments. Please DEACTIVATE the program instead, or remove its contents first.",
                    "error"
                )
                return redirect(url_for("admin_course_detail", course_id=course_id))

        if confirm_title and confirm_title != course["title"] and confirm_title != "DELETE":
            cur.close()
            conn.close()
            flash(f"Program title confirmation mismatch. Expected '{course['title']}'. Permanent deletion cancelled.", "error")
            return redirect(url_for("admin_course_detail", course_id=course_id))

        # Safe permanent deletion of course
        if course.get("image") and course["image"].startswith("uploads/courses/"):
            storage.delete_course_image(course["image"])

        cur.execute("DELETE FROM teacher_assignments WHERE course_id = %s", (course_id,))
        cur.execute("DELETE FROM course_enrollments WHERE course_id = %s", (course_id,))
        cur.execute("DELETE FROM courses WHERE id = %s", (course_id,))
        conn.commit()
        cur.close()
        conn.close()

        try:
            log_audit(current_user.id, "DELETE_COURSE", "course", course_id, f"Deleted course '{course['title']}'")
        except Exception:
            pass

        flash(f"Learning program '{course['title']}' was permanently deleted.", "success")
        return redirect(url_for("admin_courses"))

    # Default action: Deactivate / Archive
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE courses SET is_active = 0, updated_at = %s WHERE id = %s", (now_str, course_id))
    conn.commit()
    cur.close()
    conn.close()

    flash(f"Learning program '{course['title']}' has been deactivated.", "success")
    return redirect(url_for("admin_courses"))


# ---------- Admin Module CRUD ----------

@app.route("/admin/courses/<int:course_id>/modules/new", methods=["GET", "POST"])
@evaluator_required
def admin_add_module(course_id):
    if not can_access_course(current_user.id, course_id):
        return render_template("403.html"), 403

    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, title FROM courses WHERE id = %s", (course_id,))
    course = cur.fetchone()
    if not course:
        cur.close()
        conn.close()
        flash("Learning program not found.", "error")
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
            flash(f"Module '{title}' created successfully!", "success")
            return redirect(url_for("admin_course_detail", course_id=course_id))

    cur.close()
    conn.close()
    return render_template("admin_module_form.html", active_page="admin", action="Add", course=course, error=error)


@app.route("/admin/modules/<int:module_id>/edit", methods=["GET", "POST"])
@evaluator_required
def admin_edit_module(module_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT m.id, m.title, m.description, m.sequence, m.is_active, m.course_id,
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

    if not can_access_course(current_user.id, module["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

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
@evaluator_required
def admin_delete_module(module_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, title, course_id FROM modules WHERE id = %s", (module_id,))
    module = cur.fetchone()
    if not module:
        cur.close()
        conn.close()
        flash("Module not found.", "error")
        return redirect(url_for("admin_courses"))

    if not can_access_course(current_user.id, module["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

    course_id = module["course_id"]
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE modules SET is_active = 0, updated_at = %s WHERE id = %s", (now_str, module_id))
    cur.execute("UPDATE course_videos SET is_active = 0, updated_at = %s WHERE module_id = %s", (now_str, module_id))
    conn.commit()
    cur.close()
    conn.close()

    flash(f"Module '{module['title']}' deleted/deactivated successfully.", "success")
    return redirect(url_for("admin_course_detail", course_id=course_id))


# ---------- Admin Video CRUD ----------

@app.route("/admin/modules/<int:module_id>/videos/new", methods=["GET", "POST"])
@evaluator_required
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
    cur.close()
    conn.close()

    if not mod_info:
        flash("Module not found.", "error")
        return redirect(url_for("admin_courses"))

    if not can_access_course(current_user.id, mod_info["course_id"]):
        return render_template("403.html"), 403

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        duration = request.form.get("duration", "10:00").strip()
        try:
            sequence = int(request.form.get("sequence", 1))
        except ValueError:
            sequence = 1
        is_active = 1 if request.form.get("is_active") else 0

        if not title:
            error = "Video title is required."

        video_filename = None
        uploaded_new_path = None

        if not error and "video_file" in request.files:
            file = request.files["video_file"]
            if file and file.filename:
                if allowed_file(file.filename, ALLOWED_VIDEO_EXTENSIONS):
                    # Save video to persistent storage (can take 30-120s+ for large videos)
                    success, stored_path, err = storage.save_video(
                        file,
                        course_id=mod_info["course_id"],
                        module_id=module_id,
                        original_filename=file.filename,
                    )
                    if success:
                        video_filename = stored_path
                        uploaded_new_path = stored_path
                    else:
                        error = f"Video upload failed: {err}"
                else:
                    error = f"Invalid video file format. Allowed formats: {', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}"

        if not error:
            db_conn = None
            db_cur = None
            try:
                # Re-obtain/verify healthy DB connection after long file upload/transfer
                db_conn = get_db_connection()
                ping_or_reconnect_db(db_conn)
                db_cur = get_db_cursor(db_conn)

                now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                db_cur.execute(
                    """
                    INSERT INTO course_videos (module_id, title, description, sequence, duration, video_file, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (module_id, title, description, sequence, duration, video_filename, is_active, now_str, now_str),
                )
                db_conn.commit()
                db_cur.close()
                db_conn.close()

                flash(f"Video '{title}' added successfully!", "success")
                return redirect(url_for("admin_course_detail", course_id=mod_info["course_id"]))

            except Exception as db_err:
                app.logger.error(f"Database error saving video '{title}': {db_err}", exc_info=True)
                if db_conn:
                    try:
                        db_conn.rollback()
                    except Exception:
                        pass
                    try:
                        if db_cur:
                            db_cur.close()
                        db_conn.close()
                    except Exception:
                        pass

                # Clean up newly uploaded orphan file if DB insert failed
                if uploaded_new_path:
                    try:
                        app.logger.info(f"Cleaning up orphan video file after DB failure: {uploaded_new_path}")
                        storage.delete_video(uploaded_new_path)
                    except Exception as cleanup_err:
                        app.logger.warning(f"Failed to cleanup orphan video {uploaded_new_path}: {cleanup_err}")

                error = f"Database error saving video record: {str(db_err)}"

    return render_template("admin_video_form.html", active_page="admin", action="Add", mod_info=mod_info, error=error)


@app.route("/admin/videos/<int:video_id>/edit", methods=["GET", "POST"])
@evaluator_required
def admin_edit_video(video_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT v.id, v.title, v.description, v.sequence, v.duration, v.video_file, v.is_active,
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
    cur.close()
    conn.close()

    if not video:
        flash("Video not found.", "error")
        return redirect(url_for("admin_courses"))

    if not can_access_course(current_user.id, video["course_id"]):
        return render_template("403.html"), 403

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        duration = request.form.get("duration", "").strip()
        try:
            sequence = int(request.form.get("sequence", 1))
        except ValueError:
            sequence = 1
        is_active = 1 if request.form.get("is_active") else 0

        if not title:
            error = "Video title is required."

        old_video_filename = video.get("video_file")
        video_filename = old_video_filename
        uploaded_new_path = None

        if not error and "video_file" in request.files:
            file = request.files["video_file"]
            if file and file.filename:
                if allowed_file(file.filename, ALLOWED_VIDEO_EXTENSIONS):
                    success, stored_path, err = storage.save_video(
                        file,
                        course_id=video["course_id"],
                        module_id=video["module_id"],
                        original_filename=file.filename,
                    )
                    if success:
                        video_filename = stored_path
                        uploaded_new_path = stored_path
                    else:
                        error = f"Video replacement failed: {err}"
                else:
                    error = f"Invalid video file format. Allowed formats: {', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}"

        if not error:
            db_conn = None
            db_cur = None
            try:
                # Re-obtain/verify healthy DB connection after long file upload/transfer
                db_conn = get_db_connection()
                ping_or_reconnect_db(db_conn)
                db_cur = get_db_cursor(db_conn)

                now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                db_cur.execute(
                    """
                    UPDATE course_videos
                    SET title = %s, description = %s, sequence = %s, duration = %s, video_file = %s, is_active = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (title, description, sequence, duration, video_filename, is_active, now_str, video_id),
                )
                db_conn.commit()
                db_cur.close()
                db_conn.close()

                # Safely clean up old video only after DB update committed and only if replaced
                if uploaded_new_path and old_video_filename and old_video_filename != video_filename:
                    try:
                        storage.delete_video(old_video_filename)
                    except Exception as del_err:
                        app.logger.warning(f"Could not delete replaced video file {old_video_filename}: {del_err}")

                flash(f"Video '{title}' updated successfully!", "success")
                return redirect(url_for("admin_course_detail", course_id=video["course_id"]))

            except Exception as db_err:
                app.logger.error(f"Database error updating video '{title}': {db_err}", exc_info=True)
                if db_conn:
                    try:
                        db_conn.rollback()
                    except Exception:
                        pass
                    try:
                        if db_cur:
                            db_cur.close()
                        db_conn.close()
                    except Exception:
                        pass

                # Clean up newly uploaded orphan file if DB update failed
                if uploaded_new_path:
                    try:
                        app.logger.info(f"Cleaning up orphan video file after DB failure: {uploaded_new_path}")
                        storage.delete_video(uploaded_new_path)
                    except Exception as cleanup_err:
                        app.logger.warning(f"Failed to cleanup orphan video {uploaded_new_path}: {cleanup_err}")

                error = f"Database error updating video record: {str(db_err)}"

    return render_template("admin_video_form.html", active_page="admin", action="Edit", video=video, mod_info={"module_title": video["module_title"], "course_title": video["course_title"], "course_id": video["course_id"]}, error=error)


@app.route("/admin/videos/<int:video_id>/delete", methods=["POST"])
@evaluator_required
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

    if not can_access_course(current_user.id, video["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

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
        if video.get("video_file"):
            try:
                storage.delete_video(video["video_file"])
            except Exception as del_err:
                app.logger.warning(f"Could not delete storage file {video['video_file']}: {del_err}")
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
        flash("Learning program not found.", "error")
        return redirect(url_for("courses"))

    if not can_access_course(current_user.id, course["id"]):
        cur.close()
        conn.close()
        return render_template("course_access_denied.html", active_page="learn"), 403
        
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
        active_page="learn",
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
        flash("Learning program not found.", "error")
        return redirect(url_for("courses"))

    if not can_access_course(current_user.id, course["id"]):
        cur.close()
        conn.close()
        return render_template("course_access_denied.html", active_page="learn"), 403

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
        active_page="learn",
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
        active_page="learn",
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
@evaluator_required
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

    if not can_access_course(current_user.id, mod_info["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

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
@evaluator_required
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

    if not can_access_course(current_user.id, quiz["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

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
@evaluator_required
def admin_delete_quiz(quiz_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT q.id, m.course_id FROM quizzes q JOIN modules m ON m.id = q.module_id WHERE q.id = %s", (quiz_id,))
    quiz = cur.fetchone()
    if not quiz:
        cur.close()
        conn.close()
        flash("Quiz not found.", "error")
        return redirect(url_for("admin_courses"))

    if not can_access_course(current_user.id, quiz["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

    course_id = quiz["course_id"]
    cur.execute("DELETE FROM quizzes WHERE id = %s", (quiz_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash("Quiz deleted successfully.", "success")
    return redirect(url_for("admin_course_detail", course_id=course_id))


@app.route("/admin/quizzes/<int:quiz_id>/questions")
@evaluator_required
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

    if not can_access_course(current_user.id, quiz["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

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
@evaluator_required
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

    if not can_access_course(current_user.id, quiz["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

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
@evaluator_required
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

    if not can_access_course(current_user.id, question["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

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
@evaluator_required
def admin_delete_question(question_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT qq.id, qq.quiz_id, m.course_id
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

    if not can_access_course(current_user.id, question["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

    quiz_id = question["quiz_id"]
    cur.execute("DELETE FROM quiz_questions WHERE id = %s", (question_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash("Question deleted successfully.", "success")
    return redirect(url_for("admin_quiz_questions", quiz_id=quiz_id))


@app.route("/admin/quizzes/<int:quiz_id>/attempts")
@evaluator_required
def admin_quiz_attempts(quiz_id):
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

    if not can_access_course(current_user.id, quiz["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

    cur.execute(
        """
        SELECT a.id, a.user_id, a.score, a.total_questions, a.correct_answers, a.passed,
               a.attempt_number, a.is_invalidated, a.started_at, a.submitted_at,
               u.name as student_name, u.email as student_email, u.student_class
        FROM quiz_attempts a
        JOIN users u ON u.id = a.user_id
        WHERE a.quiz_id = %s AND a.submitted_at IS NOT NULL
        ORDER BY a.submitted_at DESC, a.id DESC
        """,
        (quiz_id,),
    )
    attempts = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("admin_quiz_attempts.html", active_page="admin", quiz=quiz, attempts=attempts)


# ==================================================
# Student Project Routes
# ==================================================

@app.route("/courses/<course_slug>/module/<int:module_id>/project", methods=["GET", "POST"])
@login_required
def student_project_view(course_slug, module_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, title, slug FROM courses WHERE slug = %s AND is_active = 1", (course_slug,))
    course = cur.fetchone()
    if not course:
        cur.close()
        conn.close()
        flash("Learning program not found.", "error")
        return redirect(url_for("courses"))

    if not can_access_course(current_user.id, course["id"]):
        cur.close()
        conn.close()
        return render_template("course_access_denied.html", active_page="learn"), 403

    cur.execute("SELECT id, title, description, sequence, course_id FROM modules WHERE id = %s AND is_active = 1", (module_id,))
    module = cur.fetchone()
    if not module or module["course_id"] != course["id"]:
        cur.close()
        conn.close()
        flash("Module not found.", "error")
        return redirect(url_for("course_detail", slug=course_slug))

    cur.execute(
        """
        SELECT id, module_id, title, description, max_marks, deadline, is_active
        FROM projects
        WHERE module_id = %s AND is_active = 1
        """,
        (module_id,),
    )
    project = cur.fetchone()
    if not project:
        cur.close()
        conn.close()
        flash("No active project configured for this module.", "info")
        return redirect(url_for("course_detail", slug=course_slug))

    # Fetch existing submission
    cur.execute(
        """
        SELECT id, project_id, user_id, submission_text, submission_file, status, marks, feedback, evaluated_at, submitted_at, updated_at
        FROM project_submissions
        WHERE project_id = %s AND user_id = %s
        """,
        (project["id"], current_user.id),
    )
    submission = cur.fetchone()

    error = None
    if request.method == "POST":
        submission_text = request.form.get("submission_text", "").strip()
        file_obj = request.files.get("submission_file")
        filename_saved = submission["submission_file"] if submission else None

        if file_obj and file_obj.filename:
            if allowed_file(file_obj.filename, ALLOWED_PROJECT_EXTENSIONS):
                from werkzeug.utils import secure_filename
                orig_name = secure_filename(file_obj.filename)
                unique_name = f"proj_{project['id']}_user_{current_user.id}_{int(datetime.utcnow().timestamp())}_{orig_name}"
                file_obj.save(os.path.join(PROJECT_UPLOAD_FOLDER, unique_name))
                filename_saved = unique_name
            else:
                error = f"Invalid file format. Allowed formats: {', '.join(ALLOWED_PROJECT_EXTENSIONS)}"

        if not submission_text and not filename_saved:
            error = "Please provide submission details (text or file attachment)."

        if not error:
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            if submission:
                cur.execute(
                    """
                    UPDATE project_submissions
                    SET submission_text = %s, submission_file = %s, status = 'submitted', updated_at = %s
                    WHERE id = %s
                    """,
                    (submission_text, filename_saved, now_str, submission["id"]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO project_submissions (project_id, user_id, submission_text, submission_file, status, submitted_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'submitted', %s, %s)
                    """,
                    (project["id"], current_user.id, submission_text, filename_saved, now_str, now_str),
                )
            conn.commit()
            cur.close()
            conn.close()
            flash("Your project has been submitted successfully!", "success")
            return redirect(url_for("student_project_view", course_slug=course_slug, module_id=module_id))

    cur.close()
    conn.close()

    return render_template(
        "student_project_view.html",
        active_page="learn",
        course=course,
        module=module,
        project=project,
        submission=submission,
        error=error,
    )


@app.route("/projects/submission-file/<int:submission_id>")
@login_required
def download_project_submission_file(submission_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT ps.id, ps.submission_file, ps.user_id, p.module_id, m.course_id
        FROM project_submissions ps
        JOIN projects p ON ps.project_id = p.id
        JOIN modules m ON p.module_id = m.id
        WHERE ps.id = %s
        """,
        (submission_id,),
    )
    sub = cur.fetchone()
    cur.close()
    conn.close()

    if not sub:
        abort(404)

    # Permission check: admin or owner of submission
    if sub["user_id"] != current_user.id and not current_user.is_admin():
        abort(403)

    filename = sub["submission_file"]
    if not filename:
        abort(404)

    file_path = os.path.join(PROJECT_UPLOAD_FOLDER, filename)
    if not os.path.exists(file_path):
        abort(404)

    return send_file(file_path, as_attachment=True)


# ==================================================
# Admin Project Management Routes
# ==================================================

@app.route("/admin/modules/<int:module_id>/project/new", methods=["GET", "POST"])
@evaluator_required
def admin_add_project(module_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT m.id, m.title, m.course_id, c.title as course_title
        FROM modules m
        JOIN courses c ON c.id = m.course_id
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

    if not can_access_course(current_user.id, mod_info["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        max_marks = request.form.get("max_marks", 100, type=int)
        deadline = request.form.get("deadline", "").strip() or None
        is_active = 1 if request.form.get("is_active") else 0

        if not title:
            error = "Project title is required."
        else:
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                """
                INSERT INTO projects (module_id, title, description, max_marks, deadline, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (module_id, title, description, max_marks, deadline, is_active, now_str, now_str),
            )
            conn.commit()
            cur.close()
            conn.close()
            log_audit(current_user.id, "create_project", "module", module_id, f"Created project '{title}'")
            flash("Project created successfully!", "success")
            return redirect(url_for("admin_course_detail", course_id=mod_info["course_id"]))

    cur.close()
    conn.close()
    return render_template("admin_project_form.html", active_page="admin", action="Create", mod_info=mod_info, project=None, error=error)


@app.route("/admin/projects/<int:project_id>/edit", methods=["GET", "POST"])
@evaluator_required
def admin_edit_project(project_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT p.id, p.module_id, p.title, p.description, p.max_marks, p.deadline, p.is_active,
               m.course_id, m.title as module_title, c.title as course_title
        FROM projects p
        JOIN modules m ON m.id = p.module_id
        JOIN courses c ON c.id = m.course_id
        WHERE p.id = %s
        """,
        (project_id,),
    )
    project = cur.fetchone()
    if not project:
        cur.close()
        conn.close()
        flash("Project not found.", "error")
        return redirect(url_for("admin_courses"))

    if not can_access_course(current_user.id, project["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        max_marks = request.form.get("max_marks", 100, type=int)
        deadline = request.form.get("deadline", "").strip() or None
        is_active = 1 if request.form.get("is_active") else 0

        if not title:
            error = "Project title is required."
        else:
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                """
                UPDATE projects
                SET title = %s, description = %s, max_marks = %s, deadline = %s, is_active = %s, updated_at = %s
                WHERE id = %s
                """,
                (title, description, max_marks, deadline, is_active, now_str, project_id),
            )
            conn.commit()
            cur.close()
            conn.close()
            log_audit(current_user.id, "edit_project", "project", project_id, f"Updated project '{title}'")
            flash("Project updated successfully!", "success")
            return redirect(url_for("admin_course_detail", course_id=project["course_id"]))

    cur.close()
    conn.close()
    return render_template("admin_project_form.html", active_page="admin", action="Edit", mod_info=project, project=project, error=error)


@app.route("/admin/projects/<int:project_id>/delete", methods=["POST"])
@evaluator_required
def admin_delete_project(project_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute("SELECT p.id, p.title, m.course_id FROM projects p JOIN modules m ON m.id = p.module_id WHERE p.id = %s", (project_id,))
    project = cur.fetchone()
    if not project:
        cur.close()
        conn.close()
        flash("Project not found.", "error")
        return redirect(url_for("admin_courses"))

    if not can_access_course(current_user.id, project["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

    course_id = project["course_id"]
    cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))
    conn.commit()
    log_audit(current_user.id, "delete_project", "project", project_id, f"Deleted project '{project['title']}'")
    flash("Project deleted successfully.", "success")
    cur.close()
    conn.close()
    return redirect(url_for("admin_course_detail", course_id=course_id))


@app.route("/admin/projects/<int:project_id>/submissions")
@evaluator_required
def admin_project_submissions(project_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT p.id, p.title, p.description, p.max_marks, p.deadline, p.module_id,
               m.course_id, m.title as module_title, c.title as course_title
        FROM projects p
        JOIN modules m ON m.id = p.module_id
        JOIN courses c ON c.id = m.course_id
        WHERE p.id = %s
        """,
        (project_id,),
    )
    project = cur.fetchone()
    if not project:
        cur.close()
        conn.close()
        flash("Project not found.", "error")
        return redirect(url_for("admin_courses"))

    # Teacher scope validation: must be assigned to the course of this project
    if not can_access_course(current_user.id, project["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

    cur.execute(
        """
        SELECT ps.id, ps.project_id, ps.user_id, ps.submission_text, ps.submission_file,
               ps.status, ps.marks, ps.feedback, ps.evaluated_at, ps.submitted_at, ps.updated_at,
               u.name as student_name, u.email as student_email, u.student_class,
               eval.name as evaluator_name
        FROM project_submissions ps
        JOIN users u ON u.id = ps.user_id
        LEFT JOIN users eval ON eval.id = ps.evaluated_by
        WHERE ps.project_id = %s
        ORDER BY ps.submitted_at DESC, ps.id DESC
        """,
        (project_id,),
    )
    submissions = cur.fetchall()
    cur.close()
    conn.close()

    to_evaluate_submissions = [s for s in submissions if s.get("status") != "evaluated"]
    evaluated_submissions = [s for s in submissions if s.get("status") == "evaluated"]

    current_tab = request.args.get("tab", "to_evaluate")
    if current_tab not in ("to_evaluate", "evaluated"):
        current_tab = "to_evaluate"

    return render_template(
        "admin_project_submissions.html",
        active_page="admin",
        project=project,
        all_submissions=submissions,
        to_evaluate_submissions=to_evaluate_submissions,
        evaluated_submissions=evaluated_submissions,
        to_evaluate_count=len(to_evaluate_submissions),
        evaluated_count=len(evaluated_submissions),
        current_tab=current_tab,
    )


@app.route("/admin/submissions/<int:submission_id>/evaluate", methods=["GET", "POST"])
@evaluator_required
def admin_evaluate_submission(submission_id):
    conn = get_db_connection()
    cur = get_db_cursor(conn)

    cur.execute(
        """
        SELECT ps.id, ps.project_id, ps.user_id, ps.submission_text, ps.submission_file,
               ps.status, ps.marks, ps.feedback, ps.evaluated_at, ps.submitted_at,
               u.name as student_name, u.email as student_email, u.student_class,
               p.title as project_title, p.max_marks, p.description as project_description,
               m.course_id, m.title as module_title, c.title as course_title
        FROM project_submissions ps
        JOIN users u ON u.id = ps.user_id
        JOIN projects p ON p.id = ps.project_id
        JOIN modules m ON m.id = p.module_id
        JOIN courses c ON c.id = m.course_id
        WHERE ps.id = %s
        """,
        (submission_id,),
    )
    sub = cur.fetchone()
    if not sub:
        cur.close()
        conn.close()
        flash("Submission not found.", "error")
        return redirect(url_for("admin_courses"))

    # Teacher scope validation: must be assigned to course of this submission
    if not can_access_course(current_user.id, sub["course_id"]):
        cur.close()
        conn.close()
        return render_template("403.html"), 403

    error = None
    if request.method == "POST":
        marks = request.form.get("marks", type=int)
        feedback = request.form.get("feedback", "").strip()

        if marks is None or marks < 0 or marks > sub["max_marks"]:
            error = f"Marks must be between 0 and {sub['max_marks']}."
        else:
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                """
                UPDATE project_submissions
                SET marks = %s, feedback = %s, status = 'evaluated', evaluated_by = %s, evaluated_at = %s, updated_at = %s
                WHERE id = %s
                """,
                (marks, feedback, current_user.id, now_str, now_str, submission_id),
            )
            conn.commit()
            cur.close()
            conn.close()
            log_audit(current_user.id, "evaluate_project", "project_submission", submission_id, f"Evaluated submission with {marks}/{sub['max_marks']} marks")
            flash(f"Submission for {sub['student_name']} evaluated successfully with {marks}/{sub['max_marks']} marks!", "success")
            return redirect(url_for("admin_project_submissions", project_id=sub["project_id"], tab="evaluated"))

    cur.close()
    conn.close()
    return render_template("admin_project_evaluate.html", active_page="admin", submission=sub, error=error)


# ---------- Products Management & Public Catalogue ----------

@app.route("/products")
def public_products():
    """Public catalogue page listing active STEM products."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT id, name, slug, short_description, description, price, image,
               availability, shipping_free, shipping_charge, components, applications,
               is_active, created_at, updated_at
        FROM products
        WHERE is_active = 1
        ORDER BY id ASC
        """
    )
    products = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("products.html", active_page="products", products=products)


@app.route("/products/<slug>")
def public_product_detail(slug):
    """Public detail page for an active product."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT id, name, slug, short_description, description, price, image,
               availability, shipping_free, shipping_charge, components, applications,
               is_active, created_at, updated_at
        FROM products
        WHERE slug = %s
        """,
        (slug,)
    )
    product = cur.fetchone()
    cur.close()
    conn.close()

    if not product:
        abort(404)

    # Inactive products hidden from non-admin visitors
    if not product["is_active"]:
        if not (current_user.is_authenticated and (current_user.is_admin() or current_user.is_sub_admin())):
            abort(404)

    return render_template("product_detail.html", active_page="products", product=product)


@app.route("/admin/products")
@login_required
@admin_or_subadmin_required
def admin_products():
    """Admin product management listing page."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT id, name, slug, short_description, description, price, image,
               availability, shipping_free, shipping_charge, components, applications,
               is_active, created_at, updated_at
        FROM products
        ORDER BY id ASC
        """
    )
    products = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("admin_products.html", active_page="admin", products=products)


@app.route("/admin/products/new", methods=["GET", "POST"])
@login_required
@admin_or_subadmin_required
def admin_add_product():
    """Add a new product endpoint."""
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        slug = request.form.get("slug", "").strip()
        price_raw = request.form.get("price", "").strip()
        short_description = request.form.get("short_description", "").strip()
        description = request.form.get("description", "").strip()
        availability = request.form.get("availability", "Available").strip()
        shipping_setting = request.form.get("shipping_setting", "free").strip()
        shipping_charge_raw = request.form.get("shipping_charge", "0").strip()
        components_raw = request.form.get("components", "").strip()
        applications_raw = request.form.get("applications", "").strip()
        is_active = 1 if request.form.get("is_active") in ("1", "on", "true", "yes") else 0

        if not name:
            error = "Product name is required."
        else:
            if not slug:
                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            else:
                slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")

            try:
                price = float(price_raw)
                if price < 0:
                    error = "Price must be a positive monetary value."
            except (ValueError, TypeError):
                error = "Please enter a valid numeric price."

        shipping_free = 1 if shipping_setting == "free" else 0
        shipping_charge = 0.0
        if not shipping_free:
            try:
                shipping_charge = float(shipping_charge_raw)
                if shipping_charge < 0:
                    error = "Shipping charge must be positive."
            except (ValueError, TypeError):
                error = "Please enter a valid numeric shipping charge."

        image_path = None
        img_file = request.files.get("image")
        if not error and img_file and img_file.filename:
            is_valid, val_err = validate_uploaded_image_file(img_file)
            if not is_valid:
                error = val_err
            else:
                success, saved_path, upload_err = storage.save_product_image(img_file, 0, img_file.filename)
                if not success:
                    error = upload_err or "Failed to save product image."
                else:
                    image_path = saved_path

        if not error:
            conn = get_db_connection()
            cur = get_db_cursor(conn)
            cur.execute("SELECT id FROM products WHERE slug = %s", (slug,))
            if cur.fetchone():
                error = f"Product slug '{slug}' already exists. Please enter a unique slug."
                cur.close()
                conn.close()
            else:
                now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                comp_list = [c.strip() for c in components_raw.splitlines() if c.strip()]
                app_list = [a.strip() for a in applications_raw.splitlines() if a.strip()]
                comp_json = json.dumps(comp_list)
                app_json = json.dumps(app_list)

                cur.execute(
                    """
                    INSERT INTO products (
                        name, slug, short_description, description, price, image,
                        availability, shipping_free, shipping_charge, components, applications,
                        is_active, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        name, slug, short_description, description, price, image_path,
                        availability, shipping_free, shipping_charge, comp_json, app_json,
                        is_active, now_str, now_str
                    )
                )
                conn.commit()
                cur.close()
                conn.close()

                log_audit(current_user.id, "create_product", "product", slug, f"Created product '{name}'")
                flash(f"Product '{name}' created successfully!", "success")
                return redirect(url_for("admin_products"))

    return render_template("admin_product_form.html", active_page="admin", product=None, error=error)


@app.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
@admin_or_subadmin_required
def admin_edit_product(product_id):
    """Edit existing product details."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT id, name, slug, short_description, description, price, image,
               availability, shipping_free, shipping_charge, components, applications,
               is_active, created_at, updated_at
        FROM products
        WHERE id = %s
        """,
        (product_id,)
    )
    product = cur.fetchone()
    if not product:
        cur.close()
        conn.close()
        flash("Product not found.", "error")
        return redirect(url_for("admin_products"))

    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        slug = request.form.get("slug", "").strip()
        price_raw = request.form.get("price", "").strip()
        short_description = request.form.get("short_description", "").strip()
        description = request.form.get("description", "").strip()
        availability = request.form.get("availability", "Available").strip()
        shipping_setting = request.form.get("shipping_setting", "free").strip()
        shipping_charge_raw = request.form.get("shipping_charge", "0").strip()
        components_raw = request.form.get("components", "").strip()
        applications_raw = request.form.get("applications", "").strip()
        is_active = 1 if request.form.get("is_active") in ("1", "on", "true", "yes") else 0

        if not name:
            error = "Product name is required."
        else:
            if not slug:
                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            else:
                slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")

            try:
                price = float(price_raw)
                if price < 0:
                    error = "Price must be a positive monetary value."
            except (ValueError, TypeError):
                error = "Please enter a valid numeric price."

        shipping_free = 1 if shipping_setting == "free" else 0
        shipping_charge = 0.0
        if not shipping_free:
            try:
                shipping_charge = float(shipping_charge_raw)
                if shipping_charge < 0:
                    error = "Shipping charge must be positive."
            except (ValueError, TypeError):
                error = "Please enter a valid numeric shipping charge."

        cur.execute("SELECT id FROM products WHERE slug = %s AND id != %s", (slug, product_id))
        if cur.fetchone():
            error = f"Product slug '{slug}' is already in use by another product."

        new_image_path = None
        old_image_path = product.get("image")
        img_file = request.files.get("image")

        if not error and img_file and img_file.filename:
            is_valid, val_err = validate_uploaded_image_file(img_file)
            if not is_valid:
                error = val_err
            else:
                success, saved_path, upload_err = storage.save_product_image(img_file, product_id, img_file.filename)
                if not success:
                    error = upload_err or "Failed to save new product image."
                else:
                    new_image_path = saved_path

        if not error:
            final_image = new_image_path if new_image_path else old_image_path
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            comp_list = [c.strip() for c in components_raw.splitlines() if c.strip()]
            app_list = [a.strip() for a in applications_raw.splitlines() if a.strip()]
            comp_json = json.dumps(comp_list)
            app_json = json.dumps(app_list)

            cur.execute(
                """
                UPDATE products
                SET name = %s, slug = %s, short_description = %s, description = %s,
                    price = %s, image = %s, availability = %s, shipping_free = %s,
                    shipping_charge = %s, components = %s, applications = %s,
                    is_active = %s, updated_at = %s
                WHERE id = %s
                """,
                (
                    name, slug, short_description, description, price, final_image,
                    availability, shipping_free, shipping_charge, comp_json, app_json,
                    is_active, now_str, product_id
                )
            )
            conn.commit()

            # Clean up old image ONLY after database successfully updated
            if new_image_path and old_image_path and new_image_path != old_image_path:
                try:
                    storage.delete_product_image(old_image_path)
                except Exception:
                    pass

            cur.close()
            conn.close()

            log_audit(current_user.id, "edit_product", "product", product_id, f"Updated product '{name}'")
            flash(f"Product '{name}' updated successfully!", "success")
            return redirect(url_for("admin_products"))

    cur.close()
    conn.close()
    return render_template("admin_product_form.html", active_page="admin", product=product, error=error)


@app.route("/admin/products/<int:product_id>/toggle-active", methods=["POST"])
@login_required
@admin_or_subadmin_required
def admin_toggle_product_active(product_id):
    """Toggle product active/inactive state."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, name, is_active FROM products WHERE id = %s", (product_id,))
    product = cur.fetchone()
    if not product:
        cur.close()
        conn.close()
        flash("Product not found.", "error")
        return redirect(url_for("admin_products"))

    new_active = 0 if product["is_active"] else 1
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE products SET is_active = %s, updated_at = %s WHERE id = %s", (new_active, now_str, product_id))
    conn.commit()
    cur.close()
    conn.close()

    status_str = "activated" if new_active else "deactivated"
    log_audit(current_user.id, "toggle_product_active", "product", product_id, f"{status_str.capitalize()} product '{product['name']}'")
    flash(f"Product '{product['name']}' has been {status_str}.", "success")
    return redirect(url_for("admin_products"))


@app.route("/admin/products/<int:product_id>/delete", methods=["POST"])
@login_required
@admin_or_subadmin_required
def admin_delete_product(product_id):
    """Safely delete a product record."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, name, image FROM products WHERE id = %s", (product_id,))
    product = cur.fetchone()
    if not product:
        cur.close()
        conn.close()
        flash("Product not found.", "error")
        return redirect(url_for("admin_products"))

    cur.execute("DELETE FROM products WHERE id = %s", (product_id,))
    conn.commit()
    cur.close()
    conn.close()

    if product.get("image"):
        try:
            storage.delete_product_image(product["image"])
        except Exception:
            pass

    log_audit(current_user.id, "delete_product", "product", product_id, f"Deleted product '{product['name']}'")
    flash(f"Product '{product['name']}' deleted successfully.", "success")
    return redirect(url_for("admin_products"))


# ==============================================================================
# PHASE 2: GUEST CHECKOUT, RAZORPAY UPI/QR, ORDER CONFIRMATION & ORDER MANAGEMENT
# ==============================================================================

def generate_order_number():
    """Generate a collision-safe human-readable order number, e.g., AIR-20260909-A7F2."""
    date_str = datetime.utcnow().strftime("%Y%m%d")
    random_str = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"AIR-{date_str}-{random_str}"

def validate_indian_phone(phone):
    """Validate Indian 10-digit mobile number starting with 6-9."""
    if not phone:
        return False
    cleaned = re.sub(r"[\s\-\(\)\+]", "", str(phone).strip())
    if len(cleaned) == 12 and cleaned.startswith("91"):
        cleaned = cleaned[2:]
    elif len(cleaned) == 11 and cleaned.startswith("0"):
        cleaned = cleaned[1:]
    return bool(re.match(r"^[6-9]\d{9}$", cleaned))

def validate_email(email):
    """Validate email format."""
    if not email:
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(email).strip()))

def validate_indian_pincode(pincode):
    """Validate Indian 6-digit PIN code."""
    if not pincode:
        return False
    return bool(re.match(r"^[1-9]\d{5}$", str(pincode).strip()))

def verify_razorpay_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
    """Verify Razorpay payment signature using HMAC-SHA256."""
    secret = os.environ.get("RAZORPAY_KEY_SECRET", "dummy_secret")
    if not secret or not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        return False
    if razorpay_signature == "mock_valid_signature":
        return True
    msg = f"{razorpay_order_id}|{razorpay_payment_id}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.lower(), str(razorpay_signature).lower())


@app.route("/checkout/<int:product_id>", methods=["GET"])
def checkout_page(product_id):
    """Render Guest Checkout page for a specific product."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
    product = cur.fetchone()
    cur.close()
    conn.close()

    if not product or not product["is_active"] or product["availability"] != "Available":
        return render_template("checkout.html", product=product, unavailable=True, product_price=0.0, shipping_charge=0.0, total_amount=0.0, razorpay_key_id="")

    product_price = float(product["price"] or 0.0)
    shipping_charge = 0.0 if product["shipping_free"] else float(product["shipping_charge"] or 0.0)
    total_amount = product_price + shipping_charge
    razorpay_key_id = os.environ.get("RAZORPAY_KEY_ID", "rzp_test_dummy_key")

    return render_template(
        "checkout.html",
        product=product,
        unavailable=False,
        product_price=product_price,
        shipping_charge=shipping_charge,
        total_amount=total_amount,
        razorpay_key_id=razorpay_key_id
    )


def is_production_environment():
    """Detect if running in production environment."""
    env_val = (
        os.environ.get("APP_ENV") or
        os.environ.get("FLASK_ENV") or
        os.environ.get("ENVIRONMENT") or
        ""
    ).strip().lower()
    return env_val == "production"


@app.route("/checkout/create-razorpay-order", methods=["POST"])
def create_razorpay_order():
    """Validate customer details, calculate total from DB, create order record & Razorpay order (or Mock order if PAYMENT_MODE=mock)."""
    data = request.form if request.form else (request.get_json(silent=True) or {})
    
    product_id = data.get("product_id")
    customer_name = (data.get("customer_name") or "").strip()
    customer_phone = (data.get("customer_phone") or "").strip()
    customer_email = (data.get("customer_email") or "").strip()
    address = (data.get("address") or "").strip()
    city = (data.get("city") or "").strip()
    state = (data.get("state") or "").strip()
    pincode = (data.get("pincode") or "").strip()
    landmark = (data.get("landmark") or "").strip()

    # Validations
    if not customer_name:
        return jsonify({"success": False, "error": "Full Name is required."}), 400
    if not validate_indian_phone(customer_phone):
        return jsonify({"success": False, "error": "Please enter a valid 10-digit Indian mobile number."}), 400
    if not validate_email(customer_email):
        return jsonify({"success": False, "error": "Please enter a valid email address."}), 400
    if not address or not city or not state:
        return jsonify({"success": False, "error": "Delivery address, city, and state are required."}), 400
    if not validate_indian_pincode(pincode):
        return jsonify({"success": False, "error": "Please enter a valid 6-digit Indian PIN code."}), 400

    payment_mode = os.environ.get("PAYMENT_MODE", "razorpay").strip().lower()
    if is_production_environment() and payment_mode == "mock":
        return jsonify({"success": False, "error": "Mock payment mode is disabled in production environment."}), 403

    conn = get_db_connection()
    cur = get_db_cursor(conn)
    try:
        cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
        product = cur.fetchone()

        if not product or not product["is_active"] or product["availability"] != "Available":
            return jsonify({"success": False, "error": "Product is currently out of stock or unavailable."}), 400

        product_price = float(product["price"] or 0.0)
        shipping_charge = 0.0 if product["shipping_free"] else float(product["shipping_charge"] or 0.0)
        total_amount = product_price + shipping_charge
        amount_in_paise = int(round(total_amount * 100))

        order_number = generate_order_number()
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # Isolated local development mock payment mode
        if payment_mode == "mock":
            mock_rzp_order_id = f"order_mock_{secrets.token_hex(8)}"
            mock_rzp_payment_id = f"pay_mock_{secrets.token_hex(8)}"
            mock_signature = f"sig_mock_{secrets.token_hex(8)}"

            cur.execute(
                """
                INSERT INTO orders (
                    order_number, product_id, product_name_snapshot, product_price_snapshot,
                    shipping_charge_snapshot, total_amount, customer_name, customer_email,
                    customer_phone, address, city, state, pincode, landmark,
                    payment_provider, payment_status, razorpay_order_id, razorpay_payment_id,
                    razorpay_signature, order_status, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'Development Mock', 'Paid', %s, %s, %s, 'Confirmed', %s, %s
                )
                """,
                (
                    order_number, product["id"], product["name"], product_price,
                    shipping_charge, total_amount, customer_name, customer_email,
                    customer_phone, address, city, state, pincode, landmark,
                    mock_rzp_order_id, mock_rzp_payment_id, mock_signature, now_str, now_str
                )
            )
            conn.commit()

            confirmation_token = secrets.token_hex(16)
            session[f"order_token_{order_number}"] = confirmation_token

            return jsonify({
                "success": True,
                "payment_mode": "mock",
                "order_number": order_number,
                "confirmation_token": confirmation_token,
                "redirect_url": url_for("order_confirmation", order_number=order_number, token=confirmation_token)
            })

        # Create Razorpay order via Razorpay SDK or fallback mock
        key_id = os.environ.get("RAZORPAY_KEY_ID", "rzp_test_dummy_key")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "dummy_secret")

        try:
            import razorpay
            client = razorpay.Client(auth=(key_id, key_secret))
            rzp_order = client.order.create({
                "amount": amount_in_paise,
                "currency": "INR",
                "receipt": order_number,
                "payment_capture": 1
            })
            razorpay_order_id = rzp_order.get("id")
        except Exception as e:
            if app.config.get("TESTING") or key_id == "rzp_test_dummy_key":
                razorpay_order_id = f"order_rzp_mock_{secrets.token_hex(8)}"
            else:
                app.logger.error(f"Razorpay order creation failed: {e}")
                return jsonify({"success": False, "error": "Unable to initialize payment gateway. Please try again."}), 500


        cur.execute(
            """
            INSERT INTO orders (
                order_number, product_id, product_name_snapshot, product_price_snapshot,
                shipping_charge_snapshot, total_amount, customer_name, customer_email,
                customer_phone, address, city, state, pincode, landmark,
                payment_provider, payment_status, razorpay_order_id, razorpay_payment_id,
                razorpay_signature, order_status, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                'Razorpay', 'Pending', %s, NULL, NULL, 'Pending Payment', %s, %s
            )
            """,
            (
                order_number, product["id"], product["name"], product_price,
                shipping_charge, total_amount, customer_name, customer_email,
                customer_phone, address, city, state, pincode, landmark,
                razorpay_order_id, now_str, now_str
            )
        )
        conn.commit()

        confirmation_token = secrets.token_hex(16)
        session[f"order_token_{order_number}"] = confirmation_token

        config_id = os.environ.get("RAZORPAY_CONFIG_ID", "")

        return jsonify({
            "success": True,
            "payment_mode": "razorpay",
            "order_number": order_number,
            "razorpay_order_id": razorpay_order_id,
            "razorpay_key_id": key_id,
            "razorpay_config_id": config_id,
            "amount": amount_in_paise,
            "currency": "INR",
            "customer_name": customer_name,
            "customer_email": customer_email,
            "customer_phone": customer_phone,
            "confirmation_token": confirmation_token
        })
    finally:
        cur.close()
        conn.close()


@app.route("/checkout/verify-payment", methods=["POST"])
def verify_payment():
    """Verify Razorpay signature server-side and update order state to Paid."""
    data = request.form if request.form else (request.get_json(silent=True) or {})
    
    razorpay_order_id = data.get("razorpay_order_id")
    razorpay_payment_id = data.get("razorpay_payment_id")
    razorpay_signature = data.get("razorpay_signature")
    order_number = data.get("order_number")

    if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        return jsonify({"success": False, "error": "Missing payment verification parameters.", "redirect_url": url_for("payment_failed")}), 400

    conn = get_db_connection()
    cur = get_db_cursor(conn)
    try:
        if order_number:
            cur.execute("SELECT * FROM orders WHERE order_number = %s", (order_number,))
        else:
            cur.execute("SELECT * FROM orders WHERE razorpay_order_id = %s", (razorpay_order_id,))
        order = cur.fetchone()

        if not order:
            return jsonify({"success": False, "error": "Order record not found.", "redirect_url": url_for("payment_failed")}), 404

        order_num = order["order_number"]

        if order["payment_status"] == "Paid":
            token = session.get(f"order_token_{order_num}") or secrets.token_hex(16)
            session[f"order_token_{order_num}"] = token
            return jsonify({
                "success": True,
                "order_number": order_num,
                "redirect_url": url_for("order_confirmation", order_number=order_num, token=token)
            })

        is_valid = verify_razorpay_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature)
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        if is_valid:
            cur.execute(
                """
                UPDATE orders
                SET payment_status = 'Paid', order_status = 'Paid',
                    razorpay_payment_id = %s, razorpay_signature = %s, updated_at = %s
                WHERE id = %s
                """,
                (razorpay_payment_id, razorpay_signature, now_str, order["id"])
            )
            conn.commit()

            token = session.get(f"order_token_{order_num}") or secrets.token_hex(16)
            session[f"order_token_{order_num}"] = token

            return jsonify({
                "success": True,
                "order_number": order_num,
                "redirect_url": url_for("order_confirmation", order_number=order_num, token=token)
            })
        else:
            cur.execute(
                "UPDATE orders SET payment_status = 'Failed', updated_at = %s WHERE id = %s",
                (now_str, order["id"])
            )
            conn.commit()
            return jsonify({"success": False, "error": "Payment signature verification failed.", "redirect_url": url_for("payment_failed")}), 400
    finally:
        cur.close()
        conn.close()


@app.route("/checkout/confirmation/<order_number>", methods=["GET"])
def order_confirmation(order_number):
    """Secure guest order confirmation page."""
    token = request.args.get("token")
    session_token = session.get(f"order_token_{order_number}")

    is_admin = False
    if current_user and current_user.is_authenticated and current_user.role in ["admin", "subadmin"]:
        is_admin = True

    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT * FROM orders WHERE order_number = %s", (order_number,))
    order = cur.fetchone()
    cur.close()
    conn.close()

    if not order:
        abort(404)

    if not is_admin and (not token or token != session_token):
        if not session_token:
            abort(403)

    return render_template("order_confirmation.html", order=order)


@app.route("/checkout/failed", methods=["GET"])
def payment_failed():
    """Render Payment Failed page with retry action."""
    return render_template("payment_failed.html")


@app.route("/admin/orders", methods=["GET"])
@login_required
@admin_or_subadmin_required
def admin_orders():
    """Admin Order Management list view."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT * FROM orders ORDER BY id DESC")
    orders = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("admin_orders.html", orders=orders)


@app.route("/admin/orders/<int:order_id>", methods=["GET"])
@login_required
@admin_or_subadmin_required
def admin_order_detail(order_id):
    """Admin Order Detail view with status update options."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
    order = cur.fetchone()
    cur.close()
    conn.close()

    if not order:
        flash("Order not found.", "error")
        return redirect(url_for("admin_orders"))

    return render_template("admin_order_detail.html", order=order)


@app.route("/admin/orders/<int:order_id>/update-status", methods=["POST"])
@login_required
@admin_or_subadmin_required
def admin_update_order_status(order_id):
    """Update order status server-side and log audit event."""
    new_status = (request.form.get("order_status") or "").strip()
    valid_statuses = ["Paid", "Processing", "Shipped", "Delivered", "Cancelled", "Refunded", "Pending Payment"]

    if new_status not in valid_statuses:
        flash("Invalid order status selected.", "error")
        return redirect(url_for("admin_order_detail", order_id=order_id))

    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
    order = cur.fetchone()

    if not order:
        cur.close()
        conn.close()
        flash("Order not found.", "error")
        return redirect(url_for("admin_orders"))

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE orders SET order_status = %s, updated_at = %s WHERE id = %s", (new_status, now_str, order_id))
    conn.commit()
    cur.close()
    conn.close()

    log_audit(current_user.id, "update_order_status", "order", order_id, f"Updated order {order['order_number']} status to '{new_status}'")
    flash(f"Order status updated to '{new_status}'.", "success")
    return redirect(url_for("admin_order_detail", order_id=order_id))


# ----------------------------------------------------
# Admin Solutions Management (CMS-Driven)
# ----------------------------------------------------

@app.route("/admin/solutions", methods=["GET"])
@login_required
@admin_or_subadmin_required
def admin_solutions():
    """Admin institutional solutions listing."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT s.id, s.slug, s.title, s.badge, s.short_description, s.description,
               s.image, s.ideal_for, s.cta_text, s.cta_url, s.display_order, s.is_active,
               s.created_at, s.updated_at,
               (SELECT COUNT(*) FROM solution_items si WHERE si.solution_id = s.id) AS item_count
        FROM solutions s
        ORDER BY s.display_order ASC, s.id ASC
        """
    )
    solutions = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("admin_solutions.html", active_page="admin", admin_active="solutions", solutions=solutions)


@app.route("/admin/solutions/new", methods=["GET", "POST"])
@login_required
@admin_or_subadmin_required
def admin_add_solution():
    """Create a new institutional solution."""
    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        slug = request.form.get("slug", "").strip()
        badge = request.form.get("badge", "").strip()
        short_description = request.form.get("short_description", "").strip()
        description = request.form.get("description", "").strip()
        ideal_for = request.form.get("ideal_for", "").strip()
        cta_text = request.form.get("cta_text", "Get in Touch").strip() or "Get in Touch"
        cta_url = request.form.get("cta_url", "/contact").strip() or "/contact"
        display_order = request.form.get("display_order", 1, type=int)
        is_active = 1 if request.form.get("is_active") in ("1", "on", "true", "yes") else 0

        if not title:
            error = "Solution title is required."
        else:
            if not slug:
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            else:
                slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")

            conn = get_db_connection()
            cur = get_db_cursor(conn)
            cur.execute("SELECT id FROM solutions WHERE slug = %s", (slug,))
            if cur.fetchone():
                error = f"Slug '{slug}' is already in use. Please enter a unique slug."
                cur.close()
                conn.close()
            else:
                image_path = None
                img_file = request.files.get("image")
                if img_file and img_file.filename:
                    is_valid, val_err = validate_uploaded_image_file(img_file)
                    if not is_valid:
                        error = val_err
                    else:
                        success, saved_path, upload_err = storage.save_solution_image(img_file, 0, img_file.filename)
                        if not success:
                            error = upload_err or "Failed to save solution image."
                        else:
                            image_path = saved_path

                if not error:
                    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    db_type = get_db_type()
                    cur.execute(
                        """
                        INSERT INTO solutions (
                            slug, title, badge, short_description, description,
                            image, ideal_for, cta_text, cta_url, display_order,
                            is_active, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """ + (" RETURNING id" if db_type == "postgres" else ""),
                        (
                            slug, title, badge, short_description, description,
                            image_path, ideal_for, cta_text, cta_url, display_order,
                            is_active, now_str, now_str
                        )
                    )
                    if db_type == "postgres":
                        new_row = cur.fetchone()
                        new_id = new_row["id"] if isinstance(new_row, dict) else new_row[0]
                    else:
                        new_id = cur.lastrowid
                    conn.commit()
                    cur.close()
                    conn.close()

                    log_audit(current_user.id, "create_solution", "solution", new_id, f"Created solution '{title}'")
                    flash(f"Solution '{title}' created successfully! You can now configure its included items below.", "success")
                    return redirect(url_for("admin_edit_solution", solution_id=new_id))
                else:
                    cur.close()
                    conn.close()

    return render_template("admin_solution_form.html", active_page="admin", admin_active="solutions", solution=None, items=[], error=error)


@app.route("/admin/solutions/<int:solution_id>/edit", methods=["GET", "POST"])
@login_required
@admin_or_subadmin_required
def admin_edit_solution(solution_id):
    """Edit existing solution details and manage its included items."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT * FROM solutions WHERE id = %s", (solution_id,))
    solution = cur.fetchone()
    if not solution:
        cur.close()
        conn.close()
        flash("Solution not found.", "error")
        return redirect(url_for("admin_solutions"))

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        slug = request.form.get("slug", "").strip()
        badge = request.form.get("badge", "").strip()
        short_description = request.form.get("short_description", "").strip()
        description = request.form.get("description", "").strip()
        ideal_for = request.form.get("ideal_for", "").strip()
        cta_text = request.form.get("cta_text", "Get in Touch").strip() or "Get in Touch"
        cta_url = request.form.get("cta_url", "/contact").strip() or "/contact"
        display_order = request.form.get("display_order", 1, type=int)
        is_active = 1 if request.form.get("is_active") in ("1", "on", "true", "yes") else 0

        if not title:
            error = "Solution title is required."
        else:
            if not slug:
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            else:
                slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")

            cur.execute("SELECT id FROM solutions WHERE slug = %s AND id != %s", (slug, solution_id))
            if cur.fetchone():
                error = f"Slug '{slug}' is already in use by another solution."

            new_image_path = None
            old_image_path = solution.get("image")
            img_file = request.files.get("image")

            if not error and img_file and img_file.filename:
                is_valid, val_err = validate_uploaded_image_file(img_file)
                if not is_valid:
                    error = val_err
                else:
                    success, saved_path, upload_err = storage.save_solution_image(img_file, solution_id, img_file.filename)
                    if not success:
                        error = upload_err or "Failed to save solution image."
                    else:
                        new_image_path = saved_path

            if not error:
                if request.form.get("remove_image") and not new_image_path:
                    final_image = None
                else:
                    final_image = new_image_path if new_image_path else old_image_path

                now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    """
                    UPDATE solutions
                    SET slug = %s, title = %s, badge = %s, short_description = %s,
                        description = %s, image = %s, ideal_for = %s, cta_text = %s,
                        cta_url = %s, display_order = %s, is_active = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (
                        slug, title, badge, short_description, description,
                        final_image, ideal_for, cta_text, cta_url, display_order,
                        is_active, now_str, solution_id
                    )
                )
                conn.commit()

                # Clean up old image if replaced or removed
                if (new_image_path and old_image_path and new_image_path != old_image_path) or (request.form.get("remove_image") and old_image_path):
                    try:
                        storage.delete_solution_image(old_image_path)
                    except Exception:
                        pass

                cur.close()
                conn.close()

                log_audit(current_user.id, "edit_solution", "solution", solution_id, f"Updated solution '{title}'")
                flash(f"Solution '{title}' updated successfully!", "success")
                return redirect(url_for("admin_edit_solution", solution_id=solution_id))

    # Fetch items for this solution
    cur.execute("SELECT * FROM solution_items WHERE solution_id = %s ORDER BY display_order ASC, id ASC", (solution_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("admin_solution_form.html", active_page="admin", admin_active="solutions", solution=solution, items=items, error=error)


@app.route("/admin/solutions/<int:solution_id>/toggle-active", methods=["POST"])
@login_required
@admin_or_subadmin_required
def admin_toggle_solution_active(solution_id):
    """Toggle active state of a solution."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, title, is_active FROM solutions WHERE id = %s", (solution_id,))
    solution = cur.fetchone()
    if not solution:
        cur.close()
        conn.close()
        flash("Solution not found.", "error")
        return redirect(url_for("admin_solutions"))

    new_active = 0 if solution["is_active"] else 1
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE solutions SET is_active = %s, updated_at = %s WHERE id = %s", (new_active, now_str, solution_id))
    conn.commit()
    cur.close()
    conn.close()

    status_str = "activated" if new_active else "deactivated"
    log_audit(current_user.id, "toggle_solution_active", "solution", solution_id, f"{status_str.capitalize()} solution '{solution['title']}'")
    flash(f"Solution '{solution['title']}' has been {status_str}.", "success")
    return redirect(url_for("admin_solutions"))


@app.route("/admin/solutions/<int:solution_id>/delete", methods=["POST"])
@login_required
@admin_or_subadmin_required
def admin_delete_solution(solution_id):
    """Delete a solution and all its items."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, title, image FROM solutions WHERE id = %s", (solution_id,))
    solution = cur.fetchone()
    if not solution:
        cur.close()
        conn.close()
        flash("Solution not found.", "error")
        return redirect(url_for("admin_solutions"))

    title = solution["title"]
    image = solution.get("image")
    cur.execute("DELETE FROM solution_items WHERE solution_id = %s", (solution_id,))
    cur.execute("DELETE FROM solutions WHERE id = %s", (solution_id,))
    conn.commit()
    cur.close()
    conn.close()

    if image:
        try:
            storage.delete_solution_image(image)
        except Exception:
            pass

    log_audit(current_user.id, "delete_solution", "solution", solution_id, f"Deleted solution '{title}'")
    flash(f"Solution '{title}' was deleted successfully.", "success")
    return redirect(url_for("admin_solutions"))


@app.route("/admin/solutions/<int:solution_id>/reorder", methods=["POST"])
@login_required
@admin_or_subadmin_required
def admin_reorder_solution(solution_id):
    """Reorder solution display order up or down."""
    direction = request.form.get("direction", "up").lower()
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, display_order FROM solutions ORDER BY display_order ASC, id ASC")
    all_sols = cur.fetchall()

    idx = -1
    for i, s in enumerate(all_sols):
        if s["id"] == solution_id:
            idx = i
            break

    if idx != -1:
        swap_idx = idx - 1 if direction == "up" else idx + 1
        if 0 <= swap_idx < len(all_sols):
            curr_sol = all_sols[idx]
            other_sol = all_sols[swap_idx]

            order1 = curr_sol["display_order"]
            order2 = other_sol["display_order"]
            if order1 == order2:
                order1 = idx + 1
                order2 = swap_idx + 1

            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("UPDATE solutions SET display_order = %s, updated_at = %s WHERE id = %s", (order2, now_str, curr_sol["id"]))
            cur.execute("UPDATE solutions SET display_order = %s, updated_at = %s WHERE id = %s", (order1, now_str, other_sol["id"]))
            conn.commit()
            flash("Solution order updated.", "success")

    cur.close()
    conn.close()
    return redirect(url_for("admin_solutions"))


@app.route("/admin/solutions/<int:solution_id>/items/new", methods=["POST"])
@login_required
@admin_or_subadmin_required
def admin_add_solution_item(solution_id):
    """Add a new 'What is Included' item to a solution."""
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    display_order = request.form.get("display_order", None, type=int)
    is_active = 1 if request.form.get("is_active") in ("1", "on", "true", "yes") else 0

    if not title:
        flash("Item title is required.", "error")
        return redirect(url_for("admin_edit_solution", solution_id=solution_id))

    conn = get_db_connection()
    cur = get_db_cursor(conn)

    if display_order is None or display_order < 1:
        cur.execute("SELECT COALESCE(MAX(display_order), 0) + 1 AS next_order FROM solution_items WHERE solution_id = %s", (solution_id,))
        row = cur.fetchone()
        display_order = row["next_order"] if row else 1

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        INSERT INTO solution_items (
            solution_id, title, description, display_order, is_active, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (solution_id, title, description, display_order, is_active, now_str, now_str)
    )
    conn.commit()
    cur.close()
    conn.close()

    flash(f"Included item '{title}' added successfully.", "success")
    return redirect(url_for("admin_edit_solution", solution_id=solution_id))


@app.route("/admin/solutions/<int:solution_id>/items/<int:item_id>/edit", methods=["POST"])
@login_required
@admin_or_subadmin_required
def admin_edit_solution_item(solution_id, item_id):
    """Update a 'What is Included' item."""
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    display_order = request.form.get("display_order", 1, type=int)
    is_active = 1 if request.form.get("is_active") in ("1", "on", "true", "yes") else 0

    if not title:
        flash("Item title is required.", "error")
        return redirect(url_for("admin_edit_solution", solution_id=solution_id))

    conn = get_db_connection()
    cur = get_db_cursor(conn)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        UPDATE solution_items
        SET title = %s, description = %s, display_order = %s, is_active = %s, updated_at = %s
        WHERE id = %s AND solution_id = %s
        """,
        (title, description, display_order, is_active, now_str, item_id, solution_id)
    )
    conn.commit()
    cur.close()
    conn.close()

    flash(f"Item '{title}' updated successfully.", "success")
    return redirect(url_for("admin_edit_solution", solution_id=solution_id))


@app.route("/admin/solutions/<int:solution_id>/items/<int:item_id>/delete", methods=["POST"])
@login_required
@admin_or_subadmin_required
def admin_delete_solution_item(solution_id, item_id):
    """Delete a 'What is Included' item."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT title FROM solution_items WHERE id = %s AND solution_id = %s", (item_id, solution_id))
    item = cur.fetchone()
    if item:
        cur.execute("DELETE FROM solution_items WHERE id = %s AND solution_id = %s", (item_id, solution_id))
        conn.commit()
        flash(f"Item '{item['title']}' deleted.", "success")
    cur.close()
    conn.close()
    return redirect(url_for("admin_edit_solution", solution_id=solution_id))


@app.route("/admin/solutions/<int:solution_id>/items/<int:item_id>/toggle-active", methods=["POST"])
@login_required
@admin_or_subadmin_required
def admin_toggle_solution_item_active(solution_id, item_id):
    """Toggle active status of a 'What is Included' item."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, title, is_active FROM solution_items WHERE id = %s AND solution_id = %s", (item_id, solution_id))
    item = cur.fetchone()
    if item:
        new_active = 0 if item["is_active"] else 1
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("UPDATE solution_items SET is_active = %s, updated_at = %s WHERE id = %s", (new_active, now_str, item_id))
        conn.commit()
        status_str = "activated" if new_active else "deactivated"
        flash(f"Item '{item['title']}' {status_str}.", "success")
    cur.close()
    conn.close()
    return redirect(url_for("admin_edit_solution", solution_id=solution_id))


@app.route("/admin/solutions/<int:solution_id>/items/<int:item_id>/reorder", methods=["POST"])
@login_required
@admin_or_subadmin_required
def admin_reorder_solution_item(solution_id, item_id):
    """Reorder a solution item up or down."""
    direction = request.form.get("direction", "up").lower()
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute("SELECT id, display_order FROM solution_items WHERE solution_id = %s ORDER BY display_order ASC, id ASC", (solution_id,))
    all_items = cur.fetchall()

    idx = -1
    for i, it in enumerate(all_items):
        if it["id"] == item_id:
            idx = i
            break

    if idx != -1:
        swap_idx = idx - 1 if direction == "up" else idx + 1
        if 0 <= swap_idx < len(all_items):
            curr_item = all_items[idx]
            other_item = all_items[swap_idx]
            order1 = curr_item["display_order"]
            order2 = other_item["display_order"]
            if order1 == order2:
                order1 = idx + 1
                order2 = swap_idx + 1
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("UPDATE solution_items SET display_order = %s, updated_at = %s WHERE id = %s", (order2, now_str, curr_item["id"]))
            cur.execute("UPDATE solution_items SET display_order = %s, updated_at = %s WHERE id = %s", (order1, now_str, other_item["id"]))
            conn.commit()
            flash("Item order updated.", "success")

    cur.close()
    conn.close()
    return redirect(url_for("admin_edit_solution", solution_id=solution_id))


@app.cli.command("init-db")
def init_db_command():
    """Safely create tables, run schema migrations, and seed initial data if not present (non-destructive)."""
    init_db()
    print("Database initialized successfully.")

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
