import sys
import traceback
from app import app, ensure_db_initialized

# Ensure production database schema is initialized/migrated on WSGI worker startup
try:
    print("[AIRODRONE WSGI] Starting database schema verification and migration...", flush=True)
    ensure_db_initialized()
    print("[AIRODRONE WSGI] Database schema initialization succeeded.", flush=True)
except Exception as e:
    print(f"[AIRODRONE WSGI ERROR] Database initialization failed on startup: {e}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)

application = app
