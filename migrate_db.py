import sqlite3
DB_PATH = 'jobs.db'

def ensure_column(cur, table, col, definition):
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# переконайтесь, що таблиця є
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
if not cur.fetchone():
    print("Таблиці jobs немає — нічого мігрувати.")
else:
    ensure_column(cur, 'jobs', 'summary', "TEXT DEFAULT ''")
    ensure_column(cur, 'jobs', 'posted_on_telegram', "INTEGER DEFAULT 0")
    ensure_column(cur, 'jobs', 'inserted_at', "TEXT")  # якщо потрібна
    print("Міграція завершена (додані відсутні колонки).")

conn.commit()
conn.close()