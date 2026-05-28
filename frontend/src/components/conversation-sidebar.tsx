"use client";

import { useState } from "react";
import { Plus, Trash2, MessageSquare, Database } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { NewChatDialog } from "@/components/new-chat-dialog";
import { ConnectionManager } from "@/components/connection-manager";
import { useConversations } from "@/lib/conversation-store";
import { useConnections } from "@/lib/connection-store";
import type { Conversation } from "@/lib/api";

const DB_ICONS: Record<string, string> = { postgresql: "🐘", mysql: "🐬", sqlite: "💾" };

function formatTime(ts: string): string {
  const date = new Date(ts);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function ConversationSidebar() {
  const { conversations, activeConversationId, setActiveConversationId, deleteConversation } = useConversations();
  const { connections } = useConnections();
  const [newChatOpen, setNewChatOpen] = useState(false);
  const [managerOpen, setManagerOpen] = useState(false);

  return (
    <>
      <aside className="w-64 flex-shrink-0 border-r bg-muted/20 flex flex-col h-screen">
        {/* Header */}
        <div className="px-4 py-3 border-b flex items-center justify-between">
          <h2 className="text-sm font-semibold">Chats</h2>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setManagerOpen(true)}
              className="p-1 rounded-md hover:bg-muted text-muted-foreground"
              title="Manage Connections"
            >
              <Database className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => setNewChatOpen(true)}
              className="p-1 rounded-md hover:bg-muted text-muted-foreground"
              title="New Chat"
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Chat list */}
        <ScrollArea className="flex-1">
          <div className="p-2 space-y-1">
            {conversations.length === 0 ? (
              <p className="text-xs text-muted-foreground text-center py-8 px-4">
                No conversations yet. Click + to start chatting with your database.
              </p>
            ) : (
              conversations.map((conv) => (
                <ChatItem
                  key={conv.id}
                  conversation={conv}
                  active={conv.id === activeConversationId}
                  onClick={() => setActiveConversationId(conv.id)}
                  onDelete={() => deleteConversation(conv.id)}
                />
              ))
            )}
          </div>
        </ScrollArea>

        {/* Footer: connection count */}
        <div className="px-4 py-2 border-t text-xs text-muted-foreground">
          {connections.length} connection{connections.length !== 1 ? "s" : ""}
        </div>
      </aside>

      <NewChatDialog open={newChatOpen} onClose={() => setNewChatOpen(false)} />
      <ConnectionManager open={managerOpen} onClose={() => setManagerOpen(false)} />
    </>
  );
}

function ChatItem({ conversation, active, onClick, onDelete }: {
  conversation: Conversation;
  active: boolean;
  onClick: () => void;
  onDelete: () => void;
}) {
  const [hover, setHover] = useState(false);

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors group ${
        active ? "bg-muted font-medium" : "hover:bg-muted/50"
      }`}
    >
      <div className="flex items-start gap-2">
        <MessageSquare className="h-3.5 w-3.5 mt-0.5 text-muted-foreground flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="truncate">{conversation.title}</p>
          <div className="flex items-center gap-2 mt-0.5 text-xs text-muted-foreground">
            <span>{DB_ICONS[conversation.db_type || "sqlite"] || "📦"}</span>
            <span>{conversation.connection_name || "Unknown"}</span>
            {conversation.message_count > 0 && (
              <span>· {conversation.message_count / 2} msgs</span>
            )}
            <span>· {formatTime(conversation.updated_at)}</span>
          </div>
        </div>
        {hover && (
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            className="p-0.5 rounded hover:bg-muted-foreground/10 text-muted-foreground opacity-60 hover:opacity-100"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        )}
      </div>
    </button>
  );
}
