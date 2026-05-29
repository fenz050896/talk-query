import os
import uuid
import sqlite3
import json
from typing import Optional
from urllib.parse import quote_plus
from sqlalchemy import create_engine, Engine, inspect
from dotenv import load_dotenv

from crypto import encrypt_password, decrypt_password
from models import ConnectionCreate, ConnectionUpdate, ConnectionResponse, TestConnectionResponse

load_dotenv()

_LOCAL_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "talkquery_config.db"))

# Engine cache: connection_id -> Engine
_engine_cache: dict[str, Engine] = {}


def _get_local_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_LOCAL_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_LOCAL_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_connections_table():
    conn = _get_local_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS connections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                db_type TEXT NOT NULL CHECK(db_type IN ('sqlite', 'postgresql', 'mysql')),
                host TEXT,
                port INTEGER,
                database_name TEXT NOT NULL,
                username TEXT,
                password_encrypted TEXT,
                ssl_mode TEXT DEFAULT 'prefer',
                last_used_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    finally:
        conn.close()


def init_database_profiles_table():
    conn = _get_local_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS database_profiles (
                connection_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                schema_hash TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE CASCADE
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _row_to_response(row: sqlite3.Row) -> ConnectionResponse:
    d = dict(row)
    d.pop("password_encrypted", None)
    return ConnectionResponse(**d)


def get_all_connections() -> list[ConnectionResponse]:
    conn = _get_local_conn()
    try:
        rows = conn.execute("SELECT * FROM connections ORDER BY last_used_at DESC").fetchall()
        return [_row_to_response(r) for r in rows]
    finally:
        conn.close()


def get_connection(conn_id: str) -> Optional[ConnectionResponse]:
    conn = _get_local_conn()
    try:
        row = conn.execute("SELECT * FROM connections WHERE id = ?", (conn_id,)).fetchone()
        return _row_to_response(row) if row else None
    finally:
        conn.close()


def get_connection_with_password(conn_id: str) -> Optional[dict]:
    """Internal: get connection including encrypted password."""
    conn = _get_local_conn()
    try:
        row = conn.execute("SELECT * FROM connections WHERE id = ?", (conn_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_connection(data: ConnectionCreate) -> ConnectionResponse:
    conn_id = str(uuid.uuid4())
    password_encrypted = encrypt_password(data.password) if data.password else None

    conn = _get_local_conn()
    try:
        conn.execute(
            """INSERT INTO connections (id, name, db_type, host, port, database_name, username, password_encrypted, ssl_mode, last_used_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (conn_id, data.name, data.db_type, data.host, data.port,
             data.database_name, data.username, password_encrypted, data.ssl_mode),
        )
        conn.commit()
    finally:
        conn.close()
    return get_connection(conn_id)


def update_connection(conn_id: str, data: ConnectionUpdate) -> Optional[ConnectionResponse]:
    existing = get_connection_with_password(conn_id)
    if not existing:
        return None

    updates = {}
    if data.name is not None:
        updates["name"] = data.name
    if data.host is not None:
        updates["host"] = data.host
    if data.port is not None:
        updates["port"] = data.port
    if data.database_name is not None:
        updates["database_name"] = data.database_name
    if data.username is not None:
        updates["username"] = data.username
    if data.password is not None and data.password != "":
        updates["password_encrypted"] = encrypt_password(data.password)
    if data.ssl_mode is not None:
        updates["ssl_mode"] = data.ssl_mode

    if not updates:
        return get_connection(conn_id)

    updates["updated_at"] = "datetime('now')"

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values())

    conn = _get_local_conn()
    try:
        conn.execute(f"UPDATE connections SET {set_clause} WHERE id = ?", values + [conn_id])
        conn.commit()
    finally:
        conn.close()

    invalidate_engine(conn_id)
    return get_connection(conn_id)


def delete_connection(conn_id: str) -> tuple[bool, list[str]]:
    """Delete a connection. Returns (success, blocking_conversations)."""
    conn = _get_local_conn()
    try:
        # Check blocking conversations
        blocking = conn.execute(
            "SELECT title FROM conversations WHERE connection_id = ?", (conn_id,)
        ).fetchall()
        if blocking:
            return False, [r["title"] for r in blocking]

        conn.execute("DELETE FROM connections WHERE id = ?", (conn_id,))
        conn.commit()

        # Clean up embedding cache
        try:
            from embeddings import delete_connection_cache
            delete_connection_cache(conn_id)
        except Exception:
            pass

        invalidate_engine(conn_id)
        return True, []
    finally:
        conn.close()


def touch_connection(conn_id: str):
    conn = _get_local_conn()
    try:
        conn.execute(
            "UPDATE connections SET last_used_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
            (conn_id,),
        )
        conn.commit()
    finally:
        conn.close()


def build_connection_url(conn_row: dict) -> str:
    """Build SQLAlchemy URL from connection row (must include password_encrypted)."""
    db_type = conn_row["db_type"]
    password = decrypt_password(conn_row.get("password_encrypted") or "")

    if db_type == "sqlite":
        return f"sqlite:///{conn_row['database_name']}"

    host = conn_row.get("host") or "localhost"
    port = conn_row.get("port") or (5432 if db_type == "postgresql" else 3306)
    username = conn_row.get("username", "")
    db_name = conn_row["database_name"]
    encoded_password = quote_plus(password) if password else ""

    if db_type == "postgresql":
        url = f"postgresql+psycopg2://{username}:{encoded_password}@{host}:{port}/{db_name}"
        # Add read-only mode
        sep = "?" if "?" not in url else "&"
        url += f"{sep}options=-c%20default_transaction_read_only%3Don"
        ssl_mode = conn_row.get("ssl_mode", "prefer")
        if ssl_mode and ssl_mode != "prefer":
            url += f"&sslmode={ssl_mode}"
        return url

    if db_type == "mysql":
        url = f"mysql+pymysql://{username}:{encoded_password}@{host}:{port}/{db_name}"
        return url

    raise ValueError(f"Unsupported db_type: {db_type}")


def get_engine(conn_id: str) -> Engine:
    if conn_id in _engine_cache:
        return _engine_cache[conn_id]

    row = get_connection_with_password(conn_id)
    if not row:
        raise ValueError(f"Connection {conn_id} not found")

    url = build_connection_url(row)
    db_type = row["db_type"]

    kwargs = {"pool_size": 5, "max_overflow": 10, "pool_pre_ping": True}
    if db_type == "sqlite":
        kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": 10,
        }
    elif db_type == "mysql":
        ssl_mode = row.get("ssl_mode", "prefer")
        ssl_args: dict = {}
        if ssl_mode == "require":
            ssl_args = {"ssl": {"check_hostname": True}}
        elif ssl_mode in ("verify-ca", "verify-full"):
            ssl_args = {"ssl": {"check_hostname": True}}
        elif ssl_mode == "disable":
            ssl_args = {"ssl": None}
        else:  # prefer
            ssl_args = {"ssl": {}}
        kwargs["connect_args"] = ssl_args

    engine = create_engine(url, **kwargs)

    if db_type == "sqlite":
        from sqlalchemy import event
        @event.listens_for(engine, "connect")
        def _set_wal(dbapi_conn, _rec):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")

    _engine_cache[conn_id] = engine

    # Update last_used_at
    touch_connection(conn_id)
    return engine


def invalidate_engine(conn_id: str):
    if conn_id in _engine_cache:
        _engine_cache[conn_id].dispose()
        del _engine_cache[conn_id]


def test_connection(data: ConnectionCreate) -> TestConnectionResponse:
    """Test a connection config without saving it."""
    row = {
        "db_type": data.db_type,
        "host": data.host,
        "port": data.port,
        "database_name": data.database_name,
        "username": data.username,
        "password_encrypted": encrypt_password(data.password) if data.password else None,
        "ssl_mode": data.ssl_mode,
    }
    return _run_test(row)


def test_existing_connection(conn_id: str) -> TestConnectionResponse:
    """Test an already-saved connection."""
    row = get_connection_with_password(conn_id)
    if not row:
        return TestConnectionResponse(success=False, message="Connection not found", error_code="NOT_FOUND")
    result = _run_test(row)
    return result


def _run_test(row: dict) -> TestConnectionResponse:
    try:
        url = build_connection_url(row)
        db_type = row["db_type"]
        kwargs = {}
        if db_type == "sqlite":
            kwargs["connect_args"] = {"check_same_thread": False}
        elif db_type == "mysql":
            ssl_mode = row.get("ssl_mode", "prefer")
            ssl_args: dict = {}
            if ssl_mode == "require":
                ssl_args = {"ssl": {"check_hostname": True}}
            elif ssl_mode in ("verify-ca", "verify-full"):
                ssl_args = {"ssl": {"check_hostname": True}}
            elif ssl_mode == "disable":
                ssl_args = {"ssl": None}
            else:
                ssl_args = {"ssl": {}}
            kwargs["connect_args"] = ssl_args

        engine = create_engine(url, **kwargs)
        conn = engine.connect()
        try:
            inspector = inspect(engine)
            tables = inspector.get_table_names()
            db_version = ""
            if db_type == "sqlite":
                db_version = conn.execute(
                    __import__("sqlalchemy").text("SELECT sqlite_version()")
                ).scalar()
            elif db_type == "postgresql":
                db_version = conn.execute(
                    __import__("sqlalchemy").text("SHOW server_version")
                ).scalar()
            elif db_type == "mysql":
                db_version = conn.execute(
                    __import__("sqlalchemy").text("SELECT VERSION()")
                ).scalar()
        finally:
            conn.close()
        engine.dispose()

        return TestConnectionResponse(
            success=True,
            message="Connection successful",
            tables=tables,
            db_version=str(db_version or ""),
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        error_code = type(e).__name__.upper()
        return TestConnectionResponse(
            success=False,
            message=str(e),
            error_code=error_code,
        )


def health_check(conn_id: str) -> str:
    """Quick health check. Returns "ok" or "error"."""
    try:
        engine = get_engine(conn_id)
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return "ok"
    except Exception:
        return "error"
