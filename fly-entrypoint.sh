#!/bin/sh
set -e

# Ensure upload directory exists on the persistent volume
mkdir -p /data/uploads

# Run pending schema migrations
python -c "
import sqlite3, os
db_path = '/data/food_storage.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(item_revisions)')]
    if 'notes' not in cols:
        conn.execute('ALTER TABLE item_revisions ADD COLUMN notes TEXT')
        conn.commit()
    conn.close()
"

# Run with gunicorn + uvicorn workers
exec gunicorn app.main:app \
    --bind 0.0.0.0:8000 \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 2 \
    --timeout 120
