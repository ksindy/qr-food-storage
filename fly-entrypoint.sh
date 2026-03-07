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

# Create inventory_entries table and migrate existing data
python -c "
import sqlite3, os
db_path = '/data/food_storage.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS inventory_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES food_items(id),
            date_prepared DATE NOT NULL,
            expiration_date DATE,
            amount REAL DEFAULT 1,
            amount_unit VARCHAR(20) DEFAULT 'qty',
            is_consumed BOOLEAN DEFAULT 0,
            created_at DATETIME,
            consumed_at DATETIME
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS ix_inventory_entries_item_id
        ON inventory_entries(item_id)
    ''')
    # Seed one entry per existing item from its latest revision
    conn.execute('''
        INSERT INTO inventory_entries (item_id, date_prepared, expiration_date,
            amount, amount_unit, is_consumed, created_at, consumed_at)
        SELECT
            fi.id,
            lr.date_prepared,
            lr.expiration_date,
            COALESCE(lr.amount, 1),
            COALESCE(lr.amount_unit, 'qty'),
            lr.is_deleted,
            lr.created_at,
            CASE WHEN lr.is_deleted THEN lr.created_at ELSE NULL END
        FROM food_items fi
        JOIN item_revisions lr ON lr.item_id = fi.id
        WHERE lr.revision_num = (
            SELECT MAX(revision_num) FROM item_revisions WHERE item_id = fi.id
        )
        AND NOT EXISTS (
            SELECT 1 FROM inventory_entries WHERE item_id = fi.id
        )
    ''')
    conn.commit()
    conn.close()
"

# Run with uvicorn directly (saves ~40MB vs gunicorn master+worker)
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --timeout-keep-alive 120
