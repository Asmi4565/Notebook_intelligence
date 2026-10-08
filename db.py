"""
Database Layer for Notebook Intelligence System (NIS)
Provides persistent storage for notebooks, pages, transcripts, quiz histories, and daily usage.
Supports SQLite (local dev) and PostgreSQL (when DATABASE_URL is set for Vercel/cloud).
"""

import os
import json
import sqlite3
import urllib.parse
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import date, datetime

# Check if PostgreSQL / Supabase DATABASE_URL environment variable is provided
def get_db_url() -> Optional[str]:
    return (
        os.environ.get("DATABASE_URL") or 
        os.environ.get("POSTGRES_URL") or 
        os.environ.get("POSTGRES_URL_NON_POOLING") or 
        os.environ.get("SUPABASE_DB_URL") or 
        os.environ.get("SUPABASE_POSTGRES_URL")
    )

def is_postgres() -> bool:
    url = get_db_url()
    return bool(url and url.strip())

def get_user_table_name(base_name: str, user_id: str) -> str:
    """Return a user-scoped table name, e.g., 'pages_{user_id}'.
    If user_id is empty or None, returns the base name.
    """
    if not user_id:
        return base_name
    # Sanitize user_id to alphanumeric and underscore only
    safe_user = ''.join(c if c.isalnum() else '_' for c in user_id)
    return f"{base_name}_{safe_user}"


_PG_TABLES_INITIALIZED = False

