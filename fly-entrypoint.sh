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
    for col, typ in [('notes', 'TEXT'), ('amount', 'REAL'), ('amount_unit', 'TEXT')]:
        if col not in cols:
            conn.execute(f'ALTER TABLE item_revisions ADD COLUMN {col} {typ}')
    conn.commit()
    conn.close()
"

# Run with gunicorn + uvicorn workers
exec gunicorn app.main:app \
    --bind 0.0.0.0:8000 \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 2 \
    --timeout 120
