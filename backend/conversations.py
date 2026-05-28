import uuid
import json
from typing import Optional
from connections import _get_local_conn
from models import ConversationResponse, ConversationDetail, MessageResponse


def init_conversations_table():
    conn = _get_local_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT 'New Chat',
                connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                sql TEXT,
                result_json TEXT,
                style TEXT DEFAULT 'normal',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                rating TEXT NOT NULL CHECK(rating IN ('up', 'down')),
                comment TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    finally:
        conn.close()


def create_conversation(connection_id: str, title: str = "New Chat") -> ConversationResponse:
    conv_id = str(uuid.uuid4())
    conn = _get_local_conn()
    try:
        conn.execute(
            "INSERT INTO conversations (id, title, connection_id, created_at, updated_at) VALUES (?, ?, ?, datetime('now'), datetime('now'))",
            (conv_id, title, connection_id),
        )
        conn.commit()
    finally:
        conn.close()
    return _get_conversation_summary(conv_id)


def get_conversations() -> list[ConversationResponse]:
    conn = _get_local_conn()
    try:
        rows = conn.execute("""
            SELECT c.*, co.name as connection_name, co.db_type,
                   (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as message_count
            FROM conversations c
            JOIN connections co ON c.connection_id = co.id
            ORDER BY c.updated_at DESC
        """).fetchall()
        return [_row_to_conv_response(r) for r in rows]
    finally:
        conn.close()


def get_conversation(conv_id: str) -> Optional[ConversationDetail]:
    conn = _get_local_conn()
    try:
        row = conn.execute("""
            SELECT c.*, co.name as connection_name, co.db_type
            FROM conversations c
            JOIN connections co ON c.connection_id = co.id
            WHERE c.id = ?
        """, (conv_id,)).fetchone()
        if not row:
            return None

        messages = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conv_id,),
        ).fetchall()

        return ConversationDetail(
            id=row["id"],
            title=row["title"],
            connection_id=row["connection_id"],
            connection_name=row["connection_name"],
            db_type=row["db_type"],
            messages=[_row_to_msg_response(m) for m in messages],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    finally:
        conn.close()


def update_conversation(conv_id: str, title: str) -> Optional[ConversationResponse]:
    conn = _get_local_conn()
    try:
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = datetime('now') WHERE id = ?",
            (title, conv_id),
        )
        conn.commit()
    finally:
        conn.close()
    return _get_conversation_summary(conv_id)


def delete_conversation(conv_id: str) -> bool:
    conn = _get_local_conn()
    try:
        cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def save_message(conv_id: str, role: str, content: str, sql: str = None,
                 result_json: str = None, style: str = "normal") -> int:
    conn = _get_local_conn()
    try:
        cursor = conn.execute(
            """INSERT INTO messages (conversation_id, role, content, sql, result_json, style, created_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (conv_id, role, content, sql, result_json, style),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
            (conv_id,),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_conversation_context(conv_id: str, n_messages: int = 6) -> list[dict]:
    """Get last N messages as context for the LLM."""
    conn = _get_local_conn()
    try:
        # Get user-assistant pairs: user question -> assistant SQL + result
        rows = conn.execute(
            """SELECT role, content, sql, result_json FROM messages
               WHERE conversation_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (conv_id, n_messages * 2),
        ).fetchall()

        # Reverse to chronological order
        rows = list(reversed(rows))

        context = []
        for row in rows:
            if row["role"] == "user":
                context.append({"role": "user", "content": row["content"]})
            else:
                ctx = {"role": "assistant", "content": row["content"]}
                if row["sql"]:
                    ctx["sql"] = row["sql"]
                if row["result_json"]:
                    try:
                        result = json.loads(row["result_json"])
                        ctx["result_summary"] = f"{result.get('row_count', 0)} rows, columns: {', '.join(result.get('columns', []))}"
                    except (json.JSONDecodeError, TypeError):
                        pass
                context.append(ctx)
        return context
    finally:
        conn.close()


def save_feedback(message_id: int, rating: str, comment: str = None):
    conn = _get_local_conn()
    try:
        # Upsert: delete existing then insert
        conn.execute("DELETE FROM feedback WHERE message_id = ?", (message_id,))
        conn.execute(
            "INSERT INTO feedback (message_id, rating, comment, created_at) VALUES (?, ?, ?, datetime('now'))",
            (message_id, rating, comment),
        )
        conn.commit()
    finally:
        conn.close()


def ensure_default_connection():
    """Create default SQLite connection from DATABASE_URL if no connections exist."""
    import os
    from models import ConnectionCreate
    from connections import get_all_connections, create_connection

    existing = get_all_connections()
    if existing:
        return None

    db_url = os.getenv("DATABASE_URL", "sqlite:///../data/talkquery.db")
    database_name = db_url.replace("sqlite:///", "")

    default = ConnectionCreate(
        name="Default (SQLite)",
        db_type="sqlite",
        database_name=database_name,
    )
    return create_connection(default)


def _get_conversation_summary(conv_id: str) -> Optional[ConversationResponse]:
    conn = _get_local_conn()
    try:
        row = conn.execute("""
            SELECT c.*, co.name as connection_name, co.db_type,
                   (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as message_count
            FROM conversations c
            JOIN connections co ON c.connection_id = co.id
            WHERE c.id = ?
        """, (conv_id,)).fetchone()
        if not row:
            return None
        return _row_to_conv_response(row)
    finally:
        conn.close()


def _row_to_conv_response(row) -> ConversationResponse:
    d = dict(row)
    return ConversationResponse(
        id=d["id"],
        title=d["title"],
        connection_id=d["connection_id"],
        connection_name=d.get("connection_name"),
        db_type=d.get("db_type"),
        message_count=d.get("message_count", 0),
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


def _row_to_msg_response(row) -> MessageResponse:
    d = dict(row)
    return MessageResponse(
        id=d["id"],
        conversation_id=d["conversation_id"],
        role=d["role"],
        content=d["content"],
        sql=d.get("sql"),
        result_json=d.get("result_json"),
        style=d.get("style", "normal"),
        created_at=d["created_at"],
    )
