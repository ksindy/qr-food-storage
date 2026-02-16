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

# Create tags and item_tags tables if they don't exist
python -c "
import sqlite3, os
db_path = '/data/food_storage.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(100) UNIQUE NOT NULL,
            is_default BOOLEAN DEFAULT 0,
            created_at DATETIME
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS item_tags (
            item_id INTEGER REFERENCES food_items(id),
            tag_id INTEGER REFERENCES tags(id),
            PRIMARY KEY (item_id, tag_id),
            UNIQUE (item_id, tag_id)
        )
    ''')
    # Seed default tags
    for tag_name in ['Prepared Meals', 'Snacks']:
        cur = conn.execute('SELECT id FROM tags WHERE name = ?', (tag_name,))
        if not cur.fetchone():
            conn.execute('INSERT INTO tags (name, is_default) VALUES (?, 1)', (tag_name,))
    conn.commit()
    conn.close()
"

# Run with gunicorn + uvicorn workers
exec gunicorn app.main:app \
    --bind 0.0.0.0:8000 \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 2 \
    --timeout 120
