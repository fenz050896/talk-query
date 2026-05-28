import os
import re
import json
import asyncio
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from db import execute_sql
from schema import get_schema_ddl, get_schema_compact, get_table_names, get_tables_structured, get_table_sample
from llm import generate_sql, generate_response, generate_answer
from analyzer import load_profile, get_relevant_context, get_tier1_only_context, analyze_database
from connections import (
    init_connections_table, get_all_connections, get_connection, get_connection_with_password,
    create_connection, update_connection, delete_connection,
    test_connection as test_conn, test_existing_connection, get_engine, health_check,
)
from conversations import (
    init_conversations_table, create_conversation, get_conversations, get_conversation,
    update_conversation, delete_conversation, save_message, get_conversation_context,
    save_feedback, ensure_default_connection,
)
from models import (
    ConnectionCreate, ConnectionUpdate, TestConnectionResponse,
    ConversationCreate, ConversationUpdate,
    FeedbackCreate, ChatRequest,
)

load_dotenv()

app = FastAPI(title="Talk-Query API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://localhost:3001",
        "http://127.0.0.1:3000", "http://127.0.0.1:3001",
        "http://10.0.0.199:3001", "http://talk-query.duckdns.org",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_ROWS = int(os.getenv("MAX_ROWS", "100"))
QUERY_TIMEOUT = int(os.getenv("QUERY_TIMEOUT", "10"))

FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "CREATE", "REPLACE", "GRANT", "REVOKE", "EXEC", "EXECUTE",
    "ATTACH", "DETACH", "PRAGMA",
]


@app.on_event("startup")
async def startup():
    from connections import init_database_profiles_table
    init_connections_table()
    init_conversations_table()
    init_database_profiles_table()
    conn = ensure_default_connection()
    if conn:
        print(f"[startup] Created default connection: {conn.name}")


# ── Validation ──────────────────────────────────────────────

def validate_sql(sql: str) -> tuple[bool, str]:
    upper = sql.upper().strip()

    if upper.startswith("PRAGMA"):
        return True, sql

    if not upper.startswith("SELECT") and not upper.startswith("WITH"):
        return False, "ERROR: Only SELECT queries allowed."

    for kw in FORBIDDEN_KEYWORDS:
        pattern = rf"\b{kw}\b"
        if re.search(pattern, upper):
            if kw == "PRAGMA":
                continue
            return False, f"ERROR: {kw} statements are not allowed."

    if "LIMIT" not in upper:
        sql = sql.rstrip(";") + f" LIMIT {MAX_ROWS}"

    return True, sql


def format_output(question: str, sql: str, results: dict, response: str) -> dict:
    return {
        "question": question,
        "sql": sql,
        "columns": results["columns"],
        "rows": results["rows"],
        "row_count": results["row_count"],
        "response": response,
    }


# ── SSE Helpers ─────────────────────────────────────────────

def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


async def _error_event(message: str):
    yield _sse_event({"type": "error", "message": message})
    yield _sse_event({"type": "done"})


def _format_context(context: list[dict]) -> str:
    if not context:
        return ""
    lines = ["Previous conversation context:"]
    for entry in context:
        if entry["role"] == "user":
            lines.append(f"User: {entry['content']}")
        else:
            lines.append(f"Assistant: {entry['content']}")
            if entry.get("sql"):
                lines.append(f"SQL: {entry['sql']}")
            if entry.get("result_summary"):
                lines.append(f"Result: {entry['result_summary']}")
    return "\n".join(lines)


# ── Chat (modified for multi-turn + persistence) ──────────────

