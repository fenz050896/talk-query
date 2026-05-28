"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { Send, Loader2, ToggleLeft, ToggleRight, Database, Circle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { MessageBubble } from "./message-bubble";
import { sendMessage, analyzeConnection, type ChatResult, type SSEEvent, type Style, type Message } from "@/lib/api";
import { useConversations } from "@/lib/conversation-store";
import { useConnections } from "@/lib/connection-store";

interface ChatMessage {
  id: string;
  role: "user" | "system";
  content: string;
  messageId?: number;
  result?: ChatResult;
  loading?: boolean;
  error?: boolean;
  style?: Style;
}

const DB_ICONS: Record<string, string> = { postgresql: "🐘", mysql: "🐬", sqlite: "💾" };

function generateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
}

export function ChatPanel() {
  const { activeConversation, activeConversationId } = useConversations();
  const { connections } = useConnections();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [cavemanOn, setCavemanOn] = useState(false);
  const [rtkOn, setRtkOn] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Load messages when active conversation changes
  useEffect(() => {
    if (activeConversation?.messages) {
      const msgs: ChatMessage[] = activeConversation.messages.map((m: Message) => ({
        id: m.id.toString(),
        role: m.role as "user" | "system",
        content: m.content,
        messageId: m.id,
        result: m.result_json ? {
          question: "",
          sql: m.sql || "",
          columns: [],
          rows: [],
          row_count: 0,
          response: m.content,
        } : undefined,
        style: m.style as Style,
      }));
      // Reconstruct result from stored data
      msgs.forEach((m) => {
        const orig = activeConversation.messages.find((o: Message) => o.id === m.messageId);
        if (orig?.result_json) {
          try {
            const parsed = JSON.parse(orig.result_json);
            m.result = {
              question: "",
              sql: orig.sql || "",
              columns: parsed.columns || [],
              rows: parsed.rows || [],
              row_count: parsed.row_count || 0,
              response: orig.content,
            };
          } catch { /* ignore */ }
        }
      });
      setMessages(msgs);
    } else {
      setMessages([]);
    }
  }, [activeConversation?.id, activeConversation?.messages]);

  const connection = connections.find((c) => c.id === activeConversation?.connection_id);

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
    });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || sending || !activeConversationId) return;

    const style: Style = cavemanOn && rtkOn ? "caveman+rtk"
      : cavemanOn ? "caveman"
      : rtkOn ? "rtk"
      : "normal";

    const userMsg: ChatMessage = {
      id: generateId(),
      role: "user",
      content: text,
    };

    const systemMsgId = generateId();
    const systemMsg: ChatMessage = {
      id: systemMsgId,
      role: "system",
      content: "",
      loading: true,
      style,
    };

    setMessages((prev) => [...prev, userMsg, systemMsg]);
    setInput("");
    setSending(true);

    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }

    const controller = new AbortController();
    abortRef.current = controller;

    let sql = "";
    let result: ChatResult | undefined;

    try {
      await sendMessage(
        text,
        style,
        (event: SSEEvent) => {
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== systemMsgId) return m;

              switch (event.type) {
                case "status":
                  return { ...m, content: statusText(event.status), loading: true };
                case "sql":
                  sql = event.sql;
                  return { ...m, content: "Executing query...", loading: true };
                case "rejected":
                  return { ...m, content: event.message, loading: false, error: true };
                case "error":
                  return { ...m, content: event.message, loading: false, error: true };
                case "result":
                  result = {
                    question: event.question,
                    sql: event.sql,
                    columns: event.columns,
                    rows: event.rows,
                    row_count: event.row_count,
                    response: event.response,
                  };
                  return { ...m, content: event.response, result, loading: false };
                default:
                  return m;
              }
            })
          );
        },
        controller.signal,
        activeConversationId,
        activeConversation?.connection_id,
      );
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === systemMsgId
            ? { ...m, content: `Connection error: ${err instanceof Error ? err.message : "Unknown error"}`, loading: false, error: true }
            : m
        )
      );
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
    setSending(false);
    setMessages((prev) =>
      prev.map((m) =>
        m.loading ? { ...m, content: "Stopped.", loading: false } : m
      )
    );
  };

  const handleAnalyze = async () => {
    if (!connection || !connection.id) return;
    setAnalyzing(true);
    try {
      const result = await analyzeConnection(connection.id);
      alert(`Analysis complete: ${result.tables_analyzed} tables analyzed.`);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Analysis failed.");
    } finally {
      setAnalyzing(false);
    }
  };

  // Empty state: no active chat
  if (!activeConversationId) {
    return (
      <div className="flex flex-col h-screen max-w-3xl mx-auto items-center justify-center text-center px-6">
        <h2 className="text-xl font-semibold text-muted-foreground mb-2">
          TalkQuery
        </h2>
        <p className="text-sm text-muted-foreground/60 max-w-md">
          Create a new chat from the sidebar to start asking questions about your database.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen max-w-3xl mx-auto">
      {/* Header */}
      <header className="flex-shrink-0 border-b px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold">{activeConversation?.title || "TalkQuery"}</h1>
            {connection && (
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className="text-xs">{DB_ICONS[connection.db_type] || "📦"}</span>
                <span className="text-xs text-muted-foreground">{connection.name}</span>
                <Circle
                  className={`h-1.5 w-1.5 fill-current ${
                    connection.health_status === "ok" ? "text-green-500" : connection.health_status === "error" ? "text-red-500" : "text-muted-foreground/30"
                  }`}
                />
                <button
                  onClick={handleAnalyze}
                  disabled={analyzing || sending}
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50 ml-2"
                  title="Analyze database"
                >
                  <RefreshCw className={`h-3 w-3 ${analyzing ? "animate-spin" : ""}`} />
                  {analyzing ? "Analyzing..." : "Analyze"}
                </button>
              </div>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => setCavemanOn(!cavemanOn)}
              className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-md border transition-colors ${
                cavemanOn ? "bg-orange-500/10 border-orange-500/30 text-orange-600" : "bg-background border-border text-muted-foreground hover:border-muted-foreground/30"
              }`}
            >
              {cavemanOn ? <ToggleRight className="h-3.5 w-3.5" /> : <ToggleLeft className="h-3.5 w-3.5" />}
              Caveman
            </button>
            <button
              onClick={() => setRtkOn(!rtkOn)}
              className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-md border transition-colors ${
                rtkOn ? "bg-blue-500/10 border-blue-500/30 text-blue-600" : "bg-background border-border text-muted-foreground hover:border-muted-foreground/30"
              }`}
            >
              {rtkOn ? <ToggleRight className="h-3.5 w-3.5" /> : <ToggleLeft className="h-3.5 w-3.5" />}
              RTK
            </button>
          </div>
        </div>
        {(cavemanOn || rtkOn) && (
          <div className="flex gap-2 mt-2">
            {cavemanOn && <span className="text-[10px] px-1.5 py-0.5 rounded bg-orange-500/10 text-orange-600">Caveman: terse responses</span>}
            {rtkOn && <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-600">RTK: compact input</span>}
          </div>
        )}
      </header>

      {/* Messages */}
      <ScrollArea className="flex-1 px-6" ref={scrollRef}>
        <div className="py-6 space-y-6">
          {messages.length === 0 && (
            <div className="text-center py-20 space-y-3">
              <h2 className="text-xl font-semibold text-muted-foreground">
                Ask your database anything
              </h2>
              <p className="text-sm text-muted-foreground/60 max-w-md mx-auto">
                Try questions like &ldquo;How many active users are there?&rdquo; or
                &ldquo;Show me the top 5 orders by price&rdquo;
              </p>
              <div className="flex flex-wrap gap-2 justify-center mt-6">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => setInput(s)}
                    className="px-3 py-1.5 text-xs rounded-full border bg-muted/30 hover:bg-muted/50 transition-colors"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              role={msg.role}
              content={msg.content}
              result={msg.result}
              loading={msg.loading}
              style={msg.style}
              messageId={msg.messageId}
            />
          ))}
        </div>
      </ScrollArea>

      {/* Input */}
      <div className="flex-shrink-0 border-t px-6 py-4 bg-background">
        <div className="flex gap-3 items-end">
          <Textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              const el = e.target;
              el.style.height = "auto";
              el.style.height = Math.min(el.scrollHeight, 120) + "px";
            }}
            onKeyDown={handleKeyDown}
            placeholder="Ask about your data..."
            className="min-h-[44px] max-h-[120px] resize-none"
            disabled={sending}
            rows={1}
          />
          {sending ? (
            <Button variant="outline" size="icon" onClick={handleStop} className="flex-shrink-0">
              <Loader2 className="h-4 w-4 animate-spin" />
            </Button>
          ) : (
            <Button onClick={handleSend} disabled={!input.trim()} size="icon" className="flex-shrink-0">
              <Send className="h-4 w-4" />
            </Button>
          )}
        </div>
        <p className="text-[10px] text-muted-foreground/50 mt-2 text-center">
          Only SELECT queries are allowed. Your data never leaves your database.
        </p>
      </div>
    </div>
  );
}

function statusText(status: string): string {
  switch (status) {
    case "generating_sql":
      return "Generating SQL query...";
    case "executing":
      return "Executing query...";
    case "generating_response":
      return "Formatting response...";
    default:
      return "Processing...";
  }
}

const SUGGESTIONS = [
  "How many users are active?",
  "Show all products with low stock",
  "What is the total revenue from orders?",
  "List users with their order counts",
  "Which products are in the Electronics category?",
  "Show completed orders with prices",
];
