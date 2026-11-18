"""
Standalone Deployment-Time Database Migration & Verification Tool for AIRODRONE.
Works across MySQL (PyMySQL), PostgreSQL, and SQLite.

Executes once per deployment BEFORE web workers launch.
Protected by a database-level distributed lock to guarantee that concurrent
processes can never collide on DDL or table metadata locks.

Usage:
    python init_db_standalone.py

Exit Codes:
    0 = Migration and verification succeeded. Safe to start web workers.
    1 = Migration or verification failed. Halts deployment before traffic is served.
"""
import sys
import os
import time
import traceback
from datetime import datetime, timezone

# Ensure script directory is on sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from app import (
    app,
    init_db,
    is_schema_initialized,
    acquire_migration_lock,
    release_migration_lock,
    get_db_connection,
    get_db_cursor,
    get_db_type,
    get_active_database_name,
    ping_or_reconnect_db,
    CURRENT_SCHEMA_VERSION,
)

ESSENTIAL_TABLES = [
    "users",
    "contacts",
    "courses",
    "course_enrollments",
    "modules",
    "course_videos",
    "video_progress",
    "quizzes",
    "quiz_questions",
    "quiz_attempts",
    "quiz_answers",
    "certificates",
    "products",
    "orders",
    "solutions",
    "solution_items",
    "laboratories",
    "lab_inquiries",
    "lab_inquiry_notes",
    "composite_lab_packages",
    "composite_lab_sectors",
    "composite_lab_faqs",
    "composite_lab_content",
    "composite_lab_enquiries",
    "composite_lab_notes",
    "composite_lab_activity_logs",
    "schema_version",
]

ESSENTIAL_COLUMNS = {
    "laboratories": ["id", "name", "slug", "about_image", "cover_image", "is_active"],
    "composite_lab_packages": ["id", "name", "slug", "price", "is_active"],
    "composite_lab_sectors": ["id", "name", "code", "is_active"],
    "composite_lab_content": ["id", "section_key", "title"],
    "composite_lab_enquiries": ["id", "school_name", "email", "phone", "status"],
    "products": ["id", "name", "slug", "price", "is_active"],
    "courses": ["id", "title", "slug", "grade"],
}


