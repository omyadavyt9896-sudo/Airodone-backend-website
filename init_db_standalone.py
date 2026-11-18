"""
Standalone Database Initialization & Verification Script for AIRODRONE.
Works across MySQL (PyMySQL), PostgreSQL, and SQLite.
Can be run via:
    python init_db_standalone.py
Or integrated into Render Build / Pre-Deploy Command.
"""
import sys
import os
import traceback

# Ensure script directory is on sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from app import (
    app,
    init_db,
    ensure_db_initialized,
    is_schema_initialized,
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

def main():
    print("=" * 70)
    print("AIRODRONE - Standalone Database Initialization & Migration")
    print("=" * 70)

    with app.app_context():
        db_type = get_db_type()
        print(f"[*] Detected database type: {db_type.upper()}")
        print(f"[*] Target Schema Version:  {CURRENT_SCHEMA_VERSION}")

        # Step 1: Test Connectivity
        print("\n[Step 1/4] Testing database connection...")
        try:
            conn = get_db_connection()
            cur = get_db_cursor(conn)
            cur.execute("SELECT 1;")
            cur.fetchone()
            cur.close()
            conn.close()
            print(" -> Database connection established successfully.")
        except Exception as e:
            print(f" [!] FAILED to connect to database: {e}", file=sys.stderr)
            traceback.print_exc()
            sys.exit(1)

        # Step 2: Execute Schema Initialization
        print("\n[Step 2/4] Executing init_db() idempotent schema migration...")
        try:
            init_db()
            print(" -> Schema initialization and data seeding completed.")
        except Exception as e:
            print(f" [!] FAILED during init_db(): {e}", file=sys.stderr)
            traceback.print_exc()
            sys.exit(1)

        # Step 3: Verify Table Existence and Row Counts
        print("\n[Step 3/4] Verifying all essential tables...")
        conn = get_db_connection()
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
            print(f" {icon} {tbl.ljust(32)}: {str(count).rjust(6)} rows ({status})")

        # Step 4: Verify Schema Sentinel
        print("\n[Step 4/4] Verifying schema_version sentinel...")
        try:
            cur.execute("SELECT version, initialized_at FROM schema_version ORDER BY version DESC;")
            versions = cur.fetchall()
            for v in versions:
                ver_val = v["version"] if isinstance(v, dict) else v[0]
                init_time = v["initialized_at"] if isinstance(v, dict) else v[1]
                print(f" -> Recorded Schema Version: {ver_val} (initialized at {init_time})")
        except Exception as e:
            print(f" [!] schema_version check error: {e}")

        cur.close()
        conn.close()

        # Final check
        print("\n" + "=" * 70)
        if missing_tables:
            print(f"[!] FAILED: {len(missing_tables)} essential tables are missing!")
            for t, err in missing_tables:
                print(f"    - {t}: {err}")
            sys.exit(1)
        else:
            print("[SUCCESS] All required database tables are present and verified!")
            print("=" * 70)

if __name__ == "__main__":
    main()
