"use client";

import { useState } from "react";
import { X, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConnectionSelector } from "@/components/connection-selector";
import { useConnections } from "@/lib/connection-store";
import { useConversations } from "@/lib/conversation-store";
import type { Connection } from "@/lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function NewChatDialog({ open, onClose }: Props) {
  const { connections } = useConnections();
  const { createConversation } = useConversations();
  const [selectedConnection, setSelectedConnection] = useState<Connection | null>(null);
  const [title, setTitle] = useState("");
  const [creating, setCreating] = useState(false);

  if (!open) return null;

  const handleCreate = async () => {
    if (!selectedConnection) return;
    setCreating(true);
    try {
      await createConversation(selectedConnection.id, title || undefined);
      onClose();
    } catch (err) {
      console.error("Failed to create conversation:", err);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/40" onClick={onClose} />
      <div className="relative bg-background border rounded-xl shadow-2xl w-full max-w-sm mx-4 p-6">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-semibold">New Chat</h2>
          <button onClick={onClose} className="p-1 rounded-md hover:bg-muted">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mb-4">
          <label className="text-sm font-medium mb-1.5 block">Database Connection</label>
          <ConnectionSelector
            selectedId={selectedConnection?.id ?? null}
            onSelect={setSelectedConnection}
          />
          {connections.length === 0 && (
            <p className="text-xs text-muted-foreground mt-2">
              Add a database connection first in the connection manager.
            </p>
          )}
        </div>

        <div className="mb-6">
          <label className="text-sm font-medium mb-1.5 block">Title (optional)</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Auto-generated from first message"
            className="w-full px-3 py-2 rounded-lg border bg-transparent text-sm outline-none focus-visible:border-ring"
          />
        </div>

        <div className="flex gap-2 justify-end">
          <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={handleCreate} disabled={!selectedConnection || creating}>
            {creating ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
            Create Chat
          </Button>
        </div>
      </div>
    </div>
  );
}
