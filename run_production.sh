#!/usr/bin/env bash
set -e

echo "========================================================================"
echo "AIRODRONE - Pre-flight Production Migration & Server Startup"
echo "========================================================================"

# Run database migration once before workers start
echo "[1/2] Executing database schema migration..."
python init_db_standalone.py

# If migration succeeded, launch Gunicorn
echo "[2/2] Migrations verified. Starting Gunicorn web workers..."
exec gunicorn --timeout 600 wsgi:application