async def stream_chat(message: str, style: str = "normal",
                      conversation_id: Optional[str] = None,
                      connection_id: Optional[str] = None):
    # Resolve connection
    conn_row = None
    if connection_id:
        conn_row = get_connection_with_password(connection_id)
        if not conn_row:
            async for ev in _error_event("Connection not found."):
                yield ev
            return
    else:
        # Fallback: use first available connection
        all_conns = get_all_connections()
        if not all_conns:
            async for ev in _error_event(
                "No database connection configured. Add a connection first."
            ):
                yield ev
            return
        conn_row = get_connection_with_password(all_conns[0].id)
        connection_id = conn_row["id"]

    engine = get_engine(conn_row["id"])

    yield _sse_event({"type": "status", "status": "generating_sql"})

    # Load profile and build context
    profile = load_profile(conn_row["id"])
    context = ""
    schema = ""

    # Detect manual analysis trigger phrases
    ANALYZE_PHRASES = [
        "analisis ulang database", "refresh database context",
        "analyze database", "analisis database", "analisa database",
        "refresh database", "reanalyze", "perbarui analisis",
    ]
    msg_lower = message.lower().strip()
    is_analyze_cmd = any(phrase in msg_lower for phrase in ANALYZE_PHRASES)

    if is_analyze_cmd:
        yield _sse_event({"type": "status", "status": "analyzing"})
        try:
            profile = await analyze_database(engine, conn_row["id"], conn_row["db_type"], conn_row["database_name"], force=True)
            yield _sse_event({"type": "result", "question": message, "sql": "",
                              "columns": [], "rows": [], "row_count": 0,
                              "response": f"Analisis database selesai. {len(profile.table_descriptions)} tabel dianalisis. Silakan tanya tentang database ini."})
            yield _sse_event({"type": "done"})
        except Exception as e:
            yield _sse_event({"type": "error", "message": f"Gagal menganalisis database: {str(e)}"})
            yield _sse_event({"type": "done"})
        return

    if profile:
        try:
            context = await get_relevant_context(profile, message, engine)
        except Exception:
            context = get_tier1_only_context(profile)
        schema = get_schema_compact(engine) if style in ("rtk", "caveman+rtk") else get_schema_ddl(engine)
    else:
        schema = get_schema_compact(engine) if style in ("rtk", "caveman+rtk") else get_schema_ddl(engine)

    # Multi-turn context
    llm_message = message
    if conversation_id:
        context_msgs = get_conversation_context(conversation_id)
        if context_msgs:
            ctx_text = _format_context(context_msgs)
            if style in ("rtk", "caveman+rtk"):
                llm_message = f"Context:\n{ctx_text}\n\nTerse query: {message}"
            else:
                llm_message = f"{ctx_text}\n\nCurrent question: {message}"

    try:
        if profile:
            answer_type, answer_content = await generate_answer(llm_message, schema, context, style)
        else:
            answer_type = "sql"
            answer_content = await generate_sql(llm_message, schema, style)
    except Exception as e:
        yield _sse_event({"type": "error", "message": f"LLM Error: {str(e)}"})
        yield _sse_event({"type": "done"})
        return

    # Handle EXPLAIN responses (meta questions — no SQL execution)
    if answer_type == "explain":
        yield _sse_event({"type": "result", "question": message, "sql": "",
                          "columns": [], "rows": [], "row_count": 0,
                          "response": answer_content})
        yield _sse_event({"type": "done"})
        if conversation_id:
            try:
                save_message(conversation_id, "user", message, style=style)
                save_message(conversation_id, "assistant", answer_content, sql="",
                            result_json=json.dumps({"columns": [], "rows": [], "row_count": 0}),
                            style=style)
            except Exception:
                pass
        return

    # Handle SELECT responses (data questions) — existing flow continues below
    if answer_content.upper().startswith("ERROR:"):
        yield _sse_event({"type": "rejected", "message": answer_content[7:].strip()})
        yield _sse_event({"type": "done"})
        return

    valid, sql_or_error = validate_sql(answer_content)
    if not valid:
        yield _sse_event({"type": "rejected", "message": sql_or_error})
        yield _sse_event({"type": "done"})
        return

    sql = sql_or_error
    yield _sse_event({"type": "sql", "sql": sql})
    yield _sse_event({"type": "status", "status": "executing"})

    try:
        results = await asyncio.to_thread(execute_sql, sql, QUERY_TIMEOUT, engine, conn_row["db_type"])
    except Exception as e:
        yield _sse_event({"type": "error", "message": f"SQL Error: {str(e)}"})
        yield _sse_event({"type": "done"})
        return

    yield _sse_event({"type": "status", "status": "generating_response"})

    response = await generate_response(message, sql, results, style)

    output = format_output(message, sql, results, response)
    yield _sse_event({"type": "result", **output})
    yield _sse_event({"type": "done"})

    # Persist messages
    if conversation_id:
        try:
            save_message(conversation_id, "user", message, style=style)
            save_message(
                conversation_id, "assistant", response,
                sql=sql,
                result_json=json.dumps({
                    "columns": results["columns"],
                    "rows": results["rows"][:20],
                    "row_count": results["row_count"],
                }),
                style=style,
            )
            # Auto-title from first user message
            conv = get_conversation(conversation_id)
            if conv and len(conv.messages) <= 2:
                title = message[:60] + ("..." if len(message) > 60 else "")
                update_conversation(conversation_id, title)
        except Exception:
            pass


# ── Chat Endpoint ─────────────────────────────────────────────

