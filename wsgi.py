import sys
from app import app, is_schema_initialized

# In production WSGI, individual worker processes must NEVER run concurrent DDL/migrations.
# Schema migrations are executed once per deployment via 'python init_db_standalone.py' (or Pre-Deploy Command).
try:
    if is_schema_initialized():
        print("[AIRODRONE WSGI] Database schema verified and active.", flush=True)
    else:
        print(
            "[AIRODRONE WSGI WARNING] Database schema sentinel or core tables not detected. "
            "Execute 'python init_db_standalone.py' before launching web workers.",
            file=sys.stderr,
            flush=True,
        )
except Exception as e:
    print(f"[AIRODRONE WSGI WARNING] Could not verify database schema on worker boot: {e}", file=sys.stderr, flush=True)

application = app
