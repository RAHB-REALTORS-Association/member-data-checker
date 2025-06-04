import sqlite3
import logging
import time
import json
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).resolve().parent.parent / "instance"
DB_FILE = DB_DIR / "mdc_app.sqlite3"

def init_db(db_path=None):
    path_to_use = db_path if db_path else DB_FILE
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = None
    try:
        conn = sqlite3.connect(path_to_use)
        cursor = conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS reco_cache (
            reco_number TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            raw_response TEXT
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reco_number TEXT NOT NULL,
            name TEXT,
            status_reported_by_reco TEXT,
            last_checked_reco INTEGER,
            first_flagged_timestamp INTEGER,
            last_flagged_timestamp INTEGER,
            notification_sent_timestamp INTEGER,
            notification_details TEXT,
            UNIQUE(reco_number)
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS run_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp INTEGER NOT NULL,
            status TEXT,
            message TEXT,
            summary TEXT,
            newly_flagged_members_count INTEGER,
            all_processed_members_details TEXT
        )
        ''')
        conn.commit()
        logger.info(f"Database initialized/verified successfully at {path_to_use}.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error during DB initialization: {e}")
        raise
    finally:
        if conn:
            conn.close()

def get_db_connection(db_path=None):
    path_to_use = db_path if db_path else DB_FILE
    conn = None
    try:
        conn = sqlite3.connect(path_to_use)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.error(f"SQLite error connecting to database: {e}")
        raise

if __name__ == '__main__':
    print(f"Initializing database at: {DB_FILE}")
    init_db()
    print("Database initialization complete.")
    db_conn_test = None
    try:
        db_conn_test = get_db_connection()
        print("Successfully connected to the database.")
        cursor = db_conn_test.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        print("Tables found:", [table['name'] for table in tables])
    except Exception as e:
        print(f"Error during database test: {e}")
    finally:
        if db_conn_test:
            db_conn_test.close()
