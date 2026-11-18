"""
SQLAlchemy SQLite 데이터베이스 설정.

local.db  — 개인 데이터: sessions, results, memos, change_requests, agile_tasks
shared.db — 공유 데이터: users, teams, subscriptions, published_snapshots
            (NAVIGATOR-SERVER가 owns shared.db; 기본 경로는 ../NAVIGATOR-SERVER/shared.db)
"""

import os
from sqlalchemy import create_engine, text
from sqlalchemy import inspect
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker, DeclarativeBase


def _is_sqlite_url(url: str) -> bool:
    return str(url).startswith("sqlite:")


def _engine_options(url: str) -> dict:
    if _is_sqlite_url(url):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True, "pool_recycle": 1800}


def _cloud_sql_postgres_url():
    socket_path = os.environ.get("INSTANCE_UNIX_SOCKET")
    password = os.environ.get("NAVIGATOR_DB_PASSWORD") or os.environ.get("DB_PASS")
    if not socket_path or not password:
        return None

    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("NAVIGATOR_DB_USER", "navigator_user"),
        password=password,
        database=os.environ.get("NAVIGATOR_DB_NAME", "navigator_shared"),
        query={"host": socket_path},
    )


def _database_url(env_name: str, fallback_sqlite_url: str):
    return (
        os.environ.get(env_name)
        or os.environ.get("NAVIGATOR_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or _cloud_sql_postgres_url()
        or fallback_sqlite_url
    )

# ── 로컬 DB (개인 산출물) ───────────────────────────────────
_STORAGE_DIR = os.environ.get(
    "NAVIGATOR_STORAGE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage"),
)
os.makedirs(_STORAGE_DIR, exist_ok=True)

LOCAL_DB_PATH = os.path.join(_STORAGE_DIR, "local.db")
LOCAL_DB_URL = _database_url("NAVIGATOR_LOCAL_DATABASE_URL", f"sqlite:///{LOCAL_DB_PATH}")

engine = create_engine(LOCAL_DB_URL, **_engine_options(LOCAL_DB_URL))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ── 공유 DB (로컬 환경 통합: local.db와 병합) ─────────────────────
SHARED_DB_PATH = LOCAL_DB_PATH
SHARED_DB_URL = LOCAL_DB_URL

shared_engine = engine
SharedSessionLocal = SessionLocal


class Base(DeclarativeBase):
    """로컬 DB 전용 Base."""
    pass


class SharedBase(DeclarativeBase):
    """공유 DB 전용 Base (통합으로 인해 동일한 engine에 바인딩됩니다)."""
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_shared_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_column_if_missing(conn, table: str, column: str, col_def: str) -> None:
    if conn.dialect.name == "sqlite":
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        existing = {row[1] for row in rows}
    else:
        existing = {col["name"] for col in inspect(conn).get_columns(table)}
    if column not in existing:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))


def _migrate_role_constraint(conn) -> None:
    """users.role CheckConstraint를 새 역할 포함하도록 확장 (shared.db)."""
    if conn.dialect.name != "sqlite":
        return
    schema = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")
    ).scalar() or ""
    if "devops" in schema:
        return

    conn.execute(text("ALTER TABLE users RENAME TO users_bak"))
    conn.execute(text("""
        CREATE TABLE users (
            id TEXT NOT NULL PRIMARY KEY,
            team_id TEXT REFERENCES teams(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL DEFAULT 'engineer',
            github_username TEXT,
            github_id TEXT UNIQUE,
            github_login TEXT,
            github_oauth_token TEXT,
            password_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("INSERT INTO users SELECT * FROM users_bak"))
    conn.execute(text("DROP TABLE users_bak"))


def _run_migrations() -> None:
    """기존 DB에 나중에 추가된 컬럼을 안전하게 추가합니다."""
    # 공유 DB 마이그레이션 (users, teams)
    with shared_engine.begin() as conn:
        _add_column_if_missing(conn, "teams", "github_client_id",     "TEXT")
        _add_column_if_missing(conn, "teams", "github_client_secret", "TEXT")
        _add_column_if_missing(conn, "users", "github_username",      "TEXT")
        _add_column_if_missing(conn, "users", "github_id",            "TEXT")
        _add_column_if_missing(conn, "users", "github_login",         "TEXT")
        _add_column_if_missing(conn, "users", "github_oauth_token",   "TEXT")
        _migrate_role_constraint(conn)
        _add_column_if_missing(conn, "users", "signup_method", "TEXT")
        # shortcut: legacy email accounts already linked to GitHub are indistinguishable; audit those rows if needed.
        conn.execute(text("""
            UPDATE users SET signup_method = CASE
                WHEN github_id IS NOT NULL OR password_hash = '' THEN 'github'
                ELSE 'email'
            END WHERE signup_method IS NULL
        """))

    # 로컬 DB 마이그레이션 (agile_tasks 등)
    with engine.begin() as conn:
        try:
            _add_column_if_missing(conn, "agile_tasks", "team_id", "TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            _add_column_if_missing(conn, "memo_items", "reflected_version", "VARCHAR(32)")
        except Exception:
            pass


def init_db():
    from auth.shared_models import User, Team, Subscription, PublishedSnapshot, DirectMessage  # noqa: F401
    from auth.quota import EmailModelUsage  # noqa: F401
    from auth.models import (  # noqa: F401
        AnalysisSession, DesignChangeRequest, MemoItem, AnalysisResult,
    )

    # 공유 테이블 생성 (shared.db)
    SharedBase.metadata.create_all(bind=shared_engine)

    # 로컬 테이블 생성 (local.db)
    Base.metadata.create_all(bind=engine)

    _run_migrations()