def run_deployment_migration():
    total_start = time.time()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    print("=" * 76)
    print(" AIRODRONE PRODUCTION DATABASE MIGRATION SYSTEM")
    print(f" Timestamp:              {timestamp}")
    print(f" Target Schema Version:  {CURRENT_SCHEMA_VERSION}")
    print("=" * 76)

    with app.app_context():
        # Phase 1: Test Connectivity & Database Identification
        print("\n[Phase 1/5] Testing database connectivity & identifying target database...")
        t0 = time.time()
        conn = None
        try:
            conn = get_db_connection()
            db_type = get_db_type(conn)
            print(f"[*] Database dialect:    {db_type.upper()}")
            cur = get_db_cursor(conn)
            cur.execute("SELECT 1;")
            cur.fetchone()
            active_db = get_active_database_name(conn)
            cur.close()
            t_conn = time.time() - t0
            print(f" -> Database connection established in {t_conn:.2f}s.")
            print(f" -> Active target database: {active_db}")
        except Exception as e:
            print(f" [!] CRITICAL: Failed to connect to database: {e}", file=sys.stderr)
            traceback.print_exc()
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            sys.exit(1)

        # Phase 2: Acquire Distributed Migration Lock
        print("\n[Phase 2/5] Acquiring distributed database migration lock...")
        t0 = time.time()
        lock_acquired = False
        try:
            lock_acquired = acquire_migration_lock(conn, timeout_seconds=30)
            if not lock_acquired:
                print(
                    " [!] ERROR: Migration lock could not be acquired within 30 seconds. "
                    "Another deployment migration may be in progress. Aborting to protect database integrity.",
                    file=sys.stderr,
                )
                conn.close()
                sys.exit(1)
            t_lock = time.time() - t0
            print(f" -> Distributed migration lock acquired successfully in {t_lock:.2f}s.")
        except Exception as e:
            print(f" [!] ERROR acquiring migration lock: {e}", file=sys.stderr)
            traceback.print_exc()
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            sys.exit(1)

        # Phase 3: Execute Schema Migration & Seeding
        print("\n[Phase 3/5] Executing idempotent schema migration & seeding pass...")
        t0 = time.time()
        try:
            cur = get_db_cursor(conn)
            if is_schema_initialized(conn=conn):
                print(f" -> Database schema is already initialized to version {CURRENT_SCHEMA_VERSION} with core tables.")
                print(" -> Running idempotent alignment pass using active locked connection...")

            # Pass conn and cur so the SAME active connection that holds the lock is used
            # This completely eliminates idle connection drops / socket reset during migrations
            init_db(conn=conn, cur=cur)
            cur.close()
            t_mig = time.time() - t0
            print(f" -> Migration & seeding pass completed successfully in {t_mig:.2f}s.")
        except Exception as e:
            print(f" [!] CRITICAL: Migration failed: {e}", file=sys.stderr)
            traceback.print_exc()
            try:
                release_migration_lock(conn)
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            sys.exit(1)

        # Phase 4: Verify Table Existence and Row Counts
        print("\n[Phase 4/5] Verifying physical presence of all essential tables and columns...")
        t0 = time.time()
        try:
            conn = ping_or_reconnect_db(conn)
            db_type = get_db_type(conn)
            cur = get_db_cursor(conn)
        except Exception as e:
            print(f" [!] CRITICAL: Database connection lost before verification: {e}", file=sys.stderr)
            traceback.print_exc()
            sys.exit(1)

        missing_tables = []
        missing_columns = []
        table_stats = []
        existing_tables = set()
        existing_columns = {}

        try:
            if db_type == "mysql":
                cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE();")
                existing_tables = {row["TABLE_NAME"] if isinstance(row, dict) else row[0] for row in cur.fetchall()}
                cur.execute("SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE();")
                for col_row in cur.fetchall():
                    tname = col_row["TABLE_NAME"] if isinstance(col_row, dict) else col_row[0]
                    cname = col_row["COLUMN_NAME"] if isinstance(col_row, dict) else col_row[1]
                    existing_columns.setdefault(tname, set()).add(cname)
            elif db_type == "postgres":
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';")
                existing_tables = {row["table_name"] if isinstance(row, dict) else row[0] for row in cur.fetchall()}
                cur.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public';")
                for col_row in cur.fetchall():
                    tname = col_row["table_name"] if isinstance(col_row, dict) else col_row[0]
                    cname = col_row["column_name"] if isinstance(col_row, dict) else col_row[1]
                    existing_columns.setdefault(tname, set()).add(cname)
            else:  # sqlite
                cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
                existing_tables = {row["name"] if isinstance(row, dict) else row[0] for row in cur.fetchall()}
                for tbl in existing_tables:
                    try:
                        cur.execute(f"PRAGMA table_info({tbl});")
                        prows = cur.fetchall()
                        existing_columns[tbl] = {pr["name"] if isinstance(pr, dict) else pr[1] for pr in prows}
                    except Exception:
                        pass
        except Exception as e:
            err_str = str(e).lower()
            if "gone away" in err_str or "reset by peer" in err_str or "broken pipe" in err_str or "2006" in err_str or "2013" in err_str:
                print(f" [!] CRITICAL: Database connection reset during schema metadata inspection: {e}", file=sys.stderr)
                sys.exit(1)
            raise e

        # Check each essential table
        for tbl in ESSENTIAL_TABLES:
            if tbl not in existing_tables:
                missing_tables.append((tbl, "Table not found in database catalog"))
                table_stats.append((tbl, 0, "MISSING"))
            else:
                try:
                    cur.execute(f"SELECT COUNT(*) as count FROM {tbl};")
                    row = cur.fetchone()
                    count = row["count"] if isinstance(row, dict) else row[0]
                    table_stats.append((tbl, count, "OK"))
                except Exception as e:
                    err_str = str(e).lower()
                    if "gone away" in err_str or "reset by peer" in err_str or "broken pipe" in err_str or "2006" in err_str or "2013" in err_str:
                        print(f" [!] CRITICAL: Database connection dropped while counting table '{tbl}': {e}", file=sys.stderr)
                        sys.exit(1)
                    missing_tables.append((tbl, str(e)))
                    table_stats.append((tbl, 0, "ERROR"))

        # Verify essential columns
        for tbl, cols in ESSENTIAL_COLUMNS.items():
            if tbl in existing_tables:
                cols_present = existing_columns.get(tbl, set())
                for col in cols:
                    if col not in cols_present:
                        missing_columns.append((tbl, col))

        for tbl, count, status in table_stats:
            icon = "[+]" if status == "OK" else "[-]"
            print(f" {icon} {tbl.ljust(34)}: {str(count).rjust(6)} rows ({status})")

        if missing_columns:
            for tbl, col in missing_columns:
                print(f" [-] Missing column: {tbl}.{col}")

        t_verify = time.time() - t0
        print(f" -> Table verification completed in {t_verify:.2f}s.")

        # Phase 5: Verify Schema Sentinel and Release Lock
        print("\n[Phase 5/5] Checking schema sentinel and releasing migration lock...")
        try:
            cur.execute("SELECT version, initialized_at FROM schema_version ORDER BY version DESC;")
            versions = cur.fetchall()
            for v in versions:
                ver_val = v["version"] if isinstance(v, dict) else v[0]
                init_time = v["initialized_at"] if isinstance(v, dict) else v[1]
                print(f" -> Active Schema Version: {ver_val} (stamped at {init_time})")
        except Exception as e:
            print(f" [!] Sentinel check warning: {e}", file=sys.stderr)

        # Release lock and close
        try:
            release_migration_lock(conn)
            print(" -> Migration lock released.")
        except Exception as e:
            print(f" [!] Warning releasing lock: {e}", file=sys.stderr)

        cur.close()
        conn.close()

        total_elapsed = time.time() - total_start
        print("\n" + "=" * 76)
        if missing_tables or missing_columns:
            if missing_tables:
                print(f" [!] FAILED: {len(missing_tables)} essential tables are missing!", file=sys.stderr)
                for t, err in missing_tables:
                    print(f"     - {t}: {err}", file=sys.stderr)
            if missing_columns:
                print(f" [!] FAILED: {len(missing_columns)} essential columns are missing!", file=sys.stderr)
                for t, col in missing_columns:
                    print(f"     - {t}.{col}", file=sys.stderr)
            print("=" * 76)
            sys.exit(1)
        else:
            print(f" [SUCCESS] All database tables verified. Migration complete in {total_elapsed:.2f}s.")
            print(" Safe for Gunicorn web workers to begin serving production traffic.")
            print("=" * 76)
            sys.exit(0)


if __name__ == "__main__":
    run_deployment_migration()
