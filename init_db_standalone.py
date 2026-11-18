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


def run_deployment_migration():
    total_start = time.time()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    print("=" * 76)
    print(" AIRODRONE PRODUCTION DATABASE MIGRATION SYSTEM")
    print(f" Timestamp:              {timestamp}")
    print(f" Target Schema Version:  {CURRENT_SCHEMA_VERSION}")
    print("=" * 76)

    with app.app_context():
        db_type = get_db_type()
        print(f"[*] Database dialect:    {db_type.upper()}")

        # Phase 1: Test Connectivity
        print("\n[Phase 1/5] Testing database connectivity...")
        t0 = time.time()
        conn = None
        try:
            conn = get_db_connection()
            cur = get_db_cursor(conn)
            cur.execute("SELECT 1;")
            cur.fetchone()
            cur.close()
            t_conn = time.time() - t0
            print(f" -> Database connection established in {t_conn:.2f}s.")
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
            init_db()
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
        print("\n[Phase 4/5] Verifying physical presence of all essential tables...")
        t0 = time.time()
        cur = get_db_cursor(conn)
        missing_tables = []
        table_stats = []

        for tbl in ESSENTIAL_TABLES:
            try:
                cur.execute(f"SELECT COUNT(*) as count FROM {tbl};")
                row = cur.fetchone()
                count = row["count"] if isinstance(row, dict) else row[0]
                table_stats.append((tbl, count, "OK"))
            except Exception as e:
                missing_tables.append((tbl, str(e)))
                table_stats.append((tbl, 0, "MISSING"))

        for tbl, count, status in table_stats:
            icon = "[+]" if status == "OK" else "[-]"
            print(f" {icon} {tbl.ljust(34)}: {str(count).rjust(6)} rows ({status})")

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
        if missing_tables:
            print(f" [!] FAILED: {len(missing_tables)} essential tables are missing!", file=sys.stderr)
            for t, err in missing_tables:
                print(f"     - {t}: {err}", file=sys.stderr)
            print("=" * 76)
            sys.exit(1)
        else:
            print(f" [SUCCESS] All database tables verified. Migration complete in {total_elapsed:.2f}s.")
            print(" Safe for Gunicorn web workers to begin serving production traffic.")
            print("=" * 76)
            sys.exit(0)


if __name__ == "__main__":
    run_deployment_migration()
