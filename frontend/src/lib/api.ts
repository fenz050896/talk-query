export interface ChatResult {
  question: string;
  sql: string;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  response: string;
}

export type SSEEvent =
  | { type: "status"; status: string }
  | { type: "sql"; sql: string }
  | { type: "rejected"; message: string }
  | { type: "error"; message: string }
  | { type: "result" } & ChatResult
  | { type: "done" };

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Style = "normal" | "caveman" | "rtk" | "caveman+rtk";

export async function sendMessage(
  message: string,
  style: Style,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
  conversationId?: string,
  connectionId?: string,
): Promise<void> {
  const body: Record<string, unknown> = { message, style };
  if (conversationId) body.conversation_id = conversationId;
  if (connectionId) body.connection_id = connectionId;

  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    throw new Error(`Server error: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const data = JSON.parse(line.slice(6)) as SSEEvent;
          onEvent(data);
        } catch {
          // skip malformed data
        }
      }
    }
  }
}

// ── Connection Types ──────────────────────────────────────

export interface Connection {
  id: string;
  name: string;
  db_type: "sqlite" | "postgresql" | "mysql";
  host: string | null;
  port: number | null;
  database_name: string;
  username: string | null;
  ssl_mode: string;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
  health_status?: "ok" | "error" | null;
}

export interface ConnectionFormData {
  name: string;
  db_type: "sqlite" | "postgresql" | "mysql";
  host?: string;
  port?: number;
  database_name: string;
  username?: string;
  password?: string;
  ssl_mode?: string;
}

export interface TestResult {
  success: boolean;
  message: string;
  tables: string[];
  db_version: string;
  error_code?: string;
}

// ── Conversation Types ────────────────────────────────────

export interface Conversation {
  id: string;
  title: string;
  connection_id: string;
  connection_name?: string;
  db_type?: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: number;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  sql?: string | null;
  result_json?: string | null;
  style: string;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

// ── Connection API ────────────────────────────────────────

export async function fetchConnections(): Promise<Connection[]> {
  const res = await fetch(`${API_BASE}/api/connections`);
  if (!res.ok) throw new Error(`Failed to fetch connections: ${res.status}`);
  const data = await res.json();
  return data.connections;
}

export async function createConnection(formData: ConnectionFormData): Promise<Connection> {
  const res = await fetch(`${API_BASE}/api/connections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(formData),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Unknown error" }));
    throw new Error(err.detail || `Error ${res.status}`);
  }
  return res.json();
}

export async function updateConnection(id: string, formData: Partial<ConnectionFormData>): Promise<Connection> {
  const res = await fetch(`${API_BASE}/api/connections/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(formData),
  });
  if (!res.ok) throw new Error(`Failed to update connection: ${res.status}`);
  return res.json();
}

export async function deleteConnection(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/connections/${id}`, { method: "DELETE" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Unknown error" }));
    throw new Error(JSON.stringify(err.detail));
  }
}

export async function testConnection(data: ConnectionFormData): Promise<TestResult> {
  const res = await fetch(`${API_BASE}/api/connections/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function testExistingConnection(id: string): Promise<TestResult> {
  const res = await fetch(`${API_BASE}/api/connections/${id}/test`, { method: "POST" });
  return res.json();
}

export async function analyzeConnection(connId: string): Promise<{status: string; message: string; tables_analyzed: number; tier1_summary: string}> {
  const res = await fetch(`${API_BASE}/api/connections/${connId}/analyze`, { method: "POST" });
  if (res.status === 409) {
    const err = await res.json();
    throw new Error(err.detail || "Analysis still valid");
  }
  if (!res.ok) throw new Error("Analysis failed");
  return res.json();
}

// ── Conversation API ──────────────────────────────────────

export async function fetchConversations(): Promise<Conversation[]> {
  const res = await fetch(`${API_BASE}/api/conversations`);
  if (!res.ok) throw new Error(`Failed to fetch conversations: ${res.status}`);
  const data = await res.json();
  return data.conversations;
}

export async function fetchConversation(id: string): Promise<ConversationDetail> {
  const res = await fetch(`${API_BASE}/api/conversations/${id}`);
  if (!res.ok) throw new Error(`Failed to fetch conversation: ${res.status}`);
  return res.json();
}

export async function createConversation(connectionId: string, title?: string): Promise<Conversation> {
  const res = await fetch(`${API_BASE}/api/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connection_id: connectionId, title: title || "New Chat" }),
  });
  if (!res.ok) throw new Error(`Failed to create conversation: ${res.status}`);
  return res.json();
}

export async function updateConversationTitle(id: string, title: string): Promise<Conversation> {
  const res = await fetch(`${API_BASE}/api/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`Failed to update conversation: ${res.status}`);
  return res.json();
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/conversations/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Failed to delete conversation: ${res.status}`);
}

// ── Feedback API ──────────────────────────────────────────

export async function submitFeedback(messageId: number, rating: "up" | "down", comment?: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: messageId, rating, comment }),
  });
  if (!res.ok) throw new Error(`Failed to submit feedback: ${res.status}`);
}

// ── Schema API ────────────────────────────────────────────

export interface TableInfo {
  name: string;
  columns: { name: string; type: string; pk: boolean; nullable: boolean }[];
  foreign_keys: { columns: string[]; ref_table: string; ref_columns: string[] }[];
  row_count: number;
}

export interface TableSample {
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
}

export async function fetchSchemaTables(connectionId?: string): Promise<TableInfo[]> {
  const params = connectionId ? `?connection_id=${connectionId}` : "";
  const res = await fetch(`${API_BASE}/api/schema/tables${params}`);
  if (!res.ok) throw new Error(`Failed to fetch schema: ${res.status}`);
  const data = await res.json();
  return data.tables;
}

export async function fetchTableSample(tableName: string, connectionId?: string): Promise<TableSample> {
  const params = connectionId ? `?connection_id=${connectionId}` : "";
  const res = await fetch(`${API_BASE}/api/schema/tables/${tableName}/sample${params}`);
  if (!res.ok) throw new Error(`Failed to fetch sample: ${res.status}`);
  return res.json();
}