@app.post("/api/chat")
async def chat(request: ChatRequest):
    return StreamingResponse(
        stream_chat(request.message, request.style,
                    request.conversation_id, request.connection_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Schema Endpoint ───────────────────────────────────────────

@app.get("/api/schema")
async def get_schema(connection_id: Optional[str] = None):
    if connection_id:
        conn_row = get_connection_with_password(connection_id)
        if not conn_row:
            raise HTTPException(status_code=404, detail="Connection not found")
    else:
        all_conns = get_all_connections()
        if not all_conns:
            return {"schema": "No connections configured."}
        conn_row = get_connection_with_password(all_conns[0].id)

    engine = get_engine(conn_row["id"])
    return {"schema": get_schema_ddl(engine)}


# ── Schema Detail Endpoints ───────────────────────────────────

@app.get("/api/schema/tables")
async def schema_tables(connection_id: Optional[str] = None):
    conn_row = _resolve_connection(connection_id)
    if not conn_row:
        raise HTTPException(status_code=404, detail="Connection not found")
    engine = get_engine(conn_row["id"])
    return {"tables": get_tables_structured(engine)}


@app.get("/api/schema/tables/{table_name}/sample")
async def schema_table_sample(table_name: str, connection_id: Optional[str] = None):
    conn_row = _resolve_connection(connection_id)
    if not conn_row:
        raise HTTPException(status_code=404, detail="Connection not found")
    engine = get_engine(conn_row["id"])
    return get_table_sample(engine, table_name)


# ── Health ────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    connections = get_all_connections()
    conversations = get_conversations()
    return {
        "status": "ok",
        "connections": len(connections),
        "conversations": len(conversations),
    }


# ── Connection Endpoints ──────────────────────────────────────

@app.get("/api/connections")
async def list_connections():
    connections = get_all_connections()
    # Add health status
    result = []
    for c in connections:
        d = c.model_dump()
        try:
            d["health_status"] = health_check(c.id) if connections else None
        except Exception:
            d["health_status"] = "error"
        result.append(d)
    return {"connections": result}


@app.post("/api/connections", status_code=201)
async def create_connection_endpoint(data: ConnectionCreate):
    conn = create_connection(data)

    # Trigger async analysis in background
    try:
        engine = get_engine(conn.id)
        asyncio.create_task(analyze_database(engine, conn.id, data.db_type, data.database_name))
    except Exception:
        pass

    return conn


@app.get("/api/connections/{conn_id}")
async def get_connection_endpoint(conn_id: str):
    conn = get_connection(conn_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return conn


@app.put("/api/connections/{conn_id}")
async def update_connection_endpoint(conn_id: str, data: ConnectionUpdate):
    conn = update_connection(conn_id, data)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return conn


@app.delete("/api/connections/{conn_id}", status_code=204)
async def delete_connection_endpoint(conn_id: str):
    ok, blocking = delete_connection(conn_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Cannot delete connection. It is referenced by conversations.",
                "conversations": blocking,
            },
        )
    return None


@app.post("/api/connections/test")
async def test_unsaved_connection(data: ConnectionCreate):
    return test_conn(data)


@app.post("/api/connections/{conn_id}/test")
async def test_saved_connection(conn_id: str):
    return test_existing_connection(conn_id)


@app.post("/api/connections/{conn_id}/analyze")
async def analyze_connection(conn_id: str, force: bool = False):
    """Trigger (re-)analysis of a database connection. Set ?force=true to skip staleness check."""
    conn_row = get_connection_with_password(conn_id)
    if not conn_row:
        raise HTTPException(status_code=404, detail="Connection not found")

    engine = get_engine(conn_id)
    try:
        profile = await analyze_database(
            engine, conn_id,
            db_type=conn_row["db_type"],
            db_name=conn_row["database_name"],
            force=force,
        )
        return {
            "status": "ok",
            "message": "Analysis complete",
            "tables_analyzed": len(profile.table_descriptions),
            "tier1_summary": profile.tier1_summary,
        }
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


# ── Conversation Endpoints ────────────────────────────────────

@app.get("/api/conversations")
async def list_conversations():
    convs = get_conversations()
    return {"conversations": [c.model_dump() for c in convs]}


@app.post("/api/conversations", status_code=201)
async def create_conversation_endpoint(data: ConversationCreate):
    return create_conversation(data.connection_id, data.title or "New Chat")


@app.get("/api/conversations/{conv_id}")
async def get_conversation_endpoint(conv_id: str):
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.patch("/api/conversations/{conv_id}")
async def update_conversation_endpoint(conv_id: str, data: ConversationUpdate):
    conv = update_conversation(conv_id, data.title or "")
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.delete("/api/conversations/{conv_id}", status_code=204)
async def delete_conversation_endpoint(conv_id: str):
    ok = delete_conversation(conv_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return None


# ── Feedback Endpoint ─────────────────────────────────────────

@app.post("/api/feedback", status_code=201)
async def create_feedback(data: FeedbackCreate):
    save_feedback(data.message_id, data.rating, data.comment)
    return {"status": "ok"}


# ── Helper ────────────────────────────────────────────────────

def _resolve_connection(connection_id: Optional[str] = None) -> Optional[dict]:
    if connection_id:
        return get_connection_with_password(connection_id)
    all_conns = get_all_connections()
    if not all_conns:
        return None
    return get_connection_with_password(all_conns[0].id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
