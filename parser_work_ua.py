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
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY,
        title TEXT,
        company TEXT,
        link TEXT,
        salary TEXT,
        summary TEXT DEFAULT '',
        inserted_at TEXT,
        posted_on_telegram INTEGER DEFAULT 0,
        UNIQUE(title, company)
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    ''')
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
    """
    Fetch page, insert new jobs into DB.
    Returns list of inserted job dicts.
    """
    init_db()
    resp = requests.get(URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')

    vacancies = soup.find_all('div', class_=lambda c: c and 'job-link' in c)

    inserted = []
    conn = get_conn()
    cur = conn.cursor()

    for vacancy in vacancies:
        h2 = vacancy.find('h2', class_='my-0')
        a_tag = h2.find('a') if h2 else None
        title = a_tag.get_text(strip=True) if a_tag else ''
        link = urljoin(BASE, a_tag['href']) if a_tag and a_tag.has_attr('href') else ''

        company = ''
        salary = ''
        spans = vacancy.find_all('span', class_=lambda c: c and 'strong-600' in c)
        for s in spans:
            mt_parent = s.find_parent('div', class_=lambda c: c and 'mt-xs' in c)
            if mt_parent:
                company = s.get_text(strip=True)
                continue
            parent_div = s.find_parent('div')
            if parent_div and not parent_div.has_attr('class'):
                salary = s.get_text(strip=True)
                continue
            if not salary and not mt_parent:
                salary = s.get_text(strip=True)
            if not company and mt_parent:
                company = s.get_text(strip=True)

        if not title:
            continue

        # check exists
        cur.execute('SELECT id FROM jobs WHERE title = ? AND company = ?', (title, company))
        if cur.fetchone():
            continue

        inserted_at = datetime.utcnow().isoformat()
        cur.execute(
            'INSERT INTO jobs (title, company, link, salary, inserted_at, posted_on_telegram) VALUES (?, ?, ?, ?, ?, 0)',
            (title, company, link, salary, inserted_at)
        )
        job_id = cur.lastrowid
        inserted.append({
            'id': job_id,
            'title': title,
            'company': company,
            'link': link,
            'salary': salary,
            'inserted_at': inserted_at
        })

    conn.commit()
    conn.close()
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