# Unified DB Connection Context Manager
class DBConnection:
    def __init__(self):
        global _PG_TABLES_INITIALIZED
        self.use_pg = is_postgres()
        if self.use_pg:
            try:
                import pg8000
                db_url = get_db_url().strip()
                if db_url.startswith("postgres://"):
                    db_url = "postgresql://" + db_url[11:]
                parsed = urllib.parse.urlparse(db_url)
                kwargs = {
                    "user": parsed.username or "postgres",
                    "password": parsed.password or "",
                    "host": parsed.hostname or "localhost",
                    "port": parsed.port or 5432,
                    "database": parsed.path.lstrip("/") or "postgres",
                    "timeout": 5
                }
                query_params = urllib.parse.parse_qs(parsed.query)
                sslmode = query_params.get("sslmode", ["require"])[0]
                if sslmode != "disable":
                    import ssl
                    kwargs["ssl_context"] = ssl.create_default_context()
                self.conn = pg8000.connect(**kwargs)
                if not _PG_TABLES_INITIALIZED:
                    self._ensure_postgres_tables()
                    _PG_TABLES_INITIALIZED = True
            except Exception as e:
                # Fallback to local /tmp SQLite if PostgreSQL connection fails
                print(f"[DB Warning] Postgres connection failed ({e}), falling back to SQLite.")
                self.use_pg = False
                self._connect_sqlite()
        else:
            self._connect_sqlite()

    def _ensure_postgres_tables(self):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS notebooks (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    user_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS pages (
                    page_id TEXT PRIMARY KEY,
                    notebook_id TEXT NOT NULL,
                    user_id TEXT,
                    page_number INTEGER NOT NULL,
                    source_filename TEXT NOT NULL,
                    image_data TEXT,
                    ocr_text TEXT NOT NULL,
                    ocr_status TEXT NOT NULL,
                    is_code BOOLEAN NOT NULL DEFAULT FALSE,
                    ocr_error TEXT,
                    folder TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS quiz_history (
                    id SERIAL PRIMARY KEY,
                    notebook_id TEXT NOT NULL,
                    num_questions INTEGER NOT NULL,
                    questions_json TEXT NOT NULL,
                    user_score INTEGER DEFAULT 0,
                    total_score INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS folders (
                    id TEXT PRIMARY KEY,
                    notebook_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS daily_usage (
                    user_id TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    ocr_count INTEGER DEFAULT 0,
                    ai_count INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, usage_date)
                );
            """)
            self.conn.commit()
        except Exception as e:
            print(f"[DB Warning] Postgres schema initialization check warning: {e}")

    def _connect_sqlite(self):
        if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            db_file = os.environ.get("NIS_DB_FILE", "/tmp/notebooks.db")
        else:
            BASE_DIR = Path(__file__).resolve().parent
            DATA_DIR = BASE_DIR / "data"
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            DEFAULT_DB_FILE = DATA_DIR / "notebooks.db"
            db_file = os.environ.get("NIS_DB_FILE", str(DEFAULT_DB_FILE))
        Path(db_file).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_file, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self._ensure_sqlite_tables()

    def _ensure_sqlite_tables(self):
        cursor = self.conn.cursor()
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS notebooks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS pages (
                page_id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                user_id TEXT,
                page_number INTEGER NOT NULL,
                source_filename TEXT NOT NULL,
                image_data TEXT,
                ocr_text TEXT NOT NULL,
                ocr_status TEXT NOT NULL,
                is_code BOOLEAN NOT NULL DEFAULT 0,
                ocr_error TEXT,
                folder TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_pages_notebook_page ON pages(notebook_id, page_number);
            CREATE TABLE IF NOT EXISTS folders (
                id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_folders_notebook ON folders(notebook_id);
            CREATE TABLE IF NOT EXISTS quiz_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notebook_id TEXT NOT NULL,
                num_questions INTEGER NOT NULL,
                questions_json TEXT NOT NULL,
                user_score INTEGER DEFAULT 0,
                total_score INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id TEXT NOT NULL,
                usage_date TEXT NOT NULL,
                ocr_count INTEGER DEFAULT 0,
                ai_count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, usage_date)
            );
        """)

        # Migration: ensure user_id column exists on pages if created in previous version
        try:
            cursor.execute("SELECT user_id FROM pages LIMIT 1")
        except Exception:
            try:
                cursor.execute("ALTER TABLE pages ADD COLUMN user_id TEXT DEFAULT NULL")
            except Exception:
                pass

        # Migration: ensure folder column exists on pages if created in previous version
        try:
            cursor.execute("SELECT folder FROM pages LIMIT 1")
        except Exception:
            try:
                cursor.execute("ALTER TABLE pages ADD COLUMN folder TEXT DEFAULT NULL")
            except Exception:
                pass

        # FTS5 Virtual Table for SQLite
        try:
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                    page_id UNINDEXED,
                    notebook_id UNINDEXED,
                    page_number UNINDEXED,
                    source_filename UNINDEXED,
                    ocr_text,
                    tokenize='unicode61'
                )
            """)
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
                    INSERT INTO pages_fts(page_id, notebook_id, page_number, source_filename, ocr_text)
                    VALUES (new.page_id, new.notebook_id, new.page_number, new.source_filename, new.ocr_text);
                END;
            """)
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
                    DELETE FROM pages_fts WHERE page_id = old.page_id;
                END;
            """)
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
                    UPDATE pages_fts
                    SET page_number = new.page_number, ocr_text = new.ocr_text
                    WHERE page_id = old.page_id;
                END;
            """)
        except Exception:
            pass

        cursor.execute("SELECT COUNT(*) as count FROM notebooks WHERE id = 'current_session'")
        row = cursor.fetchone()
        if not row or row["count"] == 0:
            cursor.execute("INSERT OR IGNORE INTO notebooks (id, name, user_id) VALUES ('current_session', 'General Academic Notes', NULL)")
        self.conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.conn.rollback()
        else:
            self.conn.commit()
        self.conn.close()

    def execute(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        if self.use_pg:
            pg_query = query.replace("?", "%s")
            cursor.execute(pg_query, params)
            if cursor.description:
                cols = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                return [dict(zip(cols, row)) for row in rows]
            return []
        else:
            cursor.execute(query, params)
            if cursor.description:
                rows = cursor.fetchall()
                return [dict(r) for r in rows]
            return []

    def execute_non_query(self, query: str, params: tuple = ()) -> int:
        cursor = self.conn.cursor()
        if self.use_pg:
            pg_query = query.replace("?", "%s")
            cursor.execute(pg_query, params)
            return cursor.rowcount
        else:
            cursor.execute(query, params)
            return cursor.rowcount


def get_connection():
    """Backward compatibility helper for sqlite3 connection in local mode."""
    BASE_DIR = Path(__file__).resolve().parent
    DATA_DIR = BASE_DIR / "data"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_DB_FILE = DATA_DIR / "notebooks.db"
    db_file = os.environ.get("NIS_DB_FILE", str(DEFAULT_DB_FILE))
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db():
    """Initializes schema and tables automatically for SQLite or PostgreSQL."""
    with DBConnection() as db:
        if db.use_pg:
            # PostgreSQL Schema Initialization
            db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS notebooks (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    user_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS pages (
                    page_id TEXT PRIMARY KEY,
                    notebook_id TEXT NOT NULL,
                    user_id TEXT,
                    page_number INTEGER NOT NULL,
                    source_filename TEXT NOT NULL,
                    image_data TEXT,
                    ocr_text TEXT NOT NULL,
                    ocr_status TEXT NOT NULL,
                    is_code BOOLEAN NOT NULL DEFAULT FALSE,
                    ocr_error TEXT,
                    folder TEXT DEFAULT 'General',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
                )
            """)
            try:
                db.execute("ALTER TABLE pages ADD COLUMN user_id TEXT DEFAULT NULL")
            except Exception:
                pass
            try:
                db.execute("ALTER TABLE pages ADD COLUMN folder TEXT DEFAULT 'General'")
            except Exception:
                pass
            db.execute("""
                CREATE TABLE IF NOT EXISTS folders (
                    id TEXT PRIMARY KEY,
                    notebook_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS quiz_history (
                    id SERIAL PRIMARY KEY,
                    notebook_id TEXT NOT NULL,
                    num_questions INTEGER NOT NULL,
                    questions_json TEXT NOT NULL,
                    user_score INTEGER DEFAULT 0,
                    total_score INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS daily_usage (
                    user_id TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    ocr_count INTEGER DEFAULT 0,
                    ai_count INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, usage_date)
                )
            """)
            # Default notebook
            default_nb = db.execute("SELECT COUNT(*) as count FROM notebooks WHERE id = 'current_session'")
            if not default_nb or default_nb[0]["count"] == 0:
                db.execute("INSERT INTO notebooks (id, name, user_id) VALUES ('current_session', 'General Academic Notes', NULL)")
        else:
            # SQLite Schema Initialization
            db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS notebooks (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    user_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS pages (
                    page_id TEXT PRIMARY KEY,
                    notebook_id TEXT NOT NULL,
                    user_id TEXT,
                    page_number INTEGER NOT NULL,
                    source_filename TEXT NOT NULL,
                    image_data TEXT,
                    ocr_text TEXT NOT NULL,
                    ocr_status TEXT NOT NULL,
                    is_code BOOLEAN NOT NULL DEFAULT 0,
                    ocr_error TEXT,
                    folder TEXT DEFAULT 'General',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
                )
            """)
            try:
                db.execute("ALTER TABLE pages ADD COLUMN user_id TEXT DEFAULT NULL")
            except Exception:
                pass
            try:
                db.execute("ALTER TABLE pages ADD COLUMN folder TEXT DEFAULT 'General'")
            except Exception:
                pass
            db.execute("""
                CREATE TABLE IF NOT EXISTS folders (
                    id TEXT PRIMARY KEY,
                    notebook_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_pages_notebook_page 
                ON pages(notebook_id, page_number)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_folders_notebook 
                ON folders(notebook_id)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS quiz_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notebook_id TEXT NOT NULL,
                    num_questions INTEGER NOT NULL,
                    questions_json TEXT NOT NULL,
                    user_score INTEGER DEFAULT 0,
                    total_score INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS daily_usage (
                    user_id TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    ocr_count INTEGER DEFAULT 0,
                    ai_count INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, usage_date)
                )
            """)
            # FTS5 Virtual Table for SQLite
            db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                    page_id UNINDEXED,
                    notebook_id UNINDEXED,
                    page_number UNINDEXED,
                    source_filename UNINDEXED,
                    ocr_text,
                    tokenize='unicode61'
                )
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
                    INSERT INTO pages_fts(page_id, notebook_id, page_number, source_filename, ocr_text)
                    VALUES (new.page_id, new.notebook_id, new.page_number, new.source_filename, new.ocr_text);
                END;
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
                    DELETE FROM pages_fts WHERE page_id = old.page_id;
                END;
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
                    UPDATE pages_fts
                    SET page_number = new.page_number, ocr_text = new.ocr_text
                    WHERE page_id = old.page_id;
                END;
            """)
            count_res = db.execute("SELECT COUNT(*) as count FROM notebooks WHERE id = 'current_session'")
            if not count_res or count_res[0]["count"] == 0:
                db.execute("INSERT INTO notebooks (id, name, user_id) VALUES ('current_session', 'General Academic Notes', NULL)")


# ==================== User Authentication & CRUD ====================

import hashlib
import secrets
import uuid

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100_000)
    return f"{salt.hex()}:{key.hex()}"


def verify_password(password: str, hashed_str: str) -> bool:
    try:
        salt_hex, key_hex = hashed_str.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expected_key = bytes.fromhex(key_hex)
        key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100_000)
        return secrets.compare_digest(key, expected_key)
    except Exception:
        return False


def create_user(name: str, email: str, password: str = "", user_id: Optional[str] = None) -> Dict[str, Any]:
    if not user_id:
        user_id = str(uuid.uuid4())
    pw_hash = hash_password(password) if password else ""
    clean_email = email.strip().lower()

    with DBConnection() as db:
        users_count = db.execute("SELECT COUNT(*) as count FROM users")
        is_first_user = (users_count and users_count[0]["count"] == 0)
        
        db.execute("""
            INSERT OR REPLACE INTO users (id, name, email, password_hash)
            VALUES (?, ?, ?, ?)
        """, (user_id, name.strip(), clean_email, pw_hash))

        if is_first_user:
            db.execute("UPDATE notebooks SET user_id = ? WHERE user_id IS NULL OR user_id = ''", (user_id,))

    return {"id": user_id, "name": name.strip(), "email": clean_email}



def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    clean_email = email.strip().lower()
    with DBConnection() as db:
        rows = db.execute("SELECT id, name, email, password_hash, created_at FROM users WHERE lower(email) = ?", (clean_email,))
        return rows[0] if rows else None


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    with DBConnection() as db:
        rows = db.execute("SELECT id, name, email, created_at FROM users WHERE id = ?", (user_id,))
        return rows[0] if rows else None


def update_user_name(user_id: str, new_name: str) -> bool:
    with DBConnection() as db:
        count = db.execute_non_query("UPDATE users SET name = ? WHERE id = ?", (new_name.strip(), user_id))
        return count > 0


def update_user_password(user_id: str, new_password: str) -> bool:
    pw_hash = hash_password(new_password)
    with DBConnection() as db:
        count = db.execute_non_query("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user_id))
        return count > 0


# ==================== Notebook CRUD ====================

def list_notebooks(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    with DBConnection() as db:
        if user_id:
            # Ensure user has at least one default personal notebook
            user_default_id = f"nb_{user_id[:8]}"
            count_res = db.execute("SELECT COUNT(*) as count FROM notebooks WHERE user_id = ?", (user_id,))
            if not count_res or count_res[0]["count"] == 0:
                create_notebook(user_default_id, "My Lecture Notes", user_id=user_id)

            rows = db.execute("""
                SELECT n.id, n.name, n.created_at, COUNT(p.page_id) as page_count
                FROM notebooks n
                LEFT JOIN pages p ON n.id = p.notebook_id
                WHERE n.user_id = ?
                GROUP BY n.id, n.name, n.created_at
                ORDER BY n.created_at ASC
            """, (user_id,))
        else:
            rows = db.execute("""
                SELECT n.id, n.name, n.created_at, COUNT(p.page_id) as page_count
                FROM notebooks n
                LEFT JOIN pages p ON n.id = p.notebook_id
                WHERE n.user_id IS NULL OR n.user_id = '' OR n.id = 'current_session'
                GROUP BY n.id, n.name, n.created_at
                ORDER BY n.created_at ASC
            """)
        return rows


def verify_notebook_owner(notebook_id: str, user_id: Optional[str]) -> bool:
    with DBConnection() as db:
        rows = db.execute("SELECT user_id FROM notebooks WHERE id = ?", (notebook_id,))
        if not rows:
            return True
        owner_id = rows[0]["user_id"]
        if not owner_id:
            return True
        return owner_id == user_id


def create_notebook(notebook_id: str, name: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    with DBConnection() as db:
        if db.use_pg:
            db.execute("""
                INSERT INTO notebooks (id, name, user_id) VALUES (?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, user_id = EXCLUDED.user_id
            """, (notebook_id, name, user_id))
        else:
            db.execute("""
                INSERT INTO notebooks (id, name, user_id) 
                VALUES (?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, user_id = EXCLUDED.user_id
            """, (notebook_id, name, user_id))
    return {"id": notebook_id, "name": name, "user_id": user_id}


def delete_notebook(notebook_id: str, user_id: Optional[str] = None) -> bool:
    with DBConnection() as db:
        if user_id:
            # Delete from user‑specific tables if they exist
            user_pages = get_user_table_name('pages', user_id)
            user_notebooks = get_user_table_name('notebooks', user_id)
            # Use generic tables as fallback if user‑specific not present
            try:
                db.execute(f"DELETE FROM {user_pages} WHERE notebook_id = ?", (notebook_id,))
            except Exception:
                db.execute("DELETE FROM pages WHERE notebook_id = ?", (notebook_id,))
            try:
                db.execute(f"DELETE FROM {user_notebooks} WHERE id = ?", (notebook_id,))
            except Exception:
                db.execute("DELETE FROM notebooks WHERE id = ?", (notebook_id,))
            db.execute("DELETE FROM quiz_history WHERE notebook_id = ?", (notebook_id,))
        else:
            db.execute("DELETE FROM pages WHERE notebook_id = ?", (notebook_id,))
            db.execute("DELETE FROM quiz_history WHERE notebook_id = ?", (notebook_id,))
            db.execute("DELETE FROM notebooks WHERE id = ?", (notebook_id,))
    return True


# ==================== Page CRUD ====================

def save_pages(notebook_id: str, pages_data: List[Dict[str, Any]], user_id: Optional[str] = None):
    """Save pages for a notebook into the shared 'pages' table, tagged with user_id.
    Uses the shared tables with user_id column for ownership — avoids the per-user
    table proliferation bug that caused 'no such table: pages_<user_id>' errors.
    """
    with DBConnection() as db:
        if db.use_pg:
            # Ensure notebook entry exists
            db.execute(
                "INSERT INTO notebooks (id, name, user_id) VALUES (?, ?, ?) ON CONFLICT (id) DO NOTHING",
                (notebook_id, notebook_id.replace("_", " ").title(), user_id)
            )
            for p in pages_data:
                folder_val = (p.get("folder") or "").strip() or None
                db.execute("""
                    INSERT INTO pages
                    (page_id, notebook_id, user_id, page_number, source_filename, image_data,
                     ocr_text, ocr_status, is_code, ocr_error, folder, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT (page_id) DO UPDATE SET
                        page_number = EXCLUDED.page_number,
                        source_filename = EXCLUDED.source_filename,
                        image_data = EXCLUDED.image_data,
                        ocr_text = EXCLUDED.ocr_text,
                        ocr_status = EXCLUDED.ocr_status,
                        is_code = EXCLUDED.is_code,
                        ocr_error = EXCLUDED.ocr_error,
                        folder = EXCLUDED.folder,
                        updated_at = CURRENT_TIMESTAMP
                """,
                (
                    p.get("page_id"),
                    notebook_id,
                    user_id,
                    p.get("page_number"),
                    p.get("source_filename"),
                    p.get("image_data"),
                    p.get("ocr_text", ""),
                    p.get("ocr_status", "pending"),
                    bool(p.get("is_code")),
                    p.get("ocr_error"),
                    folder_val,
                ))
        else:
            db.execute(
                "INSERT OR IGNORE INTO notebooks (id, name, user_id) VALUES (?, ?, ?)",
                (notebook_id, notebook_id.replace("_", " ").title(), user_id)
            )
            for p in pages_data:
                folder_val = (p.get("folder") or "").strip() or None
                db.execute("""
                    INSERT OR REPLACE INTO pages
                    (page_id, notebook_id, user_id, page_number, source_filename, image_data,
                     ocr_text, ocr_status, is_code, ocr_error, folder, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    p.get("page_id"),
                    notebook_id,
                    user_id,
                    p.get("page_number"),
                    p.get("source_filename"),
                    p.get("image_data"),
                    p.get("ocr_text", ""),
                    p.get("ocr_status", "pending"),
                    1 if p.get("is_code") else 0,
                    p.get("ocr_error"),
                    folder_val,
                ))


def get_db_pages(notebook_id: str, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve pages for a notebook from the shared 'pages' table.
    When user_id is provided, returns pages owned by that user or unassigned pages in that notebook.
    When user_id is None, if notebook has an owner, returns that owner's pages; otherwise returns all notebook pages.
    """
    with DBConnection() as db:
        if user_id:
            rows = db.execute("""
                SELECT page_id, notebook_id, user_id, page_number, source_filename,
                       image_data, ocr_text, ocr_status, is_code, ocr_error, folder
                FROM pages
                WHERE notebook_id = ? AND (user_id = ? OR user_id IS NULL OR user_id = '')
                ORDER BY page_number ASC
            """, (notebook_id, user_id))
        else:
            # Check if notebook has an owner
            nb_rows = db.execute("SELECT user_id FROM notebooks WHERE id = ?", (notebook_id,))
            nb_owner = nb_rows[0].get("user_id") if nb_rows else None
            if nb_owner:
                rows = db.execute("""
                    SELECT page_id, notebook_id, user_id, page_number, source_filename,
                           image_data, ocr_text, ocr_status, is_code, ocr_error, folder
                    FROM pages
                    WHERE notebook_id = ? AND (user_id = ? OR user_id IS NULL OR user_id = '')
                    ORDER BY page_number ASC
                """, (notebook_id, nb_owner))
            else:
                rows = db.execute("""
                    SELECT page_id, notebook_id, user_id, page_number, source_filename,
                           image_data, ocr_text, ocr_status, is_code, ocr_error, folder
                    FROM pages
                    WHERE notebook_id = ?
                    ORDER BY page_number ASC
                """, (notebook_id,))
        for r in rows:
            r["is_code"] = bool(r.get("is_code"))
            r["folder"] = r.get("folder") or ""
        return rows


def get_notebook_folders(notebook_id: str, user_id: Optional[str] = None) -> List[str]:
    """Returns persistent folders for the notebook, scoped to user_id when provided."""
    with DBConnection() as db:
        try:
            rows = db.execute("SELECT name FROM folders WHERE notebook_id = ? ORDER BY name ASC", (notebook_id,))
            folders = [r["name"] for r in rows if r.get("name")]
        except Exception:
            folders = []

        # Also include any distinct folders already assigned to pages for this user
        existing_set = set(folders)
        try:
            if user_id:
                page_rows = db.execute(
                    "SELECT DISTINCT folder FROM pages WHERE notebook_id = ? AND (user_id = ? OR user_id IS NULL OR user_id = '') AND folder IS NOT NULL AND folder != ''",
                    (notebook_id, user_id)
                )
            else:
                page_rows = db.execute(
                    "SELECT DISTINCT folder FROM pages WHERE notebook_id = ? AND folder IS NOT NULL AND folder != ''",
                    (notebook_id,)
                )
            for pr in page_rows:
                f = pr.get("folder")
                if f and f not in existing_set:
                    folders.append(f)
                    existing_set.add(f)
        except Exception as e:
            print(f"Error fetching folders from pages: {e}")
        return folders


def create_folder(notebook_id: str, name: str) -> Dict[str, Any]:
    folder_id = f"fld_{uuid.uuid4().hex[:8]}"
    clean_name = name.strip()
    with DBConnection() as db:
        if db.use_pg:
            db.execute("INSERT INTO notebooks (id, name) VALUES (?, ?) ON CONFLICT (id) DO NOTHING", (notebook_id, notebook_id.replace("_", " ").title()))
            db.execute("INSERT INTO folders (id, notebook_id, name) VALUES (?, ?, ?)", (folder_id, notebook_id, clean_name))
        else:
            db.execute("INSERT OR IGNORE INTO notebooks (id, name) VALUES (?, ?)", (notebook_id, notebook_id.replace("_", " ").title()))
            db.execute("INSERT OR REPLACE INTO folders (id, notebook_id, name) VALUES (?, ?, ?)", (folder_id, notebook_id, clean_name))
    return {"id": folder_id, "name": clean_name}


def delete_folder(notebook_id: str, name_or_id: str) -> bool:
    with DBConnection() as db:
        db.execute("DELETE FROM folders WHERE notebook_id = ? AND (id = ? OR name = ?)", (notebook_id, name_or_id, name_or_id))
        db.execute("UPDATE pages SET folder = NULL, updated_at = CURRENT_TIMESTAMP WHERE notebook_id = ? AND (folder = ? OR folder = ?)", (notebook_id, name_or_id, name_or_id))
    return True


def update_page_folder(notebook_id: str, page_number: int, folder_name: Optional[str]) -> bool:
    clean_folder = folder_name.strip() if (folder_name and folder_name.strip()) else None
    with DBConnection() as db:
        count = db.execute_non_query("""
            UPDATE pages SET folder = ?, updated_at = CURRENT_TIMESTAMP
            WHERE notebook_id = ? AND page_number = ?
        """, (clean_folder, notebook_id, page_number))
        return count > 0


def update_document_folder(notebook_id: str, source_filename: str, folder_name: Optional[str]) -> bool:
    clean_folder = folder_name.strip() if (folder_name and folder_name.strip()) else None
    with DBConnection() as db:
        count = db.execute_non_query("""
            UPDATE pages SET folder = ?, updated_at = CURRENT_TIMESTAMP
            WHERE notebook_id = ? AND source_filename = ?
        """, (clean_folder, notebook_id, source_filename))
        return count > 0


def get_user_stats(user_id: str) -> Dict[str, Any]:
    """Retrieves comprehensive account stats and usage limits for the user profile."""
    from datetime import datetime, timezone
    with DBConnection() as db:
        nb_rows = db.execute("SELECT id FROM notebooks WHERE user_id = ?", (user_id,))
        nb_ids = [r["id"] for r in nb_rows] if nb_rows else []
        total_notebooks = len(nb_ids)

        total_pages = 0
        if nb_ids:
            placeholders = ",".join(["?"] * len(nb_ids))
            p_rows = db.execute(f"SELECT COUNT(*) as count FROM pages WHERE notebook_id IN ({placeholders})", tuple(nb_ids))
            total_pages = p_rows[0]["count"] if p_rows else 0
        else:
            p_rows = db.execute("SELECT COUNT(*) as count FROM pages")
            total_pages = p_rows[0]["count"] if p_rows else 0

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        usage_row = db.execute("SELECT ocr_count, ai_count FROM daily_usage WHERE user_id = ? AND usage_date = ?", (user_id, today_str))
        ocr_count = usage_row[0]["ocr_count"] if usage_row else 0
        ai_count = usage_row[0]["ai_count"] if usage_row else 0

        user_row = db.execute("SELECT created_at FROM users WHERE id = ?", (user_id,))
        created_at = str(user_row[0]["created_at"])[:10] if user_row and user_row[0].get("created_at") else "Recently"

        # Also count user folders
        f_count_rows = db.execute("SELECT COUNT(*) as count FROM folders")
        total_folders = f_count_rows[0]["count"] if f_count_rows else 0

        return {
            "total_notebooks": total_notebooks,
            "total_pages": total_pages,
            "total_folders": total_folders,
            "ocr_usage": ocr_count,
            "ocr_limit": 50,
            "ai_usage": ai_count,
            "ai_limit": 50,
            "member_since": created_at
        }


def update_page_transcript(notebook_id: str, page_number: int, ocr_text: str, is_code: bool) -> bool:
    with DBConnection() as db:
        count = db.execute_non_query("""
            UPDATE pages 
            SET ocr_text = ?, is_code = ?, updated_at = CURRENT_TIMESTAMP
            WHERE notebook_id = ? AND page_number = ?
        """, (ocr_text, is_code if db.use_pg else (1 if is_code else 0), notebook_id, page_number))
        return count > 0


def delete_page(notebook_id: str, page_number: int) -> List[Dict[str, Any]]:
    with DBConnection() as db:
        db.execute("DELETE FROM pages WHERE notebook_id = ? AND page_number = ?", (notebook_id, page_number))
        remaining = db.execute("""
            SELECT page_id, notebook_id, source_filename, image_data, ocr_text, ocr_status, is_code, ocr_error
            FROM pages
            WHERE notebook_id = ?
            ORDER BY page_number ASC
        """, (notebook_id,))
        for idx, p in enumerate(remaining, start=1):
            db.execute("""
                UPDATE pages SET page_number = ?, page_id = ?
                WHERE page_id = ?
            """, (idx, f"{notebook_id}_p{idx}", p["page_id"]))
    return get_db_pages(notebook_id)


def delete_pages_by_numbers(notebook_id: str, page_numbers: List[int]) -> List[Dict[str, Any]]:
    with DBConnection() as db:
        for num in page_numbers:
            db.execute("DELETE FROM pages WHERE notebook_id = ? AND page_number = ?", (notebook_id, num))
        remaining = db.execute("""
            SELECT page_id, notebook_id, source_filename, image_data, ocr_text, ocr_status, is_code, ocr_error
            FROM pages
            WHERE notebook_id = ?
            ORDER BY page_number ASC
        """, (notebook_id,))
        for idx, p in enumerate(remaining, start=1):
            db.execute("""
                UPDATE pages SET page_number = ?, page_id = ?
                WHERE page_id = ?
            """, (idx, f"{notebook_id}_p{idx}", p["page_id"]))
    return get_db_pages(notebook_id)


def delete_document_pages(notebook_id: str, source_filename: str) -> List[Dict[str, Any]]:
    with DBConnection() as db:
        db.execute("DELETE FROM pages WHERE notebook_id = ? AND source_filename = ?", (notebook_id, source_filename))
        remaining = db.execute("""
            SELECT page_id, notebook_id, source_filename, image_data, ocr_text, ocr_status, is_code, ocr_error
            FROM pages
            WHERE notebook_id = ?
            ORDER BY page_number ASC
        """, (notebook_id,))
        for idx, p in enumerate(remaining, start=1):
            db.execute("""
                UPDATE pages SET page_number = ?, page_id = ?
                WHERE page_id = ?
            """, (idx, f"{notebook_id}_p{idx}", p["page_id"]))
    return get_db_pages(notebook_id)


def clear_db_pages(notebook_id: str):
    with DBConnection() as db:
        db.execute("DELETE FROM pages WHERE notebook_id = ?", (notebook_id,))


# ==================== Full-Text Search (SQLite FTS5 / Postgres ILIKE) ====================

def search_pages(notebook_id: str, query: str) -> List[Dict[str, Any]]:
    if not query or not query.strip():
        return []

    clean_terms = [w.replace('"', '').replace("'", '') for w in query.strip().split() if w]
    if not clean_terms:
        return []

    with DBConnection() as db:
        if db.use_pg:
            # Postgres ILIKE-based search with highlighted snippet creation
            results = []
            like_term = f"%{query.strip()}%"
            rows = db.execute("""
                SELECT page_number, source_filename, ocr_text
                FROM pages
                WHERE notebook_id = ? AND ocr_text ILIKE ?
                ORDER BY page_number ASC
            """, (notebook_id, like_term))
            
            for r in rows:
                text = r["ocr_text"]
                pos = text.lower().find(query.strip().lower())
                if pos != -1:
                    start = max(0, pos - 60)
                    end = min(len(text), pos + len(query.strip()) + 60)
                    raw_snippet = text[start:end]
                    highlighted = raw_snippet.replace(query.strip(), f"<mark>{query.strip()}</mark>")
                    snippet = f"...{highlighted}..."
                else:
                    snippet = text[:150]
                results.append({
                    "page_number": r["page_number"],
                    "source_filename": r["source_filename"],
                    "snippet": snippet
                })
            return results
        else:
            # SQLite FTS5 search
            fts_query = " OR ".join(f'"{term}"' for term in clean_terms)
            try:
                rows = db.execute("""
                    SELECT page_number, source_filename,
                           snippet(pages_fts, 4, '<mark>', '</mark>', '...', 12) as snippet
                    FROM pages_fts
                    WHERE notebook_id = ? AND pages_fts MATCH ?
                    ORDER BY rank
                """, (notebook_id, fts_query))
                return rows
            except Exception:
                rows = db.execute("""
                    SELECT page_number, source_filename, ocr_text
                    FROM pages
                    WHERE notebook_id = ? AND ocr_text LIKE ?
                    ORDER BY page_number ASC
                """, (notebook_id, f"%{query}%"))
                return [
                    {
                        "page_number": r["page_number"],
                        "source_filename": r["source_filename"],
                        "snippet": f"...<mark>{query}</mark>..." if query in r["ocr_text"] else r["ocr_text"][:150]
                    }
                    for r in rows
                ]


# ==================== Quiz History CRUD ====================

def save_quiz_history(notebook_id: str, num_questions: int, questions: list, user_score: int = 0, total_score: int = 0) -> int:
    with DBConnection() as db:
        if db.use_pg:
            rows = db.execute("""
                INSERT INTO quiz_history (notebook_id, num_questions, questions_json, user_score, total_score)
                VALUES (?, ?, ?, ?, ?) RETURNING id
            """, (notebook_id, num_questions, json.dumps(questions), user_score, total_score))
            return rows[0]["id"] if rows else 1
        else:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO quiz_history (notebook_id, num_questions, questions_json, user_score, total_score)
                    VALUES (?, ?, ?, ?, ?)
                """, (notebook_id, num_questions, json.dumps(questions), user_score, total_score))
                conn.commit()
                return cursor.lastrowid


def get_quiz_history(notebook_id: str) -> List[Dict[str, Any]]:
    with DBConnection() as db:
        rows = db.execute("""
            SELECT id, notebook_id, num_questions, questions_json, user_score, total_score, created_at
            FROM quiz_history
            WHERE notebook_id = ?
            ORDER BY created_at DESC
        """, (notebook_id,))
        result = []
        for r in rows:
            item = dict(r)
            try:
                item["questions"] = json.loads(item["questions_json"])
            except Exception:
                item["questions"] = []
            result.append(item)
        return result


# ==================== Per-User Daily Rate Limits (Item 8) ====================

def check_and_increment_daily_usage(user_id: str, action_type: str = "ocr", limit: int = 50) -> tuple[bool, int, int]:
    """Checks and increments per-user daily usage counters for OCR and AI calls."""
    today_str = date.today().isoformat()
    uid = user_id or "anonymous_session"
    col = "ocr_count" if action_type == "ocr" else "ai_count"

    with DBConnection() as db:
        rows = db.execute("SELECT ocr_count, ai_count FROM daily_usage WHERE user_id = ? AND usage_date = ?", (uid, today_str))
        if not rows:
            if db.use_pg:
                db.execute("""
                    INSERT INTO daily_usage (user_id, usage_date, ocr_count, ai_count)
                    VALUES (?, ?, 0, 0)
                    ON CONFLICT (user_id, usage_date) DO NOTHING
                """, (uid, today_str))
            else:
                db.execute("""
                    INSERT OR IGNORE INTO daily_usage (user_id, usage_date, ocr_count, ai_count)
                    VALUES (?, ?, 0, 0)
                """, (uid, today_str))
            current = 0
        else:
            current = rows[0][col]

        if current >= limit:
            return (False, current, limit)

        # Increment usage
        new_count = current + 1
        db.execute(f"UPDATE daily_usage SET {col} = ? WHERE user_id = ? AND usage_date = ?", (new_count, uid, today_str))
        return (True, new_count, limit)


# Initialize schema automatically
try:
    init_db()
except Exception as e:
    print(f"[DB] Deferred init_db warning: {e}")
