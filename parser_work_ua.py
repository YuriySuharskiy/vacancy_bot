import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import sqlite3
from datetime import datetime, timedelta

BASE = 'https://www.work.ua'
START_PATH = 'jobs-junior/'
URL = urljoin(BASE, START_PATH)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
}

DB_PATH = 'jobs.db'

def get_conn():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # створюємо таблицю, якщо нема (підлаштуй поля під вашу схему)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY,
        title TEXT,
        company TEXT,
        link TEXT,
        salary TEXT,
        summary TEXT DEFAULT '',
        inserted_at TEXT,
        posted_on_telegram INTEGER DEFAULT 0
    )
    """)
    # створюємо унікальний індекс по link (якщо в БД вже нема дублів)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_link ON jobs(link);")
    # таблиця meta
    cur.execute("""
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    conn.commit()
    conn.close()

def set_meta(key: str, value: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()

def get_meta(key: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT value FROM meta WHERE key = ?', (key,))
    row = cur.fetchone()
    conn.close()
    return row['value'] if row else None

def delete_old_jobs(days: int = 30):
    cutoff = datetime.utcnow() - timedelta(days=days)
    cutoff_iso = cutoff.isoformat()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('DELETE FROM jobs WHERE inserted_at < ?', (cutoff_iso,))
    conn.commit()
    conn.close()

def fetch_and_store():
    import requests
    import sqlite3
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    from datetime import datetime

    print("[debug] fetch_and_store starting")
    try:
        r = requests.get(URL, headers=HEADERS, timeout=12)
    except Exception as e:
        print("[debug] request error:", e)
        return []

    if r.status_code != 200:
        print("[debug] bad status:", r.status_code)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/jobs/" in href and href.rstrip("/") != "/jobs":
            full = urljoin(BASE, href)
            title = (a.get_text() or "").strip()
            found.append((full, title))

    print(f"[debug] parsed {len(found)} candidate links (raw)")
    # унікалізуємо по посиланню
    uniq = []
    seen = set()
    for u, t in found:
        if u not in seen:
            seen.add(u)
            uniq.append((u, t))
    print(f"[debug] unique links to consider: {len(uniq)}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    inserted = []
    for link, title in uniq:
        now = datetime.utcnow().isoformat()
        try:
            # підлаштуй поля під вашу схему (company, salary тощо)
            cur.execute(
                "INSERT OR IGNORE INTO jobs (title, company, link, salary, summary, inserted_at, posted_on_telegram) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (title, "", link, "", "", now)
            )
            conn.commit()
            # дізнаємось чи вставилось
            cur2 = conn.execute("SELECT id FROM jobs WHERE link = ?", (link,))
            row = cur2.fetchone()
            if row:
                inserted.append(link)
                print("[debug] ensured in DB:", link)
        except Exception as e:
            print("[debug] SQL error for", link, ":", e)
    conn.close()
    print(f"[debug] fetch_and_store done, inserted/ensured: {len(inserted)}")
    return inserted

def get_unposted_jobs():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM jobs WHERE posted_on_telegram = 0 ORDER BY inserted_at ASC')
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def mark_jobs_posted(job_ids):
    if not job_ids:
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.executemany('UPDATE jobs SET posted_on_telegram = 1 WHERE id = ?', [(jid,) for jid in job_ids])
    conn.commit()
    conn.close()

def save_job_summary(job_id: int, summary: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('UPDATE jobs SET summary = ? WHERE id = ?', (summary, job_id))
    conn.commit()
    conn.close()