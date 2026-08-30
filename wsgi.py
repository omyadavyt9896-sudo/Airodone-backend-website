from app import app, ensure_db_initialized

# Ensure production database schema is initialized/migrated on WSGI worker startup
try:
    ensure_db_initialized()
except Exception as e:
    pass

application = app
