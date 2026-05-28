from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal
from datetime import datetime


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    db_type: Literal["sqlite", "postgresql", "mysql"]
    host: Optional[str] = None
    port: Optional[int] = None
    database_name: str = Field(min_length=1)
    username: Optional[str] = None
    password: Optional[str] = None
    ssl_mode: str = "prefer"

    @field_validator("host")
    @classmethod
    def host_required_for_remote(cls, v, info):
        db_type = info.data.get("db_type")
        if db_type in ("postgresql", "mysql") and not v:
            raise ValueError("host is required for PostgreSQL and MySQL")
        return v


class ConnectionUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    database_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    ssl_mode: Optional[str] = None


class ConnectionResponse(BaseModel):
    id: str
    name: str
    db_type: str
    host: Optional[str] = None
    port: Optional[int] = None
    database_name: str
    username: Optional[str] = None
    ssl_mode: str
    last_used_at: Optional[str] = None
    created_at: str
    updated_at: str
    health_status: Optional[str] = None  # "ok" | "error" | None (unchecked)


class TestConnectionResponse(BaseModel):
    success: bool
    message: str
    tables: list[str] = []
    db_version: str = ""
    error_code: Optional[str] = None


class ConversationCreate(BaseModel):
    connection_id: str
    title: Optional[str] = "New Chat"


class ConversationUpdate(BaseModel):
    title: Optional[str] = None


class ConversationResponse(BaseModel):
    id: str
    title: str
    connection_id: str
    connection_name: Optional[str] = None
    db_type: Optional[str] = None
    message_count: int = 0
    created_at: str
    updated_at: str


class MessageResponse(BaseModel):
    id: int
    conversation_id: str
    role: str
    content: str
    sql: Optional[str] = None
    result_json: Optional[str] = None
    style: str = "normal"
    created_at: str


class ConversationDetail(BaseModel):
    id: str
    title: str
    connection_id: str
    connection_name: Optional[str] = None
    db_type: Optional[str] = None
    messages: list[MessageResponse] = []
    created_at: str
    updated_at: str


class FeedbackCreate(BaseModel):
    message_id: int
    rating: Literal["up", "down"]
    comment: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    style: str = "normal"
    conversation_id: Optional[str] = None
    connection_id: Optional[str] = None
